"""Static seal and opening checks without a browser, database, or temporary files."""

from html.parser import HTMLParser
from pathlib import Path
import re

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


class IndexDocument(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        self.elements.append({"tag": tag, "attrs": dict(attrs)})


@pytest.fixture(scope="module")
def sources():
    return {
        filename: (STATIC / filename).read_text(encoding="utf-8")
        for filename in (
            "index.html", "app.js", "intro.js", "cursor-fx.js", "style.css", "intro.css",
        )
    }


def css_declarations(source):
    """Track selectors through nested media/keyframe blocks, as in cursor checks."""
    source = re.sub(r"/\*[\s\S]*?\*/", "", source)
    blocks = []
    for match in re.finditer(r"([^{};]*)([{};])", source):
        text, delimiter = match.groups()
        text = text.strip()
        if delimiter == "{":
            blocks.append(text)
        else:
            if text:
                yield tuple(blocks), text
            if delimiter == "}":
                assert blocks, "Unexpected closing CSS brace"
                blocks.pop()
    assert not blocks, "Unclosed CSS block"


def css_selectors(selector_list):
    """Do not split commas inside :is(), :not(), or attribute selectors."""
    depth = 0
    start = 0
    for index, character in enumerate(selector_list):
        if character in "([":
            depth += 1
        elif character in ")]":
            depth -= 1
        elif character == "," and depth == 0:
            yield selector_list[start:index].strip()
            start = index + 1
    yield selector_list[start:].strip()


def test_seal_layer_is_unique_and_decorative(sources):
    document = IndexDocument()
    document.feed(sources["index.html"])
    document.close()
    layers = [node for node in document.elements if node["attrs"].get("id") == "seal-layer"]
    assert len(layers) == 1
    assert layers[0]["tag"] == "div"
    assert layers[0]["attrs"].get("aria-hidden") == "true"


def test_seal_feedback_and_seen_achievements_are_present(sources):
    source = sources["app.js"]
    assert re.search(r"\bfunction\s+stampSeal\s*\(", source)
    for label in ("再练", "过关", "记住", "掌握"):
        assert label in source
    assert "achievementsSeen" in source


def test_cursor_manager_exposes_drop(sources):
    manager = re.search(
        r"\bwindow\.CursorFX\s*=\s*\{([\s\S]*?)\n[ \t]*\};",
        sources["cursor-fx.js"],
    )
    assert manager, "Missing CursorFX manager"
    assert re.search(r"\bdrop\s*(?:\(|:)", manager[1])


@pytest.mark.parametrize("filename", ["style.css", "intro.css"])
def test_opening_initial_hidden_content_requires_motion_gate(sources, filename):
    hidden_rules = 0
    for blocks, declaration in css_declarations(sources[filename]):
        if not re.fullmatch(r"opacity\s*:\s*0(?:\.0*)?\s*(?:!important)?", declaration):
            continue
        # Transparent animation frames do not hide the natural/static content.
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            continue
        assert blocks and not blocks[-1].startswith("@")
        for selector in css_selectors(blocks[-1]):
            # The vermilion review tile's empty light overlay is decoration, not opening
            # content. It may be transparent until the pointer enters the card.
            if re.search(r"\.ov-tile-review(?=[\s.:#\[]|$)", selector) and "::after" in selector:
                continue
            hidden_rules += 1
            assert re.search(r"\.(?:intro|home)-anim\b", selector), (
                f"Content hidden without an allowed-motion gate in {filename}: {selector}"
            )
    assert hidden_rules, f"Missing gated opening rules in {filename}"


@pytest.mark.parametrize(
    ("filename", "gate"), [("intro.js", "intro-anim"), ("app.js", "home-anim")],
)
def test_openings_check_reduced_motion(sources, filename, gate):
    source = sources[filename]
    assert re.search(r"prefers-reduced-motion\s*:\s*reduce", source)
    assert gate in source

