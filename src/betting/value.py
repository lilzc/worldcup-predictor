from config import MIN_EDGE
from src.betting.kelly import remove_margin, decimal_to_implied


def detect_value(model_prob: float, decimal_odds: float, true_prob: float = None) -> dict:
    market_prob = decimal_to_implied(decimal_odds)
    compare_prob = true_prob if true_prob is not None else market_prob
    edge = model_prob - compare_prob
    has_value = edge >= MIN_EDGE
    return {"edge": round(edge, 4), "has_value": has_value, "market_prob": round(market_prob, 4)}


def _devig2(odds_a: float, odds_b: float) -> tuple[float, float]:
    raw = [decimal_to_implied(odds_a), decimal_to_implied(odds_b)]
    true_p = remove_margin(raw)
    return true_p[0], true_p[1]


def analyze_market(
    model_probs: dict,
    odds_home: float,
    odds_draw: float,
    odds_away: float,
    odds_over25: float = None,
    odds_under25: float = None,
    # Extended markets (all optional)
    ou_odds: dict = None,       # {2.0: (over_odds, under_odds), 2.5: ..., 3.0: ..., 3.5: ...}
    ah_odds: dict = None,       # {1.5: (home_odds, away_odds), 0.5: ..., 1.0: ..., ...}
    cs_odds: dict = None,       # {"2-0": odds, "1-0": odds, ...}
) -> dict:
    """
    model_probs: output from matrix_to_probs() merged with adjustments.apply_all()
    ou_odds: dict mapping line (float) → (over_odds, under_odds)
    ah_odds: dict mapping line (float) → (home_cover_odds, away_cover_odds)
             line > 0 means home team is giving goals (favorite)
    cs_odds: dict mapping "hg-ag" string → decimal odds
    """
    # ── 1X2 ──────────────────────────────────────────────────────────────────
    raw_implied = [
        decimal_to_implied(odds_home),
        decimal_to_implied(odds_draw),
        decimal_to_implied(odds_away),
    ]
    true_implied = remove_margin(raw_implied)

    results = {
        "home_win": {
            "model": round(model_probs["home_win"], 4),
            "market_true": round(true_implied[0], 4),
            **detect_value(model_probs["home_win"], odds_home, true_implied[0]),
        },
        "draw": {
            "model": round(model_probs["draw"], 4),
            "market_true": round(true_implied[1], 4),
            **detect_value(model_probs["draw"], odds_draw, true_implied[1]),
        },
        "away_win": {
            "model": round(model_probs["away_win"], 4),
            "market_true": round(true_implied[2], 4),
            **detect_value(model_probs["away_win"], odds_away, true_implied[2]),
        },
    }

    # ── O/U 2.5 (backward compat) ────────────────────────────────────────────
    if odds_over25 is not None and odds_under25 is not None:
        t_o, t_u = _devig2(odds_over25, odds_under25)
        results["over25"] = {
            "model": round(model_probs.get("over25", 0), 4),
            "market_true": round(t_o, 4),
            **detect_value(model_probs.get("over25", 0), odds_over25, t_o),
        }
        results["under25"] = {
            "model": round(model_probs.get("under25", 0), 4),
            "market_true": round(t_u, 4),
            **detect_value(model_probs.get("under25", 0), odds_under25, t_u),
        }
    elif odds_over25 is not None:
        raw = decimal_to_implied(odds_over25)
        t = raw / 1.06
        results["over25"] = {
            "model": round(model_probs.get("over25", 0), 4),
            "market_true": round(t, 4),
            **detect_value(model_probs.get("over25", 0), odds_over25, t),
        }

    # ── Multi-line O/U ───────────────────────────────────────────────────────
    if ou_odds:
        results["ou_lines"] = {}
        for line, (o_odds, u_odds) in sorted(ou_odds.items()):
            key = f"over{str(line).replace('.', '')}"
            model_p = model_probs.get(key, None)
            if model_p is None:
                continue
            t_o, t_u = _devig2(o_odds, u_odds)
            results["ou_lines"][line] = {
                "over": {
                    "model": round(model_p, 4),
                    "market_true": round(t_o, 4),
                    **detect_value(model_p, o_odds, t_o),
                },
                "under": {
                    "model": round(1 - model_p, 4),
                    "market_true": round(t_u, 4),
                    **detect_value(1 - model_p, u_odds, t_u),
                },
            }

    # ── Asian Handicap ────────────────────────────────────────────────────────
    if ah_odds:
        results["ah_lines"] = {}
        for line, (home_odds, away_odds) in sorted(ah_odds.items()):
            key = f"ah{str(line).replace('.', '')}"
            model_home = model_probs.get(key, None)
            if model_home is None:
                continue
            t_h, t_a = _devig2(home_odds, away_odds)
            results["ah_lines"][line] = {
                "home": {
                    "model": round(model_home, 4),
                    "market_true": round(t_h, 4),
                    **detect_value(model_home, home_odds, t_h),
                },
                "away": {
                    "model": round(1 - model_home, 4),
                    "market_true": round(t_a, 4),
                    **detect_value(1 - model_home, away_odds, t_a),
                },
            }

    # ── Correct Score ─────────────────────────────────────────────────────────
    if cs_odds:
        results["correct_score"] = {}
        top_scores = model_probs.get("top_scores", [])
        score_map = {(s[0], s[1]): s[2] for s in top_scores}
        for score_str, odds_val in cs_odds.items():
            try:
                hg_s, ag_s = score_str.split("-")
                key = (int(hg_s), int(ag_s))
            except (ValueError, AttributeError):
                continue
            model_p = score_map.get(key, 0.001)
            market_p = decimal_to_implied(odds_val) / 1.15  # CS book has ~15% margin
            results["correct_score"][score_str] = {
                "model": round(model_p, 4),
                "market_true": round(market_p, 4),
                **detect_value(model_p, odds_val, market_p),
            }

    return results
