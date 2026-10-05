"""账号小部件的异步、校验与焦点行为（Node 内置测试运行器 + 假 DOM）。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_account_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/account_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-1500:]
    # 终端与捕获输出分别使用简洁报告和 TAP，两种都认。
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 35, result.stdout[-1500:]
