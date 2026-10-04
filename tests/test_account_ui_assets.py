"""Account security and legal-entry contracts without a browser or temporary files."""

from pathlib import Path
import re

import pytest

from test_cursor_fx_assets import css_declarations, css_selectors
from test_landing_auth_assets import LandingDocument, function_body


STATIC = Path(__file__).resolve().parents[1] / "static"
ACCOUNT_BUTTONS = {
    "account-password": "修改密码…",
    "account-revoke-others": "退出其他设备",
    "account-delete": "注销账号…",
}
LEGAL_LINKS = {"/terms": "服务条款", "/privacy": "隐私政策"}


class AccountDocument(LandingDocument):
    def __init__(self):
        super().__init__()
        self.text_nodes = []

    def handle_data(self, text):
        self.text_nodes.append((tuple(self.stack), text))

    def text_within(self, index):
        return "".join(text for ancestors, text in self.text_nodes if index in ancestors)


@pytest.fixture(scope="module")
def document():
    document = AccountDocument()
    document.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    document.close()
    return document


@pytest.fixture(scope="module")
def account_source():
    return (STATIC / "account.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def app_source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


def descendants(document, parent_index, tag=None):
    return [
        (index, node) for index, node in enumerate(document.elements)
        if parent_index in node["ancestors"] and (tag is None or node["tag"] == tag)
    ]


def assert_legal_links(document, parent_index, *, book_titles=False):
    links = [
        (index, node) for index, node in descendants(document, parent_index, "a")
        if node["attrs"].get("href") in LEGAL_LINKS
    ]
    assert [node["attrs"]["href"] for _, node in links] == list(LEGAL_LINKS)
    for index, link in links:
        attributes = link["attrs"]
        assert attributes.get("target") == "_blank"
        assert "noopener" in attributes.get("rel", "").split()
        text = LEGAL_LINKS[attributes["href"]]
        assert document.text_within(index).strip() == (f"《{text}》" if book_titles else text)


def submit_handler(source, form_id):
    handler = re.search(
        rf'(?m)^\$\(["\']#{re.escape(form_id)}["\']\)\.addEventListener\(["\']submit["\'],[\s\S]*?^\}}\);',
        source,
    )
    assert handler, f"Missing submit handler for #{form_id}"
    return handler.group()


def test_account_menu_contains_all_security_entries_and_legal_links(document):
    panels = [
        (index, node) for index, node in enumerate(document.elements)
        if "account-menu-panel" in node["attrs"].get("class", "").split()
    ]
    assert len(panels) == 1
    panel_index, panel = panels[0]
    assert any(
        document.elements[index]["tag"] == "details"
        and "account-menu" in document.elements[index]["attrs"].get("class", "").split()
        for index in panel["ancestors"]
    )
    for button_id, text in ACCOUNT_BUTTONS.items():
        index, button = document.by_id(button_id)
        assert panel_index in button["ancestors"]
        assert button["tag"] == "button" and button["attrs"].get("type") == "button"
        assert document.text_within(index).strip() == text
    assert_legal_links(document, panel_index)
    assert any(
        node["attrs"].get("href") == "/api/export"
        for _, node in descendants(document, panel_index, "a")
    )


def test_trial_accounts_hide_security_entries_without_hiding_legal_links(app_source):
    render = function_body(app_source, "renderUserInfo")
    for button_id in ACCOUNT_BUTTONS:
        assert button_id in render
    assert re.search(r"\.hidden\s*=\s*(?:Boolean\()?user\.is_trial", render)
    assert not re.search(r'["\']/terms["\']|["\']/privacy["\']', render)


def test_account_dialog_is_a_closed_labelled_native_modal(document, account_source):
    dialog_index, dialog = document.by_id("account-dialog")
    assert dialog["tag"] == "dialog"
    assert "open" not in dialog["attrs"]
    assert dialog["attrs"].get("aria-labelledby") == "account-dialog-title"
    _, content = document.by_id("account-dialog-content")
    assert dialog_index in content["ancestors"]
    assert "account-dialog-title" in account_source
    assert re.search(r'(?:createElement|node|element)\(["\']h2["\']', account_source)
    assert re.search(r"\.showModal\s*\(", account_source)
    assert re.search(r"\.close\s*\(", account_source)
    assert re.search(r'addEventListener\(["\']cancel["\']', account_source)
    assert re.search(r'setAttribute\(["\']role["\'],\s*["\']alert["\']', account_source)


def test_registration_requires_explicit_boolean_legal_acceptance(document, app_source):
    form_index, _ = document.by_id("register-form")
    checkboxes = [
        (index, node) for index, node in descendants(document, form_index, "input")
        if node["attrs"].get("name") == "accept_terms"
    ]
    assert len(checkboxes) == 1
    checkbox_index, checkbox = checkboxes[0]
    assert checkbox["attrs"].get("type") == "checkbox"
    assert "required" in checkbox["attrs"]
    label_indices = [
        index for index in checkbox["ancestors"] if document.elements[index]["tag"] == "label"
    ]
    if checkbox["attrs"].get("id"):
        label_indices.extend(
            index for index, node in descendants(document, form_index, "label")
            if node["attrs"].get("for") == checkbox["attrs"]["id"]
        )
    assert label_indices, "Legal checkbox needs a visible label"
    label_index = label_indices[0]
    label_text = re.sub(r"\s+", "", document.text_within(label_index))
    assert "我已阅读并同意《服务条款》和《隐私政策》" in label_text
    assert_legal_links(document, label_index, book_titles=True)
    submit_buttons = [
        index for index, node in descendants(document, form_index, "button")
        if node["attrs"].get("type", "submit") == "submit"
    ]
    assert submit_buttons and checkbox_index < min(submit_buttons)
    handler = submit_handler(app_source, "register-form")
    assert re.search(r"accept_terms\s*:\s*true\b", handler)
    assert ".checked" in handler
    assert handler.index(".checked") < handler.index('api("/api/auth/register"')


@pytest.mark.parametrize("form_id,minimum,autocomplete", [
    ("login-form", "6", "current-password"),
    ("register-form", "8", "new-password"),
    ("reset-form", "8", "new-password"),
])
def test_password_minimum_changes_only_for_new_passwords(document, form_id, minimum, autocomplete):
    form_index, _ = document.by_id(form_id)
    passwords = [
        node for _, node in descendants(document, form_index, "input")
        if node["attrs"].get("name") == "password"
    ]
    assert len(passwords) == 1
    assert passwords[0]["attrs"].get("minlength") == minimum
    assert passwords[0]["attrs"].get("autocomplete") == autocomplete
    if form_id != "login-form":
        hint_index, _ = document.by_id(form_id.replace("-form", "-password-hint"))
        assert document.text_within(hint_index) == "至少 8 个字符。"


@pytest.mark.parametrize("view_id", ["intro", "auth"])
def test_each_public_view_has_its_own_legal_navigation(document, view_id):
    view_index, _ = document.by_id(view_id)
    navigations = [
        (index, node) for index, node in descendants(document, view_index, "nav")
        if node["attrs"].get("aria-label") == "法律信息"
    ]
    assert len(navigations) == 1
    assert_legal_links(document, navigations[0][0])


def test_email_binding_requires_and_submits_current_password(document, app_source):
    form_index, _ = document.by_id("email-prompt")
    passwords = [
        node for _, node in descendants(document, form_index, "input")
        if node["attrs"].get("name") == "password"
    ]
    assert len(passwords) == 1
    attributes = passwords[0]["attrs"]
    assert attributes.get("type") == "password"
    assert attributes.get("autocomplete") == "current-password"
    assert "required" in attributes
    assert any(document.elements[index]["tag"] == "label" for index in passwords[0]["ancestors"])
    handler = submit_handler(app_source, "email-prompt")
    assert 'api("/api/me/email"' in handler
    assert 'method: "PUT"' in handler
    assert "password: form.password.value" in handler


def test_account_assets_are_versioned_and_updated_hooks_are_loaded(document):
    for filename, tag, attribute in (
        ("account.js", "script", "src"), ("account.css", "link", "href"),
        ("app.js", "script", "src"), ("shell.js", "script", "src"),
    ):
        assets = [
            node for node in document.elements
            if node["tag"] == tag and node["attrs"].get(attribute, "").split("?", 1)[0] == f"/static/{filename}"
        ]
        assert len(assets) == 1
        assert re.fullmatch(rf"/static/{re.escape(filename)}\?v=\d+", assets[0]["attrs"][attribute])
        if tag == "script":
            assert "defer" in assets[0]["attrs"]
            assert "async" not in assets[0]["attrs"]
        else:
            assert assets[0]["attrs"].get("rel") == "stylesheet"
        version = int(assets[0]["attrs"][attribute].split("?v=")[1])
        if filename == "app.js":
            assert version >= 55
        elif filename == "shell.js":
            assert version >= 4


def test_account_actions_reuse_csrf_api_and_keep_local_lifecycle_guards(account_source, app_source):
    for endpoint in ("/api/me/password", "/api/me/sessions/revoke-others", "/api/me/delete-account"):
        assert endpoint in account_source
    assert re.search(r"\bapi\s*\(", account_source)
    assert not re.search(r"\b(?:fetch|XMLHttpRequest)\b", account_source)
    api = function_body(app_source, "api")
    assert '"X-CSRF-Protection": "1"' in api
    assert 'credentials: "same-origin"' in api
    assert re.search(r"\blet\s+pending\s*=", account_source)
    assert re.search(r"\blet\s+generation\s*=", account_source)
    assert re.search(r"\buser(?:\?\.|\.)id\b", account_source)
    assert not re.search(r"\brun\s*\(", account_source)
    assert re.search(r"window\.Account\s*=\s*\{[^}]*\bopenPassword\b[^}]*\bopenRevokeOthers\b[^}]*\bopenDelete\b[^}]*\bclose\b[^}]*\breset\b", account_source)
    assert "window.Account?.reset()" in function_body(app_source, "signedOut")
    shell_source = (STATIC / "shell.js").read_text(encoding="utf-8")
    assert "window.Account?.reset()" in function_body(shell_source, "reset")


def test_account_and_markup_preserve_csp_and_plain_text_rendering(document, account_source):
    assert "innerHTML" not in account_source
    assert "textContent" in account_source
    assert not re.search(r"\.on\w+\s*=|setAttribute\(\s*[\"']on\w+", account_source)
    for node in document.elements:
        assert "style" not in node["attrs"]
        assert not any(name.lower().startswith("on") for name in node["attrs"])


def test_account_styles_use_theme_tokens_and_gate_visual_enhancements():
    source = (STATIC / "account.css").read_text(encoding="utf-8")
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", source)
    assert "!important" not in source
    assert "var(--danger)" in source
    assert "::backdrop" in source
    assert "28rem" in source
    assert "max-width: 520px" in source
    assert ":focus-visible" in source
    declarations = list(css_declarations(source))
    for blocks, declaration in declarations:
        if re.match(r"(?:-webkit-)?backdrop-filter\s*:", declaration):
            assert any(
                block.startswith("@media") and "hover: hover" in block and "pointer: fine" in block
                for block in blocks
            )
            assert any(block.startswith("@supports") and "backdrop-filter" in block for block in blocks)
        if "color-mix(" in declaration and "transparent" in declaration:
            assert any(block.startswith("@supports") and "color-mix(" in block for block in blocks)
            for selector in css_selectors(blocks[-1]):
                assert re.match(r"html\[data-cursor-fx=([\"'])ripple\1\]", selector)
        assert not re.search(r"\b(?:rgba?|hsla?)\(\s*[\d.]", declaration)
    assert any(
        re.fullmatch(r"min-height\s*:\s*44px", declaration)
        and any("pointer: coarse" in block for block in blocks)
        for blocks, declaration in declarations
    ), "Touch controls need a 44px hit target"
    if re.search(r"(?:animation|transition)\s*:", source):
        assert "prefers-reduced-motion" in source
