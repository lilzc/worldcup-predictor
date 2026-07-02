#!/usr/bin/env python3
"""
Historical backtest for WC2026 prediction system.
24 matches from 06-15 to 06-22.
Tracks: Edge推单, 稳单, OU参考, HT推单
Stake: ¥100 per bet.
"""

import sys
import contextlib
import io

sys.path.insert(0, ".")

from predict import predict
from config import TEAM_ELO, MIN_EDGE, GSV_LAMBDA_ELO_MIN, GSV_LAMBDA_DIFF_MIN, GSV_LAMBDA_DIFF_MAX

# ─── Calibration constants (from today.py) ───────────────────────────────────
DRAW_MIN_PROB      = 0.35
DRAW_MIN_EDGE      = 0.07
WIN_MIN_PROB       = 0.25
OU_FENCE_LO        = 0.44
OU_FENCE_HI        = 0.57
ARTIFACT_KILL      = 0.20
ARTIFACT_GAP       = 0.08
CS_MIN_EDGE        = 0.08
UNDER_MKTOVER_KILL = 0.52

STAKE = 100.0

# ─── Match data ───────────────────────────────────────────────────────────────
matches = [
    {
        "date": "06-15", "home": "Belgium", "away": "Egypt",
        "odds_home": 1.50, "odds_draw": 4.15, "odds_away": 6.60,
        "ou_odds": {2.0: (1.70,2.25), 2.25: (1.97,1.93), 2.5: (1.97,1.93), 2.75: (2.23,1.71), 3.0: (2.66,1.50)},
        "ah_odds": {0.5: (1.50,2.72), 0.75: (1.64,2.38), 1.0: (1.93,1.99), 1.25: (2.28,1.70), 1.5: (2.58,1.55)},
        "ht_1x2_odds": (2.04, 2.28, 8.00),
        "ft_h": 1, "ft_a": 1, "ht_h": 0, "ht_a": 1,
    },
    {
        "date": "06-17", "home": "Portugal", "away": "Congo DR",
        "odds_home": 1.27, "odds_draw": 5.60, "odds_away": 11.00,
        "ou_odds": {2.0: (1.60,2.42), 2.25: (2.06,1.84), 2.5: (1.81,2.09), 2.75: (2.06,1.84), 3.0: (2.40,1.61), 3.25: (2.69,1.49)},
        "ah_odds": {1.0: (1.43,2.96), 1.25: (1.65,2.36), 1.5: (1.88,2.04), 1.75: (2.13,1.80), 2.0: (2.56,1.56), 2.25: (2.88,1.45)},
        "ht_1x2_odds": (1.73, 2.55, 11.50),
        "ft_h": 1, "ft_a": 1, "ht_h": 1, "ht_a": 1,
    },
    {
        "date": "06-17", "home": "England", "away": "Croatia",
        "odds_home": 1.73, "odds_draw": 3.60, "odds_away": 5.00,
        "ou_odds": {1.75: (1.47,2.75), 2.0: (1.61,2.40), 2.25: (1.90,2.00), 2.5: (2.19,1.74), 2.75: (2.53,1.55)},
        "ah_odds": {0.0: (1.51,2.69), 0.25: (1.51,2.69), 0.5: (1.73,2.23), 0.75: (1.96,1.96), 1.0: (2.35,1.66), 1.25: (2.72,1.50), 1.5: (3.04,1.41)},
        "ht_1x2_odds": (2.36, 2.18, 5.80),
        "ft_h": 4, "ft_a": 2, "ht_h": 2, "ht_a": 2,
    },
    {
        "date": "06-17", "home": "Ghana", "away": "Panama",
        "odds_home": 2.31, "odds_draw": 3.35, "odds_away": 3.10,
        "ou_odds": {1.75: (1.57,2.49), 2.0: (1.78,2.13), 2.25: (2.11,1.80), 2.5: (2.40,1.61)},
        "ah_odds": {-0.5: (3.08,1.40), 0.0: (1.69,2.29), 0.25: (2.07,1.85), 0.5: (2.35,1.66), 0.75: (2.81,1.47)},
        "ht_1x2_odds": (3.10, 2.07, 4.05),
        "ft_h": 1, "ft_a": 0, "ht_h": 0, "ht_a": 0,
    },
    {
        "date": "06-17", "home": "Uzbekistan", "away": "Colombia",
        "odds_home": 8.40, "odds_draw": 4.80, "odds_away": 1.38,
        "ou_odds": {2.0: (1.72,2.21), 2.25: (1.98,1.92), 2.5: (1.98,1.92), 2.75: (2.23,1.71), 3.0: (2.66,1.50)},
        "ah_odds": {-1.5: (2.17,1.77), -1.25: (1.90,2.02), -1.0: (1.62,2.42), -0.75: (1.47,2.81), -0.5: (1.39,3.12)},
        "ht_1x2_odds": (9.20, 2.35, 1.92),
        "ft_h": 1, "ft_a": 3, "ht_h": 0, "ht_a": 1,
    },
    {
        "date": "06-18", "home": "Switzerland", "away": "Bosnia",
        "odds_home": 1.54, "odds_draw": 4.20, "odds_away": 6.00,
        "ou_odds": {2.0: (1.48,2.72), 2.25: (2.01,1.89), 2.5: (2.01,1.89), 2.75: (2.28,1.68)},
        "ah_odds": {0.5: (1.54,2.61), 0.75: (1.67,2.33), 1.0: (1.95,1.97), 1.25: (2.28,1.70), 1.5: (2.58,1.55)},
        "ht_1x2_odds": (2.08, 2.31, 7.20),
        "ft_h": 4, "ft_a": 1, "ht_h": 0, "ht_a": 0,
    },
    {
        "date": "06-18", "home": "Canada", "away": "Qatar",
        "odds_home": 1.28, "odds_draw": 5.60, "odds_away": 10.00,
        "ou_odds": {2.5: (1.74,2.19), 2.75: (1.94,1.96), 3.0: (2.25,1.70), 3.25: (2.53,1.55)},
        "ah_odds": {1.0: (1.44,2.92), 1.25: (1.66,2.35), 1.5: (1.89,2.03), 1.75: (2.13,1.80), 2.0: (2.56,1.56), 2.25: (2.85,1.46)},
        "ht_1x2_odds": (1.73, 2.62, 10.50),
        "ft_h": 6, "ft_a": 0, "ht_h": 3, "ht_a": 0,
    },
    {
        "date": "06-18", "home": "Mexico", "away": "South Korea",
        "odds_home": 2.11, "odds_draw": 3.30, "odds_away": 3.60,
        "ou_odds": {1.75: (1.53,2.58), 2.0: (1.71,2.23), 2.25: (2.02,1.88), 2.5: (2.31,1.66)},
        "ah_odds": {-0.5: (2.11,1.82), -0.25: (1.81,2.12), 0.0: (1.52,2.66), 0.25: (1.40,3.08), 0.5: (2.47,1.60)},
        "ht_1x2_odds": (2.72, 2.13, 4.65),
        "ft_h": 1, "ft_a": 0, "ht_h": 0, "ht_a": 0,
    },
    {
        "date": "06-19", "home": "USA", "away": "Australia",
        "odds_home": 1.63, "odds_draw": 4.00, "odds_away": 5.20,
        "ou_odds": {2.0: (1.70,2.25), 2.25: (1.95,1.95), 2.5: (1.95,1.95), 2.75: (2.17,1.75), 3.0: (2.56,1.54)},
        "ah_odds": {0.25: (1.45,2.88), 0.5: (1.64,2.38), 0.75: (1.83,2.09), 1.0: (2.13,1.80), 1.25: (2.47,1.60), 1.5: (2.75,1.49)},
        "ht_1x2_odds": (2.20, 2.28, 6.20),
        "ft_h": 2, "ft_a": 0, "ht_h": 2, "ht_a": 0,
    },
    {
        "date": "06-19", "home": "Scotland", "away": "Morocco",
        "odds_home": 5.40, "odds_draw": 3.45, "odds_away": 1.72,
        "ou_odds": {1.75: (1.54,2.56), 2.0: (1.72,2.21), 2.25: (2.02,1.88), 2.5: (2.31,1.66)},
        "ah_odds": {-1.5: (3.04,1.41), -1.25: (2.69,1.51), -1.0: (2.31,1.69), -0.75: (1.95,1.97), -0.5: (1.72,2.25), -0.25: (1.48,2.78)},
        "ht_1x2_odds": (6.50, 2.11, 2.36),
        "ft_h": 0, "ft_a": 1, "ht_h": 0, "ht_a": 1,
    },
    {
        "date": "06-19", "home": "Brazil", "away": "Haiti",
        "odds_home": 1.09, "odds_draw": 10.50, "odds_away": 23.00,
        "ou_odds": {3.25: (1.64,2.31), 3.5: (1.83,2.05), 3.75: (2.04,1.84), 4.0: (2.31,1.64)},
        "ah_odds": {2.5: (1.83,2.07), 2.75: (2.04,1.86), 3.0: (2.33,1.65), 3.25: (2.56,1.54)},
        "ht_1x2_odds": (1.37, 3.85, 16.00),
        "ft_h": 3, "ft_a": 0, "ht_h": 3, "ht_a": 0,
    },
    {
        "date": "06-19", "home": "Turkey", "away": "Paraguay",
        "odds_home": 2.08, "odds_draw": 3.45, "odds_away": 3.50,
        "ou_odds": {2.0: (1.55,2.53), 2.25: (1.83,2.07), 2.5: (2.09,1.81), 2.75: (2.40,1.61)},
        "ah_odds": {-0.5: (2.08,1.84), -0.25: (1.80,2.13), 0.0: (1.53,2.63), 0.25: (1.41,3.04), 0.5: (2.42,1.62), 0.75: (3.04,1.41)},
        "ht_1x2_odds": (2.70, 2.18, 4.45),
        "ft_h": 0, "ft_a": 1, "ht_h": 0, "ht_a": 1,
    },
    {
        "date": "06-20", "home": "Netherlands", "away": "Sweden",
        "odds_home": 1.74, "odds_draw": 4.00, "odds_away": 4.40,
        "ou_odds": {2.5: (1.67,2.29), 2.75: (1.84,2.06), 3.0: (2.12,1.79), 3.25: (2.40,1.61)},
        "ah_odds": {0.25: (1.53,2.63), 0.5: (1.72,2.25), 0.75: (1.92,2.00), 1.0: (2.25,1.72), 1.25: (2.56,1.56), 1.5: (2.85,1.46)},
        "ht_1x2_odds": (2.25, 2.43, 5.00),
        "ft_h": 5, "ft_a": 1, "ht_h": 2, "ht_a": 0,
    },
    {
        "date": "06-20", "home": "Germany", "away": "Ivory Coast",
        "odds_home": 1.50, "odds_draw": 4.65, "odds_away": 5.50,
        "ou_odds": {2.5: (1.59,2.44), 2.75: (1.74,2.19), 3.0: (1.96,1.94), 3.25: (2.23,1.71)},
        "ah_odds": {0.5: (1.53,2.63), 0.75: (1.66,2.35), 1.0: (1.87,2.05), 1.25: (2.14,1.79), 1.5: (2.42,1.62)},
        "ht_1x2_odds": (2.01, 2.51, 6.30),
        "ft_h": 2, "ft_a": 1, "ht_h": 0, "ht_a": 1,
    },
    {
        "date": "06-20", "home": "Ecuador", "away": "Curacao",
        "odds_home": 1.13, "odds_draw": 8.30, "odds_away": 20.00,
        "ou_odds": {2.5: (1.58,2.47), 2.75: (1.72,2.21), 3.0: (1.94,1.96), 3.25: (2.21,1.72)},
        "ah_odds": {1.5: (1.47,2.81), 1.75: (1.57,2.53), 2.0: (1.73,2.23), 2.25: (2.01,1.91), 2.5: (2.25,1.72), 2.75: (2.58,1.55)},
        "ht_1x2_odds": (1.45, 3.25, 17.00),
        "ft_h": 0, "ft_a": 0, "ht_h": 0, "ht_a": 0,
    },
    {
        "date": "06-20", "home": "Tunisia", "away": "Japan",
        "odds_home": 5.80, "odds_draw": 4.00, "odds_away": 1.58,
        "ou_odds": {2.0: (1.59,2.44), 2.25: (1.87,2.03), 2.5: (2.14,1.77), 2.75: (2.47,1.58)},
        "ah_odds": {-1.5: (2.75,1.49), -1.25: (2.42,1.62), -1.0: (2.07,1.85), -0.75: (1.77,2.17), -0.5: (1.59,2.49), -0.25: (1.41,3.04)},
        "ht_1x2_odds": (7.30, 2.23, 2.13),
        "ft_h": 0, "ft_a": 4, "ht_h": 0, "ht_a": 2,
    },
    {
        "date": "06-21", "home": "Spain", "away": "Saudi Arabia",
        "odds_home": 1.08, "odds_draw": 10.50, "odds_away": 26.00,
        "ou_odds": {3.0: (1.56,2.47), 3.25: (1.74,2.16), 3.5: (1.99,1.89), 3.75: (2.21,1.70), 4.0: (2.53,1.55)},
        "ah_odds": {2.0: (1.45,2.81), 2.25: (1.62,2.38), 2.5: (1.82,2.08), 2.75: (2.05,1.85), 3.0: (2.35,1.64), 3.25: (2.61,1.52)},
        "ht_1x2_odds": (1.33, 4.00, 18.50),
        "ft_h": 4, "ft_a": 0, "ht_h": 3, "ht_a": 0,
    },
    {
        "date": "06-21", "home": "Belgium", "away": "Iran",
        "odds_home": 1.44, "odds_draw": 4.65, "odds_away": 7.00,
        "ou_odds": {2.0: (1.62,2.38), 2.25: (1.84,2.06), 2.5: (1.84,2.06), 2.75: (2.08,1.82), 3.0: (2.44,1.59)},
        "ah_odds": {0.5: (1.45,2.88), 0.75: (1.55,2.58), 1.0: (1.73,2.23), 1.25: (2.01,1.91), 1.5: (2.29,1.69), 1.75: (2.69,1.51)},
        "ht_1x2_odds": (1.93, 2.44, 7.90),
        "ft_h": 0, "ft_a": 0, "ht_h": 0, "ht_a": 0,
    },
    {
        "date": "06-21", "home": "Uruguay", "away": "Cape Verde",
        "odds_home": 1.44, "odds_draw": 4.20, "odds_away": 7.80,
        "ou_odds": {1.75: (1.55,2.53), 2.0: (1.73,2.20), 2.25: (2.05,1.85), 2.5: (2.35,1.64)},
        "ah_odds": {0.5: (1.47,2.81), 0.75: (1.59,2.49), 1.0: (1.82,2.11), 1.25: (2.16,1.78), 1.5: (2.47,1.60), 1.75: (2.92,1.44)},
        "ht_1x2_odds": (2.00, 2.20, 10.00),
        "ft_h": 2, "ft_a": 2, "ht_h": 2, "ht_a": 1,
    },
    {
        "date": "06-21", "home": "New Zealand", "away": "Egypt",
        "odds_home": 5.60, "odds_draw": 4.00, "odds_away": 1.59,
        "ou_odds": {2.0: (1.56,2.51), 2.25: (1.84,2.06), 2.5: (2.11,1.80), 2.75: (2.42,1.60)},
        "ah_odds": {-1.5: (2.75,1.49), -1.25: (2.42,1.62), -1.0: (2.08,1.84), -0.75: (1.78,2.16), -0.5: (1.60,2.47), -0.25: (1.42,3.00)},
        "ht_1x2_odds": (6.60, 2.22, 2.21),
        "ft_h": 1, "ft_a": 3, "ht_h": 1, "ht_a": 0,
    },
    {
        "date": "06-22", "home": "Argentina", "away": "Austria",
        "odds_home": 1.44, "odds_draw": 4.45, "odds_away": 6.60,
        "ou_odds": {2.0: (1.67,2.29), 2.25: (1.93,1.97), 2.5: (1.93,1.97), 2.75: (2.20,1.73), 3.0: (2.53,1.55)},
        "ah_odds": {0.5: (1.48,2.78), 0.75: (1.59,2.49), 1.0: (1.79,2.14), 1.25: (2.11,1.82), 1.5: (2.42,1.62), 1.75: (2.81,1.47)},
        "ht_1x2_odds": (1.90, 2.42, 8.30),
        "ft_h": 2, "ft_a": 0, "ht_h": 1, "ht_a": 0,
    },
    {
        "date": "06-22", "home": "France", "away": "Iraq",
        "odds_home": 1.06, "odds_draw": 11.50, "odds_away": 27.00,
        "ou_odds": {3.0: (1.64,2.31), 3.25: (1.93,1.97), 3.5: (1.83,2.05), 3.75: (2.05,1.83), 4.0: (2.31,1.64)},
        "ah_odds": {2.5: (1.74,2.19), 2.75: (1.93,1.97), 3.0: (2.20,1.73), 3.25: (2.44,1.59)},
        "ht_1x2_odds": (1.30, 4.20, 22.00),
        "ft_h": 3, "ft_a": 0, "ht_h": 1, "ht_a": 0,
    },
    {
        "date": "06-22", "home": "Norway", "away": "Senegal",
        "odds_home": 2.13, "odds_draw": 3.50, "odds_away": 3.25,
        "ou_odds": {2.25: (1.64,2.35), 2.5: (1.87,2.03), 2.75: (2.13,1.78)},
        "ah_odds": {-0.25: (1.47,2.81), 0.0: (1.60,2.47), 0.25: (1.87,2.05), 0.5: (2.14,1.79), 0.75: (2.49,1.59)},
        "ht_1x2_odds": (2.78, 2.29, 3.90),
        "ft_h": 3, "ft_a": 2, "ht_h": 1, "ht_a": 0,
    },
    {
        "date": "06-22", "home": "Jordan", "away": "Algeria",
        "odds_home": 6.40, "odds_draw": 4.00, "odds_away": 1.55,
        "ou_odds": {2.25: (1.70,2.25), 2.5: (1.94,1.96), 2.75: (2.20,1.73)},
        "ah_odds": {-1.5: (2.29,1.69), -1.25: (2.08,1.84), -1.0: (1.84,2.08), -0.75: (1.62,2.42), -0.5: (1.41,3.04)},
        "ht_1x2_odds": (6.70, 2.31, 2.11),
        "ft_h": 1, "ft_a": 2, "ht_h": 1, "ht_a": 0,
    },
]


