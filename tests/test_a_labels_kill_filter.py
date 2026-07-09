"""
回归测试 — 对抗审查 #4：[A推单] console 展示未过滤，绕过 kill 关卡。

病灶：run_auto_today 把 predict() 原始 portfolio 原样标成 [A推单] 打印，未经任何
kill；而 _kr(compute_1x2_kill_results) 已算好却没用于展示 → 一条 today.py 本会 KILL
的 1X2 方向被当"推单"呈现。低风险修复：诊断展示改名 + 套已算好的 1X2 kill。
删掉 _filter_a_labels_by_kill 本文件即变红。
"""


def test_filter_a_labels_hides_killed_1x2():
    from predict_market import _filter_a_labels_by_kill
    labels = ["主场胜 (Spain)", "Over 2.5", "AH -1.5 Spain"]
    kr = {
        "home_win": {"passed": False, "kill_reason": "近平场次1X2Win压制"},
        "draw": {"passed": True},
        "away_win": {"passed": True},
    }
    out = _filter_a_labels_by_kill(labels, kr)
    assert "主场胜 (Spain)" not in out, "被 kill 的 1X2 方向必须从诊断展示隐藏"
    assert "Over 2.5" in out, "OU 非 1X2，不受 1X2 kill 影响，透传"
    assert "AH -1.5 Spain" in out, "AH 非 1X2，透传"


def test_filter_a_labels_passthrough_when_passed():
    from predict_market import _filter_a_labels_by_kill
    labels = ["主场胜 (Spain)", "平局"]
    kr = {"home_win": {"passed": True}, "draw": {"passed": True}, "away_win": {"passed": True}}
    assert _filter_a_labels_by_kill(labels, kr) == labels, "全通过时不改动"


def test_filter_a_labels_none_kill_is_noop():
    from predict_market import _filter_a_labels_by_kill
    labels = ["主场胜 (Spain)", "Over 2.5"]
    assert _filter_a_labels_by_kill(labels, None) == labels, "无 kill_results 时原样透传"
