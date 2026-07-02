#!/usr/bin/env python3
"""
今日赛程 + 赔率数据源 — B 系统 --auto-today 管线
the-odds-api v4  sport_key = soccer_fifa_world_cup

三项强制约束：
  1. 当前日期来自系统时钟，不硬编码
  2. 配额耗尽/无赛程 → 明确报错/提示，绝不跌回旧数据
  3. 队名映射不上 → 标注仍出 B 预测，不静默丢场次
"""

from __future__ import annotations
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from config import ODDS_API_KEY

SPORT_KEY = "soccer_fifa_world_cup"
API_BASE  = "https://api.the-odds-api.com/v4"

# 优先取 sharp 盘口；没有就按顺序往下取
BOOKMAKER_PRIORITY = [
    "pinnacle", "betonlineag", "lowvig",
    "bet365", "williamhill", "betway",
    "unibet", "unibet_nl", "unibet_se", "unibet_dk",
    "bwin", "tipico_de", "leovegas_se", "pmu_fr",
    "parimatch", "1xbet", "marathonbet",
]

# the-odds-api 队名 → 本系统 TEAM_ELO 队名
TEAM_NAME_MAP: dict[str, str] = {
    # Americas
    "United States":                    "USA",
    "US":                               "USA",
    "Korea Republic":                   "South Korea",
    "Republic of Korea":                "South Korea",
    "Korea DPR":                        "North Korea",
    # Africa
    "DR Congo":                         "Congo DR",
    "Congo - Kinshasa":                 "Congo DR",
    "Democratic Republic of the Congo": "Congo DR",
    "Côte d'Ivoire":                    "Ivory Coast",
    "Cote d'Ivoire":                    "Ivory Coast",
    # Europe
    "Czech Republic":                   "Czechia",
    "Bosnia and Herzegovina":           "Bosnia",
    "Bosnia & Herzegovina":             "Bosnia",
    # Middle East / Asia
    "Iran (Islamic Republic of)":       "Iran",
    "KSA":                              "Saudi Arabia",
    # Others with common alias issues
    "Cape Verde Islands":               "Cape Verde",
    "Curacao":                          "Curacao",
    "New Zealand":                      "New Zealand",
    "South Africa":                     "South Africa",
}


@dataclass
class MatchOdds:
    home: str           # 归一化后名（TEAM_ELO key）
    away: str
    home_raw: str       # API 原始名
    away_raw: str
    commence_time: str  # ISO UTC
    odds_home: float
    odds_draw: float
    odds_away: float
    bookmaker: str      # 赔率取自哪家
    kickoff_delta: str  # 距开球描述
    home_matched: bool  # 是否在 TEAM_ELO 中
    away_matched: bool


@dataclass
class FetchResult:
    matches: list[MatchOdds]
    today_utc: str
    sport_key: str
    quota_remaining: str
    quota_used: str
    now_utc_iso: str
    error: Optional[str] = None   # 非 None 表示失败

    @property
    def ok(self) -> bool:
        return self.error is None


# ── 内部工具 ──────────────────────────────────────────────────────────────

def _normalize_team(raw: str) -> str:
    return TEAM_NAME_MAP.get(raw, raw)


def _bm_rank(key: str) -> int:
    key_l = key.lower()
    for i, k in enumerate(BOOKMAKER_PRIORITY):
        if k in key_l or key_l in k:
            return i
    return len(BOOKMAKER_PRIORITY)


def _extract_h2h(
    match_dict: dict,
    home_raw: str,
    away_raw: str,
) -> tuple[float, float, float, str] | None:
    """
    从 bookmakers 列表里按优先级找第一个有完整 h2h 的盘口。
    返回 (odds_home, odds_draw, odds_away, bookmaker_title) 或 None。
    """
    bms_sorted = sorted(
        match_dict.get("bookmakers", []),
        key=lambda b: _bm_rank(b.get("key", "")),
    )
    for bm in bms_sorted:
        for mkt in bm.get("markets", []):
            if mkt.get("key") != "h2h":
                continue
            oc = {o["name"]: float(o["price"]) for o in mkt.get("outcomes", [])}
            if home_raw in oc and away_raw in oc and "Draw" in oc:
                return oc[home_raw], oc["Draw"], oc[away_raw], bm.get("title", bm.get("key", "?"))
    return None


def _kickoff_delta(commence_iso: str, now_utc: datetime) -> str:
    try:
        ko = datetime.fromisoformat(commence_iso.replace("Z", "+00:00"))
        secs = (ko - now_utc).total_seconds()
        if secs < -300:
            return f"已开球 {int(-secs/60)}min 前"
        elif secs < 0:
            return "已开球"
        elif secs < 3600:
            return f"距开球 {int(secs/60)}min"
        else:
            return f"距开球 {secs/3600:.1f}h"
    except Exception:
        return "未知"


# ── 主函数 ────────────────────────────────────────────────────────────────

