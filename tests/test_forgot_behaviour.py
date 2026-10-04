"""找回密码表单的提示、60 秒倒计时与迟到响应守卫（Node 内置测试运行器 + 假浏览器）。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_forgot_form_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/forgot_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-1500:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 6, result.stdout[-1500:]
