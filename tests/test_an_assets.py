"""AN 的静态契约：资源引用与版本、关键 id、主题令牌、无内联样式 / 脚本、手机与触屏规则。"""
from pathlib import Path
import re

import pytest

from test_contrast_tokens import css_declarations


STATIC = Path(__file__).resolve().parents[1] / "static"
AN_STYLES = ("practice.css", "mastery.css", "clusters.css")
AN_SCRIPTS = ("practice.js", "mastery.js", "clusters.js")


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def index_html():
    return read("index.html")


def version(index_html, name):
    match = re.search(rf'(?:href|src)="/static/{re.escape(name)}\?v=(\d+)"', index_html)
    assert match, f"index.html 没有引用带版本号的 {name}"
    return int(match.group(1))


def test_assets_are_versioned_and_bumped_from_the_baseline(index_html):
    # 基线（origin/main f3b0b08）：mastery.css 2 / mastery.js 2 / clusters.css 2 / clusters.js 4 / app.js 58；practice.* 是新文件。
    assert version(index_html, "mastery.css") >= 3
    assert version(index_html, "mastery.js") >= 3
    assert version(index_html, "clusters.css") >= 3
    assert version(index_html, "clusters.js") >= 5
    assert version(index_html, "app.js") >= 59
    assert version(index_html, "practice.css") >= 1
    assert version(index_html, "practice.js") >= 1


def test_practice_module_loads_before_everything_that_uses_it(index_html):
    scripts = re.findall(r'<script defer src="/static/([\w.]+)\?v=\d+"></script>', index_html)
    for user in ("mastery.js", "clusters.js", "app.js"):
        assert scripts.index("practice.js") < scripts.index(user)
    assert index_html.count("/static/practice.js") == 1 and index_html.count("/static/practice.css") == 1


def test_page_structure_ids(index_html):
    for name in ("mastery-overview-card", "mastery-overview", "mastery-select-note", "mastery-chart-hint",
                 "mastery-chart-card", "mastery-chart", "mastery-chart-summary", "mastery-table", "mastery-alert"):
        assert index_html.count(f'id="{name}"') == 1, name
    # 图例就是列表里被选中的行：不再单独放一排芯片，也不再有分区卡片网格。
    assert 'id="mastery-legend"' not in index_html and 'id="mastery-zones"' not in index_html
    note = re.search(r'<p id="mastery-select-note"[^>]*>', index_html)[0]
    assert 'role="status"' in note and 'aria-live="polite"' in note
    overview = re.search(r'<ul id="mastery-overview"[^>]*>', index_html)[0]
    assert 'role="list"' in overview
    assert index_html.index('id="mastery-overview-card"') < index_html.index('id="mastery-chart-card"'), "列表在主图上方"


@pytest.mark.parametrize("name", AN_STYLES)
def test_stylesheets_follow_the_project_rules(name):
    css = read(name)
    assert "!important" not in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "颜色必须来自主题令牌"
    assert "hsl(" not in css and not re.search(r"rgba?\(\s*\d", css), "不写字面量颜色函数"
    # backdrop-filter 只能出现在 (hover: hover) and (pointer: fine) 块里。
    assert "backdrop-filter" not in css
    assert not re.search(r'style\s*=\s*"', css)


@pytest.mark.parametrize("name", AN_SCRIPTS)
def test_scripts_never_build_html_or_inline_styles(name):
    source = read(name)
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert forbidden not in source, (name, forbidden)
    assert 'setAttribute("style"' not in source and ".style.cssText" not in source
    assert "item.textContent = text" in source or name == "practice.js" or "textContent" in source


def test_practice_button_has_touch_target_and_focus_ring():
    css = read("practice.css")
    assert re.search(r"\.practice-now-button \{[^}]*min-height: 44px", css)
    assert re.search(r"\.practice-now-link \{[^}]*min-height: 44px", css)
    assert "outline: 3px solid var(--accent)" in css


def test_mastery_rows_work_on_phones_and_touch_screens():
    css = read("mastery.css")
    phone = re.search(r"@media \(max-width: 599px\) \{(.*?)\n\}", css, re.S)
    assert phone and "grid-template-areas" in phone.group(1), "手机上每行改成两列堆叠，不横向滚动"
    assert 'grid-template-columns: minmax(0, 1fr) auto;' in phone.group(1)
    coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", css, re.S)
    assert coarse and ".mastery-row-main" in coarse.group(1) and "min-height: 44px" in coarse.group(1)
    # 名字过长时截断而不是撑破一行。
    assert re.search(r"\.mastery-row-zone \{[^}]*text-overflow: ellipsis", css)
    assert re.search(r"\.mastery-row-main \{[^}]*min-height: 52px", css)
    # 每个等级都有自己的令牌配色；没有数据的行单独一种。
    for tier in ("is-new", "is-familiar", "is-proficient", "is-mastered", "is-none"):
        assert f".mastery-tier.{tier} " in css


def test_hover_effects_only_for_fine_pointers():
    css = read("mastery.css")
    block = re.search(r"@media \(hover: hover\) and \(pointer: fine\) \{(.*?)\n\}", css, re.S)
    assert block and ".mastery-row-main:hover" in block.group(1)
    assert ".mastery-row-main:hover" not in css.replace(block.group(0), "")


def test_trend_badge_is_keyboard_reachable_and_not_color_only():
    css = read("clusters.css")
    script = read("clusters.js")
    assert ".clusters-trend:focus-visible" in css
    assert re.search(r"\.clusters-trend:is\(:hover, :focus-visible\)::after \{[^}]*display: block", css)
    assert re.search(r"\.clusters-trend::after \{[^}]*content: attr\(data-tip\)", css)
    assert "badge.tabIndex = 0" in script and 'badge.title = trend.text' in script
    assert 'badge.setAttribute("aria-label"' in script


def test_thresholds_are_named_constants():
    mastery = read("mastery.js")
    assert "const TIER_BOUNDS = Object.freeze({ familiar: 40, proficient: 70, mastered: 90 });" in mastery
    assert "const MAX_SELECTED = 4;" in mastery and "const MAX_PRIORITY = 2;" in mastery
    clusters = read("clusters.js")
    assert "windowDays: 14, minRecent: 3, failBelow: 3, improvePoints: 20, repeatPercent: 40" in clusters
    assert "const LIMIT = 5;" in read("practice.js")


def test_clusters_page_uses_only_existing_endpoints():
    script = read("clusters.js")
    # 徽标只多读现有的 GET /api/mistakes/{id}（拿复习记录），没有新增接口。
    assert sorted(set(re.findall(r'api\(([^)]*)\)', script))) == sorted([
        '"/api/insights/clusters"', '"/api/me"', '"/api/insights/clusters", { method: "POST" }',
        "`/api/mistakes/${id}`",
    ])
