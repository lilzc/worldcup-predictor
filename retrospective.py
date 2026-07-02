#!/usr/bin/env python3
"""
回顾分析：测试 Elo diff 150-330 区间（巴士战术区）是否存在系统性弱点
- OU大球推单是否系统性亏损
- AH -1.25/-1.5 是否覆盖率不足
"""

import sys
import io
import contextlib

sys.path.insert(0, ".")

from config import (MIN_EDGE,
                    GSV_LAMBDA_FACTOR, GSV_LAMBDA_ELO_MIN,
                    GSV_LAMBDA_DIFF_MIN, GSV_LAMBDA_DIFF_MAX)

ARTIFACT_GAP  = 0.08   # gap >= 8% → LOW (exclude from Edge推单)
ARTIFACT_KILL = 0.20   # gap >= 20% → KILL (discard)
from src.models.poisson import get_elo, score_matrix, matrix_to_probs, get_lambdas, ou_prob, ah_prob
from src.models.adjustments import apply_all
from src.betting.value import analyze_market
from src.betting.kelly import remove_margin, decimal_to_implied

# ── 回顾赛事数据 ─────────────────────────────────────────────────────────────
matches = [
    # --- 06-15 ---
    {"date":"06-15","home":"Belgium","away":"Egypt",
     "oh":1.50,"od":4.15,"oa":6.60,
     "ou_odds":{2.0:(1.70,2.25),2.25:(1.97,1.93),2.5:(1.97,1.93),2.75:(2.23,1.71),3.0:(2.66,1.50)},
     "ah_odds":{0.5:(1.50,2.72),0.75:(1.64,2.38),1.0:(1.93,1.99),1.25:(2.28,1.70),1.5:(2.58,1.55)},
     "ft_h":1,"ft_a":1,"ht_h":0,"ht_a":1},
    # --- 06-17 ---
    {"date":"06-17","home":"Portugal","away":"Congo DR",
     "oh":1.27,"od":5.60,"oa":11.00,
     "ou_odds":{2.0:(1.60,2.42),2.25:(2.06,1.84),2.5:(1.81,2.09),2.75:(2.06,1.84),3.0:(2.40,1.61),3.25:(2.69,1.49)},
     "ah_odds":{1.0:(1.43,2.96),1.25:(1.65,2.36),1.5:(1.88,2.04),1.75:(2.13,1.80),2.0:(2.56,1.56),2.25:(2.88,1.45)},
     "ft_h":1,"ft_a":1,"ht_h":1,"ht_a":1},
    {"date":"06-17","home":"England","away":"Croatia",
     "oh":1.73,"od":3.60,"oa":5.00,
     "ou_odds":{1.75:(1.47,2.75),2.0:(1.61,2.40),2.25:(1.90,2.00),2.5:(2.19,1.74),2.75:(2.53,1.55)},
     "ah_odds":{0.0:(1.51,2.69),0.25:(1.51,2.69),0.5:(1.73,2.23),0.75:(1.96,1.96),1.0:(2.35,1.66),1.25:(2.72,1.50),1.5:(3.04,1.41)},
     "ft_h":4,"ft_a":2,"ht_h":2,"ht_a":2},
    {"date":"06-17","home":"Ghana","away":"Panama",
     "oh":2.31,"od":3.35,"oa":3.10,
     "ou_odds":{1.75:(1.57,2.49),2.0:(1.78,2.13),2.25:(2.11,1.80),2.5:(2.40,1.61)},
     "ah_odds":{0.0:(1.69,2.29),0.25:(2.07,1.85),0.5:(2.35,1.66),0.75:(2.81,1.47)},
     "ft_h":1,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-17","home":"Uzbekistan","away":"Colombia",
     "oh":8.40,"od":4.80,"oa":1.38,
     "ou_odds":{2.0:(1.72,2.21),2.25:(1.98,1.92),2.5:(1.98,1.92),2.75:(2.23,1.71),3.0:(2.66,1.50)},
     "ah_odds":{},
     "ft_h":1,"ft_a":3,"ht_h":0,"ht_a":1},
    # --- 06-18 ---
    {"date":"06-18","home":"Switzerland","away":"Bosnia",
     "oh":1.54,"od":4.20,"oa":6.00,
     "ou_odds":{2.0:(1.48,2.72),2.25:(2.01,1.89),2.5:(2.01,1.89),2.75:(2.28,1.68)},
     "ah_odds":{0.5:(1.54,2.61),0.75:(1.67,2.33),1.0:(1.95,1.97),1.25:(2.28,1.70),1.5:(2.58,1.55)},
     "ft_h":4,"ft_a":1,"ht_h":0,"ht_a":0},
    {"date":"06-18","home":"Canada","away":"Qatar",
     "oh":1.28,"od":5.60,"oa":10.00,
     "ou_odds":{2.5:(1.74,2.19),2.75:(1.94,1.96),3.0:(2.25,1.70),3.25:(2.53,1.55)},
     "ah_odds":{1.0:(1.44,2.92),1.25:(1.66,2.35),1.5:(1.89,2.03),1.75:(2.13,1.80),2.0:(2.56,1.56),2.25:(2.85,1.46)},
     "ft_h":6,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-18","home":"Mexico","away":"South Korea",
     "oh":2.11,"od":3.30,"oa":3.60,
     "ou_odds":{1.75:(1.53,2.58),2.0:(1.71,2.23),2.25:(2.02,1.88),2.5:(2.31,1.66)},
     "ah_odds":{0.0:(1.52,2.66),0.25:(1.40,3.08),0.5:(2.47,1.60)},
     "ft_h":1,"ft_a":0,"ht_h":0,"ht_a":0},
    # --- 06-19 ---
    {"date":"06-19","home":"USA","away":"Australia",
     "oh":1.63,"od":4.00,"oa":5.20,
     "ou_odds":{2.0:(1.70,2.25),2.25:(1.95,1.95),2.5:(1.95,1.95),2.75:(2.17,1.75),3.0:(2.56,1.54)},
     "ah_odds":{0.25:(1.45,2.88),0.5:(1.64,2.38),0.75:(1.83,2.09),1.0:(2.13,1.80),1.25:(2.47,1.60),1.5:(2.75,1.49)},
     "ft_h":2,"ft_a":0,"ht_h":2,"ht_a":0},
    {"date":"06-19","home":"Scotland","away":"Morocco",
     "oh":5.40,"od":3.45,"oa":1.72,
     "ou_odds":{1.75:(1.54,2.56),2.0:(1.72,2.21),2.25:(2.02,1.88),2.5:(2.31,1.66)},
     "ah_odds":{},
     "ft_h":0,"ft_a":1,"ht_h":0,"ht_a":1},
    {"date":"06-19","home":"Brazil","away":"Haiti",
     "oh":1.09,"od":10.50,"oa":23.00,
     "ou_odds":{3.25:(1.64,2.31),3.5:(1.83,2.05),3.75:(2.04,1.84),4.0:(2.31,1.64)},
     "ah_odds":{2.5:(1.83,2.07),2.75:(2.04,1.86),3.0:(2.33,1.65),3.25:(2.56,1.54)},
     "ft_h":3,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-19","home":"Turkey","away":"Paraguay",
     "oh":2.08,"od":3.45,"oa":3.50,
     "ou_odds":{2.0:(1.55,2.53),2.25:(1.83,2.07),2.5:(2.09,1.81),2.75:(2.40,1.61)},
     "ah_odds":{0.0:(1.53,2.63),0.25:(1.41,3.04),0.5:(2.42,1.62),0.75:(3.04,1.41)},
     "ft_h":0,"ft_a":1,"ht_h":0,"ht_a":1},
    # --- 06-20 ---
    {"date":"06-20","home":"Netherlands","away":"Sweden",
     "oh":1.74,"od":4.00,"oa":4.40,
     "ou_odds":{2.5:(1.67,2.29),2.75:(1.84,2.06),3.0:(2.12,1.79),3.25:(2.40,1.61)},
     "ah_odds":{0.25:(1.53,2.63),0.5:(1.72,2.25),0.75:(1.92,2.00),1.0:(2.25,1.72),1.25:(2.56,1.56),1.5:(2.85,1.46)},
     "ft_h":5,"ft_a":1,"ht_h":2,"ht_a":0},
    {"date":"06-20","home":"Germany","away":"Ivory Coast",
     "oh":1.50,"od":4.65,"oa":5.50,
     "ou_odds":{2.5:(1.59,2.44),2.75:(1.74,2.19),3.0:(1.96,1.94),3.25:(2.23,1.71)},
     "ah_odds":{0.5:(1.53,2.63),0.75:(1.66,2.35),1.0:(1.87,2.05),1.25:(2.14,1.79),1.5:(2.42,1.62)},
     "ft_h":2,"ft_a":1,"ht_h":0,"ht_a":1},
    {"date":"06-20","home":"Ecuador","away":"Curacao",
     "oh":1.13,"od":8.30,"oa":20.00,
     "ou_odds":{2.5:(1.58,2.47),2.75:(1.72,2.21),3.0:(1.94,1.96),3.25:(2.21,1.72)},
     "ah_odds":{1.5:(1.47,2.81),1.75:(1.57,2.53),2.0:(1.73,2.23),2.25:(2.01,1.91),2.5:(2.25,1.72),2.75:(2.58,1.55)},
     "ft_h":0,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-20","home":"Tunisia","away":"Japan",
     "oh":5.80,"od":4.00,"oa":1.58,
     "ou_odds":{2.0:(1.59,2.44),2.25:(1.87,2.03),2.5:(2.14,1.77),2.75:(2.47,1.58)},
     "ah_odds":{},
     "ft_h":0,"ft_a":4,"ht_h":0,"ht_a":2},
    # --- 06-21 ---
    {"date":"06-21","home":"Spain","away":"Saudi Arabia",
     "oh":1.08,"od":10.50,"oa":26.00,
     "ou_odds":{3.0:(1.56,2.47),3.25:(1.74,2.16),3.5:(1.99,1.89),3.75:(2.21,1.70),4.0:(2.53,1.55)},
     "ah_odds":{2.0:(1.45,2.81),2.25:(1.62,2.38),2.5:(1.82,2.08),2.75:(2.05,1.85),3.0:(2.35,1.64),3.25:(2.61,1.52)},
     "ft_h":4,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-21","home":"Belgium","away":"Iran",
     "oh":1.44,"od":4.65,"oa":7.00,
     "ou_odds":{2.0:(1.62,2.38),2.25:(1.84,2.06),2.5:(1.84,2.06),2.75:(2.08,1.82),3.0:(2.44,1.59)},
     "ah_odds":{0.5:(1.45,2.88),0.75:(1.55,2.58),1.0:(1.73,2.23),1.25:(2.01,1.91),1.5:(2.29,1.69),1.75:(2.69,1.51)},
     "ft_h":0,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-21","home":"Uruguay","away":"Cape Verde",
     "oh":1.44,"od":4.20,"oa":7.80,
     "ou_odds":{1.75:(1.55,2.53),2.0:(1.73,2.20),2.25:(2.05,1.85),2.5:(2.35,1.64)},
     "ah_odds":{0.5:(1.47,2.81),0.75:(1.59,2.49),1.0:(1.82,2.11),1.25:(2.16,1.78),1.5:(2.47,1.60),1.75:(2.92,1.44)},
     "ft_h":2,"ft_a":2,"ht_h":2,"ht_a":1},
    {"date":"06-21","home":"New Zealand","away":"Egypt",
     "oh":5.60,"od":4.00,"oa":1.59,
     "ou_odds":{2.0:(1.56,2.51),2.25:(1.84,2.06),2.5:(2.11,1.80),2.75:(2.42,1.60)},
     "ah_odds":{},
     "ft_h":1,"ft_a":3,"ht_h":1,"ht_a":0},
    # --- 06-22 ---
    {"date":"06-22","home":"Argentina","away":"Austria",
     "oh":1.44,"od":4.45,"oa":6.60,
     "ou_odds":{2.0:(1.67,2.29),2.25:(1.93,1.97),2.5:(1.93,1.97),2.75:(2.20,1.73),3.0:(2.53,1.55)},
     "ah_odds":{0.5:(1.48,2.78),0.75:(1.59,2.49),1.0:(1.79,2.14),1.25:(2.11,1.82),1.5:(2.42,1.62),1.75:(2.81,1.47)},
     "ft_h":2,"ft_a":0,"ht_h":1,"ht_a":0},
    {"date":"06-22","home":"France","away":"Iraq",
     "oh":1.06,"od":11.50,"oa":27.00,
     "ou_odds":{3.0:(1.64,2.31),3.25:(1.93,1.97),3.5:(1.83,2.05),3.75:(2.05,1.83),4.0:(2.31,1.64)},
     "ah_odds":{2.5:(1.74,2.19),2.75:(1.93,1.97),3.0:(2.20,1.73),3.25:(2.44,1.59)},
     "ft_h":3,"ft_a":0,"ht_h":1,"ht_a":0},
    {"date":"06-22","home":"Norway","away":"Senegal",
     "oh":2.13,"od":3.50,"oa":3.25,
     "ou_odds":{2.25:(1.64,2.35),2.5:(1.87,2.03),2.75:(2.13,1.78)},
     "ah_odds":{0.0:(1.60,2.47),0.25:(1.87,2.05),0.5:(2.14,1.79),0.75:(2.49,1.59)},
     "ft_h":3,"ft_a":2,"ht_h":1,"ht_a":0},
    {"date":"06-22","home":"Jordan","away":"Algeria",
     "oh":6.40,"od":4.00,"oa":1.55,
     "ou_odds":{2.25:(1.70,2.25),2.5:(1.94,1.96),2.75:(2.20,1.73)},
     "ah_odds":{},
     "ft_h":1,"ft_a":2,"ht_h":1,"ht_a":0},
    # --- 06-24 ---
    {"date":"06-24","home":"Portugal","away":"Uzbekistan",
     "oh":1.11,"od":8.80,"oa":20.00,
     "ou_odds":{2.75:(1.60,2.42),3.0:(1.74,2.19),3.5:(2.25,1.70)},
     "ah_odds":{1.75:(1.50,2.72),2.0:(1.66,2.35),2.5:(2.17,1.77),2.75:(2.44,1.61),3.0:(2.92,1.44)},
     "ft_h":5,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-24","home":"England","away":"Ghana",
     "oh":1.18,"od":7.10,"oa":15.00,
     "ou_odds":{2.5:(1.59,2.44),2.75:(1.73,2.20),3.0:(1.97,1.93),3.25:(2.23,1.71)},
     "ah_odds":{1.5:(1.59,2.49),1.75:(1.74,2.21),2.25:(2.25,1.72),2.5:(2.53,1.57),2.75:(2.92,1.44)},
     "ft_h":0,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-24","home":"Panama","away":"Croatia",
     "oh":6.20,"od":4.40,"oa":1.49,
     "ou_odds":{2.25:(1.55,2.53),2.5:(1.75,2.17),2.75:(1.97,1.93),3.0:(2.28,1.68)},
     "ah_odds":{},
     "ft_h":0,"ft_a":1,"ht_h":0,"ht_a":0},
    {"date":"06-24","home":"Colombia","away":"Congo DR",
     "oh":1.54,"od":4.00,"oa":6.30,
     "ou_odds":{2.0:(1.66,2.31),2.25:(1.98,1.92),2.5:(2.25,1.70),2.75:(2.58,1.53)},
     "ah_odds":{0.5:(1.55,2.58),0.75:(1.72,2.25),1.0:(2.01,1.91),1.25:(2.35,1.66),1.5:(2.69,1.51)},
     "ft_h":1,"ft_a":0,"ht_h":0,"ht_a":0},
]