# ─── Helper: Calibration gate (mirrors today.py) ──────────────────────────────
def _market_implied(dec_odds):
    return 1.0 / dec_odds

def _hybrid_rule2_kill(home, away):
    he = TEAM_ELO.get(home, 1700)
    ae = TEAM_ELO.get(away, 1700)
    diff = he - ae
    gsv = ((he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX) or
           (ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX))
    if abs(diff) > 300 and not gsv:
        stronger = home if diff > 0 else away
        return True, f"Elo差{diff:+d}>300且无GSV"
    return False, ""

def calibration_gate(label, model_prob, edge, dec_odds, market_true=None):
    market_p = market_true if market_true is not None else _market_implied(dec_odds)
    gap = abs(model_prob - market_p)

    if "平局" in label:
        if model_prob < DRAW_MIN_PROB:
            return False, "", f"平局模型{model_prob:.0%}<{DRAW_MIN_PROB:.0%}"
        if edge < DRAW_MIN_EDGE:
            return False, "", f"平局需边际≥{DRAW_MIN_EDGE:.0%}"

    if ("胜" in label and "平局" not in label and "受让" not in label and "让" not in label):
        if model_prob < WIN_MIN_PROB:
            return False, "", f"1X2胜注模型{model_prob:.0%}<{WIN_MIN_PROB:.0%}"

    if ("Over" in label or "Under" in label) and "比分" not in label:
        if OU_FENCE_LO <= model_prob <= OU_FENCE_HI:
            return False, "", f"O/U fence zone"

    if "Under" in label and "比分" not in label:
        market_over_implied = 1.0 - market_p
        if market_over_implied >= UNDER_MKTOVER_KILL and model_prob > 0.50:
            return False, "", f"市场大球意图强"

    if "比分" in label and edge < CS_MIN_EDGE:
        return False, "", f"CS边际{edge:.1%}<{CS_MIN_EDGE:.0%}"

    if gap >= ARTIFACT_KILL:
        return False, "", f"差{gap:.0%}≥{ARTIFACT_KILL:.0%}，artifact"

    if edge < MIN_EDGE:
        return False, "", f"边际{edge:.1%}<MIN_EDGE"

    if gap >= ARTIFACT_GAP:
        conf = "LOW"
    elif edge >= 0.08:
        conf = "HIGH"
    else:
        conf = "MED"

    return True, conf, ""


