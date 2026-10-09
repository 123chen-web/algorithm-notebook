"""N2 笔记富渲染（公式/流程图/图片附件）静态契约：
版本号与加载顺序、CSP 不放宽、懒加载契约、CSS 令牌与动效/手机断点。
仿照 tests/test_notes_links_assets.py，扫描真实文件断言。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "notes-rich.css").read_text(encoding="utf-8")
NR_JS = (STATIC / "notes-rich.js").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def test_assets_versioned_and_loaded_in_order():
    assert '<link rel="stylesheet" href="/static/notes-rich.css?v=3">' in HTML
    scripts = re.findall(r'<script defer src="/static/([\w.-]+\.js)\?v=(\d+)"></script>', HTML)
    names = [name for name, _ in scripts]
    assert names.count("notes-rich.js") == 1
    assert names.index("notes.js") < names.index("notes-links.js") < names.index("notes-rich.js") < names.index("app.js")
    versions = dict(scripts)
    assert versions["notes-rich.js"] == "3"
    assert versions["notes.js"] == "3"
    assert versions["notes-links.js"] == "3"
    assert versions["app.js"] == "92"


def test_no_inline_style_or_inline_script_in_markup():
    assert not re.search(r'\bstyle\s*=', HTML), "不得写行内样式"
    assert not re.search(r'<script\b(?![^>]*\bsrc=)[^>]*>', HTML), "不得写内联脚本"


def test_notes_rich_js_safe_by_contract():
    for forbidden in ("innerHTML", "insertAdjacentHTML", "outerHTML", "document.write",
                      "localStorage", "sessionStorage", "eval(", "javascript:"):
        assert forbidden not in NR_JS, f"notes-rich.js 不得出现 {forbidden}"
    assert not re.search(r"\.style\.\w+\s*=", NR_JS), "不得写行内样式"
    assert 'setAttribute("style"' not in NR_JS
    assert "fetch(" not in NR_JS, "上传走 XHR；其它请求统一走 hooks.api"


def test_vendor_lazy_loaded_only_at_runtime():
    # index.html 不得静态引入 vendor；只有 notes-rich.js 内动态注入。
    assert "vendor/katex" not in HTML
    assert "vendor/mermaid" not in HTML
    assert "/static/vendor/katex/katex.min.js" in NR_JS
    assert "/static/vendor/katex/katex.min.css" in NR_JS
    assert "/static/vendor/mermaid/mermaid.min.js" in NR_JS
    # 懒加载地址带 ?v=，才能被 sw.js 运行时缓存（断网可用）。
    assert "katex.min.js?v=1" in NR_JS
    assert "katex.min.css?v=1" in NR_JS
    assert "mermaid.min.js?v=1" in NR_JS
    # mermaid 必须 strict、不自动跑。
    assert "securityLevel" in NR_JS
    assert "startOnLoad" in NR_JS


def test_csp_header_unchanged_and_strict():
    assert "script-src 'self'" in MAIN
    assert "style-src 'self'" in MAIN
    assert "connect-src 'self'" in MAIN
    assert "unsafe-inline" not in MAIN.split("script-src")[1].split(";")[0]
    assert "unsafe-eval" not in MAIN


def test_no_external_origin_reference():
    assert "https://" not in NR_JS
    assert "http://" not in NR_JS


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
