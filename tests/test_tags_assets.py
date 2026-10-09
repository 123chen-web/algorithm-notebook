"""错因标签前端的静态契约：引用、接口、无障碍、触屏点击范围、样式约束。"""
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def index_html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script():
    return (STATIC / "tags.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stylesheet():
    return (STATIC / "tags.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_assets_are_versioned_and_script_loads_before_the_app(index_html):
    assert re.search(r'<link rel="stylesheet" href="/static/tags\.css\?v=\d+">', index_html)
    tags = re.search(r'<script defer src="/static/tags\.js\?v=\d+"></script>', index_html)
    app = re.search(r'<script defer src="/static/app\.js\?v=\d+"></script>', index_html)
    assert tags and app and tags.start() < app.start()


def test_markup_has_the_sidebar_mount_and_a_hidden_tag_filter(index_html):
    assert 'id="sidebar-tags"' in index_html
    select = re.search(r'<label class="zone-filter" hidden>\s*标签\s*<select id="tag-filter">', index_html)
    assert select, "the tag filter appears only once the user has tags"


def test_script_never_builds_html_from_strings(script):
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script


def test_editor_saves_the_whole_tag_list_through_the_tags_endpoint(script):
    assert "/api/mistakes/${item.id}/tags" in script
    assert 'method: "PUT"' in script and '"X-CSRF-Protection": "1"' in script
    assert "body: JSON.stringify({ tags: next })" in script
    assert "if (saving) return;" in script


def test_editor_is_labelled_and_announces_changes(script):
    assert 'label.htmlFor = inputId' in script
    assert 'status.setAttribute("aria-live", "polite")' in script
    assert 'remove.setAttribute("aria-label", `移除标签 ${tag}`)' in script
    assert 'setAttribute("aria-pressed"' in script


def test_limits_come_from_the_server_not_from_the_script(script):
    assert "state.limits.length" in script and "state.limits.per_mistake" in script
    assert not re.search(r"maxLength = \d+;", script)


def test_filters_use_the_records_filter_event_and_the_list_query(script, app_source):
    assert 'new CustomEvent("records:filter", { detail: { tag: same ? "" : item.tag } })' in script
    assert "&tag=${encodeURIComponent(tagParam)}" in app_source
    handler = re.search(r'document\.addEventListener\("records:filter", \(event\) => \{(?P<body>[\s\S]*?)\n\}\);', app_source)
    assert handler and "tagSelect.value = tag;" in handler["body"]


def test_detail_and_cards_use_the_widgets_and_stay_in_sync(app_source):
    assert "window.TagEditor.render(item)" in app_source
    # 详情仍逐条编辑；列表题卡改为展示服务端合并后的全题标签。
    cards = (STATIC / "problem-cards.js").read_text(encoding="utf-8")
    assert "window.ProblemCards.card(group" in app_source
    assert "metadata.problem_tags" in cards
    assert 'node("span", "problem-chip", tag)' in cards
    handler = re.search(r'document\.addEventListener\("mistake:tags-changed", \(event\) => \{(?P<body>[\s\S]*?)\n\}\);', app_source)
    assert handler and "rvfRenderCards(" in handler["body"]
    assert "rvfRefreshProblem(item)" in handler["body"]


def test_stylesheet_follows_the_project_rules(stylesheet):
    assert "!important" not in stylesheet
    assert "backdrop-filter" not in stylesheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet), "colors must come from theme tokens"
    # 移除按钮看上去 24px，但点击范围扩到 44px。
    assert re.search(r"\.tag-remove::after \{ content: \"\"; position: absolute; inset: -10px; \}", stylesheet)
    coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", stylesheet, re.S)
    assert coarse and coarse.group(1).count("min-height: 44px") >= 3
    # 卡片里的 span 默认块级，要显式压回来。
    assert ".record-button .record-tags { display: flex;" in stylesheet
