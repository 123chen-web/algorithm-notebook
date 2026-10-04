"""掌握度趋势页的静态契约：入口、引用、无障碍、颜色令牌、触屏点击范围。"""
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def index_html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script():
    return (STATIC / "mastery.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stylesheet():
    return (STATIC / "mastery.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_assets_are_versioned_and_script_loads_before_the_app(index_html):
    assert re.search(r'<link rel="stylesheet" href="/static/mastery\.css\?v=\d+">', index_html)
    mastery = re.search(r'<script defer src="/static/mastery\.js\?v=\d+"></script>', index_html)
    app = re.search(r'<script defer src="/static/app\.js\?v=\d+"></script>', index_html)
    assert mastery and app and mastery.start() < app.start()


def test_page_sidebar_entry_and_more_sheet_entry_exist(index_html):
    assert re.search(r'<section id="mastery-page"[^>]*aria-labelledby="mastery-title"[^>]*\bhidden\b', index_html)
    assert re.search(r'class="nav-item" data-view="mastery"', index_html)
    assert re.search(r'class="more-item" data-view="mastery"', index_html)
    # 提醒卡在总览的信息栏里，默认不显示。
    assert re.search(r'<section id="ov-fading-card"[^>]*\bhidden\b', index_html)


def test_the_chart_has_a_text_alternative_and_keyboard_support(index_html, script, stylesheet):
    assert 'id="mastery-chart" class="mastery-chart" tabindex="0" role="group"' in index_html
    assert 'id="mastery-chart-summary"' in index_html and 'aria-live="polite"' in index_html
    assert '<summary>查看数据表</summary>' in index_html
    # 数据表比手机屏幕宽时在容器里横向滚动：容器自己要能被键盘聚焦，否则键盘用户滚不动。
    assert re.search(r'<div id="mastery-table" tabindex="0" role="region" aria-label="[^"]+"></div>', index_html)
    assert "#mastery-table:focus-visible" in stylesheet
    for key in ("ArrowLeft", "ArrowRight", "Home", "End", "Escape"):
        assert f'"{key}"' in script
    assert 'role: "img"' in script and "aria-label" in script
    assert 'scope: "col"' in script and 'scope: "row"' in script


def test_series_are_told_apart_by_more_than_color(script, stylesheet):
    assert 'const DASHES = ["", "8 4", "2 4", "10 3 2 3"' in script
    assert '"stroke-dasharray": DASHES[seriesIndex(entry.zone)]' in script
    assert 'mastery-swatch[data-dash="1"]' in stylesheet
    # 图例就是列表里被选中的行：选中状态不只靠颜色（aria-pressed + 线型样例 + 左侧竖线）。
    assert re.search(r"\.mastery-row \.mastery-swatch \{[^}]*visibility: hidden", stylesheet)
    assert re.search(r"\.mastery-row\.is-selected \.mastery-swatch \{[^}]*visibility: visible", stylesheet)
    assert 'setAttribute("aria-pressed", String(on))' in script


def test_script_never_builds_html_from_strings(script):
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script


def test_view_switching_loads_refreshes_and_resets_the_page(app_source):
    assert '$("#mastery-page").hidden = view !== "mastery";' in app_source
    assert 'else if (view === "mastery") await window.Mastery.load();' in app_source
    assert re.search(r'if \(view === "mastery"\) \{\s*message\(\);\s*await window\.Mastery\.load\(\);', app_source)


def test_actions_use_existing_entry_points(script):
    assert "window.FocusReview.start({ zone: alert.zone })" in script
    assert "window.FocusReview.start({ zone: entry.zone })" in script
    assert 'new CustomEvent("records:filter", { detail: { zone: ' in script


def test_stylesheet_follows_the_project_rules(stylesheet):
    assert "!important" not in stylesheet
    assert "backdrop-filter" not in stylesheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet), "colors must come from theme tokens"
    assert "var(--zone-color" in stylesheet
    coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", stylesheet, re.S)
    assert coarse and coarse.group(1).count("min-height: 44px") >= 2


def test_every_zone_has_a_fixed_color_from_the_shared_series_tokens():
    shell = (STATIC / "shell.css").read_text(encoding="utf-8")
    style = (STATIC / "style.css").read_text(encoding="utf-8")
    zones = ["算法", "前端", "后端", "数据库", "系统设计", "高等数学", "线性代数", "概率统计"]
    for position, zone in enumerate(zones, start=1):
        assert f'[data-zone="{zone}"] {{ --zone-color: var(--series-{position}); }}' in shell
        assert re.search(rf"--series-{position}: #[0-9a-fA-F]{{6}};", style)
