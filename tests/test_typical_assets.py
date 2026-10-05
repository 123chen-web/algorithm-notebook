"""错因专题页"我的三大典型失误"与考前一页纸的静态契约检查。"""
from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


@pytest.fixture(scope="module")
def assets():
    return {name: (STATIC / name).read_text(encoding="utf-8") for name in (
        "index.html", "typical.js", "typical.css", "app.js", "shell.js",
    )}


def test_versioned_assets_load_before_app(assets):
    html = assets["index.html"]
    css = re.search(r'<link rel="stylesheet" href="/static/typical\.css\?v=\d+">', html)
    script = re.search(r'<script defer src="/static/typical\.js\?v=\d+"></script>', html)
    app = re.search(r'<script defer src="/static/app\.js\?v=\d+">', html)
    assert css and script and app
    assert script.start() < app.start()


def test_clusters_page_carries_the_card_and_sheet_markup(assets):
    html = assets["index.html"]
    start = html.index('id="clusters-page"')
    end = html.index('id="weekly-recap-page"', start)
    region = html[start:end]
    for name in (
        "typical-card", "typical-print-open", "typical-status", "typical-guide",
        "typical-list", "typical-sheet-wrap", "typical-sheet",
        "typical-sheet-print", "typical-sheet-close",
    ):
        assert region.count(f'id="{name}"') == 1, name
    assert "我的三大典型失误" in region
    assert "考前一页纸" in region
    assert "打印 / 保存为 PDF" in region
    assert re.search(r'<section id="typical-card"[^>]*\bhidden\b', region)
    assert re.search(r'id="typical-sheet-wrap"[^>]*\bhidden\b', region)


def test_script_contract_and_safe_dom(assets):
    script = assets["typical.js"]
    for forbidden in (
        "innerHTML", "insertAdjacentHTML", "document.write", "eval(",
        ".localStorage", "sessionStorage", "setAttribute(\"style\"",
    ):
        assert forbidden not in script, forbidden
    assert not re.search(r"\bfetch\(", script), "请求必须走宿主注入的 hooks.api"
    assert "window.Typical" in script
    for method in ("configure", "mount", "reset"):
        assert re.search(rf"{method}\s*[:(]", script), method
    # 迟到响应守卫：登录代次、用户 id、当前视图、请求序号四件套。
    assert "getEpoch()" in script and "getView()" in script
    assert 'getView() === "clusters"' in script
    # 用户可控文字一律走 textContent / createTextNode。
    assert "textContent" in script


def test_app_wiring(assets):
    app = assets["app.js"]
    assert "window.Typical?.configure(" in app
    assert "window.Typical?.reset();" in assets["shell.js"]
    # showView 进入错因专题时挂载卡片；离开后迟到响应由视图守卫丢弃。
    branch = re.search(r'else if \(view === "clusters"\) \{[\s\S]*?\}', app)
    assert branch and "await window.Clusters.load();" in branch[0]
    assert "window.Typical?.mount($(\"#typical-card\"));" in branch[0]


def test_styles_use_tokens_and_print_is_black_on_white(assets):
    css = assets["typical.css"]
    assert "!important" not in css
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", css)
    assert 'style="' not in assets["index.html"].split('id="clusters-page"')[1].split("</section>")[0]
    # A4 打印视图：@page 定尺寸，打印态黑白，且隐藏页面其余部分。
    assert "@page" in css and re.search(r"size:\s*A4", css)
    print_block = re.search(r"@media print \{([\s\S]*)\}\s*$", css)
    assert print_block, "打印规则放在文件末尾的 @media print 块"
    body = print_block.group(1)
    assert "color: black" in body and "background: white" in body
    assert "display: none" in body
    assert re.search(r"border(?:-bottom|-color)?:\s*1px solid black|border-color:\s*black", body)


class IdParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.ids[attrs["id"]] = attrs


def test_node_behaviour(assets):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required for the JS behaviour checks")
    parser = IdParser()
    parser.feed(assets["index.html"])
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("typical_behaviour.cjs"))],
        text=True, encoding="utf-8", capture_output=True, timeout=60, cwd=ROOT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
