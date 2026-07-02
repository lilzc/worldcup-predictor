"""
B 系统：市场锚定 λ 反解引擎
物理隔离：无 Elo 调整、无 AD 因子、无 apply_all、无 Kelly。
λ 完全来自市场 1X2 赔率反解（B1 路线）。
选项 B：只拟合 P(home_win) / P(away_win)，平局由 score_matrix 统一产出。
"""
import math
import numpy as np
from scipy.optimize import minimize
from config import DC_RHO, BASE_GOALS

LAMBDA_MIN = 0.20
LAMBDA_MAX = 4.00


# ── 独立 Poisson+DC 矩阵构建（不依赖 poisson.py 内部状态）─────────────

def _pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def _dc_tau(x: int, y: int, lam: float, mu: float, rho: float) -> float:
    if x == 0 and y == 0: return max(1e-6, 1 - lam * mu * rho)
    if x == 0 and y == 1: return 1 + lam * rho
    if x == 1 and y == 0: return 1 + mu * rho
    if x == 1 and y == 1: return max(1e-6, 1 - rho)
    return 1.0


def build_b_matrix(lam: float, mu: float, max_goals: int = 8, rho: float = DC_RHO) -> np.ndarray:
    """Dixon-Coles matrix from given λ/μ. No team lookups, no Elo, no AD."""
    n = max_goals + 1
    mat = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            mat[i, j] = _dc_tau(i, j, lam, mu, rho) * _pmf(i, lam) * _pmf(j, mu)
    mat /= mat.sum()
    return mat


# ── 纯矩阵统计函数（复制自 poisson.py，避免 import 侧效应）──────────────

def _ou_prob(mat: np.ndarray, line: float) -> float:
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
        return 0.5 * (_ou_prob(mat, line - 0.25) + _ou_prob(mat, line + 0.25))


def _ah_prob(mat: np.ndarray, line: float) -> float:
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
        return 0.5 * (_ah_prob(mat, line - 0.25) + _ah_prob(mat, line + 0.25))


def _btts_prob(mat: np.ndarray) -> float:
    n = mat.shape[0]
    return float(sum(mat[i, j] for i in range(1, n) for j in range(1, n)))


def _matrix_probs(mat: np.ndarray) -> dict:
    """Extract 1X2, top scores, derived OU/AH/DC/BTTS from DC matrix."""
    n = mat.shape[0]
    home_win = float(np.tril(mat, -1).sum())
    draw     = float(np.diag(mat).sum())
    away_win = float(np.triu(mat, 1).sum())

    top_scores = sorted(
        [(i, j, float(mat[i, j])) for i in range(n) for j in range(n)],
        key=lambda x: -x[2]
    )

    return {
        'home_win': home_win,
        'draw':     draw,
        'away_win': away_win,
        'over15':   _ou_prob(mat, 1.5),
        'over25':   _ou_prob(mat, 2.5),
        'over35':   _ou_prob(mat, 3.5),
        'btts':     _btts_prob(mat),
        'dc_1x':    home_win + draw,
        'dc_x2':    draw + away_win,
        'dc_12':    home_win + away_win,
        'ah05':     _ah_prob(mat, 0.5),
        'ah10':     _ah_prob(mat, 1.0),
        'ah15':     _ah_prob(mat, 1.5),
        'ah20':     _ah_prob(mat, 2.0),
        'ah25':     _ah_prob(mat, 2.5),   # P(hg-ag > 2.5), home AH -2.5
        'ah_neg15': _ah_prob(mat, -1.5),  # P(hg-ag > -1.5) = 1 - P(away AH -1.5)
        'ah_neg25': _ah_prob(mat, -2.5),  # P(hg-ag > -2.5) = 1 - P(away AH -2.5)
        'top_scores': top_scores[:8],
    }


# ── λ 反解（B1 核心）────────────────────────────────────────────────────

def remove_margin_3way(h_odds: float, d_odds: float, a_odds: float) -> tuple:
    """三向去水，返回 (p_home, p_draw, p_away) 之和=1.0。"""
    raw = [1 / h_odds, 1 / d_odds, 1 / a_odds]
    total = sum(raw)
    return tuple(x / total for x in raw)


