"""Weakness report asset contracts without browsers, databases or temp files."""

from pathlib import Path
import json
import re
import shutil
import subprocess

import pytest

from test_cursor_fx_assets import css_declarations, css_selectors, function_body
from test_layout_assets import LayoutDocument


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def stylesheet():
    path = STATIC / "weakness.css"
    assert path.is_file(), "The weakness report needs its own stylesheet"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def document():
    document = LayoutDocument()
    document.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    document.close()
    return document.elements


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


def test_report_stylesheet_is_versioned_and_loaded_after_shared_styles(document, stylesheet):
    links = [
        node["attrs"] for node in document
        if node["tag"] == "link" and node["attrs"].get("rel") == "stylesheet"
    ]
    report_links = [
        (index, attrs) for index, attrs in enumerate(links)
        if attrs.get("href", "").split("?", 1)[0] == "/static/weakness.css"
    ]
    assert len(report_links) == 1
    report_index, attrs = report_links[0]
    assert re.fullmatch(r"/static/weakness\.css\?v=\d+", attrs["href"])
    shared_indices = [
        index for index, attrs in enumerate(links)
        if attrs.get("href", "").split("?", 1)[0] in (
            "/static/style.css", "/static/themes.css",
        )
    ]
    assert shared_indices and max(shared_indices) < report_index


def test_report_selectors_cannot_change_other_pages(stylesheet):
    checked = 0
    for blocks, declaration in css_declarations(stylesheet):
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            continue
        assert blocks and not blocks[-1].startswith("@"), declaration
        for selector in css_selectors(blocks[-1]):
            # Theme and active ripple attributes may precede the page scope.
            scoped = re.sub(r"^(?:html(?:\[[^\]]+\])+\s+)+", "", selector)
            assert re.match(r"#weakness-page(?=[\s.:#\[]|$)", scoped), selector
            checked += 1
    assert checked, "Missing page-scoped report rules"


def test_report_paper_has_rounded_edges_and_padding_on_all_sides(stylesheet):
    declarations = list(css_declarations(stylesheet))
    layout = "#weakness-page .weakness-layout"
    base = {
        declaration for blocks, declaration in declarations
        if blocks == (layout,)
    }
    assert "border-radius: var(--radius)" in base
    assert "border: 1px solid var(--line)" in base
    assert "padding: var(--space-6)" in base
    assert not any(declaration.startswith("padding-top:") for declaration in base)
    assert not any(declaration.startswith("border-top:") for declaration in base)
    assert any(
        blocks[-1] == layout
        and "@media (max-width: 600px)" in blocks
        and declaration == "padding: var(--space-4)"
        for blocks, declaration in declarations
    )
    # A clipped/scrolling wrapper would break sticky containment or focus rings.
    assert not any(
        layout in css_selectors(blocks[-1])
        and re.match(r"overflow(?:-[xy])?\s*:\s*(hidden|clip|auto|scroll)\b", declaration)
        for blocks, declaration in declarations
        if not blocks[-1].startswith("@")
    )


def test_report_uses_tokens_without_priority_overrides_or_external_assets(stylesheet):
    source = re.sub(r"/\*[\s\S]*?\*/", "", stylesheet)
    assert not re.search(r"!\s*important\b", source, flags=re.IGNORECASE)
    assert not re.search(r"@import\b|\burl\s*\(", source, flags=re.IGNORECASE)
    for _, declaration in css_declarations(source):
        name, separator, value = declaration.partition(":")
        if not separator:
            continue
        # Color values belong to the existing shared theme token definitions.
        assert not re.search(r"#[0-9a-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|lab|lch|oklab|oklch)\s*\(", value, re.I), (
            f"Hard-coded color in {name}: {value}"
        )


def test_report_blur_and_translucency_keep_the_existing_ripple_gates(stylesheet):
    for blocks, declaration in css_declarations(stylesheet):
        if re.match(r"(?:-webkit-)?backdrop-filter\s*:", declaration):
            assert any(
                block.startswith("@media")
                and re.search(r"\(\s*hover\s*:\s*hover\s*\)", block)
                and re.search(r"\(\s*pointer\s*:\s*fine\s*\)", block)
                for block in blocks
            ), f"Touch devices must not receive report blur: {blocks}"
        if "color-mix(" not in declaration or "transparent" not in declaration:
            continue
        assert any(
            block.startswith("@supports") and "color-mix(" in block
            for block in blocks
        ), f"Missing an opaque fallback: {blocks}"
        for selector in css_selectors(blocks[-1]):
            assert re.match(r"html\[data-cursor-fx=([\"'])ripple\1\]", selector), selector


