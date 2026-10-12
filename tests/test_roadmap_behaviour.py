"""学习路线前端的异步行为：三态切换、失败回滚、迟到响应丢弃、例题勾选、筛选
（Node 内置测试运行器 + js_harness 假浏览器）。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_roadmap_async_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/roadmap_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-2000:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 15, result.stdout[-2000:]
