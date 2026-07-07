"""
daily_sync.py — WC2026 模型每日数据管线

用法：
  python3 daily_sync.py                    # 跑四步，赛果只到 staging
  python3 daily_sync.py --commit-results   # 从 staging 确认入库 + replay
  python3 daily_sync.py --skip-news        # 跳过新闻（不想烧搜索额度时）
  python3 daily_sync.py --health-only      # 只跑体检
"""
import sys
import os
import json
import argparse
from datetime import datetime, timezone

# Ensure project root is on sys.path
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

DIVIDER = "=" * 52
THIN    = "─" * 52


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _ts_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Step 1: DB Health ─────────────────────────────────────────────────────────

def step_health(verbose: bool = True) -> dict:
    if verbose:
        print("\n[1/4] 数据库完整性体检")
    from src.analysis.db_health import run_all_checks, print_report
    result = run_all_checks()
    if verbose:
        # Indent the report
        import io, contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            print_report(result)
        for line in buf.getvalue().splitlines():
            print("  " + line)
    return result


def _tag_date_drift_dups(pending: list, db_matches: list) -> None:
    """
    In-place: SINGLE_SOURCE pending entries whose team pair + score already exist
    in DB with date ±1 day are tagged DATE_DRIFT_DUP (odds-api +1d drift artifact).
    These are archived silently — not shown as real conflicts, not written to
    pending_results.jsonl.
    """
    from datetime import datetime as _dt
    db_idx: dict = {}
    for m in db_matches:
        key = (min(m["home"], m["away"]), max(m["home"], m["away"]))
        db_idx.setdefault(key, []).append(m)

    for p in pending:
        if p.get("verdict") != "SINGLE_SOURCE":
            continue
        key = (min(p["home"], p["away"]), max(p["home"], p["away"]))
        for db_m in db_idx.get(key, []):
            same_home = p["home"] == db_m["home"]
            p_hg = p["hg"] if same_home else p["ag"]
            p_ag = p["ag"] if same_home else p["hg"]
            if db_m["hg"] == p_hg and db_m["ag"] == p_ag:
                try:
                    d_p  = _dt.strptime(p["date"],    "%Y-%m-%d").date()
                    d_db = _dt.strptime(db_m["date"], "%Y-%m-%d").date()
                    if abs((d_p - d_db).days) == 1:
                        p["verdict"] = "DATE_DRIFT_DUP"
                        p["note"] = f"已在DB(date={db_m['date']}),日期漂移归档"
                        break
                except Exception:
                    pass


# ── Step 2: 赛果同步 ─────────────────────────────────────────────────────────

