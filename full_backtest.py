#!/usr/bin/env python3
"""
Full WC 2026 backtest — 28 matches dual-track recommendations vs actual results.
Tracks: Edge推单 / 稳单 / OU参考
"""
import sys, io, contextlib, re, os

# Use main repo — worktree predict.py is older (no ou_odds/ah_odds support)
MAIN = '/Users/ryanliu/Desktop/worldcup2026'
# Remove any worktree paths that might shadow main repo modules
sys.path = [p for p in sys.path if 'worktree' not in p and 'agent-' not in p]
sys.path.insert(0, MAIN)
os.chdir(MAIN)

from predict import predict
from config import MIN_EDGE, TEAM_ELO, NEAR_EQUAL_AH_DIFF, NEAR_EQUAL_1X2_WIN_DIFF
import src.models.poisson as pm
from src.models.poisson import get_elo

ARTIFACT_GAP  = 0.08   # model-market gap > 8% -> LOW
ARTIFACT_KILL = 0.20   # model-market gap > 20% -> KILL

# ── Settlement helpers ────────────────────────────────────────────────────────

def settle_ou_whole(line, total, direction):
    frac = line % 1
    if frac == 0.0:
        if direction == 'over':
            if total > line: return 'WIN', 1.0
            elif total == line: return 'PUSH', 0.0
            else: return 'LOSS', -1.0
        else:
            if total < line: return 'WIN', 1.0
            elif total == line: return 'PUSH', 0.0
            else: return 'LOSS', -1.0
    else:  # 0.5
        if direction == 'over':
            return ('WIN', 1.0) if total > line else ('LOSS', -1.0)
        else:
            return ('WIN', 1.0) if total < line else ('LOSS', -1.0)


def settle_ou(line, total, direction='over'):
    frac = round(line % 1, 2)
    if frac in (0.0, 0.5):
        return settle_ou_whole(line, total, direction)
    else:  # quarter line: 0.25 or 0.75
        line_lo = line - 0.25
        line_hi = line + 0.25
        out_lo, m_lo = settle_ou_whole(line_lo, total, direction)
        out_hi, m_hi = settle_ou_whole(line_hi, total, direction)
        avg = (m_lo + m_hi) / 2
        if avg >= 0.9:   return 'WIN',       1.0   # both halves win
        if avg >= 0.4:   return 'HALF-WIN',  0.5   # half win, half push
        if avg <= -0.9:  return 'LOSS',     -1.0   # both halves lose
        if avg <= -0.4:  return 'HALF-LOSS',-0.5   # half push, half lose
        return 'PUSH', 0.0


def settle_ah_whole(line, margin, direction):
    if direction == 'home':
        diff = margin - line
    else:
        diff = line - margin
    frac = line % 1
    if frac == 0.0:
        if diff > 0: return 'WIN', 1.0
        elif diff == 0: return 'PUSH', 0.0
        else: return 'LOSS', -1.0
    else:  # 0.5
        if diff > 0: return 'WIN', 1.0
        else: return 'LOSS', -1.0


def settle_ah(line, home_goals, away_goals, direction='home'):
    margin = home_goals - away_goals
    frac = round(line % 1, 2)
    if frac in (0.0, 0.5):
        return settle_ah_whole(line, margin, direction)
    else:  # quarter line
        line_lo = line - 0.25
        line_hi = line + 0.25
        out_lo, m_lo = settle_ah_whole(line_lo, margin, direction)
        out_hi, m_hi = settle_ah_whole(line_hi, margin, direction)
        avg = (m_lo + m_hi) / 2
        if avg >= 0.9:  return 'WIN',       1.0   # both halves win
        if avg >= 0.4:  return 'HALF-WIN',  0.5   # half win, half push
        if avg <= -0.9: return 'LOSS',     -1.0   # both halves lose
        if avg <= -0.4: return 'HALF-LOSS',-0.5   # half push, half lose
        return 'PUSH', 0.0


def pnl_from_outcome(outcome, mult, odds, stake=1.0):
    if outcome == 'WIN':
        return stake * (odds - 1)
    elif outcome == 'HALF-WIN':
        return stake * (odds - 1) * 0.5
    elif outcome == 'PUSH':
        return 0.0
    elif outcome == 'HALF-LOSS':
        return -stake * 0.5
    else:  # LOSS
        return -stake


