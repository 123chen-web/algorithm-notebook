"""Forum detail asset contracts without a browser, database, or temp files."""

from pathlib import Path
import json
import re
import shutil
import subprocess

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
    assert attrs["href"] == "/static/forum.css?v=2"
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
        "forum-comment-form",
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
    assert {node["attrs"]["data-forum-order"] for node in orders} == {"earliest", "latest"}
    assert len(orders) == 2
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
    assert "is_op" in comments


def test_forum_has_visible_keyboard_focus_and_responsive_floor_layout(stylesheet):
    assert ":focus-visible" in stylesheet
    assert re.search(r"@media[^{}]*max-width", stylesheet)
    assert re.search(r"grid-template-columns\s*:", stylesheet)
    assert re.search(r"overflow-wrap\s*:\s*(?:anywhere|break-word)", stylesheet)


def test_forum_documentation_describes_stable_floors_and_private_deleted_quotes():
    readme = (STATIC.parent / "README.md").read_text(encoding="utf-8")
    section = readme.split("## 讨论区", 1)[1].split("\n## ", 1)[0]
    for text in ("楼主", "楼层", "reply_to_id", "60", "回复的楼层已删除", "不会重新编号"):
        assert text in section


FORUM_RENDER_HARNESS = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const payload = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
class Node {
  constructor(tag, text = "", className = "") {
    this.tag = tag; this.textContent = text; this.className = className;
    this.children = []; this.attributes = {}; this.listeners = {};
    this.hidden = false; this.value = ""; this.focusCalls = [];
    this.classList = {
      add: (name) => { this.className += ` ${name}`; },
      remove: (name) => { this.className = this.className.split(" ").filter((c) => c !== name).join(" "); },
    };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this.textContent = ""; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  addEventListener(name, handler) { this.listeners[name] = handler; }
  focus(options) { this.focusCalls.push(options); }
  scrollIntoView(options) { this.scrollOptions = options; }
  get childElementCount() { return this.children.filter((child) => child instanceof Node).length; }
}
function walk(root) {
  return [root, ...root.children.filter((c) => c instanceof Node).flatMap(walk)];
}
function text(root) {
  return root.textContent + root.children.map((child) => child instanceof Node ? text(child) : String(child)).join("");
}
const ids = {};
for (const id of ["forum-comments", "forum-comments-title", "forum-post-comment-count", "forum-only-op",
  "forum-comment-form", "forum-comment-avatar", "forum-comment-trial-note", "forum-comment-body",
  "forum-comment-count", "forum-reply-target", "forum-reply-label", "forum-reply-cancel", "forum-comment-status"]) {
  ids[id] = new Node("div"); ids[id].id = id;
}
const orderButtons = ["earliest", "latest"].map((order) => {
  const button = new Node("button"); button.dataset = { forumOrder: order }; return button;
});
const context = {
  user: { id: 1, username: "我", avatar_version: 0, has_avatar: false, is_trial: false },
  forumPost: { comments: [] }, forumOnlyOp: false, forumCommentOrder: "earliest", forumReplyTarget: null,
  element: (tag, value = "", className = "") => new Node(tag, value, className),
  avatarElement: (_id, name) => new Node("span", name, "avatar"),
  timestamp: (value) => `完整时间 ${value}`, forumRelativeTime: () => "刚刚",
  document: { querySelectorAll: () => orderButtons, createElement: (tag) => new Node(tag) },
  window: { matchMedia: () => ({ matches: true }), clearTimeout() {}, setTimeout: () => 1 },
  message() {},
  $: (selector) => ids[selector.slice(1)] ?? Object.values(ids).flatMap(walk).find((node) => node.id === selector.slice(1)),
};
vm.createContext(context);
vm.runInContext(payload.source, context);
const comment = (id, floor, userId, isOp = false) => ({
  id, post_id: 1, floor, user_id: userId, is_op: isOp, username: `作者${userId}`,
  avatar_version: 0, has_avatar: false, body: `正文${floor}`, created_at: "2026-10-02T08:00:00+00:00",
  updated_at: null, reply_to: null,
});
const order = () => ids["forum-comments"].children.filter((c) => c.tag === "article").map((c) => c.id);
const actions = (root) => walk(root).filter((node) => node.tag === "button" && node.className.includes("forum-text-action"));
if (payload.scenario === "sort_filter") {
  const comments = [comment(1, 1, 1, true), comment(3, 3, 2), comment(5, 5, 1, true)];
  context.forumPost.comments = comments;
  context.renderForumComments(comments);
  assert.deepEqual(order(), ["forum-comment-1", "forum-comment-3", "forum-comment-5"]);
  context.forumCommentOrder = "latest";
  context.renderForumComments(comments);
  assert.deepEqual(order(), ["forum-comment-5", "forum-comment-3", "forum-comment-1"]);
  context.forumOnlyOp = true;
  context.renderForumComments(comments);
  assert.deepEqual(order(), ["forum-comment-5", "forum-comment-1"]);
  assert.deepEqual(comments.map((c) => [c.id, c.floor]), [[1, 1], [3, 3], [5, 5]]);
  assert.equal(ids["forum-comments-title"].textContent, "全部评论 · 3");
  assert.equal(ids["forum-only-op"].getAttribute("aria-pressed"), "true");
  assert.deepEqual(orderButtons.map((b) => b.getAttribute("aria-pressed")), ["false", "true"]);
} else if (payload.scenario === "ownership_quotes") {
  for (const [userId, labels] of [[1, ["回复", "编辑", "删除"]], [2, ["回复", "举报", "举报头像"]]]) {
    const row = comment(userId, 3, userId, userId === 1);
    const root = context.renderForumComment(row);
    assert.equal(root.tag, "article");
    assert.equal(root.getAttribute("aria-labelledby"), `forum-comment-heading-${userId}`);
    assert.deepEqual(actions(root).map((button) => button.textContent), labels);
    for (const button of actions(root)) {
      assert.equal(button.type, "button");
      assert.match(button.getAttribute("aria-label"), /3 楼/);
    }
    context.user.is_trial = true;
    assert.equal(actions(context.renderForumComment(row)).length, 0);
    context.user.is_trial = false;
  }
  const row = comment(8, 8, 2);
  row.reply_to = { id: 3, floor: 3, deleted: true, username: "已删秘密作者", excerpt: "已删秘密正文" };
  const deleted = context.renderForumComment(row);
  assert.match(text(deleted), /回复的楼层已删除/);
  assert.doesNotMatch(text(deleted), /已删秘密作者|已删秘密正文/);
  assert.equal(walk(deleted).filter((node) => node.tag === "button" && node.className.includes("forum-quote")).length, 0);
  row.reply_to = { id: 3, floor: 3, deleted: false, username: "苏晚", excerpt: "边界检查" };
  const quote = walk(context.renderForumComment(row)).find((node) => node.tag === "button" && node.className.includes("forum-quote"));
  assert.equal(quote.type, "button");
  assert.match(quote.getAttribute("aria-label"), /3 楼/);
  assert.match(text(quote), /回复 3 楼 @苏晚：边界检查/);
  assert.equal(typeof quote.listeners.click, "function");
} else if (payload.scenario === "empty_trial") {
  context.forumPost.comments = [comment(3, 3, 2)];
  context.forumOnlyOp = true;
  context.renderForumComments(context.forumPost.comments);
  assert.match(text(ids["forum-comments"]), /楼主还没有评论/);
  context.forumPost.comments = [];
  context.renderForumComments([]);
  assert.match(text(ids["forum-comments"]), /还没有评论，来抢沙发吧/);
  context.user.is_trial = true;
  context.renderForumComments([]);
  assert.equal(ids["forum-comment-form"].hidden, true);
  assert.equal(ids["forum-comment-trial-note"].hidden, false);
  context.user.is_trial = false;
  context.renderForumComments([]);
  assert.equal(ids["forum-comment-form"].hidden, false);
  assert.equal(ids["forum-comment-trial-note"].hidden, true);
} else if (payload.scenario === "reply_reduced_motion") {
  const row = comment(3, 3, 1, true);
  ids["forum-comment-body"].value = "未提交的评论草稿";
  context.selectForumReply(row);
  assert.equal(context.forumReplyTarget.id, 3);
  assert.equal(ids["forum-reply-target"].hidden, false);
  assert.match(ids["forum-reply-label"].textContent, /回复 3 楼 @作者1/);
  assert.equal(ids["forum-comment-body"].focusCalls.length, 1);
  assert.equal(ids["forum-comment-form"].scrollOptions.behavior, "instant");
  context.clearForumReply();
  assert.equal(context.forumReplyTarget, null);
  assert.equal(ids["forum-reply-target"].hidden, true);
  assert.equal(ids["forum-comment-body"].value, "未提交的评论草稿");
  context.forumPost.comments = [row];
  context.renderForumComments([row]);
  context.scrollToForumComment(3);
  const target = ids["forum-comments"].children[0];
  assert.equal(target.scrollOptions.behavior, "instant");
  assert.equal(target.focusCalls.length, 1);
  assert.equal(target.className.includes("forum-floor-highlight"), false);
  context.updateForumCommentCount();
  assert.equal(ids["forum-comment-count"].textContent, `${ids["forum-comment-body"].value.length}/2000`);
} else { throw new Error(`Unknown scenario ${payload.scenario}`); }
"""


@pytest.mark.parametrize("scenario", [
    "sort_filter", "ownership_quotes", "empty_trial", "reply_reduced_motion",
])
def test_forum_render_behavior_uses_real_functions_without_temp_files(source, scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for forum render behavior checks")
    functions = []
    for name in (
        "forumTime", "forumTextAction", "selectForumReply", "clearForumReply",
        "scrollToForumComment", "updateForumCommentCount", "renderForumCommentControls",
        "renderForumComment", "renderForumComments",
    ):
        declaration = re.search(rf"(?m)^function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", source)
        assert declaration, f"Missing {name} renderer or helper"
        functions.append(declaration[0] + function_body(source, name) + "}")
    result = subprocess.run(
        [node, "-e", FORUM_RENDER_HARNESS],
        input=json.dumps({"source": "\n".join(functions), "scenario": scenario}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=10,
        cwd=STATIC.parent, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
