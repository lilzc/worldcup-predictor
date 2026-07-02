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

from config import (TEAM_ELO, BANKROLL,
                    GSV_LAMBDA_FACTOR, GSV_LAMBDA_ELO_MIN,
                    GSV_LAMBDA_DIFF_MIN, GSV_LAMBDA_DIFF_MAX,
                    GSV_LAMBDA_FACTOR_EXTENDED, GSV_LAMBDA_DIFF_EXTENDED,
                    HT_DRAW_KILL_ELO_DIFF)
from src.models.poisson import (score_matrix, matrix_to_probs, get_elo, get_lambdas,
                                ht_score_matrix, ht_matrix_to_probs)
from src.models.adjustments import apply_all
from src.betting.kelly import american_to_decimal, build_portfolio
from src.betting.value import analyze_market


def parse_odds_arg(val: str) -> float:
    """Accept American (+150, -120) or decimal (2.50) odds string."""
    if val is None:
        return None
    v = float(val)
    if abs(v) >= 100:
        return american_to_decimal(int(v))
    return v  # already decimal


def _fmt_row(label_m: str, v: dict):
    model_pct  = f"{v['model']*100:.1f}%"
    market_pct = f"{v.get('market_true', v.get('market_prob', 0))*100:.1f}%"
    edge_pct   = f"{v['edge']*100:+.1f}%"
    star = " ⭐" if v["has_value"] else ""
    print(f"  {label_m:<16} {model_pct:>8} {market_pct:>10} {edge_pct:>8}{star}")


