// static/app/forum-post.js — 论坛：帖子详情/AI 要点/采纳。
// 由 static/app/forum.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

async function openForumPost(postId) {
  const generation = ++forumDetailGeneration;
  const epoch = sessionEpoch;
  const owner = user?.id;
  forumSummaryController?.reset();
  const status = $("#forum-open-status");
  const retry = $("#forum-open-retry");
  const current = () => generation === forumDetailGeneration && epoch === sessionEpoch && owner === user?.id && view === "forum";
  status.textContent = "正在打开帖子…";
  retry.hidden = true;
  if (retry.forumRetryHandler) retry.removeEventListener("click", retry.forumRetryHandler);
  retry.forumRetryHandler = () => { if (current()) void openForumPost(postId); };
  retry.addEventListener("click", retry.forumRetryHandler);
  let post;
  try {
    post = await api(`/api/posts/${postId}`);
  } catch (error) {
    if (error.status === 401 && !user && sessionEpoch === epoch + 1) {
      showForumLoginExpired();
      return;
    }
    if (!current()) return;
    status.textContent = error.status ? (error.detail || error.message || "打不开帖子，请稍后重试。") : "打不开帖子，请检查网络后重试。";
    retry.hidden = false;
    message(status.textContent, true);
    return;
  }
  if (generation !== forumDetailGeneration || epoch !== sessionEpoch || owner !== user?.id || view !== "forum") return;
  status.textContent = "";
  if (forumPost?.id !== post.id) {
    clearForumReply();
    $("#forum-comment-form").reset();
    forumOnlyOp = forumCodeOnly = forumMentionOnly = false;
    forumCurrentComment = null;
    forumMapModel = null;
    forumMapDrag = null;
    setForumPreview(false);
  }
  forumPost = post;
  if (forumReplyTarget && !post.comments.some((comment) => comment.id === forumReplyTarget.id)) {
    clearForumReply();
  }
  $("#forum-list").hidden = true;
  $("#forum-compose").hidden = true;
  $("#forum-detail").hidden = false;
  renderForumPost(post);
  renderForumComments(post.comments);
  if (user.ai_enabled && post.comments.length >= 2) forumSummary().load(post);
  else $("#forum-summary").hidden = true;
}

function updateForumPostStats() {
  if (!forumPost) return;
  const target = $("#forum-post-stats");
  if (!target) return; // An inline post editor deliberately replaces the read panel.
  const stats = window.Thread.stats(forumPost);
  target.replaceChildren();
  for (const [value, label, id] of [
    [stats.replies, "回复", "forum-post-comment-count"],
    [stats.participants, "人参与"], [stats.codeBlocks, "段代码"],
  ]) {
    const stat = element("span", "", "thread-stat");
    const number = element("b", String(value));
    if (id) number.id = id;
    stat.append(number, ` ${label}`);
    target.append(stat);
  }
  const recent = element("span", "最近活动 ", "thread-stat");
  recent.append(element("b", forumRelativeTime(stats.lastActivity)));
  target.append(recent);
}

function updateForumPostSignal() {
  const status = $("#forum-post-signal");
  if (!status || !forumPost) return;
  const supported = Object.hasOwn(forumPost, "accepted_comment_id");
  status.hidden = !supported;
  if (!supported) return;
  const solved = forumPost.comments.some((item) => item.id === forumPost.accepted_comment_id);
  status.classList.toggle("is-solved", solved);
  status.replaceChildren(element("i", "", "thread-signal-dot"), solved ? "已解决" : "讨论中");
}

