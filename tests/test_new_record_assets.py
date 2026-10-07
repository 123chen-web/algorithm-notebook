"""New-record form presentation contracts without a browser or temporary files.

Browser acceptance covers interaction, native validation, and rendered geometry;
these checks protect the document and progressive-enhancement boundaries.
"""

from pathlib import Path
import re

import pytest

from test_cursor_fx_assets import css_declarations, css_selectors
from test_layout_assets import layout_document


STATIC = Path(__file__).resolve().parents[1] / "static"


class FormDocument:
    """Add form lookups to the shared document parser."""

    def __init__(self):
        self.elements = layout_document()

    def by_id(self, element_id):
        matches = [
            (index, node) for index, node in enumerate(self.elements)
            if node["attrs"].get("id") == element_id
        ]
        assert len(matches) == 1, f"Expected one #{element_id}"
        return matches[0]

    def in_page(self):
        page_index, _ = self.by_id("new-page")
        return [
            (index, node) for index, node in enumerate(self.elements)
            if page_index in node["ancestors"]
        ]


def has_class(node, class_name):
    return class_name in node["attrs"].get("class", "").split()


@pytest.fixture(scope="module")
def document():
    return FormDocument()


@pytest.fixture(scope="module")
def form_source():
    return (STATIC / "new-record.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def form_css():
    return (STATIC / "new-record.css").read_text(encoding="utf-8")


def test_form_enhancement_is_local_versioned_and_deferred_after_app(document):
    scripts = [node["attrs"] for node in document.elements if node["tag"] == "script"]
    enhancement = [
        (index, attrs) for index, attrs in enumerate(scripts)
        if attrs.get("src", "").split("?", 1)[0] == "/static/new-record.js"
    ]
    app = [
        index for index, attrs in enumerate(scripts)
        if attrs.get("src", "").split("?", 1)[0] == "/static/app.js"
    ]
    assert len(enhancement) == len(app) == 1
    index, attrs = enhancement[0]
    assert "defer" in attrs and "async" not in attrs
    assert re.fullmatch(r"/static/new-record\.js\?v=\d+", attrs["src"])
    assert index > app[0]
    styles = [
        node["attrs"] for node in document.elements
        if node["tag"] == "link" and node["attrs"].get("rel") == "stylesheet"
        and node["attrs"].get("href", "").split("?", 1)[0] == "/static/new-record.css"
    ]
    assert len(styles) == 1
    assert re.fullmatch(r"/static/new-record\.css\?v=\d+", styles[0]["href"])


def test_upload_and_record_remain_independent_native_forms(document):
    photo_index, photo = document.by_id("problem-photo-form")
    record_index, record = document.by_id("problem-form")
    assert photo["tag"] == record["tag"] == "form"
    assert photo_index not in record["ancestors"]
    assert record_index not in photo["ancestors"]
    assert "novalidate" not in photo["attrs"] and "novalidate" not in record["attrs"]
    _, file_input = document.by_id("problem-photo-file")
    assert photo_index in file_input["ancestors"]
    assert record_index not in file_input["ancestors"]
    for _, node in document.in_page():
        if node["attrs"].get("name") in {"title", "zone", "language", "code", "thinking"}:
            assert record_index in node["ancestors"]
            assert photo_index not in node["ancestors"]


def test_original_field_validation_and_accessibility_contracts_survive(document):
    expected = {
        "title": ("input", {"required": None, "maxlength": "200"}),
        "zone": ("select", {"id": "problem-zone", "required": None}),
        "language": ("input", {"maxlength": "40", "value": "Python"}),
        "code": ("textarea", {
            "required": None, "maxlength": "40000", "rows": "10",
            "spellcheck": "false", "aria-describedby": "code-hint",
        }),
        "thinking": ("textarea", {"required": None, "maxlength": "8000", "rows": "4"}),
    }
    for name, (tag, required_attrs) in expected.items():
        matches = [node for _, node in document.in_page() if node["attrs"].get("name") == name]
        assert len(matches) == 1, name
        node = matches[0]
        assert node["tag"] == tag
        for attribute, value in required_attrs.items():
            assert attribute in node["attrs"] and node["attrs"][attribute] == value, (name, attribute)
        for attribute in ("required", "maxlength", "minlength"):
            if attribute not in required_attrs:
                assert attribute not in node["attrs"], (name, attribute)
        assert any(document.elements[index]["tag"] == "label" for index in node["ancestors"]), name
    for element_id, expected_attrs in {
        "problem-photo-form": {"aria-labelledby": "problem-photo-title"},
        "problem-photo-file": {
            "type": "file", "accept": "image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp",
            "aria-describedby": "problem-photo-hint",
        },
        "problem-photo-preview": {"alt": "所选题目照片预览", "hidden": None},
        "problem-photo-recognize": {"type": "submit", "data-blocked": "1", "disabled": None},
        "problem-photo-clear": {"type": "button", "hidden": None},
        "problem-photo-status": {"role": "status", "aria-live": "polite"},
        "mistake-inputs": {"role": "group", "aria-describedby": "mistake-hint"},
        "add-mistake": {"type": "button"},
        "problem-save": {"type": "submit"},
        "problem-photo-save": {"type": "submit", "hidden": None},
    }.items():
        _, node = document.by_id(element_id)
        for attribute, value in expected_attrs.items():
            assert attribute in node["attrs"] and node["attrs"][attribute] == value, (element_id, attribute)
    for element_id in (
        "problem-photo-title", "problem-photo-hint", "problem-photo-quota", "problem-form",
        "problem-basics-title", "code-hint", "problem-thinking-title", "problem-mistakes-title",
        "mistake-hint",
    ):
        document.by_id(element_id)


def test_new_page_has_four_numbered_cards_and_display_only_progress(document):
    cards = [(index, node) for index, node in document.in_page() if has_class(node, "form-card")]
    assert len(cards) == 4
    for card_index, _ in cards:
        children = [node for node in document.elements if card_index in node["ancestors"]]
        assert sum(has_class(node, "form-card-heading") for node in children) == 1
        assert any(has_class(node, "form-fields") for node in children)
        assert any(has_class(node, "form-number") for node in children)
    _, progress = document.by_id("form-section-progress")
    assert progress["attrs"].get("aria-hidden") == "true"
    assert progress["tag"] not in {"input", "button", "select"}


def test_record_modes_stay_in_the_desktop_grid_content_column(document):
    basics_index = next(index for index, node in document.in_page()
                        if node["attrs"].get("data-form-section") == "basics")
    direct = [node for node in document.elements
              if node["ancestors"] and node["ancestors"][-1] == basics_index]
    assert len(direct) == 2
    assert has_class(direct[0], "form-card-heading")
    assert has_class(direct[1], "form-fields")
    fields_index = next(index for index, node in enumerate(document.elements)
                        if node is direct[1])
    for name in ("problem-quick", "record-mode-hint", "problem-sentence-field"):
        _, node = document.by_id(name)
        assert fields_index in node["ancestors"]


def test_fold_buttons_own_initially_inert_contents_and_basics_remain_open(document):
    toggles = [(index, node) for index, node in document.in_page() if has_class(node, "form-fold-toggle")]
    assert len(toggles) == 2
    controls = set()
    for _, toggle in toggles:
        attrs = toggle["attrs"]
        assert toggle["tag"] == "button" and attrs.get("type") == "button"
        assert attrs.get("aria-expanded") == "false"
        assert attrs.get("aria-controls")
        controls.add(attrs["aria-controls"])
        content_index, content = document.by_id(attrs["aria-controls"])
        assert has_class(content, "form-fold-content")
        assert "inert" in content["attrs"] or "hidden" in content["attrs"]
        toggle_cards = [index for index in toggle["ancestors"] if has_class(document.elements[index], "form-card")]
        assert len(toggle_cards) == 1 and toggle_cards[0] in content["ancestors"]
        assert any(content_index in node["ancestors"] and node["tag"] in {"textarea", "div"} for node in document.elements)
    assert controls == {"problem-thinking-content", "problem-mistakes-content"}
    basics_index, basics = document.by_id("problem-basics-title")
    basics_cards = [index for index in basics["ancestors"] if has_class(document.elements[index], "form-card")]
    assert len(basics_cards) == 1
    assert not any(has_class(node, "form-fold-toggle") and basics_cards[0] in node["ancestors"] for node in document.elements)
    assert not any("inert" in document.elements[index]["attrs"] or "hidden" in document.elements[index]["attrs"] for index in basics["ancestors"] if index != document.by_id("new-page")[0] and document.elements[index]["tag"] != "main")


def test_native_invalid_capture_retains_browser_validation_and_restores_focus(document, form_source):
    assert re.search(
        r'addEventListener\(\s*["\']invalid["\'][\s\S]*?,\s*(?:true|\{\s*capture\s*:\s*true\s*\})\s*\)',
        form_source,
    ), "The non-bubbling invalid event needs a capture listener"
    assert "aria-expanded" in form_source and "inert" in form_source
    assert re.search(r'\.focus\s*\(', form_source)
    assert re.search(r'\.scrollIntoView\s*\(', form_source)
    assert not re.search(r'\.noValidate\s*=\s*true|setAttribute\(\s*["\']novalidate', form_source)
    assert not re.search(r'addEventListener\(\s*["\']submit["\']', form_source)
    _, record_form = document.by_id("problem-form")
    assert "novalidate" not in record_form["attrs"]


def test_dropzone_keeps_native_file_focus_and_preview_inside_clickable_label(document, form_css):
    _, file_input = document.by_id("problem-photo-file")
    _, preview = document.by_id("problem-photo-preview")
    assert "hidden" not in file_input["attrs"]
    assert int(file_input["attrs"].get("tabindex", "0")) >= 0
    dropzones = [
        (index, node) for index, node in document.in_page()
        if has_class(node, "photo-dropzone")
    ]
    assert len(dropzones) == 1
    label_index, label = dropzones[0]
    assert label["tag"] == "label" and label["attrs"].get("for") == "problem-photo-file"
    assert label_index in file_input["ancestors"] and label_index in preview["ancestors"]
    for element_id in ("problem-photo-recognize", "problem-photo-clear"):
        _, button = document.by_id(element_id)
        assert label_index not in button["ancestors"]
    for blocks, declaration in css_declarations(form_css):
        if not blocks:
            continue
        selector = blocks[-1]
        if "problem-photo-file" in selector or re.search(r'input\[type\s*=\s*["\']?file', selector):
            assert not re.fullmatch(r'(?:display\s*:\s*none|visibility\s*:\s*hidden)(?:\s*!important)?', declaration)
    assert re.search(r'\.photo-dropzone[^{}]*:focus(?:-within|-visible)?', form_css)


def test_drop_reuses_change_without_new_file_validation_or_network_calls(form_source):
    for event in ("dragenter", "dragover", "drop"):
        assert re.search(rf'["\']{event}["\']', form_source)
    assert re.search(r'dataTransfer\??\.files\??\s*\[\s*0\s*\]', form_source)
    assert re.search(r'\.files\s*=', form_source)
    assert re.search(r'dispatchEvent\(\s*new\s+Event\(\s*["\']change["\']', form_source)
    assert not re.search(r'\bfetch\s*\(|\bXMLHttpRequest\b|\bimport\s*\(|\brequire\s*\(', form_source)
    assert not re.search(r'\b(?:file|droppedFile)\.(?:size|type)\b', form_source)


def test_card_and_field_visuals_use_shared_theme_tokens(form_css):
    declarations = list(css_declarations(form_css))
    for blocks, declaration in declarations:
        name, separator, value = declaration.partition(":")
        name, value = name.strip(), value.strip()
        if not separator or name.startswith("--"):
            continue
        assert not re.search(r'#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?)\(\s*[\d.]', value), (blocks, declaration)
        if name.endswith("-radius"):
            assert re.fullmatch(r'(?:var\(--radius[\w-]*\)\s*)+', value), declaration
        if name in {"box-shadow", "background", "background-color", "border-color", "color"} and value not in {"none", "transparent", "inherit", "currentColor"}:
            assert "var(--" in value, declaration
    assert not re.search(r'html\[data-theme\s*=', form_css), "Form rules must be shared by the two themes"
    assert any("form-card" in blocks[-1] and declaration.startswith("background: var(--surface)") for blocks, declaration in declarations if blocks)
    assert any("border-color: var(--accent)" == declaration for _, declaration in declarations)
    assert any("box-shadow:" in declaration and "var(--" in declaration for _, declaration in declarations)


def test_desktop_card_keeps_heading_and_fields_in_two_columns(form_css):
    desktop_layouts = [
        (blocks, declaration) for blocks, declaration in css_declarations(form_css)
        if declaration.startswith("grid-template-columns:")
        and any(block.startswith("@media") and re.search(r'\(\s*min-width\s*:\s*1024px\s*\)', block) for block in blocks)
        and blocks and "form-card" in blocks[-1]
    ]
    assert desktop_layouts
    assert any("var(--form-aside-width)" in declaration and "minmax(" in declaration for _, declaration in desktop_layouts)


def test_new_form_blur_and_translucency_obey_existing_pointer_support_guards(form_css):
    for blocks, declaration in css_declarations(form_css):
        if re.match(r'(?:-webkit-)?backdrop-filter\s*:', declaration):
            assert any(
                block.startswith("@media")
                and re.search(r'\(\s*hover\s*:\s*hover\s*\)', block)
                and re.search(r'\(\s*pointer\s*:\s*fine\s*\)', block)
                for block in blocks
            ), (blocks, declaration)
        if "color-mix(" in declaration and "transparent" in declaration:
            assert any(block.startswith("@supports") and "color-mix(" in block for block in blocks), (blocks, declaration)


def test_form_styles_remain_scoped_and_mobile_save_bar_handles_keyboard(form_css, form_source):
    declarations = list(css_declarations(form_css))
    for blocks, _ in declarations:
        if blocks and not blocks[-1].startswith("@"):
            assert all(selector.startswith("#new-page") for selector in css_selectors(blocks[-1]))
    mobile_footer = [
        declaration for blocks, declaration in declarations
        if blocks and "form-footer" in blocks[-1]
        and any(block.startswith("@media") and "max-width" in block for block in blocks)
    ]
    assert any(re.fullmatch(r'position\s*:\s*sticky', declaration) for declaration in mobile_footer)
    assert any(declaration.startswith("bottom:") for declaration in mobile_footer)
    assert any("linear-gradient(" in declaration for declaration in mobile_footer)
    assert "visualViewport" in form_source


def test_reduced_motion_disables_form_transitions_and_animations(form_css):
    reduced = [
        declaration for blocks, declaration in css_declarations(form_css)
        if any(block.startswith("@media") and re.search(r'\(\s*prefers-reduced-motion\s*:\s*reduce\s*\)', block) for block in blocks)
    ]
    assert any(re.fullmatch(r'transition\s*:\s*none(?:\s*!important)?', declaration) for declaration in reduced)
    assert any(re.fullmatch(r'animation\s*:\s*none(?:\s*!important)?', declaration) for declaration in reduced)


def test_transitions_are_bounded_and_fold_height_is_the_only_layout_exception(form_css):
    for blocks, declaration in css_declarations(form_css):
        if not declaration.startswith("transition:") or re.match(r'transition:\s*none', declaration):
            continue
        value = declaration.partition(":")[2]
        assert "var(--motion-ease)" in value
        for property_name in re.findall(r'(?:^|,)\s*([a-z-]+)\s+', value):
            assert property_name in {"opacity", "transform", "box-shadow", "border-color", "grid-template-rows"}, declaration
            if property_name == "grid-template-rows":
                assert "form-fold" in blocks[-1], declaration
        times = re.findall(r'(?<![\w-])([\d.]+)(ms|s)\b', value)
        assert times or "var(--motion-fast)" in value or "var(--motion-enter)" in value
        for number, unit in times:
            milliseconds = float(number) * (1000 if unit == "s" else 1)
            assert 150 <= milliseconds <= 220, declaration


def test_qixi_progress_is_transparent_on_desktop_and_a_guarded_pill_on_mobile():
    stylesheet = (STATIC / "themes.css").read_text(encoding="utf-8")
    selector = r'html\[data-theme\s*=\s*(["\'])qixi\1\]\s+#new-page\s+\.form-section-progress'
    progress = [
        (blocks, declaration) for blocks, declaration in css_declarations(stylesheet)
        if blocks and any(re.fullmatch(selector, item) for item in css_selectors(blocks[-1]))
    ]
    assert progress, "Missing Qixi progress overrides"
    base = [declaration for blocks, declaration in progress if len(blocks) == 1]
    assert any(re.fullmatch(r'background\s*:\s*transparent', declaration) for declaration in base)
    mobile = [
        (blocks, declaration) for blocks, declaration in progress
        if any(block.startswith("@media") and re.search(r'\(\s*max-width\s*:', block) for block in blocks)
    ]
    fallback = [
        declaration for blocks, declaration in mobile
        if not any(block.startswith("@supports") for block in blocks)
    ]
    assert any(re.fullmatch(r'border-radius\s*:\s*var\(\s*--radius-pill\s*\)', declaration) for declaration in fallback)
    assert any(re.fullmatch(r'background\s*:\s*var\(\s*--paper\s*\)', declaration) for declaration in fallback)
    mixes = [(blocks, declaration) for blocks, declaration in mobile if "color-mix(" in declaration]
    assert mixes, "Missing supported mobile progress gradient"
    for blocks, declaration in mixes:
        assert any(block.startswith("@supports") and "color-mix(" in block for block in blocks)
        assert "linear-gradient(" in declaration
        assert re.search(r'var\(\s*--paper\s*\)', declaration)
        assert re.search(r'var\(\s*--paper-2\s*\)', declaration)
        assert not re.search(r'\btransparent\b', declaration), "Mobile sticky progress must conceal the scrolling content"
    assert all("!important" not in declaration for _, declaration in progress)
