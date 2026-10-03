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


def element_index(elements, element_id):
    matches = [
        index for index, node in enumerate(elements)
        if node["attrs"].get("id") == element_id
    ]
    assert len(matches) == 1, element_id
    return matches[0]


def test_shell_keyboard_order_is_skip_link_then_sidebar_then_topbar_then_content():
    elements = layout_document()
    skip = elements_with_class(elements, "skip-link")
    assert len(skip) == 1
    skip_index, link = skip[0]
    assert link["tag"] == "a" and link["attrs"].get("href") == "#app"
    app_index = element_index(elements, "app")
    assert elements[app_index]["tag"] == "main"
    assert elements[app_index]["attrs"].get("tabindex") == "-1", "the skip link target must be focusable"

    sidebar_index = element_index(elements, "app-sidebar")
    headers = [
        index for index, node in enumerate(elements)
        if node["tag"] == "header" and "header" in node["attrs"].get("class", "").split()
    ]
    assert len(headers) == 1
    assert skip_index < sidebar_index < headers[0] < app_index
    sidebar = elements[sidebar_index]
    assert sidebar["tag"] == "aside"
    navigation = [
        node for node in elements
        if node["tag"] == "nav" and sidebar_index in node["ancestors"]
    ]
    assert [node["attrs"].get("aria-label") for node in navigation] == ["主导航"]

    # 手机端的底栏和"更多"抽屉在主容器之后，且不在 <main> 里。
    tabbar_index = element_index(elements, "app-tabbar")
    sheet_index = element_index(elements, "more-sheet")
    assert app_index < tabbar_index < sheet_index
    assert app_index not in elements[tabbar_index]["ancestors"]
    assert app_index not in elements[sheet_index]["ancestors"]


def test_more_sheet_is_a_closed_modal_dialog_opened_by_a_labelled_tab():
    elements = layout_document()
    tab = elements[element_index(elements, "tab-more")]
    assert tab["tag"] == "button" and tab["attrs"].get("type") == "button"
    assert tab["attrs"].get("aria-haspopup") == "dialog"
    assert tab["attrs"].get("aria-expanded") == "false"
    assert tab["attrs"].get("aria-controls") == "more-sheet"
    sheet_index = element_index(elements, "more-sheet")
    assert "hidden" in elements[sheet_index]["attrs"]
    dialogs = [
        node for node in elements
        if sheet_index in node["ancestors"] and node["attrs"].get("role") == "dialog"
    ]
    assert len(dialogs) == 1
    assert dialogs[0]["attrs"].get("aria-modal") == "true"
    assert dialogs[0]["attrs"].get("aria-labelledby") == "more-title"
    element_index(elements, "more-title")
    closers = [
        node for node in elements
        if sheet_index in node["ancestors"] and "data-more-close" in node["attrs"]
    ]
    assert len(closers) >= 2, "backdrop and close button both dismiss the sheet"


def shell_source():
    return (STATIC / "shell.js").read_text(encoding="utf-8")


def function_block(source, signature):
    """Return the body of a function declared at two-space indentation inside shell.js."""
    start = source.index(signature)
    end = source.index("\n  }\n", start)
    return source[start:end]


def test_more_sheet_escape_closes_and_restores_focus():
    source = shell_source()
    listener = re.search(
        r'document\.addEventListener\("keydown", \(event\) => \{(?P<body>[\s\S]*?)\n  \}\);',
        source,
    )
    assert listener, "Missing document keydown listener"
    body = listener["body"]
    assert re.search(r'event\.key === "Escape" && sheet && !sheet\.hidden', body)
    assert re.search(r"\bcloseMore\(\)", body), "Escape must restore focus (the default)"
    close = function_block(source, "function closeMore(")
    assert "restore = true" in close
    assert "if (restore)" in close
    assert "returnFocus?.isConnected" in close
    assert "target?.focus()" in close
    # 不是 Escape 触发的关闭（点了某个入口、窗口变宽）不抢焦点。
    assert source.count("closeMore({ restore: false })") >= 3


def test_more_sheet_traps_tab_focus_inside_the_dialog():
    source = shell_source()
    trap = re.search(
        r'sheet\?\.addEventListener\("keydown", \(event\) => \{(?P<body>[\s\S]*?)\n  \}\);',
        source,
    )
    assert trap, "Missing focus trap listener"
    body = trap["body"]
    assert 'event.key !== "Tab"' in body
    assert re.search(r"event\.shiftKey[\s\S]*last\.focus\(\)", body)
    assert "first.focus()" in body
    assert "panel.focus()" in body


def test_opening_the_sheet_moves_focus_into_it_and_locks_background_scroll():
    source = shell_source()
    body = function_block(source, "function openMore(")
    assert "returnFocus = document.activeElement" in body
    assert 'classList.add("sheet-open")' in body
    assert "panel?.focus(" in body
    assert "desktop.matches" in body, "the sheet only exists on narrow screens"
    css = (STATIC / "shell.css").read_text(encoding="utf-8")
    assert re.search(r"body\.sheet-open\s*\{\s*overflow:\s*hidden;", css)


def shell_css_declarations():
    from test_cursor_fx_assets import css_declarations

    return list(css_declarations((STATIC / "shell.css").read_text(encoding="utf-8")))


