import math
import json
import os
import numpy as np
from config import TEAM_ELO, WC_GOAL_DISCOUNT


def _load_live_elo() -> dict:
    path = "data/elo_state.json"
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return TEAM_ELO


_ELO_CACHE: dict | None = None


def get_elo() -> dict:
    global _ELO_CACHE
    if _ELO_CACHE is None:
        _ELO_CACHE = _load_live_elo()
    return _ELO_CACHE


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

BASE_GOALS = 1.15  # WC group stage per-team average (empirically validated)
ELO_SCALE = 700    # 700 Elo points = e^1 ≈ 2.7x more goals


def elo_to_lambda(team_elo: float, opponent_elo: float) -> float:
    diff = team_elo - opponent_elo
    return BASE_GOALS * np.exp(diff / ELO_SCALE)


def dc_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    """Dixon-Coles low-score correction for 0-0, 1-0, 0-1, 1-1."""
    if x == 0 and y == 0:
        return max(1e-6, 1 - lam * mu * rho)
    elif x == 0 and y == 1:
        return 1 + lam * rho
    elif x == 1 and y == 0:
        return 1 + mu * rho
    elif x == 1 and y == 1:
        return max(1e-6, 1 - rho)
    return 1.0


WC_HOST_NATIONS = {"USA", "Canada", "Mexico"}  # genuine home crowd advantage


def score_matrix(
    home_team: str,
    away_team: str,
    max_goals: int = 8,
    rho: float = -0.13,
    home_advantage: float = None,   # None → auto-detect (host vs neutral)
    custom_home_elo: float = None,
    custom_away_elo: float = None,
) -> np.ndarray:
    """
    Build (max_goals+1) x (max_goals+1) probability matrix.
    rho: Dixon-Coles correlation parameter (typically -0.1 to -0.15)
    home_advantage: None = auto (1.05 for hosts, 1.0 for neutral-site games)
    """
    if home_advantage is None:
        home_advantage = 1.05 if home_team in WC_HOST_NATIONS else 1.0
    live_elo = get_elo()
    home_elo = custom_home_elo or live_elo.get(home_team, 1700)
    away_elo = custom_away_elo or live_elo.get(away_team, 1700)

    lam = elo_to_lambda(home_elo, away_elo) * home_advantage * WC_GOAL_DISCOUNT
    mu  = elo_to_lambda(away_elo, home_elo) * WC_GOAL_DISCOUNT

    mat = np.zeros((max_goals + 1, max_goals + 1))
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            tau = dc_tau(i, j, lam, mu, rho)
            mat[i, j] = tau * _poisson_pmf(i, lam) * _poisson_pmf(j, mu)

    mat /= mat.sum()
    return mat


def matrix_to_probs(mat: np.ndarray) -> dict:
    """Extract 1X2, O/U, and top correct scores from probability matrix."""
    home_win = float(np.tril(mat, -1).sum())
    draw     = float(np.diag(mat).sum())
    away_win = float(np.triu(mat, 1).sum())

    n = mat.shape[0]
    over25 = sum(mat[i, j] for i in range(n) for j in range(n) if i + j > 2)
    over35 = sum(mat[i, j] for i in range(n) for j in range(n) if i + j > 3)

    # Top 10 correct scores
    scores = []
    for i in range(n):
        for j in range(n):
            scores.append((i, j, float(mat[i, j])))
    scores.sort(key=lambda x: -x[2])

    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        "over25": float(over25),
        "over35": float(over35),
        "under25": float(1 - over25),
        "under35": float(1 - over35),
        "top_scores": scores[:10],
    }
