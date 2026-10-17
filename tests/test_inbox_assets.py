"""Inbox (sorting bins) asset contracts without a browser, database, or temp files."""

from pathlib import Path
import re

import pytest

from test_cursor_fx_assets import css_declarations, css_selectors, function_body
from test_layout_assets import LayoutDocument


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def stylesheet():
    path = STATIC / "inbox.css"
    assert path.is_file(), "Inbox styles need their own stylesheet"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def source():
    return (STATIC / "inbox.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def document():
    parser = LayoutDocument()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    parser.close()
    return parser.elements


def element_with_id(document, element_id):
    matches = [
        (index, node) for index, node in enumerate(document)
        if node["attrs"].get("id") == element_id
    ]
    assert len(matches) == 1, f"Expected one #{element_id}"
    return matches[0]


def motion_allowed(blocks):
    return any(
        block.startswith("@media")
        and re.search(r"\(\s*prefers-reduced-motion\s*:\s*no-preference\s*\)", block)
        for block in blocks
    )


def test_inbox_stylesheet_is_versioned_and_loaded_after_shared_styles(document, stylesheet):
    links = [
        node["attrs"] for node in document
        if node["tag"] == "link" and node["attrs"].get("rel") == "stylesheet"
    ]
    matches = [
        (index, attrs) for index, attrs in enumerate(links)
        if attrs.get("href", "").split("?", 1)[0] == "/static/inbox.css"
    ]
    assert len(matches) == 1
    inbox_index, attrs = matches[0]
    assert attrs["href"] == "/static/inbox.css?v=2"
    shared_indices = [
        index for index, attrs in enumerate(links)
        if attrs.get("href", "").split("?", 1)[0] in (
            "/static/style.css",
            "/static/themes.css",
        )
    ]
    assert shared_indices and max(shared_indices) < inbox_index


def test_inbox_script_is_versioned(document):
    scripts = [
        node["attrs"] for node in document
        if node["tag"] == "script" and node["attrs"].get("src", "").split("?", 1)[0] == "/static/inbox.js"
    ]
    assert len(scripts) == 1
    assert re.fullmatch(r"/static/inbox\.js\?v=\d+", scripts[0]["src"]), "带版本号（数字跟随 index.html 提升，不写死）"


def test_inbox_selectors_are_scoped_to_inbox_or_roadmap_page(stylesheet):
    checked = 0
    for blocks, declaration in css_declarations(stylesheet):
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            continue
        assert blocks and not blocks[-1].startswith("@"), declaration
        for selector in css_selectors(blocks[-1]):
            scoped = re.sub(r"^(?:html(?:\[[^\]]+\])+\s+)+", "", selector)
            assert re.match(r"#(inbox-page|roadmap-page)(?=[\s.:#\[]|$)", scoped), selector
            checked += 1
    assert checked, "Missing page-scoped inbox rules"


def test_inbox_uses_theme_tokens_without_priority_overrides_or_remote_assets(stylesheet):
    clean = re.sub(r"/\*[\s\S]*?\*/", "", stylesheet)
    assert not re.search(r"!\s*important\b", clean, re.I)
    assert not re.search(r"@import\b|\burl\s*\(", clean, re.I)
    for _, declaration in css_declarations(clean):
        name, separator, value = declaration.partition(":")
        assert separator, declaration
        assert not re.search(
            r"#[0-9a-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|lab|lch|oklab|oklch)\s*\((?!\s*var\s*\()",
            value, re.I,
        ), f"Hard-coded color in {name}: {value}"
        if name.strip() in ("color", "background-color", "border-color", "outline-color"):
            assert re.search(r"var\s*\(|\b(?:currentColor|transparent|inherit)\b", value), declaration


def test_inbox_motion_is_allowed_only_with_no_reduced_motion(stylesheet):
    animated = 0
    for blocks, declaration in css_declarations(stylesheet):
        name, _, value = declaration.partition(":")
        if re.match(r"(?:-\w+-)?animation(?:-|$)", name.strip()):
            animated += 1
            assert motion_allowed(blocks), f"Inbox animation lacks a motion gate: {blocks}"
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            assert motion_allowed(blocks), f"Inbox keyframes lack a motion gate: {blocks}"
        if name.strip() == "scroll-behavior" and value.strip() == "smooth":
            assert motion_allowed(blocks), f"Smooth scrolling lacks a motion gate: {blocks}"
    assert animated, "Missing inbox animations (suggested-bin breathing, stamp, ink)"


def test_inbox_page_structure(document):
    page_index, _ = element_with_id(document, "inbox-page")
    for element_id in (
        "inbox-title", "inbox-back", "inbox-status", "inbox-retry",
        "inbox-remaining", "inbox-arena", "inbox-card-stack", "inbox-bins",
        "inbox-bubble", "inbox-toast", "inbox-done", "inbox-results",
        "inbox-back-results", "inbox-live",
    ):
        _, node = element_with_id(document, element_id)
        assert page_index in node["ancestors"], element_id
    _, live = element_with_id(document, "inbox-live")
    assert live["attrs"].get("aria-live") == "polite", "keyboard steps must be announced"


def test_roadmap_page_has_the_inbox_entry_point(document):
    _, node = element_with_id(document, "roadmap-inbox-entry")
    page_index, _ = element_with_id(document, "roadmap-page")
    assert page_index in node["ancestors"]


def test_inbox_script_contracts(source):
    # 三种操作方式：拖拽用 Pointer Events，绝不用 HTML5 原生 dragstart。
    assert "pointerdown" in source and "pointermove" in source and "pointerup" in source
    assert "dragstart" not in source
    # 迟到响应丢弃。
    assert "function ticket()" in source and "function current(" in source
    # 用户文字不用 innerHTML 拼。
    assert "innerHTML" not in source
    # 撤销与跳过。
    assert "inbox-undo" in source
    assert "skipCurrent" in source
