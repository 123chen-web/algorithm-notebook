"""Landing and authentication view contracts without a browser or temporary files."""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"
AUTH_FORMS = ("login-form", "register-form", "forgot-form", "reset-form")


class LandingDocument(HTMLParser):
    """Retain ancestry so forms and navigation belong to the intended view."""

    VOID_TAGS = frozenset(
        "area base br col embed hr img input link meta param source track wbr".split()
    )

    def __init__(self):
        super().__init__()
        self.elements = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        self.elements.append({
            "tag": tag,
            "attrs": dict(attrs),
            "ancestors": tuple(self.stack),
        })
        if tag not in self.VOID_TAGS:
            self.stack.append(len(self.elements) - 1)

    def handle_endtag(self, tag):
        for position in range(len(self.stack) - 1, -1, -1):
            if self.elements[self.stack[position]]["tag"] == tag:
                del self.stack[position:]
                break

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID_TAGS:
            self.handle_endtag(tag)

    def by_id(self, element_id):
        matches = [
            (index, node) for index, node in enumerate(self.elements)
            if node["attrs"].get("id") == element_id
        ]
        assert len(matches) == 1, f"Expected one #{element_id}, got {len(matches)}"
        return matches[0]


@pytest.fixture(scope="module")
def document():
    document = LandingDocument()
    document.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    document.close()
    return document


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def function_body(source, name):
    declaration = re.search(
        rf"(?m)^(?P<indent>[ \t]*)(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
        source,
    )
    assert declaration, f"Missing {name} function"
    remainder = source[declaration.end():]
    end = re.search(rf"(?m)^{re.escape(declaration['indent'])}\}}", remainder)
    assert end, f"Unclosed {name} function"
    return remainder[:end.start()]


def test_all_three_views_wait_for_session_resolution(document):
    roots = [node for node in document.elements if node["tag"] == "html"]
    assert len(roots) == 1
    assert roots[0]["attrs"].get("data-view") == "pending"
    for element_id in ("intro", "auth", "app"):
        _, node = document.by_id(element_id)
        assert "hidden" in node["attrs"], element_id


def test_authentication_forms_belong_only_to_the_auth_view(document):
    auth_index, auth = document.by_id("auth")
    intro_index, _ = document.by_id("intro")
    app_index, _ = document.by_id("app")
    assert auth["attrs"].get("aria-label") == "账号登录与注册"
    for element_id in AUTH_FORMS:
        _, node = document.by_id(element_id)
        assert node["tag"] == "form"
        assert auth_index in node["ancestors"]
        assert intro_index not in node["ancestors"]
        assert app_index not in node["ancestors"]
    assert not any(
        node["tag"] == "form" and intro_index in node["ancestors"]
        for node in document.elements
    )


def test_welcome_and_auth_have_native_hash_navigation(document):
    for parent_id, destination in (("intro", "#/auth"), ("auth", "#/welcome")):
        parent_index, _ = document.by_id(parent_id)
        links = [
            node for node in document.elements
            if parent_index in node["ancestors"]
            and node["tag"] == "a"
            and node["attrs"].get("href") == destination
        ]
        assert links, f"Missing {destination} link within #{parent_id}"


def test_hash_routes_keep_authenticated_users_in_the_app(app_source):
    route = function_body(app_source, "renderPageRoute")
    assert re.search(r"addEventListener\(\s*['\"]hashchange['\"]", app_source)
    assert re.search(r"user\s*\?\s*['\"]app['\"]", route)
    for destination in ("welcome", "auth", "app"):
        assert re.search(rf"['\"]{destination}['\"]", route)
    assert "#/auth" in route
    assert "resetToken" in route
    assert "history.replaceState" in route
    assert "location.search" in route, "Routing must preserve password-reset query parameters"


def test_session_pending_cannot_reveal_a_public_view(app_source):
    assert re.search(r"\blet\s+sessionReady\s*=\s*false\s*;", app_source)
    route = function_body(app_source, "renderPageRoute")
    guard = re.search(r"if\s*\(\s*!sessionReady\s*\)\s*return\s*;", route)
    assert guard, "Route rendering must wait for the initial authentication check"
    visibility = re.search(r"\.hidden\s*=", route)
    assert visibility and guard.end() < visibility.start()
    enter = function_body(app_source, "enterApp")
    check = re.search(r"user\s*=\s*await\s+api\(\s*['\"]/api/me['\"]\s*\)", enter)
    ready = re.search(r"sessionReady\s*=\s*true\s*;", enter)
    render = re.search(r"\brenderPageRoute\s*\(", enter)
    assert check and ready and render
    assert check.end() < ready.start() < render.start()


def test_intro_opening_does_not_override_route_visibility():
    source = (STATIC / "intro.js").read_text(encoding="utf-8")
    assert not re.search(r"\bintro\.hidden\s*=", source)
    assert "MutationObserver" in source
