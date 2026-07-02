#!/usr/bin/env python3
"""
Walk-Forward 真实赔率回测
用法：python3 walkforward.py

数据源：
  - 比赛结果: data/wc2026_results.json
  - 赔率:     MATCHES 字典（从赔率文件手工录入，time-stamped）

每场取 edge 最高的 OK 档注单（gap < ARTIFACT_GAP），不下 LOW/KILL 档。
负线 AH 全部跳过（已知概率计算不准确，且历史上均无可信 edge）。
"""

import sys
import math
import json
sys.path.insert(0, ".")

from src.models.poisson import score_matrix, matrix_to_probs, ah_prob, ou_prob, compute_ad_factor
from src.models.adjustments import apply_all
from src.betting.kelly import remove_margin, kelly_fraction
from config import (TEAM_ELO, BASE_GOALS, ELO_SCALE,
                    GSV_LAMBDA_FACTOR, GSV_LAMBDA_ELO_MIN,
                    GSV_LAMBDA_DIFF_MIN, GSV_LAMBDA_DIFF_MAX,
                    GSV_LAMBDA_FACTOR_EXTENDED, GSV_LAMBDA_DIFF_EXTENDED,
                    BANKROLL, KELLY_FRACTION, MIN_EDGE, AD_ENABLED)

ARTIFACT_GAP   = 0.08   # gap ≥ 8% → LOW（不下）
ARTIFACT_KILL  = 0.20   # gap ≥ 20% → KILL（丢弃）
MIN_MODEL_PROB = 0.15   # 模型概率 < 15% → 丢弃（防GSV过修正深长赔）
MIN_WIN_PROB   = 0.25   # 1X2胜负：模型概率 < 25% → 跳过（低置信高赔率注不如OU替代）
STAKE          = 100    # 每注等额 ¥100（回测用）

# ─────────────────────────────────────────────────────────────────────────────
# 真实赔率数据（来源：世界杯2026赔率_按日期.md）
# 格式：
#   key = (home_en, away_en, results_date)
#   value = {
#     "1x2":  (home_odds, draw_odds, away_odds),
#     "ou":   [(line, over_odds, under_odds), ...],   主线优先放第一
#     "ah":   [(line, home_odds, away_odds), ...]     正值=主队让分，负值=主队受让
#   }
#
# results_date = 比赛在 data/wc2026_results.json 里对应的日期（中国时间-1天）
# ─────────────────────────────────────────────────────────────────────────────