def fetch_today_matches(
    date_override: str | None = None,
    verbose: bool = True,
) -> FetchResult:
    """
    拉今日世界杯赛程 + 1X2 赔率。
    date_override: "YYYY-MM-DD"，不填则用当前 UTC 日期。
    返回 FetchResult，never raises。
    """
    if not ODDS_API_KEY:
        print("错误: ODDS_API_KEY 未设置，请先 export ODDS_API_KEY=你的key")
        print("      获取地址: https://the-odds-api.com")
        import sys; sys.exit(1)

    now_utc   = datetime.now(timezone.utc)
    today_utc = date_override or now_utc.strftime("%Y-%m-%d")
    now_iso   = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    key_masked = ODDS_API_KEY[:4] + "****" + ODDS_API_KEY[-4:]

    url = (f"{API_BASE}/sports/{SPORT_KEY}/odds/"
           f"?apiKey={ODDS_API_KEY}&regions=eu&markets=h2h&oddsFormat=decimal")
    safe_url = url.replace(ODDS_API_KEY, key_masked)

    if verbose:
        print(f"  当前 UTC    : {now_iso}")
        print(f"  今日 UTC    : {today_utc}  (过滤赛程用此日期)")
        print(f"  API endpoint: {safe_url}")

    # ── HTTP 请求 ─────────────────────────────────────────────────────────
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "worldcup2026-b/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            quota_remaining = resp.headers.get("x-requests-remaining", "N/A")
            quota_used      = resp.headers.get("x-requests-used",      "N/A")
            raw_data        = json.loads(resp.read())

    except urllib.error.HTTPError as e:
        quota_remaining = e.headers.get("x-requests-remaining", "N/A")
        body_txt = e.read().decode("utf-8", errors="replace")[:300]
        if e.code == 401:
            msg = f"HTTP 401 — key 无效或过期  ({key_masked})"
        elif e.code == 429:
            msg = f"HTTP 429 — 配额已耗尽  剩余: {quota_remaining}"
        else:
            msg = f"HTTP {e.code} {e.reason}: {body_txt}"
        if verbose:
            print(f"  ✗ 请求失败: {msg}")
        return FetchResult([], today_utc, SPORT_KEY, quota_remaining, "N/A", now_iso, error=msg)

    except Exception as e:
        if verbose:
            print(f"  ✗ 网络错误: {e}")
        return FetchResult([], today_utc, SPORT_KEY, "N/A", "N/A", now_iso, error=str(e))

    if verbose:
        print(f"  剩余配额    : {quota_remaining}  已用: {quota_used}")
        print(f"  API 共返回  : {len(raw_data)} 场（含未来多天）")

    # ── 按今日过滤 ────────────────────────────────────────────────────────
    today_raw = [m for m in raw_data if m.get("commence_time", "")[:10] == today_utc]

    if not today_raw:
        dates_avail = sorted({m.get("commence_time","")[:10] for m in raw_data if m.get("commence_time","")})
        msg = f"今日 ({today_utc}) 无赛程  (API 共 {len(raw_data)} 场，可用日期: {dates_avail})"
        if verbose:
            print(f"  ⚠ {msg}")
        return FetchResult([], today_utc, SPORT_KEY, quota_remaining, quota_used, now_iso, error=msg)

    if verbose:
        print(f"  今日场次    : {len(today_raw)} 场")

    # ── 解析每场 ─────────────────────────────────────────────────────────
    from config import TEAM_ELO
    matches: list[MatchOdds] = []
    skipped: list[str] = []

    for m in today_raw:
        home_raw = m.get("home_team", "")
        away_raw = m.get("away_team", "")
        commence = m.get("commence_time", "")

        home_norm = _normalize_team(home_raw)
        away_norm = _normalize_team(away_raw)
        home_matched = home_norm in TEAM_ELO
        away_matched = away_norm in TEAM_ELO

        h2h = _extract_h2h(m, home_raw, away_raw)
        if h2h is None:
            reason = f"无 h2h 赔率 (bookmakers={[b['key'] for b in m.get('bookmakers',[])]})"
            if verbose:
                print(f"  ⚠ {home_raw} vs {away_raw}: {reason}，跳过 B1")
            skipped.append(f"{home_raw} vs {away_raw} ({reason})")
            continue

        odds_h, odds_d, odds_a, bm_title = h2h

        matches.append(MatchOdds(
            home=home_norm, away=away_norm,
            home_raw=home_raw, away_raw=away_raw,
            commence_time=commence,
            odds_home=odds_h, odds_draw=odds_d, odds_away=odds_a,
            bookmaker=bm_title,
            kickoff_delta=_kickoff_delta(commence, now_utc),
            home_matched=home_matched, away_matched=away_matched,
        ))

    if verbose and matches:
        print()
        print(f"  {len(matches)} 场已解析:")
        for mt in matches:
            nm_warn = ""
            if not mt.home_matched:
                nm_warn += f" ⚠{mt.home_raw}(未匹配)"
            if not mt.away_matched:
                nm_warn += f" ⚠{mt.away_raw}(未匹配)"
            print(f"    {mt.home:<18} vs {mt.away:<18}  {mt.kickoff_delta}  "
                  f"[{mt.odds_home}/{mt.odds_draw}/{mt.odds_away}] @{mt.bookmaker}{nm_warn}")

    return FetchResult(
        matches=matches,
        today_utc=today_utc,
        sport_key=SPORT_KEY,
        quota_remaining=quota_remaining,
        quota_used=quota_used,
        now_utc_iso=now_iso,
    )
