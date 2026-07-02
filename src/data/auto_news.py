#!/usr/bin/env python3
"""
自动情报搜集层 — B 系统 --auto 管线

五项修正已内建：
  1. 独立源 = 媒体机构级去重（espn.com+espn.co.uk=1源；snippet Jaccard相似度>0.55过滤转载）
  2. λ 调整纯乘法：单条 ∈[0.80,1.20]，单队合计 ∈[0.75,1.25]，无绝对值 cap
  3. 空搜索 → 标"搜索未覆盖"，不静默等价于健康；存疑 snippet → 存疑，不强升确定
  4. must_win/dead_rubber → 只做平局倾斜（draw_skew），不乘 λ（方向不确定，cap ±0.05）
  5. 跨队 snippet 归属护栏：处理 A 队时，含 B 队队名的结果不计入 A 队独立源和分类（字符串级）
"""

from __future__ import annotations
import re
import time
import logging
import functools
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse
from typing import Optional

try:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    _DDG_AVAILABLE = True
except ImportError:
    _DDG_AVAILABLE = False

logger = logging.getLogger(__name__)

# ── 置信度常量 ────────────────────────────────────────────────────────────
CONF_CONFIRMED  = "确定"         # ≥2 独立机构，内容明确
CONF_PREDICTED  = "预计"         # 1机构+强信号，或多机构但模糊
CONF_SINGLE_SRC = "存疑(单源)"   # 只找到1个机构
CONF_INFERRED   = "推理"         # 积分/赛制推理，无直接报道
CONF_NOT_FOUND  = "搜索未覆盖"   # 搜索返回空或无关

# ── λ 调整边界（修正2：纯乘法，无绝对值 cap）────────────────────────────
SINGLE_MULT_MIN = 0.80   # 单条 flag 乘数下限
SINGLE_MULT_MAX = 1.20   # 单条 flag 乘数上限
TEAM_MULT_MIN   = 0.75   # 单队合计乘数下限
TEAM_MULT_MAX   = 1.25   # 单队合计乘数上限

# ── 已知媒体机构规范化 ────────────────────────────────────────────────────
# DDG news() 直接提供 source 字段，匹配优先
_ORG_ALIASES: list[tuple[list[str], str]] = [
    (["ESPN", "ESPN Sport", "ESPN FC", "ESPN Brasil"], "ESPN"),
    (["BBC", "BBC Sport", "BBC News", "BBC Football"], "BBC"),
    (["Sky Sports", "Sky News", "SkySports"], "Sky Sports"),
    (["Guardian", "The Guardian"], "Guardian"),
    (["Reuters", "Reuters Sport"], "Reuters"),
    (["AP", "AP News", "Associated Press"], "AP"),
    (["FIFA", "FIFA.com"], "FIFA"),
    (["Goal", "Goal.com"], "Goal"),
    (["Al Jazeera", "Al-Jazeera", "Aljazeera"], "Al Jazeera"),
    (["Fox Sports", "FOX Sports"], "Fox Sports"),
    (["CBS Sports", "CBSSports"], "CBS Sports"),
    (["Sports Illustrated", "SI.com"], "Sports Illustrated"),
    (["Marca", "marca.com"], "Marca"),
    (["L'Equipe", "Lequipe"], "L'Equipe"),
    (["GiveMeSport"], "GiveMeSport"),
    (["90min"], "90min"),
    (["FourFourTwo", "4-4-2"], "FourFourTwo"),
    (["Telegraph", "The Telegraph"], "Telegraph"),
    (["Independent", "The Independent"], "Independent"),
    (["Sports Mole", "SportsMole"], "Sports Mole"),
    (["SofaScore", "Sofascore"], "SofaScore"),
    (["Transfermarkt"], "Transfermarkt"),
    (["WhoScored", "Whoscored"], "WhoScored"),
    (["Opta", "The Analyst", "OptaJoe"], "Opta/Analyst"),
]

