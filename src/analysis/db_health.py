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


# ── Check 1: 缺失赛果（对照 martj42 CSV 权威赛程，不依赖 odds-api 滚动窗口）───
# 修复前：只看 cache 里有记录的场次，cache 过期或 API 离线时盲区大。
# 修复后：以 martj42 国际赛果 CSV 为权威来源，比较已完赛场次是否全部入库。

MARTJ42_CSV_PATH = "data/cache/international_results.csv"
_WC2026_START    = "2026-06-11"

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

    # ── martj42 CSV 对照（权威赛程）──
    if not os.path.exists(MARTJ42_CSV_PATH):
        # 权威赛程源缺失时无法核对漏录 → 保守判失败（缺信息不猜测通过）
        return {"name": name, "ok": False,
                "detail": "martj42 CSV 缓存不存在，无法核对漏录（保守判失败，请刷新 martj42 缓存）"}

    import csv as _csv, io as _io
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    try:
        with open(MARTJ42_CSV_PATH, encoding="utf-8") as f:
            content = f.read()
    except Exception as e:
        return {"name": name, "ok": False, "detail": f"读取 martj42 CSV 失败: {e}"}

    # Build DB lookup: team-pair regardless of home/away order
    db_pairs: set = set()
    for m in matches:
        key = tuple(sorted([m["home"], m["away"]]))
        db_pairs.add(key)

    # Read martj42, filter WC2026 period and past dates
    missing = []
    reader = _csv.DictReader(_io.StringIO(content))
    for row in reader:
        d = row.get("date", "")
        if d < _WC2026_START or d > today_str:
            continue
        h = _norm_team(row.get("home_team", ""))
        a = _norm_team(row.get("away_team", ""))
        try:
            int(row.get("home_score", ""))
            int(row.get("away_score", ""))
        except (ValueError, TypeError):
            continue  # 无比分 = 未完赛
        key = tuple(sorted([h, a]))
        if key not in db_pairs:
            missing.append(f"{d}: {h} vs {a}")

    if missing:
        detail = f"{len(missing)}场漏录(martj42有但DB无): " + ", ".join(missing[:5])
        if len(missing) > 5:
            detail += f" ... (+{len(missing)-5})"
        return {"name": name, "ok": False, "detail": detail}
    return {"name": name, "ok": True, "detail": f"全部覆盖（对照 martj42 CSV，WC2026 {_WC2026_START}起）"}


# ── Check 2: 重复记录 ─────────────────────────────────────────────────────────

def check_duplicates(matches: list[dict]) -> dict:
    name = "重复记录"
    seen: dict = {}
    dupes = []
    for m in matches:
        # 主客顺序归一化：A-B 与 B-A 同日视为同场重复（113→85 去重事故同类）
        key = (m.get("date"), *sorted([m.get("home") or "", m.get("away") or ""]))
        if key in seen:
            dupes.append(f"{m.get('date')} {m.get('home')} vs {m.get('away')}")
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


def assert_elo_fresh() -> None:
    """预测入口守卫：Elo/AD状态过期于赛果库时响亮拒跑。"""
    r = check_state_freshness()
    if not r["ok"]:
        print(f"\n  ✗ Elo 状态过期于赛果库: {r['detail']}")
        print(f"  先执行: python3 update_elo.py --replay")
        print(f"  不允许带过期 Elo 出预测。")
        sys.exit(1)


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
    # odds_history 非空却零覆盖 = 系统性 key/格式断裂（如队名归一漂移）→ 判失败
    # 部分覆盖不判失败：早期场次在盘口追踪开始前无 odds_history 属正常
    if total > 0 and n_covered == 0:
        return {"name": name, "ok": False,
                "detail": detail + " — 零覆盖，疑 key/格式断裂"}
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
