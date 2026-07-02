import math
import json
import os
import numpy as np
from config import (TEAM_ELO, WC_GOAL_DISCOUNT, BASE_GOALS, ELO_SCALE, DC_RHO, HT_LAMBDA_FACTOR,
                    AD_ENABLED, AD_SHRINKAGE_K, AD_BLEND_WEIGHT, AD_MIN_MATCHES, AD_CAP_LO, AD_CAP_HI)


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


AD_STATE_PATH = "data/attack_defense_state.json"
_AD_CACHE: dict | None = None


def _load_ad_state() -> dict:
    if os.path.exists(AD_STATE_PATH):
        with open(AD_STATE_PATH) as f:
            return json.load(f)
    return {}


def get_ad_state() -> dict:
    global _AD_CACHE
    if _AD_CACHE is None:
        _AD_CACHE = _load_ad_state()
    return _AD_CACHE


def compute_ad_factor(ad_state: dict, team: str) -> tuple[float, float]:
    """Returns (att_applied, def_applied). Pure function, no side effects.
    att > 1 = 进球多于 Elo 预期; def > 1 = 失球多于 Elo 预期（防守差）。
    """
    s = ad_state.get(team, {})
    if s.get("n", 0) < AD_MIN_MATCHES:
        return 1.0, 1.0
    att_r = (s["att_goals_sum"] + AD_SHRINKAGE_K) / (s["att_exp_sum"] + AD_SHRINKAGE_K)
    def_r = (s["def_goals_sum"] + AD_SHRINKAGE_K) / (s["def_exp_sum"] + AD_SHRINKAGE_K)
    att = max(AD_CAP_LO, min(AD_CAP_HI, 1.0 + AD_BLEND_WEIGHT * (att_r - 1.0)))
    dfn = max(AD_CAP_LO, min(AD_CAP_HI, 1.0 + AD_BLEND_WEIGHT * (def_r - 1.0)))
    return att, dfn


def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

# BASE_GOALS and ELO_SCALE are defined in config.py (require spec+backtest before changing)


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
    rho: float = DC_RHO,
    home_advantage: float = None,
    custom_home_elo: float = None,
    custom_away_elo: float = None,
    use_form: bool = True,
    before_date: str = None,
    lam_scale_home: float = 1.0,
    lam_scale_away: float = 1.0,
    # AD custom injection: bypasses get_ad_state() when provided (for walkforward/backtest)
    custom_att_home: float = None,
    custom_def_home: float = None,
    custom_att_away: float = None,
    custom_def_away: float = None,
) -> np.ndarray:
    """
    Build (max_goals+1) x (max_goals+1) probability matrix.
    rho: Dixon-Coles correlation parameter (typically -0.1 to -0.15)
    home_advantage: None = auto (1.05 for hosts, 1.0 for neutral-site games)
    lam_scale_home/away: optional GSV lambda correction (e.g. 0.80 for frustration-zone matches)
    custom_att/def_*: attack/defense factors; when provided, bypass get_ad_state() cache
    """
    if home_advantage is None:
        home_advantage = 1.05 if home_team in WC_HOST_NATIONS else 1.0
    live_elo = get_elo()
    home_elo = custom_home_elo if custom_home_elo is not None else live_elo.get(home_team, 1700)
    away_elo = custom_away_elo if custom_away_elo is not None else live_elo.get(away_team, 1700)

    lam = elo_to_lambda(home_elo, away_elo) * home_advantage * WC_GOAL_DISCOUNT * lam_scale_home
    mu  = elo_to_lambda(away_elo, home_elo) * WC_GOAL_DISCOUNT * lam_scale_away

    if use_form:
        try:
            from src.data.form import get_team_form
            home_form = get_team_form(home_team, before_date=before_date)
            away_form = get_team_form(away_team, before_date=before_date)
            FORM_WEIGHT = 0.10  # reduced from 0.20: club form transfers ~50% to WC context
            lam *= (1 - FORM_WEIGHT) + FORM_WEIGHT * home_form["form_factor"]
            mu  *= (1 - FORM_WEIGHT) + FORM_WEIGHT * away_form["form_factor"]
        except Exception:
            pass

    # Attack/defense tempo factor: decouples geometric mean from Elo diff lock
    # λ_home_new = λ_elo × att_home × def_away   (home attacks vs away defense)
    # λ_away_new = λ_elo × att_away × def_home
    if AD_ENABLED:
        if custom_att_home is not None:
            att_h, def_h = custom_att_home, custom_def_home
            att_a, def_a = custom_att_away, custom_def_away
        else:
            _ad = get_ad_state()
            att_h, def_h = compute_ad_factor(_ad, home_team)
            att_a, def_a = compute_ad_factor(_ad, away_team)
        lam *= att_h * def_a
        mu  *= att_a * def_h

    mat = np.zeros((max_goals + 1, max_goals + 1))
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            tau = dc_tau(i, j, lam, mu, rho)
            mat[i, j] = tau * _poisson_pmf(i, lam) * _poisson_pmf(j, mu)

    mat /= mat.sum()
    return mat


