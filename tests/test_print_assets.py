"""考前打印版的静态契约：入口、引用、数据来源、打印样式、无障碍。"""
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def index_html():
    return (STATIC / "index.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def script():
    return (STATIC / "print.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def stylesheet():
    return (STATIC / "print.css").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def test_assets_are_versioned_and_the_print_rules_load_after_the_shell(index_html):
    sheet = re.search(r'<link rel="stylesheet" href="/static/print\.css\?v=\d+">', index_html)
    shell = re.search(r'<link rel="stylesheet" href="/static/shell\.css\?v=\d+">', index_html)
    assert sheet and shell and shell.start() < sheet.start(), "print rules must win over the shell's display rules"
    script_tag = re.search(r'<script defer src="/static/print\.js\?v=\d+"></script>', index_html)
    app = re.search(r'<script defer src="/static/app\.js\?v=\d+"></script>', index_html)
    assert script_tag and app and script_tag.start() < app.start()


def test_page_and_entries_exist(index_html):
    assert re.search(r'<section id="print-page"[^>]*aria-labelledby="print-title"[^>]*\bhidden\b', index_html)
    assert re.search(r'class="nav-item" data-view="print"', index_html)
    assert re.search(r'class="more-item" data-view="print"', index_html)
    button = re.search(r'<button id="print-go"[^>]*>', index_html)
    assert button and "disabled" in button.group() and 'data-blocked="1"' in button.group(), (
        "the global busy toggle must not re-enable it while there is nothing to print"
    )


def test_script_reads_existing_endpoints_only_and_never_builds_html(script):
    assert "/api/mistakes?due_only=false" in script and "/api/zones" in script
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in script
    assert "window.print()" in script


def test_options_are_remembered_defensively(script):
    assert re.search(r"function readOptions\(\) \{\s*try \{", script)
    assert re.search(r"function saveOptions\(\) \{\s*try \{", script)


def test_options_are_native_labelled_controls(script):
    assert 'node("fieldset", "print-fieldset")' in script and 'node("legend"' in script
    assert 'input.type = "checkbox"' in script and 'input.type = "radio"' in script
    assert "print-summary" in script


def test_view_switching_loads_refreshes_and_resets(app_source):
    assert '$("#print-page").hidden = view !== "print";' in app_source
    assert 'else if (view === "print") await window.PrintNotebook.load();' in app_source
    assert re.search(r'if \(view === "print"\) \{\s*message\(\);\s*await window\.PrintNotebook\.load\(\);', app_source)


def test_stylesheet_follows_the_project_rules(stylesheet):
    assert "!important" not in stylesheet
    assert "backdrop-filter" not in stylesheet
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", stylesheet), "colors must come from theme tokens or print keywords"


def test_sticky_options_panel_scrolls_inside_itself_on_short_screens(stylesheet):
    # 选项面板有 800 多像素高；吸顶时如果不限高，矮屏幕上最下面的选项要滚到整页最底才够得着。
    panel = stylesheet[stylesheet.index(".print-panel {"):]
    panel = panel[:panel.index("}")]
    assert "position: sticky" in panel
    assert "max-height: calc(100vh -" in panel and "max-height: calc(100dvh -" in panel
    assert "overflow-y: auto" in panel


def test_print_media_keeps_only_the_paper(stylesheet):
    printing = stylesheet[stylesheet.index("@media print {"):]
    for hidden in (".app-sidebar", ".app-tabbar", ".more-sheet", ".header", "#notice", "#seal-layer", ".scene-backdrop"):
        assert hidden in printing, hidden
    assert "section:not(#print-page) { display: none; }" in printing
    assert "#print-page .print-panel" in printing
    # 每个分区可以另起一页，一道题不被劈开，代码自动换行。
    assert ".print-zone.is-new-page { break-before: page; }" in printing
    assert ".print-problem { break-inside: avoid;" in printing
    assert "white-space: pre-wrap" in stylesheet
    assert "@page { size: A4;" in stylesheet
    assert "color: black" in printing and "background: none" in printing