# ── 辅助函数 ─────────────────────────────────────────────────────────────────

def _ah_actual(hg, ag, line):
    diff = hg - ag
    frac = line % 1
    if frac == 0.5:
        return 1.0 if diff > line else 0.0
    elif frac == 0.0:
        if diff > line: return 1.0
        if diff == int(line): return 0.5
        return 0.0
    else:
        return 0.5 * (_ah_actual(hg, ag, line - 0.25) + _ah_actual(hg, ag, line + 0.25))

def _ou_actual(hg, ag, line):
    total = hg + ag
    frac = line % 1
    if frac == 0.5:
        return 1.0 if total > line else 0.0
    elif frac == 0.0:
        if total > line: return 1.0
        if total == int(line): return 0.5
        return 0.0
    else:
        return 0.5 * (_ou_actual(hg, ag, line - 0.25) + _ou_actual(hg, ag, line + 0.25))

def compute_pnl(result, odds, stake=1.0):
    """result: 1=win, 0.5=push, 0=loss. Returns net P&L on stake."""
    if result == 1.0:
        return stake * (odds - 1)
    elif result == 0.5:
        return 0.0
    else:
        return -stake

def fmt_result(r):
    if r == 1.0: return "W"
    if r == 0.5: return "P"
    return "L"


# ── 计算模型概率（suppress stdout） ──────────────────────────────────────────

