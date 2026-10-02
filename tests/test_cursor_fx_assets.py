"""Static cursor effect integration checks without a browser or database."""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"
REACT_BITS_URL = "https://github.com/DavidHDev/react-bits"


class IndexDocument(HTMLParser):
    """Record tags in document order using the standard-library parser."""

    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs)})


@pytest.fixture(scope="module")
def index_document():
    document = IndexDocument()
    document.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    document.close()
    return document


@pytest.fixture(scope="module")
def cursor_source():
    path = STATIC / "cursor-fx.js"
    assert path.is_file()
    return path.read_text(encoding="utf-8")


def function_body(source, function_name):
    """Extract a named function using its declaration's indentation boundary."""
    declaration = re.search(
        rf"(?m)^(?P<indent>[ \t]*)(?:async\s+)?function\s+{re.escape(function_name)}\s*\([^)]*\)\s*\{{",
        source,
    )
    assert declaration, f"Missing {function_name} function"
    end = re.search(
        rf"(?m)^{re.escape(declaration['indent'])}\}}", source[declaration.end():]
    )
    assert end, f"Missing end of {function_name} function"
    return source[declaration.end():declaration.end() + end.start()]


def css_declarations(source):
    """Yield declarations with their enclosing selector and at-rule blocks."""
    source = re.sub(r"/\*[\s\S]*?\*/", "", source)
    blocks = []
    for match in re.finditer(r"([^{};]*)([{};])", source):
        text, delimiter = match.groups()
        text = text.strip()
        if delimiter == "{":
            blocks.append(text)
        else:
            if text:
                yield tuple(blocks), text
            if delimiter == "}":
                assert blocks, "Unexpected closing CSS brace"
                blocks.pop()
    assert not blocks, "Unclosed CSS block"


def css_selectors(selector_list):
    """Split a selector list without splitting arguments to :is() or :not()."""
    depth = 0
    start = 0
    for index, character in enumerate(selector_list):
        if character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            yield selector_list[start:index].strip()
            start = index + 1
    yield selector_list[start:].strip()


def test_visual_style_defines_red_pen_and_system_font_tokens():
    stylesheet = (STATIC / "style.css").read_text(encoding="utf-8")
    root_tokens = {}
    for blocks, declaration in css_declarations(stylesheet):
        if len(blocks) != 1 or ":root" not in css_selectors(blocks[0]):
            continue
        name, separator, value = declaration.partition(":")
        if separator and name.startswith("--"):
            root_tokens[name.strip()] = value.strip()
    assert root_tokens.get("--accent", "").lower() == "#c23a2b"
    assert root_tokens.get("--serif")
    assert root_tokens.get("--kai")


def test_lobby_has_no_decorative_tile_arrows(index_document):
    assert all(
        "tile-arrow" not in node["attrs"].get("class", "").split()
        for node in index_document.elements
    )


@pytest.mark.parametrize("filename", ["style.css", "intro.css"])
def test_visual_styles_do_not_use_previous_terracotta_accent(filename):
    assert "#bc5b3a" not in (STATIC / filename).read_text(encoding="utf-8").lower()


def test_cursor_effect_preserves_third_party_license(cursor_source):
    header = re.match(r"/\*[\s\S]*?\*/", cursor_source)
    assert header, "The cursor effect must start with its license notice"
    for required in ("David Haz", "Commons Clause", REACT_BITS_URL):
        assert required in header.group()


def test_cursor_script_is_deferred_versioned_and_loaded_before_app(index_document):
    scripts = [node["attrs"] for node in index_document.elements if node["tag"] == "script"]
    cursor_scripts = [
        (index, attrs) for index, attrs in enumerate(scripts)
        if attrs.get("src", "").split("?", 1)[0] == "/static/cursor-fx.js"
    ]
    app_scripts = [
        index for index, attrs in enumerate(scripts)
        if attrs.get("src", "").split("?", 1)[0] == "/static/app.js"
    ]
    assert len(cursor_scripts) == 1
    assert len(app_scripts) == 1
    cursor_index, attrs = cursor_scripts[0]
    assert "defer" in attrs
    assert re.fullmatch(r"/static/cursor-fx\.js\?v=\d+", attrs["src"])
    assert cursor_index < app_scripts[0]


def test_cursor_effect_has_no_remote_code_or_library_imports(cursor_source):
    assert re.findall(r"https?://[^\s*]+", cursor_source) == [REACT_BITS_URL]
    library_import = (
        r"(?:\bfrom\s*|\bimport\s*(?:\(\s*)?|\brequire\s*\(\s*)"
        r"[\"'](?:three|ogl)(?:/|[\"'])"
    )
    assert not re.search(library_import, cursor_source, flags=re.IGNORECASE)


@pytest.mark.parametrize(
    "required",
    [
        "prefers-reduced-motion", "webgl2", "aria-hidden", "pointer-events",
        "localStorage", "webglcontextlost",
    ],
)
def test_cursor_effect_contains_accessibility_and_lifecycle_guards(cursor_source, required):
    assert required in cursor_source


@pytest.mark.parametrize("function_name", ["showView", "signedOut"])
def test_view_and_signout_do_not_change_cursor_mode(function_name):
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert not re.search(
        r"\bsetMode\s*\(\s*([\"'])ghost\1\s*\)", source, flags=re.IGNORECASE
    )
    assert "CursorFX" not in function_body(source, function_name)