_DOMAIN_ORG: dict[str, str] = {
    "espn.com": "ESPN", "espn.co.uk": "ESPN", "espn.in": "ESPN",
    "bbc.com": "BBC", "bbc.co.uk": "BBC",
    "skysports.com": "Sky Sports",
    "theguardian.com": "Guardian",
    "goal.com": "Goal",
    "reuters.com": "Reuters",
    "apnews.com": "AP",
    "sportsmole.co.uk": "Sports Mole",
    "aljazeera.com": "Al Jazeera", "aljazeera.net": "Al Jazeera",
    "transfermarkt.com": "Transfermarkt",
    "sofascore.com": "SofaScore",
    "whoscored.com": "WhoScored",
    "givemesport.com": "GiveMeSport",
    "90min.com": "90min",
    "fourfourtwo.com": "FourFourTwo",
    "marca.com": "Marca",
    "as.com": "AS",
    "lequipe.fr": "L'Equipe",
    "theanalyst.com": "Opta/Analyst",
    "nytimes.com": "NYTimes",
    "foxsports.com": "Fox Sports",
    "cbssports.com": "CBS Sports",
    "fifa.com": "FIFA",
    "telegraph.co.uk": "Telegraph",
    "independent.co.uk": "Independent",
    "si.com": "Sports Illustrated",
    "sportinglife.com": "Sporting Life",
    "flashscore.com": "FlashScore",
    "yahoo.com": "Yahoo Sports",
}

_BLACKLIST = {
    "sportytrader", "bettingexpert", "betfair", "betway",
    "williamhill", "oddschecker", "bestbetting", "predictz",
    "footballtips", "soccervista", "soccerpunter", "forebet",
    "windrawwin", "leaguelane", "freesupertips", "tips180",
    "1x2monster", "betensured", "tipster", "soccerbetting",
    "bettingtips", "predictsoccer",
}

# ── 关键词集 ──────────────────────────────────────────────────────────────
_INJURY_OUT = {
    "ruled out", "will miss", "confirmed absent", "definitely out",
    "won't feature", "out of squad", "not travel", "unable to play",
    "confirmed injury", "long-term injury", "surgery",
    "serious injury", "out for", "sidelined",
}
_INJURY_DOUBT = {
    "doubtful", "doubt", "fitness test", "monitoring", "uncertain",
    "knock", "fitness concern", "will be assessed", "late fitness",
    "possible doubt", "not fully fit", "50-50", "in doubt", "struggles",
    "question mark", "remains a doubt",
}
_ROTATION = {
    "rotation", "rotate", "rest key", "fresh legs", "changes expected",
    "ring the changes", "squad rotation", "keeping options", "several changes",
    "rotated side", "reserve lineup", "mass changes", "heavily rotated",
    "back-up", "second-string", "weaker side",
}
_MUST_WIN = {
    "must win", "need victory", "facing elimination", "need all three",
    "must beat", "cannot afford", "win or go home", "do or die",
    "need points to", "need to win", "must not lose", "need a win",
}
_DEAD_RUBBER = {
    "already qualified", "already through", "secure progression",
    "guaranteed passage", "top of the group", "confirmed top spot",
    "no pressure", "dead rubber", "nothing to play for", "both already qualified",
    "both teams through", "already secured", "qualification sealed",
}


# ── NewsFlag 结构 ─────────────────────────────────────────────────────────

@dataclass
class NewsFlag:
    team_name: str
    team_side: str                       # "home" | "away"
    type: str                            # "injury" | "lineup" | "motivation"
    content: str
    sources: list[str] = field(default_factory=list)
    n_independent_orgs: int = 0
    confidence: str = CONF_NOT_FOUND
    lam_adj: float = 1.0                 # λ乘数（injury/lineup用）
    draw_skew: float = 0.0               # 平局倾斜（motivation用）
    lambda_impact: str = ""
    timestamp: str = ""


# ── DDG 搜索（带重试+限流）────────────────────────────────────────────────