# ─── AH outcome evaluation ────────────────────────────────────────────────────
def _ah_simple(home_goals, away_goals, line):
    """Returns 'home', 'away', 'push'."""
    margin = home_goals - away_goals
    if margin > line:
        return 'home'
    elif margin < line:
        return 'away'
    else:
        return 'push'

def ah_result(home_goals, away_goals, line, direction):
    """
    Returns (pnl_factor, result_str) where pnl_factor:
      1.0 = full win, -1.0 = full loss, 0.5 = half win, -0.5 = half loss, 0.0 = push
    direction: 'home' or 'away' (which side we bet on)
    """
    # Quarter lines = split
    if line % 0.5 == 0.25:
        r1 = _ah_simple(home_goals, away_goals, line - 0.25)
        r2 = _ah_simple(home_goals, away_goals, line + 0.25)
        results = []
        for r in [r1, r2]:
            if r == direction:
                results.append(1.0)
            elif r == 'push':
                results.append(0.0)
            else:
                results.append(-1.0)
        factor = sum(results) / 2.0
        if factor == 1.0:    return factor, "WIN"
        if factor == -1.0:   return factor, "LOSS"
        if factor == 0.5:    return factor, "HALF-WIN"
        if factor == -0.5:   return factor, "HALF-LOSS"
        return 0.0, "PUSH"
    else:
        r = _ah_simple(home_goals, away_goals, line)
        if r == direction:   return 1.0, "WIN"
        if r == 'push':      return 0.0, "PUSH"
        return -1.0, "LOSS"


