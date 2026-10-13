"""复习=重做：新静态资源（redo-flow.js/css、index.html 接线）的 CSP 与主题静态检查。"""
from pathlib import Path
import re

import pytest

from test_review_feel_static import Markup

STATIC = Path(__file__).resolve().parents[1] / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")


def test_redo_flow_js_respects_csp_and_exports_api():
    source = (STATIC / "redo-flow.js").read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in source, forbidden
    assert "window.RedoFlow" in source
    for name in ("normalize", "diffLines", "similarityToGrade", "redoGradeSuggest",
                 "buildEvidence", "startTimer", "fetchHint", "REDO_LIMITS",
                 "DEFAULT_REDO_LIMIT", "MODE_LABEL"):
        assert name in source, name


def test_redo_flow_css_uses_theme_tokens_and_motion_gates():
    source = (STATIC / "redo-flow.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)
    assert "!important" not in source
    # 颜色走主题变量
    assert "var(--" in source
    # 动效门：任何 animation/transition 都要在 prefers-reduced-motion: no-preference 内
    stack = []
    for match in re.finditer(r"([^{};]*)([{};])", re.sub(r"/\*[\s\S]*?\*/", "", source)):
        token, delimiter = match.groups()
        if delimiter == "{":
            stack.append(token.strip())
        else:
            if re.match(r"\s*(?:animation|transition)(?:-|\s*:)", token):
                assert any("prefers-reduced-motion: no-preference" in block for block in stack)
            if delimiter == "}":
                assert stack
                stack.pop()
    assert not stack


def test_redo_assets_wired_in_index_with_versions_and_order():
    markup = Markup(INDEX)
    scripts = [attrs["src"] for tag, attrs in markup.nodes if tag == "script" and "src" in attrs]
    styles = [attrs.get("href") for tag, attrs in markup.nodes
              if tag == "link" and attrs.get("rel") == "stylesheet"]
    assert scripts.count("/static/redo-flow.js?v=1") == 1
    assert styles.count("/static/redo-flow.css?v=1") == 1
    # redo-flow 必须在 focus.js 和 app.js 之前（它们在运行时用 window.RedoFlow）
    assert scripts.index("/static/redo-flow.js?v=1") < scripts.index("/static/focus.js?v=8")
    assert scripts.index("/static/redo-flow.js?v=1") < scripts.index("/static/app.js?v=103")
    # 整页仍无内联 script / style 属性（CSP）
    assert all("style" not in attrs for _, attrs in markup.nodes)
    assert all("src" in attrs for tag, attrs in markup.nodes if tag == "script")


def test_account_redo_preference_select():
    select = re.search(r'<select id="account-redo-preference"[^>]*>([\s\S]*?)</select>', INDEX)
    assert select, "账号页需要 account-redo-preference 下拉"
    assert re.findall(r'<option value="([^"]*)"', select.group(1)) == ["redo_first", "recall_only"]
    markup = Markup(INDEX)
    assert any(tag == "label" and attrs.get("for") == "account-redo-preference"
               for tag, attrs in markup.nodes)
    # 帮助文字里写明默认值
    assert "默认重做优先" in INDEX


@pytest.mark.parametrize("filename", ["app.js", "focus.js", "overview.js", "offline-sync.js", "offline-review.js"])
def test_no_new_dangerous_dom_apis_in_touched_files(filename):
    source = (STATIC / filename).read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "insertAdjacentHTML", "document.write"):
        assert forbidden not in source, (filename, forbidden)