@pytest.mark.parametrize("button_id", ["fx-toggle", "fx-toggle-intro"])
def test_cursor_toggles_are_unique_native_buttons(index_document, button_id):
    buttons = [
        node for node in index_document.elements
        if node["attrs"].get("id") == button_id
    ]
    assert len(buttons) == 1
    assert buttons[0]["tag"] == "button"
    assert buttons[0]["attrs"].get("type") == "button"
    assert "link-button" in buttons[0]["attrs"].get("class", "").split()
    assert "fx-toggle" in buttons[0]["attrs"].get("class", "").split()


@pytest.mark.parametrize("filename", ["cursor-fx.js", "style.css"])
def test_removed_cursor_effect_has_no_asset_references(filename):
    assert "ghost" not in (STATIC / filename).read_text(encoding="utf-8").lower()


@pytest.mark.parametrize(
    "constant",
    [
        "RIPPLE_AMBIENT_FIRST_DELAY", "RIPPLE_AMBIENT_INTERVAL",
        "RIPPLE_AMBIENT_POWER", "RIPPLE_AMBIENT_OPACITY", "RIPPLE_AMBIENT_MARGIN",
        "RIPPLE_AMBIENT_IDLE_MS", "RIPPLE_TOUCH_MAX_DPR", "RIPPLE_TOUCH_MIN_FRAME_MS",
        "RIPPLE_AMBIENT_PROBES", "RIPPLE_MIN_OPACITY",
    ],
)
def test_touch_ripple_tuning_constants_are_present(cursor_source, constant):
    assert re.search(rf"\bconst\s+{constant}\s*=", cursor_source)


def test_ambient_ripple_checks_exposed_background(cursor_source):
    ripple = function_body(cursor_source, "createRipple")
    assert re.search(r"\belementFromPoint\s*\(", ripple)


def test_ripple_render_uses_opacity_cutoff_constant(cursor_source):
    render = function_body(cursor_source, "render")
    assert not re.search(r"(?<![\w.])0\.002(?![\w.])", render)
    assert re.search(r"\bopacity\s*<\s*RIPPLE_MIN_OPACITY\b", render)


def test_ripple_pause_and_destroy_clear_ambient_timeout(cursor_source):
    pause = function_body(cursor_source, "pause")
    destroy = function_body(cursor_source, "destroy")
    assert re.search(r"\bclearTimeout\s*\(", pause)
    assert re.search(r"\b(?:clearTimeout|pause)\s*\(", destroy)


@pytest.mark.parametrize(
    "filename",
    [
        "style.css", "intro.css", "shell.css", "overview.css", "activity.css",
        "emoji.css", "palette.css", "focus.css", "tags.css", "mastery.css", "clusters.css", "print.css",
    ],
)
def test_backdrop_blur_is_limited_to_fine_hover_pointers(filename):
    declarations = css_declarations((STATIC / filename).read_text(encoding="utf-8"))
    for blocks, declaration in declarations:
        if not re.match(r"(?:-webkit-)?backdrop-filter\s*:", declaration):
            continue
        media = [block for block in blocks if block.startswith("@media")]
        assert any(
            re.search(r"\(\s*hover\s*:\s*hover\s*\)", block)
            and re.search(r"\(\s*pointer\s*:\s*fine\s*\)", block)
            for block in media
        ), f"Touch devices must not receive {declaration} in {filename}: {blocks}"


@pytest.mark.parametrize("filename", ["style.css", "intro.css", "shell.css", "overview.css"])
def test_translucent_panels_require_running_ripple_and_color_mix_support(filename):
    declarations = css_declarations((STATIC / filename).read_text(encoding="utf-8"))
    translucent_rules = 0
    for blocks, declaration in declarations:
        if not ("color-mix(" in declaration and "transparent" in declaration):
            continue
        translucent_rules += 1
        assert any(
            block.startswith("@supports") and "color-mix(" in block
            for block in blocks
        ), f"Missing opaque color-mix fallback in {filename}: {blocks}"
        assert blocks and not blocks[-1].startswith("@")
        for selector in css_selectors(blocks[-1]):
            assert re.match(r"html\[data-cursor-fx=([\"'])ripple\1\]", selector), (
                f"Translucency must be gated on a running ripple in {filename}: {selector}"
            )
    assert translucent_rules, f"Missing ripple panel styles in {filename}"


def test_ripple_is_registered_and_has_background_style(cursor_source):
    assert 'registerEffect("ripple"' in cursor_source
    stylesheet = (STATIC / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.cursor-fx-canvas(?:--ripple)?\s*\{", stylesheet)


@pytest.mark.parametrize("forbidden", [".jpg", ".jpeg", ".png", ".webp", "Image(", "fetch("])
def test_cursor_effect_has_no_external_images_or_requests(cursor_source, forbidden):
    assert forbidden not in cursor_source


def test_cursor_documentation_describes_completed_ripple():
    source = (STATIC.parent / "README.md").read_text(encoding="utf-8")
    section = source.split("## 鼠标特效", 1)[1].split("\n## ", 1)[0]
    assert "留待第二步" not in section
    assert "尚未实现" not in section
    assert "RippleDistortion" in section
