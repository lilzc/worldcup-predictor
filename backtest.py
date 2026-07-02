#!/usr/bin/env python3
"""
回测：用模型预测已完赛事，对比实际结果，报告 1X2 / O/U / AH / 正确比分准确率与校准。

用法:
  python3 backtest.py               # 全部已记录赛事
  python3 backtest.py --use-live-elo # 用 data/elo_state.json 的实时Elo
  python3 backtest.py --markets 1x2,ou,ah,cs  # 只显示指定市场
"""

import json
import sys
import argparse

sys.path.insert(0, ".")

from src.models.poisson import score_matrix, matrix_to_probs, ou_prob, ah_prob, compute_ad_factor
from src.models.adjustments import apply_all
from config import (TEAM_ELO, GSV_LAMBDA_FACTOR, GSV_LAMBDA_ELO_MIN,
                    GSV_LAMBDA_DIFF_MIN, GSV_LAMBDA_DIFF_MAX,
                    GSV_LAMBDA_FACTOR_EXTENDED, GSV_LAMBDA_DIFF_EXTENDED,
                    AD_ENABLED)

RESULTS_PATH = "data/wc2026_results.json"

OU_LINES = [2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75]
AH_LINES = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5]


def load_elo(use_live: bool) -> dict:
    if use_live:
        import os
        path = "data/elo_state.json"
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    return dict(TEAM_ELO)


def predict_match(home: str, away: str, elo: dict, before_date: str = None,
                  ad_state: dict = None) -> tuple:
    home_elo = elo.get(home)
    away_elo = elo.get(away)
    he = float(home_elo or 1700)
    ae = float(away_elo or 1700)

    # AD factors: custom inject (bypasses module-level cache)
    if AD_ENABLED and ad_state is not None:
        att_h, def_h = compute_ad_factor(ad_state, home)
        att_a, def_a = compute_ad_factor(ad_state, away)
    else:
        att_h = def_h = att_a = def_a = 1.0

    mat = score_matrix(
        home, away,
        custom_home_elo=he, custom_away_elo=ae,
        before_date=before_date,
        custom_att_home=att_h, custom_def_home=def_h,
        custom_att_away=att_a, custom_def_away=def_a,
    )
    raw = matrix_to_probs(mat)
    adj = apply_all(home, away, raw["home_win"], raw["draw"], raw["away_win"],
                    home_elo=he, away_elo=ae)
    probs = {**raw, **adj}

    # Apply GSV lambda correction to AH/O/U (mirrors predict.py dual-matrix logic)
    diff = he - ae
    lam_h = lam_a = 1.0
    if he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX:
        lam_h = GSV_LAMBDA_FACTOR
    elif ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX:
        lam_a = GSV_LAMBDA_FACTOR
    elif he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < diff <= GSV_LAMBDA_DIFF_EXTENDED:
        lam_h = GSV_LAMBDA_FACTOR_EXTENDED
    elif ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < -diff <= GSV_LAMBDA_DIFF_EXTENDED:
        lam_a = GSV_LAMBDA_FACTOR_EXTENDED
    if lam_h != 1.0 or lam_a != 1.0:
        mat_gsv = score_matrix(home, away,
                               custom_home_elo=he, custom_away_elo=ae,
                               before_date=before_date,
                               lam_scale_home=lam_h, lam_scale_away=lam_a,
                               custom_att_home=att_h, custom_def_home=def_h,
                               custom_att_away=att_a, custom_def_away=def_a)
        raw_gsv = matrix_to_probs(mat_gsv)
        for k, v in raw_gsv.items():
            if k.startswith("ah") or k.startswith("over") or k.startswith("under"):
                probs[k] = v
        mat = mat_gsv

    return probs, mat


def actual_outcome(hg: int, ag: int) -> str:
    if hg > ag: return "home_win"
    if hg == ag: return "draw"
    return "away_win"


def brier(prob: float, hit: bool) -> float:
    return (prob - (1 if hit else 0)) ** 2