# ── Parse portfolio label ─────────────────────────────────────────────────────

def parse_label(label):
    label = label.strip()
    m = re.match(r'^AH ([+-])(\d+(?:\.\d+)?)\s+(.+)$', label)
    if m:
        sign, val, team = m.group(1), float(m.group(2)), m.group(3)
        direction = 'home' if sign == '-' else 'away'
        return ('AH', direction, val, team)
    m = re.match(r'^(Over|Under)\s+(\d+(?:\.\d+)?)$', label)
    if m:
        direction = 'over' if m.group(1) == 'Over' else 'under'
        return ('OU', direction, float(m.group(2)), None)
    m = re.match(r'^主场胜\s*\((.+)\)$', label)
    if m:
        return ('1X2', 'home_win', None, m.group(1))
    if label == '平局':
        return ('1X2', 'draw', None, None)
    m = re.match(r'^客场胜\s*\((.+)\)$', label)
    if m:
        return ('1X2', 'away_win', None, m.group(1))
    return ('UNKNOWN', None, None, None)


# ── Gap check ─────────────────────────────────────────────────────────────────

def check_gap(model_prob, market_true):
    gap = abs(model_prob - market_true)
    if gap >= ARTIFACT_KILL:
        return 'KILL'
    if gap >= ARTIFACT_GAP:
        return 'LOW'
    return 'OK'


# ── Match data ────────────────────────────────────────────────────────────────