def step_results_sync() -> dict:
    print("\n[2/4] 赛果同步")
    from src.data.results_sync import (
        fetch_wc2026_results,
        fetch_scores_from_api,
        cross_validate,
        write_staging,
        read_staging,
    )
    import json as _json
    import os as _os

    RESULTS_PATH = "data/wc2026_results.json"

    # Load already-in-db keys
    in_db = set()
    if _os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, encoding="utf-8") as f:
            for m in _json.load(f).get("matches", []):
                in_db.add((m["date"], m["home"], m["away"]))
                in_db.add((m["date"], m["away"], m["home"]))

    # Source A: odds-api scores
    print("  拉取源A (odds-api scores) ...")
    src_a = fetch_scores_from_api(days_from=3)
    # Filter to new matches only
    src_a_new = [m for m in src_a if (m["date"], m["home"], m["away"]) not in in_db
                                   and (m["date"], m["away"], m["home"]) not in in_db]
    if src_a:
        print(f"  源A (odds-api scores): 拉取 {len(src_a)} 场已完赛，其中 {len(src_a_new)} 场新增 ✓")
    else:
        print("  源A (odds-api scores): 无数据或 API key 未配置")

    # Source B: martj42 CSV
    print("  拉取源B (martj42 CSV) ...")
    try:
        src_b_all, latest_date = fetch_wc2026_results()
        src_b_new = [m for m in src_b_all if (m["date"], m["home"], m["away"]) not in in_db
                                            and (m["date"], m["away"], m["home"]) not in in_db]
        print(f"  源B (martj42 CSV): 拉取 {len(src_b_all)} 场 WC2026，其中 {len(src_b_new)} 场新增 ✓")
    except Exception as e:
        print(f"  源B (martj42 CSV): 拉取失败 — {e}")
        src_b_new = []
        src_b_all = []

    # Load full DB records for drift-dup detection
    db_matches_full = []
    if _os.path.exists(RESULTS_PATH):
        with open(RESULTS_PATH, encoding="utf-8") as f:
            db_matches_full = _json.load(f).get("matches", [])

    # Cross-validate new matches only
    if not src_a_new and not src_b_new:
        print("  无新赛果需要处理")
        write_staging([], [])
        return {"confirmed": 0, "pending": 0}

    print("  交叉验证:")
    confirmed, pending = cross_validate(src_a_new, src_b_new)
    _tag_date_drift_dups(pending, db_matches_full)

    # Print cross-validation results
    for c in confirmed:
        print(f"    ✓ {c['home']} {c['hg']}-{c['ag']} {c['away']}  两源一致 → staging")
    for p in pending:
        verdict_label = "仅单源" if p["verdict"] == "SINGLE_SOURCE" else "比分冲突"
        src_label = ""
        if p["source_a"] and not p["source_b"]:
            src_label = "(odds-api)"
        elif p["source_b"] and not p["source_a"]:
            src_label = "(martj42)"
        print(f"    ⚠ {p['home']} {p['hg']}-{p['ag']} {p['away']}  {verdict_label} {src_label} → pending")

    write_staging(confirmed, pending)

    # ── 增强版确认表 ──────────────────────────────────────────────────────────
    W = 66
    def _hline(c="─"): print("  ┌" + c * W + "┐")
    def _hline_mid(c="─"): print("  ├" + c * W + "┤")
    def _hline_bot(c="─"): print("  └" + c * W + "┘")
    def _row(s): print(f"  │ {s:<{W-1}}│")

    if confirmed:
        print(f"\n  待入库赛果 — {len(confirmed)} 场双源确认，可直接入库:")
        _hline()
        for i, c in enumerate(confirmed):
            if i > 0:
                _hline_mid()
            sa = c.get("source_a") or {}
            sb = c.get("source_b") or {}
            sa_score = f"{sa['hg']}-{sa['ag']}" if sa else "N/A"
            sb_score = f"{sb['hg']}-{sb['ag']}" if sb else "N/A"
            # 口径判断：都是 FT 90min（小组赛），淘汰赛另行标注
            stage = c.get("stage", "Group")
            if stage == "Group":
                oral = "90min FT (小组赛，无加时)"
            else:
                oral = f"⚠ {stage} — 请确认是否为90min比分"
            _row(f"场次:  {c['home']} vs {c['away']}   ({c['date']})")
            _row(f"源A odds-api : {sa_score:<8}  源B martj42 : {sb_score:<8}  → {oral}")
            _row(f"建议入库值   : {c['hg']}-{c['ag']}  [{stage}]  ← 两源一致，直接确认")
        _hline_bot()
        print("  → 确认无误请执行: python3 daily_sync.py --commit-results")

    real_pending  = [p for p in pending if p.get("verdict") != "DATE_DRIFT_DUP"]
    drift_dups    = [p for p in pending if p.get("verdict") == "DATE_DRIFT_DUP"]

    if real_pending:
        print(f"\n  待人工裁决 — {len(real_pending)} 条冲突/单源，不自动入库:")
        _hline()
        for i, p in enumerate(real_pending):
            if i > 0:
                _hline_mid()
            sa = p.get("source_a") or {}
            sb = p.get("source_b") or {}
            sa_score = f"{sa['hg']}-{sa['ag']}" if sa else "—"
            sb_score = f"{sb['hg']}-{sb['ag']}" if sb else "—"
            _row(f"场次: {p['home']} vs {p['away']}  ({p['date']})")
            _row(f"源A odds-api : {sa_score:<10}  源B martj42 : {sb_score:<10}  verdict={p['verdict']}")
            if p["verdict"] == "CONFLICT":
                sa_obj = p.get("source_a") or {}
                sb_obj = p.get("source_b") or {}
                is_maybe_aet = (sa_obj and sb_obj and
                                sa_obj["hg"] != sb_obj["hg"] and
                                (sa_obj["hg"] == sa_obj["ag"] or sb_obj["hg"] == sb_obj["ag"]))
                if is_maybe_aet:
                    _row("⚠ 可能口径差异: 一源为90min平局，另一源为加时后总比分")
                    _row("建议: 人工核对，以90min比分手动填入后运行 --commit-results")
                else:
                    _row(f"冲突原因: {p.get('note','比分不一致')}")
                    _row("建议: 核实比赛录像/官方比分，确认后手动修改 staging 再 --commit-results")
            elif p["verdict"] == "SINGLE_SOURCE":
                src_name = "odds-api" if sa else "martj42"
                _row(f"单源: 仅有 {src_name} 数据，等待第二源或人工确认")
                _row(f"建议入库值(单源参考): {p['hg']}-{p['ag']}  — 请人工核实后入库")
        _hline_bot()
        print(f"  冲突记录已写入 data/pending_results.jsonl")

    if drift_dups:
        print(f"\n  日期漂移归档 — {len(drift_dups)} 条(已在DB，odds-api +1日漂移，自动跳过):")
        for d in drift_dups:
            print(f"    ✓ {d['home']} {d['hg']}-{d['ag']} {d['away']}  {d['note']}")

    return {"confirmed": len(confirmed), "pending": len(pending)}


