#!/usr/bin/env python3
"""
python3 today.py              — 用下方 MANUAL_MATCHES 手动赔率运行
python3 today.py --auto       — 从 The Odds API 自动抓取（需在 config.py 填 key）
python3 today.py --bankroll 5000  — 临时覆盖本金
"""

import sys
import argparse
sys.path.insert(0, ".")

from predict import predict
from src.betting.kelly import american_to_decimal
from config import BANKROLL, ODDS_API_KEY

# ── 手动赔率（无 API key 时使用，格式 American 或 decimal）─────────────────
MANUAL_MATCHES = [
    {
        "home": "Netherlands", "away": "Sweden",
        "odds_home": -145, "odds_draw": 290, "odds_away": 450,
        "odds_over25": -108, "odds_under25": -112,
    },
    {
        "home": "Germany", "away": "Ivory Coast",
        "odds_home": -190, "odds_draw": 340, "odds_away": 580,
        "odds_over25": -120, "odds_under25": 100,
    },
    {
        "home": "Ecuador", "away": "Curacao",
        "odds_home": -350, "odds_draw": 420, "odds_away": 900,
        "odds_over25": -130, "odds_under25": 110,
    },
    {
        "home": "Tunisia", "away": "Japan",
        "odds_home": 240, "odds_draw": 230, "odds_away": -140,
        "odds_over25": 105, "odds_under25": -125,
    },
]


def to_dec(v):
    if v is None:
        return None
    f = float(v)
    if abs(f) >= 100:
        return american_to_decimal(int(f))
    return f


def run_matches(matches: list[dict], bankroll: float):
    all_bets = []
    for m in matches:
        result = predict(
            home_team=m["home"],
            away_team=m["away"],
            odds_home=to_dec(m.get("odds_home")),
            odds_draw=to_dec(m.get("odds_draw")),
            odds_away=to_dec(m.get("odds_away")),
            odds_over25=to_dec(m.get("odds_over25")),
            odds_under25=to_dec(m.get("odds_under25")),
            bankroll=bankroll,
        )
        if isinstance(result, dict) and "portfolio" in result:
            all_bets.extend(result["portfolio"])

    value_bets = [b for b in all_bets if b.get("stake", 0) > 0]
    value_bets.sort(key=lambda x: -x["ev"])
    total_stake = sum(b["stake"] for b in value_bets)
    total_ev    = sum(b["ev"] for b in value_bets)

    print(f"\n{'═'*60}")
    print(f"  今日汇总  总投注 ¥{total_stake:.0f}  预期盈利 ¥{total_ev:.1f}")
    print(f"{'═'*60}")
    for b in value_bets:
        print(f"  {b['label']:<32} ¥{b['stake']:>6.0f}  EV ¥{b['ev']:>6.1f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true", help="从 The Odds API 自动抓取赔率")
    parser.add_argument("--bankroll", type=float, default=BANKROLL)
    args = parser.parse_args()

    if args.auto:
        if not ODDS_API_KEY:
            print("错误: config.py 中 ODDS_API_KEY 为空，请先填写")
            sys.exit(1)
        from src.data.odds_api import get_todays_matches
        matches = get_todays_matches()
        if not matches:
            print("今日暂无即将开始的世界杯比赛，或 API 返回为空")
            sys.exit(0)
        print(f"从 Pinnacle 抓取到 {len(matches)} 场比赛\n")
    else:
        matches = MANUAL_MATCHES

    run_matches(matches, args.bankroll)


if __name__ == "__main__":
    main()