matches = [
    {"date":"06-15","home":"Belgium","away":"Egypt",
     "odds_1x2":(1.50,4.15,6.60),
     "ou_odds":{2.0:(1.70,2.25),2.25:(1.97,1.93),2.5:(1.97,1.93),2.75:(2.23,1.71),3.0:(2.66,1.50)},
     "ah_odds":{0.5:(1.50,2.72),0.75:(1.64,2.38),1.0:(1.93,1.99),1.25:(2.28,1.70),1.5:(2.58,1.55)},
     "ft_h":1,"ft_a":1,"ht_h":0,"ht_a":1},
    {"date":"06-17","home":"Portugal","away":"Congo DR",
     "odds_1x2":(1.27,5.60,11.00),
     "ou_odds":{2.0:(1.60,2.42),2.25:(2.06,1.84),2.5:(1.81,2.09),2.75:(2.06,1.84),3.0:(2.40,1.61),3.25:(2.69,1.49)},
     "ah_odds":{1.0:(1.43,2.96),1.25:(1.65,2.36),1.5:(1.88,2.04),1.75:(2.13,1.80),2.0:(2.56,1.56),2.25:(2.88,1.45)},
     "ft_h":1,"ft_a":1,"ht_h":1,"ht_a":1},
    {"date":"06-17","home":"England","away":"Croatia",
     "odds_1x2":(1.73,3.60,5.00),
     "ou_odds":{1.75:(1.47,2.75),2.0:(1.61,2.40),2.25:(1.90,2.00),2.5:(2.19,1.74),2.75:(2.53,1.55)},
     "ah_odds":{0.25:(1.51,2.69),0.5:(1.73,2.23),0.75:(1.96,1.96),1.0:(2.35,1.66),1.25:(2.72,1.50),1.5:(3.04,1.41)},
     "ft_h":4,"ft_a":2,"ht_h":2,"ht_a":2},
    {"date":"06-17","home":"Ghana","away":"Panama",
     "odds_1x2":(2.31,3.35,3.10),
     "ou_odds":{1.75:(1.57,2.49),2.0:(1.78,2.13),2.25:(2.11,1.80),2.5:(2.40,1.61)},
     "ah_odds":{0.0:(1.69,2.29),0.25:(2.07,1.85),0.5:(2.35,1.66),0.75:(2.81,1.47)},
     "ft_h":1,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-17","home":"Uzbekistan","away":"Colombia",
     "odds_1x2":(8.40,4.80,1.38),
     "ou_odds":{2.0:(1.72,2.21),2.25:(1.98,1.92),2.5:(1.98,1.92),2.75:(2.23,1.71),3.0:(2.66,1.50)},
     "ah_odds":{},
     "ft_h":1,"ft_a":3,"ht_h":0,"ht_a":1},
    {"date":"06-18","home":"Switzerland","away":"Bosnia",
     "odds_1x2":(1.54,4.20,6.00),
     "ou_odds":{2.0:(1.48,2.72),2.25:(2.01,1.89),2.5:(2.01,1.89),2.75:(2.28,1.68)},
     "ah_odds":{0.5:(1.54,2.61),0.75:(1.67,2.33),1.0:(1.95,1.97),1.25:(2.28,1.70),1.5:(2.58,1.55)},
     "ft_h":4,"ft_a":1,"ht_h":0,"ht_a":0},
    {"date":"06-18","home":"Canada","away":"Qatar",
     "odds_1x2":(1.28,5.60,10.00),
     "ou_odds":{2.5:(1.74,2.19),2.75:(1.94,1.96),3.0:(2.25,1.70),3.25:(2.53,1.55)},
     "ah_odds":{1.0:(1.44,2.92),1.25:(1.66,2.35),1.5:(1.89,2.03),1.75:(2.13,1.80),2.0:(2.56,1.56),2.25:(2.85,1.46)},
     "ft_h":6,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-18","home":"Mexico","away":"South Korea",
     "odds_1x2":(2.11,3.30,3.60),
     "ou_odds":{1.75:(1.53,2.58),2.0:(1.71,2.23),2.25:(2.02,1.88),2.5:(2.31,1.66)},
     "ah_odds":{0.0:(1.52,2.66),0.25:(1.40,3.08),0.5:(2.47,1.60),0.75:(3.04,1.41)},
     "ft_h":1,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-19","home":"USA","away":"Australia",
     "odds_1x2":(1.63,4.00,5.20),
     "ou_odds":{2.0:(1.70,2.25),2.25:(1.95,1.95),2.5:(1.95,1.95),2.75:(2.17,1.75),3.0:(2.56,1.54)},
     "ah_odds":{0.25:(1.45,2.88),0.5:(1.64,2.38),0.75:(1.83,2.09),1.0:(2.13,1.80),1.25:(2.47,1.60),1.5:(2.75,1.49)},
     "ft_h":2,"ft_a":0,"ht_h":2,"ht_a":0},
    {"date":"06-19","home":"Scotland","away":"Morocco",
     "odds_1x2":(5.40,3.45,1.72),
     "ou_odds":{1.75:(1.54,2.56),2.0:(1.72,2.21),2.25:(2.02,1.88),2.5:(2.31,1.66)},
     "ah_odds":{},
     "ft_h":0,"ft_a":1,"ht_h":0,"ht_a":1},
    {"date":"06-19","home":"Brazil","away":"Haiti",
     "odds_1x2":(1.09,10.50,23.00),
     "ou_odds":{3.25:(1.64,2.31),3.5:(1.83,2.05),3.75:(2.04,1.84),4.0:(2.31,1.64)},
     "ah_odds":{2.5:(1.83,2.07),2.75:(2.04,1.86),3.0:(2.33,1.65),3.25:(2.56,1.54)},
     "ft_h":3,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-19","home":"Turkey","away":"Paraguay",
     "odds_1x2":(2.08,3.45,3.50),
     "ou_odds":{2.0:(1.55,2.53),2.25:(1.83,2.07),2.5:(2.09,1.81),2.75:(2.40,1.61)},
     "ah_odds":{0.0:(1.53,2.63),0.25:(1.41,3.04),0.5:(2.42,1.62),0.75:(3.04,1.41)},
     "ft_h":0,"ft_a":1,"ht_h":0,"ht_a":1},
    {"date":"06-20","home":"Netherlands","away":"Sweden",
     "odds_1x2":(1.74,4.00,4.40),
     "ou_odds":{2.5:(1.67,2.29),2.75:(1.84,2.06),3.0:(2.12,1.79),3.25:(2.40,1.61)},
     "ah_odds":{0.25:(1.53,2.63),0.5:(1.72,2.25),0.75:(1.92,2.00),1.0:(2.25,1.72),1.25:(2.56,1.56),1.5:(2.85,1.46)},
     "ft_h":5,"ft_a":1,"ht_h":2,"ht_a":0},
    {"date":"06-20","home":"Germany","away":"Ivory Coast",
     "odds_1x2":(1.50,4.65,5.50),
     "ou_odds":{2.5:(1.59,2.44),2.75:(1.74,2.19),3.0:(1.96,1.94),3.25:(2.23,1.71)},
     "ah_odds":{0.5:(1.53,2.63),0.75:(1.66,2.35),1.0:(1.87,2.05),1.25:(2.14,1.79),1.5:(2.42,1.62)},
     "ft_h":2,"ft_a":1,"ht_h":0,"ht_a":1},
    {"date":"06-20","home":"Ecuador","away":"Curacao",
     "odds_1x2":(1.13,8.30,20.00),
     "ou_odds":{2.5:(1.58,2.47),2.75:(1.72,2.21),3.0:(1.94,1.96),3.25:(2.21,1.72)},
     "ah_odds":{1.5:(1.47,2.81),1.75:(1.57,2.53),2.0:(1.73,2.23),2.25:(2.01,1.91),2.5:(2.25,1.72),2.75:(2.58,1.55)},
     "ft_h":0,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-20","home":"Tunisia","away":"Japan",
     "odds_1x2":(5.80,4.00,1.58),
     "ou_odds":{2.0:(1.59,2.44),2.25:(1.87,2.03),2.5:(2.14,1.77),2.75:(2.47,1.58)},
     "ah_odds":{},
     "ft_h":0,"ft_a":4,"ht_h":0,"ht_a":2},
    {"date":"06-21","home":"Spain","away":"Saudi Arabia",
     "odds_1x2":(1.08,10.50,26.00),
     "ou_odds":{3.0:(1.56,2.47),3.25:(1.74,2.16),3.5:(1.99,1.89),3.75:(2.21,1.70),4.0:(2.53,1.55)},
     "ah_odds":{2.0:(1.45,2.81),2.25:(1.62,2.38),2.5:(1.82,2.08),2.75:(2.05,1.85),3.0:(2.35,1.64),3.25:(2.61,1.52)},
     "ft_h":4,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-21","home":"Belgium","away":"Iran",
     "odds_1x2":(1.44,4.65,7.00),
     "ou_odds":{2.0:(1.62,2.38),2.25:(1.84,2.06),2.5:(1.84,2.06),2.75:(2.08,1.82),3.0:(2.44,1.59)},
     "ah_odds":{0.5:(1.45,2.88),0.75:(1.55,2.58),1.0:(1.73,2.23),1.25:(2.01,1.91),1.5:(2.29,1.69),1.75:(2.69,1.51)},
     "ft_h":0,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-21","home":"Uruguay","away":"Cape Verde",
     "odds_1x2":(1.44,4.20,7.80),
     "ou_odds":{1.75:(1.55,2.53),2.0:(1.73,2.20),2.25:(2.05,1.85),2.5:(2.35,1.64)},
     "ah_odds":{0.5:(1.47,2.81),0.75:(1.59,2.49),1.0:(1.82,2.11),1.25:(2.16,1.78),1.5:(2.47,1.60),1.75:(2.92,1.44)},
     "ft_h":2,"ft_a":2,"ht_h":2,"ht_a":1},
    {"date":"06-21","home":"New Zealand","away":"Egypt",
     "odds_1x2":(5.60,4.00,1.59),
     "ou_odds":{2.0:(1.56,2.51),2.25:(1.84,2.06),2.5:(2.11,1.80),2.75:(2.42,1.60)},
     "ah_odds":{},
     "ft_h":1,"ft_a":3,"ht_h":1,"ht_a":0},
    {"date":"06-22","home":"Argentina","away":"Austria",
     "odds_1x2":(1.44,4.45,6.60),
     "ou_odds":{2.0:(1.67,2.29),2.25:(1.93,1.97),2.5:(1.93,1.97),2.75:(2.20,1.73),3.0:(2.53,1.55)},
     "ah_odds":{0.5:(1.48,2.78),0.75:(1.59,2.49),1.0:(1.79,2.14),1.25:(2.11,1.82),1.5:(2.42,1.62),1.75:(2.81,1.47)},
     "ft_h":2,"ft_a":0,"ht_h":1,"ht_a":0},
    {"date":"06-22","home":"France","away":"Iraq",
     "odds_1x2":(1.06,11.50,27.00),
     "ou_odds":{3.0:(1.64,2.31),3.25:(1.93,1.97),3.5:(1.83,2.05),3.75:(2.05,1.83),4.0:(2.31,1.64)},
     "ah_odds":{2.5:(1.74,2.19),2.75:(1.93,1.97),3.0:(2.20,1.73),3.25:(2.44,1.59)},
     "ft_h":3,"ft_a":0,"ht_h":1,"ht_a":0},
    {"date":"06-22","home":"Norway","away":"Senegal",
     "odds_1x2":(2.13,3.50,3.25),
     "ou_odds":{2.25:(1.64,2.35),2.5:(1.87,2.03),2.75:(2.13,1.78)},
     "ah_odds":{0.0:(1.60,2.47),0.25:(1.87,2.05),0.5:(2.14,1.79),0.75:(2.49,1.59)},
     "ft_h":3,"ft_a":2,"ht_h":1,"ht_a":0},
    {"date":"06-22","home":"Jordan","away":"Algeria",
     "odds_1x2":(6.40,4.00,1.55),
     "ou_odds":{2.25:(1.70,2.25),2.5:(1.94,1.96),2.75:(2.20,1.73)},
     "ah_odds":{},
     "ft_h":1,"ft_a":2,"ht_h":1,"ht_a":0},
    {"date":"06-24","home":"Portugal","away":"Uzbekistan",
     "odds_1x2":(1.11,8.80,20.00),
     "ou_odds":{2.75:(1.60,2.42),3.0:(1.74,2.19),3.5:(2.25,1.70)},
     "ah_odds":{1.75:(1.50,2.72),2.0:(1.66,2.35),2.5:(2.17,1.77),2.75:(2.44,1.61),3.0:(2.92,1.44)},
     "ft_h":5,"ft_a":0,"ht_h":3,"ht_a":0},
    {"date":"06-24","home":"England","away":"Ghana",
     "odds_1x2":(1.18,7.10,15.00),
     "ou_odds":{2.5:(1.59,2.44),2.75:(1.73,2.20),3.0:(1.97,1.93),3.25:(2.23,1.71)},
     "ah_odds":{1.5:(1.59,2.49),1.75:(1.74,2.21),2.25:(2.25,1.72),2.5:(2.53,1.57),2.75:(2.92,1.44)},
     "ft_h":0,"ft_a":0,"ht_h":0,"ht_a":0},
    {"date":"06-24","home":"Panama","away":"Croatia",
     "odds_1x2":(6.20,4.40,1.49),
     "ou_odds":{2.25:(1.55,2.53),2.5:(1.75,2.17),2.75:(1.97,1.93),3.0:(2.28,1.68)},
     "ah_odds":{},
     "ft_h":0,"ft_a":1,"ht_h":0,"ht_a":0},
    {"date":"06-24","home":"Colombia","away":"Congo DR",
     "odds_1x2":(1.54,4.00,6.30),
     "ou_odds":{2.0:(1.66,2.31),2.25:(1.98,1.92),2.5:(2.25,1.70),2.75:(2.58,1.53)},
     "ah_odds":{0.5:(1.55,2.58),0.75:(1.72,2.25),1.0:(2.01,1.91),1.25:(2.35,1.66),1.5:(2.69,1.51)},
     "ft_h":1,"ft_a":0,"ht_h":0,"ht_a":0},
]