def _ah_actual(hg: int, ag: int, line: float) -> float:
    """Return AH result for home team: 1=win, 0.5=push, 0=loss."""
    diff = hg - ag
    frac = line % 1
    if frac == 0.5:
        return 1.0 if diff > line else 0.0
    elif frac == 0.0:
        if diff > line: return 1.0
        if diff == int(line): return 0.5
        return 0.0
    else:
        return 0.5 * (_ah_actual(hg, ag, line - 0.25) + _ah_actual(hg, ag, line + 0.25))


def _ou_actual(hg: int, ag: int, line: float) -> float:
    """Return Over result: 1=win, 0.5=push, 0=loss."""
    total = hg + ag
    frac = line % 1
    if frac == 0.5:
        return 1.0 if total > line else 0.0
    elif frac == 0.0:
        if total > line: return 1.0
        if total == int(line): return 0.5
        return 0.0
    else:
        return 0.5 * (_ou_actual(hg, ag, line - 0.25) + _ou_actual(hg, ag, line + 0.25))


def calibration_table(preds: list[dict], label: str = ""):
    buckets = [(i / 10, (i + 1) / 10) for i in range(10)]
    rows = []
    for lo, hi in buckets:
        b = [p for p in preds if lo <= p["prob"] < hi]
        if len(b) < 2:
            continue
        act = sum(p["hit"] for p in b) / len(b)
        mod = sum(p["prob"] for p in b) / len(b)
        rows.append((f"{lo:.0%}-{hi:.0%}", len(b), mod, act))
    if not rows:
        return
    print(f"\n  {label}校准 (模型概率 vs 实际发生率):")
    print(f"  {'区间':<10} {'n':>5} {'模型':>8} {'实际':>8} {'差':>8}")
    print(f"  {'─'*44}")
    for interval, n, mod, act in rows:
        diff = act - mod
        flag = " ←" if abs(diff) > 0.12 else ""
        print(f"  {interval:<10} {n:>5} {mod*100:>7.1f}% {act*100:>7.1f}% {diff*100:>+7.1f}%{flag}")


