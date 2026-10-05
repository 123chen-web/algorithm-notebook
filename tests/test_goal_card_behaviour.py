"""总览页「目标」卡的异步行为：渲染、迟到响应丢弃、表单校验、二次确认删除
（Node 内置测试运行器 + js_harness 假浏览器）。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_goal_card_async_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/goal_card_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    # 终端里是 "ℹ fail 0"，管道里（CI）是 TAP 的 "# fail 0"；两种都认，并确认测试真的跑了。
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 37, result.stdout[-1500:]
