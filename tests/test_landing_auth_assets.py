"""Landing and authentication view contracts without a browser or temporary files."""

from html.parser import HTMLParser
from pathlib import Path
import re
from xml.etree import ElementTree

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


def test_login_and_registration_have_one_selected_accessible_tab(document):
    switch_index, switch = document.by_id("auth-switch")
    assert switch["attrs"].get("role") == "tablist"
    assert switch["attrs"].get("aria-label")
    for tab_id, form_id, selected in (
        ("auth-login-tab", "login-form", True),
        ("auth-register-tab", "register-form", False),
    ):
        _, tab = document.by_id(tab_id)
        assert switch_index in tab["ancestors"]
        assert tab["tag"] == "button"
        assert tab["attrs"].get("type") == "button"
        assert tab["attrs"].get("role") == "tab"
        assert tab["attrs"].get("aria-controls") == form_id
        assert tab["attrs"].get("aria-selected") == str(selected).lower()
        assert tab["attrs"].get("tabindex", "0") == ("0" if selected else "-1")
        _, form = document.by_id(form_id)
        assert form["attrs"].get("role") == "tabpanel"
        assert form["attrs"].get("aria-labelledby") == tab_id
    visible_forms = [
        form_id for form_id in AUTH_FORMS
        if "hidden" not in document.by_id(form_id)[1]["attrs"]
    ]
    assert visible_forms == ["login-form"]


def test_authentication_view_removes_eyebrow_labels(document):
    auth_index, _ = document.by_id("auth")
    assert not any(
        auth_index in node["ancestors"]
        and "eyebrow" in node["attrs"].get("class", "").split()
        for node in document.elements
    )


@pytest.mark.parametrize(
    "form_id,field_name,required_attributes",
    [
        ("login-form", "username", {"maxlength": "32", "autocomplete": "username"}),
        ("login-form", "password", {
            "type": "password", "minlength": "6", "maxlength": "128",
            "autocomplete": "current-password",
        }),
        ("register-form", "username", {"maxlength": "32", "autocomplete": "username"}),
        ("register-form", "password", {
            "type": "password", "minlength": "6", "maxlength": "128",
            "autocomplete": "new-password",
        }),
        ("register-form", "email", {
            "type": "email", "maxlength": "254", "autocomplete": "email",
        }),
        ("register-form", "invite_code", {"maxlength": "256", "autocomplete": "off"}),
        ("register-form", "timezone", {"maxlength": "64"}),
        ("forgot-form", "email", {
            "type": "email", "maxlength": "254", "autocomplete": "email",
        }),
        ("reset-form", "password", {
            "type": "password", "minlength": "6", "maxlength": "128",
            "autocomplete": "new-password",
        }),
    ],
)
def test_authentication_input_validation_is_preserved(
    document, form_id, field_name, required_attributes,
):
    form_index, _ = document.by_id(form_id)
    fields = [
        node for node in document.elements
        if form_index in node["ancestors"]
        and node["tag"] == "input"
        and node["attrs"].get("name") == field_name
    ]
    assert len(fields) == 1
    attributes = fields[0]["attrs"]
    assert "required" in attributes
    for name, value in required_attributes.items():
        assert attributes.get(name) == value


@pytest.mark.parametrize(
    "form_id,endpoint,payload_fields",
    [
        ("login-form", "/api/auth/login", ("formObject(form)",)),
        ("register-form", "/api/auth/register", ("formObject(form)",)),
        ("forgot-form", "/api/auth/forgot-password", ("email: form.email.value",)),
        ("reset-form", "/api/auth/reset-password", ("token: resetToken", "password: form.password.value")),
    ],
)
def test_authentication_submit_handlers_preserve_the_api_contract(
    app_source, form_id, endpoint, payload_fields,
):
    handler = re.search(
        rf'(?m)^\$\(["\']#{re.escape(form_id)}["\']\)\.addEventListener\(["\']submit["\'],[\s\S]*?^\}}\);',
        app_source,
    )
    assert handler, f"Missing submit handler for #{form_id}"
    body = handler.group()
    assert "event.preventDefault()" in body
    assert re.search(rf'\bapi\(["\']{re.escape(endpoint)}["\']', body)
    assert re.search(r'method\s*:\s*["\']POST["\']', body)
    for field in payload_fields:
        assert field in body