# ── P&L accumulators ──────────────────────────────────────────────────────────

edge_bets = []
stable_bets = []
ou_ref_bets = []

print("=" * 70)
print("WC 2026 全场回测 — 28场 双轨推单 vs 实际结果")
print("=" * 70)

def _wf_elo_update(elo: dict, home: str, away: str, hg: int, ag: int) -> None:
    eh = elo.get(home, 1700)
    ea = elo.get(away, 1700)
    Eh = 1 / (1 + 10 ** ((ea - eh) / 400))
    Sh = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
    elo[home] = round(eh + 60 * (Sh - Eh), 1)
    elo[away] = round(ea + 60 * ((1 - Sh) - (1 - Eh)), 1)


# Build Elo snapshots from ALL wc2026_results.json (58场) — 赛前快照
import json as _json
with open(os.path.join(MAIN, 'data/wc2026_results.json')) as _f:
    _all_results = _json.load(_f)['matches']

_wf = dict(TEAM_ELO)
_elo_snapshot = {}  # (home, away) -> pre-match Elo
for _r in _all_results:
    _key = (_r['home'], _r['away'])
    _elo_snapshot[_key] = dict(_wf)
    _wf_elo_update(_wf, _r['home'], _r['away'], _r['hg'], _r['ag'])
_final_wf = dict(_wf)  # post-06-22 state — used for 06-24 matches not yet in results


