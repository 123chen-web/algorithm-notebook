// static/app/forum-comments.js — 论坛：评论/回复/评论编辑器。
// 由 static/app/forum.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

function clearForumReply() {
  forumReplyTarget = null;
  $("#forum-reply-target").hidden = true;
  $("#forum-reply-label").textContent = "";
  $("#forum-composer-title").textContent = "> 发表你的看法";
}

function selectForumReply(comment) {
  if (user.is_trial) return;
  forumReplyTarget = comment;
  $("#forum-reply-label").textContent = `回复 ${comment.floor} 楼 @${comment.username}`;
  $("#forum-composer-title").textContent = `> 回复 #${String(comment.floor).padStart(2, "0")} ${comment.username}`;
  $("#forum-reply-target").hidden = false;
  $("#forum-reply-cancel").setAttribute("aria-label", `取消回复 ${comment.floor} 楼`);
  const form = $("#forum-comment-form");
  setForumPreview(false);
  form.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "center" });
  $("#forum-comment-body").focus({ preventScroll: true });
}

// 手机软键盘会在 focus 之后缩小可视区域，只在回复框越界时重新定位。

function keepForumReplyVisible() {
  const viewport = window.visualViewport;
  const field = $("#forum-comment-body");
  if (!viewport || !forumDetailVisible() || field.hidden || document.activeElement !== field) return;
  const rect = field.getBoundingClientRect();
  if (rect.top < viewport.offsetTop || rect.bottom > viewport.offsetTop + viewport.height) {
    field.scrollIntoView({ block: "center", behavior: "auto" });
  }
}

function scrollToForumComment(commentId, { clearFilters = false } = {}) {
  if (!forumPost) return;
  let target = $(`#forum-comment-${commentId}`);
  if (((clearFilters && (forumOnlyOp || forumCodeOnly || forumMentionOnly)) || !target || target.hidden)
    && forumPost.comments.some((comment) => comment.id === commentId)) {
    forumOnlyOp = false;
    forumCodeOnly = false;
    forumMentionOnly = false;
    renderForumComments(forumPost.comments);
    target = $(`#forum-comment-${commentId}`);
    $("#forum-comment-status").textContent = "已显示全部评论，跳转到被引用的楼层。";
  }
  if (!target) {
    message("回复的楼层已删除。");
    return;
  }
  const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  target.expandForumComment?.();
  forumCurrentComment = commentId;
  target.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "center" });
  target.focus({ preventScroll: true });
  window.clearTimeout(target.forumHighlightTimer);
  target.classList.add("forum-floor-highlight");
  target.forumHighlightTimer = window.setTimeout(() => target.classList.remove("forum-floor-highlight"), 1800);
  scheduleForumMap();
}

// 与服务器的 truncate_text 同一套规则：按字符截断，但不把一个表情（肤色、连接序列、国旗、键帽）劈成两半。

function updateForumCommentCount() {
  const count = $("#forum-comment-body").value.length;
  $("#forum-comment-count").textContent = `${count} / 2000`;
  $("#forum-comment-progress").style.setProperty("--thread-progress", `${Math.min(100, count / 20)}%`);
  if (forumPreview) $("#forum-comment-preview").replaceChildren(forumBody($("#forum-comment-body").value));
}

function renderForumCommentControls() {
  const count = forumPost.comments.length;
  $("#forum-comments-title").replaceChildren("回复 ", element("span", String(count), "thread-count-badge"));
  const helpful = forumPost.comments.some((comment) => Number.isInteger(comment.helpful_count));
  if (!helpful && forumCommentOrder === "helpful") forumCommentOrder = "earliest";
  document.querySelectorAll("#forum-comment-sort button").forEach((button) => {
    if (button.dataset.forumOrder === "helpful") button.hidden = !helpful;
    button.setAttribute("aria-pressed", String(button.dataset.forumOrder === forumCommentOrder));
  });
  $("#forum-only-op").setAttribute("aria-pressed", String(forumOnlyOp));
  const codeCount = forumPost.comments.filter((comment) => window.Thread.hasCode(comment.body)).length;
  const mentionCount = forumPost.comments.filter((comment) => window.Thread.mentionsMe(comment, forumPost.comments, user)).length;
  $("#forum-code-count").textContent = String(codeCount);
  $("#forum-mention-count").textContent = String(mentionCount);
  setForumDisabled($("#forum-code-only"), codeCount === 0);
  setForumDisabled($("#forum-mention-only"), mentionCount === 0);
  $("#forum-code-only").setAttribute("aria-pressed", String(forumCodeOnly));
  $("#forum-mention-only").setAttribute("aria-pressed", String(forumMentionOnly));
  $("#forum-comment-form").hidden = user.is_trial;
  $("#forum-comment-trial-note").hidden = !user.is_trial;
  const avatar = $("#forum-comment-avatar");
  avatar.replaceChildren(avatarElement(user.id, user.username, user.avatar_version, { hasAvatar: user.has_avatar }));
  updateForumCommentCount();
  updateForumPostStats();
  updateForumPostSignal();
  renderForumAccepted();
}


