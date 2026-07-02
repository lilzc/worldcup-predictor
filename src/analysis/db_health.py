"""
DB health check for wc2026_results.json and related state files.

Usage:
    python3 -m src.analysis.db_health
"""
import sys
import os
import json
from datetime import datetime, timezone, timedelta

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

RESULTS_PATH    = "data/wc2026_results.json"
ELO_STATE_PATH  = "data/elo_state.json"
AD_STATE_PATH   = "data/attack_defense_state.json"
CACHE_DIR       = "data/cache"
ODDS_HISTORY    = "data/odds_history.jsonl"


def _load_results() -> list[dict]:
    if not os.path.exists(RESULTS_PATH):
        return []
    with open(RESULTS_PATH, encoding="utf-8") as f:
        return json.load(f).get("matches", [])


# ── Check 1: 缺失赛果（cache 里有 commence_time 超3小时但 results 无记录）────

_COMBINED_NAME_MAP = {
    "United States": "USA",
    "Ivory Coast": "Ivory Coast",
    "Côte d'Ivoire": "Ivory Coast",
    "Cote d'Ivoire": "Ivory Coast",
    "South Korea": "South Korea",
    "Korea Republic": "South Korea",
    "DR Congo": "Congo DR",
    "Curaçao": "Curacao",
    "Curacao": "Curacao",
    "Iran": "Iran",
    "Iran (Islamic Republic of)": "Iran",
    "Bosnia and Herzegovina": "Bosnia",
    "Bosnia & Herzegovina": "Bosnia",
    "Czech Republic": "Czechia",
    "Netherlands": "Netherlands",
    "New Zealand": "New Zealand",
    "Saudi Arabia": "Saudi Arabia",
    "Cape Verde": "Cape Verde",
}


def _norm_team(name: str) -> str:
    return _COMBINED_NAME_MAP.get(name, name)


