"""错因专题静态契约及无网络、无临时目录的真实 JS 渲染检查。"""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@pytest.fixture(scope="module")
def assets():
    return {name: (STATIC / name).read_text(encoding="utf-8") for name in (
        "index.html", "clusters.js", "clusters.css", "app.js", "shell.js",
    )}


def test_versioned_assets_load_before_app(assets):
    html = assets["index.html"]
    assert re.search(r'<link rel="stylesheet" href="/static/clusters\.css\?v=\d+">', html)
    clusters = re.search(r'<script defer src="/static/clusters\.js\?v=\d+"></script>', html)
    app = re.search(r'<script defer src="/static/app\.js\?v=\d+"></script>', html)
    assert clusters and app and clusters.start() < app.start()


def test_page_and_navigation_ids_are_registered(assets):
    html = assets["index.html"]
    assert re.search(r'<section id="clusters-page"[^>]*aria-labelledby="clusters-title"[^>]*\bhidden\b', html)
    for name in (
        "clusters-title", "clusters-generate", "clusters-quota", "clusters-new",
        "clusters-status", "clusters-retry", "clusters-empty", "clusters-empty-title",
        "clusters-empty-text", "clusters-result",
    ):
        assert html.count(f'id="{name}"') == 1
    assert 'class="nav-item" data-view="clusters"' in html
    assert 'class="more-item" data-view="clusters"' in html
    sidebar = html.split('class="more-item"')[0]
    assert sidebar.index('data-view="mastery"') < sidebar.index('data-view="clusters"')


def test_page_status_is_live_and_generation_uses_own_guard(assets):
    html, script = assets["index.html"], assets["clusters.js"]
    status = re.search(r'<[^>]*id="clusters-status"[^>]*>', html)
    assert status and 'role="status"' in status[0] and 'aria-live="polite"' in status[0]
    assert 'setAttribute("aria-busy"' in script
    assert 'setAttribute("aria-disabled"' in script and 'dataset.blocked' in script
    assert not re.search(r'\.disabled\s*=', script)
    assert 'user.id === userId && generation === ticket' in script
    assert '归并相似错因 · 消耗 1 次 AI 额度' in script
    assert '重新归并 · 消耗 1 次 AI 额度' in script


def test_every_user_and_ai_field_uses_text_nodes(assets):
    script = assets["clusters.js"]
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script
    assert 'item.textContent = text' in script


def test_view_refresh_focus_and_logout_are_connected(assets):
    app = assets["app.js"]
    assert '$("#clusters-page").hidden = view !== "clusters";' in app
    assert 'else if (view === "clusters") await window.Clusters.load();' in app
    assert re.search(r'if \(view === "clusters"\) \{\s*message\(\);\s*await window\.Clusters\.load\(\);', app)
    focus = app[app.index('document.addEventListener("focus:closed"'):]
    assert 'view === "clusters"' in focus and 'window.Clusters.load()' in focus
    assert 'window.Clusters?.reset();' in assets["shell.js"]


def test_navigation_and_focus_use_existing_contracts(assets):
    script = assets["clusters.js"]
    assert 'new CustomEvent("app:navigate", { detail: { view: "all", recordId: member.mistake_id } })' in script
    assert 'window.FocusReview?.start({ ids })' in script
    assert 'cluster.members.filter(isDue)' in script
    assert 'document.addEventListener("app:data-changed"' in script


def test_styles_use_theme_tokens_and_accessible_touch_targets(assets):
    css = assets["clusters.css"]
    assert '!important' not in css
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', css)
    assert 'backdrop-filter' not in css
    assert 'outline: 3px solid var(--accent)' in css
    assert css.count('min-height: 44px') >= 4
    assert '@media (pointer: coarse)' in css and '@media (max-width: 599px)' in css
    assert 'var(--kai)' in css and 'var(--serif)' in css
    assert '@media (prefers-reduced-motion: no-preference)' in css
    assert css.index('@media (prefers-reduced-motion: no-preference)') < css.index('animation:')


def test_quota_uses_shared_profile_display_without_client_decrement(assets):
    script = assets["clusters.js"]
    assert '$("#home-quota-text")' in script
    assert 'updateUserInfo();' in script
    assert 'api("/api/me")' in script
    assert not re.search(r'ai_daily_remaining\s*(?:--|-=|=\s*user)', script)


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids[attrs["id"]] = attrs


@pytest.mark.parametrize("scenario", [
    "states", "ready", "no-focus", "future-only", "failure-quota", "quota-unavailable",
    "stale-load", "account-switch", "reset-generation", "load-error", "data-changed",
    "zero-clusters", "navigation-quota",
])
def test_real_page_rendering_and_async_guards(assets, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the JS rendering checks")
    parser = IdParser()
    parser.feed(assets["index.html"])
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_clusters_page_render.cjs"))],
        input=json.dumps({"source": assets["clusters.js"], "scenario": scenario, "ids": parser.ids}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=10, cwd=ROOT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