function setForumPreview(preview) {
  forumPreview = preview;
  $("#forum-comment-body").hidden = preview;
  $("#forum-comment-preview").hidden = !preview;
  $("#forum-editor-tab").setAttribute("aria-pressed", String(!preview));
  $("#forum-preview-tab").setAttribute("aria-pressed", String(preview));
  if (preview) updateForumCommentCount();
}

function renderForumComment(comment) {
  let post = forumPost;
  const wrap = element("article", "", "forum-comment");
  wrap.id = `forum-comment-${comment.id}`;
  wrap.tabIndex = -1;
  wrap.setAttribute("aria-labelledby", `forum-comment-heading-${comment.id}`);
  let inlineForm = null;
  let editing = false;
  let expanded = false;

  function restoreActions() {
    inlineForm = null;
    editing = false;
    readOnly();
    wrap.querySelector(".forum-actions button")?.focus({ preventScroll: true });
  }

  function floorHeading() {
    const heading = element("h4", `${comment.floor} 楼 ${comment.username}`, "forum-sr-only");
    heading.id = `forum-comment-heading-${comment.id}`;
    return heading;
  }

  function readOnly() {
    wrap.replaceChildren();
    wrap.classList.toggle("is-op", Boolean(comment.is_op));
    wrap.classList.toggle("is-me", comment.user_id === user.id);
    const accepted = post.accepted_comment_id === comment.id;
    wrap.classList.toggle("is-accepted", accepted);
    wrap.classList.toggle("has-code", window.Thread.hasCode(comment.body));
    wrap.dataset.floor = String(comment.floor);
    // Keep an open editor when a quoted floor changes elsewhere in the thread.
    if (editing) {
      wrap.append(floorHeading(), inlineForm);
      return;
    }
    const author = element("div", "", "thread-avatar-slot");
    const avatarButton = element("button", "", "profile-author profile-avatar");
    avatarButton.type = "button";
    avatarButton.setAttribute("aria-label", `查看 ${comment.username} 的个人资料`);
    avatarButton.append(avatarElement(comment.user_id, comment.username, comment.avatar_version, { hasAvatar: comment.has_avatar }));
    avatarButton.addEventListener("click", () => window.Profile?.open(comment.user_id));
    author.append(avatarButton);
    const content = element("div", "", "forum-comment-content thread-card");
    const meta = element("header", "", "forum-comment-meta thread-head");
    meta.append(profileAuthor(comment.user_id, comment.username));
    if (comment.is_op) meta.append(element("span", "楼主", "forum-op-stamp"));
    if (comment.user_id === user.id) meta.append(element("span", "我", "forum-self-badge"));
    meta.append(element("span", `#${String(comment.floor).padStart(2, "0")}`, "forum-floor-number thread-floor"), forumTime(comment.created_at, true));
    if (comment.updated_at) {
      const edited = element("span", "编辑于 ", "forum-comment-edited");
      edited.append(forumTime(comment.updated_at, true));
      meta.append(edited);
    }
    if (accepted) {
      const flags = element("span", "", "thread-flags");
      flags.append(element("span", "✓ 已采纳", "thread-flag is-accepted"));
      meta.append(flags);
    }
    content.append(meta);
    if (comment.reply_to) {
      const reply = comment.reply_to;
      if (reply.deleted) {
        const deleted = element("button", "回复的楼层已删除", "forum-quote thread-reply-chip is-deleted");
        deleted.type = "button";
        deleted.disabled = true;
        content.append(deleted);
      } else {
        const quote = element("button", "", "forum-quote thread-reply-chip");
        quote.type = "button";
        quote.setAttribute("aria-label", `跳转到 ${reply.floor} 楼 ${reply.username} 的评论`);
        quote.append(element("b", `↳ #${String(reply.floor).padStart(2, "0")} ${reply.username} `, "forum-quote-author"), element("span", reply.excerpt || ""));
        quote.addEventListener("click", () => scrollToForumComment(reply.id));
        content.append(quote);
      }
    }
    const body = element("div", "", "forum-comment-body thread-prose");
    body.append(forumBody(comment.body));
    const more = forumTextAction("展开全文", `展开 ${comment.floor} 楼全文`, () => {
      expanded = !expanded;
      applyExpanded();
      scheduleForumMap();
    });
    more.classList.add("thread-more");
    more.hidden = true;
    function applyExpanded() {
      body.dataset.collapsed = String(!expanded);
      more.setAttribute("aria-expanded", String(expanded));
      more.querySelector(".thread-action-label").textContent = expanded ? "收起" : "展开全文";
      more.setAttribute("aria-label", `${expanded ? "收起" : "展开"} ${comment.floor} 楼全文`);
    }
    if (accepted) expanded = true;
    applyExpanded();
    wrap.expandForumComment = () => { expanded = true; applyExpanded(); };
    wrap.readForumMeasure = () => ({ body, more, height: body.scrollHeight,
      limit: parseFloat(window.getComputedStyle(body).fontSize) * 11.5 });
    content.append(body, more);
    const actions = element("footer", "", "forum-actions thread-actions");
    if (!user.is_trial) {
      actions.append(forumTextAction("回复", `回复 ${comment.floor} 楼`, () => selectForumReply(comment)));
    }
    if (!user.is_trial && comment.user_id !== user.id && Number.isInteger(comment.helpful_count) && typeof comment.viewer_helpful === "boolean") {
      const helpful = forumTextAction(`有用 ${comment.helpful_count}`, `标记 ${comment.floor} 楼有用`, () => updateForumHelpful(comment, helpful));
      helpful.dataset.threadHelpful = "1";
      helpful.setAttribute("aria-pressed", String(comment.viewer_helpful));
      setForumDisabled(helpful, forumMutations.has(`helpful:${comment.id}`));
      actions.append(helpful);
    }
    if (!user.is_trial && user.id === post.user_id && comment.user_id !== user.id && Object.hasOwn(post, "accepted_comment_id")) {
      const accept = forumTextAction(accepted ? "取消采纳" : "采纳", `${accepted ? "取消采纳" : "采纳"} ${comment.floor} 楼`, () => updateForumAccepted(comment, accept));
      accept.dataset.threadAccept = "1";
      setForumDisabled(accept, forumMutations.has(`accepted:${post.id}`));
      actions.append(accept);
    }
    actions.append(element("span", "", "push"));
    if (!user.is_trial && user.id === comment.user_id) {
      const editBtn = forumTextAction("编辑", `编辑 ${comment.floor} 楼`, editForm);
      const deleteBtn = forumTextAction("删除", `删除 ${comment.floor} 楼`, () => run(async () => {
        if (!confirm("删除这条评论？无法恢复。")) return;
        await api(`/api/comments/${comment.id}`, { method: "DELETE" });
        let nextFloor = wrap.nextElementSibling;
        while (nextFloor?.hidden) nextFloor = nextFloor.nextElementSibling;
        let previousFloor = wrap.previousElementSibling;
        while (previousFloor?.hidden) previousFloor = previousFloor.previousElementSibling;
        const nextFloorId = nextFloor?.id || previousFloor?.id;
        wrap.remove();
        post.comments = post.comments.filter((item) => item.id !== comment.id);
        if (post.accepted_comment_id === comment.id) post.accepted_comment_id = null;
        // Clear all quoted copies immediately, including comments hidden by the filter.
        for (const item of post.comments) {
          if (item.reply_to?.id === comment.id) item.reply_to = { id: comment.id, floor: comment.floor, deleted: true };
        }
        if (post !== forumPost) return;
        if (forumReplyTarget?.id === comment.id) clearForumReply();
        renderForumComments(post.comments);
        const next = nextFloorId ? $(`#${nextFloorId}`) : null;
        (next || $("#forum-comment-body")).focus({ preventScroll: true });
        message("已删除这条评论。");
      }), true);
      actions.append(editBtn, deleteBtn);
    } else if (!user.is_trial) {
      const reportBtn = forumTextAction("举报", `举报 ${comment.floor} 楼`, () => {
        const form = reportForm();
        inlineForm = form;
        actions.replaceWith(form);
        form.querySelector("textarea").focus();
      });
      const reportAvatarBtn = forumTextAction("举报头像", `举报 ${comment.floor} 楼的头像`, () => {
        const form = avatarReportForm(comment.user_id, restoreActions);
        form.classList.add("forum-inline-form");
        inlineForm = form;
        actions.replaceWith(form);
        form.querySelector("textarea").focus();
      });
      actions.append(reportBtn, reportAvatarBtn);
    }
    if (actions.childElementCount) content.append(inlineForm || actions);
    wrap.append(floorHeading(), author, content);
  }

  function editForm() {
    const body = textarea(comment.body, 2000, 3);
    body.required = true;
    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", restoreActions);

    const form = document.createElement("form");
    form.className = "forum-inline-form";
    form.append(field("评论内容", body), save, cancel);
    window.EmojiPicker?.attach(body); // 表情面板：编辑评论
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/comments/${comment.id}`, {
          method: "PUT",
          body: JSON.stringify({ body: body.value }),
        });
        Object.assign(comment, updated);
        const normalized = comment.body.replace(/[\s\u001c-\u001f\u0085]+/gu, " ").trim();
        const excerpt = truncateExcerpt(normalized, 60);
        for (const item of post.comments) {
          if (item.reply_to?.id === comment.id && !item.reply_to.deleted) item.reply_to.excerpt = excerpt;
        }
        if (post !== forumPost) return;
        message("评论已更新。");
        inlineForm = null;
        editing = false;
        readOnly();
        renderForumComments(post.comments);
        $(`#forum-comment-${comment.id}`)?.focus({ preventScroll: true });
      });
    });

    inlineForm = form;
    editing = true;
    wrap.replaceChildren(floorHeading(), form);
    body.focus();
  }

  function reportForm() {
    const reason = textarea("", 500, 3);
    const submit = element("button", "提交举报", "primary");
    submit.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", restoreActions);

    const form = document.createElement("form");
    form.className = "forum-inline-form";
    form.append(field("举报原因（可选）", reason), submit, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/comments/${comment.id}/report`, {
          method: "POST",
          body: JSON.stringify({ reason: reason.value }),
        });
        message("已提交举报，管理员会尽快处理。");
        restoreActions();
      });
    });

    return form;
  }

  wrap.refreshForumComment = (updated, redraw = true) => {
    comment = updated;
    post = forumPost;
    if (redraw) readOnly();
  };
  readOnly();
  return wrap;
}

function renderForumComments(comments) {
  const list = $("#forum-comments");
  const active = document.activeElement;
  const hadFocus = active && list.contains(active);
  const selection = hadFocus && typeof active.selectionStart === "number"
    ? [active.selectionStart, active.selectionEnd, active.selectionDirection] : null;
  if (!list.forumCommentNodes || list.forumCommentPostId !== forumPost.id || list.forumCommentOwner !== user.id) {
    list.replaceChildren();
    list.forumCommentNodes = new Map();
    list.forumCommentPostId = forumPost.id;
    list.forumCommentOwner = user.id;
  }
  const nodes = list.forumCommentNodes;
  const currentIds = new Set(comments.map((comment) => comment.id));
  for (const [id, entry] of nodes) {
    if (!currentIds.has(id)) {
      entry.node.remove();
      nodes.delete(id);
    }
  }
  list.forumCommentsEmpty?.remove();
  list.forumCommentsEmpty = null;
  if (forumCommentOrder === "helpful" && !comments.some((item) => Number.isInteger(item.helpful_count))) forumCommentOrder = "earliest";
  const ordered = window.Thread.arrange(comments, { ...forumFilters(), onlyOp: false, codeOnly: false, mentionOnly: false });
  const visible = new Set(window.Thread.arrange(comments, forumFilters()).map((comment) => comment.id));
  let animationIndex = 0;
  for (const comment of ordered) {
    const signature = JSON.stringify([user.is_trial, forumPost.accepted_comment_id, comment]);
    let entry = nodes.get(comment.id);
    const isNew = !entry || entry.node.parentNode !== list;
    if (!entry || entry.node.parentNode !== list) {
      entry = { node: renderForumComment(comment), signature };
      nodes.set(comment.id, entry);
    } else if (entry.signature !== signature) {
      entry.node.refreshForumComment(comment);
      entry.signature = signature;
    } else {
      entry.node.refreshForumComment(comment, false);
    }
    // Native hidden + inert also remove every control in a filtered floor from Tab navigation.
    entry.node.hidden = !visible.has(comment.id);
    entry.node.inert = entry.node.hidden;
    if (isNew && !entry.node.hidden && animationIndex < 8) {
      entry.node.classList.add("is-entering");
      entry.node.style.setProperty("--i", String(animationIndex));
    }
    if (!entry.node.hidden) animationIndex += 1;
    list.append(entry.node);
  }
  if (!visible.size) {
    const empty = element("div", "", "forum-comments-empty");
    const seal = element("span", comments.length ? "候" : "首", "forum-empty-seal");
    seal.setAttribute("aria-hidden", "true");
    empty.append(seal, element("p", comments.length ? "没有符合条件的回复，试试取消筛选。" : "还没有评论，来抢沙发吧"));
    list.append(empty);
    list.forumCommentsEmpty = empty;
  }
  // Moving existing nodes may blur a focused field; restore its caret/selection without scrolling.
  if (hadFocus) {
    if (list.contains(active) && !active.closest(".forum-comment")?.hidden) {
      active.focus({ preventScroll: true });
      if (selection) active.setSelectionRange(...selection);
    } else {
      $("#forum-only-op").focus({ preventScroll: true });
    }
  }
  renderForumCommentControls();
  if (forumSummaryController) {
    const state = forumSummaryController.getState();
    if (state.summary && state.summary.comment_count !== comments.length) state.summary.stale = true;
    renderForumSummary(state);
  }
  scheduleForumMap();
}


function submitForumComment() {
  const form = $("#forum-comment-form");
  const body = $("#forum-comment-body");
  if (!forumDetailVisible() || user.is_trial || busy) return;
  if (!body.value.trim()) {
    setForumPreview(false);
    body.focus();
    body.reportValidity();
    forumAnnounce("请输入回复内容。");
    return;
  }
  if (!form.checkValidity()) {
    setForumPreview(false);
    form.reportValidity();
    return;
  }
  run(async () => {
    const post = forumPost;
    const epoch = sessionEpoch;
    const generation = forumDetailGeneration;
    const owner = user.id;
    const current = () => post === forumPost && epoch === sessionEpoch
      && generation === forumDetailGeneration && user?.id === owner && forumDetailVisible();
    const sentBody = body.value;
    const data = new FormData(form);
    const payload = { body: data.get("body") };
    if (forumReplyTarget) payload.reply_to_id = forumReplyTarget.id;
    let created;
    try {
      created = await api(`/api/posts/${post.id}/comments`, {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch (error) {
      if (error.status === 401 && !user && sessionEpoch === epoch + 1) {
        showForumLoginExpired();
        return;
      }
      if (!current()) return;
      const detail = error.detail || (/\p{Script=Han}/u.test(error.message || "") ? error.message : "网络连接失败，请保留草稿后重试。");
      forumAnnounce(detail);
      message(detail, true);
      return;
    }
    if (!current()) return;
    post.comments.push(created);
    if (body.value === sentBody) form.reset();
    clearForumReply();
    setForumPreview(false);
    const wasHidden = !window.Thread.arrange([created, ...post.comments.filter((item) => item.id !== created.id)], forumFilters()).some((item) => item.id === created.id);
    if (forumOnlyOp && !created.is_op) forumOnlyOp = false;
    if (forumCodeOnly && !window.Thread.hasCode(created.body)) forumCodeOnly = false;
    if (forumMentionOnly && !window.Thread.mentionsMe(created, post.comments, user)) forumMentionOnly = false;
    renderForumComments(post.comments);
    scrollToForumComment(created.id);
    forumAnnounce(wasHidden ? "评论已发表，已清除会隐藏新评论的筛选。" : "评论已发表。");
    message("评论已发表。");
  });
}

document.querySelectorAll("#forum-comment-sort button").forEach((button) => {
  button.addEventListener("click", () => {
    if (!forumPost || button.hidden) return;
    forumCommentOrder = button.dataset.forumOrder;
    renderForumComments(forumPost.comments);
    $("#forum-comment-status").textContent = `已按${{ earliest: "最早", latest: "最新", helpful: "最有用" }[forumCommentOrder]}排序，楼层号保持不变。`;
  });
});

$("#forum-only-op").addEventListener("click", () => {
  if (!forumPost) return;
  forumOnlyOp = !forumOnlyOp;
  renderForumComments(forumPost.comments);
  $("#forum-comment-status").textContent = forumOnlyOp ? "已切换为只看楼主。" : "已显示全部评论。";
});

$("#forum-code-only").addEventListener("click", () => {
  if (!forumPost) return;
  forumCodeOnly = !forumCodeOnly;
  renderForumComments(forumPost.comments);
  forumAnnounce(forumCodeOnly ? "已筛选含代码的评论，筛选条件取交集。" : "已取消含代码筛选。");
});
$("#forum-mention-only").addEventListener("click", () => {
  if (!forumPost) return;
  forumMentionOnly = !forumMentionOnly;
  renderForumComments(forumPost.comments);
  forumAnnounce(forumMentionOnly ? "已筛选提及或回复我的评论，筛选条件取交集。" : "已取消 @我 筛选。");
});

$("#forum-editor-tab").addEventListener("click", () => {
  setForumPreview(false);
  $("#forum-comment-body").focus();
});
$("#forum-preview-tab").addEventListener("click", () => setForumPreview(true));
$("#forum-insert-code").addEventListener("click", () => {
  setForumPreview(false);
  const body = $("#forum-comment-body");
  const start = body.selectionStart;
  const end = body.selectionEnd;
  const selected = body.value.slice(start, end);
  const before = start && body.value[start - 1] !== "\n" ? "\n" : "";
  const after = end < body.value.length && body.value[end] !== "\n" ? "\n" : "";
  const insertion = `${before}\x60\x60\x60\n${selected}\n\x60\x60\x60${after}`;
  if (body.value.length - (end - start) + insertion.length > 2000) {
    forumAnnounce("插入代码块后超过 2000 字，请先缩短回复。");
    return;
  }
  body.setRangeText(insertion, start, end, "end");
  body.setSelectionRange(start + before.length + 4, start + before.length + 4 + selected.length);
  body.focus();
  updateForumCommentCount();
});

document.addEventListener("keydown", (event) => {
  const dialogOpen = [...document.querySelectorAll('dialog[open], [role="dialog"], [aria-modal="true"]')]
    .some((node) => !node.hidden && !node.closest("[hidden]") && node.getAttribute("aria-hidden") !== "true");
  if (!forumDetailVisible() || dialogOpen || event.defaultPrevented || event.isComposing) return;
  const body = $("#forum-comment-body");
  const inBody = event.target === body;
  if (inBody && event.key === "Escape" && forumReplyTarget) {
    event.preventDefault();
    clearForumReply();
    forumAnnounce("已取消回复目标，草稿保留。");
    return;
  }
  if ((inBody || forumPreview && $("#forum-comment-form").contains(event.target))
    && (event.ctrlKey || event.metaKey) && !event.altKey && event.key === "Enter" && !user.is_trial) {
    event.preventDefault();
    submitForumComment();
    return;
  }
  if (!window.Thread.keyboardAllowed(event, { detailVisible: true, dialogOpen })) return;
  const key = event.key.toLowerCase();
  if (key === "j" || key === "k") {
    event.preventDefault();
    forumMoveFloor(key === "j" ? 1 : -1);
  } else if (key === "r") {
    const focused = document.activeElement?.closest(".forum-comment");
    const id = focused ? Number(focused.id.replace("forum-comment-", "")) : forumCurrentComment;
    const comment = window.Thread.arrange(forumPost.comments, forumFilters()).find((item) => item.id === id);
    if (comment && !user.is_trial) { event.preventDefault(); selectForumReply(comment); }
  }
});

$("#forum-reply-cancel").addEventListener("click", () => {
  clearForumReply();
  $("#forum-comment-body").focus();
});

$("#forum-comment-body").addEventListener("input", updateForumCommentCount);
// 表情面板：评论/回复框的按钮放在页脚（和字数并排），发新帖的正文按钮放在输入框下面。
window.EmojiPicker?.attach($("#forum-comment-body"), { container: $(".forum-composer-footer") });

$("#forum-comment-form").addEventListener("submit", (event) => {
  event.preventDefault();
  submitForumComment();
});