MATCHES_ODDS = {
    # ── 06-16 赔率文件 = 06-15 results ────────────────────────────────────
    ("Belgium", "Egypt", "2026-06-15"): {
        "1x2": (1.50, 4.15, 6.60),
        "ou":  [(2.5,1.97,1.93),(2.75,2.23,1.71),(2.25,1.70,2.25),(3.0,2.66,1.50)],
        "ah":  [(1.0,1.93,1.99),(1.25,2.28,1.70),(0.75,1.64,2.38),
                (1.5,2.58,1.55),(0.5,1.50,2.72),(1.75,3.08,1.40)],
    },

    # ── 06-18 赔率文件 = 06-17 results ────────────────────────────────────
    ("Portugal", "Congo DR", "2026-06-17"): {
        "1x2": (1.27, 5.60, 11.00),
        "ou":  [(2.75,2.06,1.84),(2.5,1.81,2.09),(3.0,2.40,1.61),
                (2.25,1.60,2.42),(3.25,2.69,1.49)],
        "ah":  [(1.5,1.88,2.04),(1.75,2.13,1.80),(1.25,1.65,2.36),
                (2.0,2.56,1.56),(2.25,2.88,1.45),(1.0,1.43,2.96)],
    },
    ("England", "Croatia", "2026-06-17"): {
        "1x2": (1.73, 3.60, 5.00),
        "ou":  [(2.25,1.90,2.00),(2.5,2.19,1.74),(2.0,1.61,2.40),
                (2.75,2.53,1.55),(1.75,1.47,2.75)],
        "ah":  [(0.75,1.96,1.96),(0.5,1.73,2.23),(1.0,2.35,1.66),
                (0.25,1.51,2.69),(1.25,2.72,1.50),(1.5,3.04,1.41)],
    },
    ("Ghana", "Panama", "2026-06-17"): {
        "1x2": (2.31, 3.35, 3.10),
        "ou":  [(2.25,2.11,1.80),(2.0,1.78,2.13),(2.5,2.40,1.61),(1.75,1.57,2.49)],
        "ah":  [(0.25,2.07,1.85),(0.5,2.35,1.66),(0.75,2.81,1.47)],
    },
    ("Uzbekistan", "Colombia", "2026-06-17"): {
        "1x2": (8.40, 4.80, 1.38),
        "ou":  [(2.5,1.98,1.92),(2.25,1.72,2.21),(2.75,2.23,1.71),(3.0,2.66,1.50)],
        "ah":  [],   # 全负线，跳过
    },

    # ── 06-19 赔率文件 = 06-18 results ────────────────────────────────────
    ("Switzerland", "Bosnia", "2026-06-18"): {
        "1x2": (1.54, 4.20, 6.00),
        "ou":  [(2.5,2.01,1.89),(2.25,1.74,2.19),(2.75,2.28,1.68),(2.0,1.48,2.72)],
        "ah":  [(1.0,1.95,1.97),(1.25,2.28,1.70),(0.75,1.67,2.33),
                (1.5,2.58,1.55),(0.5,1.54,2.61),(1.75,3.08,1.40)],
    },
    ("Canada", "Qatar", "2026-06-18"): {
        "1x2": (1.28, 5.60, 10.00),
        "ou":  [(2.75,1.94,1.96),(2.5,1.74,2.19),(3.0,2.25,1.70),(3.25,2.53,1.55)],
        "ah":  [(1.5,1.89,2.03),(1.75,2.13,1.80),(1.25,1.66,2.35),
                (2.0,2.56,1.56),(2.25,2.85,1.46),(1.0,1.44,2.92)],
    },
    ("Mexico", "South Korea", "2026-06-18"): {
        "1x2": (2.11, 3.30, 3.60),
        "ou":  [(2.25,2.02,1.88),(2.0,1.71,2.23),(2.5,2.31,1.66),(1.75,1.53,2.58)],
        "ah":  [(0.5,2.11,1.82),(0.25,1.81,2.12),(0.75,2.47,1.60)],
    },

    # ── 06-20 赔率文件 = 06-19 results ────────────────────────────────────
    ("USA", "Australia", "2026-06-19"): {
        "1x2": (1.63, 4.00, 5.20),
        "ou":  [(2.5,1.95,1.95),(2.75,2.17,1.75),(2.25,1.70,2.25),(3.0,2.56,1.54)],
        "ah":  [(1.0,2.13,1.80),(0.75,1.83,2.09),(0.5,1.64,2.38),(1.25,2.47,1.60)],
    },
    ("Scotland", "Morocco", "2026-06-19"): {
        "1x2": (5.40, 3.45, 1.72),
        "ou":  [(2.25,2.02,1.88),(2.0,1.72,2.21),(2.5,2.31,1.66)],
        "ah":  [],   # 全负线，跳过
    },
    ("Brazil", "Haiti", "2026-06-19"): {
        "1x2": (1.09, 10.50, 23.00),
        "ou":  [(3.75,2.04,1.84),(3.5,1.83,2.05),(4.0,2.31,1.64),(3.25,1.64,2.31)],
        "ah":  [(2.75,2.04,1.86),(2.5,1.83,2.07),(3.0,2.33,1.65),(2.25,1.64,2.35)],
    },
    ("Turkey", "Paraguay", "2026-06-19"): {
        "1x2": (2.08, 3.45, 3.50),
        "ou":  [(2.25,1.83,2.07),(2.5,2.09,1.81),(2.75,2.40,1.61),(2.0,1.55,2.53)],
        "ah":  [(0.5,2.08,1.84),(0.25,1.80,2.13),(0.75,2.42,1.62)],
    },

    # ── 06-21 赔率文件 = 06-20 results ────────────────────────────────────
    ("Netherlands", "Sweden", "2026-06-20"): {
        "1x2": (1.74, 4.00, 4.40),
        "ou":  [(2.75,1.84,2.06),(3.0,2.12,1.79),(2.5,1.67,2.29),(3.25,2.40,1.61)],
        "ah":  [(0.75,1.92,2.00),(0.5,1.72,2.25),(1.0,2.25,1.72),
                (1.25,2.56,1.56),(0.25,1.53,2.63),(1.5,2.85,1.46)],
    },
    ("Germany", "Ivory Coast", "2026-06-20"): {
        "1x2": (1.50, 4.65, 5.50),
        "ou":  [(3.0,1.96,1.94),(2.75,1.74,2.19),(3.25,2.23,1.71),(2.5,1.59,2.44)],
        "ah":  [(1.0,1.87,2.05),(1.25,2.14,1.79),(0.75,1.66,2.35),
                (1.5,2.42,1.62),(0.5,1.53,2.63),(1.75,2.81,1.47)],
    },
    ("Ecuador", "Curacao", "2026-06-20"): {
        "1x2": (1.13, 8.30, 20.00),
        "ou":  [(3.0,1.94,1.96),(2.75,1.72,2.21),(3.25,2.21,1.72),(2.5,1.58,2.47)],
        "ah":  [(2.25,2.01,1.91),(2.0,1.73,2.23),(2.5,2.25,1.72),(1.75,1.57,2.53)],
    },
    ("Tunisia", "Japan", "2026-06-20"): {
        "1x2": (5.80, 4.00, 1.58),
        "ou":  [(2.25,1.87,2.03),(2.5,2.14,1.77),(2.0,1.59,2.44)],
        "ah":  [],   # 全负线，跳过
    },

    # ── 06-22 赔率文件 = 06-21 results ────────────────────────────────────
    ("Spain", "Saudi Arabia", "2026-06-21"): {
        "1x2": (1.08, 10.50, 26.00),
        "ou":  [(3.5,1.99,1.89),(3.25,1.74,2.16),(3.75,2.21,1.70),(3.0,1.56,2.47)],
        "ah":  [(2.75,2.05,1.85),(2.5,1.82,2.08),(3.0,2.35,1.64),
                (2.25,1.62,2.38),(3.25,2.61,1.52),(2.0,1.45,2.81)],
    },
    ("Belgium", "Iran", "2026-06-21"): {
        "1x2": (1.44, 4.65, 7.00),
        "ou":  [(2.5,1.84,2.06),(2.75,2.08,1.82),(2.25,1.62,2.38),(3.0,2.44,1.59)],
        "ah":  [(1.25,2.01,1.91),(1.0,1.73,2.23),(1.5,2.29,1.69),
                (0.75,1.55,2.58),(1.75,2.69,1.51),(0.5,1.45,2.88)],
    },
    ("Uruguay", "Cape Verde", "2026-06-21"): {
        "1x2": (1.44, 4.20, 7.80),
        "ou":  [(2.25,2.05,1.85),(2.0,1.73,2.20),(2.5,2.35,1.64),(1.75,1.55,2.53)],
        "ah":  [(1.0,1.82,2.11),(1.25,2.16,1.78),(0.75,1.59,2.49),
                (1.5,2.47,1.60),(0.5,1.47,2.81),(1.75,2.92,1.44)],
    },
    ("New Zealand", "Egypt", "2026-06-21"): {
        "1x2": (5.60, 4.00, 1.59),
        "ou":  [(2.25,1.84,2.06),(2.5,2.11,1.80),(2.75,2.42,1.60),(2.0,1.56,2.51)],
        "ah":  [],   # 全负线，跳过
    },

    # ── 06-23 赔率文件 = 06-22 results ────────────────────────────────────
    ("Argentina", "Austria", "2026-06-22"): {
        "1x2": (1.44, 4.45, 6.60),
        "ou":  [(2.5,1.93,1.97),(2.75,2.20,1.73),(2.25,1.67,2.29),(3.0,2.53,1.55)],
        "ah":  [(1.25,2.11,1.82),(1.0,1.79,2.14),(1.5,2.42,1.62),
                (0.75,1.59,2.49),(0.5,1.48,2.78),(1.75,2.81,1.47)],
    },
    ("France", "Iraq", "2026-06-22"): {
        "1x2": (1.06, 11.50, 27.00),
        "ou":  [(3.75,2.05,1.83),(3.5,1.83,2.05),(3.25,1.64,2.31),(4.0,2.31,1.64)],
        "ah":  [(2.75,1.93,1.97),(2.5,1.74,2.19),(3.0,2.20,1.73),
                (3.25,2.44,1.59),(2.25,1.57,2.49),(3.5,2.66,1.50)],
    },
    ("Norway", "Senegal", "2026-06-22"): {
        "1x2": (2.13, 3.50, 3.25),
        "ou":  [(2.5,1.87,2.03),(2.75,2.13,1.78),(2.25,1.64,2.35),(3.0,2.53,1.55)],
        "ah":  [(0.25,1.87,2.05),(0.5,2.14,1.79),(0.75,2.49,1.59)],
    },
    ("Jordan", "Algeria", "2026-06-22"): {
        "1x2": (6.40, 4.00, 1.55),
        "ou":  [(2.5,1.94,1.96),(2.75,2.20,1.73),(2.25,1.70,2.25),(3.0,2.61,1.52)],
        "ah":  [],   # 全负线（Algeria客队让球），跳过
    },

    # ── 06-26 赔率文件 = 06-26 results ────────────────────────────────────
    ("Curacao", "Ivory Coast", "2026-06-26"): {
        "1x2": (16.00, 7.90, 1.15),
        "ou":  [(3.25,1.97,1.93),(3.5,2.21,1.72),(3.0,1.72,2.21),(2.75,1.57,2.49)],
        "ah":  [(-2.25,1.86,2.06),(-2.0,2.14,1.79),(-2.5,1.67,2.33),
                (-1.75,2.40,1.63),(-2.75,1.52,2.66),(-1.5,2.72,1.50)],
    },
    ("Ecuador", "Germany", "2026-06-26"): {
        "1x2": (5.60, 4.40, 1.54),
        "ou":  [(2.75,1.85,2.05),(3.0,2.13,1.78),(2.5,1.68,2.28),(3.25,2.42,1.60)],
        "ah":  [(-1.0,2.00,1.92),(-1.25,1.72,2.25),(-0.75,2.31,1.68),
                (-1.5,1.58,2.51),(-0.5,2.58,1.55),(-1.75,1.44,2.92)],
    },
    ("Tunisia", "Netherlands", "2026-06-26"): {
        "1x2": (29.00, 11.00, 1.07),
        "ou":  [(3.5,2.04,1.84),(3.25,1.82,2.06),(3.75,2.28,1.66),(3.0,1.60,2.38)],
        "ah":  [(-2.75,1.88,2.02),(-2.5,2.13,1.78),(-3.0,1.66,2.31),
                (-2.25,2.42,1.60),(-3.25,1.54,2.56),(-3.5,1.45,2.81)],
    },
    ("Japan", "Sweden", "2026-06-26"): {
        "1x2": (1.87, 3.65, 3.90),
        "ou":  [(2.5,1.92,1.98),(2.75,2.19,1.74),(2.25,1.66,2.31),(3.0,2.53,1.55)],
        "ah":  [(0.5,1.90,2.02),(0.75,2.17,1.77),(0.25,1.65,2.36),
                (1.0,2.66,1.52),(0.0,1.43,2.96),(1.25,3.00,1.42),(1.5,3.32,1.35)],
    },
    ("Paraguay", "Australia", "2026-06-26"): {
        "1x2": (2.69, 2.19, 3.90),
        "ou":  [(1.75,1.89,2.01),(2.0,2.28,1.68),(1.5,1.67,2.29),(2.25,2.63,1.51)],
        "ah":  [(0.25,2.13,1.80),(0.0,1.64,2.38),(0.5,2.63,1.53),
                (-0.25,1.40,3.08),(0.75,3.12,1.39)],
    },
    ("Turkey", "USA", "2026-06-26"): {
        "1x2": (3.55, 3.75, 1.95),
        "ou":  [(2.75,1.88,2.00),(3.0,2.19,1.72),(2.5,1.69,2.23),(3.25,2.42,1.58)],
        "ah":  [(-0.5,1.94,1.96),(-0.75,1.73,2.20),(-0.25,2.25,1.70),
                (-1.0,1.52,2.61),(0.0,2.72,1.48),(-1.25,1.40,3.00)],
    },

    # ── 06-27 赔率文件 = 06-27 results ────────────────────────────────────
    ("Norway", "France", "2026-06-27"): {
        "1x2": (4.35, 4.45, 1.65),
        "ou":  [(3.25,2.08,1.82),(3.0,1.82,2.08),(3.5,2.31,1.66),(2.75,1.63,2.36)],
        "ah":  [(-0.75,2.08,1.84),(-1.0,1.80,2.13),(-0.5,2.35,1.66),
                (-1.25,1.62,2.42),(-1.5,1.51,2.69),(-0.25,2.75,1.49)],
    },
    ("Senegal", "Iraq", "2026-06-27"): {
        "1x2": (1.21, 6.70, 12.00),
        "ou":  [(3.0,1.83,2.05),(3.25,2.08,1.80),(2.75,1.64,2.31),(3.5,2.35,1.62)],
        "ah":  [(2.0,2.09,1.81),(1.75,1.81,2.09),(1.5,1.65,2.33),
                (2.25,2.35,1.64),(2.5,2.58,1.53),(1.25,1.48,2.72)],
    },
    ("Cape Verde", "Saudi Arabia", "2026-06-27"): {
        "1x2": (2.65, 3.40, 2.62),
        "ou":  [(2.25,1.94,1.96),(2.5,2.23,1.71),(2.0,1.65,2.33),(2.75,2.56,1.54)],
        "ah":  [(0.0,1.98,1.94),(-0.25,1.69,2.29),(0.25,2.33,1.67),
                (-0.5,1.53,2.63),(0.5,2.66,1.52),(-0.75,1.37,3.22)],
    },
    ("Uruguay", "Spain", "2026-06-27"): {
        "1x2": (5.40, 3.90, 1.63),
        "ou":  [(2.25,1.92,1.98),(2.5,2.20,1.73),(2.0,1.63,2.36),(2.75,2.53,1.55)],
        "ah":  [(-0.75,2.07,1.85),(-1.0,1.76,2.19),(-0.5,2.36,1.65),
                (-1.25,1.56,2.56),(-0.25,2.88,1.45),(-1.5,1.45,2.88)],
    },
    ("Egypt", "Iran", "2026-06-27"): {
        "1x2": (2.53, 2.57, 3.75),
        "ou":  [(2.0,2.12,1.79),(1.75,1.79,2.12),(1.5,1.60,2.42),(2.25,2.47,1.58)],
        "ah":  [(0.25,2.06,1.86),(0.0,1.62,2.42),(0.5,2.44,1.61),
                (-0.25,1.43,2.96),(0.75,2.92,1.44)],
    },
    ("New Zealand", "Belgium", "2026-06-27"): {
        "1x2": (13.00, 7.90, 1.17),
        "ou":  [(3.75,1.98,1.90),(3.5,1.79,2.09),(4.0,2.23,1.69),(3.25,1.62,2.35)],
        "ah":  [(-2.25,1.90,2.00),(-2.0,2.16,1.76),(-2.5,1.72,2.21),
                (-1.75,2.40,1.61),(-2.75,1.57,2.49),(-1.5,2.63,1.51)],
    },
    # ── 06-28 赔率文件 = 06-28 results ────────────────────────────────────
    ("England", "Panama", "2026-06-28"): {
        "1x2": (1.17, 7.50, 15.00),
        "ou":  [(3.5,2.20,1.68)],
        "ah":  [(2.5,2.35,1.57)],
    },
    ("Croatia", "Ghana", "2026-06-28"): {
        "1x2": (1.71, 3.75, 5.50),
        "ou":  [(2.5,2.35,1.74)],
        "ah":  [(0.5,1.74,2.05)],
    },
    ("Algeria", "Austria", "2026-06-28"): {
        "1x2": (3.65, 3.30, 2.15),
        "ou":  [(2.5,2.15,1.74)],
        "ah":  [],
    },
    ("Jordan", "Argentina", "2026-06-28"): {
        "1x2": (15.00, 7.50, 1.17),
        "ou":  [(2.5,1.61,2.43),(3.5,2.00,1.90)],
        "ah":  [],
    },
    ("Colombia", "Portugal", "2026-06-28"): {
        "1x2": (3.50, 3.80, 1.95),
        "ou":  [(2.5,2.08,1.77)],
        "ah":  [],
    },
    ("Congo DR", "Uzbekistan", "2026-06-28"): {
        "1x2": (2.55, 3.30, 5.00),
        "ou":  [(2.5,2.20,1.63)],
        "ah":  [(0.5,1.69,2.15)],
    },
}