function renderForumAccepted() {
  const root = $("#forum-accepted");
  const comment = forumPost?.comments.find((item) => item.id === forumPost.accepted_comment_id);
  root.hidden = !comment;
  root.replaceChildren();
  if (!comment) return;
  const heading = element("header", "", "thread-answer-head");
  const title = element("h3", "楼主已采纳的答案");
  title.id = "forum-accepted-title";
  heading.append(element("span", "✓", "thread-answer-icon"), title,
    element("span", `#${String(comment.floor).padStart(2, "0")} · ${comment.username}`, "thread-answer-who"));
  const excerpt = element("div", "", "thread-answer-excerpt");
  excerpt.append(forumBody(window.Thread.excerpt(comment.body, 160)));
  const footer = element("div", "", "thread-answer-foot");
  const jump = forumTextAction("跳到原楼层 ⌄", `跳转到 ${comment.floor} 楼的已采纳答案`, () => scrollToForumComment(comment.id, { clearFilters: true }));
  jump.classList.add("thread-link");
  footer.append(jump);
  if (!user.is_trial && user.id === forumPost.user_id) {
    const cancel = forumTextAction("取消采纳", "取消采纳此答案", () => updateForumAccepted(comment, cancel));
    cancel.dataset.threadAccept = "1";
    setForumDisabled(cancel, forumMutations.has(`accepted:${forumPost.id}`));
    footer.append(cancel);
  }
  if (Number.isInteger(comment.helpful_count)) footer.append(element("span", `${comment.helpful_count} 人觉得有用`, "thread-answer-meta"));
  root.append(heading, excerpt, footer);
}

function forumSummary() {
  if (!forumSummaryController) forumSummaryController = window.Thread.createSummaryController({
    api,
    getIdentity: () => ({ postId: forumPost?.id, sessionEpoch, userId: user?.id, visible: forumDetailVisible() }),
    onState: renderForumSummary,
  });
  return forumSummaryController;
}


function renderForumSummary(state) {
  const root = $("#forum-summary");
  const enabled = user?.ai_enabled && forumPost?.comments.length >= 2 && forumDetailVisible();
  root.hidden = !enabled;
  if (!enabled) { root.replaceChildren(); return; }
  root.setAttribute("aria-busy", String(state.phase === "generating" || state.phase === "loading"));
  const heading = element("h3", "✦ AI 要点");
  heading.id = "forum-summary-title";
  root.replaceChildren(heading);
  if (state.phase === "generating" || state.phase === "loading") {
    root.append(element("p", `正在阅读 ${forumPost.comments.length} 条回复…`, "thread-summary-status"));
    for (let i = 0; i < 3; i += 1) {
      const skeleton = element("span", "", "thread-summary-skeleton");
      skeleton.setAttribute("aria-hidden", "true");
      root.append(skeleton);
    }
  } else if (state.summary) {
    const summary = state.summary;
    root.append(element("p", summary.tldr, "thread-summary-tldr"));
    const points = element("ul", "", "thread-summary-points");
    for (const point of summary.points) {
      const item = element("li", point.text);
      for (const floor of point.floors.filter((floor) => forumPost.comments.some((comment) => comment.floor === floor))) {
        const chip = forumTextAction(`#${String(floor).padStart(2, "0")}`, `跳转到 ${floor} 楼`, () => forumJumpFloor(floor));
        chip.classList.add("thread-floor");
        item.append(chip);
      }
      points.append(item);
    }
    root.append(points);
    if (summary.open_questions.length) {
      root.append(element("h4", "待解决的问题"));
      const questions = element("ul");
      summary.open_questions.forEach((question) => questions.append(element("li", question)));
      root.append(questions);
    }
    root.append(element("p", `基于 ${summary.comment_count} 条回复 · 生成于 ${timestamp(summary.generated_at)}`, "thread-summary-meta"));
    if (summary.stale) root.append(element("p", "之后又有新回复，要点可能过时", "thread-summary-stale"));
  } else if (state.phase !== "error") {
    root.append(element("p", "把讨论提炼成一句结论和可跳转的楼层要点。", "thread-summary-status"));
  }
  if (state.error) root.append(element("p", state.error, "thread-summary-error"));
  if (!user.is_trial && (!state.summary || state.summary.stale || state.phase === "error")) {
    const button = forumTextAction(state.summary ? "重新生成（用 1 次 AI 额度）" : "AI 提炼要点（用 1 次 AI 额度）",
      "AI 提炼要点，使用 1 次 AI 额度", () => forumSummary().generate(forumPost));
    setForumDisabled(button, state.phase === "generating" || state.phase === "loading");
    root.append(button);
  }
}


