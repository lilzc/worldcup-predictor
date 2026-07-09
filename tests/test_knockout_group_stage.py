"""
回归测试 — #1 淘汰赛误施小组赛波动罚分修复。

病灶：is_group_stage 全仓库无一处传 False → 淘汰赛强队被误施 group_stage_volatility 罚分。
锁定：predict() 按 match_date 派生 is_group_stage（淘汰赛=False，小组赛/None=True）。
删掉 predict.py 的 _is_group 派生本文件即变红。
"""
import io
import contextlib


def _capture_is_group(match_date):
    """spy apply_all 捕获 predict() 传入的 is_group_stage（不依赖 Elo 数值，确定性）。"""
    import predict as pm
    captured = {}
    real = pm.apply_all

    def spy(*args, **kwargs):
        captured["is_group_stage"] = kwargs.get("is_group_stage")
        return real(*args, **kwargs)

    pm.apply_all = spy
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            pm.predict("Spain", "Curacao", match_date=match_date)
    finally:
        pm.apply_all = real
    return captured.get("is_group_stage")


def test_knockout_date_is_not_group_stage():
    assert _capture_is_group("2026-07-10") is False, "淘汰赛日期必须 is_group_stage=False（不施罚分）"


def test_group_date_is_group_stage():
    assert _capture_is_group("2026-06-20") is True, "小组赛日期必须 is_group_stage=True（保留罚分）"


def test_knockout_start_boundary():
    # 边界：06-28 当天即淘汰赛（>=KNOCKOUT_START）
    assert _capture_is_group("2026-06-28") is False, "KNOCKOUT_START 当天算淘汰赛"
    assert _capture_is_group("2026-06-27") is True, "KNOCKOUT_START 前一天算小组赛"


def test_none_date_defaults_to_group_stage():
    assert _capture_is_group(None) is True, "date 缺失→保守按小组赛（不改 ad-hoc 调用行为）"


def test_knockout_removes_penalty_direction():
    """端到端方向：强队在淘汰赛日期的胜率 >= 小组赛日期（罚分被移除）。"""
    import predict as pm

    def hw(md):
        with contextlib.redirect_stdout(io.StringIO()):
            r = pm.predict("Spain", "Curacao", match_date=md)
        return r["home_win"]

    assert hw("2026-07-10") >= hw("2026-06-20"), "淘汰赛强队胜率不应低于小组赛（罚分已移除）"
