"""
All probability adjustment factors synthesized from three repos + our backtest.
Applied after base Poisson model in sequence.
"""

from config import DEFENDING_CHAMPION, UCL_MENTALITY, PROB_CAP


# ── 1. Favorite-Longshot Bias (Repo2: 彩票悖论) ─────────────────────────────
# Academic consensus: markets overprice heavy favorites, underprice large underdogs.

def flb_correction(p: float) -> float:
    if p > 0.70:
        return p - 0.05 * (p - 0.70) / 0.30
    if p < 0.10:
        return p + 0.03
    return p


# ── 2. Probability cap (Repo3: 85%硬上限) ───────────────────────────────────

def cap_probs(hw: float, d: float, aw: float) -> tuple[float, float, float]:
    if hw >= aw and hw > PROB_CAP:
        overflow = hw - PROB_CAP
        hw = PROB_CAP
        d  += overflow * 0.60
        aw += overflow * 0.40
    elif aw > hw and aw > PROB_CAP:
        overflow = aw - PROB_CAP
        aw = PROB_CAP
        d  += overflow * 0.60
        hw += overflow * 0.40
    return hw, d, aw


# ── 3. Defending champion curse (Repo2: 卫冕冠军诅咒) ────────────────────────
# No team has won back-to-back WCs since 1962.

CHAMPION_PENALTY = 0.05

def defending_champion_adjustment(team: str, prob: float) -> float:
    if team == DEFENDING_CHAMPION:
        return prob - CHAMPION_PENALTY
    return prob


# ── 4. UCL mentality signal (Repo2: 欧冠心态) ────────────────────────────────
# Positive = finals winner mentality; negative = consecutive exits

def ucl_adjustment(team: str, prob: float) -> float:
    delta = UCL_MENTALITY.get(team, 0.0)
    return prob + delta * 0.5   # dampen: WC context ≠ UCL context


# ── 5. H2H adjustment (Repo3: 头对头历史) ────────────────────────────────────
# Optional — pass in h2h_edge computed externally.

def h2h_adjustment(prob: float, h2h_edge: float) -> float:
    return prob + h2h_edge * 0.10   # 10% weight on H2H signal


# ── 6. Group Stage Volatility (Repo2: 小组赛强队波动) ────────────────────────
# Strong teams (Elo>1850) underperform in group stage — rotate/experiment.
# Back-tested: Brazil 1-1 Morocco, Spain 0-0 Cape Verde both validated this.

from config import TEAM_ELO as _BASE_ELO

def group_stage_volatility(home_team: str, away_team: str,
                            hw: float, d: float, aw: float,
                            is_group_stage: bool = True) -> tuple[float, float, float]:
    if not is_group_stage:
        return hw, d, aw
    live_elo = _BASE_ELO  # use base as approximation here
    penalty = 0.06
    if live_elo.get(home_team, 0) > 1850 and hw > aw:
        hw -= penalty
        d  += penalty * 0.7
        aw += penalty * 0.3
    if live_elo.get(away_team, 0) > 1850 and aw > hw:
        aw -= penalty
        d  += penalty * 0.7
        hw += penalty * 0.3
    return hw, d, aw


# ── Master apply function ────────────────────────────────────────────────────

def apply_all(
    home_team: str,
    away_team: str,
    hw: float,
    d: float,
    aw: float,
    h2h_home_edge: float = 0.0,
    is_group_stage: bool = True,
) -> dict:
    # FLB
    hw = flb_correction(hw)
    aw = flb_correction(aw)

    # Defending champion curse
    hw = defending_champion_adjustment(home_team, hw)
    aw = defending_champion_adjustment(away_team, aw)

    # UCL mentality
    hw = ucl_adjustment(home_team, hw)
    aw = ucl_adjustment(away_team, aw)

    # H2H
    if h2h_home_edge != 0:
        hw = h2h_adjustment(hw, h2h_home_edge)
        aw = h2h_adjustment(aw, -h2h_home_edge)

    # Group stage volatility
    hw, d, aw = group_stage_volatility(home_team, away_team, hw, d, aw, is_group_stage)

    # Normalize
    total = hw + d + aw
    hw, d, aw = hw / total, d / total, aw / total

    # Cap
    hw, d, aw = cap_probs(hw, d, aw)

    # Final normalize
    total = hw + d + aw
    return {
        "home_win": hw / total,
        "draw":     d  / total,
        "away_win": aw / total,
    }
