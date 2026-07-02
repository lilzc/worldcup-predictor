from config import KELLY_FRACTION


def american_to_decimal(american: int) -> float:
    if american > 0:
        return american / 100 + 1
    return 100 / abs(american) + 1


def decimal_to_implied(decimal_odds: float) -> float:
    return 1 / decimal_odds


def remove_margin(probs_raw: list[float]) -> list[float]:
    """Normalize bookmaker odds to remove vig."""
    total = sum(probs_raw)
    return [p / total for p in probs_raw]


def kelly_fraction(model_prob: float, decimal_odds: float) -> float:
    """Full Kelly * KELLY_FRACTION."""
    b = decimal_odds - 1
    if b <= 0:
        return 0.0
    q = 1 - model_prob
    f = (b * model_prob - q) / b
    return max(0.0, f * KELLY_FRACTION)


def size_bet(model_prob: float, decimal_odds: float, bankroll: float) -> float:
    f = kelly_fraction(model_prob, decimal_odds)
    return round(f * bankroll, 0)


def build_portfolio(bets: list[dict], bankroll: float) -> list[dict]:
    """
    bets: [{"label": str, "model_prob": float, "decimal_odds": float}]
    Scale proportionally if total > bankroll.
    """
    for bet in bets:
        bet["raw_stake"] = size_bet(bet["model_prob"], bet["decimal_odds"], bankroll)

    total = sum(b["raw_stake"] for b in bets)
    scale = min(1.0, bankroll / total) if total > 0 else 1.0

    for bet in bets:
        bet["stake"] = round(bet["raw_stake"] * scale, 0)
        market_true = bet.get("market_true", 1 / bet["decimal_odds"])
        bet["edge"] = bet["model_prob"] - market_true
        bet["ev"] = (bet["model_prob"] * bet["decimal_odds"] - 1) * bet["stake"]

    return sorted(bets, key=lambda x: -x["ev"])
