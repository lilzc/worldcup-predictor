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

# WC2026 淘汰赛首日。daily_sync 的口径警告（"请确认是否为90min比分"）依赖 stage
# 字段，此前 5 处硬编码 "Group" 导致警告永远不触发（2026-07-05 修复）。
KNOCKOUT_START = "2026-06-28"


def _stage_for(date_str: str) -> str:
    return "Knockout" if date_str >= KNOCKOUT_START else "Group"

# Reuse form.py's CSV cache
CSV_CACHE_PATH = "data/cache/international_results.csv"
CSV_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
CSV_COMMITS_API = "https://api.github.com/repos/martj42/international_results/commits?path=results.csv&per_page=1"
CSV_TTL_FALLBACK = 43200  # 12h fallback if GitHub API unavailable

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


def _martj42_last_commit_ts() -> float | None:
    """Return UNIX timestamp of latest results.csv commit, or None on failure."""
    try:
        import urllib.request, json as _json
        req = urllib.request.Request(
            CSV_COMMITS_API,
            headers={"User-Agent": "wc2026-model", "Accept": "application/vnd.github+json"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            commits = _json.loads(r.read())
        if commits:
            dt_str = commits[0]["commit"]["committer"]["date"]  # e.g. "2026-07-03T05:06:30Z"
            dt = datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            return dt.timestamp()
    except Exception:
        pass
    return None


def _fetch_csv() -> str:
    os.makedirs("data/cache", exist_ok=True)
    cache_exists = os.path.exists(CSV_CACHE_PATH)
    if cache_exists:
        cache_mtime = os.path.getmtime(CSV_CACHE_PATH)
        # Prefer commit-timestamp comparison; fall back to 12h TTL
        last_commit = _martj42_last_commit_ts()
        if last_commit is not None:
            stale = last_commit > cache_mtime
            method = "commit-ts"
        else:
            stale = (datetime.now().timestamp() - cache_mtime) > CSV_TTL_FALLBACK
            method = "12h-fallback"
        if not stale:
            with open(CSV_CACHE_PATH, encoding="utf-8") as f:
                return f.read()
        print(f"[results_sync] 缓存过期({method})，重新拉取 martj42 CSV")
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
            "stage": _stage_for(date_str),
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


# ─────────────────────────────────────────────────────────────────────────────
# Daily-sync extensions: dual-source cross-validation + staging management
# ─────────────────────────────────────────────────────────────────────────────

import subprocess
import hashlib
from datetime import date as _date

STAGING_PATH  = "data/results_staging.json"
PENDING_PATH  = "data/pending_results.jsonl"
RESULTS_AUTO_COMMIT = False  # 默认 False：双源一致时仍需人工确认
                              # True=双源一致后自动入库+replay，风险：跳过人工检查，
                              # 若某源数据错误（如延迟更新的比分）将直接污染 elo_state.json


def fetch_scores_from_api(days_from: int = 2) -> list[dict]:
    """
    GET /v4/sports/soccer_fifa_world_cup/scores/?daysFrom=...
    返回 [{"home":str,"away":str,"hg":int,"ag":int,"date":str,"completed":bool}, ...]
    只返回 completed=True 的场次，队名已归一化。
    API key 从 config.py ODDS_API_KEY 读取。
    消耗约 1 credit。
    失败返回 []（不 raise，调用方判断空列表）。
    """
    # Import here to keep module-level imports minimal
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    try:
        from config import ODDS_API_KEY
    except ImportError:
        print("[results_sync] ⚠ 无法导入 config.ODDS_API_KEY，跳过 API scores 源")
        return []

    if not ODDS_API_KEY:
        print("[results_sync] ⚠ ODDS_API_KEY 未设置，跳过 odds-api scores 源")
        return []

    import requests as _requests
    url = f"https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup/scores/"
    params = {"apiKey": ODDS_API_KEY, "daysFrom": days_from}
    try:
        resp = _requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        remaining = resp.headers.get("x-requests-remaining", "?")
        used = resp.headers.get("x-requests-used", "?")
        print(f"[results_sync] odds-api scores: 已用 {used}，剩余 {remaining} credits")
    except Exception as e:
        print(f"[results_sync] ⚠ odds-api scores 拉取失败: {e}")
        return []

    # odds-api name map for scores endpoint
    _SCORE_NAME_MAP = {
        "United States": "USA",
        "Cote d'Ivoire": "Ivory Coast",
        "Côte d'Ivoire": "Ivory Coast",
        "Korea Republic": "South Korea",
        "Curacao": "Curacao",
        "Curaçao": "Curacao",
        "DR Congo": "Congo DR",
        "Netherlands": "Netherlands",
        "Iran (Islamic Republic of)": "Iran",
        "Czechia": "Czechia",
        "Czech Republic": "Czechia",
        "Bosnia and Herzegovina": "Bosnia",
        "Bosnia & Herzegovina": "Bosnia",
        "New Zealand": "New Zealand",
        "Saudi Arabia": "Saudi Arabia",
        "Cape Verde": "Cape Verde",
        "South Korea": "South Korea",
    }

    def _norm_api(name: str) -> str:
        return _SCORE_NAME_MAP.get(name, name)

    results = []
    for event in data:
        if not event.get("completed", False):
            continue
        scores = event.get("scores") or []
        home_raw = event.get("home_team", "")
        away_raw = event.get("away_team", "")
        home = _norm_api(home_raw)
        away = _norm_api(away_raw)

        home_score = None
        away_score = None
        for s in scores:
            sname = _norm_api(s.get("name", ""))
            try:
                val = int(s["score"])
            except (KeyError, ValueError, TypeError):
                continue
            if sname == home:
                home_score = val
            elif sname == away:
                away_score = val

        if home_score is None or away_score is None:
            if home_raw and away_raw:
                print(f"[results_sync] ⚠ 队名未匹配，跳过: {home_raw} vs {away_raw}")
            continue

        # Extract date from commence_time
        commence = event.get("commence_time", "")
        try:
            from datetime import datetime as _dt, timezone as _tz
            ct = _dt.fromisoformat(commence.replace("Z", "+00:00"))
            date_str = ct.strftime("%Y-%m-%d")
        except Exception:
            date_str = commence[:10] if len(commence) >= 10 else ""

        if not date_str:
            continue

        results.append({
            "home": home,
            "away": away,
            "hg": home_score,
            "ag": away_score,
            "date": date_str,
            "completed": True,
        })

    return results


def cross_validate(src_a: list[dict], src_b: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    src_a: odds-api scores（已归一化队名）
    src_b: martj42 CSV（已归一化队名）

    匹配逻辑：(home, away) 或 (away, home) 同日期±1天内 → 视为同一场
    （允许±1天是因为时区差异，UTC 凌晨比赛日期可能差1天）

    返回 (confirmed, pending)：
    - confirmed: 两源比分一致，verdict="AGREE"
    - pending: 两源不一致或只有一源，verdict="CONFLICT"/"SINGLE_SOURCE"
    """
    from datetime import datetime as _dt

    def _date_obj(s: str):
        try:
            return _dt.strptime(s, "%Y-%m-%d").date()
        except Exception:
            return None

    def _within_1day(d1: str, d2: str) -> bool:
        o1, o2 = _date_obj(d1), _date_obj(d2)
        if o1 is None or o2 is None:
            return False
        return abs((o1 - o2).days) <= 1

    def _match_key(m: dict):
        h, a = m.get("home", ""), m.get("away", "")
        return (min(h, a), max(h, a))

    # Index src_b by canonical pair → list of entries
    b_index: dict = {}
    for m in src_b:
        k = _match_key(m)
        b_index.setdefault(k, []).append(m)

    confirmed = []
    pending = []
    used_b = set()  # indices of src_b entries consumed

    for m_a in src_a:
        k = _match_key(m_a)
        candidates = b_index.get(k, [])

        # Find the best-matching src_b entry (±1 day)
        matched_b = None
        matched_b_idx = None
        for idx, m_b in enumerate(candidates):
            b_global_idx = id(m_b)
            if b_global_idx in used_b:
                continue
            if _within_1day(m_a["date"], m_b["date"]):
                matched_b = m_b
                matched_b_idx = b_global_idx
                break

        # Canonical home/away: use src_b orientation if available, else src_a
        home = matched_b["home"] if matched_b else m_a["home"]
        away = matched_b["away"] if matched_b else m_a["away"]
        date = matched_b["date"] if matched_b else m_a["date"]

        src_a_info = {"name": "odds-api", "hg": m_a["hg"], "ag": m_a["ag"]}

        if matched_b is None:
            # Only in src_a
            entry = {
                "date": date, "home": home, "away": away,
                "hg": m_a["hg"], "ag": m_a["ag"], "stage": _stage_for(date),
                "source_a": src_a_info, "source_b": None,
                "verdict": "SINGLE_SOURCE",
                "note": "仅 odds-api 有记录，martj42 尚未更新",
            }
            pending.append(entry)
            print(f"[results_sync] ⚠ 单源未确认: {home} {m_a['hg']}-{m_a['ag']} {away} ({date}) — 仅 odds-api")
        else:
            used_b.add(matched_b_idx)
            src_b_info = {"name": "martj42", "hg": matched_b["hg"], "ag": matched_b["ag"]}
            if m_a["hg"] == matched_b["hg"] and m_a["ag"] == matched_b["ag"]:
                entry = {
                    "date": date, "home": home, "away": away,
                    "hg": matched_b["hg"], "ag": matched_b["ag"], "stage": _stage_for(date),
                    "source_a": src_a_info, "source_b": src_b_info,
                    "verdict": "AGREE",
                    "note": "两源比分一致",
                }
                confirmed.append(entry)
            else:
                entry = {
                    "date": date, "home": home, "away": away,
                    "hg": matched_b["hg"], "ag": matched_b["ag"], "stage": _stage_for(date),
                    "source_a": src_a_info, "source_b": src_b_info,
                    "verdict": "CONFLICT",
                    "note": f"比分冲突: odds-api={m_a['hg']}-{m_a['ag']} vs martj42={matched_b['hg']}-{matched_b['ag']}",
                }
                pending.append(entry)
                print(f"[results_sync] ⚠ 待人工裁决: {home} vs {away} ({date}) — {entry['note']}")

    # src_b entries not matched by src_a
    used_b_ids = used_b
    for m_b in src_b:
        if id(m_b) in used_b_ids:
            continue
        k = _match_key(m_b)
        # Check if any src_a entry covers this (might have been used above)
        already_covered = False
        for m_a in src_a:
            if _match_key(m_a) == k and _within_1day(m_a["date"], m_b["date"]):
                already_covered = True
                break
        if already_covered:
            continue

        home, away, date = m_b["home"], m_b["away"], m_b["date"]
        entry = {
            "date": date, "home": home, "away": away,
            "hg": m_b["hg"], "ag": m_b["ag"], "stage": _stage_for(date),
            "source_a": None,
            "source_b": {"name": "martj42", "hg": m_b["hg"], "ag": m_b["ag"]},
            "verdict": "SINGLE_SOURCE",
            "note": "仅 martj42 有记录，odds-api 未返回",
        }
        pending.append(entry)
        print(f"[results_sync] ⚠ 单源未确认: {home} {m_b['hg']}-{m_b['ag']} {away} ({date}) — 仅 martj42")

    return confirmed, pending


def write_staging(confirmed: list, pending: list) -> None:
    """写入 data/results_staging.json"""
    from datetime import datetime as _dt, timezone as _tz
    staging = {
        "_created": _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "confirmed": confirmed,
        "pending": pending,
    }
    os.makedirs(os.path.dirname(STAGING_PATH) if os.path.dirname(STAGING_PATH) else ".", exist_ok=True)
    with open(STAGING_PATH, "w", encoding="utf-8") as f:
        json.dump(staging, f, ensure_ascii=False, indent=2)
    print(f"[results_sync] staging 已写入: {len(confirmed)} 条双源确认，{len(pending)} 条待裁决")


def read_staging() -> dict:
    """读取 staging，返回 {"confirmed":[], "pending":[], "_created":str}"""
    if not os.path.exists(STAGING_PATH):
        return {"confirmed": [], "pending": [], "_created": ""}
    with open(STAGING_PATH, encoding="utf-8") as f:
        return json.load(f)


def _elo_checksum(path: str) -> str:
    """SHA256 of elo_state.json content for consistency check."""
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class ReplayError(RuntimeError):
    """赛果已入库但 replay 失败 → elo_state 可能与赛果库不一致。调用方须以非0退出码收尾。"""


def commit_from_staging(auto_replay: bool = True) -> int:
    """
    1. 读 staging["confirmed"]
    2. 过滤已在 results.json 的记录（date+home+away 去重）
    3. 追加到 results.json，按 date 排序
    4. 如果 auto_replay: 调用 subprocess 运行 python3 update_elo.py --replay
    5. 自检：replay 前后对比 elo_state.json checksum（历史场次应与仅新增后一致）
       实现：replay前记录checksum，replay后比较——若历史队伍Elo有意外变化则WARN
    6. 返回新增条数
    """
    staging = read_staging()
    confirmed = staging.get("confirmed", [])

    if not confirmed:
        print("[results_sync] staging 中无已确认赛果")
        return 0

    # Load current results
    if not os.path.exists(RESULTS_PATH):
        existing_data = {"_note": "2026 WC 实际赛果。", "matches": []}
    else:
        with open(RESULTS_PATH, encoding="utf-8") as f:
            existing_data = json.load(f)

    current = existing_data.get("matches", [])
    seen = {(m["date"], m["home"], m["away"]) for m in current}

    # Check for in-db score conflicts before adding
    in_db_by_key = {(m["date"], m["home"], m["away"]): m for m in current}

    new_entries = []
    for r in confirmed:
        key = (r["date"], r["home"], r["away"])
        key_rev = (r["date"], r["away"], r["home"])

        if key in seen or key_rev in seen:
            # Check for score conflict
            existing_m = in_db_by_key.get(key) or in_db_by_key.get(key_rev)
            if existing_m:
                if existing_m["hg"] != r["hg"] or existing_m["ag"] != r["ag"]:
                    print(
                        f"[results_sync] ⚠ 已入库比分冲突，绝不覆盖: "
                        f"{r['home']} vs {r['away']} ({r['date']}) "
                        f"库中={existing_m['hg']}-{existing_m['ag']} "
                        f"staging={r['hg']}-{r['ag']}"
                    )
            continue

        # Strip internal staging metadata fields before storing
        clean = {
            "date": r["date"], "home": r["home"], "away": r["away"],
            "hg": r["hg"], "ag": r["ag"],
            "stage": r.get("stage") or _stage_for(r["date"]),
        }
        new_entries.append(clean)
        seen.add(key)

    if not new_entries:
        print("[results_sync] 无新赛果可入库（已全部存在）")
        return 0

    # Capture pre-replay elo checksum
    elo_path = "data/elo_state.json"
    pre_checksum = _elo_checksum(elo_path)
    pre_elo = {}
    if os.path.exists(elo_path):
        with open(elo_path, encoding="utf-8") as f:
            pre_elo = json.load(f)

    # Write to results.json
    current.extend(new_entries)
    current.sort(key=lambda x: x["date"])
    existing_data["matches"] = current
    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(existing_data, f, ensure_ascii=False, indent=2)
    print(f"[results_sync] 已入库 {len(new_entries)} 条赛果 → {RESULTS_PATH}")
    for e in new_entries:
        print(f"  {e['date']}  {e['home']} {e['hg']}-{e['ag']} {e['away']}")

    # Replay Elo
    replay_ok = True
    if auto_replay:
        print("[results_sync] 触发 replay ...")
        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        try:
            result = subprocess.run(
                [sys.executable, "update_elo.py", "--replay"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=120,
            )
            if result.returncode != 0:
                replay_ok = False
                print(f"[results_sync] ✗ replay 失败(退出码将非0):\n{result.stderr}")
            else:
                total = len(current)
                print(f"[results_sync] replay 完成 ({total} 场)")
        except subprocess.TimeoutExpired:
            replay_ok = False
            print("[results_sync] ✗ replay 超时(退出码将非0)")
        except Exception as e:
            replay_ok = False
            print(f"[results_sync] ✗ replay 异常(退出码将非0): {e}")

        # Consistency check: verify historical teams' Elo didn't change unexpectedly
        post_checksum = _elo_checksum(elo_path)
        if pre_checksum and pre_checksum == post_checksum:
            print("[results_sync] ⚠ Elo checksum 未变化（replay 可能未生效？）")
        elif os.path.exists(elo_path):
            with open(elo_path, encoding="utf-8") as f:
                post_elo = json.load(f)

            new_teams = {e["home"] for e in new_entries} | {e["away"] for e in new_entries}
            unexpected_changes = []
            for team, pre_val in pre_elo.items():
                post_val = post_elo.get(team)
                if post_val is None:
                    continue
                if team in new_teams:
                    continue  # Expected to change
                if abs(pre_val - post_val) > 0.1:
                    unexpected_changes.append(
                        f"{team}: {pre_val:.1f}→{post_val:.1f}"
                    )
            if unexpected_changes:
                print(
                    f"[results_sync] ⚠ Elo 轨迹自检 WARN: "
                    f"{len(unexpected_changes)} 支历史队伍Elo意外变化: "
                    + ", ".join(unexpected_changes[:5])
                )
            else:
                print(f"[results_sync] Elo 轨迹自检: ✓ ({len(current) - len(new_entries)} 场历史 checksum 一致)")

    # Append pending to PENDING_PATH — skip DATE_DRIFT_DUP (already in DB)
    pending = staging.get("pending", [])
    real_pending = [p for p in pending if p.get("verdict") != "DATE_DRIFT_DUP"]
    if real_pending:
        with open(PENDING_PATH, "a", encoding="utf-8") as f:
            for p in real_pending:
                f.write(json.dumps(p, ensure_ascii=False) + "\n")
        print(f"[results_sync] {len(real_pending)} 条待裁决记录已追加 → {PENDING_PATH}")

    if auto_replay and not replay_ok:
        raise ReplayError(
            f"{len(new_entries)} 条赛果已入库，但 replay 失败 → elo_state.json 可能与赛果库不一致；"
            f"请手动重跑 `python3 update_elo.py --replay` 并核对 checksum"
        )
    return len(new_entries)
