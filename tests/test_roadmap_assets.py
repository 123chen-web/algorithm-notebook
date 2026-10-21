"""学习路线的静态契约：入口、页面骨架、脚本顺序、CSP 兼容（无 innerHTML / 无行内样式）、
样式只用主题令牌。套路卡片与渲染逻辑必须是独立页和主站共用的同一份文件。"""
import re
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
PATTERNS_HTML = (STATIC / "patterns.html").read_text(encoding="utf-8")
ROADMAP_JS = (STATIC / "roadmap.js").read_text(encoding="utf-8")
RENDER_JS = (STATIC / "pattern-render.js").read_text(encoding="utf-8")
PICKER_JS = (STATIC / "pattern-picker.js").read_text(encoding="utf-8")
PATTERNS_JS = (STATIC / "patterns.js").read_text(encoding="utf-8")
CSS_FILES = {name: (STATIC / name).read_text(encoding="utf-8")
             for name in ("patterns.css", "roadmap.css")}


def script_sources():
    return re.findall(r'<script defer src="(/static/[^"?]+)\?v=(\d+)"', INDEX)


def test_sidebar_and_mobile_menus_have_a_roadmap_entry():
    more_group = INDEX[INDEX.index('id="sidebar-more-list"'):INDEX.index("</nav>", INDEX.index('id="sidebar-more-list"'))]
    assert 'data-view="roadmap"' in more_group, "学习路线放在侧栏「更多」里"
    assert "学习路线" in more_group
    more = INDEX[INDEX.index('class="more-item" data-view="home"'):]
    assert 'data-view="roadmap"' in more, "手机「更多」里也要有入口"
    for entry in re.findall(r'<button type="button" class="(?:nav-item|more-item)" data-view="roadmap">.*?</button>', INDEX, re.S):
        assert 'viewBox="0 0 24 24"' in entry, "图标沿用现有内联 SVG 风格"


def test_the_page_skeleton_has_every_node_the_script_touches():
    for node_id in (
        "roadmap-page", "roadmap-status", "roadmap-retry", "roadmap-summary-card",
        "roadmap-summary-text", "roadmap-progress", "roadmap-cats", "roadmap-weak",
        "roadmap-cat", "roadmap-level", "roadmap-state", "roadmap-q", "roadmap-count",
        "roadmap-list",
    ):
        assert f'id="{node_id}"' in INDEX, node_id
    assert 'aria-busy="false"' in INDEX[INDEX.index('id="roadmap-page"'):INDEX.index('id="mastery-page"')]
    # 进度条要有无障碍名字/描述，不能只有一根光秃秃的进度条。
    assert 'aria-describedby="roadmap-summary-text"' in INDEX


def test_new_record_form_has_a_placeholder_for_the_pattern_picker():
    assert 'id="problem-patterns"' in INDEX


def test_scripts_load_in_dependency_order_before_app():
    sources = [src for src, _version in script_sources()]
    pos = {name: sources.index(f"/static/{name}") for name in (
        "pattern-render.js", "pattern-picker.js", "roadmap.js", "app.js", "mastery.js")}
    assert pos["pattern-render.js"] < pos["pattern-picker.js"] < pos["roadmap.js"] < pos["app.js"]
    for name in ("patterns.css", "roadmap.css"):
        assert f'href="/static/{name}?v=' in INDEX


def test_the_standalone_page_shares_the_same_render_file():
    sources = re.findall(r'<script defer src="(/static/[^"?]+)\?v=(\d+)"', PATTERNS_HTML)
    names = [name for name, _v in sources]
    assert names.index("/static/pattern-render.js") < names.index("/static/patterns.js")
    # 独立页自己不再重复实现卡片渲染。
    assert "function renderCard" not in PATTERNS_JS
    assert "PatternRender" in PATTERNS_JS


def test_every_asset_the_pages_link_exists():
    for path in re.findall(r'(?:href|src)="(/static/[^"?#]+)', INDEX + PATTERNS_HTML):
        assert (ROOT / path.lstrip("/")).exists(), path


@pytest.mark.parametrize("name", ["roadmap.js", "pattern-render.js", "pattern-picker.js"])
def test_scripts_are_csp_safe(name):
    source = (STATIC / name).read_text(encoding="utf-8")
    assert "innerHTML" not in source and "outerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "eval(" not in source and "document.write" not in source
    assert not re.search(r"\.style\.\w+\s*=", source)
    assert "localStorage" not in source and "sessionStorage" not in source


@pytest.mark.parametrize("name", ["patterns.css", "roadmap.css"])
def test_stylesheets_use_tokens_only_and_work_on_small_screens(name):
    css = CSS_FILES[name]
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "只用主题令牌，不写十六进制颜色"
    assert "!important" not in css
    assert "max-width: 520px" in css


def test_reduced_motion_is_respected():
    assert "prefers-reduced-motion: reduce" in CSS_FILES["roadmap.css"]


def test_dynamic_text_is_never_concatenated_as_markup():
    for source in (ROADMAP_JS, RENDER_JS, PICKER_JS):
        assert "createTextNode" in source or "textContent" in source
        assert "insertAdjacentHTML" not in source
    # 三态按钮是可聚焦的原生 button，靠 aria-pressed 表达状态。
    assert 'aria-pressed' in RENDER_JS
    assert '.pattern-state' in CSS_FILES["patterns.css"]


def test_roadmap_only_talks_to_its_own_endpoints():
    urls = set(re.findall(r'"/api/roadmap[^"`]*"', ROADMAP_JS))
    # 待分类箱入口读未分类数（?limit=1 只取总数）。
    assert urls == {'"/api/roadmap"', '"/api/roadmap/inbox?limit=1&offset=0"'}
    # 套路清单是静态资源，和登录无关。
    assert re.findall(r'fetch\("([^"]+)"', ROADMAP_JS) == ["/static/data/patterns.json"]
    assert re.findall(r'fetch\("([^"]+)"', PICKER_JS) == ["/static/data/patterns.json"]