def get_lambdas(
    home_team: str,
    away_team: str,
    home_advantage: float = None,
    custom_home_elo: float = None,
    custom_away_elo: float = None,
    use_form: bool = True,
    before_date: str = None,
) -> dict:
    """Return final λ/μ used by score_matrix (after form adjustment). For display only."""
    if home_advantage is None:
        home_advantage = 1.05 if home_team in WC_HOST_NATIONS else 1.0
    live_elo = get_elo()
    home_elo = custom_home_elo if custom_home_elo is not None else live_elo.get(home_team, 1700)
    away_elo = custom_away_elo if custom_away_elo is not None else live_elo.get(away_team, 1700)

    lam = elo_to_lambda(home_elo, away_elo) * home_advantage * WC_GOAL_DISCOUNT
    mu  = elo_to_lambda(away_elo, home_elo) * WC_GOAL_DISCOUNT

    if use_form:
        try:
            from src.data.form import get_team_form
            FORM_WEIGHT = 0.10
            lam *= (1 - FORM_WEIGHT) + FORM_WEIGHT * get_team_form(home_team, before_date=before_date)["form_factor"]
            mu  *= (1 - FORM_WEIGHT) + FORM_WEIGHT * get_team_form(away_team, before_date=before_date)["form_factor"]
        except Exception:
            pass

    if AD_ENABLED:
        _ad = get_ad_state()
        att_h, def_h = compute_ad_factor(_ad, home_team)
        att_a, def_a = compute_ad_factor(_ad, away_team)
        lam *= att_h * def_a
        mu  *= att_a * def_h

    return {"lam": round(lam, 3), "mu": round(mu, 3)}


def ou_prob(mat: np.ndarray, line: float) -> float:
    """
    Effective Over probability at given line. Handles whole/half/quarter (split) lines.
    Quarter lines (e.g. 2.25 = split 2/2.5, 2.75 = split 2.5/3) return average of
    adjacent half-lines, per Asian market convention (push on exact whole total).
    """
    n = mat.shape[0]
    frac = line % 1
    if frac == 0.0:
        whole = int(line)
        over = sum(mat[i, j] for i in range(n) for j in range(n) if i + j > whole)
        push = sum(mat[i, j] for i in range(n) for j in range(n) if i + j == whole)
        return float(over + 0.5 * push)
    elif frac == 0.5:
        return float(sum(mat[i, j] for i in range(n) for j in range(n) if i + j > line))
    else:
        return 0.5 * (ou_prob(mat, line - 0.25) + ou_prob(mat, line + 0.25))


def ah_prob(mat: np.ndarray, line: float) -> float:
    """
    Effective home-team AH win probability at given line (positive = home gives goals).
    line=1.5 means home must win by 2+; line=0.75 means half on 0.5, half on 1.
    Push at whole number margins; quarter lines split per Asian convention.
    """
    n = mat.shape[0]
    frac = line % 1
    if frac == 0.0:
        whole = int(line)
        win  = sum(mat[i, j] for i in range(n) for j in range(n) if i - j > whole)
        push = sum(mat[i, j] for i in range(n) for j in range(n) if i - j == whole)
        return float(win + 0.5 * push)
    elif frac == 0.5:
        return float(sum(mat[i, j] for i in range(n) for j in range(n) if i - j > line))
    else:
        return 0.5 * (ah_prob(mat, line - 0.25) + ah_prob(mat, line + 0.25))