def run_model(home, away, oh, od, oa, ou_odds, ah_odds):
    """Run predict logic inline (mirrors predict.py but without printing)."""
    live = get_elo()
    he = live.get(home, 1700)
    ae = live.get(away, 1700)
    diff = he - ae

    mat = score_matrix(home, away)
    raw = matrix_to_probs(mat)
    adj = apply_all(home, away, raw["home_win"], raw["draw"], raw["away_win"])
    probs = {**raw, **adj}

    # GSV correction
    lam_h = lam_a = 1.0
    gsv_triggered = False
    if he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX:
        lam_h = GSV_LAMBDA_FACTOR
        gsv_triggered = True
    elif ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX:
        lam_a = GSV_LAMBDA_FACTOR
        gsv_triggered = True
    if gsv_triggered:
        mat_gsv = score_matrix(home, away, lam_scale_home=lam_h, lam_scale_away=lam_a)
        raw_gsv = matrix_to_probs(mat_gsv)
        for k, v in raw_gsv.items():
            if k.startswith("ah") or k.startswith("over") or k.startswith("under"):
                probs[k] = v

    # analyze_market
    value = analyze_market(probs, oh, od, oa, ou_odds=ou_odds, ah_odds=ah_odds if ah_odds else None)

    lam_info = get_lambdas(home, away)
    expected_goals = lam_info["lam"] + lam_info["mu"]
    if gsv_triggered:
        expected_goals_gsv = lam_info["lam"] * (lam_h or lam_a) + lam_info["mu"] * (lam_a if lam_h == 1.0 else 1.0)
    else:
        expected_goals_gsv = expected_goals

    return {
        "probs": probs,
        "value": value,
        "he": he, "ae": ae, "diff": diff,
        "gsv": gsv_triggered,
        "lam": lam_info["lam"],
        "mu": lam_info["mu"],
        "expected_goals": expected_goals,
        "expected_goals_gsv": lam_info["lam"] * lam_h + lam_info["mu"] * lam_a,
    }


