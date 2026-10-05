"""导出到 Anki 的静态资产契约：账号菜单里的入口、版本化引用、CSP 与主题令牌。"""

import re
from pathlib import Path

import pytest

from test_landing_auth_assets import LandingDocument


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def document():
    parser = LandingDocument()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    parser.close()
    return parser


@pytest.fixture(scope="module")
def widget_source():
    return (STATIC / "anki-export.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def style_source():
    return (STATIC / "anki-export.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_anki_entry_lives_next_to_json_export_inside_account_panel(document):
    panels = [
        (index, node) for index, node in enumerate(document.elements)
        if "account-menu-panel" in node["attrs"].get("class", "").split()
    ]
    assert len(panels) == 1
    panel_index, _ = panels[0]

    block_index, block = document.by_id("anki-export")
    assert panel_index in block["ancestors"]
    assert block["tag"] == "div"

    for element_id in ("anki-scope", "anki-zone", "anki-download", "anki-status"):
        index, node = document.by_id(element_id)
        assert block_index in node["ancestors"]

    scope_index, scope = document.by_id("anki-scope")
    assert scope["tag"] == "select"
    option_values = [
        node["attrs"].get("value")
        for index, node in enumerate(document.elements)
        if scope_index in node["ancestors"] and node["tag"] == "option"
    ]
    assert option_values == ["all", "weak", "mastered", "zone"]

    download_index, download = document.by_id("anki-download")
    assert download["tag"] == "button"
    assert download["attrs"].get("type") == "button"

    status_index, status = document.by_id("anki-status")
    assert status["tag"] == "p"
    assert status["attrs"].get("role") == "status"
    assert status["attrs"].get("aria-live") == "polite"

    zone_index, zone = document.by_id("anki-zone")
    assert zone["tag"] == "select"
    assert "hidden" in zone["attrs"]

    # 三步导入说明必须在入口块里。
    steps = [
        node for index, node in enumerate(document.elements)
        if block_index in node["ancestors"] and node["tag"] == "li"
    ]
    assert len(steps) == 3
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    start = html.index('id="anki-export"')
    block_html = html[start:html.index("</div>", start)]
    assert "导入" in block_html
    assert "GUID" in block_html
    assert "Tab" in block_html


def test_anki_assets_are_versioned_and_loaded_once(document):
    for filename, tag, attribute in (
        ("anki-export.js", "script", "src"),
        ("anki-export.css", "link", "href"),
    ):
        assets = [
            node for node in document.elements
            if node["tag"] == tag
            and node["attrs"].get(attribute, "").split("?", 1)[0] == f"/static/{filename}"
        ]
        assert len(assets) == 1
        assert re.fullmatch(
            rf"/static/{re.escape(filename)}\?v=\d+", assets[0]["attrs"][attribute]
        )
        if tag == "script":
            assert "defer" in assets[0]["attrs"]
            assert "async" not in assets[0]["attrs"]
        else:
            assert assets[0]["attrs"].get("rel") == "stylesheet"


def test_widget_uses_text_content_guard_and_blob_download(widget_source):
    assert "textContent" in widget_source
    assert "innerHTML" not in widget_source
    assert not re.search(r"\.on\w+\s*=|setAttribute\(\s*[\"']on\w+", widget_source)
    assert not re.search(r"style\.(?:cssText|setProperty)|\.setAttribute\([\"']style", widget_source)
    # 迟到响应守卫：epoch + 点击票号；登出复位。
    assert "epoch" in widget_source and "clickTicket" in widget_source
    assert "function reset" in widget_source
    # 同源凭据与 CSRF 头；GET 端点。
    assert 'credentials: "same-origin"' in widget_source
    assert '"X-CSRF-Protection": "1"' in widget_source
    assert "/api/export/anki?scope=" in widget_source
    # 文件名取自 Content-Disposition，下载走 <a download>。
    assert "Content-Disposition" in widget_source
    assert re.search(r"setAttribute\(\s*[\"']download[\"']", widget_source)
    # 服务器返回的错误信息只能 textContent。
    assert "data.detail" in widget_source


def test_app_wires_zones_and_signout_reset(app_source):
    assert "window.AnkiExport?.setZones(zones)" in app_source
    assert "window.AnkiExport?.reset()" in app_source


def test_styles_use_theme_tokens_only(style_source):
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", style_source)
    assert "!important" not in style_source
    assert not re.search(r"\b(?:rgba?|hsla?)\(\s*[\d.]", style_source)
    assert "var(--surface)" in style_source
    assert "var(--danger)" in style_source
    assert ":focus-visible" in style_source
    assert "44px" in style_source
    if re.search(r"(?:animation|transition)\s*:", style_source):
        assert "prefers-reduced-motion" in style_source


def test_markup_has_no_inline_style_or_inline_handlers(document):
    block_index, _ = document.by_id("anki-export")
    for index, node in enumerate(document.elements):
        if block_index in node["ancestors"] or index == block_index:
            assert "style" not in node["attrs"]
            assert not any(name.lower().startswith("on") for name in node["attrs"])
