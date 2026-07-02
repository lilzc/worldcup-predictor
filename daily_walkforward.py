#!/usr/bin/env python3
"""
日历式 Walk-Forward 回测
- Elo 一天一天累积更新（K=60，先于当天推单）
- 双轨输出：Edge推单 + 稳单 + OU参考
- 完整 v3 pipeline：近平 AH 压制 + Rule④ 同向去重
用法：python3 daily_walkforward.py
"""
import sys, io, contextlib, re, os, json

sys.path.insert(0, ".")
os.chdir(os.path.dirname(os.path.abspath(__file__)) or ".")

from predict import predict
from config import MIN_EDGE, TEAM_ELO, NEAR_EQUAL_AH_DIFF, NEAR_EQUAL_1X2_WIN_DIFF, GSV_LAMBDA_DIFF_EXTENDED
import src.models.poisson as pm

ARTIFACT_GAP  = 0.08
ARTIFACT_KILL = 0.20
WIN_MIN_PROB  = 0.25
DRAW_MIN_PROB = 0.35
DRAW_MIN_EDGE = 0.07
OU_FENCE_LO   = 0.44
OU_FENCE_HI   = 0.57
UNDER_MKTOVER_KILL = 0.52


# ── Settlement helpers ────────────────────────────────────────────────────────

def settle_ou_whole(line, total, direction):
    if direction == 'over':
        if total > line:  return 'WIN', 1.0
        elif total == line: return 'PUSH', 0.0
        else: return 'LOSS', -1.0
    else:
        if total < line:  return 'WIN', 1.0
        elif total == line: return 'PUSH', 0.0
        else: return 'LOSS', -1.0


def settle_ou(line, total, direction='over'):
    frac = round(line % 1, 2)
    if frac in (0.0, 0.5):
        return settle_ou_whole(line, total, direction)
    lo, hi = line - 0.25, line + 0.25
    ol, ml = settle_ou_whole(lo, total, direction)
    oh, mh = settle_ou_whole(hi, total, direction)
    avg = (ml + mh) / 2
    if avg >= 0.9:   return 'WIN',       1.0
    if avg >= 0.4:   return 'HALF-WIN',  0.5
    if avg <= -0.9:  return 'LOSS',     -1.0
    if avg <= -0.4:  return 'HALF-LOSS',-0.5
    return 'PUSH', 0.0


def settle_ah_whole(line, margin, direction):
    diff = margin - line if direction == 'home' else line - margin
    if line % 1 == 0.0:
        if diff > 0: return 'WIN', 1.0
        elif diff == 0: return 'PUSH', 0.0
        else: return 'LOSS', -1.0
    return ('WIN', 1.0) if diff > 0 else ('LOSS', -1.0)


def settle_ah(line, hg, ag, direction='home'):
    margin = hg - ag
    frac = round(line % 1, 2)
    if frac in (0.0, 0.5):
        return settle_ah_whole(line, margin, direction)
    lo, hi = line - 0.25, line + 0.25
    ol, ml = settle_ah_whole(lo, margin, direction)
    oh, mh = settle_ah_whole(hi, margin, direction)
    avg = (ml + mh) / 2
    if avg >= 0.9:  return 'WIN',       1.0
    if avg >= 0.4:  return 'HALF-WIN',  0.5
    if avg <= -0.9: return 'LOSS',     -1.0
    if avg <= -0.4: return 'HALF-LOSS',-0.5
    return 'PUSH', 0.0


def pnl(outcome, odds, stake=1.0):
    if outcome == 'WIN':      return stake * (odds - 1)
    if outcome == 'HALF-WIN': return stake * (odds - 1) * 0.5
    if outcome == 'PUSH':     return 0.0
    if outcome == 'HALF-LOSS':return -stake * 0.5
    return -stake


def parse_label(label):
    m = re.match(r'^AH ([+-])(\d+(?:\.\d+)?)\s+(.+)$', label.strip())
    if m:
        sign, val, team = m.group(1), float(m.group(2)), m.group(3)
        return ('AH', 'home' if sign == '-' else 'away', val, team)
    m = re.match(r'^(Over|Under)\s+(\d+(?:\.\d+)?)$', label.strip())
    if m:
        return ('OU', 'over' if m.group(1) == 'Over' else 'under', float(m.group(2)), None)
    if label == '平局':        return ('1X2', 'draw', None, None)
    m = re.match(r'^主场胜\s*\((.+)\)$', label.strip())
    if m: return ('1X2', 'home_win', None, m.group(1))
    m = re.match(r'^客场胜\s*\((.+)\)$', label.strip())
    if m: return ('1X2', 'away_win', None, m.group(1))
    return ('UNKNOWN', None, None, None)


