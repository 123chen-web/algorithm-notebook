// static/app/forum-board.js — 论坛：帖子列表/发帖入口/共享小工具。
// 由 static/app/forum.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

// ---- 领域状态 ----
let forumPost = null;
let forumCommentOrder = "earliest";
let forumOnlyOp = false;
let forumCodeOnly = false;
let forumMentionOnly = false;
let forumPreview = false;
let forumDetailGeneration = 0;
let forumCurrentComment = null;
let forumSummaryController = null;
let forumMapFrame = null;
let forumMapModel = null;
let forumMapDrag = null;
let forumMapDragFrame = null;
const forumMutations = new Map();
let forumReplyTarget = null;
let forumListGeneration = 0;

async function showForumList() {
  forumDetailGeneration += 1;
  forumSummaryController?.reset();
  forumPost = null;
  forumCurrentComment = null;
  forumMapModel = null;
  forumMapDrag = null;
  $("#forum-minimap").hidden = true;
  $("#forum-floor-nav").hidden = true;
  clearForumReply();
  $("#forum-compose").hidden = true;
  $("#forum-detail").hidden = true;
  $("#forum-list").hidden = false;
  $("#forum-new-post-btn").hidden = user.is_trial;
  await loadForumPosts();
}

// ---- 事件绑定与模块配置 ----

// 列表的请求、渲染、筛选和分页都在 static/board.js 里；这里只负责递增代数
// （「返回讨论区」靠它判断是否有更新的列表加载抢走了焦点）。

async function loadForumPosts() {
  forumListGeneration += 1;
  await window.Board.show();
}

// 从帖子详情回到列表：页面回到顶部，焦点落在刚才读的那个帖子上（找不到就落在列表标题）。
// 焦点不为它滚动页面——回到讨论区就该先看到讨论区的顶部。

function restoreForumListPosition(postId) {
  window.scrollTo({ top: 0, behavior: "instant" });
  const row = postId ? $(`#forum-posts [data-post-id="${postId}"] .board-open`) : null;
  (row || $("#forum-list-title")).focus({ preventScroll: true });
}

function showForumCompose(options = {}) {
  forumDetailGeneration += 1;
  forumSummaryController?.reset();
  forumMapModel = null;
  forumMapDrag = null;
  $("#forum-list").hidden = true;
  $("#forum-detail").hidden = true;
  $("#forum-compose").hidden = false;
  window.Board.openCompose(options);
}

function showForumLoginExpired() {
  // signedOut() 可能回到长介绍页；将错误和重新登录入口放在同一屏。
  history.replaceState(null, "", `${location.pathname}${location.search}#/auth`);
  renderPageRoute();
  message("登录已过期，请重新登录后回复。", true);
  $("#notice")?.scrollIntoView({ block: "start", behavior: "auto" });
}

function forumRelativeTime(value) {
  const seconds = Math.max(0, (Date.now() - new Date(value).getTime()) / 1000);
  if (!Number.isFinite(seconds)) return timestamp(value);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
  if (seconds < 2592000) return `${Math.floor(seconds / 86400)} 天前`;
  if (seconds < 31536000) return `${Math.floor(seconds / 2592000)} 个月前`;
  return `${Math.floor(seconds / 31536000)} 年前`;
}

function forumTime(value, relative = false) {
  const time = element("time", relative ? forumRelativeTime(value) : timestamp(value));
  time.dateTime = value;
  time.title = timestamp(value);
  return time;
}

function forumTextAction(label, ariaLabel, callback, danger = false) {
  const button = element("button", "", `forum-text-action link-button thread-act${danger ? " danger" : ""}`);
  const iconName = /^回复/.test(label) ? "reply" : /有用/.test(label) ? "up"
    : /^编辑/.test(label) ? "edit" : /^删除/.test(label) ? "delete"
      : /^举报/.test(label) ? "flag" : /采纳/.test(label) ? "check"
        : /^跳到原楼层|^展开全文|^收起/.test(label) ? "down" : null;
  if (iconName) button.append(window.Thread.actionIcon(iconName));
  button.append(element("span", label, "thread-action-label"));
  button.type = "button";
  button.setAttribute("aria-label", ariaLabel);
  button.addEventListener("click", callback);
  return button;
}

