#!/usr/bin/env python3
"""
回测：用模型预测已完赛事，对比实际结果，报告准确率与校准误差。

用法:
  python3 backtest.py               # 全部已记录赛事
  python3 backtest.py --use-live-elo # 用 data/elo_state.json 的实时Elo（更准确）
"""

import json
import sys
import argparse

sys.path.insert(0, ".")

from src.models.poisson import score_matrix, matrix_to_probs
from src.models.adjustments import apply_all
from config import TEAM_ELO

RESULTS_PATH = "data/wc2026_results.json"


def load_elo(use_live: bool) -> dict:
    if use_live:
        import os
        path = "data/elo_state.json"
        if os.path.exists(path):
            with open(path) as f:
                return json.load(f)
    return dict(TEAM_ELO)


def predict_match(home: str, away: str, elo: dict) -> dict:
    home_elo = elo.get(home)
    away_elo = elo.get(away)
    mat = score_matrix(
        home, away,
        custom_home_elo=home_elo,
        custom_away_elo=away_elo,
    )
    raw = matrix_to_probs(mat)
    adj = apply_all(home, away, raw["home_win"], raw["draw"], raw["away_win"])
    return {**raw, **adj}


def actual_outcome(hg: int, ag: int) -> str:
    if hg > ag:
        return "home_win"
    if hg == ag:
        return "draw"
    return "away_win"


def brier_score(prob: float, hit: bool) -> float:
    return (prob - (1 if hit else 0)) ** 2


def run_backtest(use_live_elo: bool = False):
    with open(RESULTS_PATH) as f:
        data = json.load(f)

    elo = load_elo(use_live_elo)
    matches = data["matches"]

    # ── Per-match results ────────────────────────────────────────────────
    records = []
    for m in matches:
        home, away = m["home"], m["away"]
        hg, ag = m["hg"], m["ag"]

        # Skip teams not in Elo table
        if home not in elo or away not in elo:
            continue

        probs = predict_match(home, away, elo)
        outcome = actual_outcome(hg, ag)
        predicted = max(["home_win", "draw", "away_win"], key=lambda k: probs[k])

        records.append({
            "match":     f"{home} {hg}-{ag} {away}",
            "outcome":   outcome,
            "predicted": predicted,
            "correct_1x2": outcome == predicted,
            "p_outcome": probs[outcome],
            "p_home":    probs["home_win"],
            "p_draw":    probs["draw"],
            "p_away":    probs["away_win"],
            "over25_model": probs["over25"],
            "over25_actual": (hg + ag) > 2,
            "total_goals": hg + ag,
        })

    if not records:
        print("没有有效记录，请检查 data/wc2026_results.json 中的球队名称")
        return

    n = len(records)
    correct_1x2 = sum(r["correct_1x2"] for r in records)
    # Multi-class Brier: sum of squared errors across all 3 outcomes per match
    brier = sum(
        brier_score(r["p_home"],  r["outcome"] == "home_win") +
        brier_score(r["p_draw"],  r["outcome"] == "draw") +
        brier_score(r["p_away"],  r["outcome"] == "away_win")
        for r in records
    ) / n
    avg_goals = sum(r["total_goals"] for r in records) / n
    over25_correct = sum(
        r["over25_actual"] == (r["over25_model"] > 0.5) for r in records
    )

    # ── Calibration buckets (model prob vs empirical hit rate) ───────────
    # Expand each match into 3 outcome predictions for proper calibration
    all_preds = []
    for r in records:
        all_preds.append({"prob": r["p_home"],  "hit": r["outcome"] == "home_win"})
        all_preds.append({"prob": r["p_draw"],  "hit": r["outcome"] == "draw"})
        all_preds.append({"prob": r["p_away"],  "hit": r["outcome"] == "away_win"})

    buckets = [(i/10, (i+1)/10) for i in range(10)]
    cal_rows = []
    for lo, hi in buckets:
        bucket = [p for p in all_preds if lo <= p["prob"] < hi]
        if len(bucket) < 2:
            continue
        actual_rate = sum(p["hit"] for p in bucket) / len(bucket)
        avg_model   = sum(p["prob"] for p in bucket) / len(bucket)
        cal_rows.append((f"{lo:.0%}-{hi:.0%}", len(bucket), avg_model, actual_rate))

    # ── Over/Under breakdown ─────────────────────────────────────────
    blowout  = [r for r in records if max(r["p_home"], r["p_away"]) > 0.70]
    balanced = [r for r in records if max(r["p_home"], r["p_away"]) <= 0.70]

    def over_rate(lst):
        if not lst:
            return 0, 0
        hits = sum(r["over25_actual"] for r in lst)
        return hits, len(lst)

    # ── Print report ────────────────────────────────────────────────
    print(f"\n{'═'*60}")
    print(f"  回测报告  {n} 场比赛")
    print(f"{'═'*60}")
    print(f"\n  1X2 准确率:    {correct_1x2}/{n}  ({correct_1x2/n*100:.1f}%)")
    print(f"  Brier Score:   {brier:.4f}  (越低越好，随机≈0.667，完美=0)")
    print(f"  平均进球数:    {avg_goals:.2f}")
    print(f"  Over2.5 方向准确: {over25_correct}/{n} ({over25_correct/n*100:.1f}%)")

    ho, hn = over_rate(blowout)
    bo, bn = over_rate(balanced)
    print(f"\n  Over2.5 拆解:")
    print(f"    大热门场次 (主/客>70%): {ho}/{hn} = {ho/hn*100:.0f}%" if hn else "    大热门场次: 无数据")
    print(f"    竞争性场次 (主/客≤70%): {bo}/{bn} = {bo/bn*100:.0f}%" if bn else "    竞争性场次: 无数据")

    print(f"\n  逐场明细:")
    print(f"  {'比赛':<35} {'实际':>8} {'预测':>8} {'模型概率':>10} {'✓'}")
    print(f"  {'─'*65}")
    for r in records:
        mark = "✓" if r["correct_1x2"] else "✗"
        outcome_label = {"home_win":"主胜", "draw":"平", "away_win":"客胜"}
        print(
            f"  {r['match']:<35} "
            f"{outcome_label[r['outcome']]:>8} "
            f"{outcome_label[r['predicted']]:>8} "
            f"{r['p_outcome']*100:>9.1f}% "
            f"{mark}"
        )

    print(f"\n  校准分析 (模型概率 vs 实际发生率):")
    print(f"  {'概率区间':<12} {'样本':>6} {'模型均值':>10} {'实发率':>10}")
    print(f"  {'─'*42}")
    for row in cal_rows:
        print(f"  {row[0]:<12} {row[1]:>6} {row[2]*100:>9.1f}% {row[3]*100:>9.1f}%")

    # ROI: flat-bet each match on model's top pick at fair odds = 1/p_predicted
    roi_total = 0.0
    for r in records:
        p_top = max(r["p_home"], r["p_draw"], r["p_away"])
        fair_odds = 1 / p_top
        if r["correct_1x2"]:
            roi_total += fair_odds - 1
        else:
            roi_total -= 1
    print(f"\n  理论ROI（最大概率选项，公平赔率平注）: {roi_total/n*100:+.1f}% / 场")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-live-elo", action="store_true")
    args = parser.parse_args()
    run_backtest(use_live_elo=args.use_live_elo)
