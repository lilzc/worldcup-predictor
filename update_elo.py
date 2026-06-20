#!/usr/bin/env python3
"""
赛后更新 Elo 评分，结果自动写入 data/elo_state.json

用法:
  python3 update_elo.py Spain "Cape Verde" 0 0
  python3 update_elo.py --replay          # 重跑所有已记录比赛，从头重建 Elo
  python3 update_elo.py --show            # 显示当前各队 Elo，与初始值对比
"""

import json
import sys
import os
import argparse

sys.path.insert(0, ".")
from config import TEAM_ELO

ELO_PATH = "data/elo_state.json"
RESULTS_PATH = "data/wc2026_results.json"
K = 60  # WC K-factor（FIFA 官方重要赛事用 60）


def load_elo() -> dict:
    if os.path.exists(ELO_PATH):
        with open(ELO_PATH) as f:
            return json.load(f)
    return dict(TEAM_ELO)


def save_elo(elo: dict):
    os.makedirs("data", exist_ok=True)
    with open(ELO_PATH, "w") as f:
        json.dump(elo, f, indent=2, ensure_ascii=False)


def expected_score(elo_a: float, elo_b: float) -> float:
    return 1 / (1 + 10 ** ((elo_b - elo_a) / 400))


def update_one(elo: dict, home: str, away: str, hg: int, ag: int) -> dict:
    eh = elo.get(home, 1700)
    ea = elo.get(away, 1700)

    Eh = expected_score(eh, ea)
    Ea = 1 - Eh

    if hg > ag:
        Sh, Sa = 1.0, 0.0
    elif hg == ag:
        Sh, Sa = 0.5, 0.5
    else:
        Sh, Sa = 0.0, 1.0

    elo[home] = round(eh + K * (Sh - Eh), 1)
    elo[away] = round(ea + K * (Sa - Ea), 1)
    return elo


def replay_all() -> dict:
    """Rebuild Elo from scratch using all recorded results."""
    with open(RESULTS_PATH) as f:
        data = json.load(f)

    elo = dict(TEAM_ELO)
    for m in data["matches"]:
        elo = update_one(elo, m["home"], m["away"], m["hg"], m["ag"])
    save_elo(elo)
    print(f"重建完成，共处理 {len(data['matches'])} 场比赛")
    return elo


def show(elo: dict):
    base = TEAM_ELO
    rows = []
    for team, current in elo.items():
        start = base.get(team, current)
        rows.append((team, start, current, current - start))
    rows.sort(key=lambda x: -x[2])

    print(f"\n  {'球队':<20} {'初始':>7} {'当前':>7} {'变化':>7}")
    print(f"  {'─'*45}")
    for team, s, c, d in rows:
        mark = "▲" if d > 0 else ("▼" if d < 0 else " ")
        print(f"  {team:<20} {s:>7.0f} {c:>7.0f} {mark}{abs(d):>6.0f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("home",   nargs="?", help="主队")
    parser.add_argument("away",   nargs="?", help="客队")
    parser.add_argument("hg",     nargs="?", type=int, help="主队进球")
    parser.add_argument("ag",     nargs="?", type=int, help="客队进球")
    parser.add_argument("--replay", action="store_true")
    parser.add_argument("--show",   action="store_true")
    args = parser.parse_args()

    if args.replay:
        elo = replay_all()
        show(elo)
        return

    elo = load_elo()

    if args.show:
        show(elo)
        return

    if not all([args.home, args.away, args.hg is not None, args.ag is not None]):
        parser.print_help()
        sys.exit(1)

    before_h = elo.get(args.home, 1700)
    before_a = elo.get(args.away, 1700)

    elo = update_one(elo, args.home, args.away, args.hg, args.ag)
    save_elo(elo)

    print(f"\n  {args.home} {args.hg}-{args.ag} {args.away}")
    print(f"  {args.home:<20} {before_h:.0f} → {elo[args.home]:.0f} ({elo[args.home]-before_h:+.0f})")
    print(f"  {args.away:<20} {before_a:.0f} → {elo[args.away]:.0f} ({elo[args.away]-before_a:+.0f})")

    # Also append to results file
    with open(RESULTS_PATH) as f:
        data = json.load(f)

    from datetime import date
    already = any(
        m["home"] == args.home and m["away"] == args.away
        for m in data["matches"]
    )
    if not already:
        data["matches"].append({
            "date": str(date.today()),
            "home": args.home, "away": args.away,
            "hg": args.hg, "ag": args.ag,
            "stage": "Group",
        })
        with open(RESULTS_PATH, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  已追加到 {RESULTS_PATH}")


if __name__ == "__main__":
    main()
