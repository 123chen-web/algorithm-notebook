"""套路库页面的静态契约：数据结构、CSP 兼容、无 innerHTML、只读本站数据。"""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "patterns.html").read_text(encoding="utf-8")
JS = (
    (STATIC / "patterns.js").read_text(encoding="utf-8")
    + "\n"
    + (STATIC / "pattern-render.js").read_text(encoding="utf-8")
)
CSS = (STATIC / "patterns.css").read_text(encoding="utf-8")
DATA = json.loads((STATIC / "data" / "patterns.json").read_text(encoding="utf-8"))
FIELDS = {"id", "title", "category", "level", "signals", "idea", "template_python", "template_cpp",
          "complexity", "pitfalls", "confusions", "examples", "related"}


def test_data_has_the_agreed_shape_and_unique_ids():
    assert len(DATA) >= 40
    ids = [item["id"] for item in DATA]
    assert len(ids) == len(set(ids))
    for item in DATA:
        assert FIELDS <= item.keys(), item["id"]
        assert item["level"] in {"入门", "进阶", "竞赛"}
        assert len(item["signals"]) >= 3 and len(item["pitfalls"]) >= 3 and 3 <= len(item["examples"]) <= 5
        assert all(related in ids for related in item["related"])
        assert "http" not in json.dumps(item, ensure_ascii=False)


def test_page_is_csp_safe_and_self_contained():
    assert not re.search(r"\bstyle\s*=", HTML)
    assert not re.search(r"<script\b(?![^>]*\bsrc=)[^>]*>", HTML)
    # 分享预览用的 canonical / og / twitter 标签允许写绝对网址，它们不加载任何资源。
    html_without_share_meta = re.sub(
        r'<(?:link rel="canonical"|meta (?:property="og:|name="twitter:))[^>]*>', "", HTML
    )
    assert "http://" not in html_without_share_meta and "https://" not in html_without_share_meta
    for source in (JS, CSS):
        assert "innerHTML" not in source and "outerHTML" not in source and "insertAdjacentHTML" not in source
        assert "eval(" not in source and "document.write" not in source
    assert not re.search(r"\.style\.\w+\s*=", JS)
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", CSS), "只用主题令牌，不写十六进制颜色"


def test_page_only_reads_its_own_static_data():
    assert re.findall(r'fetch\("([^"]+)"', JS) == ["/static/data/patterns.json"]
    assert 'credentials: "omit"' in JS
    assert "localStorage" not in JS and "sessionStorage" not in JS


def test_every_asset_the_page_links_exists():
    for path in re.findall(r'(?:href|src)="(/static/[^"?]+)', HTML):
        assert (STATIC.parent / path.lstrip("/")).exists(), path


def test_example_links_only_use_whitelisted_domains():
    # 例题链接的域名白名单：只允许洛谷和力扣，防止数据里混入别的域名。
    urls = re.findall(r"https://[A-Za-z0-9.-]+", JS)
    assert urls, "patterns.js 应该生成例题链接"
    for url in urls:
        host = url.split("//", 1)[1]
        assert host in {"www.luogu.com.cn", "leetcode.cn"}, url
    assert "https://www.luogu.com.cn/problem/" in JS
    assert "https://leetcode.cn/problemset/?search=" in JS
    # 链接在新标签页打开，不把本站窗口交出去。
    assert 'link.setAttribute("target", "_blank")' in JS
    assert 'link.setAttribute("rel", "noopener noreferrer")' in JS


def test_example_sources_are_all_linkable():
    # 数据里的 source 必须都在白名单里，否则前端只会显示文字。
    sources = {example["source"] for item in DATA for example in item["examples"]}
    assert sources <= {"luogu", "leetcode-cn"}, sources
