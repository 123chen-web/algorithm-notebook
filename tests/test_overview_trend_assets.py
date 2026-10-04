"""总览「趋势」区的静态契约：关键 id / 版本号 / 不写十六进制色 / 无 !important / 无内联样式 / 动效守卫。"""

import re
from pathlib import Path

import pytest

from test_contrast_tokens import css_declarations, css_selectors


STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "overview.css").read_text(encoding="utf-8")
JS = (STATIC / "overview.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
STYLE = (STATIC / "style.css").read_text(encoding="utf-8") + (STATIC / "themes.css").read_text(encoding="utf-8")
TREND_CSS = CSS[CSS.index("/* ---------- 趋势区"):]
HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}(?![\w-])")


def strip_comments(source):
    return re.sub(r"/\*[\s\S]*?\*/", "", source)


def version(path):
    match = re.search(rf'(?:src|href)="/static/{re.escape(path)}\?v=(\d+)"', HTML)
    assert match, f"index.html 没有引用 {path}"
    return int(match.group(1))


def test_trend_section_sits_between_the_tiles_and_the_columns():
    assert HTML.count('id="home-trend"') == 1
    tiles = HTML.index('class="ov-tiles"')
    trend = HTML.index('id="home-trend"')
    columns = HTML.index('class="ov-columns"')
    assert tiles < trend < columns, "趋势区在现有大卡下方、待复习列表上方"
    # 现有的卡片一个都没少。
    for ident in ("tile-review", "tile-streak", "tile-heatmap", "ov-due-list", "ov-weakness-card", "ov-groups-card", "ov-hot-card"):
        assert f'id="{ident}"' in HTML


def test_trend_shell_ids_are_built_by_the_script():
    for ident in (
        "home-trend-title", "home-trend-range", "home-trend-error", "home-trend-retry",
        "home-trend-tabs", "home-trend-panel", "home-trend-chart", "home-trend-note",
        "home-trend-how", "home-trend-data",
    ):
        assert ident in JS, ident
    assert 'role", "tablist"' in JS and 'role", "tab"' in JS and 'role", "tabpanel"' in JS
    assert '"aria-selected"' in JS
    assert '"home-trend-days"' in JS


def test_changed_static_files_have_bumped_versions():
    assert version("overview.css") >= 3
    assert version("overview.js") >= 3
    assert version("app.js") >= 59


def test_home_loading_wires_the_trend_with_a_late_response_guard():
    wiring = re.search(r"window\.Overview\?\.loadTrend\(\{[^\n]*\}\);", APP)
    assert wiring, "loadHome() 里要调用 Overview.loadTrend"
    assert "sessionEpoch" in wiring.group(0) and "isCurrent" in wiring.group(0)
    assert re.search(r"window\.Overview\s*=\s*\{[^}]*\brenderTrend\b[^}]*\btrendModel\b[^}]*\bloadTrend\b", JS)


def test_trend_assets_follow_the_styling_rules():
    css = strip_comments(CSS)
    js = strip_comments(JS)
    assert not HEX_COLOR.search(css), "overview.css 里不能写十六进制色，用主题令牌"
    assert not HEX_COLOR.search(js), "overview.js 里不能写十六进制色，用主题令牌"
    assert "!important" not in css
    assert not re.search(r"innerHTML|insertAdjacentHTML|outerHTML|document\.write", js), "用户可控文字一律 textContent"
    assert not re.search(r"""setAttribute\(\s*["']style["']|\.cssText|\bstyle\s*=\s*["']""", js), "CSP 不允许内联样式"
    assert 'style="' not in HTML[HTML.index('id="home-trend"'):HTML.index('class="ov-columns"')]


def test_trend_css_only_uses_defined_theme_tokens():
    used = set(re.findall(r"var\((--[\w-]+)", strip_comments(TREND_CSS)))
    defined = set(re.findall(r"(--[\w-]+)\s*:", STYLE)) | set(re.findall(r"(--[\w-]+)\s*:", TREND_CSS))
    assert used <= defined, f"未定义的令牌：{sorted(used - defined)}"


def test_trend_animation_only_when_motion_is_allowed():
    for blocks, declaration in css_declarations(CSS):
        name = declaration.partition(":")[0].strip()
        if name not in {"animation", "animation-name", "transition"} and not declaration.startswith("@keyframes"):
            continue
        if not blocks or not any(".ov-trend" in block or "ov-trend-pulse" in block for block in blocks):
            continue
        assert any("prefers-reduced-motion: no-preference" in block for block in blocks), (blocks, declaration)
    assert "animation: ov-trend-pulse" in CSS


def test_trend_touch_targets_and_phone_scroll_snap():
    declarations = list(css_declarations(CSS))

    def rule(selector, prop, *, media=None):
        return [
            body.partition(":")[2].strip()
            for blocks, body in declarations
            if blocks and selector in set(css_selectors(blocks[-1])) and body.partition(":")[0].strip() == prop
            and (media is None or any(media in block for block in blocks[:-1]))
        ]

    assert rule(".ov-trend-range-button", "min-height") == ["40px", "44px"]  # 触屏再放大到 44px
    assert rule(".ov-trend-how summary", "min-height") == ["40px", "44px"]
    assert int(rule(".ov-trend-tab", "min-height")[0].rstrip("px")) >= 40
    assert rule(".ov-trend-tabs", "scroll-snap-type", media="max-width: 599px") == ["x mandatory"]
    assert rule(".ov-trend-tabs", "overflow-x", media="max-width: 599px") == ["auto"]
    assert rule(".ov-trend-tab", "scroll-snap-align", media="max-width: 599px") == ["start"]


def test_svg_charts_scale_to_the_container():
    assert rule_exists(".ov-chart", "width", "100%")
    assert rule_exists(".ov-chart", "height", "auto")


def rule_exists(selector, prop, value):
    return any(
        blocks and selector in set(css_selectors(blocks[-1]))
        and body.partition(":")[0].strip() == prop and body.partition(":")[2].strip() == value
        for blocks, body in css_declarations(CSS)
    )
