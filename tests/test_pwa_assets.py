"""PWA 客户端的静态契约：文件存在、manifest 与图标、CSP 安全写法、主题令牌、体积上限。"""
import json
import re
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
MANIFEST = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
CSS = (STATIC / "pwa.css").read_text(encoding="utf-8")
SW = (STATIC / "sw.js").read_text(encoding="utf-8")
REGISTER = (STATIC / "pwa-register.js").read_text(encoding="utf-8")
SYNC = (STATIC / "offline-sync.js").read_text(encoding="utf-8")
ICON_SCRIPT = (ROOT / "assets" / "make_pwa_icons.py").read_text(encoding="utf-8")

JS_SOURCES = {"sw.js": SW, "pwa-register.js": REGISTER, "offline-sync.js": SYNC}
# 体积上限（字节）：无构建无依赖的小部件不该膨胀。
SIZE_LIMITS = {
    "static/sw.js": 12_000,
    "static/pwa-register.js": 20_000,
    "static/offline-sync.js": 24_000,
    "static/pwa.css": 8_000,
    "static/manifest.webmanifest": 4_000,
}


def test_all_pwa_files_exist():
    for relative in (
        "static/manifest.webmanifest", "static/sw.js", "static/pwa-register.js",
        "static/offline-sync.js", "static/pwa.css", "assets/make_pwa_icons.py",
        "static/icons/icon-192.png", "static/icons/icon-512.png",
        "static/icons/icon-maskable-192.png", "static/icons/icon-maskable-512.png",
    ):
        assert (ROOT / relative).is_file(), relative


def test_sources_stay_small():
    for relative, limit in SIZE_LIMITS.items():
        size = (ROOT / relative).stat().st_size
        assert size <= limit, f"{relative} 有 {size} 字节，超过上限 {limit}"


def test_manifest_declares_installable_identity():
    assert MANIFEST["name"] == "欧叶OY"
    assert MANIFEST["short_name"]
    assert MANIFEST["display"] == "standalone"
    assert MANIFEST["start_url"] == "/"
    assert MANIFEST["scope"] == "/"
    # 主题色 / 背景色取站点令牌的实际值（static/style.css 的 --accent 与 --paper）。
    assert MANIFEST["theme_color"] == "#c23a2b"
    assert MANIFEST["background_color"] == "#f5f0e6"


def _png_size(path):
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", f"{path.name} 不是 PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def test_manifest_icons_point_at_real_pngs_with_matching_sizes():
    icons = {icon["src"]: icon for icon in MANIFEST["icons"]}
    for size in ("192", "512"):
        for kind, purpose in ((f"icon-{size}", "any"), (f"icon-maskable-{size}", "maskable")):
            src = f"/static/icons/{kind}.png"
            assert src in icons, src
            assert icons[src]["sizes"] == f"{size}x{size}"
            assert icons[src]["type"] == "image/png"
            assert icons[src]["purpose"] == purpose
            assert _png_size(STATIC / "icons" / f"{kind}.png") == (int(size), int(size))


def test_icon_generator_uses_bundled_font_and_token_colors():
    assert "NotoSansSC-Regular-subset.otf" in ICON_SCRIPT
    assert "ImageFont.truetype" in ICON_SCRIPT
    # 章面红与字色必须与 manifest / 令牌一致（accent #c23a2b、on-accent #fff、paper #f5f0e6）。
    assert "(194, 58, 43, 255)" in ICON_SCRIPT
    assert "(255, 255, 255, 255)" in ICON_SCRIPT
    assert "(245, 240, 230, 255)" in ICON_SCRIPT
    assert 'CHAR = "错"' in ICON_SCRIPT


def test_js_is_csp_and_xss_safe():
    for name, source in JS_SOURCES.items():
        assert "innerHTML" not in source, name
        assert "insertAdjacentHTML" not in source, name
        assert "outerHTML" not in source, name
        assert "eval(" not in source, name
        assert "document.write" not in source, name
        assert "localStorage" not in source and "sessionStorage" not in source, name
        assert not re.search(r"\.style\.\w+\s*=", source), name
        assert 'setAttribute("style"' not in source, name


def test_js_guards_late_responses_and_binds_user():
    assert "request.epoch === hooks.getEpoch()" in SYNC
    assert "request.sequence === sequence[request.name]" in SYNC
    assert "forUser" in SYNC and "pwa:queue-dropped" in SYNC
    assert "gen !== generation" in REGISTER, "pwa-register 的迟到注册响应要丢弃"


def test_sw_never_caches_api_or_set_cookie_responses():
    assert 'url.pathname.startsWith("/api/")' in SW
    assert SW.count('headers.get("set-cookie")') >= 2, "预缓存与运行时两处都要跳过 Set-Cookie"
    assert "SKIP_WAITING" in SW
    assert 'request.mode === "navigate"' in SW
    assert "CACHE_PREFIX" in SW and "caches.delete" in SW


def test_offline_sync_uses_existing_queue_and_review_route():
    assert "window.OfflineQueue" in SYNC
    assert "/api/review/queue?limit=50" in SYNC
    assert re.search(r"/api/mistakes/\$\{op\.mistakeId\}/review", SYNC)
    assert "client_op_id" in SYNC and "reviewed_at" in SYNC
    for status in ("401", "404", "409"):
        assert status in SYNC


def test_css_uses_theme_tokens_only():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", CSS), "不写十六进制颜色"
    assert "!important" not in CSS
    assert not re.search(r"\b(?:rgb|rgba|hsl|hsla)\(", CSS)
    assert not re.search(r"\bstyle\s*=", CSS)
    for match in re.finditer(r"(?<![\w-])(color|background|border-color|fill|stroke)\s*:\s*([^;}]+)", CSS):
        value = match.group(2).strip()
        assert value.startswith("var(") or value in {"transparent", "none", "inherit"}, match.group(0)


def test_css_motion_is_gated_and_layout_is_phone_safe():
    motion = re.search(r"@media \(prefers-reduced-motion: no-preference\) \{.*?\n\}", CSS, re.S)
    assert motion, "动效必须包在 prefers-reduced-motion: no-preference 里"
    outside = CSS.replace(motion.group(0), "")
    assert not re.search(r"animation|transition|@keyframes", outside)
    assert "overflow-x" not in CSS
    assert "min-height: 40px" in CSS, "触控目标至少 40px"
    assert "max-width: calc(100vw - 2 * var(--space-4))" in CSS
    assert "overflow-wrap: anywhere" in CSS and "min-width: 0" in CSS
    assert not re.search(r"(?<![\w-])width:\s*\d{3,}px", CSS), "不写会撑出手机宽度的固定宽度"
