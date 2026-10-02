"""Learning-group asset and accessibility contracts, without temp directories."""

from pathlib import Path
import re

import pytest

from test_cursor_fx_assets import css_declarations, css_selectors, function_body
from test_layout_assets import LayoutDocument


STATIC = Path(__file__).resolve().parents[1] / "static"


class GroupDocument(LayoutDocument):
    """Retain visible copy alongside the shared structural document parser."""

    def handle_data(self, text):
        for index in self.stack:
            self.elements[index].setdefault("text", []).append(text)


@pytest.fixture(scope="module")
def group_css():
    path = STATIC / "groups.css"
    assert path.is_file()
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def document():
    parser = GroupDocument()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    parser.close()
    return parser.elements


@pytest.fixture(scope="module")
def group_source():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    # Keep all adjacent group helpers without requiring names for new helpers.
    first = re.search(r"(?m)^(?:async\s+)?function\s+\w*[Gg]roup\w*\s*\(", source)
    assert first, "Missing group page implementation"
    return source[first.start():].split("function resetAchievementShareCard", 1)[0]


def node_with_id(document, element_id):
    nodes = [(index, node) for index, node in enumerate(document)
             if node["attrs"].get("id") == element_id]
    assert len(nodes) == 1, element_id
    return nodes[0]


def property_rules(source, selector, property_name):
    """Return declarations in source order, including their conditional gates."""
    rules = []
    for blocks, declaration in css_declarations(source):
        name, _, value = declaration.partition(":")
        if name.strip() == property_name and selector in css_selectors(blocks[-1]):
            rules.append((blocks, value.strip()))
    assert rules, f"Missing {property_name} for {selector}"
    return rules


def unconditional_value(source, selector, property_name):
    rules = [value for blocks, value in property_rules(source, selector, property_name)
             if len(blocks) == 1]
    assert rules, f"Missing unconditional {property_name} for {selector}"
    return rules[-1]


def test_group_stylesheet_is_versioned_and_follows_shared_themes(document, group_css):
    links = [node["attrs"].get("href", "") for node in document
             if node["tag"] == "link" and node["attrs"].get("rel") == "stylesheet"]
    group_links = [href for href in links if href.split("?")[0] == "/static/groups.css"]
    assert group_links == ["/static/groups.css?v=2"]
    group_index = links.index(group_links[0])
    for shared in ("style.css", "themes.css"):
        assert next(i for i, href in enumerate(links)
                    if href.split("?")[0] == f"/static/{shared}") < group_index


def test_group_styles_cannot_escape_page_scope(group_css):
    checked = 0
    for blocks, declaration in css_declarations(group_css):
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            continue
        assert blocks and not blocks[-1].startswith("@"), declaration
        for selector in css_selectors(blocks[-1]):
            selector = re.sub(r"^(?:html(?:\[[^\]]+\])+\s+)+", "", selector)
            assert re.match(r"#groups-page(?=[\s.:#\[]|$)", selector), selector
            checked += 1
    assert checked


def test_group_styles_use_existing_tokens_and_no_external_assets(group_css):
    source = re.sub(r"/\*[\s\S]*?\*/", "", group_css)
    assert not re.search(r"!\s*important\b|@import\b|\burl\s*\(", source, re.I)
    for _, declaration in css_declarations(source):
        _, _, value = declaration.partition(":")
        assert not re.search(
            r"#[0-9a-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|lab|lch|oklab|oklch)\s*\(",
            value, re.I,
        ), declaration
    for token in ("--ink-2", "--accent", "--azurite", "--gamboge-ink", "--kai",
                  "--serif", "--text-lg"):
        assert f"var({token})" in source
    shared_css = "\n".join((STATIC / name).read_text(encoding="utf-8")
                           for name in ("style.css", "themes.css"))
    defined_tokens = set(re.findall(r"(--[\w-]+)\s*:", shared_css + source))
    assert set(re.findall(r"var\(\s*(--[\w-]+)", source)) <= defined_tokens
    for level in range(1, 9):
        assert re.search(rf'\[data-level=["\']{level}["\']\]', source)


