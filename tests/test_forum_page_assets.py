"""Forum detail asset contracts without a browser, database, or temp files."""

from pathlib import Path
import re

import pytest

from test_cursor_fx_assets import css_declarations, css_selectors, function_body
from test_layout_assets import LayoutDocument


STATIC = Path(__file__).resolve().parents[1] / "static"


@pytest.fixture(scope="module")
def stylesheet():
    path = STATIC / "forum.css"
    assert path.is_file(), "Forum detail styles need their own stylesheet"
    return path.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def source():
    return (STATIC / "app.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def document():
    parser = LayoutDocument()
    parser.feed((STATIC / "index.html").read_text(encoding="utf-8"))
    parser.close()
    return parser.elements


def element_with_id(document, element_id):
    matches = [
        (index, node) for index, node in enumerate(document)
        if node["attrs"].get("id") == element_id
    ]
    assert len(matches) == 1, f"Expected one #{element_id}"
    return matches[0]


def motion_allowed(blocks):
    return any(
        block.startswith("@media")
        and re.search(r"\(\s*prefers-reduced-motion\s*:\s*no-preference\s*\)", block)
        for block in blocks
    )


def test_forum_stylesheet_is_versioned_and_loaded_after_shared_styles(document, stylesheet):
    links = [
        node["attrs"] for node in document
        if node["tag"] == "link" and node["attrs"].get("rel") == "stylesheet"
    ]
    matches = [
        (index, attrs) for index, attrs in enumerate(links)
        if attrs.get("href", "").split("?", 1)[0] == "/static/forum.css"
    ]
    assert len(matches) == 1
    forum_index, attrs = matches[0]
    assert attrs["href"] == "/static/forum.css?v=5"
    shared_indices = [
        index for index, attrs in enumerate(links)
        if attrs.get("href", "").split("?", 1)[0] in (
            "/static/style.css", "/static/themes.css",
        )
    ]
    assert shared_indices and max(shared_indices) < forum_index


def test_forum_selectors_are_scoped_to_the_forum_page(stylesheet):
    checked = 0
    for blocks, declaration in css_declarations(stylesheet):
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            continue
        assert blocks and not blocks[-1].startswith("@"), declaration
        for selector in css_selectors(blocks[-1]):
            # Existing theme and running-ripple attributes may precede scope.
            scoped = re.sub(r"^(?:html(?:\[[^\]]+\])+\s+)+", "", selector)
            assert re.match(r"#forum-page(?=[\s.:#\[]|$)", scoped), selector
            checked += 1
    assert checked, "Missing page-scoped forum rules"


def test_forum_uses_theme_tokens_without_priority_overrides_or_remote_assets(stylesheet):
    clean = re.sub(r"/\*[\s\S]*?\*/", "", stylesheet)
    assert not re.search(r"!\s*important\b", clean, re.I)
    assert not re.search(r"@import\b|\burl\s*\(", clean, re.I)
    for _, declaration in css_declarations(clean):
        name, separator, value = declaration.partition(":")
        assert separator, declaration
        assert not re.search(
            r"#[0-9a-f]{3,8}\b|\b(?:rgb|rgba|hsl|hsla|lab|lch|oklab|oklch)\s*\((?!\s*var\s*\()",
            value, re.I,
        ), f"Hard-coded color in {name}: {value}"
        if name.strip() in ("color", "background-color", "border-color", "outline-color"):
            assert re.search(r"var\s*\(|\b(?:currentColor|transparent|inherit)\b", value), declaration


def test_forum_blur_and_translucency_preserve_opaque_fallback_and_pointer_gates(stylesheet):
    declarations = list(css_declarations(stylesheet))
    for blocks, declaration in declarations:
        if re.match(r"(?:-webkit-)?backdrop-filter\s*:", declaration):
            assert any(
                block.startswith("@media")
                and re.search(r"\(\s*hover\s*:\s*hover\s*\)", block)
                and re.search(r"\(\s*pointer\s*:\s*fine\s*\)", block)
                for block in blocks
            ), f"Touch devices must not receive forum blur: {blocks}"
        if "color-mix(" not in declaration or "transparent" not in declaration:
            continue
        assert any(
            block.startswith("@supports") and "color-mix(" in block
            for block in blocks
        ), f"Missing color-mix support gate: {blocks}"
        assert any(
            not any(block.startswith("@supports") for block in fallback_blocks)
            and re.match(r"background(?:-color)?\s*:\s*var\(", fallback_declaration)
            for fallback_blocks, fallback_declaration in declarations
        ), "Translucent forum surfaces need an opaque token-based fallback"


def test_forum_highlight_motion_is_allowed_only_with_no_reduced_motion(stylesheet):
    animated = 0
    for blocks, declaration in css_declarations(stylesheet):
        name, _, value = declaration.partition(":")
        if re.match(r"(?:-\w+-)?animation(?:-|$)", name.strip()):
            animated += 1
            assert motion_allowed(blocks), f"Forum animation lacks a motion gate: {blocks}"
        if any(re.match(r"@(?:-\w+-)?keyframes\b", block) for block in blocks):
            assert motion_allowed(blocks), f"Forum keyframes lack a motion gate: {blocks}"
        if name.strip() == "scroll-behavior" and value.strip() == "smooth":
            assert motion_allowed(blocks), f"Smooth scrolling lacks a motion gate: {blocks}"
    assert animated, "Missing optional new-floor/reference highlight"


def test_forum_preserves_unique_page_and_detail_ids(document):
    page_index, _ = element_with_id(document, "forum-page")
    for element_id in (
        "forum-list", "forum-posts", "forum-detail", "forum-back", "forum-post",
        "forum-comments-section", "forum-comments-title", "forum-comments",
        "forum-comment-form", "forum-comment-sort", "forum-only-op", "forum-comment-status",
        "forum-comment-avatar", "forum-composer-title", "forum-reply-target", "forum-reply-label",
        "forum-reply-cancel", "forum-comment-body", "forum-comment-count", "forum-comment-trial-note",
        "forum-back-bottom",
    ):
        _, node = element_with_id(document, element_id)
        assert page_index in node["ancestors"], element_id
    _, back = element_with_id(document, "forum-back")
    assert back["tag"] == "button"
    assert back["attrs"].get("type") == "button"
    _, detail = element_with_id(document, "forum-detail")
    assert "panel" not in detail["attrs"].get("class", "").split()


def test_forum_detail_has_clear_back_buttons_above_and_below_the_thread(document, stylesheet, source):
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    detail_index, _ = element_with_id(document, "forum-detail")
    positions = []
    for element_id in ("forum-back", "forum-back-bottom"):
        _, back = element_with_id(document, element_id)
        assert detail_index in back["ancestors"], element_id
        assert back["tag"] == "button" and back["attrs"].get("type") == "button"
        classes = back["attrs"].get("class", "").split()
        assert "forum-back-button" in classes
        # The way back to the post list is primary navigation, not a quiet text action.
        assert "forum-text-action" not in classes, element_id
        markup = re.search(rf'<button id="{element_id}"[^>]*>(.*?)</button>', html, re.S)
        assert markup, element_id
        assert '<span aria-hidden="true">←</span>' in markup.group(1)
        assert re.sub(r"<[^>]+>", "", markup.group(1)).replace("←", "").strip() == "返回讨论区"
        positions.append(html.index(f'id="{element_id}"'))
    # One above the post card for the first screen, one after the comments and composer for the last.
    assert positions[0] < html.index('id="forum-post"') < html.index('id="forum-comment-form"') < positions[1]

    rules = [
        declaration for blocks, declaration in css_declarations(stylesheet)
        if blocks == ("#forum-page .forum-back-button",)
    ]
    heights = [
        int(match.group(1)) for declaration in rules
        if (match := re.fullmatch(r"min-height\s*:\s*(\d+)px", declaration))
    ]
    assert heights and min(heights) >= 40
    # It must keep the shared bordered-button look instead of re-flattening it.
    assert not any(re.match(r"(?:border|background)\s*:\s*(?:0|none)\b", declaration) for declaration in rules)

    # Both buttons share one handler that waits for the list and puts the reader back on the post they read.
    assert 'document.querySelectorAll("#forum-back, #forum-back-bottom")' in source
    handler = source[source.index('document.querySelectorAll("#forum-back, #forum-back-bottom")'):][:900]
    assert "showForumList()" in handler and "forumListGeneration" in handler
    assert "restoreForumListPosition(openedPostId)" in handler
    restore = function_body(source, "restoreForumListPosition")
    assert 'window.scrollTo({ top: 0, behavior: "instant" })' in restore
    assert ".focus({ preventScroll: true })" in restore and "data-post-id" in restore
    assert "row.dataset.postId = String(post.id)" in source
    _, list_title = element_with_id(document, "forum-list-title")
    assert list_title["attrs"].get("tabindex") == "-1"


def test_forum_composer_preserves_a_labeled_three_row_bounded_textarea(document):
    form_index, form = element_with_id(document, "forum-comment-form")
    assert form["tag"] == "form"
    fields = [
        node for node in document
        if node["tag"] == "textarea" and form_index in node["ancestors"]
        and node["attrs"].get("name") == "body"
    ]
    assert len(fields) == 1
    field = fields[0]
    assert field["attrs"].get("rows") == "3"
    assert field["attrs"].get("maxlength") == "2000"
    assert "required" in field["attrs"]
    field_id = field["attrs"].get("id")
    assert any(document[index]["tag"] == "label" for index in field["ancestors"]) or (
        field_id and any(
            node["tag"] == "label" and node["attrs"].get("for") == field_id
            for node in document
        )
    )
    assert any(
        node["tag"] == "button" and node["attrs"].get("type") == "submit"
        and form_index in node["ancestors"] for node in document
    )


def test_forum_sort_filter_and_reply_cancel_are_native_keyboard_controls(document):
    sort_index, sort = element_with_id(document, "forum-comment-sort")
    assert sort["attrs"].get("role") == "group"
    assert sort["attrs"].get("aria-label") or sort["attrs"].get("aria-labelledby")
    orders = [
        node for node in document
        if "data-forum-order" in node["attrs"] and sort_index in node["ancestors"]
    ]
    assert {node["attrs"]["data-forum-order"] for node in orders} == {"earliest", "latest", "helpful"}
    assert len(orders) == 3
    for node in orders:
        assert node["tag"] == "button"
        assert node["attrs"].get("type") == "button"
        assert node["attrs"].get("aria-pressed") in ("true", "false")
    _, only_op = element_with_id(document, "forum-only-op")
    assert only_op["tag"] == "button"
    assert only_op["attrs"].get("type") == "button"
    assert only_op["attrs"].get("aria-pressed") == "false"
    reply_index, reply = element_with_id(document, "forum-reply-target")
    assert "hidden" in reply["attrs"]
    assert reply["attrs"].get("role") == "status"
    for element_id in ("forum-reply-label", "forum-reply-cancel"):
        _, node = element_with_id(document, element_id)
        assert reply_index in node["ancestors"]
    _, cancel = element_with_id(document, "forum-reply-cancel")
    assert cancel["tag"] == "button"
    assert cancel["attrs"].get("type") == "button"
    assert cancel["attrs"].get("aria-label")


def test_forum_composer_has_count_avatar_trial_notice_and_live_feedback(document):
    form_index, _ = element_with_id(document, "forum-comment-form")
    _, count = element_with_id(document, "forum-comment-count")
    assert count["tag"] == "output"
    assert form_index in count["ancestors"]
    _, avatar = element_with_id(document, "forum-comment-avatar")
    assert form_index in avatar["ancestors"]
    _, body = element_with_id(document, "forum-comment-body")
    assert body["attrs"].get("name") == "body"
    _, status = element_with_id(document, "forum-comment-status")
    assert status["attrs"].get("role") == "status"
    assert status["attrs"].get("aria-live") == "polite"
    _, trial = element_with_id(document, "forum-comment-trial-note")
    assert "hidden" in trial["attrs"]


def test_forum_keeps_render_data_flow_and_native_accessible_floor_articles(source):
    for function_name in ("renderForumPost", "renderForumComment", "renderForumComments"):
        assert function_body(source, function_name)
    comment = function_body(source, "renderForumComment")
    assert re.search(r"element\(\s*[\"']article[\"']", comment)
    assert "aria-labelledby" in comment
    assert "comment.floor" in comment
    assert "comment.is_op" in comment
    assert "comment.reply_to" in comment
    assert "avatarElement(" in comment
    assert "editForm" in comment
    assert "reportForm" in comment
    assert "avatarReportForm" in comment
    assert "wrap.remove()" in comment


def test_forum_reply_helpers_carry_target_reset_focus_and_reduced_motion_contract(source):
    select = function_body(source, "selectForumReply")
    assert "forum-reply-label" in select
    assert "comment.floor" in select and "comment.username" in select
    assert re.search(r"\.focus\s*\(", select)
    clear = function_body(source, "clearForumReply")
    assert "forum-reply-target" in clear
    scroll = function_body(source, "scrollToForumComment")
    assert "prefers-reduced-motion" in scroll
    assert "forum-floor-highlight" in scroll
    assert "scrollIntoView" in scroll
    assert "forum-comment-" in scroll
    assert "reply_to_id" in source
    assert re.search(r"forum-comment-body[^\n]*addEventListener\(\s*[\"']input[\"']", source)
    assert function_body(source, "updateForumCommentCount")
    assert function_body(source, "renderForumCommentControls")
    time = function_body(source, "forumTime")
    assert "title" in time and "timestamp(" in time
    actions = function_body(source, "forumTextAction")
    assert "aria-label" in actions


def test_forum_deleted_quotes_and_trial_restrictions_have_explicit_render_paths(source):
    comment = function_body(source, "renderForumComment")
    assert "deleted" in comment and "回复的楼层已删除" in comment
    assert "user.is_trial" in comment
    comments = function_body(source, "renderForumComments")
    assert "renderForumCommentControls()" in comments
    controls = function_body(source, "renderForumCommentControls")
    assert "forum-comment-form" in controls and "user.is_trial" in controls
    assert "forum-comment-trial-note" in controls
    assert "还没有评论，来抢沙发吧" in comments
    assert "window.Thread.arrange" in comments
    assert "forumFilters()" in comments


def test_forum_has_visible_keyboard_focus_and_responsive_floor_layout(stylesheet):
    assert ":focus-visible" in stylesheet
    assert re.search(r"@media[^{}]*max-width", stylesheet)
    assert re.search(r"grid-template-columns\s*:", stylesheet)
    assert re.search(r"overflow-wrap\s*:\s*(?:anywhere|break-word)", stylesheet)


def test_thread_script_is_versioned_external_and_loaded_before_app(document):
    scripts = [node["attrs"].get("src", "") for node in document if node["tag"] == "script"]
    assert scripts.count("/static/thread.js?v=1") == 1
    thread_index = scripts.index("/static/thread.js?v=1")
    app_indices = [index for index, src in enumerate(scripts) if src.split("?", 1)[0] == "/static/app.js"]
    assert len(app_indices) == 1 and thread_index < app_indices[0]
    assert scripts[app_indices[0]] == "/static/app.js?v=59"


def test_thread_markup_uses_external_csp_safe_controls_and_shared_renderer(document, source):
    page_index, _ = element_with_id(document, "forum-page")
    for node in document:
        if page_index not in node["ancestors"]:
            continue
        assert "style" not in node["attrs"]
        assert not any(name.lower().startswith("on") for name in node["attrs"])
    thread_source = (STATIC / "thread.js").read_text(encoding="utf-8")
    assert not re.search(r"\.innerHTML\s*=|insertAdjacentHTML\s*\(", thread_source)
    assert "window.Thread.renderBody" in function_body(source, "forumBody")
    assert "forumBody(post.body)" in function_body(source, "renderForumPost")
    assert "forumBody(comment.body)" in function_body(source, "renderForumComment")
    for element_id in ("forum-code-only", "forum-mention-only", "forum-editor-tab", "forum-preview-tab"):
        _, control = element_with_id(document, element_id)
        assert control["tag"] == "button" and control["attrs"].get("type") == "button"
        assert control["attrs"].get("aria-pressed") in ("true", "false")


def test_thread_map_is_pointer_enhancement_excluded_from_keyboard_navigation(document, source):
    _, hud = element_with_id(document, "forum-floor-nav")
    assert hud["attrs"].get("aria-hidden") == "true"
    map_index, _ = element_with_id(document, "forum-minimap")
    assert any(document[index]["attrs"].get("aria-hidden") == "true" for index in document[map_index]["ancestors"])
    for element_id in ("forum-map-top", "forum-map-latest", "forum-floor-prev", "forum-floor-next"):
        _, button = element_with_id(document, element_id)
        assert button["tag"] == "button" and button["attrs"].get("tabindex") == "-1"
        assert button["attrs"].get("aria-label")
    assert "requestAnimationFrame" in function_body(source, "scheduleForumMap")
    assert "--thread-sticky-top" in function_body(source, "updateForumMap")
    assert "getBoundingClientRect" in function_body(source, "updateForumMap")


def test_thread_optional_panels_start_hidden_without_empty_static_shells(document):
    for element_id in ("forum-accepted", "forum-summary"):
        index, node = element_with_id(document, element_id)
        assert node["tag"] == "section" and "hidden" in node["attrs"]
        assert not any(index in child["ancestors"] for child in document)


def test_forum_documentation_describes_stable_floors_and_private_deleted_quotes():
    readme = (STATIC.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("## 讨论区", 1)[1].split("\n## ", 1)[0]
    for text in ("楼主", "楼层", "reply_to_id", "60", "回复的楼层已删除", "不会重新编号"):
        assert text in section