def _ddg_news(query: str, max_results: int = 8, timelimit: str = "w") -> list[dict]:
    """DDG news search. Returns [] on any error — never raises."""
    if not _DDG_AVAILABLE:
        return []
    for attempt in range(2):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.news(query, max_results=max_results, timelimit=timelimit))
            time.sleep(1.0)
            return results
        except Exception as e:
            logger.debug(f"DDG news attempt {attempt + 1}: {e}")
            if attempt == 0:
                time.sleep(2.0)
    return []


# ── 源去重 ────────────────────────────────────────────────────────────────

def _canonical_org(source_name: str, url: str) -> Optional[str]:
    """
    Map DDG result to canonical organization.
    Returns None if blacklisted.
    修正1：机构级去重——espn.com + espn.co.uk = ESPN（1源）。
    """
    combined = (url + " " + source_name).lower()
    for bl in _BLACKLIST:
        if bl in combined:
            return None

    # Try source_name match (most reliable — DDG extracts it)
    if source_name:
        src = source_name.strip()
        for aliases, canonical in _ORG_ALIASES:
            for alias in aliases:
                if alias.lower() in src.lower():
                    return canonical
        return src[:30]

    # Fallback: domain
    try:
        domain = urlparse(url).netloc.lower()
        for pfx in ("www.", "m.", "en.", "uk.", "us.", "fr.", "de.", "es.", "ar."):
            if domain.startswith(pfx):
                domain = domain[len(pfx):]
        return _DOMAIN_ORG.get(domain, domain[:20] or "Unknown")
    except Exception:
        return "Unknown"


def _snippet_similar(s1: str, s2: str, threshold: float = 0.55) -> bool:
    """
    Jaccard similarity on words.
    修正1：同内容不同域名 = 1源（转载/syndication 检测）。
    """
    def words(s: str) -> set:
        return set(re.sub(r"[^a-z0-9 ]", "", s.lower()).split())
    w1, w2 = words(s1), words(s2)
    if len(w1) < 6 or len(w2) < 6:
        return False
    return len(w1 & w2) / len(w1 | w2) > threshold


def _dedupe_to_orgs(raw_results: list[dict]) -> tuple[list[str], list[str]]:
    """
    过滤黑名单 + 机构级去重 + 转载检测。
    返回 (unique_org_names, unique_snippets)。
    修正1核心：结果数 ≠ 独立源数。
    """
    seen_orgs: dict[str, list[str]] = {}
    all_seen_snippets: list[str] = []

    for r in raw_results:
        url    = r.get("url", "")
        source = r.get("source", "")
        snippet = (r.get("body", "") or "").strip()

        org = _canonical_org(source, url)
        if org is None:
            continue  # blacklisted

        # 修正1：跨机构转载检测——相似度>0.55视为同一内容
        if any(_snippet_similar(snippet, seen) for seen in all_seen_snippets):
            continue

        seen_orgs.setdefault(org, []).append(snippet)
        all_seen_snippets.append(snippet)

    unique_orgs     = list(seen_orgs.keys())
    unique_snippets = [s for ss in seen_orgs.values() for s in ss]
    return unique_orgs, unique_snippets


# ── 文本分类 ─────────────────────────────────────────────────────────────

def _classify_injury(snippets: list[str]) -> tuple[str, float]:
    """Return (category, specificity). category: 'out'|'doubtful'|'rotation'|'none'."""
    text = " ".join(snippets).lower()
    if any(kw in text for kw in _INJURY_OUT):
        return "out", 0.9
    if any(kw in text for kw in _INJURY_DOUBT):
        return "doubtful", 0.6
    if any(kw in text for kw in _ROTATION):
        return "rotation", 0.5
    return "none", 0.0


def _classify_motivation(snippets: list[str]) -> tuple[str, str]:
    """Return (category, content_zh)."""
    text = " ".join(snippets).lower()
    if any(kw in text for kw in _MUST_WIN):
        return "must_win", "需赢球方能晋级"
    if any(kw in text for kw in _DEAD_RUBBER):
        return "dead_rubber", "已提前出线/晋级无压力"
    return "none", ""


