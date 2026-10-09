"""套路库页面的静态契约：数据结构、CSP 兼容、无 innerHTML、只读本站数据。"""
import json
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "static"
HTML = (STATIC / "patterns.html").read_text(encoding="utf-8")
JS = (STATIC / "patterns.js").read_text(encoding="utf-8")
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
    assert "http://" not in HTML and "https://" not in HTML
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
