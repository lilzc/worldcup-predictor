#!/usr/bin/env python3
"""
用法:
  python predict.py Netherlands Sweden
  python predict.py Netherlands Sweden --home -145 --draw +290 --away +380 --over -110
  python predict.py Netherlands Sweden --home 2.10 --draw 3.40 --away 3.60 --over 1.91 --bankroll 2000
"""

import sys
import argparse
import numpy as np

sys.path.insert(0, ".")

from config import TEAM_ELO, BANKROLL
from src.models.poisson import score_matrix, matrix_to_probs
from src.models.adjustments import apply_all
from src.betting.kelly import american_to_decimal, build_portfolio
from src.betting.value import analyze_market


def parse_odds_arg(val: str) -> float:
    """Accept American (+150, -120) or decimal (2.50) odds string."""
    if val is None:
        return None
    v = float(val)
    if val.startswith("+") or (v > 0 and v < 10 and not val.startswith("-")):
        # Could be decimal — treat <10 as decimal, else American
        if abs(v) >= 100:
            return american_to_decimal(int(v))
    if abs(v) >= 100:
        return american_to_decimal(int(v))
    return v  # already decimal


def print_probs_table(label: str, probs: dict, odds: dict, value: dict):
    print(f"\n{'─'*52}")
    print(f"  {label}")
    print(f"{'─'*52}")
    print(f"  {'市场':<12} {'模型':>8} {'市场真实':>10} {'边际':>8} {'价值':>6}")
    print(f"  {'─'*48}")

    markets = [
        ("主场胜", "home_win"),
        ("平局",   "draw"),
        ("客场胜", "away_win"),
        ("Over 2.5", "over25"),
        ("Under 2.5", "under25"),
    ]

    for label_m, key in markets:
        if key not in value:
            continue
        v = value[key]
        model_pct  = f"{v['model']*100:.1f}%"
        market_pct = f"{v.get('market_true', v.get('market_prob',0))*100:.1f}%"
        edge_pct   = f"{v['edge']*100:+.1f}%"
        star = " ⭐" if v["has_value"] else ""
        print(f"  {label_m:<12} {model_pct:>8} {market_pct:>10} {edge_pct:>8}{star}")


def print_correct_scores(home: str, away: str, top_scores: list, n: int = 6):
    print(f"\n  波胆（前{n}）:")
    for i, j, p in top_scores[:n]:
        bar = "█" * int(p * 200)
        print(f"    {home} {i}-{j} {away}  {p*100:5.1f}%  {bar}")


def print_bets(portfolio: list[dict]):
    if not portfolio:
        print("\n  无正向Kelly仓位")
        return
    print(f"\n  建仓方案:")
    print(f"  {'投注标的':<28} {'赔率':>8} {'模型':>8} {'下注':>8} {'预期盈利':>10}")
    print(f"  {'─'*64}")
    total_stake = 0
    total_ev = 0
    for b in portfolio:
        if b["stake"] <= 0:
            continue
        print(
            f"  {b['label']:<28} "
            f"{b['decimal_odds']:>8.2f} "
            f"{b['model_prob']*100:>7.1f}% "
            f"¥{b['stake']:>7.0f} "
            f"¥{b['ev']:>9.1f}"
        )
        total_stake += b["stake"]
        total_ev += b["ev"]
    print(f"  {'─'*64}")
    print(f"  {'合计':<28} {'':>8} {'':>8} ¥{total_stake:>7.0f} ¥{total_ev:>9.1f}")


