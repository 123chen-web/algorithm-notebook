"""讨论区主页（static/board.js）的行为：Node 内置测试运行器 + 假浏览器，不启动后端，不创建临时目录。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_board_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/board_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-1500:]
    # 终端里是 "ℹ fail 0"，管道里（CI）是 TAP 的 "# fail 0"；两种都认，并确认测试真的跑了。
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 60, result.stdout[-1500:]