# ── λ 调整计算（修正2+4）──────────────────────────────────────────────────

def compute_lam_adj(flags: list[NewsFlag]) -> dict:
    """
    修正2：纯乘法。单条 ∈[0.80,1.20]，单队合计 ∈[0.75,1.25]。
    修正4：must_win/dead_rubber → 只改 draw_skew，不乘 λ。
    只处理 CONF_CONFIRMED / CONF_PREDICTED 的 flag，其余不动 λ。
    """
    home_mults: list[float] = []
    away_mults: list[float] = []
    draw_skew_total = 0.0
    audit: list[str] = []

    for flag in flags:
        if flag.confidence not in (CONF_CONFIRMED, CONF_PREDICTED):
            continue
        conf_factor = 1.0 if flag.confidence == CONF_CONFIRMED else 0.5

        if flag.type == "injury":
            base = 0.88 if "确认缺阵" in flag.content else 0.93
            mult = 1.0 - (1.0 - base) * conf_factor
            mult = max(SINGLE_MULT_MIN, min(SINGLE_MULT_MAX, mult))
            flag.lam_adj = mult
            flag.lambda_impact = f"λ ×{mult:.3f}（伤停）"
            (home_mults if flag.team_side == "home" else away_mults).append(mult)
            audit.append(f"{flag.team_name} {flag.lambda_impact} [{flag.confidence}]")

        elif flag.type == "lineup":
            base = 0.85 if flag.confidence == CONF_CONFIRMED else 0.92
            mult = max(SINGLE_MULT_MIN, min(SINGLE_MULT_MAX, base))
            flag.lam_adj = mult
            flag.lambda_impact = f"λ ×{mult:.3f}（轮换）"
            (home_mults if flag.team_side == "home" else away_mults).append(mult)
            audit.append(f"{flag.team_name} {flag.lambda_impact} [{flag.confidence}]")

        elif flag.type == "motivation":
            # 修正4：心态类 → 只做平局倾斜，λ 不动
            if "must_win" in flag.content or "需赢球" in flag.content:
                skew = -0.03 * conf_factor
            elif "dead_rubber" in flag.content or "已提前" in flag.content:
                skew = +0.02 * conf_factor
            else:
                skew = 0.0
            flag.draw_skew = max(-0.05, min(0.05, skew))
            flag.lambda_impact = f"平局倾斜 {skew:+.3f}（不改λ）"
            draw_skew_total += flag.draw_skew
            audit.append(f"{flag.team_name} {flag.lambda_impact} [{flag.confidence}]")

    # 合计并封顶
    home_total = max(TEAM_MULT_MIN, min(TEAM_MULT_MAX,
        functools.reduce(lambda a, b: a * b, home_mults, 1.0)))
    away_total = max(TEAM_MULT_MIN, min(TEAM_MULT_MAX,
        functools.reduce(lambda a, b: a * b, away_mults, 1.0)))
    draw_skew_total = max(-0.05, min(0.05, draw_skew_total))

    return {
        "home_mult": home_total,
        "away_mult": away_total,
        "draw_skew": draw_skew_total,
        "audit": audit,
    }


# ── 各类搜索结果处理 ──────────────────────────────────────────────────────

def _not_found(team: str, side: str, flag_type: str, ts: str) -> NewsFlag:
    """修正3：显式标注搜索未覆盖，不静默当作健康。"""
    return NewsFlag(
        team_name=team, team_side=side, type=flag_type,
        content=f"搜索未覆盖（{team}）",
        sources=[], n_independent_orgs=0,
        confidence=CONF_NOT_FOUND, timestamp=ts,
    )


def _filter_relevant(raw_results: list[dict], team: str) -> list[dict]:
    """Keep only results mentioning the team name."""
    key = team.lower().split()[0]  # 取队名第一个词
    return [
        r for r in raw_results
        if key in (r.get("title", "") + " " + r.get("body", "")).lower()
    ]


