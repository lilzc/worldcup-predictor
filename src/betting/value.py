from config import MIN_EDGE
from src.betting.kelly import remove_margin, decimal_to_implied


def detect_value(model_prob: float, decimal_odds: float) -> dict:
    market_prob = decimal_to_implied(decimal_odds)
    edge = model_prob - market_prob
    has_value = edge >= MIN_EDGE
    return {"edge": round(edge, 4), "has_value": has_value, "market_prob": round(market_prob, 4)}


def analyze_market(
    model_probs: dict,
    odds_home: float,
    odds_draw: float,
    odds_away: float,
    odds_over25: float = None,
    odds_under25: float = None,
) -> dict:
    """
    model_probs: output from adjustments.apply_all + poisson over/under probs
    odds_*: decimal odds
    Returns value analysis for each market.
    """
    # Remove bookmaker margin from 1X2
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
            **detect_value(model_probs["home_win"], odds_home),
        },
        "draw": {
            "model": round(model_probs["draw"], 4),
            "market_true": round(true_implied[1], 4),
            **detect_value(model_probs["draw"], odds_draw),
        },
        "away_win": {
            "model": round(model_probs["away_win"], 4),
            "market_true": round(true_implied[2], 4),
            **detect_value(model_probs["away_win"], odds_away),
        },
    }

    if odds_over25:
        results["over25"] = {
            "model": round(model_probs.get("over25", 0), 4),
            **detect_value(model_probs.get("over25", 0), odds_over25),
        }
    if odds_under25:
        results["under25"] = {
            "model": round(model_probs.get("under25", 0), 4),
            **detect_value(model_probs.get("under25", 0), odds_under25),
        }

    return results