# ── 点对点辅助：inline Elo + AD 更新（不依赖 update_elo.py，避免循环引用） ─────

_K_ELO = 60


def _wf_elo_update(elo: dict, home: str, away: str, hg: int, ag: int) -> dict:
    eh, ea = elo.get(home, 1700.0), elo.get(away, 1700.0)
    Eh = 1 / (1 + 10 ** ((ea - eh) / 400))
    Sh = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
    elo[home] = round(eh + _K_ELO * (Sh - Eh), 1)
    elo[away] = round(ea + _K_ELO * ((1 - Sh) - (1 - Eh)), 1)
    return elo


def _wf_ad_exp(elo_team: float, elo_opp: float) -> float:
    return BASE_GOALS * math.exp((elo_team - elo_opp) / ELO_SCALE)


def _wf_ad_update(ad: dict, team: str, gf: int, ga: int, exp_f: float, exp_a: float):
    if team not in ad:
        ad[team] = {"att_goals_sum": 0.0, "att_exp_sum": 0.0,
                    "def_goals_sum": 0.0, "def_exp_sum": 0.0, "n": 0}
    s = ad[team]
    s["att_goals_sum"] += gf;  s["att_exp_sum"] += exp_f
    s["def_goals_sum"] += ga;  s["def_exp_sum"] += exp_a
    s["n"] += 1