def ou_result(home_goals, away_goals, line, direction):
    total = home_goals + away_goals
    # Quarter lines = split
    if line % 0.5 == 0.25:
        lines = [line - 0.25, line + 0.25]
        results = []
        for l in lines:
            if total > l:
                results.append('over')
            elif total < l:
                results.append('under')
            else:
                results.append('push')
        factors = []
        for r in results:
            if r == direction:
                factors.append(1.0)
            elif r == 'push':
                factors.append(0.0)
            else:
                factors.append(-1.0)
        factor = sum(factors) / 2.0
    else:
        if total > line:
            r = 'over'
        elif total < line:
            r = 'under'
        else:
            r = 'push'
        if r == direction:
            factor = 1.0
        elif r == 'push':
            factor = 0.0
        else:
            factor = -1.0

    if factor == 1.0:   return factor, "WIN"
    if factor == -1.0:  return factor, "LOSS"
    if factor == 0.5:   return factor, "HALF-WIN"
    if factor == -0.5:  return factor, "HALF-LOSS"
    return 0.0, "PUSH"


def pnl_from_factor(factor, odds, stake=STAKE):
    """P&L given outcome factor (-1, -0.5, 0, 0.5, 1) and decimal odds."""
    if factor == 0.0:
        return 0.0
    if factor > 0:
        return round(factor * stake * (odds - 1), 2)
    else:
        return round(factor * stake, 2)  # factor is negative