def ht_score_matrix(
    home_team: str,
    away_team: str,
    max_goals: int = 4,
    rho: float = DC_RHO,
    home_advantage: float = None,
    custom_home_elo: float = None,
    custom_away_elo: float = None,
    use_form: bool = True,
    before_date: str = None,
    lam_scale_home: float = 1.0,
    lam_scale_away: float = 1.0,
) -> np.ndarray:
    """
    Half-time score matrix: λ_HT = λ_FT × HT_LAMBDA_FACTOR (0.46).
    WC 2026实测：上半场46%进球，下半场54%（76-90+分钟扎堆导致）。
    max_goals=4 足够（WC上半场单队3球极罕见）。
    """
    return score_matrix(
        home_team, away_team,
        max_goals=max_goals,
        rho=rho,
        home_advantage=home_advantage,
        custom_home_elo=custom_home_elo,
        custom_away_elo=custom_away_elo,
        use_form=use_form,
        before_date=before_date,
        lam_scale_home=lam_scale_home * HT_LAMBDA_FACTOR,
        lam_scale_away=lam_scale_away * HT_LAMBDA_FACTOR,
    )


def ht_matrix_to_probs(mat: np.ndarray) -> dict:
    """
    Extract HT 1X2, HT OU (0.5/0.75/1.0/1.25/1.5/1.75/2.0),
    HT AH (0.25/0.5/0.75/1.0/1.25/1.5), and top HT correct scores.
    Keys prefixed with 'ht_' to avoid collision with full-game keys.
    """
    home_win = float(np.tril(mat, -1).sum())
    draw     = float(np.diag(mat).sum())
    away_win = float(np.triu(mat, 1).sum())

    ht_ou_lines = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    ht_ah_lines = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5]

    ou_probs = {f"ht_over{str(l).replace('.', '')}": ou_prob(mat, l) for l in ht_ou_lines}
    ah_probs = {f"ht_ah{str(l).replace('.', '')}": ah_prob(mat, l) for l in ht_ah_lines}

    n = mat.shape[0]
    scores = sorted(
        [(i, j, float(mat[i, j])) for i in range(n) for j in range(n)],
        key=lambda x: -x[2]
    )
    return {
        "ht_home_win": home_win,
        "ht_draw": draw,
        "ht_away_win": away_win,
        **ou_probs,
        **ah_probs,
        "ht_top_scores": scores[:6],
    }


def matrix_to_probs(mat: np.ndarray) -> dict:
    """
    Extract 1X2, O/U (multiple lines), AH (multiple lines), and top correct scores.
    O/U lines: 1.5–4.0 in 0.25 steps.  AH lines: 0.25–3.5 in 0.25 steps (home giving).
    All probabilities are effective (push = 0.5 weight) per Asian handicap convention.
    """
    home_win = float(np.tril(mat, -1).sum())
    draw     = float(np.diag(mat).sum())
    away_win = float(np.triu(mat, 1).sum())

    # O/U lines covering all lines in typical WC odds board
    ou_lines = [1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5, 3.75, 4.0]
    # AH lines (home giving goals; negate for home receiving)
    ah_lines = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75, 3.0, 3.25, 3.5]

    ou_probs = {f"over{str(l).replace('.', '')}": ou_prob(mat, l) for l in ou_lines}
    ah_probs = {f"ah{str(l).replace('.', '')}": ah_prob(mat, l) for l in ah_lines}

    # Top 10 correct scores
    n = mat.shape[0]
    scores = sorted(
        [(i, j, float(mat[i, j])) for i in range(n) for j in range(n)],
        key=lambda x: -x[2]
    )

    return {
        "home_win": home_win,
        "draw": draw,
        "away_win": away_win,
        # Backward-compat aliases
        "over25": ou_prob(mat, 2.5),
        "over35": ou_prob(mat, 3.5),
        "under25": 1 - ou_prob(mat, 2.5),
        "under35": 1 - ou_prob(mat, 3.5),
        **ou_probs,
        **ah_probs,
        "top_scores": scores[:10],
    }