# ── 带赔率数据的比赛列表（与 full_backtest.py 同步） ───────────────────────────

MATCHES = [
    {"date":"06-15","home":"Belgium","away":"Egypt",
     "odds_1x2":(1.50,4.15,6.60),
     "ou_odds":{2.0:(1.70,2.25),2.25:(1.97,1.93),2.5:(1.97,1.93),2.75:(2.23,1.71),3.0:(2.66,1.50)},
     "ah_odds":{0.5:(1.50,2.72),0.75:(1.64,2.38),1.0:(1.93,1.99),1.25:(2.28,1.70),1.5:(2.58,1.55)},
     "ft_h":1,"ft_a":1},
    {"date":"06-17","home":"Portugal","away":"Congo DR",
     "odds_1x2":(1.27,5.60,11.00),
     "ou_odds":{2.0:(1.60,2.42),2.25:(2.06,1.84),2.5:(1.81,2.09),2.75:(2.06,1.84),3.0:(2.40,1.61),3.25:(2.69,1.49)},
     "ah_odds":{1.0:(1.43,2.96),1.25:(1.65,2.36),1.5:(1.88,2.04),1.75:(2.13,1.80),2.0:(2.56,1.56),2.25:(2.88,1.45)},
     "ft_h":1,"ft_a":1},
    {"date":"06-17","home":"England","away":"Croatia",
     "odds_1x2":(1.73,3.60,5.00),
     "ou_odds":{1.75:(1.47,2.75),2.0:(1.61,2.40),2.25:(1.90,2.00),2.5:(2.19,1.74),2.75:(2.53,1.55)},
     "ah_odds":{0.25:(1.51,2.69),0.5:(1.73,2.23),0.75:(1.96,1.96),1.0:(2.35,1.66),1.25:(2.72,1.50),1.5:(3.04,1.41)},
     "ft_h":4,"ft_a":2},
    {"date":"06-17","home":"Ghana","away":"Panama",
     "odds_1x2":(2.31,3.35,3.10),
     "ou_odds":{1.75:(1.57,2.49),2.0:(1.78,2.13),2.25:(2.11,1.80),2.5:(2.40,1.61)},
     "ah_odds":{0.0:(1.69,2.29),0.25:(2.07,1.85),0.5:(2.35,1.66),0.75:(2.81,1.47)},
     "ft_h":1,"ft_a":0},
    {"date":"06-17","home":"Uzbekistan","away":"Colombia",
     "odds_1x2":(8.40,4.80,1.38),
     "ou_odds":{2.0:(1.72,2.21),2.25:(1.98,1.92),2.5:(1.98,1.92),2.75:(2.23,1.71),3.0:(2.66,1.50)},
     "ah_odds":{},
     "ft_h":1,"ft_a":3},
    {"date":"06-18","home":"Switzerland","away":"Bosnia",
     "odds_1x2":(1.54,4.20,6.00),
     "ou_odds":{2.0:(1.48,2.72),2.25:(2.01,1.89),2.5:(2.01,1.89),2.75:(2.28,1.68)},
     "ah_odds":{0.5:(1.54,2.61),0.75:(1.67,2.33),1.0:(1.95,1.97),1.25:(2.28,1.70),1.5:(2.58,1.55)},
     "ft_h":4,"ft_a":1},
    {"date":"06-18","home":"Canada","away":"Qatar",
     "odds_1x2":(1.28,5.60,10.00),
     "ou_odds":{2.5:(1.74,2.19),2.75:(1.94,1.96),3.0:(2.25,1.70),3.25:(2.53,1.55)},
     "ah_odds":{1.0:(1.44,2.92),1.25:(1.66,2.35),1.5:(1.89,2.03),1.75:(2.13,1.80),2.0:(2.56,1.56),2.25:(2.85,1.46)},
     "ft_h":6,"ft_a":0},
    {"date":"06-18","home":"Mexico","away":"South Korea",
     "odds_1x2":(2.11,3.30,3.60),
     "ou_odds":{1.75:(1.53,2.58),2.0:(1.71,2.23),2.25:(2.02,1.88),2.5:(2.31,1.66)},
     "ah_odds":{0.0:(1.52,2.66),0.25:(1.40,3.08),0.5:(2.47,1.60),0.75:(3.04,1.41)},
     "ft_h":1,"ft_a":0},
    {"date":"06-19","home":"USA","away":"Australia",
     "odds_1x2":(1.63,4.00,5.20),
     "ou_odds":{2.0:(1.70,2.25),2.25:(1.95,1.95),2.5:(1.95,1.95),2.75:(2.17,1.75),3.0:(2.56,1.54)},
     "ah_odds":{0.25:(1.45,2.88),0.5:(1.64,2.38),0.75:(1.83,2.09),1.0:(2.13,1.80),1.25:(2.47,1.60),1.5:(2.75,1.49)},
     "ft_h":2,"ft_a":0},
    {"date":"06-19","home":"Scotland","away":"Morocco",
     "odds_1x2":(5.40,3.45,1.72),
     "ou_odds":{1.75:(1.54,2.56),2.0:(1.72,2.21),2.25:(2.02,1.88),2.5:(2.31,1.66)},
     "ah_odds":{},
     "ft_h":0,"ft_a":1},
    {"date":"06-19","home":"Brazil","away":"Haiti",
     "odds_1x2":(1.09,10.50,23.00),
     "ou_odds":{3.25:(1.64,2.31),3.5:(1.83,2.05),3.75:(2.04,1.84),4.0:(2.31,1.64)},
     "ah_odds":{2.5:(1.83,2.07),2.75:(2.04,1.86),3.0:(2.33,1.65),3.25:(2.56,1.54)},
     "ft_h":3,"ft_a":0},
    {"date":"06-19","home":"Turkey","away":"Paraguay",
     "odds_1x2":(2.08,3.45,3.50),
     "ou_odds":{2.0:(1.55,2.53),2.25:(1.83,2.07),2.5:(2.09,1.81),2.75:(2.40,1.61)},
     "ah_odds":{0.0:(1.53,2.63),0.25:(1.41,3.04),0.5:(2.42,1.62),0.75:(3.04,1.41)},
     "ft_h":0,"ft_a":1},
    {"date":"06-20","home":"Netherlands","away":"Sweden",
     "odds_1x2":(1.74,4.00,4.40),
     "ou_odds":{2.5:(1.67,2.29),2.75:(1.84,2.06),3.0:(2.12,1.79),3.25:(2.40,1.61)},
     "ah_odds":{0.25:(1.53,2.63),0.5:(1.72,2.25),0.75:(1.92,2.00),1.0:(2.25,1.72),1.25:(2.56,1.56),1.5:(2.85,1.46)},
     "ft_h":5,"ft_a":1},
    {"date":"06-20","home":"Germany","away":"Ivory Coast",
     "odds_1x2":(1.50,4.65,5.50),
     "ou_odds":{2.5:(1.59,2.44),2.75:(1.74,2.19),3.0:(1.96,1.94),3.25:(2.23,1.71)},
     "ah_odds":{0.5:(1.53,2.63),0.75:(1.66,2.35),1.0:(1.87,2.05),1.25:(2.14,1.79),1.5:(2.42,1.62)},
     "ft_h":2,"ft_a":1},
    {"date":"06-20","home":"Ecuador","away":"Curacao",
     "odds_1x2":(1.13,8.30,20.00),
     "ou_odds":{2.5:(1.58,2.47),2.75:(1.72,2.21),3.0:(1.94,1.96),3.25:(2.21,1.72)},
     "ah_odds":{1.5:(1.47,2.81),1.75:(1.57,2.53),2.0:(1.73,2.23),2.25:(2.01,1.91),2.5:(2.25,1.72),2.75:(2.58,1.55)},
     "ft_h":0,"ft_a":0},
    {"date":"06-20","home":"Tunisia","away":"Japan",
     "odds_1x2":(5.80,4.00,1.58),
     "ou_odds":{2.0:(1.59,2.44),2.25:(1.87,2.03),2.5:(2.14,1.77),2.75:(2.47,1.58)},
     "ah_odds":{},
     "ft_h":0,"ft_a":4},
    {"date":"06-21","home":"Spain","away":"Saudi Arabia",
     "odds_1x2":(1.08,10.50,26.00),
     "ou_odds":{3.0:(1.56,2.47),3.25:(1.74,2.16),3.5:(1.99,1.89),3.75:(2.21,1.70),4.0:(2.53,1.55)},
     "ah_odds":{2.0:(1.45,2.81),2.25:(1.62,2.38),2.5:(1.82,2.08),2.75:(2.05,1.85),3.0:(2.35,1.64),3.25:(2.61,1.52)},
     "ft_h":4,"ft_a":0},
    {"date":"06-21","home":"Belgium","away":"Iran",
     "odds_1x2":(1.44,4.65,7.00),
     "ou_odds":{2.0:(1.62,2.38),2.25:(1.84,2.06),2.5:(1.84,2.06),2.75:(2.08,1.82),3.0:(2.44,1.59)},
     "ah_odds":{0.5:(1.45,2.88),0.75:(1.55,2.58),1.0:(1.73,2.23),1.25:(2.01,1.91),1.5:(2.29,1.69),1.75:(2.69,1.51)},
     "ft_h":0,"ft_a":0},
    {"date":"06-21","home":"Uruguay","away":"Cape Verde",
     "odds_1x2":(1.44,4.20,7.80),
     "ou_odds":{1.75:(1.55,2.53),2.0:(1.73,2.20),2.25:(2.05,1.85),2.5:(2.35,1.64)},
     "ah_odds":{0.5:(1.47,2.81),0.75:(1.59,2.49),1.0:(1.82,2.11),1.25:(2.16,1.78),1.5:(2.47,1.60),1.75:(2.92,1.44)},
     "ft_h":2,"ft_a":2},
    {"date":"06-21","home":"New Zealand","away":"Egypt",
     "odds_1x2":(5.60,4.00,1.59),
     "ou_odds":{2.0:(1.56,2.51),2.25:(1.84,2.06),2.5:(2.11,1.80),2.75:(2.42,1.60)},
     "ah_odds":{},
     "ft_h":1,"ft_a":3},
    {"date":"06-22","home":"Argentina","away":"Austria",
     "odds_1x2":(1.44,4.45,6.60),
     "ou_odds":{2.0:(1.67,2.29),2.25:(1.93,1.97),2.5:(1.93,1.97),2.75:(2.20,1.73),3.0:(2.53,1.55)},
     "ah_odds":{0.5:(1.48,2.78),0.75:(1.59,2.49),1.0:(1.79,2.14),1.25:(2.11,1.82),1.5:(2.42,1.62),1.75:(2.81,1.47)},
     "ft_h":2,"ft_a":0},
    {"date":"06-22","home":"France","away":"Iraq",
     "odds_1x2":(1.06,11.50,27.00),
     "ou_odds":{3.0:(1.64,2.31),3.25:(1.93,1.97),3.5:(1.83,2.05),3.75:(2.05,1.83),4.0:(2.31,1.64)},
     "ah_odds":{2.5:(1.74,2.19),2.75:(1.93,1.97),3.0:(2.20,1.73),3.25:(2.44,1.59)},
     "ft_h":3,"ft_a":0},
    {"date":"06-22","home":"Norway","away":"Senegal",
     "odds_1x2":(2.13,3.50,3.25),
     "ou_odds":{2.25:(1.64,2.35),2.5:(1.87,2.03),2.75:(2.13,1.78)},
     "ah_odds":{0.0:(1.60,2.47),0.25:(1.87,2.05),0.5:(2.14,1.79),0.75:(2.49,1.59)},
     "ft_h":3,"ft_a":2},
    {"date":"06-22","home":"Jordan","away":"Algeria",
     "odds_1x2":(6.40,4.00,1.55),
     "ou_odds":{2.25:(1.70,2.25),2.5:(1.94,1.96),2.75:(2.20,1.73)},
     "ah_odds":{},
     "ft_h":1,"ft_a":2},
    {"date":"06-24","home":"Portugal","away":"Uzbekistan",
     "odds_1x2":(1.11,8.80,20.00),
     "ou_odds":{2.75:(1.60,2.42),3.0:(1.74,2.19),3.5:(2.25,1.70)},
     "ah_odds":{1.75:(1.50,2.72),2.0:(1.66,2.35),2.5:(2.17,1.77),2.75:(2.44,1.61),3.0:(2.92,1.44)},
     "ft_h":5,"ft_a":0},
    {"date":"06-24","home":"England","away":"Ghana",
     "odds_1x2":(1.18,7.10,15.00),
     "ou_odds":{2.5:(1.59,2.44),2.75:(1.73,2.20),3.0:(1.97,1.93),3.25:(2.23,1.71)},
     "ah_odds":{1.5:(1.59,2.49),1.75:(1.74,2.21),2.25:(2.25,1.72),2.5:(2.53,1.57),2.75:(2.92,1.44)},
     "ft_h":0,"ft_a":0},
    {"date":"06-24","home":"Panama","away":"Croatia",
     "odds_1x2":(6.20,4.40,1.49),
     "ou_odds":{2.25:(1.55,2.53),2.5:(1.75,2.17),2.75:(1.97,1.93),3.0:(2.28,1.68)},
     "ah_odds":{},
     "ft_h":0,"ft_a":1},
    {"date":"06-24","home":"Colombia","away":"Congo DR",
     "odds_1x2":(1.54,4.00,6.30),
     "ou_odds":{2.0:(1.66,2.31),2.25:(1.98,1.92),2.5:(2.25,1.70),2.75:(2.58,1.53)},
     "ah_odds":{0.5:(1.55,2.58),0.75:(1.72,2.25),1.0:(2.01,1.91),1.25:(2.35,1.66),1.5:(2.69,1.51)},
     "ft_h":1,"ft_a":0},
    {"date":"06-25","home":"Switzerland","away":"Canada",
     "odds_1x2":(2.31,3.10,3.05),
     "ou_odds":{2.25:(1.85,2.05),2.5:(2.14,1.77),2.75:(2.47,1.58)},
     "ah_odds":{0.0:(1.72,2.25),0.25:(2.07,1.85),0.5:(2.36,1.65)},
     "ft_h":2,"ft_a":1},
    {"date":"06-25","home":"Bosnia","away":"Qatar",
     "odds_1x2":(1.38,5.10,7.30),
     "ou_odds":{2.75:(1.73,2.20),3.0:(1.97,1.93),3.25:(2.25,1.70),2.5:(1.59,2.44)},
     "ah_odds":{0.75:(1.47,2.81),1.0:(1.59,2.49),1.25:(1.82,2.11),1.5:(2.09,1.83),2.0:(2.88,1.45)},
     "ft_h":3,"ft_a":1},
    {"date":"06-25","home":"Scotland","away":"Brazil",
     "odds_1x2":(9.90,5.30,1.30),
     "ou_odds":{2.25:(1.60,2.42),2.5:(1.82,2.08),2.75:(2.06,1.84),3.0:(2.40,1.61)},
     "ah_odds":{},
     "ft_h":0,"ft_a":3},
    {"date":"06-25","home":"Morocco","away":"Haiti",
     "odds_1x2":(1.19,6.90,13.50),
     "ou_odds":{2.75:(1.68,2.28),3.0:(1.89,2.01),3.25:(2.14,1.77),3.5:(2.40,1.61)},
     "ah_odds":{1.25:(1.45,2.88),1.5:(1.59,2.49),1.75:(1.76,2.19),2.0:(2.01,1.91),2.25:(2.31,1.68),2.5:(2.56,1.56)},
     "ft_h":4,"ft_a":2},
    {"date":"06-25","home":"Czechia","away":"Mexico",
     "odds_1x2":(3.80,3.50,1.98),
     "ou_odds":{2.0:(1.51,2.63),2.25:(1.77,2.14),2.5:(2.03,1.87),2.75:(2.31,1.66)},
     "ah_odds":{},
     "ft_h":0,"ft_a":3},
    {"date":"06-25","home":"South Africa","away":"South Korea",
     "odds_1x2":(5.30,3.95,1.63),
     "ou_odds":{2.0:(1.55,2.53),2.25:(1.85,2.05),2.5:(2.09,1.81),2.75:(2.40,1.61)},
     "ah_odds":{},
     "ft_h":1,"ft_a":0},
]