def predict(
    home_team: str,
    away_team: str,
    odds_home: float = None,
    odds_draw: float = None,
    odds_away: float = None,
    odds_over25: float = None,
    odds_under25: float = None,
    bankroll: float = None,
    h2h_home_edge: float = 0.0,
    force_scores: list = None,   # e.g. [(2,1), (1,0)] — 强制推荐的波胆
):
    bankroll = bankroll or BANKROLL

    # ── 1. Poisson score matrix ───────────────────────────────────────────
    mat = score_matrix(home_team, away_team)
    raw_probs = matrix_to_probs(mat)

    # ── 2. Apply all adjustments ──────────────────────────────────────────
    adj = apply_all(
        home_team, away_team,
        raw_probs["home_win"], raw_probs["draw"], raw_probs["away_win"],
        h2h_home_edge=h2h_home_edge,
    )
    # Merge adjusted 1X2 back into full probs dict
    probs = {**raw_probs, **adj}

    from src.models.poisson import get_elo
    live = get_elo()
    print(f"\n{'═'*52}")
    print(f"  {home_team}  vs  {away_team}")
    he, ae = live.get(home_team), live.get(away_team)
    if he and ae:
        print(f"  Elo差: {he-ae:+.0f}  ({he:.0f} vs {ae:.0f})")
    print(f"{'═'*52}")

    # ── 3. Raw model output (no odds needed) ─────────────────────────────
    if not any([odds_home, odds_draw, odds_away]):
        print(f"\n  模型概率（无市场赔率对比）:")
        print(f"  主场胜: {probs['home_win']*100:.1f}%")
        print(f"  平局:   {probs['draw']*100:.1f}%")
        print(f"  客场胜: {probs['away_win']*100:.1f}%")
        print(f"  Over2.5:{probs['over25']*100:.1f}%")
        print_correct_scores(home_team, away_team, probs["top_scores"])
        return probs

    # ── 4. Value detection ────────────────────────────────────────────────
    value = analyze_market(
        probs,
        odds_home=odds_home,
        odds_draw=odds_draw,
        odds_away=odds_away,
        odds_over25=odds_over25,
        odds_under25=odds_under25,
    )
    print_probs_table(f"{home_team} vs {away_team}", probs, {}, value)

    # ── 5. Kelly portfolio ────────────────────────────────────────────────
    bets = []
    market_map = {
        "home_win":  (f"主场胜 ({home_team})",  odds_home),
        "draw":      ("平局",                     odds_draw),
        "away_win":  (f"客场胜 ({away_team})",   odds_away),
        "over25":    ("Over 2.5",                 odds_over25),
        "under25":   ("Under 2.5",                odds_under25),
    }
    for key, (label, dec_odds) in market_map.items():
        if dec_odds is None or key not in value:
            continue
        if value[key]["has_value"]:
            bets.append({
                "label":       label,
                "model_prob":  probs.get(key, 0),
                "decimal_odds": dec_odds,
            })

    # Forced correct scores (always include 2 per match)
    scores_to_add = force_scores or [
        raw_probs["top_scores"][0][:2],
        raw_probs["top_scores"][1][:2],
    ]
    for i, j in scores_to_add:
        score_prob = float(mat[i, j])
        score_label = f"波胆 {home_team} {i}-{j} {away_team}"
        # Correct score bookmaker margin ≈ 30% (higher than 1X2/OU)
        implied_cs_odds = 1 / (score_prob * 0.70) if score_prob > 0 else 999
        bets.append({
            "label":        score_label,
            "model_prob":   score_prob,
            "decimal_odds": implied_cs_odds,
        })

    portfolio = build_portfolio(bets, bankroll)
    print_bets(portfolio)
    print_correct_scores(home_team, away_team, raw_probs["top_scores"])

    return {"probs": probs, "value": value, "portfolio": portfolio}


def main():
    parser = argparse.ArgumentParser(description="世界杯比赛预测")
    parser.add_argument("home", help="主队名（英文，如 Netherlands）")
    parser.add_argument("away", help="客队名（英文，如 Sweden）")
    parser.add_argument("--home",    dest="odds_home",    type=str, default=None)
    parser.add_argument("--draw",    dest="odds_draw",    type=str, default=None)
    parser.add_argument("--away",    dest="odds_away",    type=str, default=None)
    parser.add_argument("--over",    dest="odds_over25",  type=str, default=None)
    parser.add_argument("--under",   dest="odds_under25", type=str, default=None)
    parser.add_argument("--bankroll",dest="bankroll",     type=float, default=None)
    parser.add_argument("--h2h",     dest="h2h",          type=float, default=0.0,
                        help="H2H主队优势 -1到+1")
    args = parser.parse_args()

    predict(
        home_team=args.home,
        away_team=args.away,
        odds_home=parse_odds_arg(args.odds_home),
        odds_draw=parse_odds_arg(args.odds_draw),
        odds_away=parse_odds_arg(args.odds_away),
        odds_over25=parse_odds_arg(args.odds_over25),
        odds_under25=parse_odds_arg(args.odds_under25),
        bankroll=args.bankroll,
        h2h_home_edge=args.h2h,
    )


if __name__ == "__main__":
    main()
