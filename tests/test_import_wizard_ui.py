"""导入向导静态边界与 Node 异步行为验证。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_import_ui_uses_text_tokens_and_valid_host_integration():
    script = (ROOT / "static/import-wizard.js").read_text(encoding="utf-8")
    css = (ROOT / "static/import-wizard.css").read_text(encoding="utf-8")
    html = (ROOT / "static/index.html").read_text(encoding="utf-8")
    app = (ROOT / "static/app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in script
    assert not re.search(r"\beval\s*\(|\bfetch\s*\(|\.style\b", script)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|!important", css)
    assert '/static/import-wizard.js?v=1' in html
    assert '/static/import-wizard.css?v=1' in html
    assert 'id="list-import"' in html
    assert 'window.ImportWizard?.reset();' in app
    assert 'window.ImportWizard?.configure(' in app


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js")
def test_import_wizard_async_behaviour():
    result = subprocess.run(["node", "--test", "tests/import_wizard_behaviour.cjs"],
                            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-1000:]
    assert re.search(r"(?m)^(?:ℹ|#) pass 11$", result.stdout), result.stdout
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout
