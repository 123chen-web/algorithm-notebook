"""Isolated runner UI contracts: no browser execution, no external fetch, and text output."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def test_runner_assets_are_versioned_before_app():
    source = (STATIC / "index.html").read_text(encoding="utf-8")
    assert source.count('/static/code-runner.css?v=1') == 1
    assert source.count('/static/code-runner.js?v=1') == 1
    assert source.index('/static/code-runner.js?v=1') < source.index('/static/app.js?v=')


def test_runner_uses_only_host_api_and_plain_text():
    source = (STATIC / "code-runner.js").read_text(encoding="utf-8")
    assert len(source.encode("utf-8")) <= 16000
    assert "hooks.api(" in source and "textContent" in source
    for forbidden in ("fetch(", "XMLHttpRequest", "innerHTML", "outerHTML", "insertAdjacentHTML",
                      "eval(", "new Function", "iframe", "http://", "https://", 'setAttribute("style"'):
        assert forbidden not in source, forbidden


def test_runner_styles_use_tokens_and_accessible_focus():
    source = (STATIC / "code-runner.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|!important|rgba?\(", source)
    assert "var(--ink)" in source and "var(--surface)" in source
    assert "min-height: 44px" in source and ":focus-visible" in source
    assert "min-width: 0" in source and "overflow-wrap: anywhere" in source


def test_runner_mount_and_cleanup_follow_review_lifecycle():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "window.CodeRunner?.unmount();" in source
    assert "window.CodeRunner?.reset();" in source
    assert "window.CodeRunner?.mount(runnerHost, item, { offline });" in source
    assert "state.answers.push(runnerHost);" in source
    assert "window.CodeRunner?.configure({ ...rvfHooks," in source


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_runner_behaviour_in_fake_browser():
    result = subprocess.run(["node", "--test", "tests/code_runner_behaviour.cjs"], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stdout[-5000:] + result.stderr[-1500:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout)
    count = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert count and int(count.group(1)) >= 30