def gate_bets(home, away, diff, value, ou_odds, ah_odds):
    """
    Apply backtest-style gates:
    - Rule②: Elo diff >300 and no GSV → KILL all
    - LOW: gap ≥ 8% (exclude from Edge推单)
    - ARTIFACT_KILL: gap ≥ 20% → kill
    - Rule④: per match keep only max-edge AH, max-edge OU
    Returns (edge_bets, stable_bet, ou_ref)
    """
    he = value.get("he", 0) if isinstance(value, dict) and "he" in value else 0

    # Check Rule②: Elo diff > 300 and no GSV
    gsv = value.get("gsv", False) if isinstance(value, dict) else False

    v = value["value"]
    probs = value["probs"]

    # Rule②: Elo diff >300, no GSV → kill
    rule2_kill = (abs(diff) > 300 and not gsv)

    # Stable bet: highest model prob 1X2 direction
    p_home = probs.get("home_win", 0)
    p_draw = probs.get("draw", 0)
    p_away = probs.get("away_win", 0)
    stable_dir = max([("home_win", p_home, 1), ("draw", p_draw, 2), ("away_win", p_away, 3)], key=lambda x: x[1])

    # OU参考: highest model prob OU ≥ 0.48 across all lines
    ou_ref = None
    if ou_odds and "ou_lines" in v:
        best_ou_prob = 0
        best_ou_side = None
        best_ou_line = None
        best_ou_odds = None
        for line, sides in v["ou_lines"].items():
            op = sides["over"]["model"]
            up = sides["under"]["model"]
            if op >= 0.48 and op > best_ou_prob:
                best_ou_prob = op
                best_ou_side = "over"
                best_ou_line = line
                best_ou_odds = ou_odds[line][0]
            if up >= 0.48 and up > best_ou_prob:
                best_ou_prob = up
                best_ou_side = "under"
                best_ou_line = line
                best_ou_odds = ou_odds[line][1]
        if best_ou_side:
            ou_ref = {"side": best_ou_side, "line": best_ou_line, "odds": best_ou_odds, "model_prob": best_ou_prob}

    if rule2_kill:
        return [], stable_dir, ou_ref, True

    # Collect candidate edge bets
    candidates = []

    # 1X2 edges
    for key, label in [("home_win", "1X2-Home"), ("draw", "1X2-Draw"), ("away_win", "1X2-Away")]:
        if key in v and v[key]["has_value"]:
            edge = v[key]["edge"]
            market_true = v[key]["market_true"]
            gap = abs(edge)  # For 1X2 gap is harder to define, use edge
            # For 1X2, get market odds
            if key == "home_win":
                odds_val = value.get("oh_stored")
            elif key == "draw":
                odds_val = value.get("od_stored")
            else:
                odds_val = value.get("oa_stored")
            candidates.append({
                "type": "1x2", "key": key, "label": label,
                "edge": edge, "model_prob": probs.get(key, 0),
                "market_true": market_true, "odds": odds_val,
                "gap": 0  # 1X2 no artifact gap check
            })

    # OU edges
    best_ou_edge_bet = None
    if ou_odds and "ou_lines" in v:
        for line, sides in v["ou_lines"].items():
            for side, side_key in [("over", 0), ("under", 1)]:
                sd = sides[side]
                if sd["has_value"]:
                    edge = sd["edge"]
                    model_p = sd["model"]
                    market_true = sd["market_true"]
                    gap = abs(model_p - market_true)
                    odds_val = ou_odds[line][side_key]
                    if gap >= ARTIFACT_KILL:
                        continue
                    grade = "LOW" if gap >= ARTIFACT_GAP else "MED"
                    if grade == "LOW":
                        continue  # exclude from Edge推单
                    if best_ou_edge_bet is None or edge > best_ou_edge_bet["edge"]:
                        best_ou_edge_bet = {
                            "type": "ou", "key": f"{side}{line}", "label": f"{side.capitalize()} {line}",
                            "edge": edge, "model_prob": model_p, "market_true": market_true,
                            "odds": odds_val, "line": line, "side": side, "gap": gap, "grade": grade
                        }

    # AH edges
    best_ah_edge_bet = None
    if ah_odds and "ah_lines" in v:
        for line, sides in v["ah_lines"].items():
            for side, side_key in [("home", 0), ("away", 1)]:
                sd = sides[side]
                if sd["has_value"]:
                    edge = sd["edge"]
                    model_p = sd["model"]
                    market_true = sd["market_true"]
                    gap = abs(model_p - market_true)
                    odds_val = ah_odds[line][side_key]
                    if gap >= ARTIFACT_KILL:
                        continue
                    grade = "LOW" if gap >= ARTIFACT_GAP else "MED"
                    if grade == "LOW":
                        continue
                    if best_ah_edge_bet is None or edge > best_ah_edge_bet["edge"]:
                        best_ah_edge_bet = {
                            "type": "ah", "key": f"ah{side}{line}", "label": f"AH {side} {line}",
                            "edge": edge, "model_prob": model_p, "market_true": market_true,
                            "odds": odds_val, "line": line, "side": side, "gap": gap, "grade": grade
                        }

    edge_bets = candidates  # 1X2
    if best_ou_edge_bet:
        edge_bets.append(best_ou_edge_bet)
    if best_ah_edge_bet:
        edge_bets.append(best_ah_edge_bet)

    return edge_bets, stable_dir, ou_ref, False


