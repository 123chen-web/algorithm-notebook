"""复习手感：普通详情、共用附加控件和专注模式的 Node 行为检查。"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_review_feel_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/review_feel_behaviour.cjs",
         "tests/review_extras_behaviour.cjs", "tests/review_focus_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-5000:] + result.stderr[-2000:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-2000:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 70, result.stdout[-2000:]