def check_missing_results(matches: list[dict]) -> dict:
    name = "缺失赛果"
    if not os.path.exists(CACHE_DIR):
        return {"name": name, "ok": True, "detail": "cache目录不存在，跳过"}

    # Build lookup using canonical names; allow both orientations
    seen_pairs: set = set()
    for m in matches:
        h, a, d = m["home"], m["away"], m["date"]
        seen_pairs.add((d, h, a))
        seen_pairs.add((d, a, h))

    # Build a set of (home, away) pairs in results (regardless of date) for ±1 day check
    seen_pair_nodates: set = set()
    for m in matches:
        h, a = m["home"], m["away"]
        seen_pair_nodates.add((h, a))
        seen_pair_nodates.add((a, h))

    now_ts = datetime.now(timezone.utc).timestamp()
    missing = []

    for fname in os.listdir(CACHE_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(CACHE_DIR, fname)
        try:
            with open(fpath, encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue

        # data can be a list (odds events) or a dict
        events = data if isinstance(data, list) else []
        for event in events:
            commence = event.get("commence_time", "")
            if not commence:
                continue
            try:
                ct = datetime.fromisoformat(commence.replace("Z", "+00:00")).timestamp()
            except Exception:
                continue
            # Only flag completed matches (started >3h ago)
            if now_ts - ct < 3 * 3600:
                continue
            home = _norm_team(event.get("home_team", ""))
            away = _norm_team(event.get("away_team", ""))
            date_str = datetime.fromtimestamp(ct, tz=timezone.utc).strftime("%Y-%m-%d")

            # Check date-exact match first
            key  = (date_str, home, away)
            key_r = (date_str, away, home)
            if key in seen_pairs or key_r in seen_pairs:
                continue

            # Try ±1 day (UTC timezone ambiguity)
            from datetime import date as _dateclass, timedelta as _td
            try:
                d = _dateclass.fromisoformat(date_str)
            except Exception:
                d = None
            found_nearby = False
            if d:
                for delta in (-1, 1):
                    nd = (d + _td(days=delta)).isoformat()
                    if (nd, home, away) in seen_pairs or (nd, away, home) in seen_pairs:
                        found_nearby = True
                        break
            if found_nearby:
                continue

            # Also check no-date pair match (already in results, just different date)
            if (home, away) in seen_pair_nodates:
                continue

            missing.append(f"{date_str} {home} vs {away}")

    if missing:
        detail = f"{len(missing)}场疑似漏录: " + ", ".join(missing[:5])
        if len(missing) > 5:
            detail += f" ... (+{len(missing)-5})"
        return {"name": name, "ok": False, "detail": detail}
    return {"name": name, "ok": True, "detail": "全部覆盖"}


# ── Check 2: 重复记录 ─────────────────────────────────────────────────────────

def check_duplicates(matches: list[dict]) -> dict:
    name = "重复记录"
    seen: dict = {}
    dupes = []
    for m in matches:
        key = (m.get("date"), m.get("home"), m.get("away"))
        if key in seen:
            dupes.append(f"{key[0]} {key[1]} vs {key[2]}")
        else:
            seen[key] = True
    if dupes:
        return {"name": name, "ok": False, "detail": f"{len(dupes)}条重复: " + ", ".join(dupes)}
    return {"name": name, "ok": True, "detail": f"无重复 ({len(matches)}场)"}


# ── Check 3: 字段完整性 ───────────────────────────────────────────────────────

def check_field_integrity(matches: list[dict]) -> dict:
    name = "字段完整性"
    required = ["date", "home", "away", "hg", "ag", "stage"]
    errors = []
    import re
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    for i, m in enumerate(matches):
        for f in required:
            if f not in m or m[f] is None:
                errors.append(f"第{i+1}条缺 {f}")
        if "hg" in m and not (isinstance(m["hg"], int) and m["hg"] >= 0):
            errors.append(f"第{i+1}条 hg 非法: {m.get('hg')}")
        if "ag" in m and not (isinstance(m["ag"], int) and m["ag"] >= 0):
            errors.append(f"第{i+1}条 ag 非法: {m.get('ag')}")
        if "date" in m and m["date"] and not date_re.match(str(m["date"])):
            errors.append(f"第{i+1}条 date 格式错误: {m.get('date')}")
    if errors:
        detail = f"{len(errors)}个问题: " + "; ".join(errors[:5])
        if len(errors) > 5:
            detail += f" ... (+{len(errors)-5})"
        return {"name": name, "ok": False, "detail": detail}
    return {"name": name, "ok": True, "detail": f"全部字段完整 ({len(matches)}场)"}


# ── Check 4: 队名一致性 ───────────────────────────────────────────────────────

def check_team_names(matches: list[dict]) -> dict:
    name = "队名一致性"
    try:
        from config import TEAM_ELO
    except ImportError:
        return {"name": name, "ok": False, "detail": "无法导入 config.TEAM_ELO"}

    known = set(TEAM_ELO.keys())
    unknown = set()
    for m in matches:
        for side in ("home", "away"):
            t = m.get(side, "")
            if t and t not in known:
                unknown.add(t)
    if unknown:
        listed = sorted(unknown)
        return {"name": name, "ok": False, "detail": f"{len(listed)}队未匹配: {listed}"}
    return {"name": name, "ok": True, "detail": f"全部队名在TEAM_ELO中 ({len(matches)}场)"}


# ── Check 5: 时序单调 ─────────────────────────────────────────────────────────

def check_chronological(matches: list[dict]) -> dict:
    name = "时序单调"
    out_of_order = []
    for i in range(1, len(matches)):
        prev = matches[i - 1].get("date", "")
        curr = matches[i].get("date", "")
        if curr < prev:
            out_of_order.append(
                f"第{i}→{i+1}条: {prev}→{curr} ({matches[i-1].get('home')} vs {matches[i].get('home')})"
            )
    if out_of_order:
        detail = f"{len(out_of_order)}处乱序: " + "; ".join(out_of_order[:3])
        return {"name": name, "ok": False, "detail": detail}
    return {"name": name, "ok": True, "detail": f"日期顺序单调 ({len(matches)}场)"}


# ── Check 6: 状态文件同步 ─────────────────────────────────────────────────────

def check_state_freshness() -> dict:
    name = "状态文件同步"
    if not os.path.exists(RESULTS_PATH):
        return {"name": name, "ok": False, "detail": "results.json 不存在"}

    results_mtime = os.path.getmtime(RESULTS_PATH)
    stale = []
    for path, label in [(ELO_STATE_PATH, "elo_state.json"), (AD_STATE_PATH, "attack_defense_state.json")]:
        if not os.path.exists(path):
            stale.append(f"{label} 不存在")
        elif os.path.getmtime(path) < results_mtime:
            stale.append(f"{label} 过期，需要 replay")

    if stale:
        return {"name": name, "ok": False, "detail": "; ".join(stale)}
    return {"name": name, "ok": True, "detail": "elo_state 和 AD_state 均晚于 results.json"}


# ── Check 7: odds_history 覆盖率 ──────────────────────────────────────────────

def check_odds_history_coverage(matches: list[dict]) -> dict:
    name = "odds_history覆盖率"
    if not os.path.exists(ODDS_HISTORY):
        return {"name": name, "ok": True, "detail": "odds_history.jsonl 不存在，跳过"}

    results_keys = set()
    for m in matches:
        results_keys.add((m.get("home"), m.get("away")))
        results_keys.add((m.get("away"), m.get("home")))

    covered = set()
    try:
        with open(ODDS_HISTORY, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                for om in entry.get("matches", []):
                    h = om.get("home", "")
                    a = om.get("away", "")
                    if (h, a) in results_keys or (a, h) in results_keys:
                        covered.add((h, a) if (h, a) in results_keys else (a, h))
    except Exception as e:
        return {"name": name, "ok": False, "detail": f"读取失败: {e}"}

    total = len(matches)
    n_covered = len(covered)
    pct = 100 * n_covered / total if total else 0
    detail = f"覆盖 {n_covered}/{total} 场 ({pct:.0f}%)"
    return {"name": name, "ok": True, "detail": detail}


# ── 主函数 ────────────────────────────────────────────────────────────────────

def run_all_checks() -> dict:
    matches = _load_results()
    n = len(matches)

    checks = [
        check_missing_results(matches),
        check_duplicates(matches),
        check_field_integrity(matches),
        check_team_names(matches),
        check_chronological(matches),
        check_state_freshness(),
        check_odds_history_coverage(matches),
    ]

    issues = [c["detail"] for c in checks if not c["ok"]]
    ok = len(issues) == 0
    return {"ok": ok, "n_matches": n, "issues": issues, "checks": checks}


def print_report(result: dict) -> None:
    SEP = "━" * 42
    CHECK_NAMES = [
        "缺失赛果",
        "重复记录",
        "字段完整性",
        "队名一致性",
        "时序单调",
        "状态文件同步",
        "odds_history覆盖率",
    ]
    print("DB HEALTH CHECK")
    print(SEP)
    for c in result["checks"]:
        mark = "✓" if c["ok"] else "✗"
        label = c["name"].ljust(16)
        print(f"  {mark} {label}  {c['detail']}")
    print(SEP)
    n = result["n_matches"]
    if result["ok"]:
        print(f"DB HEALTH: OK ({n} matches)")
    else:
        ni = len(result["issues"])
        print(f"DB HEALTH: WARN ({ni} issues, {n} matches)")


if __name__ == "__main__":
    result = run_all_checks()
    print_report(result)
    sys.exit(0 if result["ok"] else 1)
