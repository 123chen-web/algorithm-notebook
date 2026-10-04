"""错因专题静态契约及无网络、无临时目录的真实 JS 渲染检查。"""
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_cursor_fx_assets import function_body
from test_app_session_assets import (
    review_session_support,
    test_stale_unauthorized_request_keeps_new_session,
    test_current_unauthorized_request_signs_out,
    test_api_keeps_success_and_error_behavior,
)


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@pytest.fixture(scope="module")
def assets():
    return {name: (STATIC / name).read_text(encoding="utf-8") for name in (
        "index.html", "clusters.js", "clusters.css", "app.js", "shell.js", "practice.js",
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
    controls = function_body(script, "renderControls")
    assert controls.index('button.dataset.blocked') < controls.index('button.disabled')
    assert 'button.disabled = $("#app").getAttribute("aria-busy") === "true" || blocked;' in controls
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
    assert 'window.Clusters?.reset();' in assets["shell.js"]


def test_navigation_and_focus_use_existing_contracts(assets):
    script = assets["clusters.js"]
    assert 'new CustomEvent("app:navigate", { detail: { view: "all", recordId: member.mistake_id } })' in script
    # 到期的成员由共用的 PracticeNow 挑出（到期日升序、最多 5 条），按钮再走 FocusReview.start({ ids })。
    assert 'window.PracticeNow.pickDueIds(cluster.members, { today: report.today })' in script
    practice = assets_practice()
    assert 'window.FocusReview.start({ ids: list.slice() })' in practice
    assert 'document.addEventListener("app:data-changed"' in script


def assets_practice():
    return (STATIC / "practice.js").read_text(encoding="utf-8")


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
        self.tags = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids[attrs["id"]] = attrs
            self.tags[attrs["id"]] = tag


@pytest.mark.parametrize("scenario", [
    "states", "ready", "no-focus", "future-only", "failure-quota", "quota-unavailable",
    "stale-load", "account-switch", "reset-generation", "load-error", "data-changed",
    "zero-clusters", "navigation-quota",
    "generation-global-busy", "deferred-refresh", "deferred-data-changed", "deferred-focus-closed",
    "focus-closed", "reset-refresh",
    "trend-rules", "trend-rules-bad", "trend-badges", "trend-concurrency", "trend-unavailable",
    "trend-stale", "practise-cap", "practise-stale",
])
def test_real_page_rendering_and_async_guards(assets, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the JS rendering checks")
    parser = IdParser()
    parser.feed(assets["index.html"])
    app = assets["app.js"]
    focus = re.search(r'^document\.addEventListener\("focus:closed",[\s\S]*?^\}\);', app, re.M)
    assert focus, "Missing focus:closed listener"
    app_behavior = "\n".join((
        review_session_support(app),
        f'function setBusy(value) {{{function_body(app, "setBusy")}\n}}',
        f'async function run(action) {{{function_body(app, "run")}\n}}',
        f'async function showView(nextView, {{ refreshUser = true }} = {{}}) {{{function_body(app, "showView")}\n}}',
        focus[0],
    ))
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_clusters_page_render.cjs"))],
        input=json.dumps({"source": assets["clusters.js"], "practice": assets["practice.js"], "scenario": scenario, "ids": parser.ids,
                          "tags": parser.tags, "appBehavior": app_behavior}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=10, cwd=ROOT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