def solve_lambdas(
    p_home_win: float,
    p_away_win: float,
    rho: float = DC_RHO,
) -> tuple:
    """
    有界最小二乘：找 (λ, μ) 使 score_matrix 的 P(home_win) ≈ p_home_win，P(away_win) ≈ p_away_win。
    平局概率不进目标函数（选项 B）——由反解 λ 的完整 score_matrix 统一产出，
    避免 Poisson 无法表达市场平局分布时 λ 被推到畸形值。
    边界：λ, μ ∈ [0.20, 4.00]。
    返回 (lam, mu, diagnostics_dict)。
    """
    total = 2.6
    ratio = p_home_win / max(p_home_win + p_away_win, 0.01)
    lam0  = max(LAMBDA_MIN, min(LAMBDA_MAX, total * ratio))
    mu0   = max(LAMBDA_MIN, min(LAMBDA_MAX, total * (1 - ratio)))

    def objective(x):
        lam_h, lam_a = x
        mat  = build_b_matrix(lam_h, lam_a, rho=rho)
        p_h  = float(np.tril(mat, -1).sum())
        p_a  = float(np.triu(mat, 1).sum())
        return (p_h - p_home_win) ** 2 + (p_a - p_away_win) ** 2

    result = minimize(
        objective,
        [lam0, mu0],
        bounds=[(LAMBDA_MIN, LAMBDA_MAX), (LAMBDA_MIN, LAMBDA_MAX)],
        method='L-BFGS-B',
        options={'ftol': 1e-12, 'gtol': 1e-10},
    )
    lam, mu = result.x

    mat_check = build_b_matrix(lam, mu, rho=rho)
    p_h = float(np.tril(mat_check, -1).sum())
    p_d = float(np.diag(mat_check).sum())
    p_a = float(np.triu(mat_check, 1).sum())

    # Verify: same rho=0 solve for Δλ proof (DC pipeline actually used)
    from scipy.optimize import minimize as _min
    def _obj0(x):
        m = build_b_matrix(x[0], x[1], rho=0.0)
        ph = float(np.tril(m, -1).sum()); pa = float(np.triu(m, 1).sum())
        return (ph - p_home_win)**2 + (pa - p_away_win)**2
    _r0 = _min(_obj0, [lam, mu], bounds=[(LAMBDA_MIN, LAMBDA_MAX)]*2, method='L-BFGS-B',
               options={'ftol': 1e-12, 'gtol': 1e-10})
    lam0, mu0 = _r0.x

    p_draw_win = 1.0 - p_home_win - p_away_win
    diag = {
        'lam':           round(lam, 6),
        'mu':            round(mu, 6),
        'p_home_model':  round(p_h, 6),
        'p_draw_model':  round(p_d, 6),
        'p_away_model':  round(p_a, 6),
        'res_home':      round(p_h - p_home_win, 6),
        'res_draw':      round(p_d - p_draw_win, 6),
        'res_away':      round(p_a - p_away_win, 6),
        # Δλ vs pure-Poisson solve — proof DC pipeline is active
        'lam_poisson':   round(lam0, 6),
        'mu_poisson':    round(mu0, 6),
        'delta_lam':     round(lam - lam0, 5),
        'delta_mu':      round(mu - mu0, 5),
        'converged':     result.success,
    }
    return lam, mu, diag


def apply_news_adj(
    base_lam: float,
    base_mu: float,
    home_mult: float,
    away_mult: float,
) -> tuple:
    """Apply news multipliers to market-solved base lambdas (pure multiply)."""
    return base_lam * home_mult, base_mu * away_mult


def market_predict(
    home: str,
    away: str,
    home_odds: float,
    draw_odds: float,
    away_odds: float,
) -> dict:
    """
    B1 全流程：赔率 → 去水 → 反解 λ → DC矩阵 → 全量概率。
    不使用 AD、Elo、home_advantage、apply_all。
    """
    p_h_mkt, p_d_mkt, p_a_mkt = remove_margin_3way(home_odds, draw_odds, away_odds)
    lam, mu, diag = solve_lambdas(p_h_mkt, p_a_mkt)
    mat   = build_b_matrix(lam, mu)
    probs = _matrix_probs(mat)
    return {
        'home':      home,
        'away':      away,
        'lam':       lam,
        'mu':        mu,
        'diag':      diag,
        'market':    {'p_home': p_h_mkt, 'p_draw': p_d_mkt, 'p_away': p_a_mkt},
        'probs':     probs,
        'mat':       mat,
    }