def test_the_site_has_a_favicon_so_browsers_do_not_request_a_missing_one():
    # 没有 <link rel="icon"> 时，浏览器每次打开页面都会去请求 /favicon.ico，得到一个 404。
    elements = layout_document()
    icons = [item["attrs"] for item in elements if item["tag"] == "link" and item["attrs"].get("rel") == "icon"]
    assert icons == [{"rel": "icon", "type": "image/svg+xml", "href": "/static/favicon.svg"}]
    svg = (STATIC / "favicon.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg ") and 'viewBox="0 0 64 64"' in svg and "错" in svg
    assert "<script" not in svg and "href=" not in svg, "an icon must stay inert"


def test_account_menu_panel_opens_leftwards_from_the_avatar_on_narrow_screens():
    # 头像在顶栏最右边。如果面板从头像左缘向右展开（style.css 里 ≤780px 的旧规则），
    # 手机上 220px 宽的面板只能露出约 50px，名字、导出、退出登录都被切掉。
    from test_cursor_fx_assets import css_selectors

    anchored = {}
    for blocks, declaration in shell_css_declarations():
        selectors = css_selectors(blocks[-1])
        if not any(item.endswith('html[data-view="app"] .header .account-menu-panel') for item in selectors):
            continue
        assert any("max-width: 1023px" in block for block in blocks[:-1]), "the rule must cover phones and tablets"
        name, _, value = declaration.partition(":")
        anchored[name.strip()] = value.strip()
    assert anchored == {"right": "0", "left": "auto"}


def test_qixi_shell_surfaces_are_glass_with_an_opaque_fallback():
    from test_cursor_fx_assets import css_selectors

    glass = [
        (blocks, declaration) for blocks, declaration in shell_css_declarations()
        if re.fullmatch(r"background\s*:\s*var\(--glass-surface\)", declaration)
    ]
    assert glass, "Qixi needs a glass background for the sidebar, top bar and tab bar"
    selectors = [item for blocks, _ in glass for item in css_selectors(blocks[-1])]
    assert any('[data-theme="qixi"]' in item and ".app-sidebar" in item for item in selectors)
    # 磨砂模糊只给精细指针，触屏沿用不透明的纸面。
    for blocks, declaration in shell_css_declarations():
        if re.match(r"(?:-webkit-)?backdrop-filter\s*:", declaration):
            assert any("hover: hover" in block and "pointer: fine" in block for block in blocks)
            assert any(block.startswith("@supports") for block in blocks)


def test_shell_layers_stack_in_a_fixed_order_below_the_seal_layer():
    from test_cursor_fx_assets import css_declarations, css_selectors

    def levels(filename, pattern):
        found = set()
        stylesheet = (STATIC / filename).read_text(encoding="utf-8")
        for blocks, declaration in css_declarations(stylesheet):
            match = re.fullmatch(r"z-index\s*:\s*(\d+)", declaration)
            if blocks and match and any(re.search(pattern, item) for item in css_selectors(blocks[-1])):
                found.add(int(match[1]))
        return found

    found = {
        "header": levels("shell.css", r"\.header$"),
        "sidebar": levels("shell.css", r"\.app-sidebar$"),
        "tabbar": levels("shell.css", r"\.app-tabbar$"),
        "popover": levels("emoji.css", r"\.emoji-popover$"),
        "sheet": levels("shell.css", r"\.more-sheet$"),
        "focus": levels("focus.css", r"\.focus$"),
        "palette": levels("palette.css", r"\.palette$"),
        "seal": levels("style.css", r"#seal-layer$"),
    }
    for name, value in found.items():
        assert len(value) == 1, f"{name} needs exactly one explicit z-index, got {value}"
    order = [
        next(iter(found[name]))
        for name in ("header", "sidebar", "tabbar", "popover", "sheet", "focus", "palette", "seal")
    ]
    assert order == sorted(order) and len(set(order)) == len(order)


def test_qixi_welcome_page_rules_stay_separate_from_the_hero_layout():
    """曾经因为清理旧大厅样式，把欢迎页的顶栏和副标题规则并进了 hero 的选择器列表。"""
    from test_cursor_fx_assets import css_declarations, css_selectors

    stylesheet = (STATIC / "themes.css").read_text(encoding="utf-8")
    header = 'html[data-theme="qixi"][data-view="welcome"] .header'
    subtitle = 'html[data-theme="qixi"][data-view="welcome"] .brand-copy > p'
    hero = 'html[data-theme="qixi"] .intro .intro-hero'
    found = {}
    for blocks, declaration in css_declarations(stylesheet):
        if len(blocks) != 1:
            continue
        selectors = list(css_selectors(blocks[0]))
        for name, selector in (("header", header), ("subtitle", subtitle), ("hero", hero)):
            if selector in selectors:
                found.setdefault(name, []).append((selectors, declaration))
    assert any(selectors == [header] and declaration == "padding: 20px 0" for selectors, declaration in found["header"])
    assert any(selectors == [subtitle] and declaration == "display: none" for selectors, declaration in found["subtitle"])
    assert any(selectors == [hero] and declaration.startswith("padding: 80px 0 120px") for selectors, declaration in found["hero"])
    assert all(len(selectors) == 1 for entries in found.values() for selectors, _ in entries), "no merged selector lists"
