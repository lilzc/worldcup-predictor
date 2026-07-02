"""
python3 -m src.data.results_sync          — sync latest WC2026 results
python3 -m src.data.results_sync --dry-run — preview without writing
"""
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone

WC_START_DATE = "2026-06-01"
RESULTS_PATH  = "data/wc2026_results.json"

# Reuse form.py's CSV cache
CSV_CACHE_PATH = "data/cache/international_results.csv"
CSV_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
CSV_TTL = 86400 * 3  # 3 days (same as form.py)

# martj42 → local name map (kept in sync with form.py NAME_MAP)
NAME_MAP = {
    "United States":          "USA",
    "Ivory Coast":            "Ivory Coast",
    "Côte d'Ivoire":          "Ivory Coast",
    "Cote d'Ivoire":          "Ivory Coast",
    "South Korea":            "South Korea",
    "Korea Republic":         "South Korea",
    "DR Congo":               "Congo DR",
    "Curaçao":                "Curacao",
    "Curacao":                "Curacao",
    "Iran":                   "Iran",
    "Bosnia and Herzegovina": "Bosnia",
    "Czech Republic":         "Czechia",
    "Netherlands":            "Netherlands",
    "New Zealand":            "New Zealand",
    "Saudi Arabia":           "Saudi Arabia",
    "Cape Verde":             "Cape Verde",
}


def _normalize(name: str) -> str:
    return NAME_MAP.get(name, name)


def _fetch_csv() -> str:
    os.makedirs("data/cache", exist_ok=True)
    if os.path.exists(CSV_CACHE_PATH):
        age = datetime.now().timestamp() - os.path.getmtime(CSV_CACHE_PATH)
        if age < CSV_TTL:
            with open(CSV_CACHE_PATH, encoding="utf-8") as f:
                return f.read()
    import requests
    resp = requests.get(CSV_URL, timeout=30)
    resp.raise_for_status()
    content = resp.text
    with open(CSV_CACHE_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print("[results_sync] 已更新国际比赛记录缓存")
    return content


def fetch_wc2026_results() -> tuple[list[dict], str | None]:
    """
    Pull WC 2026 results from martj42 CSV.
    Returns (results_list, latest_date_str).
    """
    content = _fetch_csv()
    reader = csv.DictReader(io.StringIO(content))
    results = []
    latest_date = None

    for row in reader:
        date_str = row.get("date", "")
        if date_str < WC_START_DATE:
            continue
        tournament = row.get("tournament", "")
        if "World Cup" not in tournament:
            continue

        try:
            hg = int(row["home_score"])
            ag = int(row["away_score"])
        except (ValueError, KeyError):
            continue

        home = _normalize(row.get("home_team", ""))
        away = _normalize(row.get("away_team", ""))
        if not home or not away:
            continue

        results.append({
            "date":  date_str,
            "home":  home,
            "away":  away,
            "hg":    hg,
            "ag":    ag,
            "stage": "Group",
        })
        if latest_date is None or date_str > latest_date:
            latest_date = date_str

    return results, latest_date


def _check_freshness(latest_date: str | None) -> bool:
    """Warn if latest WC result is stale (>48h behind today)."""
    if latest_date is None:
        print("[results_sync] ⚠ CSV 中未找到 WC2026 比赛记录（martj42 尚未更新？）")
        return False
    cutoff = (datetime.now() - timedelta(hours=48)).strftime("%Y-%m-%d")
    if latest_date < cutoff:
        print(f"[results_sync] ⚠ 最新赛果日期 {latest_date}，距今超过48小时，可能未及时更新")
    return True


def sync_to_json(new_results: list[dict], dry_run: bool = False) -> int:
    """
    Merge new_results into wc2026_results.json.
    Returns number of new entries added.
    """
    if not os.path.exists(RESULTS_PATH):
        existing = {"_note": "2026 WC 实际赛果。", "matches": []}
    else:
        with open(RESULTS_PATH, encoding="utf-8") as f:
            existing = json.load(f)

    current = existing.get("matches", [])
    seen = {(m["date"], m["home"], m["away"]) for m in current}

    added = []
    for r in new_results:
        key = (r["date"], r["home"], r["away"])
        if key not in seen:
            added.append(r)
            seen.add(key)

    if not added:
        print("[results_sync] 无新赛果")
        return 0

    if dry_run:
        print(f"[results_sync] dry-run: 将新增 {len(added)} 条赛果:")
        for r in added:
            print(f"  {r['date']}  {r['home']} {r['hg']}-{r['ag']} {r['away']}")
        return len(added)

    current.extend(added)
    current.sort(key=lambda x: x["date"])
    existing["matches"] = current

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    print(f"[results_sync] 新增 {len(added)} 条赛果 → {RESULTS_PATH}")
    for r in added:
        print(f"  {r['date']}  {r['home']} {r['hg']}-{r['ag']} {r['away']}")
    return len(added)


def run(dry_run: bool = False) -> int:
    try:
        results, latest_date = fetch_wc2026_results()
    except Exception as e:
        print(f"[results_sync] 获取 CSV 失败: {e}")
        return 0

    print(f"[results_sync] 从 CSV 找到 {len(results)} 场 WC2026 赛果，最新: {latest_date}")
    if not _check_freshness(latest_date):
        return 0

    return sync_to_json(results, dry_run=dry_run)


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    run(dry_run=dry)