def test_gold_seal_text_is_darker_only_in_ink_and_keeps_existing_borders(group_css):
    gold_token = "--groups-seal-gold-text"
    assert unconditional_value(group_css, "#groups-page", gold_token) == "var(--gamboge-ink)"
    ink_selector = 'html[data-theme="ink"] #groups-page'
    assert unconditional_value(group_css, ink_selector, gold_token) == "var(--ink)"
    mixed_rules = [(blocks, value) for blocks, value in property_rules(
        group_css, ink_selector, gold_token,
    ) if "color-mix(" in value]
    assert mixed_rules
    for blocks, value in mixed_rules:
        assert any(block.startswith("@supports") and "color-mix(" in block
                   for block in blocks)
        assert not any(block.startswith("@media") for block in blocks)
        assert re.sub(r"\s+", "", value) == (
            "color-mix(insrgb,var(--gamboge-ink)85%,var(--ink))"
        )
    # Qixi retains its existing gold, including on touch and unsupported devices.
    for blocks, declaration in css_declarations(group_css):
        name, _, value = declaration.partition(":")
        if name.strip() == gold_token:
            assert set(css_selectors(blocks[-1])) <= {"#groups-page", ink_selector}
            assert not any(block.startswith("@media") for block in blocks)
        if name.strip().startswith("border"):
            assert "--groups-seal-gold-text" not in value
            assert "--groups-seal-text" not in value
    badge = "#groups-page .groups-level-badge"
    assert unconditional_value(group_css, badge, "--groups-seal-text") == "var(--groups-seal-ink)"
    assert unconditional_value(group_css, badge, "color") == "var(--groups-seal-ink)"
    assert unconditional_value(group_css, badge, "border") == "3px solid var(--groups-seal-ink)"
    assert unconditional_value(group_css, badge + "::before", "border") == "1px solid currentColor"
    for level in (7, 8):
        selector = f'{badge}[data-level="{level}"]'
        assert unconditional_value(group_css, selector, "--groups-seal-ink") == "var(--gamboge-ink)"
        assert unconditional_value(group_css, selector, "--groups-seal-text") == f"var({gold_token})"
    text_selector = badge + " :is(.groups-seal-character, .groups-seal-number)"
    assert unconditional_value(group_css, text_selector, "color") == "var(--groups-seal-text)"
    for state in ("reached", "current"):
        selector = f'#groups-page .groups-level-step[data-state="{state}"] .groups-level-badge'
        assert unconditional_value(group_css, selector, "--groups-seal-text") == "var(--surface)"
        assert unconditional_value(group_css, selector, "background") == "var(--groups-seal-ink)"


def test_seal_number_font_sizes_never_fall_below_eight_pixels(group_css):
    shared_css = (STATIC / "style.css").read_text(encoding="utf-8")
    token_sizes = {}
    for blocks, declaration in css_declarations(shared_css):
        name, _, value = declaration.partition(":")
        if name.strip().startswith("--text-"):
            token_sizes.setdefault(name.strip(), []).append(value.strip())
    checked = 0
    for blocks, declaration in css_declarations(group_css):
        if ".groups-seal-number" not in blocks[-1]:
            continue
        name, _, value = declaration.partition(":")
        if name.strip() not in {"font", "font-size"}:
            continue
        size = re.search(r"(?:^|\s)(\d+(?:\.\d+)?px|var\(--text-[\w-]+\))(?=/|\s|$)", value.strip())
        assert size, f"Unresolved seal number font size: {declaration}"
        token = re.fullmatch(r"var\((--text-[\w-]+)\)", size[1])
        values = token_sizes.get(token[1], []) if token else [size[1]]
        assert values, f"Undefined seal number size token: {size[1]}"
        for resolved in values:
            assert re.fullmatch(r"\d+(?:\.\d+)?px", resolved), resolved
            assert float(resolved[:-2]) >= 8, (blocks, declaration, resolved)
        checked += 1
    assert checked >= 3, "Check default, hero and ladder seal number sizes"


@pytest.mark.parametrize("selector", [
    "#groups-page .page-heading .muted",
    "#groups-page #groups-status",
    "#groups-page #groups-list > .muted",
    "#groups-page #groups-streak-rules",
    "#groups-page #groups-member-actions",
    "#groups-page .groups-weakness > .muted",
    "#groups-page .danger-zone .danger-hint",
])
def test_exposed_group_explanations_use_full_strength_ink_on_every_device(group_css, selector):
    assert unconditional_value(group_css, "#groups-page", "--groups-note-ink") == "var(--ink)"
    assert unconditional_value(group_css, selector, "color") == "var(--groups-note-ink)"
    # A theme or media override must not restore a pale color on another device.
    for blocks, declaration in css_declarations(group_css):
        name, _, value = declaration.partition(":")
        if name.strip() == "--groups-note-ink":
            assert blocks == ("#groups-page",) and value.strip() == "var(--ink)"
        if name.strip() != "color":
            continue
        for candidate in css_selectors(blocks[-1]):
            plain_selector = re.sub(r"^html(?:\[[^\]]+\])+\s+", "", candidate)
            if plain_selector == selector and (candidate != selector or len(blocks) > 1):
                assert value.strip() == "var(--groups-note-ink)", (blocks, declaration)