# ── Step 2b: --commit-results ─────────────────────────────────────────────────

def step_commit_results() -> int:
    print("\n[commit] 从 staging 入库赛果 ...")
    from src.data.results_sync import read_staging, commit_from_staging

    staging = read_staging()
    confirmed = staging.get("confirmed", [])
    if not confirmed:
        print("[commit] staging 中无已确认赛果，请先运行 daily_sync.py 生成 staging")
        return 0

    print(f"[commit] 将入库 {len(confirmed)} 条赛果:")
    for c in confirmed:
        print(f"  {c['home']} {c['hg']}-{c['ag']} {c['away']} ({c['date']})")

    n = commit_from_staging(auto_replay=True)
    return n


# ── Step 3: 今日赛程/盘口 ─────────────────────────────────────────────────────

def step_odds() -> dict:
    print("\n[3/4] 今日赛程 / 盘口")
    try:
        from src.data.odds_api import get_todays_matches
        matches = get_todays_matches()
        if matches:
            print(f"  今日盘口: {len(matches)} 场")
            for m in matches:
                home = m.get("home", "?")
                away = m.get("away", "?")
                ct   = m.get("commence_time", "")
                print(f"    {home} vs {away}  (开赛: {ct[:16] if ct else '?'})")
        else:
            print("  今日无开赛场次（或 API key 未配置）")
        return {"n_matches": len(matches)}
    except Exception as e:
        print(f"  ⚠ 盘口拉取失败: {e}")
        return {"n_matches": 0}


# ── Step 4: 汇总 ──────────────────────────────────────────────────────────────

def step_summary(health: dict, results: dict, odds: dict, quota_remaining: str = "?") -> None:
    print("\n[4/4] 汇总")
    print("  新闻模块：手动运行 python3 -m src.data.auto_news <home> <away>")
    print()
    health_status = "OK" if health.get("ok") else "WARN"
    n_confirmed = results.get("confirmed", 0)
    n_matches   = odds.get("n_matches", 0)
    print(f"  配额剩余: {quota_remaining}  |  体检: {health_status}  |  "
          f"新赛果: {n_confirmed} 条待确认  |  盘口: {n_matches} 场")
    print(f"  时间戳: {_ts_iso()}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="WC2026 每日数据管线")
    parser.add_argument("--commit-results", action="store_true",
                        help="从 staging 确认入库 + 触发 replay")
    parser.add_argument("--skip-news", action="store_true",
                        help="跳过新闻步骤（省 API 额度）")
    parser.add_argument("--health-only", action="store_true",
                        help="只跑数据库体检")
    args = parser.parse_args()

    print(DIVIDER)
    print(f"  daily_sync.py  {_utc_now()}")
    print(DIVIDER)

    # --health-only: short circuit
    if args.health_only:
        health = step_health(verbose=True)
        print()
        return

    # --commit-results: commit then run full pipeline
    if args.commit_results:
        n = step_commit_results()
        print()
        # After commit, still run health check and show status
        health = step_health(verbose=True)
        odds   = step_odds()
        step_summary(health, {"confirmed": 0}, odds)
        return

    # Normal flow: health → sync → odds → summary
    health  = step_health(verbose=True)
    results = step_results_sync()

    if not args.skip_news:
        odds = step_odds()
    else:
        print("\n[3/4] 盘口（--skip-news，跳过）")
        odds = {"n_matches": 0}

    step_summary(health, results, odds)
    print()


if __name__ == "__main__":
    main()