function forumJumpFloor(floor) {
  const comment = forumPost?.comments.find((item) => item.floor === floor);
  if (comment) scrollToForumComment(comment.id);
}

async function updateForumHelpful(comment, button) {
  const post = forumPost;
  const epoch = sessionEpoch;
  const owner = user?.id;
  const generation = forumDetailGeneration;
  const key = `helpful:${comment.id}`;
  if (!post || forumMutations.has(key)) return;
  const token = {};
  forumMutations.set(key, token);
  try { await window.Thread.performMutation({
    button,
    request: () => api(`/api/comments/${comment.id}/helpful`, { method: comment.viewer_helpful ? "DELETE" : "PUT" }),
    isCurrent: () => post === forumPost && epoch === sessionEpoch && owner === user?.id && generation === forumDetailGeneration && forumDetailVisible(),
    onSuccess: (result) => {
      comment.helpful_count = result.helpful_count;
      comment.viewer_helpful = result.viewer_helpful;
      if (forumMutations.get(key) === token) forumMutations.delete(key);
      renderForumComments(post.comments);
      forumAnnounce(result.viewer_helpful ? "已标记有用。" : "已取消有用。");
      $(`#forum-comment-${comment.id} [data-thread-helpful]`)?.focus({ preventScroll: true });
    },
    onError: forumAnnounce,
  }); } finally {
    if (forumMutations.get(key) === token) forumMutations.delete(key);
    const current = post === forumPost ? $(`#forum-comment-${comment.id} [data-thread-helpful]`) : null;
    if (current) setForumDisabled(current, forumMutations.has(key));
  }
}


async function updateForumAccepted(comment, button) {
  const post = forumPost;
  const epoch = sessionEpoch;
  const owner = user?.id;
  const generation = forumDetailGeneration;
  const key = `accepted:${post?.id}`;
  if (!post || forumMutations.has(key)) return;
  const cancel = post.accepted_comment_id === comment.id;
  const token = {};
  forumMutations.set(key, token);
  const controls = [...document.querySelectorAll("#forum-page [data-thread-accept]")];
  controls.forEach((control) => setForumDisabled(control, true));
  try {
    await window.Thread.performMutation({
      button,
      request: () => api(`/api/posts/${post.id}/accepted`, cancel ? { method: "DELETE" }
        : { method: "PUT", body: JSON.stringify({ comment_id: comment.id }) }),
      isCurrent: () => post === forumPost && epoch === sessionEpoch && owner === user?.id && generation === forumDetailGeneration && forumDetailVisible(),
      onSuccess: (result) => {
        post.accepted_comment_id = result.accepted_comment_id;
        if (forumMutations.get(key) === token) forumMutations.delete(key);
        renderForumComments(post.comments);
        forumAnnounce(result.accepted_comment_id === null ? "已取消采纳。" : "已采纳这条答案。");
        $(`#forum-comment-${comment.id} [data-thread-accept]`)?.focus({ preventScroll: true });
      },
      onError: forumAnnounce,
    });
  } finally {
    if (forumMutations.get(key) === token) forumMutations.delete(key);
    const currentControls = post === forumPost ? document.querySelectorAll("#forum-page [data-thread-accept]") : controls;
    currentControls.forEach((control) => setForumDisabled(control, forumMutations.has(key)));
  }
}