def ft_1x2_result(ft_h, ft_a, direction):
    if direction == "home":
        won = ft_h > ft_a
    elif direction == "draw":
        won = ft_h == ft_a
    else:  # away
        won = ft_h < ft_a
    return (1.0, "WIN") if won else (-1.0, "LOSS")


def parse_label_for_direction(label):
    """
    Parse predict()'s portfolio label to extract (bet_type, direction, line).
    Labels like:
      "主场胜 (Belgium)"  -> 1X2, home
      "客场胜 (Egypt)"    -> 1X2, away
      "平局"              -> 1X2, draw
      "Over 2.5"          -> OU, over, 2.5
      "Under 2.75"        -> OU, under, 2.75
      "AH -1.25 Canada"   -> AH, home, 1.25
      "AH +1.25 Qatar"    -> AH, away, 1.25
    """
    if label == "平局":
        return "1X2", "draw", None
    if label.startswith("主场胜"):
        return "1X2", "home", None
    if label.startswith("客场胜"):
        return "1X2", "away", None
    if label.startswith("Over "):
        line = float(label.split()[1])
        return "OU", "over", line
    if label.startswith("Under "):
        line = float(label.split()[1])
        return "OU", "under", line
    if label.startswith("AH -"):
        # "AH -1.25 Canada" -> home gives 1.25
        parts = label.split()
        line = float(parts[1].lstrip("-"))
        return "AH", "home", line
    if label.startswith("AH +"):
        # "AH +1.25 Qatar" -> away gets 1.25
        parts = label.split()
        line = float(parts[1].lstrip("+"))
        return "AH", "away", line
    return "UNKNOWN", None, None


def evaluate_bet(bet_type, direction, line, odds, ft_h, ft_a):
    """Returns (pnl, result_str)."""
    if bet_type == "1X2":
        factor, res = ft_1x2_result(ft_h, ft_a, direction)
        return pnl_from_factor(factor, odds), res
    elif bet_type == "OU":
        factor, res = ou_result(ft_h, ft_a, line, direction)
        return pnl_from_factor(factor, odds), res
    elif bet_type == "AH":
        factor, res = ah_result(ft_h, ft_a, line, direction)
        return pnl_from_factor(factor, odds), res
    return 0.0, "UNKNOWN"


# ─── Run predict() silently ───────────────────────────────────────────────────
def silent_predict(m):
    f = io.StringIO()
    with contextlib.redirect_stdout(f):
        # Ghana/Panama has negative AH lines -> only pass positive ones to predict
        # For home-underdog matches, negative lines mean away gives goals.
        # predict() only accepts positive lines (home gives goals).
        # Pass only positive lines; negative lines are for away team.
        ah = {k: v for k, v in m.get("ah_odds", {}).items() if k >= 0}
        result = predict(
            home_team=m["home"],
            away_team=m["away"],
            odds_home=m["odds_home"],
            odds_draw=m["odds_draw"],
            odds_away=m["odds_away"],
            ou_odds=m.get("ou_odds"),
            ah_odds=ah if ah else None,
            ht_1x2_odds=m.get("ht_1x2_odds"),
        )
    return result


# ─── Process away-gives-goals AH (negative line matches) ──────────────────────
# For matches with negative AH lines (home is underdog), we need to evaluate
# bets where away team gives goals. We'll manually check these.
# The predict() function only handles positive lines, so negative-line AH
# matches won't have AH bets in the portfolio (since we filter them out above).
# We'll note this in the output.

NEG_AH_MATCHES = {
    # home: away team gives |line| goals to home
    # "Scotland vs Morocco": negative lines mean Morocco gives goals to Scotland
    # Evaluate: away wins AH if away_goals - home_goals > |line|
    # For our backtest, we skip negative-line AH (predict() doesn't model them)
}


