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
    assert styles.count("/static/intro-film.css?v=2") == 1, "intro-film.css 改过，版本号要加一"
    assert sources.count("/static/intro-film.js?v=2") == 1, "intro-film.js 改过，版本号要加一"
    assert sources.count("/static/intro-glyphs.js?v=1") == 1
    assert sources.count("/static/intro.js?v=13") == 1, "intro.js 改过，版本号要加一"
    assert sources.count("/static/app.js?v=74") == 1, "app.js 改过，版本号要加一"
    film = sources.index("/static/intro-film.js?v=2")
    glyphs = sources.index("/static/intro-glyphs.js?v=1")
    assert "defer" in scripts[film] and "defer" in scripts[glyphs]
    assert glyphs < film < sources.index("/static/app.js?v=74"), "字形数据要在开场脚本之前加载"
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


def function_source(source, name):
    start = source.index(f"  function {name}(")
    return source[start:source.index("\n  }\n", start)]


def test_film_script_is_csp_safe_and_reads_layout_only_once_per_hand_off(script):
    for forbidden in (
        "innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function",
        "offsetWidth", "offsetHeight", "getComputedStyle", "requestAnimationFrame", "setInterval",
        "startViewTransition",
    ):
        assert forbidden not in script, forbidden
    fly = function_source(script, "fly")
    outside = script.replace(fly, "")
    assert "getBoundingClientRect" not in outside, "布局只在共享元素交接开始时量一次"
    assert fly.count("getBoundingClientRect()") == 3
    assert ".animate(" in fly and "for (" not in fly.split(".animate(")[0].split("const towards")[1]
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


def test_timing_lives_in_one_named_table_at_the_top_of_the_script(script, stylesheet):
    head = script[:script.index("const STORAGE_KEY")]
    assert re.search(r"const TIMING = Object\.freeze\(\{", head), "时间参数集中在文件顶部的 TIMING 表"
    assert 'const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)"' in head
    assert 'const EASE_MOVE = "cubic-bezier(0.4, 0, 0.2, 1)"' in head
    for field in ("overlap:", "stagger:", "rise:", "morph:", "ctaAt:", "announce:", "enter:", "hold:", "exit:"):
        assert field in head, field
    rest = script[script.index("const STORAGE_KEY"):]
    assert "cubic-bezier" not in rest, "缓动只在顶部定义一次"
    assert not re.search(r"\}\s*,\s*\d+\s*\)", rest), "setTimeout 的时长不散落在代码里，只能来自 TIMING"
    assert "TIMING.announce" in rest
    assert "cubic-bezier" not in stylesheet
    timed = 0
    for _, declaration in css_declarations(stylesheet):
        name, _, value = declaration.partition(":")
        if name.strip().startswith("animation") or name.strip() == "transition":
            timed += 1
            literal = re.sub(r"var\(--[\w-]+\)", "", value)
            assert not re.search(r"(?<![\w-])\d*\.?\d+m?s\b", literal), f"CSS 里的时长要来自 TIMING 表：{declaration}"
            assert not re.search(r"\b(?:ease|ease-in|ease-out|ease-in-out|linear|steps)\b", literal), f"缓动只用 --ease-out / --ease-move：{declaration}"
    assert timed


def test_film_animates_only_compositor_friendly_properties_behind_the_motion_query(stylesheet):
    keyframes = 0
    for blocks, declaration in css_declarations(stylesheet):
        name = declaration.partition(":")[0].strip()
        if name.startswith("animation") or name == "transition" or any(block.startswith("@keyframes") for block in blocks):
            assert blocks and blocks[0] == "@media (prefers-reduced-motion: no-preference)", (blocks, declaration)
        if any(block.startswith("@keyframes") for block in blocks):
            keyframes += 1
            assert name in {"opacity", "transform", "stroke-dashoffset"}, declaration
            assert "rotate" not in declaration, "不旋转"
            rise = re.search(r"translateY\((-?\d+)px\)", declaration)
            assert not rise or 8 <= abs(int(rise.group(1))) <= 12, f"文字上浮 8–12px：{declaration}"
        if name == "transition":
            properties = {part.strip().split()[0] for part in declaration.partition(":")[2].split(",")}
            assert properties <= {"opacity", "visibility"}, declaration
    assert keyframes


def test_film_buttons_meet_the_touch_target_minimum(stylesheet):
    declarations = list(css_declarations(stylesheet))
    for selector in (".film-skip", ".film-actions button", ".film-transcript summary"):
        heights = [d for blocks, d in declarations if blocks and blocks[-1] == selector and d.startswith("min-height")]
        assert heights, selector
        assert int(re.search(r"(\d+)px", heights[0]).group(1)) >= 40, selector


def test_film_stays_within_its_size_budget(script, stylesheet):
    glyphs = (STATIC / "intro-glyphs.js").read_text(encoding="utf-8")
    markup = '<button type="button" id="intro-film-replay" class="intro-film-replay">重看开场</button>'
    total = sum(len(text.encode("utf-8")) for text in (script, stylesheet, glyphs, markup))
    assert total <= 40 * 1024, total


def test_glyph_outlines_are_small_path_data_extracted_from_the_bundled_font():
    path = STATIC / "intro-glyphs.js"
    source = path.read_text(encoding="utf-8")
    assert path.stat().st_size <= 15 * 1024
    assert "NotoSansSC-Regular-subset.otf" in source and "SIL Open Font License" in source
    assert (STATIC.parent / "assets" / "fonts" / "make_intro_glyphs.py").exists(), "生成脚本随仓库提交"
    match = re.search(r"window\.IntroGlyphs = Object\.freeze\((\{.*\})\);", source)
    assert match
    import json
    data = json.loads(match.group(1))
    assert data["text"] == "欧叶OY"
    assert [glyph["char"] for glyph in data["glyphs"]] == list("欧叶OY")
    assert re.fullmatch(r"0 -?\d+ \d+ \d+", data["viewBox"])
    for glyph in data["glyphs"]:
        assert glyph["contours"]
        for contour in glyph["contours"]:
            assert re.fullmatch(r"M[-\d ]+(?:[LCQ][-\d ]+)*Z", contour), contour[:40]
    assert not list(STATIC.glob("*.otf")) and not list(STATIC.glob("*.ttf")) and not list(STATIC.glob("*.woff*"))


def test_reduced_motion_shows_the_complete_wordmark(stylesheet):
    """默认（无动画）样式就是最终画面：墨色填满、描线层隐藏、遮罩不偏移。"""
    base = [(blocks, declaration) for blocks, declaration in css_declarations(stylesheet) if not any(b.startswith("@media") for b in blocks)]
    assert ((".film-strokes",), "opacity: 0") in base
    assert ((".film-ink path",), "fill: var(--ink)") in base
    def property_of(declaration):
        return declaration.partition(":")[0].strip()
    assert not any(blocks[-1].startswith(".film-ink") and property_of(declaration) in {"opacity", "transform"} for blocks, declaration in base)
    assert not any(blocks[-1] == ".film-glyphs" and property_of(declaration) == "opacity" for blocks, declaration in base)


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