function renderForumPost(post) {
  const root = $("#forum-post");

  function restoreActions() {
    readOnly();
    root.querySelector(".forum-actions button")?.focus({ preventScroll: true });
  }

  function readOnly() {
    root.replaceChildren();
    const bar = element("div", "", "thread-post-bar");
    const signal = element("span", "", "thread-status");
    signal.id = "forum-post-signal";
    bar.append(element("span", `THREAD #${String(post.id).padStart(4, "0")}`, "thread-id"), signal);
    root.append(bar);
    const authorLine = element("div", "", "forum-post-author");
    const authorInfo = element("div", "", "forum-post-author-info");
    const authorName = element("div", "", "forum-author-name");
    authorName.append(profileAuthor(post.user_id, post.username), element("span", "楼主", "forum-op-stamp"));
    const date = element("div", "", "forum-post-date");
    date.append(forumTime(post.created_at));
    if (post.updated_at) date.append(" · 编辑于 ", forumTime(post.updated_at));
    authorInfo.append(authorName, date);
    authorLine.append(
      avatarElement(post.user_id, post.username, post.avatar_version, {
        hasAvatar: post.has_avatar,
      }),
      authorInfo
    );
    const title = element("h2", post.title, "thread-post-title");
    title.id = "forum-detail-title";
    const body = element("div", "", "forum-post-body thread-post-body thread-prose");
    body.append(forumBody(post.body));
    root.append(
      authorLine,
      title,
      body
    );
    const footer = element("footer", "", "forum-post-footer thread-post-foot");
    const stats = element("div", "", "thread-stats");
    stats.id = "forum-post-stats";
    footer.append(stats);
    if (user.id === post.user_id) {
      const editBtn = forumTextAction("编辑", "编辑这条帖子", editForm);
      const deleteBtn = forumTextAction("删除", "删除这条帖子", () => run(async () => {
        if (!confirm("删除这条帖子？帖子下的评论也会一起不可见，无法恢复。")) return;
        await api(`/api/posts/${post.id}`, { method: "DELETE" });
        if (forumPost !== post) return;
        message("已删除这条帖子。");
        await showForumList();
      }), true);
      const actions = element("div", "", "forum-actions");
      actions.append(editBtn, deleteBtn);
      footer.append(actions);
    } else if (!user.is_trial) {
      const actions = element("div", "", "forum-actions");
      const reportBtn = forumTextAction("举报", "举报这条帖子", () => {
        const form = reportForm();
        actions.replaceWith(form);
        form.querySelector("textarea").focus();
      });
      const reportAvatarBtn = forumTextAction("举报头像", `举报楼主 ${post.username} 的头像`, () => {
        const form = avatarReportForm(post.user_id, restoreActions);
        form.classList.add("forum-inline-form");
        actions.replaceWith(form);
        form.querySelector("textarea").focus();
      });
      actions.append(reportBtn, reportAvatarBtn);
      footer.append(actions);
    }
    root.append(footer);
    updateForumPostStats();
    updateForumPostSignal();
  }

  function editForm() {
    const title = document.createElement("input");
    title.value = post.title;
    title.maxLength = 200;
    title.required = true;

    const body = textarea(post.body, 8000, 8);
    body.required = true;

    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", restoreActions);

    const form = document.createElement("form");
    form.className = "forum-inline-form";
    form.append(field("标题", title), field("正文", body), save, cancel);
    window.EmojiPicker?.attach(body); // 表情面板：编辑帖子正文
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/posts/${post.id}`, {
          method: "PUT",
          body: JSON.stringify({ title: title.value, body: body.value }),
        });
        post.title = updated.title;
        post.body = updated.body;
        post.updated_at = updated.updated_at;
        if (forumPost !== post) return;
        message("帖子已更新。");
        restoreActions();
      });
    });

    const heading = element("h2", "编辑帖子");
    heading.id = "forum-detail-title";
    root.replaceChildren(heading, form);
    title.focus();
  }

  // 只替换"举报"按钮所在的操作区，不动上面已经展示的标题和正文。
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
        await api(`/api/posts/${post.id}/report`, {
          method: "POST",
          body: JSON.stringify({ reason: reason.value }),
        });
        message("已提交举报，管理员会尽快处理。");
        restoreActions();
      });
    });

    return form;
  }

  readOnly();
}