def test_group_blur_and_transparency_have_device_gates_and_fallbacks(group_css):
    for blocks, declaration in css_declarations(group_css):
        if re.match(r"(?:-webkit-)?backdrop-filter\s*:", declaration):
            assert any(
                block.startswith("@media")
                and re.search(r"\(\s*hover\s*:\s*hover\s*\)", block)
                and re.search(r"\(\s*pointer\s*:\s*fine\s*\)", block)
                for block in blocks
            ), blocks
        if "color-mix(" in declaration and "transparent" in declaration:
            assert any(block.startswith("@supports") and "color-mix(" in block
                       for block in blocks), blocks
            # The non-supporting branch must retain an opaque background.
            assert "background: var(" in group_css


def test_group_motion_runs_only_when_requested(group_css):
    for blocks, declaration in css_declarations(group_css):
        name, _, value = declaration.partition(":")
        animated = re.match(r"(?:-\w+-)?(?:animation|transition)(?:-|$)", name.strip())
        keyframe = any(re.match(r"@(?:-\w+-)?keyframes\b", b) for b in blocks)
        if (animated and value.strip() != "none") or keyframe:
            assert any(block.startswith("@media") and re.search(
                r"\(\s*prefers-reduced-motion\s*:\s*no-preference\s*\)", block,
            ) for block in blocks), (blocks, declaration)
    assert ":focus-visible" in group_css


def test_existing_group_ids_and_live_status_are_preserved(document):
    page_index, page = node_with_id(document, "groups-page")
    assert page["attrs"].get("aria-labelledby") == "groups-title"
    for element_id in (
        "groups-title", "groups-status", "groups-retry", "groups-overview",
        "groups-list", "groups-create-form", "groups-name", "groups-join-form",
        "groups-invite-input", "groups-detail", "groups-back", "groups-detail-content",
        "groups-detail-title", "groups-invite-code", "groups-members-title",
        "groups-streak-rules", "groups-members", "groups-member-actions",
        "groups-weakness-title", "groups-weakness", "groups-leave", "groups-delete",
        "groups-hero", "groups-hero-badge", "groups-detail-meta",
        "groups-level-progress", "groups-level-caption", "groups-copy-invite",
        "groups-upgrade", "groups-level-ladder", "groups-level-rules",
        "groups-levels-status", "groups-levels-retry",
    ):
        _, node = node_with_id(document, element_id)
        assert page_index in node["ancestors"], element_id
    _, status = node_with_id(document, "groups-status")
    assert status["attrs"].get("role") == "status"
    assert status["attrs"].get("aria-live") == "polite"
    _, members = node_with_id(document, "groups-members")
    assert members["tag"] == "ul"
    assert members["attrs"].get("aria-labelledby") == "groups-members-title"
    assert members["attrs"].get("aria-describedby") == "groups-streak-rules"
    for element_id in ("groups-back", "groups-copy-invite", "groups-retry", "groups-levels-retry"):
        _, button = node_with_id(document, element_id)
        assert button["tag"] == "button"
        assert button["attrs"].get("type") == "button"


def test_group_forms_keep_labels_and_explain_the_ten_member_limit(document):
    for form_id, input_id in (("groups-create-form", "groups-name"),
                             ("groups-join-form", "groups-invite-input")):
        form_index, form = node_with_id(document, form_id)
        _, control = node_with_id(document, input_id)
        assert form["tag"] == "form"
        assert form_index in control["ancestors"]
        labels = [node for node in document if node["tag"] == "label"
                  and node["attrs"].get("for") == input_id]
        assert len(labels) == 1 and form_index in labels[0]["ancestors"]
        panel = document[form["ancestors"][-1]]
        assert "每个小组最多 10 人" in "".join(panel.get("text", []))