# ─── Main backtest ────────────────────────────────────────────────────────────
def run_backtest():
    print("=" * 80)
    print("  WC2026 历史回测 — 24场 (06-15 至 06-22)")
    print("  下注金额: ¥100/注 (所有track)")
    print("=" * 80)

    # Results storage
    edge_bets  = []  # {match, label, type, dir, line, odds, conf, grade, model_prob, edge, pnl, result}
    stable_bets = []
    ou_ref_bets = []
    ht_bets    = []
    cs_refs    = []

    # ── Per-match processing ──────────────────────────────────────────────────
    for m in matches:
        home, away = m["home"], m["away"]
        ft_h, ft_a = m["ft_h"], m["ft_a"]
        ht_h, ht_a = m.get("ht_h", 0), m.get("ht_a", 0)

        result = silent_predict(m)
        if not isinstance(result, dict):
            print(f"  WARN: predict() returned None for {home} vs {away}")
            continue

        probs    = result.get("probs", {})
        value    = result.get("value", {})
        portfolio = result.get("portfolio", [])
        ht_probs = result.get("ht_probs", {})
        ht_value = result.get("ht_value", {})

        rule2_kill, rule2_reason = _hybrid_rule2_kill(home, away)

        # ── 1. EDGE推单 (Rule④: 同场只取最高edge的AH线) ─────────────────────
        raw_bets = []
        for b in portfolio:
            if b.get("stake", 0) <= 0:
                continue
            if rule2_kill:
                continue
            ok, conf, kill_reason = calibration_gate(
                b["label"], b["model_prob"], b["edge"], b["decimal_odds"],
                b.get("market_true")
            )
            if not ok:
                continue
            if conf == "LOW":
                continue

            bet_type, direction, line = parse_label_for_direction(b["label"])
            if bet_type == "UNKNOWN":
                continue
            raw_bets.append((bet_type, direction, line, b["decimal_odds"], conf, b["model_prob"], b["edge"], b["label"]))

        # Rule④: 同类型(AH/OU)内只取最高edge一条
        best_by_type = {}  # type -> highest edge bet
        for bt in raw_bets:
            bet_type = bt[0]
            edge_val = bt[6]
            if bet_type in ("AH", "OU"):
                if bet_type not in best_by_type or edge_val > best_by_type[bet_type][6]:
                    best_by_type[bet_type] = bt
            else:  # 1X2: keep all (rare to have multiple 1X2 bets)
                best_by_type[f"1X2_{bt[1]}"] = bt

        for bt in best_by_type.values():
            bet_type, direction, line, odds, conf, model_prob, edge, label = bt
            pnl, res = evaluate_bet(bet_type, direction, line, odds, ft_h, ft_a)
            edge_bets.append({
                "match": f"{home} vs {away}",
                "date": m["date"],
                "label": label,
                "type": bet_type,
                "direction": direction,
                "line": line,
                "odds": odds,
                "conf": conf,
                "model_prob": model_prob,
                "edge": edge,
                "pnl": pnl,
                "result": res,
            })

        # ── 2. 稳单 (highest model prob 1X2 direction) ────────────────────────
        dirs = [
            (probs.get("home_win", 0), "home", home + "胜", m["odds_home"]),
            (probs.get("draw", 0),     "draw", "平局",      m["odds_draw"]),
            (probs.get("away_win", 0), "away", away + "胜", m["odds_away"]),
        ]
        dirs.sort(reverse=True)
        best_mp, best_dir, best_lbl, best_odds = dirs[0]
        factor, res = ft_1x2_result(ft_h, ft_a, best_dir)
        pnl = pnl_from_factor(factor, best_odds)
        stable_bets.append({
            "match": f"{home} vs {away}",
            "date": m["date"],
            "direction": best_dir,
            "label": best_lbl,
            "odds": best_odds,
            "model_prob": best_mp,
            "pnl": pnl,
            "result": res,
            "rule2": rule2_kill,
        })

        # ── 3. OU参考 (highest model prob OU direction >= 0.48) ───────────────
        ou_opts = []
        ou_lines_v = value.get("ou_lines", {})
        ou_cfg = m.get("ou_odds", {})
        for line_v, data in ou_lines_v.items():
            for side, idx in [("over", 0), ("under", 1)]:
                mp_ou = data[side]["model"]
                ed_ou = data[side]["edge"]
                if line_v in ou_cfg:
                    odds_ou = ou_cfg[line_v][idx]
                else:
                    continue
                if mp_ou >= 0.48:
                    ou_opts.append((mp_ou, line_v, side, odds_ou, ed_ou))
        if ou_opts:
            ou_opts.sort(reverse=True)
            mp_ou, line_ou, side_ou, odds_ou, ed_ou = ou_opts[0]
            factor, res = ou_result(ft_h, ft_a, line_ou, side_ou)
            pnl = pnl_from_factor(factor, odds_ou)
            ou_ref_bets.append({
                "match": f"{home} vs {away}",
                "date": m["date"],
                "line": line_ou,
                "side": side_ou,
                "odds": odds_ou,
                "model_prob": mp_ou,
                "edge": ed_ou,
                "pnl": pnl,
                "result": res,
            })

        # ── 4. HT推单 (edge > 0.03 from ht_value, not killed) ────────────────
        if ht_value:
            ht_cfg = m.get("ht_1x2_odds")  # (h_odds, d_odds, a_odds)
            ht_map = [
                ("home_win", "home", ht_cfg[0] if ht_cfg else None),
                ("draw",     "draw", ht_cfg[1] if ht_cfg else None),
                ("away_win", "away", ht_cfg[2] if ht_cfg else None),
            ]
            for ht_key, ht_dir, ht_odds in ht_map:
                if ht_odds is None:
                    continue
                v = ht_value.get(ht_key, {})
                ht_edge = v.get("edge", 0)
                ht_model = v.get("model", 0)
                killed = v.get("killed")
                if killed or ht_edge < 0.03:
                    continue
                # Evaluate HT 1X2
                if ht_dir == "home":
                    ht_won = ht_h > ht_a
                elif ht_dir == "draw":
                    ht_won = ht_h == ht_a
                else:
                    ht_won = ht_h < ht_a
                pnl = pnl_from_factor(1.0 if ht_won else -1.0, ht_odds)
                res = "WIN" if ht_won else "LOSS"
                ht_bets.append({
                    "match": f"{home} vs {away}",
                    "date": m["date"],
                    "direction": ht_dir,
                    "odds": ht_odds,
                    "model_prob": ht_model,
                    "edge": ht_edge,
                    "pnl": pnl,
                    "result": res,
                    "ht_score": f"{ht_h}-{ht_a}",
                })

        # ── 5. CS参考 ─────────────────────────────────────────────────────────
        top_scores = probs.get("top_scores", [])
        actual_cs = f"{ft_h}-{ft_a}"
        cs_top3 = [(hg, ag, p) for hg, ag, p in top_scores[:3]]
        hit = any(f"{hg}-{ag}" == actual_cs for hg, ag, _ in cs_top3)
        cs_refs.append({
            "match": f"{home} vs {away}",
            "date": m["date"],
            "actual": actual_cs,
            "top3": [f"{hg}-{ag}({p*100:.1f}%)" for hg, ag, p in cs_top3],
            "hit": hit,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # OUTPUT
    # ─────────────────────────────────────────────────────────────────────────

    print("\n" + "=" * 80)
    print("  比赛汇总")
    print("=" * 80)
    print(f"  {'日期':<7} {'比赛':<35} {'FT':>6} {'HT':>6}")
    print(f"  {'-'*56}")
    for m in matches:
        print(f"  {m['date']:<7} {m['home']+' vs '+m['away']:<35} "
              f"{m['ft_h']}-{m['ft_a']:>1}     {m['ht_h']}-{m['ht_a']}")

    # ── Edge推单 ──────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  Edge推单 (经校准关卡过滤, LOW grade已排除, Rule②已应用)")
    print("=" * 80)
    if edge_bets:
        print(f"  {'日期':<7} {'比赛':<28} {'标的':<28} {'赔率':>5} {'模型':>6} {'边际':>6} {'置信':>4} {'结果':>9} {'P&L':>8}")
        print(f"  {'-'*104}")
        for b in edge_bets:
            print(f"  {b['date']:<7} {b['match']:<28} {b['label']:<28} "
                  f"{b['odds']:>5.2f} {b['model_prob']*100:>5.1f}% {b['edge']*100:>+5.1f}% "
                  f"{b['conf']:>4} {b['result']:>9} {b['pnl']:>+8.0f}")
    else:
        print("  无Edge推单")

    edge_total_stake = len(edge_bets) * STAKE
    edge_total_pnl = sum(b["pnl"] for b in edge_bets)
    edge_wins = sum(1 for b in edge_bets if b["result"] == "WIN")
    edge_losses = sum(1 for b in edge_bets if b["result"] == "LOSS")
    edge_pushes = sum(1 for b in edge_bets if b["result"] in ("PUSH", "HALF-WIN", "HALF-LOSS"))
    print(f"\n  Edge推单汇总: {len(edge_bets)}注  {edge_wins}W {edge_losses}L {edge_pushes}P/HW/HL")
    if edge_total_stake > 0:
        print(f"  总投入: ¥{edge_total_stake:.0f}  P&L: ¥{edge_total_pnl:+.0f}  "
              f"ROI: {edge_total_pnl/edge_total_stake*100:+.1f}%")

    # ── 稳单 ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  稳单 (每场1X2最高模型概率方向, ¥100/注)")
    print("=" * 80)
    print(f"  {'日期':<7} {'比赛':<35} {'方向':<18} {'赔率':>5} {'模型':>6} {'结果':>6} {'P&L':>8}")
    print(f"  {'-'*88}")
    for b in stable_bets:
        r2tag = "[R2⚠]" if b["rule2"] else ""
        print(f"  {b['date']:<7} {b['match']:<35} {b['label']:<18} "
              f"{b['odds']:>5.2f} {b['model_prob']*100:>5.1f}% {b['result']:>6} {b['pnl']:>+8.0f} {r2tag}")

    stable_total_stake = len(stable_bets) * STAKE
    stable_total_pnl = sum(b["pnl"] for b in stable_bets)
    stable_wins = sum(1 for b in stable_bets if b["result"] == "WIN")
    stable_losses = sum(1 for b in stable_bets if b["result"] == "LOSS")
    print(f"\n  稳单汇总: {len(stable_bets)}注  {stable_wins}W {stable_losses}L")
    print(f"  总投入: ¥{stable_total_stake:.0f}  P&L: ¥{stable_total_pnl:+.0f}  "
          f"ROI: {stable_total_pnl/stable_total_stake*100:+.1f}%")

    # ── OU参考 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  OU参考 (OU-A: 最高model概率≥0.48, 仅参考)")
    print("=" * 80)
    if ou_ref_bets:
        print(f"  {'日期':<7} {'比赛':<35} {'盘口':<16} {'赔率':>5} {'模型':>6} {'边际':>6} {'结果':>9} {'P&L':>8}")
        print(f"  {'-'*96}")
        for b in ou_ref_bets:
            side_lbl = f"{'大' if b['side']=='over' else '小'}{b['line']}"
            print(f"  {b['date']:<7} {b['match']:<35} {side_lbl:<16} "
                  f"{b['odds']:>5.2f} {b['model_prob']*100:>5.1f}% {b['edge']*100:>+5.1f}% "
                  f"{b['result']:>9} {b['pnl']:>+8.0f}")
    else:
        print("  无OU参考信号")

    ou_total_stake = len(ou_ref_bets) * STAKE
    ou_total_pnl = sum(b["pnl"] for b in ou_ref_bets)
    ou_wins = sum(1 for b in ou_ref_bets if b["result"] == "WIN")
    ou_losses = sum(1 for b in ou_ref_bets if b["result"] == "LOSS")
    ou_others = len(ou_ref_bets) - ou_wins - ou_losses
    print(f"\n  OU参考汇总: {len(ou_ref_bets)}注  {ou_wins}W {ou_losses}L {ou_others}P/HW/HL")
    if ou_total_stake > 0:
        print(f"  总投入: ¥{ou_total_stake:.0f}  P&L: ¥{ou_total_pnl:+.0f}  "
              f"ROI: {ou_total_pnl/ou_total_stake*100:+.1f}%")

    # ── HT推单 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  HT推单 (ht_value edge≥0.03且未KILL, 仅HT 1X2)")
    print("=" * 80)
    if ht_bets:
        print(f"  {'日期':<7} {'比赛':<35} {'方向':<10} {'赔率':>5} {'模型':>6} {'边际':>6} {'HT比分':>6} {'结果':>6} {'P&L':>8}")
        print(f"  {'-'*96}")
        for b in ht_bets:
            dir_lbl = {"home": "主场胜", "draw": "平局", "away": "客场胜"}[b["direction"]]
            print(f"  {b['date']:<7} {b['match']:<35} {dir_lbl:<10} "
                  f"{b['odds']:>5.2f} {b['model_prob']*100:>5.1f}% {b['edge']*100:>+5.1f}% "
                  f"{b['ht_score']:>6} {b['result']:>6} {b['pnl']:>+8.0f}")
    else:
        print("  无HT推单信号")

    ht_total_stake = len(ht_bets) * STAKE
    ht_total_pnl = sum(b["pnl"] for b in ht_bets)
    ht_wins = sum(1 for b in ht_bets if b["result"] == "WIN")
    ht_losses = sum(1 for b in ht_bets if b["result"] == "LOSS")
    print(f"\n  HT推单汇总: {len(ht_bets)}注  {ht_wins}W {ht_losses}L")
    if ht_total_stake > 0:
        print(f"  总投入: ¥{ht_total_stake:.0f}  P&L: ¥{ht_total_pnl:+.0f}  "
              f"ROI: {ht_total_pnl/ht_total_stake*100:+.1f}%")

    # ── CS参考 ────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  CS参考 (前3波胆, 仅参考不计盈亏)")
    print("=" * 80)
    print(f"  {'日期':<7} {'比赛':<35} {'实际':>6}  {'前3预测':<42} {'命中':>5}")
    print(f"  {'-'*100}")
    cs_hits = 0
    for c in cs_refs:
        hit_str = "★HIT" if c["hit"] else "-"
        if c["hit"]:
            cs_hits += 1
        top3_str = "  ".join(c["top3"])
        print(f"  {c['date']:<7} {c['match']:<35} {c['actual']:>6}  {top3_str:<42} {hit_str:>5}")
    print(f"\n  CS参考命中: {cs_hits}/{len(cs_refs)} ({cs_hits/len(cs_refs)*100:.1f}%)")

    # ── 总汇 ──────────────────────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print("  回测总汇")
    print("=" * 80)
    print(f"  {'Track':<12} {'注数':>4} {'胜':>4} {'负':>4} {'其他':>5} {'总投入':>8} {'P&L':>8} {'ROI':>8}")
    print(f"  {'-'*56}")

    def track_row(name, bets, wins, losses, stake, pnl):
        others = len(bets) - wins - losses
        roi = pnl / stake * 100 if stake > 0 else 0
        print(f"  {name:<12} {len(bets):>4} {wins:>4} {losses:>4} {others:>5} "
              f"¥{stake:>7.0f} {pnl:>+8.0f} {roi:>+7.1f}%")

    track_row("Edge推单", edge_bets, edge_wins, edge_losses,
              edge_total_stake, edge_total_pnl)
    track_row("稳单", stable_bets, stable_wins, stable_losses,
              stable_total_stake, stable_total_pnl)
    track_row("OU参考", ou_ref_bets, ou_wins, ou_losses,
              ou_total_stake, ou_total_pnl)
    track_row("HT推单", ht_bets, ht_wins, ht_losses,
              ht_total_stake, ht_total_pnl)

    all_bets = edge_bets + stable_bets + ou_ref_bets + ht_bets
    all_wins = edge_wins + stable_wins + ou_wins + ht_wins
    all_losses = edge_losses + stable_losses + ou_losses + ht_losses
    all_stake = edge_total_stake + stable_total_stake + ou_total_stake + ht_total_stake
    all_pnl = edge_total_pnl + stable_total_pnl + ou_total_pnl + ht_total_pnl
    print(f"  {'-'*56}")
    track_row("合计", all_bets, all_wins, all_losses, all_stake, all_pnl)

    print("\n  注: Edge推单已排除LOW grade;  稳单Rule②场次标[R2⚠]仅展示不计特殊处理")
    print("  注: OU参考无稳单过滤(仅model≥0.48);  HT仅1X2市场(无HT OU/AH数据)")
    print("=" * 80)


if __name__ == "__main__":
    run_backtest()