# ── Build walk-forward Elo from ALL 58 wc2026_results.json ────────────────────

def _elo_update(elo, home, away, hg, ag):
    eh, ea = elo.get(home, 1700), elo.get(away, 1700)
    Eh = 1 / (1 + 10 ** ((ea - eh) / 400))
    Sh = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
    elo[home] = round(eh + 60 * (Sh - Eh), 1)
    elo[away] = round(ea + 60 * ((1 - Sh) - (1 - Eh)), 1)


with open("data/wc2026_results.json") as _f:
    _all_results = json.load(_f)["matches"]

_wf = dict(TEAM_ELO)
_elo_snapshot = {}      # (home, away) -> pre-match Elo
for _r in _all_results:
    _key = (_r["home"], _r["away"])
    _elo_snapshot[_key] = dict(_wf)
    _elo_update(_wf, _r["home"], _r["away"], _r["hg"], _r["ag"])
_final_wf = dict(_wf)


# ── Process each match ────────────────────────────────────────────────────────

def process_match(m, elo_snap):
    home, away = m["home"], m["away"]
    oh, od, oa = m["odds_1x2"]
    ft_h, ft_a = m["ft_h"], m["ft_a"]
    total = ft_h + ft_a

    pm._ELO_CACHE = elo_snap.copy()
    he = elo_snap.get(home, 1700)
    ae = elo_snap.get(away, 1700)
    elo_diff = he - ae
    rule2_kill = abs(elo_diff) > GSV_LAMBDA_DIFF_EXTENDED

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = predict(
            home_team=home, away_team=away,
            odds_home=oh, odds_draw=od, odds_away=oa,
            ou_odds=m["ou_odds"] or None,
            ah_odds=m["ah_odds"] or None,
        )

    if not isinstance(result, dict) or "portfolio" not in result:
        return None, elo_diff, rule2_kill

    portfolio = result["portfolio"]
    probs = result["probs"]
    value = result.get("value", {})

    # Collect and grade items
    items = []
    for b in portfolio:
        label = b["label"]
        model_prob = b["model_prob"]
        odds_b = b["decimal_odds"]
        edge = b.get("edge", model_prob - 1/odds_b)
        market_true = b.get("market_true", 1/odds_b)
        bet_type, direction, line, team = parse_label(label)
        gap = abs(model_prob - market_true)

        if rule2_kill:
            grade = "KILL"
        elif gap >= ARTIFACT_KILL:
            grade = "KILL"
        elif gap >= ARTIFACT_GAP:
            grade = "LOW"
        else:
            grade = "MED"

        items.append({
            "label": label, "bet_type": bet_type, "direction": direction,
            "line": line, "odds": odds_b, "model_prob": model_prob,
            "market_true": market_true, "edge": edge, "grade": grade,
        })

    # Rule④: best AH + best OU per match
    ah_items = [it for it in items if it["bet_type"] == "AH"
                and it["grade"] == "MED" and it["edge"] >= MIN_EDGE]
    ou_items = [it for it in items if it["bet_type"] == "OU"
                and it["grade"] == "MED" and it["edge"] >= MIN_EDGE
                and not (OU_FENCE_LO <= it["model_prob"] <= OU_FENCE_HI)
                and not ("Under" in it["label"] and it["model_prob"] > 0.50
                         and (1.0 - it["market_true"]) >= UNDER_MKTOVER_KILL)]
    x12_raw  = [it for it in items if it["bet_type"] == "1X2"
                and it["grade"] == "MED" and it["edge"] >= MIN_EDGE]

    x12_items = []
    for it in x12_raw:
        lbl, mp, eg = it["label"], it["model_prob"], it["edge"]
        if "平局" in lbl and (mp < DRAW_MIN_PROB or eg < DRAW_MIN_EDGE): continue
        if "胜" in lbl and "平局" not in lbl and mp < WIN_MIN_PROB:      continue
        x12_items.append(it)

    best_ah = max(ah_items, key=lambda x: x["edge"]) if ah_items else None
    best_ou = max(ou_items, key=lambda x: x["edge"]) if ou_items else None

    # 近平场次 AH 压制
    if best_ah and abs(elo_diff) <= NEAR_EQUAL_AH_DIFF:
        best_ah = None

    # 近平场次 1X2 Win 压制：diff≤100 时方向性胜负不可靠（回测3/3全亏）
    if abs(elo_diff) <= NEAR_EQUAL_1X2_WIN_DIFF:
        x12_items = [it for it in x12_items
                     if it["direction"] not in ("home_win", "away_win")]

    # Rule④ 扩展：同向 1X2 + AH 去重
    if best_ah:
        ah_dir_as_1x2 = "home_win" if best_ah["direction"] == "home" else "away_win"
        same_dir = [it for it in x12_items if it["direction"] == ah_dir_as_1x2]
        if same_dir:
            best_same = max(same_dir, key=lambda x: x["edge"])
            if best_same["edge"] >= best_ah["edge"]:
                best_ah = None
            else:
                x12_items = [it for it in x12_items if it["direction"] != ah_dir_as_1x2]

    edge_candidates = x12_items + ([best_ah] if best_ah else []) + ([best_ou] if best_ou else [])

    # 稳单
    p_h = probs.get("home_win", 0)
    p_d = probs.get("draw", 0)
    p_a = probs.get("away_win", 0)
    best_1x2 = max([("home_win", p_h, oh, home), ("draw", p_d, od, "平局"),
                    ("away_win", p_a, oa, away)], key=lambda x: x[1])
    sd, sp, so, sl = best_1x2
    if sd == "home_win":
        stable_lbl = f"{home} 胜"
        stable_out = "WIN" if ft_h > ft_a else "LOSS"
    elif sd == "draw":
        stable_lbl = "平局"
        stable_out = "WIN" if ft_h == ft_a else "LOSS"
    else:
        stable_lbl = f"{away} 胜"
        stable_out = "WIN" if ft_a > ft_h else "LOSS"

    # OU参考
    ou_ref = None
    if "ou_lines" in value:
        for lv, sides in sorted(value["ou_lines"].items()):
            for dv in ["over", "under"]:
                mp = sides[dv]["model"]
                if mp >= 0.48:
                    lo = m["ou_odds"].get(lv)
                    if lo:
                        ou_line_odds = lo[0 if dv == "over" else 1]
                        if ou_ref is None or mp > ou_ref["model_prob"]:
                            ou_ref = {"label": f"{'Over' if dv=='over' else 'Under'} {lv}",
                                      "odds": ou_line_odds, "model_prob": mp,
                                      "line": lv, "direction": dv}

    # Edge結果
    edge_results = []
    for it in edge_candidates:
        bt, dr, ln = it["bet_type"], it["direction"], it["line"]
        if bt == "AH":
            out, _ = settle_ah(ln, ft_h, ft_a, dr)
        elif bt == "OU":
            out, _ = settle_ou(ln, total, dr)
        else:
            if dr == "home_win":  out = "WIN" if ft_h > ft_a else "LOSS"
            elif dr == "draw":    out = "WIN" if ft_h == ft_a else "LOSS"
            else:                 out = "WIN" if ft_a > ft_h else "LOSS"
        ep = pnl(out, it["odds"])
        edge_results.append({**it, "outcome": out, "pnl": ep})

    # OU参考結果
    ou_ref_result = None
    if ou_ref:
        out, _ = settle_ou(ou_ref["line"], total, ou_ref["direction"])
        ou_ref_result = {**ou_ref, "outcome": out, "pnl": pnl(out, ou_ref["odds"])}

    return {
        "home": home, "away": away, "score": f"{ft_h}-{ft_a}",
        "elo_diff": elo_diff, "he": he, "ae": ae,
        "rule2_kill": rule2_kill,
        "stable": {"label": stable_lbl, "odds": so, "prob": sp, "outcome": stable_out,
                   "pnl": pnl(stable_out, so)},
        "edge": edge_results,
        "ou_ref": ou_ref_result,
    }, elo_diff, rule2_kill


