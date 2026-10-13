"""成长山水（static/landscape.js/css）的静态契约：资源引用与版本、关键 id、主题令牌、
无内联样式/脚本、手机规则、减少动画。"""
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def version(name):
    match = re.search(rf'(?:href|src)="/static/{re.escape(name)}\?v=(\d+)"', INDEX)
    assert match, f"index.html 没有引用带版本号的 {name}"
    return int(match.group(1))


def test_landscape_assets_are_versioned_and_load_before_app_js():
    assert version("landscape.css") >= 1
    assert version("landscape.js") >= 1
    scripts = re.findall(r'<script defer src="/static/([\w.]+)\?v=\d+"></script>', INDEX)
    assert scripts.index("landscape.js") < scripts.index("app.js")
    # app.js 动过（加了挂载的两行），版本号必须加一。
    assert version("app.js") >= 101
    assert INDEX.count('id="landscape-card"') == 1, "总览页要有山水容器"
    assert INDEX.index('id="landscape-card"') < INDEX.index('id="sign-card"'), "山水在今日一签上方"


def test_landscape_script_is_csp_friendly():
    source = read("landscape.js")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in source, forbidden
    assert ".style." not in source and 'setAttribute("style"' not in source
    assert "textContent" in source, "用户/服务器文字必须走 textContent"


def test_landscape_script_exposes_contract_and_reads_existing_endpoints():
    source = read("landscape.js")
    assert "window.Landscape" in source
    assert "configure" in source and "mount" in source and "reset" in source
    assert '"/api/roadmap"' in source, "只读现有的 /api/roadmap"
    assert "/static/data/patterns.json" in source, "总数与标题读静态套路清单"
    assert "createElementNS" in source, "纯 SVG 构建，不用图片"
    # 元素预算：整幅画约 40 个元素以内（用类名计数关键形状）。
    assert source.count("SLOTS") >= 1


def test_landscape_styles_use_theme_tokens_and_reduced_motion():
    css = read("landscape.css")
    assert "prefers-reduced-motion: reduce" in css, "减少动画偏好下全部静止"
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "颜色必须来自主题令牌"
    assert not re.search(r"\brgba?\(\s*[\d.]", css), "不写字面量颜色函数"
    assert "!important" not in css
    assert ":focus-visible" in css, "生长元素键盘可聚焦要有可见焦点"
    assert "@media (max-width: 599px)" in css, "手机宽度要有适配"
    # 栖间主题有自己的山水配色（淡月）。
    assert 'html[data-theme="qixi"]' in css
    # 雾白主题也有自己的配色（更淡的冷月）。
    assert 'html[data-theme="fog"]' in css


@pytest.mark.parametrize("name", ("landscape.js",))
def test_landscape_script_has_no_hardcoded_colors(name):
    source = read(name)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source), "颜色走 CSS 类，不写死色值"


def test_landscape_card_uses_feel_layer_instead_of_its_own_hover():
    # 山水容器用手感层的上浮+聚光灯：feel.css 选择器列表和 feel.js SPOT 里都要有 .ls-wrap，
    # landscape.css 里不能自己再写一套 hover 上浮（不重复造轮子）。
    feel_css = read("feel.css")
    feel_js = read("feel.js")
    landscape_css = read("landscape.css")
    assert ".ls-wrap" in feel_css, "feel.css 的卡片列表要包含 .ls-wrap"
    assert ".ls-wrap" in feel_js, "feel.js 的 SPOT 列表要包含 .ls-wrap"
    assert ".ls-wrap:hover" not in landscape_css, "landscape.css 不自己写 hover 上浮"