function truncateExcerpt(text, limit) {
  const chars = Array.from(text);
  if (chars.length <= limit) return text;
  const joins = (character) => {
    const code = character.codePointAt(0);
    return character === "\u200d" || character === "\ufe0e" || character === "\ufe0f"
      || (code >= 0x1F3FB && code <= 0x1F3FF) || (code >= 0xE0020 && code <= 0xE007F)
      || /\p{M}/u.test(character);
  };
  let cut = limit;
  while (cut > 0 && (joins(chars[cut]) || chars[cut - 1] === "\u200d")) cut -= 1;
  let regional = 0;
  const isRegional = (character) => character.codePointAt(0) >= 0x1F1E6 && character.codePointAt(0) <= 0x1F1FF;
  while (regional < cut && isRegional(chars[cut - 1 - regional])) regional += 1;
  if (regional % 2) cut -= 1;
  return `${chars.slice(0, cut).join("")}…`;
}

function forumFilters() {
  return { order: forumCommentOrder, onlyOp: forumOnlyOp, codeOnly: forumCodeOnly, mentionOnly: forumMentionOnly, me: user };
}

function forumDetailVisible() {
  return Boolean(user && view === "forum" && forumPost && !$("#forum-detail").hidden && !$("#forum-page").hidden);
}

function forumAnnounce(text) {
  $("#forum-comment-status").textContent = text;
}

// The application's existing global busy switch respects data-blocked. Keep
// forum-local pending and unavailable controls disabled when that switch ends.

function setForumDisabled(button, blocked) {
  button.dataset.blocked = blocked ? "1" : "0";
  button.disabled = blocked || busy;
}

function forumBody(text) {
  return window.Thread.renderBody(text, { mentionMe: user?.username, onCopyStatus: forumAnnounce });
}

$("#forum-new-post-btn").addEventListener("click", () => {
  message();
  showForumCompose();
});

$("#forum-compose-cancel").addEventListener("click", () => run(async () => {
  message();
  await showForumList();
}));

// 详情页顶部和底部各有一个「返回讨论区」，行为完全一样。
document.querySelectorAll("#forum-back, #forum-back-bottom").forEach((button) => {
  button.addEventListener("click", () => run(async () => {
    const openedPostId = forumPost?.id;
    message();
    const loading = showForumList();
    // loadForumPosts 在第一个 await 之前就递增了代数；之后若用户又点进别的帖子或离开，就不再抢焦点。
    const generation = forumListGeneration;
    await loading;
    if (generation !== forumListGeneration || view !== "forum" || $("#forum-list").hidden) return;
    restoreForumListPosition(openedPostId);
  }));
});

// 讨论区主页与发帖页（static/board.js）：请求都带登录代次，登出再登录之后迟到的响应会被丢弃。

function profileAuthor(userId, username) {
  return window.Profile?.author(userId, username) || element("strong", username);
}

window.Board?.mount({
  api,
  getUser: () => user,
  getEpoch: () => sessionEpoch,
  getView: () => view,
  isListVisible: () => !$("#forum-page").hidden && !$("#forum-list").hidden,
  isDetailVisible: forumDetailVisible,
  onLoginExpired: showForumLoginExpired,
  openPost: (id) => { void openForumPost(id); },
  cancelOpening: () => {
    forumDetailGeneration += 1;
    $("#forum-open-status").textContent = "";
    $("#forum-open-retry").hidden = true;
  },
  openCompose: (options) => {
    message();
    showForumCompose(options);
  },
  afterPublish: (post) => run(async () => {
    await openForumPost(post.id);
    message("帖子已发布。");
  }),
  avatar: avatarElement,
  author: (userId, username) => profileAuthor(userId, username),
  renderBody: forumBody,
  timestamp,
});