# ── Main: day-by-day output ───────────────────────────────────────────────────

from collections import defaultdict

by_date = defaultdict(list)
for m in MATCHES:
    by_date[m["date"]].append(m)

edge_all, stable_all, ou_all = [], [], []
running_edge_pnl = 0.0
running_stable_pnl = 0.0
running_edge_bets = 0

print("=" * 72)
print("  Walk-Forward 日历式回测  |  v3 pipeline  |  每日递增 Elo")
print("  双轨：Edge推单 + 稳单 + OU参考  |  近平AH压制 + Rule④同向去重")
print("=" * 72)

day_num = 0
elo_changes = {}  # 记录Elo变化用于每日显示

for date in sorted(by_date.keys()):
    day_num += 1
    matches_today = by_date[date]

    print(f"\n{'─'*72}")
    print(f"  Day {day_num}  —  2026-{date}  ({len(matches_today)} 场)")

    # 显示今日相关队伍的当前 Elo
    teams_today = set()
    for m in matches_today:
        teams_today.add(m["home"]); teams_today.add(m["away"])
    snap_key = (matches_today[0]["home"], matches_today[0]["away"])
    day_snap = _elo_snapshot.get(snap_key, _final_wf)
    elo_line = "  今日Elo: " + " | ".join(
        f"{t} {day_snap.get(t, TEAM_ELO.get(t,1700)):.0f}" for t in sorted(teams_today))
    print(elo_line)
    print(f"{'─'*72}")

    day_edge_pnl = 0.0
    day_stable_pnl = 0.0
    day_edge_bets = 0
    day_wins = 0
    day_losses = 0
    day_hl = 0

    for m in matches_today:
        snap_key = (m["home"], m["away"])
        elo_snap = _elo_snapshot.get(snap_key, _final_wf)
        res, elo_diff, rule2_kill = process_match(m, elo_snap)

        home, away = m["home"], m["away"]
        ft = f"{m['ft_h']}-{m['ft_a']}"
        sign = "+" if elo_diff >= 0 else ""
        kill_tag = f"  [KILL:Elo>{GSV_LAMBDA_DIFF_EXTENDED}]" if rule2_kill else ""

        print(f"\n  {home} {ft} {away}  (Elo差 {sign}{elo_diff:.0f}){kill_tag}")

        if res is None:
            print("    [无推单数据]")
            continue

        # 稳单
        s = res["stable"]
        s_sym = "WIN" if s["outcome"] == "WIN" else "LOSS"
        print(f"    [稳单] {s['label']:<22} @{s['odds']:.2f}  model={s['prob']*100:.1f}%  → {s_sym}  {s['pnl']:+.2f}")
        stable_all.append(s)
        day_stable_pnl += s["pnl"]
        running_stable_pnl += s["pnl"]

        # OU参考
        if res["ou_ref"]:
            ou = res["ou_ref"]
            print(f"    [OU参] {ou['label']:<22} @{ou['odds']:.2f}  model={ou['model_prob']*100:.1f}%  → {ou['outcome']}  {ou['pnl']:+.2f}")
            ou_all.append(ou)

        # Edge推单
        if not res["edge"]:
            if rule2_kill:
                print(f"    [Edge] 无 [KILL:Elo>{GSV_LAMBDA_DIFF_EXTENDED}]")
            else:
                print(f"    [Edge] 无满足条件注单")
        else:
            for it in res["edge"]:
                sym = {"WIN": "WIN", "LOSS": "LOSS", "HALF-WIN": "½W",
                       "HALF-LOSS": "½L", "PUSH": "PUSH"}.get(it["outcome"], it["outcome"])
                print(f"    [Edge] {it['label']:<26} @{it['odds']:.2f}  edge={it['edge']*100:+.1f}%  → {sym}  {it['pnl']:+.2f}")
                edge_all.append(it)
                day_edge_pnl += it["pnl"]
                running_edge_pnl += it["pnl"]
                day_edge_bets += 1
                running_edge_bets += 1
                if it["outcome"] in ("WIN",): day_wins += 1
                elif it["outcome"] in ("LOSS",): day_losses += 1
                elif "HALF" in it["outcome"]: day_hl += 1

    # Day summary
    n_edge = len([b for m in matches_today
                  for b in (process_match(m, _elo_snapshot.get((m["home"],m["away"]),_final_wf))[0] or {}).get("edge",[])])
    roi_run = running_edge_pnl / running_edge_bets * 100 if running_edge_bets else 0
    roi_stable_run = running_stable_pnl / len(stable_all) * 100 if stable_all else 0
    print(f"\n  ▸ 当日 Edge P&L: {day_edge_pnl:+.2f}  |  当日 稳单 P&L: {day_stable_pnl:+.2f}")
    print(f"  ▸ 累计 Edge: {running_edge_pnl:+.2f} ({running_edge_bets}注 ROI {roi_run:+.1f}%)  "
          f"稳单: {running_stable_pnl:+.2f} ({len(stable_all)}注 ROI {roi_stable_run:+.1f}%)")