for m in matches:
    home, away = m['home'], m['away']
    oh, od, oa = m['odds_1x2']
    ft_h, ft_a = m['ft_h'], m['ft_a']
    total = ft_h + ft_a
    margin = ft_h - ft_a

    _snap_key = (home, away)
    pm._ELO_CACHE = _elo_snapshot.get(_snap_key, _final_wf).copy()
    live = pm._ELO_CACHE
    he = live.get(home, 1700)
    ae = live.get(away, 1700)
    elo_diff = he - ae
    rule2_kill = abs(elo_diff) > 300

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        result = predict(
            home_team=home,
            away_team=away,
            odds_home=oh,
            odds_draw=od,
            odds_away=oa,
            ou_odds=m['ou_odds'] if m['ou_odds'] else None,
            ah_odds=m['ah_odds'] if m['ah_odds'] else None,
        )

    if not isinstance(result, dict) or 'portfolio' not in result:
        print(f"\n=== {m['date']} {home} {ft_h}-{ft_a} {away} — no portfolio ===")
        continue

    portfolio = result['portfolio']
    probs = result['probs']
    value = result['value']

    sign = '+' if elo_diff >= 0 else ''
    kill_tag = "  [KILL: Elo差>300]" if rule2_kill else ""
    print(f"\n=== {m['date']} {home} {ft_h}-{ft_a} {away}  (Elo差: {sign}{elo_diff:.0f}){kill_tag} ===")

    # ── Collect all portfolio items ───────────────────────────────────────
    items = []
    for b in portfolio:
        label = b['label']
        model_prob = b['model_prob']
        odds_b = b['decimal_odds']
        edge = b.get('edge', model_prob - 1/odds_b)
        market_true = b.get('market_true', 1/odds_b)

        bet_type, direction, line, team = parse_label(label)
        gap_status = check_gap(model_prob, market_true)

        items.append({
            'label': label,
            'bet_type': bet_type,
            'direction': direction,
            'line': line,
            'odds': odds_b,
            'model_prob': model_prob,
            'market_true': market_true,
            'edge': edge,
            'gap_status': gap_status,
        })

    # ── Apply Rule② and gap grades ────────────────────────────────────────
    if rule2_kill:
        for it in items:
            it['final_grade'] = 'KILL'
    else:
        for it in items:
            if it['gap_status'] == 'KILL':
                it['final_grade'] = 'KILL'
            elif it['gap_status'] == 'LOW':
                it['final_grade'] = 'LOW'
            else:
                it['final_grade'] = 'MED'

    # ── Rule④: dedup AH and OU — keep best edge per market type ──────────
    ah_items = [it for it in items if it['bet_type'] == 'AH'
                and it['final_grade'] == 'MED' and it['edge'] >= MIN_EDGE]
    ou_items = [it for it in items if it['bet_type'] == 'OU'
                and it['final_grade'] == 'MED' and it['edge'] >= MIN_EDGE]
    x12_items_raw = [it for it in items if it['bet_type'] == '1X2'
                     and it['final_grade'] == 'MED' and it['edge'] >= MIN_EDGE]

    # today.py filters for 1X2 bets (WIN_MIN_PROB, DRAW_MIN_PROB, DRAW_MIN_EDGE)
    WIN_MIN_PROB  = 0.25
    DRAW_MIN_PROB = 0.35
    DRAW_MIN_EDGE = 0.07
    x12_items = []
    for it in x12_items_raw:
        lbl = it['label']
        mp  = it['model_prob']
        eg  = it['edge']
        if '平局' in lbl:
            if mp < DRAW_MIN_PROB or eg < DRAW_MIN_EDGE:
                continue  # today.py kills this draw
        elif '胜' in lbl:
            if mp < WIN_MIN_PROB:
                continue  # today.py kills low-prob win bets
        x12_items.append(it)

    best_ah = max(ah_items, key=lambda x: x['edge']) if ah_items else None
    best_ou = max(ou_items, key=lambda x: x['edge']) if ou_items else None

    # 近平场次 AH 压制：Elo差≤100 时 AH 信号噪声比高（K=60更新±10-15点占diff本身10-15%）
    if best_ah and abs(elo_diff) <= NEAR_EQUAL_AH_DIFF:
        best_ah = None

    # 近平场次 1X2 Win 压制：diff≤100 时方向性下注全亏（回测3/3=100%亏损）
    if abs(elo_diff) <= NEAR_EQUAL_1X2_WIN_DIFF:
        x12_items = [it for it in x12_items
                     if it['direction'] not in ('home_win', 'away_win')]

    # Rule④ 扩展：1X2 Win 与 AH 同向时只保留 edge 更高那条
    if best_ah:
        ah_dir_as_1x2 = 'home_win' if best_ah['direction'] == 'home' else 'away_win'
        same_dir = [it for it in x12_items if it['direction'] == ah_dir_as_1x2]
        if same_dir:
            best_same = max(same_dir, key=lambda x: x['edge'])
            if best_same['edge'] >= best_ah['edge']:
                best_ah = None
            else:
                x12_items = [it for it in x12_items if it['direction'] != ah_dir_as_1x2]

    edge_candidates = x12_items + ([best_ah] if best_ah else []) + ([best_ou] if best_ou else [])

    # ── 稳单: highest model_prob 1X2 direction ───────────────────────────
    p_home = probs.get('home_win', 0)
    p_draw = probs.get('draw', 0)
    p_away = probs.get('away_win', 0)
    best_1x2 = max([('home_win', p_home, oh, home), ('draw', p_draw, od, '平局'),
                    ('away_win', p_away, oa, away)], key=lambda x: x[1])
    stable_direction, stable_prob, stable_odds, stable_label = best_1x2

    if stable_direction == 'home_win':
        stable_display = f"{home} 胜"
        stable_outcome = 'WIN' if ft_h > ft_a else 'LOSS'
    elif stable_direction == 'draw':
        stable_display = "平局"
        stable_outcome = 'WIN' if ft_h == ft_a else 'LOSS'
    else:
        stable_display = f"{away} 胜"
        stable_outcome = 'WIN' if ft_a > ft_h else 'LOSS'

    stable_pnl = pnl_from_outcome(stable_outcome, 1.0, stable_odds)
    print(f"  [稳单] {stable_display} @ {stable_odds:.2f}  model={stable_prob*100:.1f}%  -> {stable_outcome}  P&L={stable_pnl:+.2f}")
    stable_bets.append({
        'match': f"{m['date']} {home} vs {away}",
        'label': stable_display,
        'odds': stable_odds,
        'model_prob': stable_prob,
        'outcome': stable_outcome,
        'pnl': stable_pnl,
    })

    # ── OU参考: model_prob >= 0.48 highest qualifying OU line ────────────
    ou_ref_best = None
    if 'ou_lines' in value:
        for line_v, sides in sorted(value['ou_lines'].items()):
            for dir_v in ['over', 'under']:
                side = sides[dir_v]
                mp = side['model']
                if mp >= 0.48:
                    ou_line_odds = m['ou_odds'][line_v][0 if dir_v == 'over' else 1]
                    if ou_ref_best is None or mp > ou_ref_best['model_prob']:
                        ou_ref_best = {
                            'label': f"{'Over' if dir_v=='over' else 'Under'} {line_v}",
                            'odds': ou_line_odds,
                            'model_prob': mp,
                            'line': line_v,
                            'direction': dir_v,
                        }

    if ou_ref_best:
        out, mult = settle_ou(ou_ref_best['line'], total, ou_ref_best['direction'])
        ou_pnl = pnl_from_outcome(out, mult, ou_ref_best['odds'])
        print(f"  [OU参考] {ou_ref_best['label']} @ {ou_ref_best['odds']:.2f}  model={ou_ref_best['model_prob']*100:.1f}%  -> {out}  P&L={ou_pnl:+.2f}")
        ou_ref_bets.append({
            'match': f"{m['date']} {home} vs {away}",
            'label': ou_ref_best['label'],
            'odds': ou_ref_best['odds'],
            'model_prob': ou_ref_best['model_prob'],
            'outcome': out,
            'pnl': ou_pnl,
        })
    else:
        print(f"  [OU参考] 无>=48%的OU线")

    # ── Edge推单 output ───────────────────────────────────────────────────
    if not edge_candidates:
        if rule2_kill:
            print(f"  [Edge] 无注单 [KILL: Elo差>300]")
        else:
            print(f"  [Edge] 无满足条件注单 (edge<3% 或 全为LOW/KILL)")
    else:
        for it in edge_candidates:
            label = it['label']
            odds_v = it['odds']
            edge_v = it['edge']
            mp = it['model_prob']
            direction = it['direction']
            line = it['line']
            bet_type = it['bet_type']

            if bet_type == 'AH':
                out, mult = settle_ah(line, ft_h, ft_a, direction)
            elif bet_type == 'OU':
                out, mult = settle_ou(line, total, direction)
            else:  # 1X2
                mult = 1.0
                if direction == 'home_win':
                    out = 'WIN' if ft_h > ft_a else 'LOSS'
                elif direction == 'draw':
                    out = 'WIN' if ft_h == ft_a else 'LOSS'
                else:
                    out = 'WIN' if ft_a > ft_h else 'LOSS'

            ep = pnl_from_outcome(out, mult, odds_v)
            gap_note = f"  [gap={((mp - it['market_true'])*100):+.1f}%]" if it['gap_status'] != 'OK' else ''
            print(f"  [Edge] {label} @ {odds_v:.2f}  edge={edge_v*100:+.1f}%  MED{gap_note}  -> {out}  P&L={ep:+.2f}")
            edge_bets.append({
                'match': f"{m['date']} {home} vs {away}",
                'label': label,
                'odds': odds_v,
                'edge': edge_v,
                'outcome': out,
                'pnl': ep,
            })



