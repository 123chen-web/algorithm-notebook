"""复习=重做：redo-flow.js 纯逻辑的 Node 行为检查。"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_redo_flow_behaviour_in_node():
    result = subprocess.run(
        ["node", "--test", "tests/redo_flow_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-5000:] + result.stderr[-2000:]
    assert re.search(r"(?m)^ℹ fail 0$", result.stdout), result.stdout[-2000:]
    passed = re.search(r"(?m)^ℹ pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 10, result.stdout[-2000:]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_review_redo_behaviour_in_a_fake_browser():
    """复习详情里的三种复习方式：生产函数在 fake browser 里跑，只替换 API I/O。"""
    result = subprocess.run(
        ["node", "--test", "tests/review_redo_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout[-5000:] + result.stderr[-2000:]
    assert re.search(r"(?m)^ℹ fail 0$", result.stdout), result.stdout[-2000:]
    passed = re.search(r"(?m)^ℹ pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 8, result.stdout[-2000:]
