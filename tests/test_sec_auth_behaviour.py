"""认证表单的真实 JS 行为回归，Node 假浏览器，无临时目录。"""
import re
import shutil
import subprocess
from pathlib import Path
import pytest

@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js")
def test_sec_auth_behaviour_in_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/sec_auth_behaviour.cjs"],
        cwd=Path(__file__).resolve().parents[1], capture_output=True,
        text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-1500:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 20, result.stdout[-1500:]
