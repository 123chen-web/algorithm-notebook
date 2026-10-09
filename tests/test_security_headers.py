import pytest
from fastapi.testclient import TestClient

import main


@pytest.mark.parametrize("path", ["/", "/api/zones"])
def test_csp_allows_local_photo_blobs_without_relaxing_other_sources(path, monkeypatch):
    # These routes are stateless; checking response headers needs no database.
    monkeypatch.setattr(main, "init_db", lambda: None)
    with TestClient(main.app) as client:
        response = client.get(path)

    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    directives = {
        parts[0]: parts[1:]
        for directive in policy.split(";")
        if (parts := directive.split())
    }
    assert directives == {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "connect-src": ["'self'"],
        "img-src": ["'self'", "blob:"],
        # frame-src 'self' 仅显式声明笔记页同源 iframe 画板；default-src 'self'
        # 本就回退允许，不是放宽（画板集成任务 N3，详见 NOTES_DRAW_REPORT.md）。
        "frame-src": ["'self'"],
        "base-uri": ["'none'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
    }
    assert "img-src 'self' blob:" in policy
    for forbidden in ("unsafe-inline", "unsafe-eval", "data:", "*"):
        assert forbidden not in policy


def test_draw_static_path_is_embeddable_same_origin_but_keeps_script_lockdown(monkeypatch):
    # /static/draw/ 是全站唯一允许被同源 iframe 嵌入的路径；脚本指令仍不得放宽。
    monkeypatch.setattr(main, "init_db", lambda: None)
    with TestClient(main.app) as client:
        draw = client.get("/static/draw/draw.html")
        home = client.get("/")

    assert draw.status_code == 200
    draw_policy = draw.headers["Content-Security-Policy"]
    draw_directives = {
        parts[0]: parts[1:]
        for directive in draw_policy.split(";")
        if (parts := directive.split())
    }
    assert draw_directives["frame-ancestors"] == ["'self'"]
    assert draw.headers["X-Frame-Options"] == "SAMEORIGIN"
    # 连接源不允许内联与任意源。
    assert draw_directives["connect-src"] == ["'self'"]
    # 脚本/样式只允许 'self' 外加 draw.html 两段厂商静态内联块的 sha256 白名单
    # （配置脚本设置本地字体前缀、全屏布局样式），绝不允许 unsafe-inline/unsafe-eval。
    import base64
    import hashlib
    import re
    from pathlib import Path

    draw_html_path = Path(__file__).resolve().parents[1] / "static" / "draw" / "draw.html"
    html = draw_html_path.read_text(encoding="utf-8")

    def _inline_hashes(tag):
        pattern = (
            r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>"
            if tag == "script"
            else r"<style[^>]*>(.*?)</style>"
        )
        out = []
        for m in re.finditer(pattern, html, re.S | re.I):
            if m.group(1).strip():
                raw = hashlib.sha256(m.group(1).encode("utf-8")).digest()
                out.append("'sha256-" + base64.b64encode(raw).decode("ascii") + "'")
        return out

    expected_script = ["'self'"] + _inline_hashes("script")
    expected_style = ["'self'"] + _inline_hashes("style")
    assert draw_directives["script-src"] == expected_script
    assert draw_directives["style-src"] == expected_style
    # hash 白名单必须非空，否则内联配置脚本被拦会导致字体回退外链。
    assert len(expected_script) > 1 and len(expected_style) > 1
    for forbidden in ("unsafe-inline", "unsafe-eval", "data:", "*"):
        assert forbidden not in draw_policy

    # 其它路径维持 frame-ancestors 'none' / DENY 语义，不允许被嵌入。
    home_directives = {
        parts[0]: parts[1:]
        for directive in home.headers["Content-Security-Policy"].split(";")
        if (parts := directive.split())
    }
    assert home_directives["frame-ancestors"] == ["'none'"]
    assert "X-Frame-Options" not in draw.headers or draw.headers["X-Frame-Options"] == "SAMEORIGIN"


def test_woff2_fonts_are_served_with_correct_mime(monkeypatch):
    from pathlib import Path
    font_dir = Path(__file__).resolve().parents[1] / "static" / "draw" / "fonts"
    woff2_files = list(font_dir.rglob("*.woff2")) if font_dir.is_dir() else []
    if not woff2_files:
        pytest.skip("画板字体目录不存在")
    monkeypatch.setattr(main, "init_db", lambda: None)
    # 字体按字族子目录存放：fonts/<family>/<file>.woff2
    first = woff2_files[0]
    rel = first.relative_to(font_dir).as_posix()
    with TestClient(main.app) as client:
        response = client.get(f"/static/draw/fonts/{rel}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "font/woff2"