# ── 主循环 ───────────────────────────────────────────────────────────────────

print("Computing model probabilities for all 28 matches...\n")

records = []
for m in matches:
    home, away = m["home"], m["away"]
    hg, ag = m["ft_h"], m["ft_a"]
    total_goals = hg + ag

    # Suppress stdout during model run
    with contextlib.redirect_stdout(io.StringIO()):
        result = run_model(home, away, m["oh"], m["od"], m["oa"], m["ou_odds"], m["ah_odds"])

    result["oh_stored"] = m["oh"]
    result["od_stored"] = m["od"]
    result["oa_stored"] = m["oa"]

    diff = result["diff"]
    gsv = result["gsv"]
    abs_diff = abs(diff)
    bus_zone = 150 <= abs_diff <= 330

    # Actual 1X2 outcome
    if hg > ag: actual_1x2 = "home_win"
    elif hg == ag: actual_1x2 = "draw"
    else: actual_1x2 = "away_win"

    # Gate bets
    edge_bets, stable_dir, ou_ref, killed = gate_bets(home, away, diff, result, m["ou_odds"], m["ah_odds"])

    # Stable P&L: bet on highest-model-prob 1X2 direction
    stable_key, stable_prob, _ = stable_dir
    if stable_key == "home_win":
        stable_odds = m["oh"]
    elif stable_key == "draw":
        stable_odds = m["od"]
    else:
        stable_odds = m["oa"]
    stable_win = 1.0 if actual_1x2 == stable_key else 0.0
    stable_pnl = compute_pnl(stable_win, stable_odds)

    # OU参考 P&L
    ou_ref_pnl = None
    ou_ref_result = None
    if ou_ref:
        ou_result = _ou_actual(hg, ag, ou_ref["line"])
        if ou_ref["side"] == "under":
            ou_result = 1.0 - ou_result if ou_result != 0.5 else 0.5
        ou_ref_pnl = compute_pnl(ou_result, ou_ref["odds"])
        ou_ref_result = ou_result

    # Edge bet P&L
    edge_pnl_total = 0
    edge_bets_resolved = []
    for bet in edge_bets:
        if bet["type"] == "ou":
            res = _ou_actual(hg, ag, bet["line"])
            if bet["side"] == "under":
                res = 1.0 - res if res != 0.5 else 0.5
            pnl = compute_pnl(res, bet["odds"])
        elif bet["type"] == "ah":
            if bet["side"] == "home":
                res = _ah_actual(hg, ag, bet["line"])
            else:
                res_home = _ah_actual(hg, ag, bet["line"])
                res = 1.0 - res_home if res_home != 0.5 else 0.5
            pnl = compute_pnl(res, bet["odds"])
        else:
            # 1X2
            key = bet["key"]
            res = 1.0 if actual_1x2 == key else 0.0
            pnl = compute_pnl(res, bet["odds"])
        edge_pnl_total += pnl
        bet["result"] = res
        bet["pnl"] = pnl
        edge_bets_resolved.append(bet)

    records.append({
        "date": m["date"],
        "home": home, "away": away,
        "hg": hg, "ag": ag,
        "total_goals": total_goals,
        "diff": diff,
        "abs_diff": abs_diff,
        "gsv": gsv,
        "bus_zone": bus_zone,
        "killed": killed,
        "actual_1x2": actual_1x2,
        "expected_goals": result["expected_goals"],
        "expected_goals_gsv": result["expected_goals_gsv"],
        "lam": result["lam"],
        "mu": result["mu"],
        "edge_bets": edge_bets_resolved,
        "stable_key": stable_key,
        "stable_prob": stable_prob,
        "stable_odds": stable_odds,
        "stable_win": stable_win,
        "stable_pnl": stable_pnl,
        "ou_ref": ou_ref,
        "ou_ref_pnl": ou_ref_pnl,
        "ou_ref_result": ou_ref_result,
        "edge_pnl_total": edge_pnl_total,
    })


