"""
回归测试 — 对抗审查 #11：db_health 不得"假绿"。

覆盖三处（M5 mtime 弱代理需另立 spec，不在本批）：
- M4 check_duplicates 主客翻转重复漏检（113→85 去重事故同类）
- M6 check_missing_results 权威源缺失时判绿（最需要时反绿灯）
- M2 check_odds_history_coverage 恒 return ok:True（0 覆盖=key/格式断裂也绿）
删掉对应修复本文件即变红。
"""


def test_duplicates_detects_home_away_flip():
    from src.analysis import db_health as dh
    matches = [
        {"date": "2026-07-08", "home": "A", "away": "B", "hg": 1, "ag": 0},
        {"date": "2026-07-08", "home": "B", "away": "A", "hg": 0, "ag": 1},  # 主客翻转重复
    ]
    r = dh.check_duplicates(matches)
    assert r["ok"] is False, "主客翻转的同场重复必须被判为重复"


def test_duplicates_passes_on_clean_db():
    from src.analysis import db_health as dh
    matches = [
        {"date": "2026-07-08", "home": "A", "away": "B"},
        {"date": "2026-07-09", "home": "C", "away": "D"},
    ]
    assert dh.check_duplicates(matches)["ok"] is True


def test_missing_results_cache_absent_is_not_green(tmp_path, monkeypatch):
    from src.analysis import db_health as dh
    monkeypatch.setattr(dh, "MARTJ42_CSV_PATH", str(tmp_path / "nonexistent.csv"))
    r = dh.check_missing_results([{"date": "2026-07-08", "home": "A", "away": "B"}])
    assert r["ok"] is False, "权威赛程源缺失时无法核对漏录，应保守判失败而非跳过判绿"


def test_odds_coverage_zero_is_not_green(tmp_path, monkeypatch):
    from src.analysis import db_health as dh
    oh = tmp_path / "odds_history.jsonl"
    oh.write_text('{"matches": [{"home": "X", "away": "Y"}]}\n', encoding="utf-8")
    monkeypatch.setattr(dh, "ODDS_HISTORY", str(oh))
    # results 里的场次与 odds_history 零交集 → 0 覆盖（指向 key/格式断裂）
    r = dh.check_odds_history_coverage([{"home": "A", "away": "B"}])
    assert r["ok"] is False, "odds_history 非空但零覆盖应判失败（系统性 key/格式断裂信号）"


def test_odds_coverage_partial_stays_green(tmp_path, monkeypatch):
    from src.analysis import db_health as dh
    oh = tmp_path / "odds_history.jsonl"
    oh.write_text('{"matches": [{"home": "A", "away": "B"}]}\n', encoding="utf-8")
    monkeypatch.setattr(dh, "ODDS_HISTORY", str(oh))
    r = dh.check_odds_history_coverage([{"home": "A", "away": "B"}, {"home": "C", "away": "D"}])
    assert r["ok"] is True, "部分覆盖（含历史无盘口场次）不应误判失败"
