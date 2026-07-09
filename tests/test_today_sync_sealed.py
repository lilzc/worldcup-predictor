"""
回归测试 — 对抗审查 #3：today.py --sync 单源直写旁路必须封死。

病灶：today.py --sync → results_sync.run() → sync_to_json() 单源 martj42 直写
wc2026_results.json，绕过双源交叉验证+staging+人工确认闸+replay。
删掉 _sealed_sync_redirect / 改回调用 sync_results 本文件即变红。
"""
import pytest


def test_today_sync_bypass_sealed(capsys):
    import today
    with pytest.raises(SystemExit) as ei:
        today._sealed_sync_redirect()
    assert ei.value.code != 0, "封死路径必须以非0退出码收尾"
    out = capsys.readouterr().out
    assert "已封" in out, "必须显式说明直写路径已封"
    assert "daily_sync" in out, "必须引导到带 staging 闸的正确管线"
