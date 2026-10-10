"""N5 所见即所得编辑器接线行为（OYEditor 桩、保存取 Markdown、上传/联想/画板/选题回调、
两条降级路径、字数上限、编辑态挂载，以及待办清单/GFM 表格的静态渲染）。
Node 内置测试运行器 + tests/js_harness.cjs 的假 DOM。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_notes_editor_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/notes_editor_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-6000:] + result.stderr[-1500:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 15, result.stdout[-1500:]