def test_aria_descriptions_keep_existing_hint_targets(document):
    original_hints = (
        "login-username-hint", "register-username-hint", "register-password-hint",
        "register-email-hint", "timezone-hint", "forgot-email-hint", "reset-password-hint",
    )
    described_ids = set()
    for node in document.elements:
        for element_id in node["attrs"].get("aria-describedby", "").split():
            document.by_id(element_id)
            described_ids.add(element_id)
    assert set(original_hints) <= described_ids


def test_both_trial_entries_are_native_buttons_in_their_views(document, app_source):
    for button_id, parent_id in (("trial-start", "intro"), ("auth-trial-start", "auth")):
        parent_index, _ = document.by_id(parent_id)
        _, button = document.by_id(button_id)
        assert button["tag"] == "button"
        assert button["attrs"].get("type") == "button"
        assert parent_index in button["ancestors"]
        assert re.search(
            rf'\$\(["\']#{re.escape(button_id)}["\']\)\.addEventListener\(["\']click["\'],\s*startTrial\)',
            app_source,
        )
    trial = function_body(app_source, "startTrial")
    assert re.search(r'\bapi\(["\']/api/auth/trial["\']', trial)
    assert re.search(r'method\s*:\s*["\']POST["\']', trial)
    assert "timezone:" in trial
    assert "enterApp()" in trial