def run_backtest(use_live_elo: bool = False, markets: str = "all", use_ad: bool = None):
    with open(RESULTS_PATH) as f:
        data = json.load(f)

    elo = load_elo(use_live_elo)
    show = set(markets.lower().split(",")) if markets != "all" else {"1x2", "ou", "ah", "cs"}

    # Load AD state explicitly (custom inject, not module-level cache)
    _use_ad = AD_ENABLED if use_ad is None else use_ad
    ad_state = {}
    if _use_ad:
        import os
        ad_path = "data/attack_defense_state.json"
        if os.path.exists(ad_path):
            with open(ad_path) as f_ad:
                ad_state = json.load(f_ad)

    records = []
    for m in data["matches"]:
        home, away = m["home"], m["away"]
        hg, ag = m["hg"], m["ag"]
        if home not in elo or away not in elo:
            continue

        probs, mat = predict_match(home, away, elo, before_date=m["date"],
                                   ad_state=ad_state if _use_ad else None)
        outcome = actual_outcome(hg, ag)
        predicted = max(["home_win", "draw", "away_win"], key=lambda k: probs[k])
        total = hg + ag

        rec = {
            "match":   f"{home} {hg}-{ag} {away}",
            "hg": hg, "ag": ag,
            "outcome": outcome, "predicted": predicted,
            "correct_1x2": outcome == predicted,
            "p_home": probs["home_win"], "p_draw": probs["draw"], "p_away": probs["away_win"],
            "p_outcome": probs[outcome],
            "total_goals": total,
        }

        # O/U per line
        for line in OU_LINES:
            key = f"over{str(line).replace('.', '')}"
            rec[f"ou_model_{line}"] = probs.get(key, ou_prob(mat, line))
            rec[f"ou_actual_{line}"] = _ou_actual(hg, ag, line)

        # AH per line (home giving goals)
        for line in AH_LINES:
            key = f"ah{str(line).replace('.', '')}"
            rec[f"ah_model_{line}"] = probs.get(key, ah_prob(mat, line))
            rec[f"ah_actual_{line}"] = _ah_actual(hg, ag, line)

        # Correct score
        top = mat.shape[0]
        actual_prob = float(mat[min(hg, top-1), min(ag, top-1)])
        rec["cs_actual_model_prob"] = actual_prob
        top1 = probs["top_scores"][0]
        rec["cs_top1_correct"] = (top1[0] == hg and top1[1] == ag)
        top3_scores = [(s[0], s[1]) for s in probs["top_scores"][:3]]
        rec["cs_top3_correct"] = (hg, ag) in top3_scores

        records.append(rec)

    if not records:
        print("没有有效记录")
        return

    n = len(records)

    # ── Header ──────────────────────────────────────────────────────────
    print(f"\n{'═'*65}")
    print(f"  回测报告  {n} 场比赛  (WC 2026 小组赛)")
    print(f"{'═'*65}")

    # ── 1X2 ─────────────────────────────────────────────────────────────
    if "1x2" in show or "all" in show:
        correct = sum(r["correct_1x2"] for r in records)
        brier_score = sum(
            brier(r["p_home"], r["outcome"] == "home_win") +
            brier(r["p_draw"], r["outcome"] == "draw") +
            brier(r["p_away"], r["outcome"] == "away_win")
            for r in records
        ) / n
        avg_goals = sum(r["total_goals"] for r in records) / n
        draws = sum(r["outcome"] == "draw" for r in records)

        print(f"\n  ── 1X2 独赢 ──────────────────────────────────────────────")
        print(f"  准确率:      {correct}/{n}  ({correct/n*100:.1f}%)")
        print(f"  Brier Score: {brier_score:.4f}  (随机≈0.667，完美=0)")
        print(f"  平均进球:    {avg_goals:.2f}  | 平局场次: {draws}/{n} ({draws/n*100:.0f}%)")

        roi = sum((1/max(r["p_home"],r["p_draw"],r["p_away"])-1) if r["correct_1x2"] else -1 for r in records)/n
        print(f"  理论ROI:     {roi*100:+.1f}% / 场 (最大概率选项，公平赔率平注)")

        print(f"\n  逐场明细:")
        print(f"  {'比赛':<35} {'实际':>6} {'预测':>6} {'概率':>7} {'✓'}")
        print(f"  {'─'*60}")
        label = {"home_win":"主胜","draw":"平","away_win":"客胜"}
        for r in records:
            mark = "✓" if r["correct_1x2"] else "✗"
            print(f"  {r['match']:<35} {label[r['outcome']]:>6} {label[r['predicted']]:>6} {r['p_outcome']*100:>6.1f}% {mark}")

        all_preds = []
        for r in records:
            all_preds += [
                {"prob": r["p_home"], "hit": r["outcome"] == "home_win"},
                {"prob": r["p_draw"], "hit": r["outcome"] == "draw"},
                {"prob": r["p_away"], "hit": r["outcome"] == "away_win"},
            ]
        calibration_table(all_preds, "1X2 ")

    # ── O/U ─────────────────────────────────────────────────────────────
    if "ou" in show or "all" in show:
        print(f"\n  ── 大小球 Over/Under ─────────────────────────────────────")
        print(f"  线    模型>50%时方向准确率    平均进球 {sum(r['total_goals'] for r in records)/n:.2f}")
        print(f"  {'线':<8} {'n>50%':>6} {'方向准':>8} {'模型均值':>10} {'实际O率':>10} {'推/退':>6}")
        print(f"  {'─'*55}")
        for line in OU_LINES:
            mk = f"ou_model_{line}"
            ak = f"ou_actual_{line}"
            model_vals = [r[mk] for r in records]
            actual_vals = [r[ak] for r in records]
            # Direction accuracy: when model says >0.5, was it over?
            call_over = [(r[mk] > 0.5, r[ak]) for r in records]
            correct_dir = sum(1 for pred, act in call_over if (pred and act >= 1.0) or (not pred and act == 0.0))
            push_n = sum(1 for r in records if 0 < r[ak] < 1)
            avg_model = sum(model_vals) / n
            avg_actual = sum(actual_vals) / n
            line_str = f"{line:.2f}".rstrip('0').rstrip('.')
            print(f"  O{line_str:<7} {n-push_n:>6}  {correct_dir/n*100:>7.1f}%  {avg_model*100:>9.1f}% {avg_actual*100:>9.1f}%  {push_n:>5}推")

        # O/U calibration for 2.5 and 3.0
        for line in [2.5, 3.0]:
            ou_preds = [{"prob": r[f"ou_model_{line}"], "hit": r[f"ou_actual_{line}"]} for r in records]
            calibration_table(ou_preds, f"O/U {line} ")

    # ── AH ──────────────────────────────────────────────────────────────
    if "ah" in show or "all" in show:
        print(f"\n  ── 让球 Asian Handicap (主队让球) ───────────────────────")
        print(f"  线    当模型AH概率>50%时方向准确   (正数=主队让球)")
        print(f"  {'线':<8} {'n>50%':>6} {'方向准':>8} {'模型均值':>10} {'实际覆盖':>10} {'推/退':>6}")
        print(f"  {'─'*55}")
        for line in AH_LINES:
            mk = f"ah_model_{line}"
            ak = f"ah_actual_{line}"
            push_n = sum(1 for r in records if 0 < r[ak] < 1)
            correct_dir = sum(
                1 for r in records
                if (r[mk] > 0.5 and r[ak] >= 1.0) or (r[mk] <= 0.5 and r[ak] == 0.0)
            )
            avg_model = sum(r[mk] for r in records) / n
            avg_actual = sum(r[ak] for r in records) / n
            line_str = f"{line:.2f}".rstrip('0').rstrip('.')
            print(f"  -{line_str:<7} {n-push_n:>6}  {correct_dir/n*100:>7.1f}%  {avg_model*100:>9.1f}% {avg_actual*100:>9.1f}%  {push_n:>5}推")

        # AH calibration for -1.0 and -1.5
        for line in [1.0, 1.5]:
            ah_preds = [{"prob": r[f"ah_model_{line}"], "hit": r[f"ah_actual_{line}"]} for r in records]
            calibration_table(ah_preds, f"AH -{line} ")

    # ── Correct Score ────────────────────────────────────────────────────
    if "cs" in show or "all" in show:
        top1_hit = sum(r["cs_top1_correct"] for r in records)
        top3_hit = sum(r["cs_top3_correct"] for r in records)
        avg_cs_prob = sum(r["cs_actual_model_prob"] for r in records) / n
        print(f"\n  ── 正确比分 Correct Score ────────────────────────────────")
        print(f"  Top-1 命中率: {top1_hit}/{n} ({top1_hit/n*100:.1f}%)")
        print(f"  Top-3 命中率: {top3_hit}/{n} ({top3_hit/n*100:.1f}%)")
        print(f"  实际比分平均模型概率: {avg_cs_prob*100:.2f}%  (公平赔率≈{1/avg_cs_prob:.0f}倍)")
        print(f"\n  市场对比说明:")
        print(f"    当前快照最优CS赔率 × 模型概率 > 1.0 时有理论EV")
        print(f"    例: 2-0 赔率6.3, 模型概率={float(matrix_to_probs(score_matrix('Spain','Saudi Arabia'))['top_scores'][0][2]):.3f}")

    print(f"\n{'═'*65}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-live-elo", action="store_true")
    parser.add_argument("--markets", default="all", help="1x2,ou,ah,cs 或 all")
    parser.add_argument("--no-ad", action="store_true", help="禁用攻防因子（A/B 对比）")
    args = parser.parse_args()
    run_backtest(use_live_elo=args.use_live_elo, markets=args.markets,
                 use_ad=False if args.no_ad else None)