# ── Summary tables ────────────────────────────────────────────────────────────

def summarize(name, bets):
    print(f"\n{'─'*70}")
    print(f"  {name} 汇总 ({len(bets)} 注)")
    print(f"  {'比赛':<32} {'投注':<22} {'赔率':>6} {'结果':>10} {'P&L':>7}")
    print(f"  {'─'*68}")
    total_pnl = 0
    wins = half_wins = half_losses = losses = pushes = 0
    for b in bets:
        total_pnl += b['pnl']
        out = b['outcome']
        if out == 'WIN': wins += 1
        elif out == 'HALF-WIN': half_wins += 1
        elif out == 'PUSH': pushes += 1
        elif out == 'HALF-LOSS': half_losses += 1
        else: losses += 1
        print(f"  {b['match']:<32} {b['label']:<22} {b['odds']:>6.2f} {out:>10} {b['pnl']:>+7.2f}")
    n = len(bets)
    roi = total_pnl / n * 100 if n > 0 else 0
    print(f"  {'─'*68}")
    print(f"  共 {n} 注: {wins}W / {half_wins}HW / {pushes}P / {half_losses}HL / {losses}L")
    print(f"  总P&L: {total_pnl:+.2f} 单位  ROI: {roi:+.1f}%")


print(f"\n\n{'='*70}")
print("  汇总报告")
print(f"{'='*70}")
summarize("Edge推单", edge_bets)
summarize("稳单", stable_bets)
summarize("OU参考", ou_ref_bets)

print(f"\n{'='*70}")
print("  综合P&L")
print(f"{'='*70}")
for name, bets in [("Edge推单", edge_bets), ("稳单", stable_bets), ("OU参考", ou_ref_bets)]:
    if bets:
        tp = sum(b['pnl'] for b in bets)
        roi = tp / len(bets) * 100
        wins = sum(1 for b in bets if b['outcome'] == 'WIN')
        hw = sum(1 for b in bets if b['outcome'] == 'HALF-WIN')
        hl = sum(1 for b in bets if b['outcome'] == 'HALF-LOSS')
        losses = sum(1 for b in bets if b['outcome'] == 'LOSS')
        print(f"  {name}: {len(bets)}注  {wins}W/{hw}HW/{hl}HL/{losses}L  总P&L={tp:+.2f}  ROI={roi:+.1f}%")
