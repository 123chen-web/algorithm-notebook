"""画板宿主的静态契约：不拼 HTML、不直接发网络请求、不写行内 style；
颜色只用已定义主题令牌；index.html 里的资源带版本号且加载顺序正确；
面板标记无内联样式/内联脚本。"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def test_draw_host_js_uses_inert_dom_and_host_api():
    source = (STATIC / "draw-host.js").read_text(encoding="utf-8")
    for forbidden in (
        "innerHTML", "outerHTML", "insertAdjacentHTML",
        "fetch(", "XMLHttpRequest", "eval(",
        ".style.", "setAttribute(\"style\"", "setAttribute('style'",
    ):
        assert forbidden not in source, forbidden
    # 协议安全校验必须在。
    assert "event.origin" in source
    assert "event.source" in source
    assert 'data.type.indexOf("draw:") === 0' in source
    # 所有请求走宿主 api。
    assert "hooks.api(" in source
    assert "reset" in source


def test_draw_host_css_uses_defined_tokens_only():
    source = (STATIC / "draw-host.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"(--[\w-]+)\s*:", (STATIC / "style.css").read_text(encoding="utf-8")))
    assert set(re.findall(r"var\((--[\w-]+)", source)) <= defined
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)


def test_index_html_wires_draw_assets_with_version_and_order():
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert re.search(r'href="/static/draw-host\.css\?v=\d+"', page)
    draw_script = re.search(r"<script[^>]*src=\"/static/draw-host\.js\?v=\d+\"[^>]*>", page)
    assert draw_script is not None
    assert "defer" in draw_script.group(0)

    # 加载顺序：notes.js → draw-host.js → app.js（宿主 app.js 配置 DrawHost 前必须已加载）。
    def pos(needle):
        index = page.find(needle)
        assert index >= 0, needle
        return index

    assert pos("notes.js?v=") < pos("draw-host.js?v=") < pos("app.js?v=")

    # 面板与列表区标记存在，且画板相关片段里没有内联 style / on* 脚本。
    panel_start = page.index('id="draw-panel"')
    panel_end = page.index("</div>", page.index("draw-panel-error", panel_start))
    panel_block = page[panel_start:panel_end]
    assert "style=" not in panel_block
    assert not re.search(r"\son\w+\s*=", panel_block)
    assert 'id="draw-new"' in page and 'id="draw-list"' in page


def test_app_does_not_reload_notes_while_card_editor_open():
    # 关闭画板会刷新画板列表并回调 onDrawingsChanged；笔记卡片正在编辑时
    # 重渲染会冲掉未保存正文（含刚插入的画板引用），必须跳过。
    app = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "onDrawingsChanged" in app
    assert '.notes-edit-content' in app
    assert '!document.querySelector(".notes-edit-content")' in app


def test_sw_precaches_draw_assets_via_manifest_with_status_messages():
    # 冷启动字体竞态修复：SW 按清单预缓存 /static/draw/，并向宿主回报状态。
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"/static/draw-manifest.json"' in sw or '"/static/draw-manifest.json"' in sw
    assert "DRAW_PRECACHE" in sw and "DRAW_CACHE_STATUS" in sw
    assert "precacheDrawAssets" in sw
    # 清单条目必须严格限定在 /static/draw/ 下。
    assert "/static/draw/" in sw
    # 预缓存不能阻塞安装（不得放进 install 的 waitUntil）。
    install_block = sw.split('addEventListener("install"', 1)[1].split("addEventListener(", 1)[0]
    assert "precacheDrawAssets" not in install_block
    # 清单文件存在且只含画板资源（外加一个同源化 shim）。
    import json
    manifest = json.loads((STATIC / "draw-manifest.json").read_text(encoding="utf-8"))
    urls = manifest["urls"]
    assert "/static/draw/draw.html" in urls
    assert "/static/draw-font-shim.js" in urls
    assert any(u.endswith(".woff2") and "/fonts/" in u for u in urls)
    assert all(u.startswith("/static/draw/") or u == "/static/draw-font-shim.js" for u in urls)


def test_draw_font_shim_strips_cross_origin_font_candidates():
    # 同源化前置脚本：包裹 FontFace，剔除跨源候选；自身不拼 HTML、不用 eval。
    shim = (STATIC / "draw-font-shim.js").read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "eval(", "document.write", "esm.sh"):
        # esm.sh 不得硬编码在 shim 里（过滤是通用的同源判定，不针对特定域名）。
        assert forbidden not in shim, forbidden
    assert "window.FontFace" in shim
    assert "sameOriginOnly" in shim
    assert 'url.indexOf("data:") === 0' in shim
    assert 'url.indexOf(origin) === 0' in shim
    # sw.js 必须把 shim 注入 draw.html 的 <head>，且走画板缓存通道。
    sw = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert '"/static/draw-font-shim.js"' in sw
    assert "injectDrawShim" in sw
    assert 'html.replace("<head>"' in sw
    assert "url.pathname === DRAW_SHIM_URL" in sw
    # dist 的 draw.html 必须保持原样、不含 shim 标签（注入只发生在 SW 响应里）。
    draw_html = (STATIC / "draw" / "draw.html").read_text(encoding="utf-8")
    assert "draw-font-shim.js" not in draw_html


def test_draw_host_gates_iframe_on_sw_cache_with_timeout_fallback():
    js = (STATIC / "draw-host.js").read_text(encoding="utf-8")
    # 闸门纯函数、等待与预热接线都在。
    assert "function drawGateDecision" in js
    assert "waitDrawCacheReady" in js and "primeDrawCache" in js
    assert "DRAW_PRECACHE" in js and "DRAW_CACHE_STATUS" in js
    # iframe 挂载必须发生在闸门等待之后（src 赋值在 waitDrawCacheReady 之后）。
    gate_pos = js.index("await waitDrawCacheReady(")
    src_pos = js.index('panel.frame.src = `${DRAW_PATH}')
    assert gate_pos < src_pos
    # 首次准备的中文提示存在。
    assert "首次打开画板需要准备本地资源" in js


def test_draw_panel_is_keyboard_and_mobile_friendly():
    css = (STATIC / "draw-host.css").read_text(encoding="utf-8")
    js = (STATIC / "draw-host.js").read_text(encoding="utf-8")
    # Esc 关闭。
    assert '"Escape"' in js
    # 手机宽度下按钮 ≥44px。
    mobile = css.split("@media (max-width: 640px)", 1)[1]
    assert "min-height: 44px" in mobile and "min-width: 44px" in mobile
    # 动效只在 prefers-reduced-motion: no-preference 下。
    assert "@media (prefers-reduced-motion: no-preference)" in css
    assert "@keyframes draw-panel-in" in css