def _filter_opponent_contamination(raw: list[dict], opponent: str) -> list[dict]:
    """
    修正5：跨队 snippet 归属护栏（精准版，不是简单"含对手名就过滤"）。

    处理 A 队时，只过滤满足以下条件的 snippet：
      伤停关键词 在 snippet 中存在 AND 对手队名出现在伤停关键词之前（≤100字符窗口）

    逻辑：
      - "Senegal goalkeeper Mendy is OUT"       → opponent_key='senegal' before 'out' → 过滤 ✓
      - "Mendy OUT for Senegal vs Belgium clash" → 'belgium'(B队) appears AFTER 'out' → 保留 ✓
      - "Belgium vs Senegal - Mendy OUT"         → 'belgium' before 'out' → 过滤（A=Senegal时） ✓

    无需 NLP，纯字符串偏移量判断。
    """
    if not opponent:
        return raw
    opp_key = next((w.lower() for w in opponent.split() if len(w) > 2), opponent.lower())
    _injury_kws = _INJURY_OUT | _INJURY_DOUBT  # 只检查伤停词，不检查轮换

    clean: list[dict] = []
    removed = 0
    for r in raw:
        text = (r.get("title", "") + " " + r.get("body", "")).lower()

        # 找到第一个伤停关键词在文本中的位置
        kw_pos = None
        for kw in _injury_kws:
            idx = text.find(kw)
            if idx != -1 and (kw_pos is None or idx < kw_pos):
                kw_pos = idx

        if kw_pos is None:
            # 没有伤停关键词，不是伤停类 snippet → 保留（不过滤）
            clean.append(r)
            continue

        # 检查对手名是否出现在伤停关键词之前的 100 字符窗口内
        pre_window = text[max(0, kw_pos - 100): kw_pos]
        if opp_key in pre_window:
            removed += 1
            logger.debug(f"opponent-filter: 过滤 snippet（'{opp_key}'出现在伤停词前）: {text[:80]}")
        else:
            clean.append(r)

    if removed > 0:
        logger.debug(f"opponent-filter: 共过滤 {removed} 条对手污染 snippet（对手='{opponent}'）")
    return clean


def _process_team_injury(team: str, side: str, raw: list[dict], ts: str,
                          opponent: str = "") -> NewsFlag:
    """
    处理伤停/轮换搜索结果。
    修正3：空返回 → 搜索未覆盖；不足 snippet → 存疑(单源)，不强升确定。
    """
    if not raw:
        return _not_found(team, side, "injury", ts)

    relevant = _filter_relevant(raw, team)
    if opponent:
        relevant = _filter_opponent_contamination(relevant, opponent)  # 修正5
    if not relevant:
        raw_orgs, _ = _dedupe_to_orgs(raw)
        return NewsFlag(
            team_name=team, team_side=side, type="injury",
            content=f"搜索结果未涉及{team}伤停",
            sources=raw_orgs[:3], n_independent_orgs=len(raw_orgs),
            confidence=CONF_NOT_FOUND, timestamp=ts,
        )

    orgs, snippets = _dedupe_to_orgs(relevant)
    n_orgs = len(orgs)
    category, spec = _classify_injury(snippets)

    if category == "none":
        return NewsFlag(
            team_name=team, team_side=side, type="injury",
            content=f"{team}：未搜到伤停/轮换信息",
            sources=orgs[:3], n_independent_orgs=n_orgs,
            confidence=CONF_NOT_FOUND, timestamp=ts,
        )

    # 修正3：严格置信度门槛
    if n_orgs >= 2 and spec >= 0.7 and category in ("out", "doubtful"):
        conf = CONF_CONFIRMED
    elif n_orgs == 1:
        conf = CONF_SINGLE_SRC      # 单源不升级
    else:
        conf = CONF_PREDICTED       # ≥2源但内容模糊

    label = {"out": f"{team} 确认缺阵", "doubtful": f"{team} 主力存疑出战",
             "rotation": f"{team} 预计轮换首发"}[category]
    # 优先选含有触发关键词的 snippet，避免 display 与分类不对应
    trigger_kws = _INJURY_OUT if category == "out" else (_INJURY_DOUBT if category == "doubtful" else _ROTATION)
    trigger_snip = next((s for s in snippets if any(kw in s.lower() for kw in trigger_kws)), None)
    summary = (trigger_snip or snippets[0])[:90] if snippets else ""
    return NewsFlag(
        team_name=team, team_side=side, type="injury",
        content=f"{label}（{summary}）",
        sources=orgs[:5], n_independent_orgs=n_orgs,
        confidence=conf, timestamp=ts,
    )