def _build_mat_custom(home: str, away: str, he: float, ae: float, ad_state: dict):
    """Point-in-time matrix using pre-match Elo and AD state (no lookahead)."""
    diff = he - ae
    lh = la = 1.0
    if he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX:
        lh = GSV_LAMBDA_FACTOR
    elif ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX:
        la = GSV_LAMBDA_FACTOR
    elif he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < diff <= GSV_LAMBDA_DIFF_EXTENDED:
        lh = GSV_LAMBDA_FACTOR_EXTENDED
    elif ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < -diff <= GSV_LAMBDA_DIFF_EXTENDED:
        la = GSV_LAMBDA_FACTOR_EXTENDED

    if AD_ENABLED:
        att_h, def_h = compute_ad_factor(ad_state, home)
        att_a, def_a = compute_ad_factor(ad_state, away)
    else:
        att_h = def_h = att_a = def_a = 1.0

    mat = score_matrix(home, away,
                       custom_home_elo=he, custom_away_elo=ae,
                       lam_scale_home=lh, lam_scale_away=la,
                       custom_att_home=att_h, custom_def_home=def_h,
                       custom_att_away=att_a, custom_def_away=def_a)
    raw = matrix_to_probs(mat)
    adj = apply_all(home, away, raw["home_win"], raw["draw"], raw["away_win"],
                    home_elo=he, away_elo=ae)
    probs = {**raw, **adj}
    return mat, probs, diff, (lh != 1.0 or la != 1.0)


