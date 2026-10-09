"""画板宿主（static/draw-host.js）的 node 行为测试包装：postMessage 协议、
防抖自动保存、冲突/失败状态、markdown 画板引用渲染。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_draw_host_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/draw_host_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-1500:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 12, result.stdout[-1500:]
