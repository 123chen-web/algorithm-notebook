"""「更新了什么」：changelog.json 的格式、帮助页与主页对 whatsnew.js 的引用。"""

import json
import re
from datetime import date
from pathlib import Path

from static_helpers import versioned_src

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
CHANGELOG = STATIC / "data" / "changelog.json"
HELP_HTML = STATIC / "help.html"
INDEX_HTML = STATIC / "index.html"
WHATSNEW_JS = STATIC / "whatsnew.js"


def _releases():
    return json.loads(CHANGELOG.read_text(encoding="utf-8"))


def test_changelog_is_a_non_empty_list_of_releases():
    releases = _releases()
    assert isinstance(releases, list) and len(releases) >= 5
    for release in releases:
        assert set(release) == {"date", "title", "items"}
        assert isinstance(release["title"], str) and release["title"].strip()
        assert isinstance(release["items"], list) and release["items"]


def test_changelog_dates_are_iso_and_newest_first():
    dates = [release["date"] for release in _releases()]
    for value in dates:
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
        date.fromisoformat(value)
    assert dates == sorted(dates, reverse=True)
    assert len(set(dates)) == len(dates), "同一天只写一条更新"


def test_changelog_items_are_short_plain_text():
    for release in _releases():
        for item in release["items"] + [release["title"]]:
            assert isinstance(item, str) and item.strip()
            assert len(item) <= 60, item
            assert not re.search(r"<[^>]*>", item), f"不要在更新记录里写 HTML：{item}"


def test_help_page_has_whats_new_section_wired_to_the_script():
    html = HELP_HTML.read_text(encoding="utf-8")
    assert 'id="whats-new"' in html
    assert 'id="whats-new-list"' in html
    assert 'href="#whats-new"' in html, "目录里要有入口"
    assert f'<script defer src="{versioned_src("whatsnew.js")}"></script>' in html


def test_main_page_links_to_whats_new_with_a_hidden_dot():
    html = INDEX_HTML.read_text(encoding="utf-8")
    assert f'<script defer src="{versioned_src("whatsnew.js")}"></script>' in html
    assert 'href="/static/help.html#whats-new"' in html
    assert re.search(r'<span class="whatsnew-dot"[^>]*data-whatsnew-dot[^>]*\bhidden\b', html)


def test_whatsnew_script_stays_csp_safe_and_same_origin():
    js = WHATSNEW_JS.read_text(encoding="utf-8")
    for token in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
        assert token not in js, token
    assert "/static/data/changelog.json" in js
    assert "http://" not in js and "https://" not in js
