"""首访开场短片的静态契约：资源版本、关键 id、CSP 与令牌约束、动画只在允许时运行。"""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class Document(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.nodes.append({"tag": tag, "attrs": dict(attrs), "ancestors": [node["attrs"] for node in self.stack]})
        if tag not in VOID:
            self.stack.append(self.nodes[-1])

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()["tag"] == tag:
                break


@pytest.fixture(scope="module")
def index():
    parser = Document()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    return parser.nodes


@pytest.fixture(scope="module")
def script():
    return (STATIC / "intro-film.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stylesheet():
    return (STATIC / "intro-film.css").read_text(encoding="utf-8")


def css_declarations(source):
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
                assert blocks, "CSS 出现多余的闭合括号"
                blocks.pop()
    assert not blocks, "CSS 块未闭合"


def test_assets_are_versioned_deferred_and_loaded_before_the_router(index):
    styles = [node["attrs"]["href"] for node in index if node["tag"] == "link" and node["attrs"].get("rel") == "stylesheet"]
    scripts = [node["attrs"] for node in index if node["tag"] == "script"]
    sources = [attrs.get("src", "") for attrs in scripts]
    assert styles.count("/static/intro-film.css?v=1") == 1
    assert sources.count("/static/intro-film.js?v=1") == 1
    assert sources.count("/static/intro.js?v=13") == 1, "intro.js 改过，版本号要加一"
    assert sources.count("/static/app.js?v=59") == 1, "app.js 改过，版本号要加一"
    film = sources.index("/static/intro-film.js?v=1")
    assert "defer" in scripts[film]
    assert film < sources.index("/static/app.js?v=59")
    assert all(attrs.get("src") for attrs in scripts), "CSP 不允许内联脚本"


def test_replay_entry_is_a_quiet_button_in_the_welcome_footer(index):
    replay = [node for node in index if node["attrs"].get("id") == "intro-film-replay"]
    assert len(replay) == 1
    node = replay[0]
    assert node["tag"] == "button" and node["attrs"].get("type") == "button"
    assert "style" not in node["attrs"]
    assert not any(name.startswith("on") for name in node["attrs"])
    assert any(attrs.get("id") == "intro" for attrs in node["ancestors"]), "只在欢迎页上出现"
    assert any("legal-links" in attrs.get("class", "") for attrs in node["ancestors"])


def test_film_exposes_its_api_and_key_ids(script):
    assert "window.IntroFilm = {" in script
    for name in ("route(view)", "replay(", "close,", "isOpen:", "ownsOpening:"):
        assert name in script
    for element_id in (
        "intro-film", "intro-film-title", "intro-film-live", "intro-film-skip",
        "intro-film-continue", "intro-film-again", "intro-film-replay",
    ):
        assert f'"{element_id}"' in script or f'"#{element_id}"' in script
    assert 'setAttribute("role", "dialog")' in script
    assert 'setAttribute("aria-modal", "true")' in script
    assert 'setAttribute("aria-live", "polite")' in script


def test_film_script_is_csp_safe_and_never_reads_layout(script):
    for forbidden in (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
        "getBoundingClientRect", "offsetWidth", "offsetHeight", "getComputedStyle",
        "requestAnimationFrame", "setInterval",
    ):
        assert forbidden not in script, forbidden
    assert not re.search(r"\.style\.(?!setProperty\()", script), "只用 style.setProperty 写自定义属性"
    assert not re.search(r"setAttribute\(\s*['\"]style['\"]", script)
    assert "textContent" in script


def test_film_storage_access_is_always_guarded(script):
    lines = [line for line in script.splitlines() if "localStorage" in line]
    assert len(lines) == 2
    for line in lines:
        assert re.search(r"try\s*\{.*localStorage.*\}\s*catch", line), line


def test_film_only_plays_motion_when_the_user_allows_it(script):
    assert '"(prefers-reduced-motion: no-preference)"' in script
    assert re.search(r"motionAllowed\s*=\s*\(\)\s*=>[^\n]*matches\s*===\s*true", script), "matchMedia 不可用时按减少动态效果处理"
    assert "visibilitychange" in script


def test_film_styles_use_tokens_only(stylesheet):
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet), "颜色只用主题令牌"
    assert "!important" not in stylesheet
    assert "backdrop-filter" not in stylesheet
    assert not re.search(r"\b(?:rgba?|hsla?)\(\s*\d", stylesheet), "颜色只用主题令牌"
    for blocks, declaration in css_declarations(stylesheet):
        name, _, value = declaration.partition(":")
        if name.strip() in {"color", "background", "background-color", "border-color", "stroke", "fill", "outline-color"}:
            assert value.strip() in {"none", "transparent", "inherit"} or value.strip().startswith("var(--"), declaration


def test_film_animates_only_compositor_friendly_properties_behind_the_motion_query(stylesheet):
    keyframes = 0
    for blocks, declaration in css_declarations(stylesheet):
        name = declaration.partition(":")[0].strip()
        if name.startswith("animation") or name == "transition" or any(block.startswith("@keyframes") for block in blocks):
            assert blocks and blocks[0] == "@media (prefers-reduced-motion: no-preference)", (blocks, declaration)
        if any(block.startswith("@keyframes") for block in blocks):
            keyframes += 1
            assert name in {"opacity", "transform", "stroke-dashoffset"}, declaration
        if name == "transition":
            properties = {part.strip().split()[0] for part in declaration.partition(":")[2].split(",")}
            assert properties <= {"opacity", "visibility"}, declaration
    assert keyframes


def test_film_buttons_meet_the_touch_target_minimum(stylesheet):
    declarations = list(css_declarations(stylesheet))
    for selector in (".intro-film .film-skip", ".intro-film .film-actions button", ".intro-film .film-transcript summary"):
        heights = [d for blocks, d in declarations if blocks and blocks[-1] == selector and d.startswith("min-height")]
        assert heights, selector
        assert int(re.search(r"(\d+)px", heights[0]).group(1)) >= 40, selector


def test_film_stays_within_its_size_budget(script, stylesheet):
    markup = '<button type="button" id="intro-film-replay" class="intro-film-replay">重看开场</button>'
    total = sum(len(text.encode("utf-8")) for text in (script, stylesheet, markup))
    assert total <= 40 * 1024, total


def test_router_hands_the_route_to_the_film_after_the_session_check():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    start = source.index("function renderPageRoute()")
    body = source[start:source.index("\n}\n", start)]
    guard = body.index("if (!sessionReady) return;")
    visibility = body.index('$("#app").hidden')
    hook = body.index("window.IntroFilm?.route(next);")
    assert guard < visibility < hook


def test_old_welcome_opening_yields_to_the_film():
    source = (STATIC / "intro.js").read_text(encoding="utf-8")
    can_animate = source[source.index("const canAnimate"):source.index("const later")]
    assert "window.IntroFilm?.ownsOpening()" in can_animate
