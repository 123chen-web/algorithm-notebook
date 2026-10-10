"""N1 笔记双向链接的静态契约：关键 id、版本号与加载顺序、CSP、令牌与动效、手机宽度。
仿照 tests/test_rank_assets.py，扫描真实文件做断言。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "notes-links.css").read_text(encoding="utf-8")
NL_JS = (STATIC / "notes-links.js").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")

IDS = ("notes-graph", "notes-solution-template")


def test_markup_has_each_required_id_exactly_once():
    for name in IDS:
        assert len(re.findall(rf'\bid="{name}"', HTML)) == 1, name


def test_assets_versioned_and_loaded_before_app_js():
    assert '<link rel="stylesheet" href="/static/notes-links.css?v=5">' in HTML
    scripts = re.findall(r'<script defer src="/static/([\w.-]+\.js)\?v=(\d+)"></script>', HTML)
    names = [name for name, _ in scripts]
    assert names.count("notes-links.js") == 1
    assert names.index("notes-links.js") < names.index("app.js"), "图谱脚本必须在 app.js 之前"
    versions = dict(scripts)
    assert versions["notes-links.js"] == "6"
    assert versions["notes.js"] == "4"
    assert versions["app.js"] == "92"


def test_no_inline_style_or_inline_script_in_markup():
    assert not re.search(r'\bstyle\s*=', HTML), "不得写行内样式"
    assert not re.search(r'<script\b(?![^>]*\bsrc=)[^>]*>', HTML), "不得写内联脚本"


def test_notes_links_js_safe_by_contract():
    for forbidden in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write",
                      "localStorage", "sessionStorage", "eval(", "javascript:"):
        assert forbidden not in NL_JS, f"notes-links.js 不得出现 {forbidden}"
    assert not re.search(r"\.style\.\w+\s*=", NL_JS), "不得写行内样式"
    assert 'setAttribute("style"' not in NL_JS
    assert "fetch(" not in NL_JS, "图谱/脚本不得自行发起外部请求，统一走 hooks.api"


def test_csp_header_unchanged_and_strict():
    assert "script-src 'self'" in MAIN
    assert "connect-src 'self'" in MAIN
    assert "unsafe-inline" not in MAIN.split("script-src")[1].split(";")[0]
    assert "unsafe-eval" not in MAIN


def test_app_js_only_wires_configure():
    assert "window.NotesLinks?.configure(" in APP
    assert "window.NotesLinks?.reset()" in APP


def test_css_uses_theme_tokens_only():
    assert not re.search(r'#[0-9a-fA-F]{3,8}\b', CSS), "不写十六进制颜色"
    assert "!important" not in CSS
    assert not re.search(r'\b(?:rgb|rgba|hsl|hsla)\(', CSS)
    for match in re.finditer(r'(?<![\w-])(color|background|border-color|fill|stroke)\s*:\s*([^;}]+)', CSS):
        value = match.group(2).strip()
        assert value.startswith("var(") or value in {"transparent", "none", "inherit"}, match.group(0)


def test_css_motion_gated_and_hover_gated():
    motion = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", CSS, re.S)
    assert motion, "动效必须包在 prefers-reduced-motion: no-preference 里"
    outside = CSS.replace(motion.group(0), "")
    assert not re.search(r"animation|transition|@keyframes", outside), "动效不得漏出媒体块"
    hover_blocks = re.findall(r"@media \(hover: hover\) and \(pointer: fine\) \{.*?\n\}", CSS, re.S)
    assert hover_blocks and all(":hover" in block for block in hover_blocks)
    assert CSS.count(":hover") == sum(block.count(":hover") for block in hover_blocks), ":hover 只能在 hover 媒体块里"


def test_css_layout_phone_safe():
    assert "@media (max-width: 520px)" in CSS
    assert not re.search(r"(?<![\w-])width:\s*\d{3,}px", CSS), "不写会撑出手机宽度的固定宽度"
