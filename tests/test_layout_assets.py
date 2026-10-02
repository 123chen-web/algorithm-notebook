"""Static layout contracts without a browser, database, or temporary files."""

from html.parser import HTMLParser
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "static"


class LayoutDocument(HTMLParser):
    """Keep ancestor relationships for structural layout contracts."""

    void_tags = frozenset(
        "area base br col embed hr img input link meta param source track wbr".split()
    )

    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.elements.append({
            "tag": tag, "attrs": dict(attrs), "ancestors": tuple(self.stack),
        })
        if tag not in self.void_tags:
            self.stack.append(len(self.elements) - 1)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.elements[self.stack[position]]["tag"] == tag:
                del self.stack[position:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.void_tags:
            self.handle_endtag(tag)


def layout_document():
    document = LayoutDocument()
    document.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    document.close()
    return document.elements


def elements_with_class(elements, class_name):
    return [
        (index, element) for index, element in enumerate(elements)
        if class_name in element["attrs"].get("class", "").split()
    ]


def test_typography_tokens_and_progressive_enhancement_are_present():
    stylesheet = (STATIC / "style.css").read_text(encoding="utf-8")
    for token in ("--measure", "--space-4", "--text-2xl"):
        assert re.search(rf"{re.escape(token)}\s*:\s*[^;{{}}]+;", stylesheet)
    for property_name, value in (
        ("text-autospace", "normal"),
        ("text-spacing-trim", "normal"),
        ("line-break", "strict"),
        ("text-wrap", "balance"),
        ("text-wrap", "pretty"),
    ):
        assert re.search(rf"\b{property_name}\s*:\s*{value}\s*;", stylesheet)


def test_new_page_container_is_not_centered_by_auto_margins():
    stylesheet = (STATIC / "style.css").read_text(encoding="utf-8")
    stylesheet = re.sub(r"/\*[\s\S]*?\*/", "", stylesheet)
    page_rules = [
        declarations
        for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", stylesheet)
        if "#new-page" in [selector.strip() for selector in selectors.split(",")]
    ]
    assert page_rules, "Missing new-page container rule"
    for declarations in page_rules:
        assert not re.search(r"\bmargin\s*:\s*0(?:px)?\s+auto\b", declarations)


def test_new_record_code_field_uses_ten_rows():
    class CodeFieldParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = []

        def handle_starttag(self, tag, attrs):
            attributes = dict(attrs)
            if tag == "textarea" and attributes.get("name") == "code":
                self.fields.append(attributes)

    document = CodeFieldParser()
    document.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    document.close()
    assert len(document.fields) == 1
    assert document.fields[0].get("rows") == "10"


def test_account_menu_is_unique_and_owns_its_controls():
    elements = layout_document()
    menus = elements_with_class(elements, "account-menu")
    assert len(menus) == 1
    menu_index, menu = menus[0]
    assert menu["tag"] == "details"
    assert len(elements_with_class(elements, "user-meta")) == 1

    summaries = [
        element for element in elements
        if element["tag"] == "summary" and menu_index in element["ancestors"]
    ]
    assert len(summaries) == 1
    assert summaries[0]["attrs"].get("aria-label") == "账号菜单"

    panels = elements_with_class(elements, "account-menu-panel")
    assert len(panels) == 1
    panel_index, panel = panels[0]
    assert menu_index in panel["ancestors"]
    for control_id in ("avatar-file-input", "remove-avatar-btn", "fx-toggle"):
        controls = [
            element for element in elements
            if element["attrs"].get("id") == control_id
        ]
        assert len(controls) == 1
        assert panel_index in controls[0]["ancestors"], control_id


def test_account_menu_panel_has_an_opaque_surface_background():
    stylesheet = (STATIC / "style.css").read_text(encoding="utf-8")
    stylesheet = re.sub(r"/\*[\s\S]*?\*/", "", stylesheet)
    backgrounds = []
    for selectors, declarations in re.findall(r"([^{}]+)\{([^{}]*)\}", stylesheet):
        if not re.search(r"\.account-menu-panel(?![\w-])", selectors):
            continue
        backgrounds.extend(re.findall(
            r"(?:^|;)\s*background(?:-color)?\s*:\s*([^;]+)", declarations
        ))
    assert backgrounds, "Missing account menu panel background"
    assert any(value.strip() == "var(--surface)" for value in backgrounds)
    for value in backgrounds:
        assert not re.search(r"\b(?:color-mix|rgba)\s*\(|\btransparent\b", value)


def test_account_menu_escape_closes_and_restores_summary_focus():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    declaration = re.search(
        r"(?m)^(?P<indent>[ \t]*)function\s+initAccountMenu\s*\([^)]*\)\s*\{",
        source,
    )
    assert declaration, "Missing account menu initialization"
    end = re.search(
        rf"(?m)^{re.escape(declaration['indent'])}\}}", source[declaration.end():]
    )
    assert end, "Unclosed account menu initialization"
    body = source[declaration.end():declaration.end() + end.start()]
    assert re.search(r"\.key\s*===?\s*([\"'])Escape\1", body)
    assert re.search(r"\.open\s*=\s*false\b", body)
    assert re.search(r"\bsummary\.focus\s*\(", body)
    assert re.search(r"\bclose\s*\(\s*true\s*\)", body)


def test_intro_steps_share_a_panel_and_invitation_is_its_difference_footer():
    elements = layout_document()
    steps_panels = elements_with_class(elements, "intro-steps")
    assert len(steps_panels) == 1
    steps_index, _ = steps_panels[0]
    steps = elements_with_class(elements, "intro-step")
    assert len(steps) == 3
    assert all(steps_index in step["ancestors"] for _, step in steps)

    differences = elements_with_class(elements, "intro-difference")
    invitations = elements_with_class(elements, "intro-invitation")
    assert len(differences) == len(invitations) == 1
    difference_index, _ = differences[0]
    invitation_index, invitation = invitations[0]
    assert difference_index in invitation["ancestors"]
    intro_toggle = [
        element for element in elements
        if element["attrs"].get("id") == "fx-toggle-intro"
    ]
    assert len(intro_toggle) == 1
    assert invitation_index in intro_toggle[0]["ancestors"]


def test_page_menu_follows_its_toggle_before_refresh_in_keyboard_order():
    elements = layout_document()
    controls = {}
    for element_id in ("page-nav", "nav-toggle", "nav-menu", "refresh"):
        matches = [
            (index, node) for index, node in enumerate(elements)
            if node["attrs"].get("id") == element_id
        ]
        assert len(matches) == 1, element_id
        controls[element_id] = matches[0]
    nav_index, _ = controls["page-nav"]
    toggle_index, toggle = controls["nav-toggle"]
    menu_index, menu = controls["nav-menu"]
    refresh_index, refresh = controls["refresh"]
    assert nav_index in toggle["ancestors"]
    assert toggle["ancestors"] == menu["ancestors"] == refresh["ancestors"]
    siblings = [
        index for index, node in enumerate(elements)
        if node["ancestors"] == toggle["ancestors"]
    ]
    assert siblings[siblings.index(toggle_index) + 1] == menu_index
    assert siblings.index(menu_index) < siblings.index(refresh_index)
    assert toggle["tag"] == "button"
    assert toggle["attrs"].get("aria-controls") == "nav-menu"
    assert toggle["attrs"].get("aria-expanded") == "false"
    assert "hidden" in menu["attrs"]


def navigation_listener_body(source, event_name, receiver):
    """Read a listener block without depending on indentation or line breaks."""
    opening = re.search(
        rf'{receiver}\s*\.addEventListener\(\s*'
        rf'(?P<quote>["\']){re.escape(event_name)}(?P=quote)\s*,\s*'
        r'\(?\s*(?P<event>[A-Za-z_$][\w$]*)\s*\)?\s*=>\s*\{',
        source,
    )
    assert opening, f"Missing page navigation {event_name} listener"
    # Skip strings and comments so their braces do not change the block depth.
    tokens = r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|`(?:\\.|[^`\\])*`|//[^\n]*|/\*[\s\S]*?\*/|[{}]'
    depth = 1
    for token in re.finditer(tokens, source[opening.end():]):
        if token.group() == "{":
            depth += 1
        elif token.group() == "}":
            depth -= 1
            if depth == 0:
                return opening["event"], source[opening.end():opening.end() + token.start()]
    raise AssertionError(f"Unclosed page navigation {event_name} listener")


def test_page_menu_escape_closes_and_restores_toggle_focus():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    event, body = navigation_listener_body(source, "keydown", r'\bdocument')
    assert re.search(rf'\b{re.escape(event)}\.key\s*(?:===?|!==?)\s*(["\'])Escape\1', body)
    assert re.search(r'\$\(\s*(["\'])#nav-menu\1\s*\)\.hidden\b', body)
    assert not re.search(rf'\b{re.escape(event)}\.target\b', body), "Escape must also close from outside the navigation"
    restore = re.search(
        r'\b(?:const|let)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*'
        r'document\.activeElement\s*===?\s*\$\(\s*(["\'])#nav-toggle\2\s*\)\s*\|\|\s*'
        r'\$\(\s*(["\'])#nav-menu\3\s*\)\.contains\(\s*document\.activeElement\s*\)',
        body,
    )
    assert restore, "Restore focus only when it started on the toggle or inside the menu"
    close = re.search(r'\bcloseNavMenu\s*\(\s*\)', body)
    focus = re.search(
        rf'\bif\s*\(\s*{re.escape(restore["name"])}\s*\)\s*(?:\{{\s*)?'
        r'\$\(\s*(["\'])#nav-toggle\1\s*\)\.focus\s*\(\s*\)',
        body,
    )
    assert close and focus and restore.end() < close.start() < focus.start()


def test_page_menu_focusout_ignores_unknown_focus_destinations():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    receiver = r'\$\(\s*(?P<selector_quote>["\'])#page-nav(?P=selector_quote)\s*\)'
    event, body = navigation_listener_body(source, "focusout", receiver)
    event = re.escape(event)
    assert re.search(
        rf'\bif\s*\(\s*{event}\.relatedTarget\s*&&\s*'
        rf'!\s*{event}\.currentTarget\.contains\(\s*{event}\.relatedTarget\s*\)\s*\)\s*'
        r'(?:\{\s*)?closeNavMenu\s*\(\s*\)',
        body,
    ), "Only a known focus destination outside page-nav should close the menu"


def test_qixi_page_navigation_has_top_spacing_without_changing_its_bottom_gap():
    from test_cursor_fx_assets import css_declarations, css_selectors

    stylesheet = (STATIC / "themes.css").read_text(encoding="utf-8")
    selector = r'html\[data-theme\s*=\s*(["\'])qixi\1\]\s+#page-nav\.tabs'
    declarations = [
        declaration for blocks, declaration in css_declarations(stylesheet)
        if blocks and any(re.fullmatch(selector, item) for item in css_selectors(blocks[-1]))
    ]
    assert any(re.fullmatch(r'margin-top\s*:\s*var\(\s*--space-3\s*\)', declaration) for declaration in declarations)
    assert not any(re.match(r'margin(?:-bottom)?\s*:', declaration) for declaration in declarations)


def test_qixi_menu_ancestors_have_explicit_ordered_stacking_levels():
    from test_cursor_fx_assets import css_declarations, css_selectors

    stylesheet = (STATIC / "themes.css").read_text(encoding="utf-8")
    theme = r'html\[data-theme\s*=\s*(?:["\']qixi["\']|qixi)\]\s+'
    selectors = {
        "header": theme + r'\.header(?::has\(\s*\.account-menu\[\s*open\s*\]\s*\))?',
        "navigation": theme + r'(?:#page-nav\.tabs|\.tabs#page-nav)'
        r'(?::has\(\s*#nav-menu:not\(\s*\[\s*hidden\s*\]\s*\)\s*\))?',
    }
    levels = {}
    for blocks, declaration in css_declarations(stylesheet):
        if not blocks:
            continue
        z_index = re.fullmatch(r'z-index\s*:\s*([+-]?\d+)\s*', declaration)
        if not z_index:
            continue
        for name, selector in selectors.items():
            if any(re.fullmatch(selector, item) for item in css_selectors(blocks[-1])):
                levels[name] = int(z_index[1])

    assert set(levels) == set(selectors), "Qixi menu ancestors need explicit numeric z-index values"
    assert levels["header"] > levels["navigation"] > 0, (
        "The account menu header must stack above page navigation, and both above content"
    )