# ═══════════════════════════════════════════════════════════════════════════════
# OUTPUT
# ═══════════════════════════════════════════════════════════════════════════════

print("=" * 90)
print("  WC 2026 回顾分析 — 全28场（06-15 至 06-24）")
print("=" * 90)

# ── 全场总表 ──────────────────────────────────────────────────────────────────
print("\n" + "─" * 90)
print(f"  {'日期':<7} {'比赛':<32} {'Elo差':>7} {'GSV':>4} {'实际':>5} {'预期':>5} {'总球':>4} {'巴士区':>5} {'结果':<10}")
print("─" * 90)
for r in records:
    match_str = f"{r['home']} vs {r['away']}"
    bus = "YES" if r["bus_zone"] else "-"
    killed = " [KILL]" if r["killed"] else ""
    print(f"  {r['date']:<7} {match_str:<32} {r['diff']:>+7.0f} {'Y' if r['gsv'] else 'N':>4} "
          f"{r['expected_goals_gsv']:>5.2f} {r['expected_goals']:>5.2f} "
          f"{r['total_goals']:>4} {bus:>5}  "
          f"{r['hg']}-{r['ag']}{killed}")

# ── A. OU推单 by Elo-diff zone ────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  A. OU推单分析 by Elo-diff zone (Edge推单OU + OU参考合并)")
print("=" * 90)

zones = [
    ("Normal (0-150)", 0, 150),
    ("Bus-Risk (150-330)", 150, 330),
    ("Blowout (330+)", 330, 9999),
]

for zone_label, lo, hi in zones:
    ou_bets = []
    actual_goals_list = []
    expected_goals_list = []
    for r in records:
        ad = r["abs_diff"]
        if not (lo <= ad < hi):
            continue
        actual_goals_list.append(r["total_goals"])
        expected_goals_list.append(r["expected_goals_gsv"])
        # Edge推单 OU bets
        for bet in r["edge_bets"]:
            if bet["type"] == "ou":
                ou_bets.append({"result": bet["result"], "pnl": bet["pnl"], "odds": bet["odds"],
                                 "label": bet["label"], "match": f"{r['home']} vs {r['away']}", "date": r["date"]})
        # OU参考
        if r["ou_ref"] and r["ou_ref_pnl"] is not None:
            ou_bets.append({"result": r["ou_ref_result"], "pnl": r["ou_ref_pnl"],
                             "odds": r["ou_ref"]["odds"],
                             "label": f"OU参考 {r['ou_ref']['side']} {r['ou_ref']['line']}",
                             "match": f"{r['home']} vs {r['away']}", "date": r["date"]})

    n_matches = sum(1 for r in records if lo <= r["abs_diff"] < hi)
    avg_actual = sum(actual_goals_list) / len(actual_goals_list) if actual_goals_list else 0
    avg_expected = sum(expected_goals_list) / len(expected_goals_list) if expected_goals_list else 0

    print(f"\n  {zone_label} — {n_matches}场  均实际进球: {avg_actual:.2f}  均模型预期: {avg_expected:.2f}  差: {avg_actual-avg_expected:+.2f}")

    if not ou_bets:
        print("  无OU注单")
        continue

    wins = sum(1 for b in ou_bets if b["result"] == 1.0)
    losses = sum(1 for b in ou_bets if b["result"] == 0.0)
    pushes = sum(1 for b in ou_bets if b["result"] == 0.5)
    total_pnl = sum(b["pnl"] for b in ou_bets)
    roi = total_pnl / len(ou_bets) * 100

    print(f"  共{len(ou_bets)}注: {wins}W {losses}L {pushes}P  净P&L: {total_pnl:+.2f}u  ROI: {roi:+.1f}%")
    print(f"  {'日期':<7} {'比赛':<30} {'注单':<20} {'结果':>5} {'P&L':>7}")
    for b in ou_bets:
        print(f"  {b['date']:<7} {b['match']:<30} {b['label']:<20} {fmt_result(b['result']):>5} {b['pnl']:>+7.2f}")


# ── B. AH覆盖分析 ─────────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  B. AH覆盖分析 — Edge推单AH注单 by line")
print("=" * 90)

ah_by_line = {}
for r in records:
    for bet in r["edge_bets"]:
        if bet["type"] == "ah":
            line = bet["line"]
            if line not in ah_by_line:
                ah_by_line[line] = []
            margin = r["hg"] - r["ag"] if bet["side"] == "home" else r["ag"] - r["hg"]
            ah_by_line[line].append({
                "result": bet["result"], "pnl": bet["pnl"], "odds": bet["odds"],
                "margin": r["hg"] - r["ag"],
                "match": f"{r['home']} vs {r['away']}",
                "date": r["date"],
                "side": bet["side"],
                "total_goals": r["total_goals"],
                "bus_zone": r["bus_zone"],
                "diff": r["diff"],
            })

if not ah_by_line:
    print("  无AH Edge推单")