def print_probs_table(label: str, probs: dict, odds: dict, value: dict):
    print(f"\n{'─'*56}")
    print(f"  {label}")
    print(f"{'─'*56}")
    print(f"  {'市场':<16} {'模型':>8} {'市场真实':>10} {'边际':>8} {'价值':>6}")
    print(f"  {'─'*52}")

    # 1X2
    for label_m, key in [("主场胜","home_win"),("平局","draw"),("客场胜","away_win")]:
        if key in value: _fmt_row(label_m, value[key])

    # O/U 2.5 (backward compat)
    for label_m, key in [("Over 2.5","over25"),("Under 2.5","under25")]:
        if key in value: _fmt_row(label_m, value[key])

    # Multi-line O/U
    if "ou_lines" in value:
        print(f"  {'─'*52}")
        for line, sides in sorted(value["ou_lines"].items()):
            line_str = f"{line:.2f}".rstrip("0").rstrip(".")
            _fmt_row(f"Over {line_str}", sides["over"])
            _fmt_row(f"Under {line_str}", sides["under"])

    # AH
    if "ah_lines" in value:
        print(f"  {'─'*52}")
        for line, sides in sorted(value["ah_lines"].items()):
            line_str = f"{line:.2f}".rstrip("0").rstrip(".")
            _fmt_row(f"AH -{line_str} 主", sides["home"])
            _fmt_row(f"AH +{line_str} 客", sides["away"])

    # CS
    if "correct_score" in value:
        print(f"  {'─'*52}")
        cs_vals = sorted(value["correct_score"].items(), key=lambda x: -x[1]["edge"])
        for score_str, v in cs_vals[:6]:
            if v["has_value"] or v["edge"] > -0.05:
                _fmt_row(f"比分 {score_str}", v)


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
    force_scores: list = None,
    # Extended markets
    ou_odds: dict = None,       # {2.5: (over_odds, under_odds), 3.0: (...), ...}
    ah_odds: dict = None,       # {1.5: (home_odds, away_odds), 1.0: (...), ...}
    cs_odds: dict = None,       # {"2-0": odds, "1-0": odds, ...}
    # Half-time markets (all optional)
    ht_1x2_odds: tuple = None,  # (home_odds, draw_odds, away_odds)
    ht_ou_odds: dict = None,    # {0.5: (over_odds, under_odds), 1.0: ..., 1.5: ...}
    ht_ah_odds: dict = None,    # {0.5: (home_odds, away_odds), 1.0: ...}
):
    bankroll = bankroll or BANKROLL

    # ── 1. Poisson score matrix ───────────────────────────────────────────
    mat = score_matrix(home_team, away_team)
    raw_probs = matrix_to_probs(mat)

    # ── 2. Apply all adjustments ──────────────────────────────────────────
    _live = get_elo()
    _he = _live.get(home_team, 1700)
    _ae = _live.get(away_team, 1700)
    _diff = _he - _ae
    adj = apply_all(
        home_team, away_team,
        raw_probs["home_win"], raw_probs["draw"], raw_probs["away_win"],
        h2h_home_edge=h2h_home_edge,
        home_elo=float(_he), away_elo=float(_ae),
    )
    # Merge adjusted 1X2 back into full probs dict
    probs = {**raw_probs, **adj}

    # ── 2b. GSV lambda frustration-zone correction for AH/O/U ────────────
    # Raw Poisson AH/O/U uses unadjusted λ.  For Elo>1850 teams in the
    # "frustration zone" (Elo diff 150-300), actual goals = 0.79x model λ.
    # Compute a corrected matrix for AH/O/U only; 1X2 stays with adj above.
    _lam_h = _lam_a = 1.0
    if (_he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= _diff <= GSV_LAMBDA_DIFF_MAX):
        _lam_h = GSV_LAMBDA_FACTOR
    elif (_ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -_diff <= GSV_LAMBDA_DIFF_MAX):
        _lam_a = GSV_LAMBDA_FACTOR
    elif (_he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < _diff <= GSV_LAMBDA_DIFF_EXTENDED):
        _lam_h = GSV_LAMBDA_FACTOR_EXTENDED
    elif (_ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < -_diff <= GSV_LAMBDA_DIFF_EXTENDED):
        _lam_a = GSV_LAMBDA_FACTOR_EXTENDED
    if _lam_h != 1.0 or _lam_a != 1.0:
        mat_gsv = score_matrix(home_team, away_team,
                               lam_scale_home=_lam_h, lam_scale_away=_lam_a)
        raw_gsv = matrix_to_probs(mat_gsv)
        # Override AH and O/U keys with GSV-corrected values
        for _k, _v in raw_gsv.items():
            if _k.startswith("ah") or _k.startswith("over") or _k.startswith("under"):
                probs[_k] = _v

    print(f"\n{'═'*52}")
    print(f"  {home_team}  vs  {away_team}")
    he, ae = _live.get(home_team), _live.get(away_team)
    if he and ae:
        print(f"  Elo差: {he-ae:+.0f}  ({he:.0f} vs {ae:.0f})")
    lam_info = get_lambdas(home_team, away_team)
    if _lam_h == GSV_LAMBDA_FACTOR or _lam_a == GSV_LAMBDA_FACTOR:
        gsv_note = f"  [GSV λ×{GSV_LAMBDA_FACTOR}]"
    elif _lam_h == GSV_LAMBDA_FACTOR_EXTENDED or _lam_a == GSV_LAMBDA_FACTOR_EXTENDED:
        gsv_note = f"  [GSV-EXT λ×{GSV_LAMBDA_FACTOR_EXTENDED}]"
    else:
        gsv_note = ""
    print(f"  λ(主队期望进球): {lam_info['lam']:.3f}  μ(客队期望进球): {lam_info['mu']:.3f}{gsv_note}")
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
        ou_odds=ou_odds,
        ah_odds=ah_odds,
        cs_odds=cs_odds,
    )
    print_probs_table(f"{home_team} vs {away_team}", probs, {}, value)

    # ── 5. Kelly portfolio ────────────────────────────────────────────────
    bets = []
    # 1X2 + O/U 2.5
    market_map = {
        "home_win":  (f"主场胜 ({home_team})",  odds_home),
        "draw":      ("平局",                     odds_draw),
        "away_win":  (f"客场胜 ({away_team})",   odds_away),
        "over25":    ("Over 2.5",                 odds_over25),
        "under25":   ("Under 2.5",                odds_under25),
    }
    for key, (lbl, dec_odds) in market_map.items():
        if dec_odds is None or key not in value:
            continue
        if value[key]["has_value"]:
            bets.append({"label": lbl, "model_prob": probs.get(key, 0), "decimal_odds": dec_odds,
                         "market_true": value[key]["market_true"]})

    # Multi-line O/U
    if ou_odds and "ou_lines" in value:
        for line, sides in value["ou_lines"].items():
            o_odds, u_odds = ou_odds[line]
            ls = f"{line:.2f}".rstrip("0").rstrip(".")
            key_o = f"over{str(line).replace('.','')}"
            if sides["over"]["has_value"]:
                bets.append({"label": f"Over {ls}", "model_prob": probs.get(key_o, 0), "decimal_odds": o_odds,
                             "market_true": sides["over"]["market_true"]})
            if sides["under"]["has_value"]:
                bets.append({"label": f"Under {ls}", "model_prob": 1 - probs.get(key_o, 0), "decimal_odds": u_odds,
                             "market_true": sides["under"]["market_true"]})

    # AH
    if ah_odds and "ah_lines" in value:
        for line, sides in value["ah_lines"].items():
            h_odds, a_odds = ah_odds[line]
            ls = f"{line:.2f}".rstrip("0").rstrip(".")
            key_ah = f"ah{str(line).replace('.','')}"
            if sides["home"]["has_value"]:
                bets.append({"label": f"AH -{ls} {home_team}", "model_prob": probs.get(key_ah, 0), "decimal_odds": h_odds,
                             "market_true": sides["home"]["market_true"]})
            if sides["away"]["has_value"]:
                bets.append({"label": f"AH +{ls} {away_team}", "model_prob": 1 - probs.get(key_ah, 0), "decimal_odds": a_odds,
                             "market_true": sides["away"]["market_true"]})

    # CS
    if cs_odds and "correct_score" in value:
        for score_str, cs_v in value["correct_score"].items():
            if cs_v["has_value"]:
                bets.append({"label": f"比分 {score_str}", "model_prob": cs_v["model"], "decimal_odds": cs_odds[score_str],
                             "market_true": cs_v["market_true"]})

    portfolio = build_portfolio(bets, bankroll)
    print_bets(portfolio)
    print_correct_scores(home_team, away_team, raw_probs["top_scores"])

    # ── 6. Half-time analysis ─────────────────────────────────────────────
    ht_probs, ht_value = {}, {}
    if any([ht_1x2_odds, ht_ou_odds, ht_ah_odds]):
        mat_ht = ht_score_matrix(home_team, away_team,
                                  lam_scale_home=_lam_h, lam_scale_away=_lam_a)
        ht_probs = ht_matrix_to_probs(mat_ht)

        # Remap ht_* keys to standard keys so analyze_market can look them up
        ht_compat = {
            "home_win": ht_probs["ht_home_win"],
            "draw":     ht_probs["ht_draw"],
            "away_win": ht_probs["ht_away_win"],
        }
        for k, v in ht_probs.items():
            if k.startswith("ht_"):
                ht_compat[k[3:]] = v  # "ht_over05" → "over05", "ht_ah05" → "ah05"

        h1o, hdo, hao = (ht_1x2_odds or (None, None, None))
        ht_value = analyze_market(
            ht_compat,
            odds_home=h1o, odds_draw=hdo, odds_away=hao,
            ou_odds=ht_ou_odds,
            ah_odds=ht_ah_odds,
        )

        # HT平局KILL：Elo差≥250时市场低估强队半场进球，平局推单可信度低
        if abs(_diff) >= HT_DRAW_KILL_ELO_DIFF and "draw" in ht_value:
            ht_value["draw"]["killed"] = f"HT_DRAW_KILL (Elo差{_diff:+.0f})"
            ht_value["draw"]["edge"] = 0.0

        print(f"\n  ── 上半场分析 ──────────────────────────────────────────")
        print(f"  HT模型: {home_team}胜 {ht_probs['ht_home_win']*100:.1f}%"
              f"  平 {ht_probs['ht_draw']*100:.1f}%"
              f"  {away_team}胜 {ht_probs['ht_away_win']*100:.1f}%")
        print(f"  HT Over1.5: {(1-ht_probs.get('ht_over15',0.5))*100:.1f}%小  "
              f"Over1.0: {ht_probs.get('ht_over10',0.5)*100:.1f}%大")
        print_probs_table(f"HT {home_team} vs {away_team}", ht_probs, {}, ht_value)

    return {"probs": probs, "value": value, "portfolio": portfolio, "mat": mat,
            "ht_probs": ht_probs, "ht_value": ht_value}


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
