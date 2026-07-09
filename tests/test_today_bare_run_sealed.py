"""
回归测试 — 对抗审查 #13：today.py 裸跑（无 --auto）落到过期 MANUAL_MATCHES 必须硬拒。

病灶：裸跑 today.py 用封存快照 MANUAL_MATCHES 出正式 Edge/稳单推单，仅打印一行
警告不退出 → 模糊指令"跑今天"复现 2026-07-03 同款事故路径。
predict_market 侧已硬挡裸跑，today.py 这个孪生入口需对齐。
删掉 _sealed_manual_redirect 本文件即变红。
"""
import pytest


def test_today_bare_run_stale_sealed(capsys):
    import today
    with pytest.raises(SystemExit) as ei:
        today._sealed_manual_redirect(3)
    assert ei.value.code != 0, "过期封存快照裸跑必须以非0退出码收尾"
    out = capsys.readouterr().out
    assert "predict_market.py --auto-today" in out, "必须引导到当日唯一权威入口"