else:
    print(f"\n  {'Line':<8} {'注数':>5} {'W':>4} {'L':>4} {'P':>4} {'ROI':>8} {'非覆盖率':>10}")
    print("  " + "-" * 55)
    for line in sorted(ah_by_line.keys()):
        bets = ah_by_line[line]
        wins = sum(1 for b in bets if b["result"] == 1.0)
        losses = sum(1 for b in bets if b["result"] == 0.0)
        pushes = sum(1 for b in bets if b["result"] == 0.5)
        total_pnl = sum(b["pnl"] for b in bets)
        roi = total_pnl / len(bets) * 100
        # "Won but didn't cover" = home won (margin > 0) but margin <= line (failed AH)
        no_cover = sum(1 for b in bets if b["side"] == "home" and 0 < b["margin"] <= line)
        no_cover_pct = no_cover / len(bets) * 100 if bets else 0
        print(f"  AH-{line:<4} {len(bets):>5} {wins:>4} {losses:>4} {pushes:>4} {roi:>+7.1f}% {no_cover_pct:>9.1f}%")

    print(f"\n  详细列表:")
    print(f"  {'日期':<7} {'比赛':<30} {'边路':<6} {'比分':<7} {'差':<6} {'结果':>5} {'非覆'}")
    print("  " + "-" * 75)
    for line in sorted(ah_by_line.keys()):
        print(f"  --- Line {line} ---")
        for b in ah_by_line[line]:
            no_cov = "YES" if b["side"] == "home" and 0 < b["margin"] <= line else "-"
            bus = "[BUS]" if b["bus_zone"] else ""
            print(f"  {b['date']:<7} {b['match']:<30} {b['side']:<6} {b['margin']:>+6}  diff={b['diff']:>+5.0f} "
                  f"{fmt_result(b['result']):>5}  非覆:{no_cov} {bus}")


# ── C. 巴士区详细分析 ─────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  C. 巴士区 (Elo diff 150-330) 详细分析")
print("=" * 90)

bus_records = [r for r in records if r["bus_zone"]]
print(f"\n  共{len(bus_records)}场在巴士区 (abs Elo diff 150-330)\n")

print(f"  {'日期':<7} {'比赛':<32} {'Elo差':>7} {'GSV':>4} {'预期':>5} {'实际':>5} {'球数':>4} {'低分':>5}")
print("  " + "-" * 75)
for r in bus_records:
    low = "YES" if r["total_goals"] <= 1 else "-"
    print(f"  {r['date']:<7} {r['home']} vs {r['away']:<22} {r['diff']:>+7.0f} "
          f"{'Y' if r['gsv'] else 'N':>4} {r['expected_goals_gsv']:>5.2f} "
          f"{r['total_goals']:>5.0f} {'':>4} {low:>5}")

avg_expected_bus = sum(r["expected_goals_gsv"] for r in bus_records) / len(bus_records) if bus_records else 0
avg_actual_bus = sum(r["total_goals"] for r in bus_records) / len(bus_records) if bus_records else 0
low_scoring = sum(1 for r in bus_records if r["total_goals"] <= 1)
print(f"\n  均模型预期进球: {avg_expected_bus:.2f}")
print(f"  均实际进球:     {avg_actual_bus:.2f}")
print(f"  差:             {avg_actual_bus - avg_expected_bus:+.2f}")
print(f"  低分场（≤1球）: {low_scoring}/{len(bus_records)} ({low_scoring/len(bus_records)*100:.1f}%)")


# ── D. Edge推单 ROI by Elo zone ───────────────────────────────────────────────
print("\n" + "=" * 90)
print("  D. Edge推单 ROI by Elo-diff zone (含1X2 + OU + AH)")
print("=" * 90)

for zone_label, lo, hi in zones:
    all_bets = []
    for r in records:
        if not (lo <= r["abs_diff"] < hi):
            continue
        for bet in r["edge_bets"]:
            all_bets.append(bet)

    n_matches = sum(1 for r in records if lo <= r["abs_diff"] < hi)
    if not all_bets:
        print(f"\n  {zone_label} ({n_matches}场): 无Edge推单")
        continue
    wins = sum(1 for b in all_bets if b["result"] == 1.0)
    losses = sum(1 for b in all_bets if b["result"] == 0.0)
    pushes = sum(1 for b in all_bets if b["result"] == 0.5)
    total_pnl = sum(b["pnl"] for b in all_bets)
    roi = total_pnl / len(all_bets) * 100
    by_type = {}
    for b in all_bets:
        t = b["type"]
        if t not in by_type:
            by_type[t] = []
        by_type[t].append(b)
    type_summary = ", ".join(f"{t}:{len(v)}注" for t, v in by_type.items())
    print(f"\n  {zone_label} ({n_matches}场): {len(all_bets)}注 [{type_summary}]")
    print(f"  {wins}W {losses}L {pushes}P  净P&L: {total_pnl:+.2f}u  ROI: {roi:+.1f}%")


# ── E. 主要发现 ───────────────────────────────────────────────────────────────
print("\n" + "=" * 90)
print("  E. 主要发现")
print("=" * 90)

# Compute stats for summary
normal = [r for r in records if r["abs_diff"] < 150]
bus = [r for r in records if 150 <= r["abs_diff"] < 330]
blowout = [r for r in records if r["abs_diff"] >= 330]

def zone_goals(zone_list):
    if not zone_list: return 0, 0
    avg_actual = sum(r["total_goals"] for r in zone_list) / len(zone_list)
    avg_expected = sum(r["expected_goals_gsv"] for r in zone_list) / len(zone_list)
    return avg_actual, avg_expected

