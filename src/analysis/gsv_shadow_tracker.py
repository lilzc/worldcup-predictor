#!/usr/bin/env python3
"""
GSV 假想盘口追踪器 — 纯记账旁路模块，不影响 A/B 任何下注输出。

对每个 GSV 触发场次（标准区/扩展区），记录假想弱方DC (1X/X2) 和弱方AH+0.5 的
假想结算。所有赔率基于无水近似(1/market_prob)，真实盘口含vig约2-4%，
实际可兑现ROI需相应下调。

Usage:
    python3 -m src.analysis.gsv_shadow_tracker --report    # 当前统计
    python3 -m src.analysis.gsv_shadow_tracker --backfill  # 回填历史（会清空日志再重写）
"""

from __future__ import annotations
import json
import math
import sys
import argparse
from pathlib import Path

# 项目根目录（从 src/analysis/ 向上两级）
_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT))

from config import (
    TEAM_ELO, GSV_LAMBDA_ELO_MIN, GSV_LAMBDA_DIFF_MIN,
    GSV_LAMBDA_DIFF_MAX, GSV_LAMBDA_DIFF_EXTENDED,
    GSV_LAMBDA_FACTOR, GSV_LAMBDA_FACTOR_EXTENDED,
    BASE_GOALS, ELO_SCALE, AD_ENABLED,
)

DATA_FILE = _ROOT / "data" / "gsv_shadow_log.jsonl"
_VIG_NOTE = (
    "假想ROI基于无水近似价，真实盘口含vig(DC≈2-4%)，"
    "实际可兑现ROI需相应下调"
)
UNLOCK_N = 8   # 解封条件：≥8 触发场次且假想ROI显著为正（2026-07定档，变更需显式讨论）

# Cohort 边界
_GREY_START = "2026-06-29"   # 灰带起点（淘汰赛首场，猜想成形期）
_OOS_START  = "2026-07-03"   # 正式 OOS 起点（猜想记档日）

def _date_to_cohort(date: str) -> str:
    if date >= _OOS_START:
        return "oos"
    if date >= _GREY_START:
        return "grey"
    return "backfill"

# ── 内部工具 ─────────────────────────────────────────────────────────────────

def _dc_outcome(hg: int, ag: int, weak_is_home: bool) -> str:
    """
    DC 1X (weak=home) 结果：home不败 → win if hg >= ag。
    DC X2 (weak=away) 结果：away不败 → win if ag >= hg。
    """
    if weak_is_home:
        return "win" if hg >= ag else "lose"
    else:
        return "win" if ag >= hg else "lose"


def _find_ah_odds(ah_list: list, line: float, want_home: bool):
    """
    在 AH odds list [(line, home_odds, away_odds), ...] 里找指定 line。
    want_home=True 返回 home_odds，否则返回 away_odds。
    返回 (odds, found)。
    """
    for entry in ah_list:
        if abs(entry[0] - line) < 0.01:
            return (entry[1] if want_home else entry[2]), True
    return None, False


def _devig(o_h: float, o_d: float, o_a: float) -> tuple[float, float, float]:
    """1X2 赔率去水，返回 (p_home, p_draw, p_away)。"""
    vig = 1/o_h + 1/o_d + 1/o_a
    return (1/o_h)/vig, (1/o_d)/vig, (1/o_a)/vig


