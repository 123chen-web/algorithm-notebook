"""AN：掌握度页的等级 / 排序 / 选择与"现在就练 5 条"共用模块的行为（Node 内置测试运行器 + 假浏览器）。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_mastery_overview_and_practice_now_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/an_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-1500:]
    # 终端里是 "ℹ fail 0"，管道里（CI）是 TAP 的 "# fail 0"；两种都认，并确认测试真的跑了。
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 18, result.stdout[-1500:]