def _ah_result(hg: int, ag: int, line: float) -> str:
    """line > 0: home gives goals. Returns home/away/push from home side."""
    margin = hg - ag
    al, frac = abs(line), abs(line) % 1
    if line < 0:
        margin = -margin
    if frac == 0.0:
        if margin > al: return "home"
        if margin == al: return "push"
        return "away"
    elif frac == 0.5:
        return "home" if margin > al else "away"
    else:
        r1 = _ah_result(hg, ag, (al - 0.25) * (1 if line > 0 else -1))
        r2 = _ah_result(hg, ag, (al + 0.25) * (1 if line > 0 else -1))
        return r1 if r1 == r2 else "half"


def _pnl_ah(result: str, stake: float, odds: float) -> float:
    if result == "home":  return stake * (odds - 1)
    if result == "push":  return 0.0
    if result == "half":  return 0.5 * stake * (odds - 1)
    return -stake


def _scan_from_probs(home, away, hg, ag, odds_entry, mat, probs):
    """扫描市场，返回通过 gate 的候选注。接受外部传入的 mat/probs（点对点）。"""
    cands = []

    # ── 1X2 ──────────────────────────────────────────────────────────────
    ho, do, ao = odds_entry["1x2"]
    mh, md, ma = remove_margin([1/ho, 1/do, 1/ao])
    for key, mo, tm, odds, lbl in [
        ("home_win", probs["home_win"], mh, ho, f"{home}胜"),
        ("draw",     probs["draw"],     md, do, "平局"),
        ("away_win", probs["away_win"], ma, ao, f"{away}胜"),
    ]:
        edge = mo - tm
        gap  = abs(mo - tm)
        if gap >= ARTIFACT_KILL or mo < MIN_MODEL_PROB:
            continue
        if key == "draw" and 0.20 <= mo <= 0.30:
            continue
        if key in ("home_win", "away_win") and mo < MIN_WIN_PROB:
            continue
        if edge >= MIN_EDGE and gap < ARTIFACT_GAP:
            won = ((key=="home_win" and hg>ag) or (key=="draw" and hg==ag)
                   or (key=="away_win" and hg<ag))
            pv = STAKE*(odds-1) if won else -STAKE
            cands.append((edge, lbl, odds, mo, tm, gap, "win" if won else "lose", pv, "1X2"))

    # ── O/U ──────────────────────────────────────────────────────────────
    for oline, oo, uo in odds_entry.get("ou", []):
        key  = f"over{str(oline).replace('.', '')}"
        mo_o = probs.get(key, ou_prob(mat, oline))
        mo_u = 1 - mo_o
        to, tu = remove_margin([1/oo, 1/uo])
        actual = hg + ag
        for mo, tm, odds, lbl, is_o in [
            (mo_o, to, oo, f"大{oline}", True),
            (mo_u, tu, uo, f"小{oline}", False),
        ]:
            edge = mo - tm
            gap  = abs(mo - tm)
            if gap >= ARTIFACT_KILL or mo < MIN_MODEL_PROB:
                continue
            if edge >= MIN_EDGE and gap < ARTIFACT_GAP:
                push = (oline % 1 == 0.0 and abs(actual - oline) < 0.01)
                won  = (not push) and ((is_o and actual > oline) or (not is_o and actual < oline))
                out  = "push" if push else ("win" if won else "lose")
                pv   = 0 if push else (STAKE*(odds-1) if won else -STAKE)
                cands.append((edge, lbl, odds, mo, tm, gap, out, pv, "OU"))

    # ── AH (正线) ─────────────────────────────────────────────────────────
    for line, ho_, ao_ in odds_entry.get("ah", []):
        if line <= 0:
            continue
        mo_h = probs.get(f"ah{str(line).replace('.','')}", ah_prob(mat, line))
        mo_a = 1 - mo_h
        th, ta = remove_margin([1/ho_, 1/ao_])

        for mo, tm, odds, side in [(mo_h, th, ho_, "home"), (mo_a, ta, ao_, "away")]:
            edge = mo - tm
            gap  = abs(mo - tm)
            if gap >= ARTIFACT_KILL or mo < MIN_MODEL_PROB:
                continue
            if edge >= MIN_EDGE and gap < ARTIFACT_GAP:
                r = _ah_result(hg, ag, line)
                if side == "away":
                    r = {"home":"away","away":"home","push":"push","half":"half"}.get(r, r)
                pv  = _pnl_ah(r, STAKE, odds)
                out = ("win" if pv > 0 else
                       "push" if pv == 0 else
                       "half" if 0 < abs(pv) < STAKE else "lose")
                lbl = (f"主-{line}({home}让)" if side=="home"
                       else f"客+{line}({away}受让)")
                cands.append((edge, lbl, odds, mo, tm, gap, out, pv, "AH"))

    cands.sort(key=lambda x: -x[0])
    return cands