def _build_shadow_record(
    home: str, away: str, date: str,
    he: float, ae: float,  # 赛前 Elo
    mat,                    # score_matrix numpy array
    probs: dict,            # model probs from _build_mat_custom
    gsv_zone: str,          # "standard" | "extended"
    lam_h_base: float, lam_a_base: float,   # pre-GSV λ (base)
    lam_h_final: float, lam_a_final: float, # post-GSV λ (final)
    hg: int, ag: int,       # 实际比分
    odds_entry: dict | None,  # MATCHES_ODDS entry or None
) -> dict:
    """构建一条 shadow log 记录。"""
    diff = he - ae

    # 确定强弱方
    if he > GSV_LAMBDA_ELO_MIN and (
        (GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX)
        or (GSV_LAMBDA_DIFF_MAX < diff <= GSV_LAMBDA_DIFF_EXTENDED)
    ):
        strong, weak = home, away
        weak_is_home = False   # 弱方是 away
        # DC 弱方不败 = X2 (draw + away_win)
        dc_model = probs["draw"] + probs["away_win"]
        dc_label = f"{away}不败(X2)"
        # AH+0.5 for weak away: line=+0.5 (home gives 0.5) → away_odds
        ah_target_line, ah_want_home = 0.5, False
    else:
        strong, weak = away, home
        weak_is_home = True   # 弱方是 home
        # DC 弱方不败 = 1X (home_win + draw)
        dc_model = probs["home_win"] + probs["draw"]
        dc_label = f"{home}不败(1X)"
        # AH+0.5 for weak home: line=-0.5 (home receives 0.5) → home_odds
        ah_target_line, ah_want_home = -0.5, True

    # 市场去水
    mkt_avail = False
    mkt_hw = mkt_d = mkt_aw = None
    if odds_entry and "1x2" in odds_entry:
        o_h, o_d, o_a = odds_entry["1x2"]
        mkt_hw, mkt_d, mkt_aw = _devig(o_h, o_d, o_a)
        mkt_avail = True

    if mkt_avail:
        if weak_is_home:
            dc_market = mkt_hw + mkt_d
        else:
            dc_market = mkt_d + mkt_aw
        dc_edge = dc_model - dc_market
        dc_hypo_odds = round(1.0 / dc_market, 4) if dc_market > 0 else None
        dc_result = _dc_outcome(hg, ag, weak_is_home)
        dc_pv = round((dc_hypo_odds - 1) if dc_result == "win" else -1, 4) if dc_hypo_odds else None
    else:
        dc_market = dc_edge = dc_hypo_odds = dc_result = dc_pv = None

    # AH+0.5 假想赔率
    ah_odds = ah_pv = ah_result = None
    ah_avail = False
    if odds_entry and "ah" in odds_entry:
        ah_odds_val, ah_found = _find_ah_odds(odds_entry["ah"], ah_target_line, ah_want_home)
        if ah_found and ah_odds_val:
            ah_odds = ah_odds_val
            ah_result = _dc_outcome(hg, ag, weak_is_home)  # AH+0.5 同 DC 胜负
            ah_pv = round((ah_odds - 1) if ah_result == "win" else -1, 4)
            ah_avail = True

    actual_outcome = "home_win" if hg > ag else ("draw" if hg == ag else "away_win")

    return {
        "match": f"{home} vs {away}",
        "home": home, "away": away, "date": date,
        "cohort": _date_to_cohort(date),
        "gsv_zone": gsv_zone,
        "strong": strong, "weak": weak,
        "elo_diff": round(diff, 1),
        "lambda_before_gsv": {
            "home": round(lam_h_base, 4),
            "away": round(lam_a_base, 4),
        },
        "lambda_after_gsv": {
            "home": round(lam_h_final, 4),
            "away": round(lam_a_final, 4),
        },
        "model_probs": {
            "home_win": round(probs["home_win"], 4),
            "draw":     round(probs["draw"],     4),
            "away_win": round(probs["away_win"], 4),
        },
        "market_probs": {
            "home_win": round(mkt_hw, 4) if mkt_hw is not None else None,
            "draw":     round(mkt_d,  4) if mkt_d  is not None else None,
            "away_win": round(mkt_aw, 4) if mkt_aw is not None else None,
        } if mkt_avail else None,
        "market_odds_available": mkt_avail,
        "shadow_bets": {
            "weak_dc": {
                "label":       dc_label,
                "model_prob":  round(dc_model, 4),
                "market_prob": round(dc_market, 4) if dc_market is not None else None,
                "edge":        round(dc_edge,  4) if dc_edge   is not None else None,
                "hypo_odds":   dc_hypo_odds,
                "result":      dc_result,
                "hypo_pv":     dc_pv,
                "vig_note":    _VIG_NOTE,
            },
            "weak_ah_plus05": {
                "label":       f"{weak} AH+0.5",
                "market_odds": ah_odds,
                "result":      ah_result,
                "hypo_pv":     ah_pv,
                "odds_available": ah_avail,
                "vig_note":    "AH赔率来自真实市场，非无水近似" if ah_avail else "无可用AH盘口数据",
            },
        },
        "actual_score": f"{hg}-{ag}",
        "actual_outcome": actual_outcome,
    }


