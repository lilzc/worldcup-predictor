import json
import os
from datetime import datetime, timezone

HISTORY_PATH = "data/odds_history.jsonl"
MAX_SNAPSHOTS_PER_MATCH = 20


def save_snapshot(matches: list[dict]) -> None:
    """Append a timestamped snapshot of current odds to the history file."""
    if not matches:
        return
    os.makedirs("data", exist_ok=True)
    entry = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "matches": [
            {
                "home": m["home"],
                "away": m["away"],
                "ah_line":      m.get("ah_line"),
                "odds_ah_home": m.get("odds_ah_home"),
                "odds_ah_away": m.get("odds_ah_away"),
                "odds_over25":  m.get("odds_over25"),
                "odds_under25": m.get("odds_under25"),
                "alt_totals":   m.get("alt_totals", {}),
            }
            for m in matches
        ],
    }
    with open(HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    _prune_history()


def _prune_history():
    """Keep at most MAX_SNAPSHOTS_PER_MATCH per (home, away) pair."""
    if not os.path.exists(HISTORY_PATH):
        return
    with open(HISTORY_PATH, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip()]

    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    counts: dict[tuple, int] = {}
    kept = []
    for entry in reversed(entries):
        kept_matches = []
        for m in entry.get("matches", []):
            key = (m["home"], m["away"])
            counts[key] = counts.get(key, 0) + 1
            if counts[key] <= MAX_SNAPSHOTS_PER_MATCH:
                kept_matches.append(m)
        if kept_matches:
            kept.append({**entry, "matches": kept_matches})

    kept.reverse()
    with open(HISTORY_PATH, "w", encoding="utf-8") as f:
        for entry in kept:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load_match_snapshots(home: str, away: str) -> list[dict]:
    """Return all snapshots for a given match, oldest first."""
    if not os.path.exists(HISTORY_PATH):
        return []
    snapshots = []
    with open(HISTORY_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            for m in entry.get("matches", []):
                if m["home"] == home and m["away"] == away:
                    snapshots.append({"fetched_at": entry.get("fetched_at", ""), **m})
    return snapshots


def get_movement(home: str, away: str) -> dict:
    """
    Compare first and latest snapshot for a match.
    Returns direction and size of AH line + totals movement.
    Needs at least 2 snapshots; returns None fields if insufficient.
    """
    snaps = _load_match_snapshots(home, away)
    if len(snaps) < 2:
        return {
            "home": home, "away": away,
            "snapshots": len(snaps),
            "ah_line_open": None, "ah_line_now": None, "ah_direction": None,
            "total_open": None, "total_now": None, "total_direction": None,
            "market_lean": "insufficient_data",
        }

    first, latest = snaps[0], snaps[-1]

    def _direction(a, b):
        if a is None or b is None:
            return None
        if b > a:
            return "up"
        if b < a:
            return "down"
        return "flat"

    ah_open = first.get("ah_line")
    ah_now  = latest.get("ah_line")
    # Pick lowest available total line as representative
    total_open = _lowest_total(first)
    total_now  = _lowest_total(latest)

    ah_dir    = _direction(ah_open, ah_now)
    total_dir = _direction(total_open, total_now)

    lean = _infer_lean(ah_dir, total_dir, ah_open, ah_now, total_open, total_now)

    return {
        "home": home, "away": away,
        "snapshots": len(snaps),
        "ah_line_open": ah_open,  "ah_line_now": ah_now,  "ah_direction": ah_dir,
        "total_open": total_open, "total_now": total_now, "total_direction": total_dir,
        "market_lean": lean,
        "stale_gap": _check_stale(snaps),
    }


def _lowest_total(snap: dict) -> float | None:
    """Return the lowest available O/U line from alt_totals (or fall back to over25 baseline)."""
    alt = snap.get("alt_totals", {})
    if alt:
        try:
            return min(float(k) for k in alt.keys())
        except Exception:
            pass
    # Fallback: infer from over25 odds if line not stored
    return None


def _infer_lean(ah_dir, total_dir, ah_open, ah_now, total_open, total_now) -> str:
    """
    Market lean inference rules:
    - Total line moved UP (e.g. 2.5→3.0) → market expects more goals → "over"
    - Total line moved DOWN → "under"
    - AH line moved in home's favour (more negative) → home expected to win bigger → less relevant for O/U
    - Conflicting signals → "neutral"
    """
    if total_dir == "up":
        return "over"
    if total_dir == "down":
        return "under"
    if total_dir == "flat" or total_dir is None:
        return "neutral"
    return "neutral"


def _check_stale(snaps: list[dict]) -> bool:
    """Return True if gap between consecutive snapshots exceeds 2 hours."""
    if len(snaps) < 2:
        return False
    try:
        t1 = datetime.fromisoformat(snaps[-2]["fetched_at"])
        t2 = datetime.fromisoformat(snaps[-1]["fetched_at"])
        return abs((t2 - t1).total_seconds()) > 7200
    except Exception:
        return False


def print_movement_summary(matches: list[dict]) -> None:
    """Print a one-line AH movement summary per match."""
    print(f"\n  亚盘 + 大小球盘口移动:")
    print(f"  {'比赛':<28} {'AH开→现':>12} {'大小线开→现':>14} {'市场倾向':>10}")
    print(f"  {'─'*66}")
    for m in matches:
        mv = get_movement(m["home"], m["away"])
        ah_str = f"{mv['ah_line_open']}→{mv['ah_line_now']}" if mv['ah_line_open'] is not None else "N/A"
        tot_str = f"{mv['total_open']}→{mv['total_now']}" if mv['total_open'] is not None else "N/A"
        lean = mv["market_lean"]
        stale = " ⚠stale" if mv.get("stale_gap") else ""
        match_str = f"{m['home'][:10]} v {m['away'][:10]}"
        print(f"  {match_str:<28} {ah_str:>12} {tot_str:>14} {lean:>10}{stale}")
