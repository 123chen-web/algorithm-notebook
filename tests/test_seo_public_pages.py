"""公开页面的 SEO / 分享预览：robots.txt、sitemap.xml、meta 标签、og-image。"""
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGES = ("index.html", "patterns.html", "help.html", "extension.html")
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
BASE = "https://ouyeoy.example"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setenv("PUBLIC_BASE_URL", BASE + "/")
    with TestClient(main.app) as c:
        yield c


def test_robots_txt_public_no_login_and_blocks_api(client):
    r = client.get("/robots.txt")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    lines = r.text.splitlines()
    assert "User-agent: *" in lines
    assert "Disallow: /api/" in lines
    assert "Allow: /static/patterns.html" in lines
    assert f"Sitemap: {BASE}/sitemap.xml" in lines


def test_sitemap_is_valid_xml_with_absolute_urls_on_base(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    root = ET.fromstring(r.content)
    assert root.tag == NS + "urlset"
    locs = [el.text for el in root.iter(NS + "loc")]
    assert locs and len(locs) == len(set(locs))
    assert all(loc.startswith(BASE + "/") for loc in locs)
    for must in ("/", "/static/patterns.html", "/static/help.html", "/static/extension.html"):
        assert BASE + must in locs
    joined = " ".join(locs)
    for private in ("/api", "billing", "admin", "pay"):
        assert private not in joined


def test_sitemap_pages_all_resolve(client):
    locs = [el.text for el in ET.fromstring(client.get("/sitemap.xml").content).iter(NS + "loc")]
    for loc in locs:
        assert client.get(loc[len(BASE):]).status_code == 200, loc


def test_base_url_falls_back_to_request_when_unset(monkeypatch):
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.delenv("PUBLIC_BASE_URL", raising=False)
    with TestClient(main.app, base_url="http://testserver") as c:
        text = c.get("/robots.txt").text
    assert "Sitemap: http://testserver/sitemap.xml" in text


def test_routes_not_in_api_namespace_or_schema():
    schema_paths = main.app.openapi()["paths"]
    assert "/robots.txt" not in schema_paths and "/sitemap.xml" not in schema_paths


@pytest.mark.parametrize("name", PAGES)
def test_public_pages_have_share_meta(name):
    html = (STATIC / name).read_text(encoding="utf-8")
    head = html[: html.index("</head>")]
    for pattern in (
        r'<meta name="description" content="[^"]{10,}">',
        r'<link rel="canonical" href="https://[^"]+">',
        r'<meta property="og:type" content="website">',
        r'<meta property="og:site_name" content="欧叶OY">',
        r'<meta property="og:title" content="[^"]+">',
        r'<meta property="og:description" content="[^"]+">',
        r'<meta property="og:url" content="https://[^"]+">',
        r'<meta property="og:image" content="https://[^"]+/static/og-image\.png">',
        r'<meta property="og:locale" content="zh_CN">',
        r'<meta name="twitter:card" content="summary_large_image">',
    ):
        assert re.search(pattern, head), (name, pattern)
    assert len(re.findall(r'name="description"', head)) == 1
    # 不引入行内脚本：每个 <script> 都必须带 src。
    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "src=" in tag, (name, tag)
    assert "application/ld+json" not in html


def test_og_image_is_1200x630_png_and_small():
    data = (STATIC / "og-image.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert (width, height) == (1200, 630)
    assert len(data) < 150 * 1024