def test_report_motion_is_opt_in_short_and_does_not_change_layout(stylesheet):
    animated = 0
    keyframe_declarations = 0
    for blocks, declaration in css_declarations(stylesheet):
        name, separator, value = declaration.partition(":")
        assert separator, declaration
        name = name.strip()
        if re.match(r"(?:-\w+-)?animation(?:-|$)", name):
            animated += 1
            assert motion_allowed(blocks), f"Report animation lacks motion preference gate: {blocks}"
            if name.endswith("animation"):
                # The first time in each shorthand is its duration; later
                # times can be deliberate stagger delays rather than motion.
                durations = [
                    re.search(r"(?<![\w.])([\d.]+)(ms|s)\b", animation)
                    for animation in css_selectors(value)
                ]
            elif name.endswith("animation-duration"):
                durations = list(re.finditer(r"(?<![\w.])([\d.]+)(ms|s)\b", value))
            else:
                durations = []
            for duration in filter(None, durations):
                milliseconds = float(duration[1]) * (1000 if duration[2] == "s" else 1)
                assert milliseconds <= 220, f"Report animation is too long: {declaration}"
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            keyframe_declarations += 1
            assert motion_allowed(blocks), f"Report keyframes lack motion preference gate: {blocks}"
            assert name in ("opacity", "transform"), f"Layout-changing report keyframe: {declaration}"
    assert animated and keyframe_declarations, "Missing the report's optional entrance motion"


def test_analysis_desk_only_sticks_in_wide_and_tall_viewports(stylesheet):
    sticky_rules = [
        blocks for blocks, declaration in css_declarations(stylesheet)
        if declaration == "position: sticky"
        and "#weakness-page .weakness-controls" in css_selectors(blocks[-1])
    ]
    assert sticky_rules, "Keep the analysis desk sticky when enough screen space is available"
    for blocks in sticky_rules:
        assert any(
            block.startswith("@media")
            and re.search(r"\(\s*min-width\s*:\s*1024px\s*\)", block)
            and re.search(r"\(\s*min-height\s*:\s*640px\s*\)", block)
            for block in blocks
        ), "Short viewports must be able to scroll the expanded billing explanation normally"


def test_pattern_list_removes_visual_markers_and_has_its_own_hidden_text_style(stylesheet):
    declarations = list(css_declarations(stylesheet))
    assert any(
        blocks == ("#weakness-page .weakness-patterns",)
        and declaration == "list-style: none"
        for blocks, declaration in declarations
    )
    hidden_text = {
        declaration for blocks, declaration in declarations
        if blocks == ("#weakness-page .weakness-sr-only",)
    }
    assert {"position: absolute", "width: 1px", "height: 1px", "overflow: hidden", "clip-path: inset(50%)"} <= hidden_text


def test_report_preserves_unique_ids_and_existing_accessibility(document):
    page_index, page = element_with_id(document, "weakness-page")
    assert page["attrs"].get("aria-labelledby") == "weakness-title"
    for element_id in (
        "weakness-controls", "weakness-count", "weakness-quota", "weakness-analyze",
        "weakness-cost", "weakness-status", "weakness-empty", "weakness-empty-text",
        "weakness-result", "growth-empty", "growth-summary", "growth-zones",
    ):
        _, node = element_with_id(document, element_id)
        assert page_index in node["ancestors"], element_id
    _, button = element_with_id(document, "weakness-analyze")
    assert button["tag"] == "button"
    assert button["attrs"].get("type") == "button"
    assert button["attrs"].get("aria-describedby") == "weakness-cost"
    assert button["attrs"].get("data-blocked") == "1"
    assert "disabled" in button["attrs"]
    _, status = element_with_id(document, "weakness-status")
    assert status["attrs"].get("role") == "status"
    assert status["attrs"].get("aria-live") == "polite"


def test_cost_explanation_uses_a_native_collapsed_keyboard_control(document):
    controls_index, _ = element_with_id(document, "weakness-controls")
    _, cost = element_with_id(document, "weakness-cost")
    details = [
        (index, document[index]) for index in cost["ancestors"]
        if document[index]["tag"] == "details"
    ]
    assert len(details) == 1
    details_index, disclosure = details[0]
    assert controls_index in disclosure["ancestors"]
    assert "open" not in disclosure["attrs"]
    summaries = [
        node for node in document
        if node["tag"] == "summary" and details_index in node["ancestors"]
    ]
    assert len(summaries) == 1
    assert summaries[0]["attrs"].get("tabindex", "0") != "-1"
    assert "hidden" not in summaries[0]["attrs"]


@pytest.mark.parametrize("scenario", ["controls", "quota", "growth", "growth-reset", "analysis"])
def test_report_rendering_preserves_control_states_and_data_semantics(document, scenario):
    """Execute real render functions without app startup or a browser/database."""
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the frontend render behavior checks")
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    functions = []
    for name in (
        "element", "timestamp", "renderWeaknessControls", "resetGrowthInsights",
        "resetWeaknessAnalysis", "weaknessRequestCurrent", "loadWeaknessAnalysis",
        "renderWeaknessAnalysis", "renderGrowthInsights",
    ):
        declaration = re.search(
            rf"(?m)^(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
            source,
        )
        assert declaration, f"Missing {name} renderer or helper"
        functions.append(declaration[0] + function_body(source, name) + "}")
    payload = {
        "source": "\n".join(functions),
        "scenario": scenario,
        "ids": {
            node["attrs"]["id"]: node["attrs"]
            for node in document if "id" in node["attrs"]
        },
    }
    result = subprocess.run(
        [node, str(Path(__file__).with_name("test_weakness_page_render.cjs"))],
        input=json.dumps(payload, ensure_ascii=False),
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=10,
        cwd=STATIC.parent,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
