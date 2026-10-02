"""Ctrl K 命令面板的静态契约：引用、无障碍、输入法安全、竞态保护、样式约束。"""
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def index_html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script():
    return (STATIC / "palette.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stylesheet():
    return (STATIC / "palette.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_assets_are_versioned_and_script_loads_before_the_app(index_html):
    assert re.search(r'<link rel="stylesheet" href="/static/palette\.css\?v=\d+">', index_html)
    palette = re.search(r'<script defer src="/static/palette\.js\?v=\d+"></script>', index_html)
    app = re.search(r'<script defer src="/static/app\.js\?v=\d+"></script>', index_html)
    shell = re.search(r'<script defer src="/static/shell\.js\?v=\d+"></script>', index_html)
    assert palette and app and shell
    assert palette.start() < app.start()


def test_header_search_button_is_a_native_button_that_starts_hidden(index_html):
    button = re.search(r'<button id="app-search-trigger"[^>]*>', index_html)
    assert button
    assert 'type="button"' in button.group() and " hidden" in button.group()
    assert "Ctrl K" in index_html


def test_script_never_builds_html_from_strings(script):
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script


def test_combobox_listbox_pattern_is_complete(script):
    for expected in (
        'setAttribute("role", "dialog")', 'setAttribute("aria-modal", "true")',
        'setAttribute("role", "combobox")', 'setAttribute("aria-controls", "palette-list")',
        'setAttribute("aria-autocomplete", "list")', 'setAttribute("role", "listbox")',
        'setAttribute("role", "option")', 'setAttribute("aria-selected"',
        "aria-activedescendant", 'setAttribute("aria-live", "polite")',
    ):
        assert expected in script, expected


@pytest.mark.parametrize("key", ["ArrowDown", "ArrowUp", "Enter", "Escape", "Tab"])
def test_keyboard_keys_are_handled(script, key):
    assert f'event.key === "{key}"' in script


def test_ctrl_or_cmd_k_toggles_the_palette(script):
    assert re.search(r"\(event\.ctrlKey \|\| event\.metaKey\)", script)
    assert 'event.key.toLowerCase() === "k"' in script
    assert "event.preventDefault()" in script


def test_ime_composition_never_triggers_navigation(script):
    handler = re.search(r"function onKeydown\(event\) \{(?P<body>[\s\S]*?)\n  \}\n", script)
    assert handler
    first_lines = handler["body"].strip().splitlines()[:2]
    assert any("event.isComposing" in line and "event.keyCode === 229" in line for line in first_lines)


def test_stale_searches_are_cancelled_and_ignored(script):
    assert "new AbortController()" in script
    assert "controller?.abort()" in script
    assert "ticket !== sequence" in script
    assert "window.setTimeout(() => fetchRemote(text, ticket), DEBOUNCE_MS)" in script


def test_selection_hands_navigation_to_the_app(script, app_source):
    assert 'new CustomEvent("app:navigate", { detail })' in script
    handler = re.search(r'document\.addEventListener\("app:navigate", \(event\) => \{(?P<body>[\s\S]*?)\n\}\);', app_source)
    assert handler
    body = handler["body"]
    assert "recordId" in body and "openMistake(recordId)" in body
    assert "postId" in body and "openForumPost(postId)" in body
    assert "showView(target)" in body


def test_pages_are_discovered_from_the_sidebar_so_new_pages_appear_automatically(script):
    assert '.app-sidebar .nav-item[data-view]' in script
    assert "button.hidden" in script, "admin-only entries stay hidden for ordinary users"


def test_signing_out_closes_the_palette(app_source):
    body = re.search(r"function signedOut\(\) \{(.*?)\n\}\n", app_source, re.S)
    assert body and "CommandPalette?.close(" in body.group(1)


def test_stylesheet_follows_the_project_rules(stylesheet):
    assert "!important" not in stylesheet
    assert "backdrop-filter" not in stylesheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet), "colors must come from theme tokens"
    assert re.search(r"\.palette \{[^}]*z-index: 55;", stylesheet)
    # 输入框 16px 起，避免 iOS 聚焦时自动放大页面；手机上选项高度不小于 44px。
    assert re.search(r"\.palette-input \{[^}]*font-size: 16px;", stylesheet)
    assert re.search(r"\.palette-option \{ min-height: 52px; \}", stylesheet)
    assert re.search(r"@media \(prefers-reduced-motion: no-preference\)", stylesheet)