# ── Final summary ─────────────────────────────────────────────────────────────

def _summarize(name, bets, pnl_key="pnl"):
    if not bets: return
    tp = sum(b[pnl_key] for b in bets)
    n = len(bets)
    wins    = sum(1 for b in bets if b.get("outcome") == "WIN")
    hw      = sum(1 for b in bets if b.get("outcome") == "HALF-WIN")
    hl      = sum(1 for b in bets if b.get("outcome") == "HALF-LOSS")
    losses  = sum(1 for b in bets if b.get("outcome") == "LOSS")
    roi = tp/n*100
    print(f"  {name:<10}: {n}注  {wins}W/{hw}HW/{hl}HL/{losses}L  P&L={tp:+.2f}  ROI={roi:+.1f}%")

ou_list = [{"outcome": o["outcome"], "pnl": o["pnl"]} for o in ou_all]
stable_list_clean = [{"outcome": s["outcome"], "pnl": s["pnl"]} for s in stable_all]
edge_list_clean   = [{"outcome": e["outcome"], "pnl": e["pnl"]} for e in edge_all]

print(f"\n{'='*72}")
print(f"  最终汇总  —  28场 walk-forward，Elo逐场递增")
print(f"{'='*72}")
_summarize("Edge推单", edge_list_clean)
_summarize("稳单",     stable_list_clean)
_summarize("OU参考",   ou_list)
print(f"{'='*72}")