def test_hero_progress_has_accessible_semantics_and_invite_is_selectable(document):
    hero_index, _ = node_with_id(document, "groups-hero")
    _, progress = node_with_id(document, "groups-level-progress")
    assert hero_index in progress["ancestors"]
    assert progress["attrs"].get("role") == "progressbar"
    assert progress["attrs"].get("aria-label")
    assert progress["attrs"].get("aria-valuemin") == "0"
    assert progress["attrs"].get("aria-valuemax") == "100"
    _, code = node_with_id(document, "groups-invite-code")
    assert code["attrs"].get("tabindex") == "0"
    assert code["attrs"].get("aria-label")


def test_upgrade_is_a_native_collapsed_details_control(document):
    details_index, details = node_with_id(document, "groups-upgrade")
    assert details["tag"] == "details"
    assert "open" not in details["attrs"]
    summaries = [node for node in document if node["tag"] == "summary"
                 and details_index in node["ancestors"]]
    assert len(summaries) == 1
    assert summaries[0]["attrs"].get("tabindex", "0") != "-1"
    for element_id in ("groups-level-ladder", "groups-level-rules"):
        _, node = node_with_id(document, element_id)
        assert details_index in node["ancestors"]
    _, ladder = node_with_id(document, "groups-level-ladder")
    assert ladder["tag"] == "ol"
    _, rules = node_with_id(document, "groups-level-rules")
    assert rules["tag"] == "ul"
    _, status = node_with_id(document, "groups-levels-status")
    assert status["attrs"].get("role") == "status"
    assert status["attrs"].get("aria-live") == "polite"
    copy = "".join(details.get("text", []))
    assert "等级按当前成员的贡献实时计算，成员退出后其贡献不再计入" in copy


def test_frontend_reads_thresholds_from_authenticated_api(group_source):
    assert re.search(r'\bapi\(\s*(["\'])/api/group-levels\1', group_source)
    # Other pages have their own limits; group renderers must use API values.
    assert not re.search(r"\b(?:120|360|800|1500|2600|4200|6500)\b", group_source)
    for api_field in ("min_points", "daily_cap", "member_limit"):
        assert api_field in group_source


def test_group_renderers_keep_native_buttons_and_use_avatar_existence(group_source):
    cards = function_body(group_source, "renderGroups")
    assert re.search(r'\belement\(\s*(["\'])button\1', cards)
    assert re.search(r'\.type\s*=\s*(["\'])button\1', cards)
    assert re.search(r'\.disabled\s*=\s*busy\b', cards)
    assert "members_preview" in cards and "avatarElement(" in cards
    assert "has_avatar" in cards
    members = function_body(group_source, "renderStudyGroup")
    assert re.search(r'\belement\(\s*(["\'])li\1', members)
    assert "avatarElement(" in members and "has_avatar" in members
    assert "removeStudyGroupMember(member)" in members
    assert re.search(r'\.setAttribute\(\s*(["\'])aria-label\1', members)
    # The server supplies streak order; changing it here would hide that contract.
    assert not re.search(r'\bgroup\.members\s*\.sort\s*\(', members)


def test_level_badge_factory_is_decorative_and_uses_the_level_name(group_source):
    bodies = [function_body(group_source, name) for name in re.findall(
        r"(?m)^(?:async\s+)?function\s+(\w+)\s*\(", group_source,
    )]
    factories = [body for body in bodies if re.search(r'\.dataset\.level\s*=', body)]
    assert factories, "Missing data-level seal badge factory"
    for body in factories:
        assert re.search(r'\.setAttribute\(\s*(["\'])aria-hidden\1\s*,\s*(["\'])true\2', body)
        assert re.search(r'\.name\s*\.slice\(\s*0\s*,\s*1\s*\)', body)
        assert "Lv." in body


def test_invite_copy_and_upgrade_seen_storage_have_safe_fallbacks(group_source):
    assert "navigator.clipboard.writeText" in group_source
    assert "document.createRange(" in group_source
    assert "getSelection(" in group_source
    assert "已复制" in group_source and "message(" in group_source
    assert not re.search(r"\balert\s*\(", group_source)
    assert "localStorage" in group_source and "groupLevelSeen:" in group_source
    assert "stampSeal(" in group_source and "升至 Lv." in group_source
    assert "try" in group_source and "catch" in group_source


def test_group_loaders_keep_stale_response_and_error_guards():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    body = function_body(source, "requestGroups")
    for guard in ("groupsGeneration", "isCurrent", '"aria-busy"', "catch", "finally"):
        assert guard in body
