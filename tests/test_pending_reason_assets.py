"""待补原因的独立模块、宿主接口、安全与主题对比度契约。"""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_contrast_tokens import THEMES, contrast_ratio, css_declarations


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
TEXT_PAIRS = (
    ("--ink", "--surface"), ("--ink-2", "--surface"),
    ("--danger", "--surface"), ("--ink", "--soft"),
    ("--on-accent", "--azurite"),
)


def test_pending_reason_resources_and_host_hooks_are_wired():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert '/static/pending-reason.css?v=1' in page
    assert '/static/pending-reason.js?v=1' in page
    assert page.index('/static/pending-reason.js?v=1') < page.index('/static/app.js?v=')
    renderer = app[app.index('function renderMistakeText(item)'):app.index('function renderProblemEditor(item)')]
    assert 'window.PendingReason?.render(item)' in renderer
    assert 'if (pendingReason) return pendingReason;' in renderer
    assert 'window.PendingReason?.configure({ ...rvfHooks, showView, openMistake,' in app
    assert 'getEpoch: () => sessionEpoch, getView: () => view' in app
    assert 'getDetailGeneration: () => rvfDetailGeneration' in app
    assert 'onSaved: pendingReasonSaved' in app
    assert 'revealed: Boolean(item.pending_reason) || window.ReviewExtras?.hideReason() === false' in app


def test_pending_reason_script_has_no_injection_or_network_bypass():
    source = (STATIC / "pending-reason.js").read_text(encoding="utf-8")
    for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', 'document.write',
                      'eval(', 'new Function', 'fetch(', 'XMLHttpRequest', '.style.',
                      'setAttribute("style"', 'localStorage'):
        assert forbidden not in source, forbidden
    assert 'textContent' in source
    assert 'hooks.api(' in source
    assert '"/api/tags/suggest"' in source
    assert '"/api/mistakes?due_only=false&pending_reason=1"' in source
    assert not re.search(r'https?://', source)
    assert len(source.encode("utf-8")) < 20 * 1024


def test_pending_reason_styles_use_checked_tokens_and_accessible_controls():
    source = (STATIC / "pending-reason.css").read_text(encoding="utf-8")
    assert '!important' not in source
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(', source)
    assert 'outline: 3px solid var(--accent)' in source
    assert source.count('min-height: 44px') >= 3
    assert 'animation' not in source and 'transition' not in source
    foregrounds = {foreground for foreground, _ in TEXT_PAIRS}
    backgrounds = {background for _, background in TEXT_PAIRS}
    used = set()
    for _, declaration in css_declarations(source):
        name, _, value = declaration.partition(':')
        name, value = name.strip(), value.strip()
        if name not in ('color', 'background', 'background-color'):
            continue
        assert re.fullmatch(r'var\(--[\w-]+\)', value), declaration
        token = re.search(r'var\((--[\w-]+)\)', value).group(1)
        assert token in (foregrounds if name == 'color' else backgrounds), declaration
        used.add(token)
    assert used == foregrounds | backgrounds


@pytest.mark.parametrize("context,tokens", list(THEMES.items()), ids=[str(context) for context in THEMES])
@pytest.mark.parametrize("foreground,background", TEXT_PAIRS)
def test_pending_reason_contrast(context, tokens, foreground, background):
    ratio = contrast_ratio(tokens[foreground], tokens[background])
    assert ratio >= 4.5, f"{context}: {foreground}/{background}={ratio:.6f}:1"


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js")
def test_pending_reason_async_behaviour():
    result = subprocess.run(
        ["node", "--test", "tests/pending_reason_behaviour.cjs"], cwd=ROOT,
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert re.search(r'(?m)^(?:ℹ|#) fail 0$', result.stdout), result.stdout[-1500:]
    assert re.search(r'(?m)^(?:ℹ|#) pass 26$', result.stdout), result.stdout[-1500:]