def _process_lineup(home: str, away: str, raw: list[dict], ts: str) -> list[NewsFlag]:
    """处理两队合场首发/轮换搜索。首发类最高只到 预计（无官方来源则不升确定）。"""
    if not raw:
        return []
    orgs, snippets = _dedupe_to_orgs(raw)
    flags: list[NewsFlag] = []

    for team, side in [(home, "home"), (away, "away")]:
        key = team.lower().split()[0]
        team_snippets = [s for s in snippets if key in s.lower()]
        if not team_snippets:
            continue
        category, _ = _classify_injury(team_snippets)
        if category != "rotation":
            continue
        # 首发信息：官方首发出前最多"预计"
        conf = CONF_PREDICTED
        content = f"{team} 预计轮换首发（{team_snippets[0][:80]}）"
        flags.append(NewsFlag(
            team_name=team, team_side=side, type="lineup",
            content=content, sources=orgs[:3], n_independent_orgs=len(orgs),
            confidence=conf, timestamp=ts,
        ))
    return flags


def _process_motivation(home: str, away: str, raw: list[dict], ts: str) -> list[NewsFlag]:
    """处理出线形势搜索。心态类标注 推理，不升 预计/确定。"""
    if not raw:
        return []
    orgs, snippets = _dedupe_to_orgs(raw)
    if not snippets:
        return []
    flags: list[NewsFlag] = []

    for team, side in [(home, "home"), (away, "away")]:
        key = team.lower().split()[0]
        team_snippets = [s for s in snippets if key in s.lower()]
        if not team_snippets:
            continue
        motive_cat, motive_content = _classify_motivation(team_snippets)
        if motive_cat == "none":
            continue
        flags.append(NewsFlag(
            team_name=team, team_side=side, type="motivation",
            content=f"{team} {motive_content}",
            sources=orgs[:3], n_independent_orgs=len(orgs),
            confidence=CONF_INFERRED, timestamp=ts,  # 心态永远是推理
        ))
    return flags


# ── 主入口 ────────────────────────────────────────────────────────────────

def gather_match_news(
    home: str,
    away: str,
    match_date: str = "",
) -> tuple[list[NewsFlag], dict]:
    """
    为单场比赛自动搜集情报。
    返回 (flags, lam_adj_dict)。
    lam_adj_dict = {home_mult, away_mult, draw_skew, audit}。
    """
    if not _DDG_AVAILABLE:
        msg = "duckduckgo-search 未安装，请运行: pip3 install duckduckgo-search"
        return [], {"home_mult": 1.0, "away_mult": 1.0, "draw_skew": 0.0, "audit": [msg]}

    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event = "World Cup 2026"
    flags: list[NewsFlag] = []

    # Q1: 主队伤停（修正5：传入对手名，过滤跨队污染）
    flags.append(_process_team_injury(home, "home", _ddg_news(f"{home} injury absence {event}"), ts,
                                       opponent=away))

    # Q2: 客队伤停
    flags.append(_process_team_injury(away, "away", _ddg_news(f"{away} injury absence {event}"), ts,
                                       opponent=home))

    # Q3: 两队首发/轮换
    flags += _process_lineup(home, away, _ddg_news(f"{home} {away} lineup rotation preview {event}"), ts)

    # Q4: 出线形势
    flags += _process_motivation(home, away, _ddg_news(f"{home} {away} standings qualification {event}"), ts)

    adj = compute_lam_adj(flags)
    return flags, adj
