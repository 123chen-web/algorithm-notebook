"""Protect account controls and native quota semantics in the compact header."""

from html.parser import HTMLParser
from pathlib import Path


class HeaderDocument(HTMLParser):
    VOID_TAGS = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self):
        super().__init__()
        self.stack = []
        self.elements = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.elements.append((tag, attrs, tuple(self.stack)))
        if tag not in self.VOID_TAGS:
            self.stack.append((tag, attrs))

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break

    def by_id(self, element_id):
        matches = [node for node in self.elements if node[1].get("id") == element_id]
        assert len(matches) == 1
        return matches[0]


def document():
    result = HeaderDocument()
    source = Path(__file__).resolve().parents[1] / "static" / "index.html"
    result.feed(source.read_text(encoding="utf-8"))
    return result


def test_compact_header_keeps_account_details_and_logout_in_the_native_menu():
    page = document()
    for element_id in ("user-info-wrap", "logout", "account-theme", "account-scene"):
        _, _, ancestors = page.by_id(element_id)
        assert any(tag == "details" for tag, _ in ancestors)
        assert any("account-menu-panel" in attrs.get("class", "").split() for _, attrs in ancestors)
    tag, attrs, ancestors = page.by_id("logout")
    assert tag == "button" and attrs.get("type") == "button"
    assert any(tag == "header" for tag, _ in ancestors)


def test_compact_header_retains_native_quota_progress_and_both_text_references():
    page = document()
    tag, attrs, ancestors = page.by_id("home-quota-progress")
    assert tag == "progress"
    assert attrs.get("max") == "1" and attrs.get("value") == "0"
    assert attrs.get("aria-describedby") == "home-quota-text"
    assert any(attrs.get("id") == "home-quota" for _, attrs in ancestors)
    page.by_id("home-quota-text")
    assert any(tag == "label" and attrs.get("for") == "home-quota-progress" for tag, attrs, _ in page.elements)
