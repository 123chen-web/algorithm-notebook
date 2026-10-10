"""N5 所见即所得编辑器（OYEditor）静态契约：
1. vendor 离线产物齐全并在 index.html 按版本号、正确顺序引入（editor 在 notes.js 前，适配层在 app.js 前）；
2. 自写适配层 notes-editor.js 不拼 HTML、不写行内样式、不直连网络、只用相对 /api 地址；
3. notes-editor.css 只用 style.css 已定义令牌，动效/悬停/手机断点合规；
4. vendor CSS 自包含（无 @import / 远程 url），所用令牌在 style.css 全部有定义；
5. CSP 维持严格 self，不引入 unsafe-inline / unsafe-eval，页面无内联脚本/样式。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
VENDOR = STATIC / "vendor" / "editor"
HTML = (STATIC / "index.html").read_text(encoding="utf-8")
ADAPTER_JS = (STATIC / "notes-editor.js").read_text(encoding="utf-8")
ADAPTER_CSS = (STATIC / "notes-editor.css").read_text(encoding="utf-8")
VENDOR_JS = (VENDOR / "oy-editor.js").read_text(encoding="utf-8")
VENDOR_CSS = (VENDOR / "oy-editor.css").read_text(encoding="utf-8")
STYLE_CSS = (STATIC / "style.css").read_text(encoding="utf-8")
MAIN = (ROOT / "main.py").read_text(encoding="utf-8")


def test_vendor_files_present():
    for name in ("oy-editor.js", "oy-editor.css", "VERSION_NOTES.md", "LICENSE", "NOTICE.md"):
        assert (VENDOR / name).is_file(), f"缺少 vendor 文件 {name}"
    assert VENDOR_JS.strip(), "oy-editor.js 不为空"
    assert VENDOR_CSS.strip(), "oy-editor.css 不为空"


def test_vendor_bundle_is_offline_and_csp_safe():
    # 顶层 var OYEditor= 挂全局；Tiptap 的 <style> 注入被显式关闭（minified 为 !1）。
    assert "var OYEditor=" in VENDOR_JS
    assert "injectCSS:!1" in VENDOR_JS
    # 样式表完全自包含：不允许 @import 或任何 url() 资源（字体/图片都已内联或不用）。
    assert "@import" not in VENDOR_CSS
    assert "url(" not in VENDOR_CSS
    assert not re.search(r"https?://", VENDOR_CSS)


def test_vendor_bundle_mathblock_patch_present():
    # 上游产物在空段落上用 setNode('mathBlock') 插入整行公式，但 mathBlock 是 atom
    # 块（非 textblock），Tiptap 会告警且不生效。分发副本改为用 insertContentAt 整体
    # 替换空段落；若升级产物后该补丁消失，斜杠“整行公式”在空行会重新失效。
    assert 'setNode("mathBlock"' not in VENDOR_JS, "mathBlock 空行插入补丁被回退"
    assert ('insertContentAt({from:r.before(r.depth),to:r.after(r.depth)},'
            '{type:"mathBlock",attrs:{tex:e}})') in VENDOR_JS


def test_vendor_bundle_table_cell_serialization_patch_present():
    # 上游 GFM 表格序列化把单元格内容当成行内节点数组直接喂给 serializeInline，
    # 而 Tiptap 单元格里包的是段落块（cell.content = [{type:'paragraph',…}]），
    # 导致保存的 Markdown 表格单元格全部为空。分发副本改为逐单元格展开段落块。
    assert ".map(b=>bu(b.content||[],!0)).join(\"<br>\")" in VENDOR_JS, \
        "表格单元格内容序列化补丁被回退"


def test_index_loads_editor_assets_versioned_and_in_order():
    assert '<script defer src="/static/vendor/editor/oy-editor.js?v=3"></script>' in HTML
    assert '<link rel="stylesheet" href="/static/vendor/editor/oy-editor.css?v=3">' in HTML
    assert '<script defer src="/static/notes-editor.js?v=3"></script>' in HTML
    assert '<link rel="stylesheet" href="/static/notes-editor.css?v=1">' in HTML

    def pos(token):
        index = HTML.find(token)
        assert index > -1, f"index.html 缺少 {token}"
        return index

    # 编辑器产物必须在笔记脚本之前；适配层在富渲染/画板之后、app.js 之前。
    assert pos("vendor/editor/oy-editor.js?v=3") < pos("notes.js?v=6")
    assert pos("notes-rich.js?v=7") < pos("draw-host.js?v=") < pos("notes-editor.js?v=3") < pos("app.js?v=96")


def test_index_has_no_inline_script_or_style():
    assert not re.search(r"\bstyle\s*=", HTML), "不得写行内样式"
    assert not re.search(r"<script\b(?![^>]*\bsrc=)[^>]*>", HTML), "不得写内联脚本"


def test_csp_remains_strict_self():
    assert "script-src 'self'" in MAIN
    assert "style-src 'self'" in MAIN
    assert "connect-src 'self'" in MAIN
    assert "img-src 'self'" in MAIN
    assert "unsafe-eval" not in MAIN
    script_policy = MAIN.split("script-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in script_policy
    style_policy = MAIN.split("style-src", 1)[1].split(";", 1)[0]
    assert "unsafe-inline" not in style_policy


def test_adapter_js_does_not_build_html_or_touch_network():
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write",
                      "eval(", "javascript:", "localStorage", "sessionStorage"):
        assert forbidden not in ADAPTER_JS, f"notes-editor.js 不得出现 {forbidden}"
    assert not re.search(r"\.style\.\w+\s*=", ADAPTER_JS), "不得写行内样式"
    assert 'setAttribute("style"' not in ADAPTER_JS
    assert "fetch(" not in ADAPTER_JS, "网络请求统一走 hooks.api / NotesRich.uploadFile"
    assert "XMLHttpRequest" not in ADAPTER_JS
    assert "https://" not in ADAPTER_JS and "http://" not in ADAPTER_JS, "只用相对 /api 地址"
    # 关键回调与降级入口都在。
    for token in ("/api/notes/attachments/", "/api/drawings/", "/api/notes/suggest",
                  "/api/problems?limit=", "attachment:", "drawing:", "enableFallback",
                  "mountEdit", "getMarkdown", "insertDrawing", "insertWikilink"):
        assert token in ADAPTER_JS, f"notes-editor.js 缺少 {token}"


def _defined_tokens():
    return set(re.findall(r"(--[\w-]+)\s*:", STYLE_CSS))


def test_adapter_css_uses_defined_theme_tokens():
    defined = _defined_tokens()
    assert set(re.findall(r"var\((--[\w-]+)", ADAPTER_CSS)) <= defined
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", ADAPTER_CSS), "不写十六进制颜色"
    assert "!important" not in ADAPTER_CSS
    # 允许令牌驱动的 rgba(var(--rgb-token), a)；禁止裸 rgb()/hsl() 字面量。
    assert not re.search(r"(?<!var\()\b(?:rgba?|hsla?)\(\s*\d", ADAPTER_CSS)
    for match in re.finditer(r"(?<![\w-])(color|background(?:-color)?|border-color|fill|stroke)\s*:\s*([^;}]+)", ADAPTER_CSS):
        value = match.group(2).strip()
        assert (
            value.startswith("var(")
            or value.startswith(("rgb(var(", "rgba(var("))
            or value in {"transparent", "none", "inherit"}
        ), match.group(0)


def test_vendor_css_tokens_are_defined():
    defined = _defined_tokens()
    assert set(re.findall(r"var\((--[\w-]+)", VENDOR_CSS)) <= defined


def test_adapter_css_motion_hover_and_phone_gated():
    motion = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", ADAPTER_CSS, re.S)
    assert motion, "动效必须包在 prefers-reduced-motion: no-preference 里"
    outside = ADAPTER_CSS.replace(motion.group(0), "")
    assert not re.search(r"animation|transition|@keyframes", outside), "动效不得漏出媒体块"
    hover_blocks = re.findall(r"@media \(hover: hover\) and \(pointer: fine\) \{.*?\n\}", outside, re.S)
    assert hover_blocks and all(":hover" in block for block in hover_blocks)
    assert ADAPTER_CSS.count(":hover") == sum(block.count(":hover") for block in hover_blocks)
    assert "@media (max-width: 520px)" in ADAPTER_CSS
    assert not re.search(r"(?<![\w-])width:\s*\d{3,}px", ADAPTER_CSS)
