"""复习手感静态契约只检查标记、资源加载、CSP 和样式规则。"""
from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


class Markup(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.nodes = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.nodes.append((tag, dict(attrs)))


def test_review_assets_have_bumped_versions_and_dependency_load_order():
    markup = Markup((STATIC / "index.html").read_text(encoding="utf-8"))
    scripts = [attrs["src"] for tag, attrs in markup.nodes if tag == "script" and "src" in attrs]
    styles = [attrs.get("href") for tag, attrs in markup.nodes if tag == "link" and attrs.get("rel") == "stylesheet"]
    for src in ("/static/review-extras.js?v=1", "/static/focus.js?v=3", "/static/app.js?v=84"):
        assert scripts.count(src) == 1
    assert scripts.index("/static/review-extras.js?v=1") < scripts.index("/static/focus.js?v=3") < scripts.index("/static/app.js?v=84")
    assert styles.count("/static/review-extras.css?v=2") == 1
    assert styles.count("/static/focus.css?v=3") == 1


def test_review_cap_markup_has_announced_status_and_accessible_options():
    source = (STATIC / "index.html").read_text(encoding="utf-8")
    markup = Markup(source)
    ids = {attrs["id"]: (tag, attrs) for tag, attrs in markup.nodes if "id" in attrs}
    for name in ("review-cap-controls", "review-daily-cap", "review-done-today", "review-cap-note", "review-queue-status"):
        assert name in ids
    assert ids["review-daily-cap"][0] == "select"
    assert ids["review-queue-status"][1].get("aria-live") == "polite"
    assert any(tag == "label" and attrs.get("for") == "review-daily-cap" for tag, attrs in markup.nodes)
    select = re.search(r'<select id="review-daily-cap">([\s\S]*?)</select>', source).group(1)
    assert re.findall(r'<option value="([^"]*)"', select) == ["", "10", "20", "30", "50", "100"]


def test_review_markup_and_scripts_respect_csp():
    markup = Markup((STATIC / "index.html").read_text(encoding="utf-8"))
    assert all("style" not in attrs for _, attrs in markup.nodes)
    assert all("src" in attrs for tag, attrs in markup.nodes if tag == "script")
    for filename in ("review-extras.js", "focus.js"):
        source = (STATIC / filename).read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
            assert forbidden not in source, (filename, forbidden)


@pytest.mark.parametrize("filename", ["review-extras.css", "focus.css"])
def test_review_css_uses_theme_tokens_and_motion_gates(filename):
    source = (STATIC / filename).read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)
    assert "!important" not in source
    assert 'style="' not in source
    # Any blur rule must remain inside a desktop pointer/hover media block.
    stack = []
    for match in re.finditer(r"([^{};]*)([{};])", re.sub(r"/\*[\s\S]*?\*/", "", source)):
        token, delimiter = match.groups()
        if delimiter == "{":
            stack.append(token.strip())
        else:
            if "backdrop-filter" in token:
                assert any("(hover: hover) and (pointer: fine)" in block for block in stack)
            if re.match(r"\s*(?:animation|transition)(?:-|\s*:)", token):
                assert any("prefers-reduced-motion: no-preference" in block for block in stack)
            if delimiter == "}":
                assert stack
                stack.pop()
    assert not stack


def test_review_toast_accounts_for_mobile_tabbar_and_safe_area():
    source = (STATIC / "review-extras.css").read_text(encoding="utf-8")
    assert "env(safe-area-inset-bottom" in source
    assert re.search(r"var\(--[\w-]*(?:tabbar|bottom-bar|bottom-nav|nav-height)[\w-]*", source)