na, ne = zone_goals(normal)
ba, be = zone_goals(bus)
oa, oe = zone_goals(blowout)

print(f"\n  进球数对比（均值）:")
print(f"  {'Zone':<22} {'场数':>5} {'模型预期':>10} {'实际':>8} {'差':>8}")
print("  " + "-" * 58)
print(f"  {'Normal (0-150)':<22} {len(normal):>5} {ne:>10.2f} {na:>8.2f} {na-ne:>+8.2f}")
print(f"  {'Bus-Risk (150-330)':<22} {len(bus):>5} {be:>10.2f} {ba:>8.2f} {ba-be:>+8.2f}")
print(f"  {'Blowout (330+)':<22} {len(blowout):>5} {oe:>10.2f} {oa:>8.2f} {oa-oe:>+8.2f}")

# Bus-zone low scoring
bus_low = sum(1 for r in bus if r["total_goals"] <= 1)
bus_under2 = sum(1 for r in bus if r["total_goals"] <= 2)
blowout_low = sum(1 for r in blowout if r["total_goals"] <= 1)

print(f"\n  巴士区低分率: {bus_low}/{len(bus)} ({bus_low/len(bus)*100:.1f}%) 场 ≤1球")
print(f"  巴士区Under2.5率: {bus_under2}/{len(bus)} ({bus_under2/len(bus)*100:.1f}%) 场 ≤2球")
if blowout:
    print(f"  大差距区低分率: {blowout_low}/{len(blowout)} ({blowout_low/len(blowout)*100:.1f}%) 场 ≤1球")

# GSV effectiveness in bus zone
bus_gsv = [r for r in bus if r["gsv"]]
bus_nogsv = [r for r in bus if not r["gsv"]]
if bus_gsv:
    gsv_avg = sum(r["total_goals"] for r in bus_gsv) / len(bus_gsv)
    nogsv_avg = sum(r["total_goals"] for r in bus_nogsv) / len(bus_nogsv) if bus_nogsv else 0
    gsv_exp = sum(r["expected_goals_gsv"] for r in bus_gsv) / len(bus_gsv)
    nogsv_exp = sum(r["expected_goals_gsv"] for r in bus_nogsv) / len(bus_nogsv) if bus_nogsv else 0
    print(f"\n  巴士区 GSV触发 vs 未触发:")
    print(f"  GSV触发({len(bus_gsv)}场): 模型预期{gsv_exp:.2f} 实际{gsv_avg:.2f} 差{gsv_avg-gsv_exp:+.2f}")
    if bus_nogsv:
        print(f"  GSV未触发({len(bus_nogsv)}场): 模型预期{nogsv_exp:.2f} 实际{nogsv_avg:.2f} 差{nogsv_avg-nogsv_exp:+.2f}")

# Specific hypothesis check
print(f"\n  假说验证:")
print(f"  1. 巴士区是否系统性高估进球？")
print(f"     模型预期均值 {be:.2f} vs 实际 {ba:.2f}，差 {ba-be:+.2f}")
if ba < be - 0.3:
    print(f"     → 确认：模型在巴士区高估进球 {be-ba:.2f} 球/场")
elif ba > be + 0.3:
    print(f"     → 否定：模型在巴士区实际低估进球")
else:
    print(f"     → 轻微：差异在±0.3球以内，不显著")

print(f"\n  2. 巴士区是否出现大量低分意外？")
if bus_low / len(bus) > 0.3:
    print(f"     → 确认：{bus_low}/{len(bus)} ({bus_low/len(bus)*100:.1f}%) 场≤1球，高概率低分")
else:
    print(f"     → 部分：{bus_low}/{len(bus)} ({bus_low/len(bus)*100:.1f}%) 场≤1球")

# AH coverage failures in bus zone
ah_bus_bets = []
for r in records:
    if r["bus_zone"]:
        for b in r["edge_bets"]:
            if b["type"] == "ah" and b["side"] == "home":
                ah_bus_bets.append(b)
if ah_bus_bets:
    no_cover_bus = sum(1 for b in ah_bus_bets if 0 < b.get("margin", 99) <= b.get("line", 0))
    # Need margin from the record
    ah_bus_detailed = []
    for r in records:
        if r["bus_zone"]:
            for b in r["edge_bets"]:
                if b["type"] == "ah" and b["side"] == "home":
                    ah_bus_detailed.append({"margin": r["hg"] - r["ag"], "line": b["line"], "result": b["result"]})
    no_cover_bus = sum(1 for b in ah_bus_detailed if 0 < b["margin"] <= b["line"])
    print(f"\n  3. 巴士区AH主队非覆盖（赢球但不够线）:")
    print(f"     {no_cover_bus}/{len(ah_bus_detailed)} 注 AH主队注在巴士区赢球但未覆盖线")
else:
    print(f"\n  3. 巴士区无AH Edge推单注单")

# Overall Edge推单 summary
all_edge = [b for r in records for b in r["edge_bets"]]
if all_edge:
    total_w = sum(1 for b in all_edge if b["result"] == 1.0)
    total_l = sum(1 for b in all_edge if b["result"] == 0.0)
    total_p = sum(1 for b in all_edge if b["result"] == 0.5)
    total_pnl = sum(b["pnl"] for b in all_edge)
    roi_all = total_pnl / len(all_edge) * 100
    print(f"\n  全局Edge推单: {len(all_edge)}注 {total_w}W {total_l}L {total_p}P  净P&L: {total_pnl:+.2f}u  ROI: {roi_all:+.1f}%")

print("\n" + "=" * 90)
print("  分析完成")
print("=" * 90)
