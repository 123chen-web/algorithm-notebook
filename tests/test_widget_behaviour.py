"""小部件的异步行为：请求晚回来、登出再登录、强制刷新、重复挂载（Node 内置测试运行器 + 假浏览器）。"""
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_widget_async_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/widget_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert "ℹ fail 0" in result.stdout
