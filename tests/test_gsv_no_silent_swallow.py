"""
回归测试 — 对抗审查 #5：GSV 追踪器/OOS 计数不得静默吞异常。

删掉对应修复（把 except 改回 `pass`）这些测试即变红。
病灶：坏数据/IO 失败被静默丢弃 → 喂 UNLOCK_N=8 的 DC/平局解封裁决的样本外 N 被无声少计。
"""
import json


def test_count_oos_gsv_n_warns_on_corrupt_line(tmp_path, capsys):
    """今天推单入口的 OOS 计数遇到损坏行必须告警，不能静默少计。"""
    import today
    logf = tmp_path / "log.jsonl"
    logf.write_text(
        json.dumps({"date": "2026-07-05"}) + "\n"
        + "CORRUPT_NOT_JSON\n"
        + json.dumps({"date": "2026-07-06"}) + "\n",
        encoding="utf-8",
    )
    n = today._count_oos_gsv_n(log_path=logf)
    err = capsys.readouterr().err
    assert n == 2, "两条合法样本外记录应被计入"
    assert "WARN" in err, "损坏行必须显式告警，不得静默跳过"


def test_load_seen_keys_warns_on_corrupt_line(tmp_path, capsys, monkeypatch):
    """去重键加载遇损坏行应告警并跳过该行，保留其余合法键。"""
    from src.analysis import gsv_shadow_tracker as gst
    logf = tmp_path / "log.jsonl"
    logf.write_text(
        "CORRUPT_NOT_JSON\n"
        + json.dumps({"home": "A", "away": "B", "date": "2026-07-04"}) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gst, "DATA_FILE", logf)
    keys = gst._load_seen_keys()
    err = capsys.readouterr().err
    assert ("A", "B", "2026-07-04") in keys, "损坏行之后的合法键应仍被加载"
    assert "WARN" in err, "损坏行必须显式告警"


def test_log_gsv_match_warns_on_io_error(tmp_path, capsys, monkeypatch):
    """写盘失败必须告警（该场未计入 OOS 样本），不得静默丢弃。"""
    from src.analysis import gsv_shadow_tracker as gst
    bad = tmp_path / "missing_parent_dir" / "log.jsonl"  # 父目录不存在 → open 抛错
    monkeypatch.setattr(gst, "DATA_FILE", bad)
    monkeypatch.setattr(gst, "_SEEN_KEYS", set())  # 跳过加载分支
    gst.log_gsv_match({"home": "A", "away": "B", "date": "2026-07-05"})
    err = capsys.readouterr().err
    assert "WARN" in err, "写盘失败必须显式告警"
