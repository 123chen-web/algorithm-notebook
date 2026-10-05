"""草稿演算区的静态契约：文件存在、只用主题令牌、无内联样式 / 脚本注入面、体积上限。"""
from pathlib import Path
import re


STATIC = Path(__file__).resolve().parents[1] / "static"


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_scratch_files_exist_and_stay_small():
    script = STATIC / "scratch.js"
    styles = STATIC / "scratch.css"
    assert script.is_file() and styles.is_file()
    assert script.stat().st_size <= 40 * 1024, "scratch.js 不能超过 40 KB"
    assert styles.stat().st_size <= 15 * 1024, "scratch.css 不能超过 15 KB"


def test_stylesheet_follows_the_project_rules():
    css = read("scratch.css")
    assert "!important" not in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), "颜色必须来自主题令牌"
    assert "hsl(" not in css and not re.search(r"rgba?\(\s*\d", css), "不写字面量颜色函数"
    assert not re.search(r'style\s*=\s*"', css)


def test_motion_only_when_reduced_motion_is_not_requested():
    css = read("scratch.css")
    block = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{(.*?)\n\}", css, re.S)
    assert block and "transition" in block.group(1)
    rest = css.replace(block.group(0), "")
    assert "transition" not in rest and "animation" not in rest, "动效必须只出现在 no-preference 媒体查询里"


def test_script_never_builds_html_or_inline_styles():
    source = read("scratch.js")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "eval(", "new Function"):
        assert forbidden not in source, forbidden
    assert 'setAttribute("style"' not in source and ".style.cssText" not in source
    assert "window.setTimeout(" in source, "防抖用 window.setTimeout"
    # 所有文字都走 textContent。
    assert "textContent" in source


def test_touch_targets_and_focus_ring():
    css = read("scratch.css")
    assert len(re.findall(r"min-height: 40px", css)) >= 4, "按钮 / 输入的触控目标至少 40px"
    assert "outline: 3px solid var(--accent)" in css
