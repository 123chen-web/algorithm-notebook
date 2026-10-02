"""表情选择面板的静态契约：引用方式、无障碍、不动 innerHTML、样式约束。"""
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def index_html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script():
    return (STATIC / "emoji.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stylesheet():
    return (STATIC / "emoji.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_assets_are_versioned_and_script_loads_before_the_app(index_html):
    assert re.search(r'<link rel="stylesheet" href="/static/emoji\.css\?v=\d+">', index_html)
    script_match = re.search(r'<script defer src="/static/emoji\.js\?v=\d+"></script>', index_html)
    app_match = re.search(r'<script defer src="/static/app\.js\?v=\d+"></script>', index_html)
    assert script_match and app_match
    assert script_match.start() < app_match.start()


def test_script_never_builds_html_from_strings(script):
    assert "innerHTML" not in script
    assert "insertAdjacentHTML" not in script
    assert "document.write" not in script


def test_panel_is_an_accessible_non_modal_dialog(script):
    assert 'setAttribute("role", "dialog")' in script
    assert 'setAttribute("aria-label", "选择表情")' in script
    assert 'setAttribute("aria-haspopup", "dialog")' in script
    assert 'setAttribute("aria-expanded"' in script
    assert 'aria-modal' not in script
    # 读屏用的状态提示，且每个表情按钮都有中文名称。
    assert 'setAttribute("aria-live", "polite")' in script
    assert 'setAttribute("aria-label", name)' in script


@pytest.mark.parametrize("key", ["Escape", "ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp", "Home", "End"])
def test_keyboard_navigation_is_supported(script, key):
    assert f'"{key}"' in script


def test_insertion_respects_the_text_box_limit_and_updates_counters(script):
    assert "maxLength" in script
    assert "setRangeText" in script
    assert 'new Event("input", { bubbles: true })' in script


def test_recent_emoji_storage_is_guarded_for_private_mode(script):
    reads = re.search(r"function readRecent\(\) \{\s*try \{", script)
    writes = re.search(r"function remember\(emoji\) \{\s*try \{", script)
    assert reads and writes


def test_stylesheet_follows_the_project_rules(stylesheet):
    assert "!important" not in stylesheet
    assert "backdrop-filter" not in stylesheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet), "colors must come from theme tokens"
    assert "var(--surface)" in stylesheet and "var(--accent)" in stylesheet
    # 触屏上点击目标不小于 44px。
    coarse = re.search(r"@media \(pointer: coarse\) \{(.*?)\n\}", stylesheet, re.S)
    assert coarse and "min-height: 44px" in coarse.group(1)


@pytest.mark.parametrize("anchor", [
    '$("#forum-comment-body")',
    '$("#forum-compose-form textarea[name=body]")',
])
def test_forum_composers_get_the_picker(app_source, anchor):
    assert re.search(r"EmojiPicker\?\.attach\(\s*" + re.escape(anchor), app_source)


def test_inline_edit_forms_get_the_picker_too(app_source):
    # 帖子编辑和评论编辑都是临时生成的表单，各自挂一次。
    assert len(re.findall(r"EmojiPicker\?\.attach\(\s*body\s*\)", app_source)) == 2


def test_signing_out_closes_an_open_panel(app_source):
    body = re.search(r"function signedOut\(\) \{(.*?)\n\}\n", app_source, re.S)
    assert body and "EmojiPicker?.close()" in body.group(1)
