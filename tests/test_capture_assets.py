"""收录（粘贴题目链接 + 书签小工具）的静态契约：关键 id、版本号、无十六进制色 / !important / 内联样式、接线位置。"""

from pathlib import Path
import re

import pytest

from test_layout_assets import layout_document


STATIC = Path(__file__).resolve().parents[1] / "static"
KEY_IDS = (
    "capture-box", "capture-url", "capture-go", "capture-status", "capture-banner",
    "capture-bookmarklet-box", "capture-bookmarklet", "capture-copy", "capture-copy-status", "capture-code",
)


@pytest.fixture(scope="module")
def document():
    return layout_document()


@pytest.fixture(scope="module")
def html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


def test_capture_ids_exist_once_inside_the_new_record_page(document):
    page_index = next(i for i, n in enumerate(document) if n["attrs"].get("id") == "new-page")
    for element_id in KEY_IDS:
        found = [n for n in document if n["attrs"].get("id") == element_id]
        assert len(found) == 1, element_id
        assert page_index in found[0]["ancestors"], f"#{element_id} 应在 #new-page 里"


def test_capture_assets_are_versioned_and_script_precedes_app(html):
    assert '<link rel="stylesheet" href="/static/capture.css?v=1">' in html
    scripts = re.findall(r'<script defer src="(/static/[^"]+)"', html)
    assert "/static/capture.js?v=1" in scripts
    app = next(i for i, src in enumerate(scripts) if src.startswith("/static/app.js"))
    assert scripts.index("/static/capture.js?v=1") < app, "app.js 启动时就要调用 Capture.intake()"


def test_capture_markup_has_no_inline_style_script_or_javascript_href(html):
    start = html.index('id="capture-box"')
    block = html[start:html.index('id="form-section-progress"')]
    assert "style=" not in block and "onclick" not in block.lower() and "<script" not in block
    assert "javascript:" not in block, "书签的 javascript: 链接必须在运行时用本站 origin 生成"


@pytest.mark.parametrize("name", ["capture.css", "capture.js"])
def test_capture_files_have_no_hex_colors_or_important(name):
    source = re.sub(r"/\*[\s\S]*?\*/", "", (STATIC / name).read_text(encoding="utf-8"))
    if name.endswith(".css"):
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)
    assert "!important" not in source
    assert "backdrop-filter" not in source


def test_capture_css_hides_bookmarklet_on_phones_and_has_touch_targets():
    css = (STATIC / "capture.css").read_text(encoding="utf-8")
    phone = css[css.index("@media (max-width: 599px)"):]
    assert ".capture-bookmarklet-box { display: none; }" in phone
    assert ".capture-mobile-note { display: block; }" in phone
    assert css.count("min-height: 44px") >= 3


def test_capture_script_makes_no_requests_and_reads_no_cookie():
    source = (STATIC / "capture.js").read_text(encoding="utf-8")
    code = source.split("function bookmarkletCode")[1].split("function bookmarkletHref")[0]
    for forbidden in ("fetch(", "XMLHttpRequest", "sendBeacon", "document.cookie", "innerHTML", "eval(", "new Function"):
        assert forbidden not in source, forbidden
    assert "cookie" not in code.lower()


def test_app_wires_capture_in_the_documented_places():
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert app.count("window.Capture?.intake()") == 2  # 启动时 + hashchange
    assert "window.Capture?.reset()" in app
    assert "window.Capture.renderThinking(item.thinking)" in app
    enter = app[app.index("async function enterApp"):app.index("async function showView")]
    assert "hasPending()" in enter and "applyCapturePending" in enter
    guard = app[app.index("async function applyCapturePending"):]
    guard = guard[:guard.index("\n}\n")]
    assert "sessionEpoch" in guard and "epoch !== sessionEpoch" in guard
    signed_out = app[app.index("function signedOut"):app.index("window.EmojiPicker?.close();")]
    assert "if (user) window.Capture?.reset()" in signed_out, "启动时 401 的 signedOut 不能清掉书签预填"
