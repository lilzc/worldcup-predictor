"""
回归测试 — 对抗审查 #12：commit+replay 失败不得吞退出码。

病灶：replay 子进程失败时只 print、commit_from_staging 仍返回成功计数、
daily_sync.main 从不 sys.exit 非0 → 赛果已写但 elo_state 陈旧，命令却 exit 0。
删掉修复（把 raise ReplayError 去掉）本文件即变红。
"""
import json
import types

import pytest


def _staging_one():
    return {
        "confirmed": [{"date": "2026-07-08", "home": "A", "away": "B", "hg": 1, "ag": 0}],
        "pending": [],
    }


def test_commit_raises_on_replay_failure(tmp_path, monkeypatch):
    from src.data import results_sync as rs
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps({"matches": []}), encoding="utf-8")
    monkeypatch.setattr(rs, "RESULTS_PATH", str(results_file))
    monkeypatch.setattr(rs, "read_staging", _staging_one)
    # 强制 replay 子进程返回非0
    monkeypatch.setattr(rs.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=1, stderr="boom", stdout=""))

    with pytest.raises(rs.ReplayError):
        rs.commit_from_staging(auto_replay=True)

    # 赛果仍已入库（写入不回滚，只是 elo 可能陈旧）
    data = json.loads(results_file.read_text(encoding="utf-8"))
    assert any(m["home"] == "A" for m in data["matches"]), "赛果应已写入 results.json"


def test_commit_no_raise_on_replay_success(tmp_path, monkeypatch):
    from src.data import results_sync as rs
    results_file = tmp_path / "results.json"
    results_file.write_text(json.dumps({"matches": []}), encoding="utf-8")
    monkeypatch.setattr(rs, "RESULTS_PATH", str(results_file))
    monkeypatch.setattr(rs, "read_staging", _staging_one)
    monkeypatch.setattr(rs.subprocess, "run",
                        lambda *a, **k: types.SimpleNamespace(returncode=0, stderr="", stdout=""))

    n = rs.commit_from_staging(auto_replay=True)
    assert n == 1, "replay 成功时正常返回入库条数"
