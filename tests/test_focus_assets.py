"""专注复习的静态契约：引用、无障碍、评分接口、键盘、样式约束。"""
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def index_html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script():
    return (STATIC / "focus.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stylesheet():
    return (STATIC / "focus.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_assets_are_versioned_and_script_loads_before_the_app(index_html):
    assert re.search(r'<link rel="stylesheet" href="/static/focus\.css\?v=\d+">', index_html)
    focus = re.search(r'<script defer src="/static/focus\.js\?v=\d+"></script>', index_html)
    app = re.search(r'<script defer src="/static/app\.js\?v=\d+"></script>', index_html)
    assert focus and app and focus.start() < app.start()


def test_script_never_builds_html_from_strings(script):
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script


def test_five_keys_map_to_the_review_qualities_the_scheduler_accepts(script):
    grades = re.findall(r'\{ key: "(\d)", quality: (\d), label: "([^"]+)"', script)
    assert [(key, int(quality)) for key, quality, _ in grades] == [
        ("1", 0), ("2", 2), ("3", 3), ("4", 4), ("5", 5),
    ]
    # 1 和 2 是答错（调度器里 <3 重置间隔），3–5 是答对，和现有四个按钮的语义一致。
    assert all(int(quality) in range(0, 6) for _, quality, _ in grades)


def test_grading_uses_the_existing_review_endpoint_with_the_item_version(script):
    assert "/api/mistakes/${item.id}/review" in script
    assert "body: JSON.stringify({ quality, version: item.version })" in script
    assert '"X-CSRF-Protection": "1"' in script
    # 评分不经过 app.js 的 run()，不会触发全局禁用按钮；防重复提交靠这一轮自己的标记
    # （挂在发出请求时那一轮上，晚到的回应不会误伤后来新开的一轮）。
    assert "active.submitting" in script
    assert "submitting: false" in script


def test_stale_and_unauthorized_responses_are_handled(script):
    assert "response.status === 401" in script
    assert "response.status === 409" in script


def test_dialog_semantics_and_progress_bar(script):
    for expected in (
        'setAttribute("role", "dialog")', 'setAttribute("aria-modal", "true")',
        'setAttribute("role", "progressbar")', "aria-valuenow", "aria-valuetext",
        'setAttribute("aria-keyshortcuts"', 'setAttribute("aria-live", "polite")',
    ):
        assert expected in script, expected


@pytest.mark.parametrize("key", ["Escape", "Tab", "Enter"])
def test_keyboard_handling(script, key):
    assert f'event.key === "{key}"' in script


def test_space_and_enter_do_not_double_fire_on_a_focused_button(script):
    assert re.search(r'const onButton = Boolean\(event\.target\.closest\?\.\("button"\)\)', script)
    assert re.search(r'\(event\.key === " " \|\| event\.key === "Enter"\) && !onButton', script)


def test_ime_and_modified_keys_are_ignored(script):
    handler = re.search(r"function onKeydown\(event\) \{(?P<body>[\s\S]*?)\n  \}\n", script)
    assert handler
    assert "event.isComposing || event.ctrlKey || event.metaKey || event.altKey" in handler["body"].splitlines()[1]


def test_zone_filter_is_local_so_all_zones_can_come_back(script):
    assert 'new URLSearchParams({ due_only: "true" })' in script
    assert 'params.set("zone"' not in script


def test_closing_tells_the_app_what_happened(script, app_source):
    assert 'new CustomEvent("focus:closed", { detail: { graded, view } })' in script
    assert 'detail: { reason: "focus-review" }' in script
    handler = re.search(r'document\.addEventListener\("focus:closed", \(event\) => \{(?P<body>[\s\S]*?)\n\}\);', app_source)
    assert handler
    body = handler["body"]
    assert "if (!user) return;" in body
    assert "showView(target)" in body and "loadHome(" in body and "loadList()" in body


def test_entry_points_exist(index_html, app_source):
    assert re.search(r'<button id="list-focus"[^>]*\bhidden\b', index_html)
    assert re.search(
        r'FocusReview\?\.start\(\{ zone: \$\("#zone-filter"\)\.value, tag: \$\("#tag-filter"\)\.value \}\)',
        app_source,
    ), "the list page entry must carry both filters"
    assert "onFocus: () => window.FocusReview?.start({})" in app_source


def test_sign_out_and_page_changes_close_the_overlay(script, app_source):
    body = re.search(r"function signedOut\(\) \{(.*?)\n\}\n", app_source, re.S)
    assert body and "FocusReview?.close()" in body.group(1)
    assert 'document.addEventListener("app:view-changed", () => { if (isOpen()) close(); });' in script


def test_stylesheet_follows_the_project_rules(stylesheet):
    assert "!important" not in stylesheet
    assert "backdrop-filter" not in stylesheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet), "colors must come from theme tokens"
    assert re.search(r"\.focus \{[^}]*z-index: 52;", stylesheet)
    assert "body.focus-open { overflow: hidden; }" in stylesheet
    # 进度条动画受 reduced-motion 门控。
    gated = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{[^}]*\.focus-fill \{ transition", stylesheet)
    assert gated
    # 手机上五个评分按钮不小于 44px 高。
    assert re.search(r"\.focus-grade \{[^}]*min-height: 66px;", stylesheet)