def run_walkforward(verbose: bool = True, use_ad: bool = None):
    """
    遍历 wc2026_results.json（时序），逐场更新 Elo + AD 状态，
    对有赔率的场次用赛前点对点状态预测并计算收益。

    use_ad: None → 读 config.AD_ENABLED；True/False → 显式覆盖（A/B 对比用）
    """
    _ad_enabled = AD_ENABLED if use_ad is None else use_ad

    with open("data/wc2026_results.json") as f:
        all_results = json.load(f)["matches"]

    # 赔率查询表：(home, away) → odds_entry
    odds_lookup = {(h, a): v for (h, a, _d), v in MATCHES_ODDS.items()}

    elo = dict(TEAM_ELO)  # 从静态起始值出发，无前视
    ad_state = {}         # 攻防状态，逐场建立

    total_bets = total_stake = total_pnl = wins = pushes = losses = 0
    rows = []
    dir_stats: dict[str, dict] = {}  # 分方向命中统计

    def _direction(lbl: str, mtype: str, home: str, away: str) -> str:
        if mtype == "1X2":
            if "平局" in lbl:  return "平局"
            if home  in lbl:  return "主胜"
            if away  in lbl:  return "客胜"
            return "1X2"
        if mtype == "OU":
            return "Over" if "大" in lbl else "Under"
        return mtype  # AH 等原样返回

    if verbose:
        ad_tag = "AD+Elo" if _ad_enabled else "Elo-only"
        print("=" * 70)
        print(f"  Walk-Forward  |  真实赔率 × 真实结果  |  点对点 {ad_tag}")
        print("=" * 70)

    for m in all_results:  # wc2026_results.json 已按日期排序
        home, away = m["home"], m["away"]
        hg, ag = m["hg"], m["ag"]

        he = elo.get(home, 1700.0)
        ae = elo.get(away, 1700.0)

        # 有赔率 → 用赛前状态预测
        if (home, away) in odds_lookup:
            odds_entry = odds_lookup[(home, away)]

            if _ad_enabled:
                mat, probs, diff, gsv = _build_mat_custom(home, away, he, ae, ad_state)
            else:
                mat, probs, diff, gsv = _build_mat_custom(
                    home, away, he, ae, {})  # 空 ad_state → 全因子=1.0

            cands = _scan_from_probs(home, away, hg, ag, odds_entry, mat, probs)

            gsv_tag = " [GSV]" if gsv else ""
            if verbose:
                print(f"\n  {home} vs {away:18}  Elo{diff:+4.0f}{gsv_tag}  实际:{hg}-{ag}")

            if not cands:
                if verbose:
                    print(f"    → NO BET")
                rows.append({"match": f"{home} vs {away}", "bet": "NO BET",
                             "result": f"{hg}-{ag}", "pnl": 0})
            else:
                b = cands[0]
                edge, lbl, odds, mo, tm, gap, outcome, pv, mtype = b
                total_bets  += 1
                total_stake += STAKE
                total_pnl   += pv
                if outcome == "win":    wins   += 1
                elif outcome == "push": pushes += 1
                else:                   losses += 1

                # 分方向统计
                d = _direction(lbl, mtype, home, away)
                ds = dir_stats.setdefault(d, {"n": 0, "w": 0, "p": 0, "l": 0, "pnl": 0.0})
                ds["n"] += 1; ds["pnl"] += pv
                if outcome == "win":    ds["w"] += 1
                elif outcome == "push": ds["p"] += 1
                else:                   ds["l"] += 1

                sym = "✓WIN" if outcome == "win" else ("=PUSH" if outcome == "push" else
                      "½" if "half" in outcome else "✗LOSE")
                if verbose:
                    print(f"    推[{mtype}] {lbl:<22} @{odds:.2f}  "
                          f"mo{mo*100:.0f}%  mkt{tm*100:.0f}%  e{edge*100:+.0f}%  gap{gap*100:.0f}%  "
                          f"{sym} P&L¥{pv:+.0f}")
                rows.append({"match": f"{home} vs {away}", "bet": lbl, "mtype": mtype,
                             "direction": d, "odds": odds,
                             "model": mo, "mkt": tm, "edge": edge, "gap": gap,
                             "result": f"{hg}-{ag}", "outcome": outcome, "pnl": pv})

        # 赛后更新 Elo 和 AD 状态（含当场结果，用于后续场次）
        exp_h = _wf_ad_exp(he, ae)
        exp_a = _wf_ad_exp(ae, he)
        _wf_ad_update(ad_state, home, hg, ag, exp_h, exp_a)
        _wf_ad_update(ad_state, away, ag, hg, exp_a, exp_h)
        elo = _wf_elo_update(elo, home, away, hg, ag)

    roi = total_pnl / total_stake * 100 if total_stake else 0
    if verbose:
        print(f"\n{'=' * 70}")
        print(f"  汇总: {total_bets}注 | ✓{wins} ={pushes} ✗{losses} | "
              f"¥{total_stake}→P&L¥{total_pnl:+.0f}  ROI{roi:+.1f}%")
        ecu_pnl = next((r["pnl"] for r in rows if "Ecuador" in r["match"]), 0)
        if ecu_pnl > 0:
            adj_stake = total_stake - STAKE
            adj_pnl   = total_pnl - ecu_pnl
            print(f"  去Ecuador偶发大赔: P&L¥{adj_pnl:+.0f}  "
                  f"ROI{adj_pnl/adj_stake*100:+.1f}% (n={total_bets-1})")

        # ── 分方向命中率台账 ──────────────────────────────────────────────────
        if dir_stats:
            print(f"\n  {'─'*60}")
            print(f"  {'方向':<8} {'N':>4} {'W':>4} {'P':>4} {'L':>4} "
                  f"{'命中率':>7} {'P&L':>9} {'ROI':>8}")
            print(f"  {'─'*60}")
            _ORDER = ["主胜", "平局", "客胜", "Over", "Under", "AH"]
            for d in _ORDER + [k for k in dir_stats if k not in _ORDER]:
                if d not in dir_stats:
                    continue
                s = dir_stats[d]
                hit = s["w"] / s["n"] * 100
                roi_d = s["pnl"] / (STAKE * s["n"]) * 100
                print(f"  {d:<8} {s['n']:>4} {s['w']:>4} {s['p']:>4} {s['l']:>4} "
                      f"{hit:>6.0f}%  ¥{s['pnl']:>+8.0f}  {roi_d:>+7.1f}%")
            print(f"  {'─'*60}")
            print(f"  注: 命中率=WIN/N（不含PUSH），样本量小时置信区间宽")
    return rows, total_bets, total_pnl, roi


if __name__ == "__main__":
    run_walkforward(verbose=True)