@pytest.fixture(scope="module")
def scene_source():
    return (STATIC / "scenes.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def scene_config(scene_source):
    arrays = re.findall(r"\bconst\s+SCENES\s*=\s*\[([\s\S]*?)\]\s*;", scene_source)
    assert len(arrays) == 1, "All scene choices must come from one configuration array"
    entries = re.findall(r"\{([^{}]*)\}", arrays[0])
    assert 3 <= len(entries) <= 4
    scenes = []
    for entry in entries:
        fields = re.findall(r'\b(id|name|src|accent)\s*:\s*(["\'])(.*?)\2', entry)
        assert len(fields) == 4
        scene = {name: value for name, _, value in fields}
        assert set(scene) == {"id", "name", "src", "accent"}
        assert all(scene.values())
        scenes.append(scene)
    return scenes


def test_scene_configuration_contains_unique_local_svg_assets(scene_config):
    assert len({scene["id"] for scene in scene_config}) == len(scene_config)
    assert len({scene["src"] for scene in scene_config}) == len(scene_config)
    for scene in scene_config:
        assert re.fullmatch(r"/static/scenes/[a-z0-9-]+\.svg", scene["src"])
        assert re.fullmatch(r"#[0-9a-fA-F]{6}", scene["accent"])
        assert (STATIC / scene["src"].removeprefix("/static/")).is_file()


def test_scene_svgs_are_well_formed_self_contained_and_script_free(scene_config):
    for scene in scene_config:
        source = (STATIC / scene["src"].removeprefix("/static/")).read_text(encoding="utf-8")
        assert "<!DOCTYPE" not in source.upper()
        assert "<!ENTITY" not in source.upper()
        svg = ElementTree.fromstring(source)
        assert svg.tag == "{http://www.w3.org/2000/svg}svg"
        assert svg.get("viewBox")
        assert svg.find("{http://www.w3.org/2000/svg}title") is not None
        assert svg.find("{http://www.w3.org/2000/svg}desc") is not None
        for element in svg.iter():
            tag = element.tag.rsplit("}", 1)[-1].lower()
            assert tag not in {"script", "foreignobject"}
            for key, value in element.attrib.items():
                name = key.rsplit("}", 1)[-1].lower()
                assert not name.startswith("on"), "SVG scene must not contain event handlers"
                if name in {"href", "src"}:
                    assert value.startswith("#"), "SVG references must remain within the scene"
                assert not re.search(r"(?:https?:|//|data:|javascript:)", value, re.IGNORECASE)
                for reference in re.findall(r"url\(([^)]*)\)", value):
                    assert reference.strip(" '\"").startswith("#")
            if tag == "style":
                assert not re.search(r"@import|https?:|data:|javascript:", element.text or "", re.IGNORECASE)


def test_scene_controls_are_accessible_and_manually_selectable(document, scene_source):
    _, backdrop = document.by_id("scene-backdrop")
    assert backdrop["attrs"].get("aria-hidden") == "true"
    intro_index, _ = document.by_id("intro")
    _, dots = document.by_id("scene-dots")
    assert intro_index in dots["ancestors"]
    assert dots["attrs"].get("role") == "group"
    assert dots["attrs"].get("aria-label")
    _, play = document.by_id("scene-play")
    assert play["tag"] == "button"
    assert play["attrs"].get("type") == "button"
    assert play["attrs"].get("aria-pressed") == "false"
    for contract in (
        'document.createElement("button")', 'button.type = "button"',
        'button.setAttribute("aria-label"', 'button.setAttribute("aria-pressed"',
        'button.addEventListener("click"', 'dots.addEventListener("keydown"',
        "ArrowLeft", "ArrowRight", "Home", "End", ".focus()",
    ):
        assert contract in scene_source


def test_mobile_scene_swipes_preserve_controls_and_vertical_scrolling(scene_source):
    for event in ("pointerdown", "pointerup", "pointercancel"):
        assert re.search(rf'addEventListener\(["\']{event}["\']', scene_source)
    assert re.search(r'pointerType\s*!==\s*["\']touch["\']', scene_source)
    assert 'closest("a, button, input, select")' in scene_source
    assert "Math.abs(dx) > Math.abs(dy)" in scene_source
    assert "selectScene((index" in scene_source


def test_scene_autoplay_requires_desktop_visible_welcome_without_reduced_motion(scene_source):
    gate = re.search(r"\bconst\s+canPlay\s*=\s*\(\)\s*=>\s*([^;]+);", scene_source)
    assert gate
    for condition in ("pageActive", "!document.hidden", "!reducedMotion.matches", "!mobile.matches"):
        assert condition in gate[1]
    assert re.search(r'dataset\.view\s*===\s*["\']welcome["\']', gate[1])
    for query in ("prefers-reduced-motion: reduce", "max-width: 720px", "hover: none", "pointer: coarse"):
        assert query in scene_source
    reconcile = function_body(scene_source, "reconcile")
    assert re.search(r"if\s*\(\s*canPlay\(\)\s*\)\s*timer\s*=", reconcile)
    assert re.search(r"setTimeout\([\s\S]*,\s*9000\s*\)", reconcile)
    assert "window.clearTimeout(timer)" in reconcile
    assert "timer = null" in reconcile
    for listener in (
        'reducedMotion.addEventListener("change", reconcile)',
        'mobile.addEventListener("change", reconcile)',
        'document.addEventListener("visibilitychange", reconcile)',
    ):
        assert listener in scene_source
    assert re.search(r'addEventListener\(["\']pagehide["\'],[^\n]*pageActive\s*=\s*false[^\n]*reconcile\(\)', scene_source)
    assert re.search(r'addEventListener\(["\']pageshow["\'],[^\n]*pageActive\s*=\s*true[^\n]*reconcile\(\)', scene_source)


def test_scene_assets_have_an_original_artwork_declaration():
    readme = (STATIC.parent / "README.md").read_text(encoding="utf-8")
    assert re.search(r"原创[^\n]*SVG|SVG[^\n]*原创", readme)
    assert "static/scenes/" in readme


def test_app_navigation_click_handlers_cannot_include_the_route_root(document, app_source):
    app_index, _ = document.by_id("app")
    navigation_buttons = [
        node for node in document.elements
        if "data-view" in node["attrs"] and node["tag"] != "html"
    ]
    assert navigation_buttons
    assert all(
        node["tag"] == "button" and app_index in node["ancestors"]
        for node in navigation_buttons
    )
    bindings = re.findall(
        r'(?m)^document\.querySelectorAll\((["\'])([^"\']+)\1\)\.forEach\(\(button\)\s*=>\s*\{([\s\S]*?)^\}\);',
        app_source,
    )
    navigation_selectors = [
        selector for _, selector, body in bindings
        if re.search(r'button\.addEventListener\(["\']click["\']', body)
        and "showView(button.dataset.view)" in body
    ]
    assert navigation_selectors == ["#app button[data-view]"], (
        "The html data-view route root must never receive app navigation click handlers"
    )
