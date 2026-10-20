"""笔记思维导图和打印：资源接入、安全规则、打印样式的约束，以及 Node 行为测试。"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from static_helpers import versioned_src

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
MAP_JS = (STATIC / "notes-map.js").read_text(encoding="utf-8")
MAP_CSS = (STATIC / "notes-map.css").read_text(encoding="utf-8")
PRINT_CSS = (STATIC / "notes-print.css").read_text(encoding="utf-8")
NOTES_JS = (STATIC / "notes.js").read_text(encoding="utf-8")
CORE_UI = (STATIC / "app" / "core-ui.js").read_text(encoding="utf-8")


def test_assets_are_wired_with_versions_and_order():
    assert f'<link rel="stylesheet" href="{versioned_src("notes-map.css")}">' in INDEX
    assert f'<link rel="stylesheet" href="{versioned_src("notes-print.css")}">' in INDEX
    script = f'<script defer src="{versioned_src("notes-map.js")}"></script>'
    assert script in INDEX
    # 导图要在配置它的 core-ui.js 之前加载（经典脚本按顺序执行）。
    assert INDEX.index(script) < INDEX.index(versioned_src("app/core-ui.js"))


def test_map_script_is_csp_safe():
    code = re.sub(r"/\*[\s\S]*?\*/", "", MAP_JS)
    for banned in ("innerHTML", "outerHTML", "insertAdjacentHTML", "eval(", "new Function", "document.write", '"style"', "setAttribute(\"style\""):
        assert banned not in code, banned
    assert ".style." not in code and ".style[" not in code
    # 取不到的东西一律走 textContent。
    assert "textContent" in code


def test_map_css_uses_theme_tokens_only():
    # 屏幕样式里不写颜色字面量，全部走主题令牌（令牌的对比度有测试守着）。
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", MAP_CSS)
    assert not re.search(r"\b(?:rgb|rgba|hsl|hsla)\(", MAP_CSS)


def test_print_css_only_uses_black_white_gray_keywords():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", PRINT_CSS)
    assert not re.search(r"\b(?:rgb|rgba|hsl|hsla)\(", PRINT_CSS)
    assert "@media print" in PRINT_CSS
    # 导图打印用横向 A4 的命名页。
    assert "@page nm-map { size: A4 landscape;" in PRINT_CSS
    # 只在点了“打印”（<html> 上有标记）时才改动页面：每条规则的选择器都得带 data-print-note。
    body = re.sub(r"/\*[\s\S]*?\*/", "", PRINT_CSS)
    body = re.sub(r"@page[^{]*\{[^}]*\}", "", body)
    body = body.replace("@media print {", "", 1)
    rules = [rule for rule in body.split("}") if "{" in rule]
    assert rules
    for rule in rules:
        selector = rule.split("{", 1)[0]
        assert "data-print-note" in selector, selector.strip()


def test_reader_has_print_button_and_view_switch():
    assert 'h("button", "打印", "notes-action")' in NOTES_JS
    assert "dataset.printNote" in NOTES_JS
    assert '"afterprint"' in NOTES_JS, "打完要把页面还原"
    assert 'h("button", "导图", "nm-tab")' in NOTES_JS
    # 折叠的答案和“遮住看”都要在打印前处理，不然纸上是空的。
    assert "details:not([open])" in NOTES_JS and "is-covered" in NOTES_JS


def test_map_and_connect_share_the_same_openers():
    assert "const notesOpeners" in CORE_UI
    assert "window.NotesMap?.configure(notesOpeners)" in CORE_UI
    assert CORE_UI.count("...notesOpeners") == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
@pytest.mark.parametrize("script, minimum", [("notes_map_behaviour.cjs", 18), ("notes_map_reader_behaviour.cjs", 5)])
def test_notes_map_behaviour_in_a_fake_browser(script, minimum):
    result = subprocess.run(
        ["node", "--test", f"tests/{script}"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= minimum, result.stdout[-1500:]
