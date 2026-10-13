"""站内页面切换对应浏览器历史（后退/前进）：行为测试（Node 内置测试运行器 + 假浏览器）+ 资源接入检查。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from static_helpers import versioned_src

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_view_history_behaviour():
    result = subprocess.run(
        ["node", "--test", "tests/view_history_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1500:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]


def test_script_is_loaded_right_after_app_js():
    scripts = re.findall(r'<script defer src="(/static/[^"]+)"', INDEX)
    app = next(i for i, src in enumerate(scripts) if src.startswith("/static/app.js"))
    assert scripts[app + 1] == versioned_src("view-history.js")


def test_script_uses_only_safe_apis():
    source = (ROOT / "static" / "view-history.js").read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "eval(", "localStorage", "sessionStorage"):
        assert forbidden not in source
    # 只处理带 oyView 的历史格，画板用的 oyDraw 不能被误响应。
    assert "oyView" in source and "oyDraw" not in source.split("*/", 1)[1]