# ── 公开 API（供 walkforward.py 旁路调用）────────────────────────────────────

_SEEN_KEYS: set[tuple] | None = None  # (home, away, date) 去重缓存，None=未初始化


def _load_seen_keys() -> set[tuple]:
    """从 DATA_FILE 读取已有的 (home, away, date) 集合。"""
    keys: set[tuple] = set()
    if not DATA_FILE.exists():
        return keys
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                r = json.loads(line)
                keys.add((r.get("home", ""), r.get("away", ""), r.get("date", "")))
    except Exception:
        pass
    return keys


def log_gsv_match(record: dict) -> None:
    """追加一条记录到 shadow log（同一 home/away/date 去重）。任何异常静默忽略。"""
    global _SEEN_KEYS
    try:
        if _SEEN_KEYS is None:
            _SEEN_KEYS = _load_seen_keys()
        key = (record.get("home", ""), record.get("away", ""), record.get("date", ""))
        if key in _SEEN_KEYS:
            return
        _SEEN_KEYS.add(key)
        with open(DATA_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


# ── 回填 ──────────────────────────────────────────────────────────────────────

def backfill_history(force: bool = False) -> None:
    """
    对 wc2026_results.json 中所有历史 GSV 触发场次回填 shadow log。
    使用赛前时点态（无前视），与 walkforward 同样的 Elo/AD 更新顺序。

    force=True: 先清空旧日志再重写（防止重复条目）。
    """
    from walkforward import (
        _wf_elo_update, _wf_ad_update, _wf_ad_exp,
        _build_mat_custom, MATCHES_ODDS,
    )

    if force:
        DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
        DATA_FILE.write_text("")

    with open(_ROOT / "data" / "wc2026_results.json") as f:
        matches = json.load(f)["matches"]

    odds_lookup = {(h, a): v for (h, a, _d), v in MATCHES_ODDS.items()}

    elo = dict(TEAM_ELO)
    ad_state: dict = {}
    n_filled = 0

    for m in matches:
        home, away = m["home"], m["away"]
        hg, ag = m["hg"], m["ag"]
        he = elo.get(home, 1700.0)
        ae = elo.get(away, 1700.0)
        diff = he - ae

        # 判断 GSV 区段（与 _build_mat_custom 同逻辑）
        gsv_std_h = he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX
        gsv_std_a = ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX
        gsv_ext_h = (he > GSV_LAMBDA_ELO_MIN
                     and GSV_LAMBDA_DIFF_MAX < diff <= GSV_LAMBDA_DIFF_EXTENDED)
        gsv_ext_a = (ae > GSV_LAMBDA_ELO_MIN
                     and GSV_LAMBDA_DIFF_MAX < -diff <= GSV_LAMBDA_DIFF_EXTENDED)
        gsv_triggered = gsv_std_h or gsv_std_a or gsv_ext_h or gsv_ext_a

        if gsv_triggered:
            zone = "standard" if (gsv_std_h or gsv_std_a) else "extended"

            # λ 基础值（pre-GSV）
            lam_h_base = BASE_GOALS * math.exp(diff / ELO_SCALE)
            lam_a_base = BASE_GOALS * math.exp(-diff / ELO_SCALE)

            # GSV 修正倍数（与 _build_mat_custom 同逻辑）
            lh = la = 1.0
            if gsv_std_h:
                lh = GSV_LAMBDA_FACTOR
            elif gsv_std_a:
                la = GSV_LAMBDA_FACTOR
            elif gsv_ext_h:
                lh = GSV_LAMBDA_FACTOR_EXTENDED
            elif gsv_ext_a:
                la = GSV_LAMBDA_FACTOR_EXTENDED

            # 赛前矩阵（无前视：使用当前 elo/ad_state，本场结果尚未纳入）
            mat, probs, _diff, _gsv = _build_mat_custom(home, away, he, ae, ad_state)

            odds_entry = odds_lookup.get((home, away))

            rec = _build_shadow_record(
                home=home, away=away, date=m["date"],
                he=he, ae=ae,
                mat=mat, probs=probs,
                gsv_zone=zone,
                lam_h_base=lam_h_base, lam_a_base=lam_a_base,
                lam_h_final=lam_h_base * lh, lam_a_final=lam_a_base * la,
                hg=hg, ag=ag,
                odds_entry=odds_entry,
            )
            log_gsv_match(rec)
            n_filled += 1

        # 赛后更新状态（保持无前视）
        exp_h = _wf_ad_exp(he, ae)
        exp_a = _wf_ad_exp(ae, he)
        _wf_ad_update(ad_state, home, hg, ag, exp_h, exp_a)
        _wf_ad_update(ad_state, away, ag, hg, exp_a, exp_h)
        elo = _wf_elo_update(elo, home, away, hg, ag)

    print(f"[GSV追踪器] 回填完成：共扫描 {len(matches)} 场，GSV触发 {n_filled} 场")
    print(f"[GSV追踪器] 日志路径: {DATA_FILE}")


# ── 统计报告 ──────────────────────────────────────────────────────────────────

def _segment_stats(recs: list[dict]) -> dict:
    """计算一组记录的 DC / AH 统计。"""
    dc = [r for r in recs if r.get("market_odds_available") and
          r["shadow_bets"]["weak_dc"]["result"] is not None]
    ah = [r for r in recs if r["shadow_bets"]["weak_ah_plus05"]["odds_available"]]
    dc_pnl = sum(r["shadow_bets"]["weak_dc"]["hypo_pv"] or 0 for r in dc)
    ah_pnl = sum(r["shadow_bets"]["weak_ah_plus05"]["hypo_pv"] or 0 for r in ah)
    return {
        "n": len(recs),
        "dc": dc, "dc_w": sum(1 for r in dc if r["shadow_bets"]["weak_dc"]["result"]=="win"),
        "dc_l": sum(1 for r in dc if r["shadow_bets"]["weak_dc"]["result"]=="lose"),
        "dc_pnl": dc_pnl, "dc_roi": dc_pnl/len(dc)*100 if dc else 0.0,
        "ah": ah, "ah_w": sum(1 for r in ah if r["shadow_bets"]["weak_ah_plus05"]["result"]=="win"),
        "ah_l": sum(1 for r in ah if r["shadow_bets"]["weak_ah_plus05"]["result"]=="lose"),
        "ah_pnl": ah_pnl, "ah_roi": ah_pnl/len(ah)*100 if ah else 0.0,
    }


def _print_segment(label: str, note: str, recs: list[dict]) -> None:
    s = _segment_stats(recs)
    std = sum(1 for r in recs if r.get("gsv_zone")=="standard")
    ext = sum(1 for r in recs if r.get("gsv_zone")=="extended")
    print(f"\n  ── {label}  {note} ──")
    print(f"     触发 {s['n']} 场（标准区 {std} | 扩展区 {ext}）")
    if s["dc"]:
        print(f"     DC  {s['dc_w']}W/{s['dc_l']}L  P&L={s['dc_pnl']:+.2f}  ROI={s['dc_roi']:+.1f}%")
    else:
        print(f"     DC  无可结算数据（无市场赔率）")
    if s["ah"]:
        print(f"     AH+0.5  {s['ah_w']}W/{s['ah_l']}L  P&L={s['ah_pnl']:+.2f}  ROI={s['ah_roi']:+.1f}%")
    # 逐场明细
    print(f"     {'场次':<30} {'区段':<6} {'弱方':<12} {'DC结果':<7} {'DC边际':>8} {'AH+0.5':>7}")
    print(f"     {'─'*72}")
    for r in recs:
        dc_b = r["shadow_bets"]["weak_dc"]
        ah_b = r["shadow_bets"]["weak_ah_plus05"]
        dc_e = f"{dc_b['edge']*100:+.1f}pp" if dc_b.get("edge") is not None else "N/A"
        dc_r = dc_b.get("result") or "N/A"
        ah_r = ah_b.get("result") or "N/A"
        print(f"     {r['match']:<30} [{r['gsv_zone'][:3].upper()}]  {r['weak']:<12} {dc_r:<7} {dc_e:>8} {ah_r:>7}")


def report() -> None:
    """三段式输出：回填 / 灰带 / 正式OOS。唯一裁决依据=正式OOS段。"""
    if not DATA_FILE.exists():
        print("[GSV追踪器] 日志文件不存在，请先运行 --backfill")
        return

    records = []
    with open(DATA_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not records:
        print("[GSV追踪器] 日志为空")
        return

    # 按 cohort 分段（兼容旧记录：无 cohort 字段按日期推断）
    def _cohort(r):
        if "cohort" in r:
            return r["cohort"]
        return _date_to_cohort(r.get("date",""))

    bf   = [r for r in records if _cohort(r) == "backfill"]
    grey = [r for r in records if _cohort(r) == "grey"]
    oos  = [r for r in records if _cohort(r) == "oos"]

    print("=" * 72)
    print("  GSV 假想盘口追踪器 — 三段式报告")
    print("=" * 72)

    _print_segment(
        "回填段（小组赛，≤6-28）",
        "同一训练池，仅参考",
        bf,
    )
    _print_segment(
        "灰带段（淘汰赛 6-29~7-02）",
        "猜想成形期，观察不裁决",
        grey,
    )

    oos_s = _segment_stats(oos)
    _print_segment(
        f"正式OOS（7-03起，N={len(oos)}/{UNLOCK_N}）",
        "唯一裁决依据",
        oos,
    )

    print()
    print(f"  ⚠ {_VIG_NOTE}")
    print()
    print("─" * 72)
    print(f"  解封条件（仅看正式OOS段）：N≥{UNLOCK_N} 且 DC无水ROI显著为正")
    print(f"  样本外起点=7-03（猜想记档日），严格晚于猜想成形；6-29~7-02为灰带cohort，观察不裁决。")
    n_oos = len(oos)
    if n_oos >= UNLOCK_N and oos_s["dc"] and oos_s["dc_roi"] > 5:
        print(f"  ✓ 正式OOS N={n_oos}≥{UNLOCK_N}，DC ROI={oos_s['dc_roi']:+.1f}% — 满足解封，可提交spec")
    else:
        progress = f"{n_oos}/{UNLOCK_N}"
        roi_str = f"{oos_s['dc_roi']:+.1f}%" if oos_s["dc"] else "暂无数据"
        print(f"  正式OOS进度: N={progress}，DC ROI={roi_str} — 未达解封条件")
    print("─" * 72)


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GSV 假想盘口追踪器")
    parser.add_argument("--backfill", action="store_true", help="回填所有历史GSV场次")
    parser.add_argument("--report",   action="store_true", help="输出当前统计报告")
    parser.add_argument("--force",    action="store_true", help="--backfill时先清空日志")
    args = parser.parse_args()

    if args.backfill:
        backfill_history(force=args.force)
    if args.report:
        report()
    if not args.backfill and not args.report:
        parser.print_help()
