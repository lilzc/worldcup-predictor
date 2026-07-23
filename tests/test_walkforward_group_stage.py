"""
回归测试 — walkforward 生产路径 group_stage 罚分接线补全（2026-07-23 follow-up）。

病灶：walkforward._build_mat_custom 的 production 分支调用 apply_all 时漏传 is_group_stage，
取默认 True → 淘汰赛场次被误施 group_stage_volatility 罚分（legacy 分支和 predict.py 均已正确接线）。
锁定：production 分支按 match_date 派生的 _is_group 传入 apply_all（淘汰赛=False，小组赛/None=True）。
删掉 walkforward.py:388 的 is_group_stage=_is_group 本文件即变红。
"""
import io
import contextlib


def _capture_is_group(match_date):
    """spy walkforward.apply_all 捕获 production 分支传入的 is_group_stage（确定性，不依赖 Elo 数值）。

    强制走 production 分支（WF_GSV_MODE 默认），传入的 he/ae 触发强队占优以驱动罚分路径。
    """
    import walkforward as wf
    captured = {}
    real = wf.apply_all

    def spy(*args, **kwargs):
        captured["is_group_stage"] = kwargs.get("is_group_stage")
        return real(*args, **kwargs)

    wf.apply_all = spy
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            # he>1850 且占优，确保 group_stage_volatility 罚分路径可达
            wf._build_mat_custom("Spain", "Curacao", 1950.0, 1600.0, {},
                                 match_date=match_date)
    finally:
        wf.apply_all = real
    return captured.get("is_group_stage")


def test_knockout_date_is_not_group_stage():
    assert _capture_is_group("2026-07-10") is False, \
        "淘汰赛日期 production 分支必须 is_group_stage=False（不施罚分）"


def test_group_date_is_group_stage():
    assert _capture_is_group("2026-06-20") is True, \
        "小组赛日期 production 分支必须 is_group_stage=True（保留罚分）"


def test_knockout_start_boundary():
    assert _capture_is_group("2026-06-28") is False, "KNOCKOUT_START 当天算淘汰赛"
    assert _capture_is_group("2026-06-27") is True, "KNOCKOUT_START 前一天算小组赛"


def test_none_date_defaults_to_group_stage():
    assert _capture_is_group(None) is True, \
        "date 缺失→保守按小组赛（不改 ad-hoc 调用行为）"


def test_knockout_removes_penalty_direction():
    """端到端方向：production 分支强队在淘汰赛日期的胜率 >= 小组赛日期（罚分被移除）。"""
    import walkforward as wf

    def hw(md):
        with contextlib.redirect_stdout(io.StringIO()):
            _mat, probs, _diff, _gsv = wf._build_mat_custom(
                "Spain", "Curacao", 1950.0, 1600.0, {}, match_date=md)
        return probs["home_win"]

    hw_knockout = hw("2026-07-10")
    hw_group = hw("2026-06-20")
    assert hw_knockout >= hw_group, \
        "淘汰赛强队胜率不应低于小组赛（production 分支罚分已移除）"
    # 罚分确有作用面：淘汰赛严格高于小组赛，证明本 fix 非空转（在此 Elo 配置下）
    assert hw_knockout > hw_group, \
        "he=1950 占优应触发罚分差，淘汰赛胜率应严格高于小组赛（否则罚分未接通）"
