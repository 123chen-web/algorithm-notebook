"""今日一签 / 今日一读前端文件的静态约定：严格 CSP 兼容、只用 textContent、接入总览页。"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def read(name):
    return (STATIC / name).read_text(encoding="utf-8")


def test_both_cards_are_loaded_before_app_js_and_have_containers():
    scripts = re.findall(r'<script defer src="(/static/[^"]+)"', INDEX)
    app = next(i for i, src in enumerate(scripts) if src.startswith("/static/app.js"))
    for name in ("sign.js", "briefing.js"):
        assert any(src.startswith(f"/static/{name}") for src in scripts[:app]), name
    assert 'id="sign-card"' in INDEX and 'id="briefing-card"' in INDEX
    assert "/static/sign.css" in INDEX and "/static/briefing.css" in INDEX


def test_scripts_are_csp_friendly():
    for name in ("sign.js", "briefing.js"):
        source = read(name)
        for forbidden in ("innerHTML", "eval(", "insertAdjacentHTML", "document.write"):
            assert forbidden not in source, (name, forbidden)
        assert ".style." not in source and 'setAttribute("style"' not in source, name


def test_briefing_links_are_http_only_and_open_safely():
    source = read("briefing.js")
    assert 'link.rel = "noopener noreferrer"' in source
    assert 'link.target = "_blank"' in source
    assert 'url.protocol === "https:" || url.protocol === "http:"' in source


def test_cards_respect_reduced_motion_and_use_theme_tokens():
    for name in ("sign.css", "briefing.css"):
        css = read(name)
        assert "prefers-reduced-motion: reduce" in css, name
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css), f"{name} 里不要写死颜色，用主题变量"
