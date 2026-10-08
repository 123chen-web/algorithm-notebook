"use strict";

/* 讨论区主页（static/board.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   纯函数直接调用；控制器挂在手工搭的最小 DOM 上，请求由测试决定何时、怎样回应。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

const plain = (value) => JSON.parse(JSON.stringify(value));
const NOW = Date.UTC(2026, 9, 4, 12, 0, 0);
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString();
const HOUR = 3600 * 1000;
const DAY = 24 * HOUR;

/* ───────────── 纯函数 ───────────── */

test("relativeTime: every boundary between 刚刚 / 分钟 / 小时 / 天 / 日期", () => {
  const { Board } = load(["board.js"]).window;
  const at = (seconds) => Board.relativeTime(new Date(NOW - seconds * 1000).toISOString(), NOW, "UTC");
  assert.equal(at(0), "刚刚");
  assert.equal(at(59), "刚刚");
  assert.equal(at(60), "1 分钟前");
  assert.equal(at(3599), "59 分钟前");
  assert.equal(at(3600), "1 小时前");
  assert.equal(at(24 * 3600 - 60), "23 小时前"); // 23 小时 59 分
  assert.equal(at(24 * 3600), "1 天前");
  assert.equal(at(29 * 86400), "29 天前");
  assert.equal(at(30 * 86400 - 1), "29 天前");
  assert.equal(at(30 * 86400), "2026-09-04");
  assert.equal(at(400 * 86400), "2025-08-30");
});

test("relativeTime: future times read as 刚刚; invalid input gives an empty string", () => {
  const { Board } = load(["board.js"]).window;
  assert.equal(Board.relativeTime(new Date(NOW + 5 * DAY).toISOString(), NOW), "刚刚");
  for (const bad of [undefined, null, "", "不是时间", NaN, {}, []]) assert.equal(Board.relativeTime(bad, NOW), "", String(bad));
  assert.equal(Board.relativeTime(new Date(NOW).toISOString(), NaN), "");
});

test("relativeTime: dates older than 30 days use the user's time zone, and a bad zone falls back to UTC", () => {
  const { Board } = load(["board.js"]).window;
  const late = "2026-08-01T20:30:00+00:00"; // 上海已经是 8 月 2 日
  assert.equal(Board.relativeTime(late, NOW, "UTC"), "2026-08-01");
  assert.equal(Board.relativeTime(late, NOW, "Asia/Shanghai"), "2026-08-02");
  assert.equal(Board.relativeTime(late, NOW, "Not/AZone"), "2026-08-01");
});

test("buildQuery: only non-default values, encoded, in a fixed order", () => {
  const { Board } = load(["board.js"]).window;
  assert.equal(Board.buildQuery({ tab: "all", sort: "activity", zone: "", q: "" }), "/api/posts");
  assert.equal(Board.buildQuery({}), "/api/posts");
  assert.equal(Board.buildQuery({ tab: "unanswered" }), "/api/posts?filter=unanswered");
  assert.equal(Board.buildQuery({ tab: "solved", sort: "hot" }), "/api/posts?sort=hot&filter=solved");
  assert.equal(Board.buildQuery({ sort: "new" }), "/api/posts?sort=new");
  assert.equal(Board.buildQuery({ zone: "none" }), "/api/posts?zone=none");
  assert.equal(Board.buildQuery({ zone: "算法" }), `/api/posts?zone=${encodeURIComponent("算法")}`);
  assert.equal(Board.buildQuery({ q: "  二分 & 边界=1  " }), `/api/posts?q=${encodeURIComponent("二分 & 边界=1")}`);
  assert.equal(
    Board.buildQuery({ q: "x", sort: "hot", tab: "mine", zone: "前端" }, { limit: 50, offset: 40 }),
    `/api/posts?q=x&sort=hot&filter=mine&zone=${encodeURIComponent("前端")}&limit=50&offset=40`,
  );
  assert.equal(Board.buildQuery({}, { limit: 20, offset: 0 }), "/api/posts");
  assert.equal(Board.buildQuery({}, { limit: 999 }), "/api/posts?limit=50");
  assert.equal(Board.buildQuery({}, { limit: 0 }), "/api/posts");
});

test("buildQuery: unknown tab, sort or zone values never reach the URL", () => {
  const { Board } = load(["board.js"]).window;
  assert.equal(Board.buildQuery({ tab: "participated", sort: "oldest", zone: "../etc" }), "/api/posts");
  assert.equal(Board.buildQuery({ tab: "evil&x=1" }), "/api/posts");
  assert.deepEqual(plain(Board.normalizeSelection({ tab: "x", sort: "hot", zone: "不存在" })), { tab: "all", sort: "hot", zone: "" });
  assert.deepEqual(plain(Board.normalizeSelection(null)), { tab: "all", sort: "activity", zone: "" });
});

const person = (id, name, extra = {}) => ({ user_id: id, username: name, avatar_version: 0, has_avatar: false, ...extra });
const post = (id, extra = {}) => ({
  id, title: `帖子 ${id}`, created_at: iso(2 * DAY), user_id: 2, username: "周知远", avatar_version: 0,
  has_avatar: false, zone: "算法", excerpt: "最近三道二分题都栽在边界上……", comment_count: 3,
  last_activity_at: iso(16 * HOUR), last_commenter: person(3, "苏晚"),
  participants: [person(2, "周知远"), person(3, "苏晚")], participant_count: 2,
  has_code: false, solved: false, helpful_total: 0, hot: false, is_mine: false, ...extra,
});
const page = (posts, extra = {}) => ({
  posts, total: posts.length, has_more: false,
  counts: { all: 8, unanswered: 3, solved: 2, mine: 1 }, zone_counts: { 算法: 3, 前端: 1, none: 2 }, ...extra,
});

test("postViewModel: solved, open, hot, mine, new and code states", () => {
  const { Board } = load(["board.js"]).window;
  const model = (extra) => Board.postViewModel(post(42, extra), { nowMs: Date.now() });
  const solved = model({ solved: true, hot: true, has_code: true, comment_count: 7 });
  assert.equal(solved.state, "solved");
  assert.equal(solved.statAria, "7 条回复，已解决");
  assert.deepEqual(plain(solved.pills.map((pill) => pill.kind)), ["solved", "hot", "code"]);
  assert.equal(solved.hot, true);
  assert.equal(solved.idLabel, "#0042");
  assert.equal(solved.statLabel, "回复");

  const open = model({ comment_count: 0, last_commenter: null, participants: [person(2, "周知远")], participant_count: 1 });
  assert.equal(open.state, "open");
  assert.equal(open.statAria, "还没有回复");
  assert.equal(open.statLabel, "待回复");
  assert.deepEqual(plain(open.pills.map((pill) => pill.kind)), ["open"]);
  assert.equal(open.last, null);

  assert.deepEqual(plain(model({ is_mine: true }).pills.map((pill) => pill.kind)), ["mine"]);
  assert.equal(model({ comment_count: 1 }).statAria, "1 条回复");
  const fresh = model({ created_at: iso(5 * HOUR + 59 * 60 * 1000), comment_count: 0 });
  assert.deepEqual(plain(fresh.pills.map((pill) => pill.kind)), ["open", "new"]);
  const stale = model({ created_at: iso(6 * HOUR + 1000), comment_count: 0 });
  assert.deepEqual(plain(stale.pills.map((pill) => pill.kind)), ["open"]);
});

test("postViewModel: missing new fields degrade to 'show nothing' without throwing", () => {
  const { Board } = load(["board.js"]).window;
  const bare = Board.postViewModel({ id: 7, title: "老接口", created_at: iso(HOUR), user_id: 1, username: "甲", avatar_version: 0, has_avatar: false });
  assert.equal(bare.excerpt, null);
  assert.equal(bare.showCount, false);
  assert.equal(bare.statAria, "");
  assert.deepEqual(plain(bare.pills.map((pill) => pill.kind)), ["new"]);
  assert.deepEqual(plain(bare.people), []);
  assert.equal(bare.peopleMore, 0);
  assert.equal(bare.zone, null);
  assert.equal(bare.last, null);
  assert.equal(bare.hot, false);
  // solved 只有严格等于 true 才算
  assert.equal(Board.postViewModel({ id: 1, title: "t", solved: "yes", comment_count: 2 }).state, "discussing");
});

test("postViewModel: excerpt, +N participants and the people stack", () => {
  const { Board } = load(["board.js"]).window;
  const many = Array.from({ length: 6 }, (_, index) => person(index + 1, `人${index + 1}`));
  const model = Board.postViewModel(post(1, { participants: many, participant_count: 9 }));
  assert.equal(model.people.length, 4);
  assert.equal(model.peopleMore, 5);
  assert.equal(model.peopleTotal, 9);
  const exact = Board.postViewModel(post(1, { participants: many.slice(0, 4), participant_count: 4 }));
  assert.equal(exact.peopleMore, 0);
  const shrunk = Board.postViewModel(post(1, { participants: many.slice(0, 3), participant_count: 1 }));
  assert.equal(shrunk.peopleTotal, 3, "participant_count never drops below what is shown");
  assert.equal(Board.postViewModel(post(1, { excerpt: "" })).excerpt, "（代码）");
  assert.equal(Board.postViewModel(post(1, { excerpt: "" })).excerptIsCode, true);
  assert.equal(Board.postViewModel(post(1, { excerpt: "正文" })).excerptIsCode, false);
});

test("zoneChips: fixed order, only positive counts plus the selected one, 未分区 only when present", () => {
  const { Board } = load(["board.js"]).window;
  const labels = (counts, selected) => plain(Board.zoneChips(counts, selected).map((chip) => [chip.zone, chip.count]));
  assert.deepEqual(labels({ 前端: 1, 算法: 3, none: 2, 后端: 0 }, ""), [["算法", 3], ["前端", 1], ["none", 2]]);
  assert.deepEqual(labels({ 概率统计: 1 }, ""), [["概率统计", 1]]);
  assert.deepEqual(labels({ 概率统计: 1 }, "后端"), [["后端", 0], ["概率统计", 1]]);
  assert.deepEqual(labels({ 算法: 1 }, "none"), [["算法", 1], ["none", 0]]);
  assert.equal(Board.zoneChips({ none: 2 }, "").at(-1).label, "未分区");
  const all = Board.zoneChips(undefined, "");
  assert.deepEqual(plain(all.map((chip) => chip.zone)), plain(Board.ZONES));
  assert.ok(all.every((chip) => chip.count === null));
  assert.equal(Board.zoneChips(null, "none").at(-1).zone, "none");
});

test("keyAllowed: only when the list is visible and nothing else owns the keyboard", () => {
  const env = load(["board.js"]);
  const { Board } = env.window;
  const input = env.document.createElement("input");
  const field = env.document.createElement("div");
  const wrapper = env.document.createElement("div");
  wrapper.setAttribute("contenteditable", "true");
  const inner = env.document.createElement("span");
  wrapper.append(inner);
  const press = (target, props = {}) => new FakeEvent("keydown", { props: { key: "j", target, ...props } });
  const ok = { listVisible: true };
  assert.equal(Board.keyAllowed(press(field), ok), true);
  assert.equal(Board.keyAllowed(press(input), ok), false, "typing in an input");
  assert.equal(Board.keyAllowed(press(env.document.createElement("textarea")), ok), false);
  assert.equal(Board.keyAllowed(press(env.document.createElement("select")), ok), false);
  assert.equal(Board.keyAllowed(press(inner), ok), false, "inside contenteditable");
  for (const modifier of ["ctrlKey", "metaKey", "altKey"]) assert.equal(Board.keyAllowed(press(field, { [modifier]: true }), ok), false, modifier);
  assert.equal(Board.keyAllowed(press(field, { defaultPrevented: true }), ok), false);
  assert.equal(Board.keyAllowed(press(field, { isComposing: true }), ok), false);
  assert.equal(Board.keyAllowed(press(field), { listVisible: false }), false);
  assert.equal(Board.keyAllowed(press(field), {}), false);
  assert.equal(Board.keyAllowed(press(field), { ...ok, detailVisible: true }), false);
  assert.equal(Board.keyAllowed(press(field), { ...ok, dialogOpen: true }), false);
  assert.equal(Board.keyAllowed(press(field), { ...ok, paletteOpen: true }), false);
  assert.equal(Board.keyAllowed(press(field, { shiftKey: true }), ok), true);
});

test("selection preferences: stored without the search text, and broken storage never throws", () => {
  const env = load(["board.js"]);
  const { Board } = env.window;
  const store = new Map();
  const storage = { getItem: (key) => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, value) };
  assert.equal(Board.savePrefs(storage, { tab: "solved", sort: "hot", zone: "none", q: "秘密搜索词" }), true);
  assert.deepEqual(plain(JSON.parse(store.get("forum-board:v1"))), { tab: "solved", sort: "hot", zone: "none" });
  assert.deepEqual(plain(Board.loadPrefs(storage)), { tab: "solved", sort: "hot", zone: "none" });
  store.set("forum-board:v1", "{坏的 json");
  assert.deepEqual(plain(Board.loadPrefs(storage)), { tab: "all", sort: "activity", zone: "" });
  store.set("forum-board:v1", JSON.stringify({ tab: "hacked", sort: 5, zone: ["算法"] }));
  assert.deepEqual(plain(Board.loadPrefs(storage)), { tab: "all", sort: "activity", zone: "" });
  const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); } };
  assert.deepEqual(plain(Board.loadPrefs(throwing)), { tab: "all", sort: "activity", zone: "" });
  assert.equal(Board.savePrefs(throwing, { tab: "mine" }), false);
  env.window.localStorage = throwing;
  assert.equal(Board.savePrefs(null, { tab: "mine" }), false);
  assert.deepEqual(plain(Board.loadPrefs()), { tab: "all", sort: "activity", zone: "" });
});

test("insertCodeBlock: caret placement, wrapping a selection, and fences always start a line", () => {
  const { Board } = load(["board.js"]).window;
  assert.deepEqual(plain(Board.insertCodeBlock("", 0, 0)), { value: "```\n\n```", start: 4, end: 4 });
  const middle = Board.insertCodeBlock("看这里\n", 4, 4);
  assert.equal(middle.value, "看这里\n```\n\n```");
  assert.equal(middle.start, 8);
  const midLine = Board.insertCodeBlock("前文后文", 2, 2);
  assert.equal(midLine.value, "前文\n```\n\n```\n后文");
  assert.equal(midLine.value.slice(midLine.start, midLine.end), "");
  assert.equal(midLine.value[midLine.start - 1], "\n");
  const wrapped = Board.insertCodeBlock("先 x = 1 再", 2, 7);
  assert.equal(wrapped.value, "先 \n```\nx = 1\n```\n 再");
  assert.equal(wrapped.value.slice(wrapped.start, wrapped.end), "x = 1");
  const lineWrap = Board.insertCodeBlock("a\nb\nc", 2, 3);
  assert.equal(lineWrap.value, "a\n```\nb\n```\nc");
  assert.equal(lineWrap.value.slice(lineWrap.start, lineWrap.end), "b");
  assert.equal(Board.insertCodeBlock("abc", 99, 120).value, "abc\n```\n\n```");
  assert.equal(Board.insertCodeBlock("abc", -5, -1).value, "```\n\n```\nabc");
  assert.equal(Board.insertCodeBlock(null, 0, 0).value, "```\n\n```");
});

test("applyTemplate: fills an empty body, appends after a confirmation otherwise, refuses to overflow", () => {
  const { Board } = load(["board.js"]).window;
  const template = "【我想做什么 / 题目是什么】\n\n【我已经试过什么】\n\n【具体卡在哪一步（可贴代码或报错）】\n```\n\n```\n";
  assert.equal(Board.TEMPLATE, template);
  assert.deepEqual(plain(Board.applyTemplate("")), { value: template, needsConfirm: false });
  assert.deepEqual(plain(Board.applyTemplate("   \n ")), { value: template, needsConfirm: false });
  assert.deepEqual(plain(Board.applyTemplate("已有内容")), { value: `已有内容\n\n${template}`, needsConfirm: true });
  assert.equal(Board.applyTemplate("行尾换行\n").value, `行尾换行\n\n${template}`);
  assert.equal(Board.applyTemplate("两个换行\n\n").value, `两个换行\n\n${template}`);
  assert.equal(Board.applyTemplate("x".repeat(8000 - template.length - 2)).value.length, 8000, "exactly fits");
  assert.equal(Board.applyTemplate("x".repeat(8000 - template.length - 1)), null, "one more character does not");
  assert.equal(Board.applyTemplate("x".repeat(8000)), null);
  assert.equal(Board.applyTemplate(null).value, template);
});

test("drafts: save, load, clear, ignore other users, ignore tampered or broken data, never throw", () => {
  const { Board } = load(["board.js"]).window;
  const store = new Map();
  const storage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, value),
    removeItem: (key) => store.delete(key),
  };
  assert.equal(Board.draftKey(7), "forum-draft:v1:7");
  assert.equal(Board.saveDraft(storage, 7, { title: "标题", body: "正文", zone: "前端" }), true);
  assert.deepEqual(plain(Board.loadDraft(storage, 7)), { title: "标题", body: "正文", zone: "前端" });
  assert.equal(Board.loadDraft(storage, 8), null, "another user sees nothing");
  // 键对、内容里的 userId 被改成别人：忽略
  store.set("forum-draft:v1:8", store.get("forum-draft:v1:7"));
  assert.equal(Board.loadDraft(storage, 8), null);
  assert.equal(Board.saveDraft(storage, 7, { title: "  ", body: "\n" }), true);
  assert.equal(store.has("forum-draft:v1:7"), false, "a blank draft removes the key");
  assert.equal(Board.saveDraft(storage, 7, { title: "只有标题", body: "", zone: "不存在" }), true);
  assert.equal(Board.loadDraft(storage, 7).zone, "");
  store.set("forum-draft:v1:7", "不是 json");
  assert.equal(Board.loadDraft(storage, 7), null);
  store.set("forum-draft:v1:7", JSON.stringify({ userId: 7, title: 5, body: "x" }));
  assert.equal(Board.loadDraft(storage, 7), null);
  store.set("forum-draft:v1:7", JSON.stringify({ userId: 7, title: "t".repeat(500), body: "b".repeat(9000), zone: "算法" }));
  const long = Board.loadDraft(storage, 7);
  assert.equal(long.title.length, 200);
  assert.equal(long.body.length, 8000);
  assert.equal(Board.clearDraft(storage, 7), true);
  assert.equal(Board.loadDraft(storage, 7), null);
  assert.equal(Board.loadDraft(storage, undefined), null);
  assert.equal(Board.saveDraft(storage, null, { title: "x", body: "y" }), false);
  const throwing = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); }, removeItem() { throw new Error("denied"); } };
  assert.equal(Board.saveDraft(throwing, 7, { title: "x", body: "y" }), false);
  assert.equal(Board.loadDraft(throwing, 7), null);
  assert.equal(Board.clearDraft(throwing, 7), false);
});

/* ───────────── 控制器：假页面 ───────────── */

function addNode(env, tag, id, { parent = env.document.body, className = "" } = {}) {
  const item = env.document.createElement(tag);
  item.id = id;
  if (className) item.className = className;
  parent.append(item);
  return item;
}

function buildPage(env) {
  const add = (tag, id, options) => addNode(env, tag, id, options);
  const page = add("section", "forum-page");
  const list = add("section", "forum-list", { parent: page });
  for (const id of ["board-count-live", "board-readouts", "board-r-all", "board-r-open", "board-r-solved", "board-r-mine",
    "board-rail-open", "board-rail-help-text", "board-rail-help-go", "board-rail-pulse", "board-rail-solved", "board-rail-all",
    "board-rail-mine", "board-ask", "forum-list-status", "forum-list-title", "board-zones", "board-rail-zones",
    "board-notice", "board-foot", "board-end"]) add("div", id, { parent: list });
  add("ol", "forum-posts", { parent: list });
  for (const id of ["forum-new-post-btn", "board-r-open-btn", "board-rail-help", "board-ask-apply", "board-more"]) add("button", id, { parent: list });
  add("form", "forum-search-form", { parent: list });
  add("input", "forum-search", { parent: list });
  add("select", "board-sort", { parent: list });
  const tabs = add("div", "board-tabs", { parent: list });
  for (const tab of ["all", "unanswered", "solved", "mine"]) {
    const button = env.document.createElement("button");
    button.dataset.boardTab = tab;
    button.setAttribute("aria-pressed", "false");
    if (tab === "unanswered" || tab === "solved") {
      const badge = env.document.createElement("span");
      badge.className = "n";
      badge.hidden = true;
      button.append(badge);
    }
    tabs.append(button);
  }
  const compose = add("section", "forum-compose", { parent: page });
  compose.hidden = true;
  add("form", "forum-compose-form", { parent: compose });
  add("input", "forum-compose-title-input", { parent: compose });
  add("textarea", "forum-compose-body", { parent: compose });
  for (const id of ["forum-compose-title-count", "forum-compose-count", "forum-compose-progress", "forum-compose-preview",
    "forum-compose-zones", "forum-compose-draft", "forum-compose-status", "forum-compose-tools"]) add("div", id, { parent: compose });
  for (const id of ["forum-compose-edit-tab", "forum-compose-preview-tab", "forum-compose-draft-clear", "forum-compose-send",
    "forum-compose-code", "forum-compose-template"]) add("button", id, { parent: compose });
  env.document.querySelector("#forum-compose-draft").hidden = true;
  env.document.querySelector("#forum-compose-preview").hidden = true;
  return { page, list, compose };
}

const q = (env, selector) => env.document.querySelector(selector);
const qa = (env, selector) => env.document.querySelectorAll(selector);
const text = (env, selector) => q(env, selector).textContent;

function fakeTimers(env) {
  const queue = [];
  let counter = 0;
  env.window.setTimeout = (callback, delay) => { queue.push({ id: ++counter, callback, delay }); return counter; };
  env.window.clearTimeout = (id) => { const index = queue.findIndex((item) => item.id === id); if (index >= 0) queue.splice(index, 1); };
  return {
    queue,
    delays: () => queue.map((item) => item.delay),
    fire() { const item = queue.shift(); assert.ok(item, "a timer is pending"); item.callback(); },
  };
}

/** 装好页面、挂上控制器；api 钩子走 window.fetch，请求记在 env.calls 里，由测试逐个回应。 */
function setup({ user = { id: 1, username: "我", timezone: "Asia/Shanghai", is_trial: false }, storage, noRenderBody = false, extraHooks = {} } = {}) {
  const env = load(["board.js"]);
  if (storage) env.window.localStorage = storage;
  const dom = buildPage(env);
  const state = { user, epoch: 1, view: "forum", listVisible: true, detailVisible: false, opened: [], composed: [], published: [], confirms: [] };
  const hooks = {
    async api(path, options = {}) {
      const response = await env.window.fetch(path, options);
      const body = await response.json();
      if (!response.ok) { const error = new Error(body.detail); error.status = response.status; throw error; }
      return body;
    },
    getUser: () => state.user,
    getEpoch: () => state.epoch,
    getView: () => state.view,
    isListVisible: () => state.listVisible,
    isDetailVisible: () => state.detailVisible,
    openPost: (id) => state.opened.push(id),
    openCompose: (options) => state.composed.push(options),
    afterPublish: (created) => state.published.push(created),
    timestamp: (value) => `T:${value}`,
    confirm: (message) => { state.confirms.push(message); return state.confirmAnswer !== false; },
    ...(noRenderBody ? {} : { renderBody: (body) => { const node = env.document.createElement("div"); node.className = "rendered"; node.textContent = `渲染:${body}`; return node; } }),
    ...extraHooks,
  };
  env.window.Board.mount(hooks);
  return { env, dom, state, hooks, Board: env.window.Board };
}

const click = (node) => node.dispatchEvent(new FakeEvent("click", { bubbles: true }));
const urls = (env) => env.calls.map((call) => call.url);
async function answer(env, index, body, status = 200) {
  env.respond(env.calls[index], status, body);
  await tick();
}
const rows = (env) => qa(env, "#forum-posts .board-row");
const rowTitles = (env) => rows(env).map((row) => row.querySelector(".board-open").textContent);

// Feedback regression: users tap the row/status, and browsers focus a button before clicking it.
test("feedback: focusin keeps a zero-reply post title connected and clickable", async () => {
  const { env, Board, state } = setup();
  const loading = Board.show();
  await answer(env, 0, page([post(71, { comment_count: 0, last_commenter: null })]));
  await loading;
  const title = q(env, "#forum-posts .board-open");
  title.focus();
  title.dispatchEvent(new FakeEvent("focusin", { bubbles: true }));
  assert.equal(title.isConnected, true, "focus must not replace the pending click target");
  assert.equal(q(env, "#forum-posts .board-open"), title);
  click(title);
  assert.deepEqual(state.opened, [71]);
});

for (const [target, selector] of [["post row", ".board-row"], ["待回复 status", ".board-stat"], ["等你来回 pill", ".board-pill.is-open"]]) {
  test(`feedback: tapping ${target} opens a zero-reply post exactly once`, async () => {
    const { env, Board, state } = setup();
    const loading = Board.show();
    await answer(env, 0, page([post(72, { comment_count: 0, last_commenter: null })]));
    await loading;
    click(q(env, `#forum-posts ${selector}`));
    assert.deepEqual(state.opened, [72]);
  });
}
const keydown = (env, key, props = {}) => {
  const event = new FakeEvent("keydown", { props: { key, target: env.document.body, ...props } });
  env.document.dispatchEvent(event);
  return event;
};

/* ───────────── 控制器：列表 ───────────── */

test("rows: author profile entry selects its user without opening the post", async () => {
  const selected = [];
  const ctx = setup();
  ctx.hooks.author = (id, name) => {
    const link = ctx.env.document.createElement("button"); link.className = "profile-author";
    link.textContent = name; link.addEventListener("click", () => selected.push(id)); return link;
  };
  const loading = ctx.Board.show();
  await answer(ctx.env, 0, page([post(42, { user_id: 9, username: "作者九" })])); await loading;
  click(q(ctx.env, ".board-meta .profile-author"));
  assert.deepEqual(selected, [9]);
  assert.deepEqual(ctx.state.opened, []);
});

test("show: skeleton while loading, then rows, readouts, tab counts, zone chips and the status line", async () => {
  const { env, Board } = setup();
  const loading = Board.show();
  await tick();
  assert.deepEqual(urls(env), ["/api/posts"]);
  const skeletons = qa(env, "#forum-posts .board-skeleton");
  assert.equal(skeletons.length, 4);
  assert.ok(skeletons.every((item) => item.getAttribute("aria-hidden") === "true"));
  assert.equal(text(env, "#forum-list-status"), "正在加载帖子列表…");

  env.respond(env.calls[0], 200, page([post(1, { solved: true, hot: true, comment_count: 7 }), post(2, { comment_count: 0, last_commenter: null })], { total: 2 }));
  await loading;
  assert.equal(qa(env, "#forum-posts .board-skeleton").length, 0);
  assert.equal(rows(env).length, 2);
  assert.equal(text(env, "#forum-list-status"), "当前显示 2 个帖子");
  assert.equal(text(env, "#board-count-live"), "2 个帖子");
  assert.equal(q(env, "#board-readouts").hidden, false);
  assert.deepEqual(["#board-r-all", "#board-r-open", "#board-r-solved", "#board-r-mine"].map((id) => text(env, id)), ["8", "3", "2", "1"]);
  assert.equal(text(env, "#board-rail-open"), "3");
  assert.equal(text(env, "#board-rail-help-text"), "个帖子还没人回复");
  const badges = qa(env, "#board-tabs .n");
  assert.deepEqual(badges.map((badge) => [badge.hidden, badge.textContent]), [[false, "3"], [false, "2"]]);
  const chips = qa(env, "#board-zones [data-zone-filter]").map((chip) => chip.dataset.zoneFilter);
  assert.deepEqual(chips, ["", "算法", "前端", "none"]);
  assert.equal(qa(env, "#board-rail-zones button").length, 3);
  assert.equal(q(env, "#forum-posts li").dataset.postId, "1");
});

test("rows: state classes, aria labels, tags, meta line, people stack and the open button", async () => {
  const { env, Board, state } = setup();
  const loading = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([
    post(42, { solved: true, hot: true, has_code: true, comment_count: 7, participants: [person(2, "周知远"), person(3, "苏晚"), person(4, "三"), person(5, "四")], participant_count: 6 }),
    post(43, { comment_count: 0, last_commenter: null, is_mine: true, participants: [person(2, "周知远")], participant_count: 1, created_at: iso(HOUR), excerpt: "" }),
  ]));
  await loading;
  const [first, second] = rows(env);
  assert.ok(first.classList.contains("is-solved") && first.classList.contains("is-hot"));
  assert.equal(first.querySelector(".board-stat").getAttribute("aria-label"), "7 条回复，已解决");
  assert.equal(first.querySelector(".board-stat b").textContent, "7");
  assert.equal(first.querySelectorAll(".board-pill").map((pill) => pill.textContent).join("|"), "已解决|热门|含代码");
  assert.equal(first.querySelector(".board-zone-chip").dataset.zone, "算法");
  assert.equal(first.querySelector(".id").textContent, "#0042");
  assert.equal(first.querySelector(".who").textContent, "周知远");
  assert.match(first.querySelector(".board-meta time").textContent, /^2 天前发布$/);
  assert.equal(first.querySelector(".board-meta time").title.startsWith("T:"), true);
  assert.equal(first.querySelector(".board-last").textContent.startsWith("苏晚 · "), true);
  assert.equal(first.querySelector(".board-people").getAttribute("aria-label"), "6 人参与");
  assert.equal(first.querySelector(".board-people .n").textContent, "+2");
  assert.equal(first.querySelectorAll(".board-people .avatar").length, 4);
  assert.equal(first.querySelector(".board-excerpt").textContent, "最近三道二分题都栽在边界上……");

  assert.ok(second.classList.contains("is-open"));
  assert.equal(second.querySelector(".board-stat").getAttribute("aria-label"), "还没有回复");
  assert.equal(second.querySelector(".board-stat span").textContent, "待回复");
  assert.equal(second.querySelectorAll(".board-pill").map((pill) => pill.textContent).join("|"), "等你来回|我发的|刚刚");
  assert.equal(second.querySelector(".board-excerpt").textContent, "（代码）");
  assert.equal(second.querySelector(".board-last"), null);
  assert.equal(second.querySelector(".board-people .n"), null);

  click(first.querySelector(".board-open"));
  assert.deepEqual(plain(state.opened), [42]);
  assert.equal(first.querySelector(".board-open").tagName, "BUTTON");
});

test("rows: user-controlled text is inserted as text, never as markup", async () => {
  const { env, Board } = setup();
  const loading = Board.show();
  await tick();
  const evil = "<img src=x onerror=alert(1)>";
  env.respond(env.calls[0], 200, page([post(1, { title: evil, excerpt: evil, username: evil, zone: "算法" })]));
  await loading;
  assert.equal(qa(env, "#forum-posts img").length, 0);
  assert.equal(rows(env)[0].querySelector(".board-open").textContent, evil);
  assert.equal(rows(env)[0].querySelector(".board-excerpt").textContent, evil);
});

test("rows: a response without the new fields still renders cleanly with no empty shells", async () => {
  const { env, Board } = setup();
  const loading = Board.show();
  await tick();
  env.respond(env.calls[0], 200, { posts: [{ id: 5, title: "老接口的帖子", created_at: iso(3 * DAY), user_id: 1, username: "甲", avatar_version: 0, has_avatar: false }] });
  await loading;
  const row = rows(env)[0];
  assert.equal(row.classList.contains("no-stat"), true);
  assert.equal(row.querySelector(".board-stat"), null);
  assert.equal(row.querySelector(".board-excerpt"), null);
  assert.equal(row.querySelector(".board-people"), null);
  assert.equal(row.querySelector(".board-tags"), null);
  assert.equal(q(env, "#board-readouts").hidden, true, "no counts, no readouts");
  assert.equal(q(env, "#board-rail-pulse").hidden, true);
  assert.deepEqual(qa(env, "#board-tabs .n").map((badge) => badge.hidden), [true, true]);
  assert.equal(text(env, "#board-count-live"), "1 个帖子");
  assert.equal(qa(env, "#board-zones [data-zone-filter]").length, 9, "no zone_counts: every zone chip, without numbers");
  assert.equal(q(env, "#board-foot").hidden, false);
  assert.equal(text(env, "#board-end"), "已显示全部 1 个帖子");
});

test("changing tab, zone and sort re-requests from offset 0 with only the non-default parameters", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;

  click(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "unanswered"));
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?filter=unanswered");
  await answer(env, 1, page([post(2, { comment_count: 0 })]));
  assert.equal(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "unanswered").getAttribute("aria-pressed"), "true");
  assert.equal(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "all").getAttribute("aria-pressed"), "false");

  click(qa(env, "#board-zones [data-zone-filter]").find((chip) => chip.dataset.zoneFilter === "算法"));
  await tick();
  assert.equal(env.calls.at(-1).url, `/api/posts?filter=unanswered&zone=${encodeURIComponent("算法")}`);
  await answer(env, 2, page([post(3)]));

  q(env, "#board-sort").value = "hot";
  q(env, "#board-sort").dispatchEvent(new FakeEvent("change", { props: { target: q(env, "#board-sort") } }));
  await tick();
  assert.equal(env.calls.at(-1).url, `/api/posts?sort=hot&filter=unanswered&zone=${encodeURIComponent("算法")}`);
  await answer(env, 3, page([post(4)]));
  assert.equal(q(env, "#board-sort").value, "hot");
});

test("zone chips toggle off on a second click and 未分区 sends zone=none", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  const chip = (zone) => qa(env, "#board-zones [data-zone-filter]").find((item) => item.dataset.zoneFilter === zone);
  click(chip("none"));
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?zone=none");
  await answer(env, 1, page([post(2, { zone: null })]));
  assert.equal(chip("none").getAttribute("aria-pressed"), "true");
  click(chip("none"));
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts");
  await answer(env, 2, page([post(1)]));
  assert.equal(chip("none").getAttribute("aria-pressed"), "false");
  assert.equal(chip("").getAttribute("aria-pressed"), "true");
  // 侧栏里的分区列表是同一套筛选
  click(qa(env, "#board-rail-zones button").find((button) => button.dataset.zoneFilter === "前端"));
  await tick();
  assert.equal(env.calls.at(-1).url, `/api/posts?zone=${encodeURIComponent("前端")}`);
  await answer(env, 3, page([post(5, { zone: "前端" })]));
});

test("the 等你回复 readout and the rail's 去帮忙 button switch to the 待回复 tab", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  click(q(env, "#board-r-open-btn"));
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?filter=unanswered");
  await answer(env, 1, page([post(2, { comment_count: 0 })]));
  click(qa(env, "#board-tabs [data-board-tab]")[0]);
  await tick();
  await answer(env, 2, page([post(1)]));
  click(q(env, "#board-rail-help"));
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?filter=unanswered");
  await answer(env, 3, page([post(2)]));
});

test("the rail says everyone has a reply when nothing is unanswered", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)], { counts: { all: 1, unanswered: 0, solved: 0, mine: 0 } }));
  await first;
  assert.equal(text(env, "#board-rail-open"), "0");
  assert.equal(text(env, "#board-rail-help-text"), "所有帖子都有人回复");
  assert.equal(q(env, "#board-rail-help-go").hidden, true);
});

/* ───────────── 控制器：搜索与竞态 ───────────── */

test("search: typing is debounced 250ms and only the last value is requested; Enter requests at once", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  const timers = fakeTimers(env);
  const input = q(env, "#forum-search");
  for (const value of ["二", "二分", "二分查"]) {
    input.value = value;
    input.dispatchEvent(new FakeEvent("input"));
  }
  assert.equal(timers.queue.length, 1, "each keystroke replaces the pending timer");
  assert.deepEqual(plain(timers.delays()), [250]);
  assert.equal(env.calls.length, 1, "nothing requested yet");
  timers.fire();
  await tick();
  assert.equal(env.calls.length, 2);
  assert.equal(env.calls[1].url, `/api/posts?q=${encodeURIComponent("二分查")}`);
  assert.equal(text(env, "#forum-list-title"), "搜索结果");
  await answer(env, 1, page([post(9)]));

  input.value = "边界";
  input.dispatchEvent(new FakeEvent("input"));
  const submit = new FakeEvent("submit");
  q(env, "#forum-search-form").dispatchEvent(submit);
  assert.equal(submit.defaultPrevented, true);
  assert.equal(timers.queue.length, 0, "Enter cancels the pending debounce");
  await tick();
  assert.equal(env.calls.at(-1).url, `/api/posts?q=${encodeURIComponent("边界")}`);
  await answer(env, 2, page([post(10)]));
  assert.equal(env.calls.length, 3);
});

test("search: an unchanged value does not request again; a value over 200 characters never leaves the page", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  const timers = fakeTimers(env);
  const input = q(env, "#forum-search");
  input.value = "   ";
  input.dispatchEvent(new FakeEvent("input"));
  timers.fire();
  await tick();
  assert.equal(env.calls.length, 1, "blank equals the current empty query");
  input.value = "字".repeat(201);
  q(env, "#forum-search-form").dispatchEvent(new FakeEvent("submit"));
  await tick();
  assert.equal(env.calls.length, 1);
  assert.equal(text(env, "#forum-list-status"), "搜索关键词不能超过 200 个字符。");
  input.value = "😀".repeat(200);
  q(env, "#forum-search-form").dispatchEvent(new FakeEvent("submit"));
  await tick();
  assert.equal(env.calls.length, 2, "200 emoji are 200 characters, not 400");
  await answer(env, 1, page([post(3)]));
});

test("search: Escape clears the box, blurs it and reloads the unfiltered list", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  const input = q(env, "#forum-search");
  input.value = "二分";
  q(env, "#forum-search-form").dispatchEvent(new FakeEvent("submit"));
  await tick();
  await answer(env, 1, page([post(2)]));
  let blurred = 0;
  input.blur = () => { blurred += 1; };
  input.dispatchEvent(new FakeEvent("keydown", { props: { key: "Escape" } }));
  await tick();
  assert.equal(input.value, "");
  assert.equal(blurred, 1);
  assert.equal(env.calls.at(-1).url, "/api/posts");
  await answer(env, 2, page([post(1)]));
  // 已经是空的再按 Esc：只失焦，不再请求
  input.dispatchEvent(new FakeEvent("keydown", { props: { key: "Escape" } }));
  await tick();
  assert.equal(env.calls.length, 3);
});

test("race: quickly changing conditions keeps only the last response, whatever order they arrive in", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  const tab = (name) => qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === name);
  click(tab("unanswered"));
  click(tab("solved"));
  click(tab("mine"));
  await tick();
  assert.deepEqual(urls(env).slice(1), ["/api/posts?filter=unanswered", "/api/posts?filter=solved", "/api/posts?filter=mine"]);
  env.respond(env.calls[3], 200, page([post(30, { title: "最后一次" })]));
  await tick();
  env.respond(env.calls[1], 200, page([post(10, { title: "第一次的迟到响应" })]));
  env.respond(env.calls[2], 200, page([post(20, { title: "第二次的迟到响应" })]));
  await tick();
  assert.deepEqual(rowTitles(env), ["最后一次"]);
  assert.equal(tab("mine").getAttribute("aria-pressed"), "true");
});

test("race: a response after sign-out and sign-in as someone else is dropped; so is one from an old epoch", async () => {
  const { env, Board, state } = setup();
  const loading = Board.show();
  await tick();
  Board.reset(); // 登出
  state.epoch += 1;
  state.user = { id: 2, username: "别人", timezone: "UTC", is_trial: false };
  env.respond(env.calls[0], 200, page([post(1, { title: "上一位用户的帖子" })]));
  await loading;
  assert.deepEqual(rowTitles(env), []);
  assert.equal(q(env, "#board-readouts").hidden, true);
  assert.equal(text(env, "#board-count-live"), "讨论区");

  const second = Board.show();
  await tick();
  assert.equal(env.calls.length, 2, "the new account asks for its own list");
  // 只换代次、不调用 reset（比如 api() 里的 401 处理先于 reset）：同样丢弃
  state.epoch += 1;
  env.respond(env.calls[1], 200, page([post(2, { title: "旧代次的响应" })]));
  await second;
  assert.deepEqual(rowTitles(env), []);

  const third = Board.show();
  await tick();
  env.respond(env.calls[2], 200, page([post(3, { title: "现在的帖子" })]));
  await third;
  assert.deepEqual(rowTitles(env), ["现在的帖子"]);
});

test("race: a response that arrives after leaving the list (detail or another view) is dropped", async () => {
  const { env, Board, state } = setup();
  const loading = Board.show();
  await tick();
  state.listVisible = false; // 点进了帖子详情
  env.respond(env.calls[0], 200, page([post(1)]));
  await loading;
  assert.deepEqual(rowTitles(env), []);
  state.listVisible = true;
  state.view = "home";
  const again = Board.show();
  await tick();
  env.respond(env.calls[1], 200, page([post(1)]));
  await again;
  assert.deepEqual(rowTitles(env), []);
});

/* ───────────── 控制器：分页 ───────────── */

test("pagination: load more appends without resetting, de-duplicates, announces, and ends with 已显示全部", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  const firstPage = Array.from({ length: 20 }, (_, index) => post(100 - index, { title: `第一页 ${index}` }));
  env.respond(env.calls[0], 200, page(firstPage, { total: 25, has_more: true }));
  await first;
  assert.equal(rows(env).length, 20);
  assert.equal(q(env, "#board-more").hidden, false);
  assert.equal(text(env, "#board-more"), "加载更多（还有 5 个）");
  assert.equal(q(env, "#board-end").hidden, true);

  q(env, "#board-more").focus();
  click(q(env, "#board-more"));
  click(q(env, "#board-more")); // 重复点击：只发一次请求
  await tick();
  assert.equal(env.calls.length, 2);
  assert.equal(env.calls[1].url, "/api/posts?offset=20");
  assert.equal(q(env, "#board-more").disabled, true);
  const second = Array.from({ length: 5 }, (_, index) => post(80 - index, { title: `第二页 ${index}` }));
  second.unshift(firstPage[19]); // 翻页期间有新帖：上一页最后一条再次出现
  env.respond(env.calls[1], 200, page(second, { total: 25, has_more: false }));
  await tick();
  assert.equal(rows(env).length, 25, "the duplicate is not added twice");
  assert.equal(rowTitles(env)[0], "第一页 0", "earlier rows are untouched");
  assert.equal(text(env, "#forum-list-status"), "已加载 5 个");
  assert.equal(q(env, "#board-more").hidden, true);
  assert.equal(q(env, "#board-end").hidden, false);
  assert.equal(text(env, "#board-end"), "已显示全部 25 个帖子");
  assert.equal(env.document.activeElement.className, "board-open", "focus moves to the first new row once the button is gone");
});

test("pagination: focus stays on the button while more remain; a failed page keeps what was loaded", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page(Array.from({ length: 20 }, (_, index) => post(index + 1)), { total: 60, has_more: true }));
  await first;
  const more = q(env, "#board-more");
  more.focus();
  click(more);
  await tick();
  env.respond(env.calls[1], 500, { detail: "服务器开小差了" });
  await tick();
  assert.equal(rows(env).length, 20);
  assert.equal(text(env, "#forum-list-status"), "服务器开小差了");
  assert.equal(more.disabled, false);
  assert.equal(env.document.activeElement, more);
  click(more);
  await tick();
  assert.equal(env.calls[2].url, "/api/posts?offset=20", "retry asks for the same page");
  env.respond(env.calls[2], 200, page(Array.from({ length: 20 }, (_, index) => post(index + 21)), { total: 60, has_more: true }));
  await tick();
  assert.equal(rows(env).length, 40);
  assert.equal(text(env, "#board-more"), "加载更多（还有 20 个）");
  assert.equal(env.document.activeElement, more);
  assert.equal(text(env, "#forum-list-status"), "已加载 20 个");
});

test("pagination: a page that arrives after the conditions changed is dropped", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page(Array.from({ length: 20 }, (_, index) => post(index + 1)), { total: 40, has_more: true }));
  await first;
  click(q(env, "#board-more"));
  await tick();
  click(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "solved"));
  await tick();
  env.respond(env.calls[2], 200, page([post(900, { title: "已解决的帖子" })]));
  await tick();
  env.respond(env.calls[1], 200, page([post(500, { title: "旧条件的第二页" })], { total: 40 }));
  await tick();
  assert.deepEqual(rowTitles(env), ["已解决的帖子"]);
});

test("pagination: has_more falls back to total when the flag is missing", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, { posts: Array.from({ length: 20 }, (_, index) => post(index + 1)), total: 33 });
  await first;
  assert.equal(q(env, "#board-more").hidden, false);
  assert.equal(text(env, "#board-more"), "加载更多（还有 13 个）");
});

/* ───────────── 控制器：空 / 错误 / 返回 / 偏好 ───────────── */

test("empty board: a seal and a post button, and only text for trial accounts", async () => {
  const { env, Board, state } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([], { counts: { all: 0, unanswered: 0, solved: 0, mine: 0 }, zone_counts: {} }));
  await first;
  const notice = q(env, "#board-notice");
  assert.equal(notice.hidden, false);
  assert.equal(notice.querySelector(".forum-empty-seal").textContent, "空");
  assert.equal(notice.querySelector("p").textContent, "还没有帖子，来发第一条吧。");
  click(notice.querySelector("button"));
  assert.deepEqual(plain(state.composed), [{}]);
  assert.equal(q(env, "#board-foot").hidden, true);
  assert.equal(text(env, "#forum-list-status"), "还没有帖子，来发第一条吧");

  const trial = setup({ user: { id: 9, username: "体验", timezone: "UTC", is_trial: true } });
  const loading = trial.Board.show();
  await tick();
  trial.env.respond(trial.env.calls[0], 200, page([], { counts: { all: 0, unanswered: 0, solved: 0, mine: 0 }, zone_counts: {} }));
  await loading;
  assert.equal(trial.env.document.querySelector("#board-notice button"), null);
  assert.match(trial.env.document.querySelector("#board-notice p").textContent, /体验账号只能浏览/);
});

test("empty result under filters offers 清除筛选, which resets tab, zone and search but keeps the sort", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  q(env, "#board-sort").value = "new";
  q(env, "#board-sort").dispatchEvent(new FakeEvent("change", { props: { target: q(env, "#board-sort") } }));
  await tick();
  await answer(env, 1, page([post(1)]));
  click(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "mine"));
  await tick();
  await answer(env, 2, page([post(1)]));
  q(env, "#forum-search").value = "不存在的词";
  q(env, "#forum-search-form").dispatchEvent(new FakeEvent("submit"));
  await tick();
  await answer(env, 3, page([], { total: 0 }));
  assert.equal(rows(env).length, 0);
  assert.equal(text(env, "#forum-list-status"), "没有符合条件的帖子");
  const reset = q(env, "#board-notice button");
  assert.equal(reset.textContent, "清除筛选");
  click(reset);
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?sort=new");
  assert.equal(q(env, "#forum-search").value, "");
  await answer(env, 4, page([post(1)]));
  assert.equal(rows(env).length, 1);
  assert.equal(q(env, "#board-notice").hidden, true);
});

test("error: the message is shown with a retry button that asks again", async () => {
  const { env, Board } = setup();
  const loading = Board.show();
  await tick();
  env.respond(env.calls[0], 500, { detail: "数据库暂时不可用" });
  await loading;
  assert.equal(qa(env, "#forum-posts .board-skeleton").length, 0);
  assert.equal(q(env, "#board-notice").querySelector("p").textContent, "数据库暂时不可用");
  assert.equal(text(env, "#forum-list-status"), "数据库暂时不可用");
  click(q(env, "#board-notice button"));
  await tick();
  assert.equal(env.calls.length, 2);
  await answer(env, 1, page([post(1)]));
  assert.equal(q(env, "#board-notice").hidden, true);
  assert.equal(rows(env).length, 1);
});

test("returning from a post keeps the loaded rows, selection and filters, and refreshes in place", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page(Array.from({ length: 20 }, (_, index) => post(index + 1)), { total: 25, has_more: true }));
  await first;
  click(q(env, "#board-more"));
  await tick();
  await answer(env, 1, page(Array.from({ length: 5 }, (_, index) => post(index + 21)), { total: 25 }));
  assert.equal(rows(env).length, 25);
  click(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "solved"));
  await tick();
  await answer(env, 2, page(Array.from({ length: 22 }, (_, index) => post(index + 1, { solved: true })), { total: 22 }));
  assert.equal(rows(env).length, 22);

  const again = Board.show(); // 从详情返回
  assert.equal(rows(env).length, 22, "rows stay while the refresh is in flight");
  assert.equal(qa(env, "#forum-posts .board-skeleton").length, 0, "no skeleton flash");
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?filter=solved&limit=22", "refresh asks for as many as were loaded");
  env.respond(env.calls.at(-1), 200, page([post(7, { title: "刷新后的唯一结果", solved: true })], { total: 1 }));
  await again;
  assert.deepEqual(rowTitles(env), ["刷新后的唯一结果"]);
  assert.equal(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "solved").getAttribute("aria-pressed"), "true");
});

test("returning with more than 50 loaded rows refreshes at most 50", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page(Array.from({ length: 20 }, (_, index) => post(index + 1)), { total: 80, has_more: true }));
  await first;
  for (let step = 1; step <= 3; step += 1) {
    click(q(env, "#board-more"));
    await tick();
    await answer(env, step, page(Array.from({ length: 20 }, (_, index) => post(step * 20 + index + 1)), { total: 80, has_more: step < 3 }));
  }
  assert.equal(rows(env).length, 80);
  void Board.show();
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?limit=50");
});

test("a failed refresh on return keeps the old rows and shows the retry notice", async () => {
  const { env, Board } = setup();
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  const again = Board.show();
  await tick();
  env.respond(env.calls[1], 503, { detail: "暂时无法刷新" });
  await again;
  assert.equal(rows(env).length, 1);
  assert.equal(q(env, "#board-notice").querySelector("p").textContent, "暂时无法刷新");
});

test("preferences: tab, sort and zone are restored from localStorage and saved when changed; the search text is not", async () => {
  const store = new Map([["forum-board:v1", JSON.stringify({ tab: "solved", sort: "hot", zone: "前端" })]]);
  const storage = { getItem: (key) => (store.has(key) ? store.get(key) : null), setItem: (key, value) => store.set(key, value), removeItem: (key) => store.delete(key) };
  const { env, Board } = setup({ storage });
  Board.reset(); // 登出 / 重新进入会重新读取偏好
  const loading = Board.show();
  await tick();
  assert.equal(env.calls[0].url, `/api/posts?sort=hot&filter=solved&zone=${encodeURIComponent("前端")}`);
  env.respond(env.calls[0], 200, page([post(1)]));
  await loading;
  assert.equal(q(env, "#board-sort").value, "hot");
  assert.equal(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "solved").getAttribute("aria-pressed"), "true");

  click(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "mine"));
  await tick();
  await answer(env, 1, page([post(2)]));
  assert.deepEqual(plain(JSON.parse(store.get("forum-board:v1"))), { tab: "mine", sort: "hot", zone: "前端" });
  q(env, "#forum-search").value = "不要保存我";
  q(env, "#forum-search-form").dispatchEvent(new FakeEvent("submit"));
  await tick();
  await answer(env, 2, page([post(3)]));
  assert.equal([...store.values()].some((value) => value.includes("不要保存我")), false);
});

test("preferences: a storage that throws on every call never breaks loading or filtering", async () => {
  const storage = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); }, removeItem() { throw new Error("denied"); } };
  const { env, Board } = setup({ storage });
  const loading = Board.show();
  await tick();
  assert.equal(env.calls[0].url, "/api/posts");
  env.respond(env.calls[0], 200, page([post(1)]));
  await loading;
  click(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "solved"));
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/posts?filter=solved");
  await answer(env, 1, page([post(2)]));
  assert.equal(rows(env).length, 1);
});

test("trial accounts: no 我的 tab, no post button, no template card", async () => {
  const { env, Board } = setup({ user: { id: 9, username: "体验", timezone: "UTC", is_trial: true } });
  const loading = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await loading;
  assert.equal(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "mine").hidden, true);
  assert.equal(q(env, "#forum-new-post-btn").hidden, true);
  assert.equal(q(env, "#board-ask").hidden, true);
  const regular = setup();
  const again = regular.Board.show();
  await tick();
  regular.env.respond(regular.env.calls[0], 200, page([post(1)]));
  await again;
  assert.equal(regular.env.document.querySelector("#forum-new-post-btn").hidden, false);
  assert.equal(regular.env.document.querySelector("#board-ask").hidden, false);
});

test("mounting twice only rebinds the hooks, so one click makes one request", async () => {
  const { env, Board, hooks } = setup();
  Board.mount(hooks);
  Board.mount(hooks);
  const first = Board.show();
  await tick();
  env.respond(env.calls[0], 200, page([post(1)]));
  await first;
  click(qa(env, "#board-tabs [data-board-tab]").find((button) => button.dataset.boardTab === "solved"));
  await tick();
  assert.equal(env.calls.length, 2);
  await answer(env, 1, page([post(2)]));
});

/* ───────────── 控制器：键盘 ───────────── */

async function loadedList(options) {
  const context = setup(options);
  const loading = context.Board.show();
  await tick();
  context.env.respond(context.env.calls[0], 200, page([post(1), post(2), post(3)]));
  await loading;
  return context;
}

test("keyboard: / focuses the search box, J and K walk the rows, N focuses the post button", async () => {
  const { env } = await loadedList();
  const slash = keydown(env, "/");
  assert.equal(slash.defaultPrevented, true);
  assert.equal(env.document.activeElement, q(env, "#forum-search"));

  keydown(env, "j");
  const buttons = qa(env, "#forum-posts .board-open");
  assert.equal(env.document.activeElement, buttons[0]);
  assert.ok(rows(env)[0].classList.contains("is-selected"));
  keydown(env, "j");
  keydown(env, "j");
  assert.equal(env.document.activeElement, buttons[2]);
  keydown(env, "j"); // 到底了不循环
  assert.equal(env.document.activeElement, buttons[2]);
  keydown(env, "k");
  assert.equal(env.document.activeElement, buttons[1]);
  assert.deepEqual(rows(env).map((row) => row.classList.contains("is-selected")), [false, true, false]);
  keydown(env, "K");
  keydown(env, "k");
  assert.equal(env.document.activeElement, buttons[0]);

  keydown(env, "n");
  assert.equal(env.document.activeElement, q(env, "#forum-new-post-btn"));
});

test("keyboard: tabbing onto a row's title selects that row", async () => {
  const { env } = await loadedList();
  const second = qa(env, "#forum-posts .board-open")[1];
  second.dispatchEvent(new FakeEvent("focusin", { bubbles: true }));
  assert.deepEqual(rows(env).map((row) => row.classList.contains("is-selected")), [false, true, false]);
  keydown(env, "j");
  assert.equal(env.document.activeElement, qa(env, "#forum-posts .board-open")[2]);
});

test("keyboard: nothing fires while typing, with modifiers, with a dialog or palette open, or when the list is hidden", async () => {
  const { env, state } = await loadedList();
  const blockedFocus = () => env.document.activeElement;
  const before = blockedFocus();
  const input = q(env, "#forum-search");
  assert.equal(keydown(env, "j", { target: input }).defaultPrevented, false);
  assert.equal(keydown(env, "/", { target: input }).defaultPrevented, false);
  assert.equal(keydown(env, "n", { target: q(env, "#forum-compose-body") }).defaultPrevented, false);
  for (const modifier of ["ctrlKey", "metaKey", "altKey"]) assert.equal(keydown(env, "j", { [modifier]: true }).defaultPrevented, false, modifier);
  assert.equal(keydown(env, "j", { isComposing: true }).defaultPrevented, false);

  const dialog = addNode(env, "dialog", "some-dialog");
  dialog.setAttribute("open", "");
  assert.equal(keydown(env, "j").defaultPrevented, false, "native dialog open");
  dialog.removeAttribute("open");
  dialog.remove();
  const modal = addNode(env, "div", "modal");
  modal.setAttribute("role", "dialog");
  assert.equal(keydown(env, "j").defaultPrevented, false, "role=dialog");
  modal.remove();

  env.window.CommandPalette = { isOpen: () => true };
  assert.equal(keydown(env, "j").defaultPrevented, false, "command palette open");
  env.window.CommandPalette = { isOpen: () => false };

  state.detailVisible = true;
  assert.equal(keydown(env, "j").defaultPrevented, false, "post detail visible");
  assert.equal(keydown(env, "/").defaultPrevented, false);
  state.detailVisible = false;
  state.listVisible = false;
  assert.equal(keydown(env, "j").defaultPrevented, false, "list hidden (compose or another page)");
  state.listVisible = true;
  state.view = "today";
  assert.equal(keydown(env, "j").defaultPrevented, false, "another view");
  state.view = "forum";
  const user = state.user;
  state.user = null;
  assert.equal(keydown(env, "j").defaultPrevented, false, "signed out");
  state.user = user;
  assert.ok(blockedFocus() === before, "focus never moved while blocked");
  // 不再被挡住时才生效；已经隐藏的对话框不算打开。
  const hidden = addNode(env, "div", "hidden-modal");
  hidden.setAttribute("role", "dialog");
  hidden.hidden = true;
  assert.equal(keydown(env, "j").defaultPrevented, true, "a hidden dialog does not block");
  assert.ok(blockedFocus() === qa(env, "#forum-posts .board-open")[0]);
});

test("keyboard: trial accounts cannot jump to the post button with N", async () => {
  const { env } = await loadedList({ user: { id: 9, username: "体验", timezone: "UTC", is_trial: true } });
  const event = keydown(env, "n");
  assert.equal(event.defaultPrevented, false);
  assert.notEqual(env.document.activeElement, q(env, "#forum-new-post-btn"));
});

/* ───────────── 控制器：发帖页 ───────────── */

function seedStorage(entries = {}) {
  const store = new Map(Object.entries(entries));
  const storage = {
    getItem: (key) => (store.has(key) ? store.get(key) : null),
    setItem: (key, value) => store.set(key, value),
    removeItem: (key) => store.delete(key),
  };
  return { store, storage };
}
const fill = (env, title, body) => {
  q(env, "#forum-compose-title-input").value = title;
  q(env, "#forum-compose-body").value = body;
  q(env, "#forum-compose-body").dispatchEvent(new FakeEvent("input", { bubbles: true }));
};
const typeBody = (env, body) => {
  q(env, "#forum-compose-body").value = body;
  q(env, "#forum-compose-body").dispatchEvent(new FakeEvent("input", { bubbles: true }));
};
const composeZoneButtons = (env) => qa(env, "#forum-compose-zones [data-compose-zone]");

test("compose: opens with eight zone chips (none selected), zeroed counters and an empty status", () => {
  const { env, Board } = setup();
  Board.openCompose({});
  const chips = composeZoneButtons(env);
  assert.deepEqual(plain(chips.map((chip) => chip.dataset.composeZone)), ["算法", "前端", "后端", "数据库", "系统设计", "高等数学", "线性代数", "概率统计"]);
  assert.ok(chips.every((chip) => chip.getAttribute("aria-pressed") === "false" && chip.dataset.zone === chip.dataset.composeZone));
  assert.equal(text(env, "#forum-compose-count"), "0 / 8000");
  assert.equal(text(env, "#forum-compose-title-count"), "0 / 200");
  assert.equal(q(env, "#forum-compose-draft").hidden, true);
  assert.equal(env.document.activeElement, q(env, "#forum-compose-title-input"));
});

test("compose: zone chips are single choice and a second click deselects", () => {
  const { env, Board } = setup();
  Board.openCompose({});
  const chip = (zone) => composeZoneButtons(env).find((item) => item.dataset.composeZone === zone);
  click(chip("前端"));
  assert.deepEqual(composeZoneButtons(env).map((item) => item.getAttribute("aria-pressed")), ["false", "true", "false", "false", "false", "false", "false", "false"]);
  click(chip("数据库"));
  assert.deepEqual(composeZoneButtons(env).filter((item) => item.getAttribute("aria-pressed") === "true").map((item) => item.dataset.composeZone), ["数据库"]);
  click(chip("数据库"));
  assert.ok(composeZoneButtons(env).every((item) => item.getAttribute("aria-pressed") === "false"));
});

test("compose: counters follow typing", () => {
  const { env, Board } = setup();
  Board.openCompose({});
  fill(env, "标题", "正文😀");
  assert.equal(text(env, "#forum-compose-count"), `${"正文😀".length} / 8000`);
  assert.equal(text(env, "#forum-compose-title-count"), "2 / 200");
});

test("compose drafts: typing is saved 800ms after the last keystroke under the user's own key", () => {
  const { store, storage } = seedStorage();
  const { env, Board } = setup({ storage });
  Board.openCompose({});
  const timers = fakeTimers(env);
  fill(env, "草稿标题", "草稿正文");
  fill(env, "草稿标题", "草稿正文更长");
  assert.equal(timers.queue.length, 1, "debounced");
  assert.deepEqual(plain(timers.delays()), [800]);
  assert.equal(store.has("forum-draft:v1:1"), false, "not saved before the delay");
  click(composeZoneButtons(env)[1]);
  assert.equal(timers.queue.length, 1);
  timers.fire();
  assert.deepEqual(plain(JSON.parse(store.get("forum-draft:v1:1"))), { userId: 1, title: "草稿标题", body: "草稿正文更长", zone: "前端" });
  fill(env, "", "");
  timers.fire();
  assert.equal(store.has("forum-draft:v1:1"), false, "emptying the form removes the draft");
});

test("compose drafts: reopening restores the draft with a notice, and 清除 deletes it and empties the form", () => {
  const { store, storage } = seedStorage({
    "forum-draft:v1:1": JSON.stringify({ userId: 1, title: "上次的标题", body: "上次的正文", zone: "概率统计" }),
  });
  const { env, Board } = setup({ storage });
  Board.openCompose({});
  assert.equal(q(env, "#forum-compose-title-input").value, "上次的标题");
  assert.equal(q(env, "#forum-compose-body").value, "上次的正文");
  assert.equal(q(env, "#forum-compose-draft").hidden, false);
  assert.equal(composeZoneButtons(env).find((item) => item.dataset.composeZone === "概率统计").getAttribute("aria-pressed"), "true");
  assert.equal(text(env, "#forum-compose-count"), `${"上次的正文".length} / 8000`);
  click(q(env, "#forum-compose-draft-clear"));
  assert.equal(store.has("forum-draft:v1:1"), false);
  assert.equal(q(env, "#forum-compose-draft").hidden, true);
  assert.equal(q(env, "#forum-compose-title-input").value, "");
  assert.equal(q(env, "#forum-compose-body").value, "");
  assert.ok(composeZoneButtons(env).every((item) => item.getAttribute("aria-pressed") === "false"));
  Board.openCompose({});
  assert.equal(q(env, "#forum-compose-draft").hidden, true, "nothing to restore any more");
});

test("compose drafts: cancelling and reopening with the saved text shows the draft notice again; unsaved edits do not", () => {
  const { storage } = seedStorage();
  const { env, Board } = setup({ storage });
  Board.openCompose({});
  const timers = fakeTimers(env);
  fill(env, "写到一半", "还没写完");
  timers.fire();
  Board.openCompose({}); // 取消后再次打开：表单里还是这份草稿
  assert.equal(q(env, "#forum-compose-draft").hidden, false);
  assert.equal(q(env, "#forum-compose-title-input").value, "写到一半");
  fill(env, "写到一半", "又多写了几个字（还没来得及保存）");
  Board.openCompose({});
  assert.equal(q(env, "#forum-compose-draft").hidden, true, "the form no longer matches the saved draft");
});

test("compose drafts: another user's draft is never restored, even if the key was copied", () => {
  const { storage } = seedStorage({
    "forum-draft:v1:2": JSON.stringify({ userId: 2, title: "别人的", body: "别人的正文", zone: "" }),
    "forum-draft:v1:1": JSON.stringify({ userId: 2, title: "被篡改的", body: "键是我、内容是别人", zone: "" }),
  });
  const { env, Board } = setup({ storage });
  Board.openCompose({});
  assert.equal(q(env, "#forum-compose-title-input").value, "");
  assert.equal(q(env, "#forum-compose-body").value, "");
  assert.equal(q(env, "#forum-compose-draft").hidden, true);
});

test("compose drafts: signing out flushes the pending draft to the old user's key and wipes the form for the next account", () => {
  const { store, storage } = seedStorage();
  const { env, Board, state } = setup({ storage });
  Board.openCompose({});
  const timers = fakeTimers(env);
  fill(env, "没发出去的标题", "没发出去的正文");
  assert.equal(timers.queue.length, 1);
  Board.reset();
  assert.equal(timers.queue.length, 0, "the pending timer is cancelled");
  assert.equal(JSON.parse(store.get("forum-draft:v1:1")).title, "没发出去的标题");
  assert.equal(q(env, "#forum-compose-title-input").value, "");
  assert.equal(q(env, "#forum-compose-body").value, "");
  state.epoch += 1;
  state.user = { id: 2, username: "别人", timezone: "UTC", is_trial: false };
  Board.openCompose({});
  assert.equal(q(env, "#forum-compose-title-input").value, "", "the next account does not see it");
  assert.equal(q(env, "#forum-compose-draft").hidden, true);
  state.user = { id: 1, username: "我", timezone: "UTC", is_trial: false };
  Board.openCompose({});
  assert.equal(q(env, "#forum-compose-title-input").value, "没发出去的标题", "the owner gets it back");
});

test("compose drafts: a storage that throws never breaks typing, opening or sending", async () => {
  const storage = { getItem() { throw new Error("denied"); }, setItem() { throw new Error("quota"); }, removeItem() { throw new Error("denied"); } };
  const { env, Board, state } = setup({ storage });
  Board.openCompose({});
  const timers = fakeTimers(env);
  fill(env, "标题", "正文");
  timers.fire();
  void Board.submitCompose();
  await tick();
  env.respond(env.calls[0], 201, { id: 5, title: "标题" });
  await tick();
  assert.deepEqual(plain(state.published), [{ id: 5, title: "标题" }]);
});

test("compose template: an empty body is filled directly; a non-empty body asks first and appends", () => {
  const { env, Board, state } = setup();
  Board.openCompose({});
  click(q(env, "#forum-compose-template"));
  assert.equal(q(env, "#forum-compose-body").value, Board.TEMPLATE);
  assert.deepEqual(plain(state.confirms), []);
  assert.equal(text(env, "#forum-compose-count"), `${Board.TEMPLATE.length} / 8000`);

  typeBody(env, "我先写了一点");
  state.confirmAnswer = false;
  click(q(env, "#forum-compose-template"));
  assert.equal(q(env, "#forum-compose-body").value, "我先写了一点", "declining changes nothing");
  assert.equal(state.confirms.length, 1);
  state.confirmAnswer = true;
  click(q(env, "#forum-compose-template"));
  assert.equal(q(env, "#forum-compose-body").value, `我先写了一点\n\n${Board.TEMPLATE}`);
  assert.equal(q(env, "#forum-compose-body").selectionStart, q(env, "#forum-compose-body").value.length);
});

test("compose template: openCompose({ template: true }) applies it on arrival, and an overflowing body is refused", () => {
  const { env, Board } = setup();
  Board.openCompose({ template: true });
  assert.equal(q(env, "#forum-compose-body").value, Board.TEMPLATE);
  typeBody(env, "x".repeat(8000));
  click(q(env, "#forum-compose-template"));
  assert.equal(q(env, "#forum-compose-body").value, "x".repeat(8000));
  assert.match(text(env, "#forum-compose-status"), /放不下模板/);
});

test("compose code button: inserts at the caret, wraps a selection, and puts the caret in the middle", () => {
  const { env, Board } = setup();
  Board.openCompose({});
  const body = q(env, "#forum-compose-body");
  body.value = "前文后文";
  body.setSelectionRange(2, 2);
  click(q(env, "#forum-compose-code"));
  assert.equal(body.value, "前文\n```\n\n```\n后文");
  assert.equal(body.selectionStart, 7);
  assert.equal(body.selectionEnd, 7);
  assert.equal(env.document.activeElement, body);

  body.value = "int x = 1;";
  body.setSelectionRange(0, 10);
  click(q(env, "#forum-compose-code"));
  assert.equal(body.value, "```\nint x = 1;\n```");
  assert.equal(body.value.slice(body.selectionStart, body.selectionEnd), "int x = 1;");

  body.value = "";
  body.selectionStart = body.selectionEnd = undefined; // 没有光标信息时落在末尾
  click(q(env, "#forum-compose-code"));
  assert.equal(body.value, "```\n\n```");
  assert.equal(text(env, "#forum-compose-count"), "8 / 8000");
  body.value = "y".repeat(7999);
  body.setSelectionRange(7999, 7999);
  click(q(env, "#forum-compose-code"));
  assert.equal(body.value, "y".repeat(7999), "refused when it would overflow");
  assert.match(text(env, "#forum-compose-status"), /放不下代码块/);
});

test("compose preview: uses the shared renderer, falls back to plain text without it, and switches back", () => {
  const withRenderer = setup();
  withRenderer.Board.openCompose({});
  typeBody(withRenderer.env, "第一行\n第二行");
  click(q(withRenderer.env, "#forum-compose-preview-tab"));
  assert.equal(q(withRenderer.env, "#forum-compose-body").hidden, true);
  assert.equal(q(withRenderer.env, "#forum-compose-preview").hidden, false);
  assert.equal(q(withRenderer.env, "#forum-compose-preview").querySelector(".rendered").textContent, "渲染:第一行\n第二行");
  assert.equal(q(withRenderer.env, "#forum-compose-preview-tab").getAttribute("aria-pressed"), "true");
  typeBody(withRenderer.env, "改了");
  assert.equal(q(withRenderer.env, "#forum-compose-preview").textContent, "渲染:改了", "preview follows edits");
  click(q(withRenderer.env, "#forum-compose-edit-tab"));
  assert.equal(q(withRenderer.env, "#forum-compose-body").hidden, false);
  assert.equal(q(withRenderer.env, "#forum-compose-preview").hidden, true);

  const plainText = setup({ noRenderBody: true });
  plainText.Board.openCompose({});
  typeBody(plainText.env, "纯文本\n<b>不是标签</b>");
  click(q(plainText.env, "#forum-compose-preview-tab"));
  const preview = q(plainText.env, "#forum-compose-preview");
  assert.equal(preview.textContent, "纯文本\n<b>不是标签</b>");
  assert.equal(preview.querySelectorAll("b").length, 0);
});

test("compose submit: the payload carries title, body and zone; the zone is omitted when none is chosen", async () => {
  const { env, Board, state } = setup();
  Board.openCompose({});
  fill(env, "  我的标题  ", "  正文内容  ");
  void Board.submitCompose();
  await tick();
  assert.equal(env.calls[0].url, "/api/posts");
  assert.equal(env.calls[0].init.method, "POST");
  assert.deepEqual(plain(JSON.parse(env.calls[0].init.body)), { title: "我的标题", body: "正文内容" });
  env.respond(env.calls[0], 201, { id: 11, title: "我的标题" });
  await tick();
  assert.deepEqual(plain(state.published), [{ id: 11, title: "我的标题" }]);

  Board.openCompose({});
  fill(env, "有分区", "正文");
  click(composeZoneButtons(env).find((chip) => chip.dataset.composeZone === "系统设计"));
  void Board.submitCompose();
  await tick();
  assert.deepEqual(plain(JSON.parse(env.calls[1].init.body)), { title: "有分区", body: "正文", zone: "系统设计" });
  env.respond(env.calls[1], 201, { id: 12, title: "有分区" });
  await tick();
});

test("compose submit: repeated clicks and Ctrl/⌘+Enter send exactly one request and disable the button", async () => {
  const { env, Board, state } = setup();
  Board.openCompose({});
  fill(env, "标题", "正文");
  const form = q(env, "#forum-compose-form");
  form.dispatchEvent(new FakeEvent("submit"));
  form.dispatchEvent(new FakeEvent("submit"));
  form.dispatchEvent(new FakeEvent("keydown", { props: { key: "Enter", ctrlKey: true } }));
  form.dispatchEvent(new FakeEvent("keydown", { props: { key: "Enter", metaKey: true } }));
  void Board.submitCompose();
  await tick();
  assert.equal(env.calls.length, 1);
  assert.equal(q(env, "#forum-compose-send").disabled, true);
  assert.equal(text(env, "#forum-compose-status"), "正在发布…");
  env.respond(env.calls[0], 201, { id: 3 });
  await tick();
  assert.equal(q(env, "#forum-compose-send").disabled, false);
  assert.equal(state.published.length, 1);
  assert.equal(env.calls.length, 1);
  // 单独的 Enter（没有修饰键）不提交
  Board.openCompose({});
  fill(env, "再来", "一个");
  form.dispatchEvent(new FakeEvent("keydown", { props: { key: "Enter" } }));
  await tick();
  assert.equal(env.calls.length, 1);
});

test("compose submit: blank title or body is refused before any request", async () => {
  const { env, Board } = setup();
  Board.openCompose({});
  fill(env, "   ", "正文");
  await Board.submitCompose();
  assert.equal(text(env, "#forum-compose-status"), "请先写一个标题。");
  fill(env, "标题", " \n ");
  await Board.submitCompose();
  assert.equal(text(env, "#forum-compose-status"), "请先写下正文。");
  assert.equal(env.calls.length, 0);
  assert.equal(q(env, "#forum-compose-send").disabled, false);
});

test("compose submit: a server error shows its detail under the form, keeps the text and allows trying again", async () => {
  const { env, Board, state } = setup();
  Board.openCompose({});
  fill(env, "标题", "正文");
  click(composeZoneButtons(env)[0]);
  void Board.submitCompose();
  await tick();
  env.respond(env.calls[0], 400, { detail: "分区不存在" });
  await tick();
  assert.equal(text(env, "#forum-compose-status"), "分区不存在");
  assert.equal(q(env, "#forum-compose-status").classList.contains("is-error"), true);
  assert.equal(q(env, "#forum-compose-send").disabled, false);
  assert.equal(q(env, "#forum-compose-body").value, "正文");
  assert.equal(state.published.length, 0);
  void Board.submitCompose();
  await tick();
  assert.equal(env.calls.length, 2);
  env.respond(env.calls[1], 201, { id: 1 });
  await tick();
  assert.equal(state.published.length, 1);
});

test("compose submit: success deletes the draft, clears the form and zone, and hands off to afterPublish", async () => {
  const { store, storage } = seedStorage({ "forum-draft:v1:1": JSON.stringify({ userId: 1, title: "旧", body: "旧", zone: "" }) });
  const { env, Board, state } = setup({ storage });
  Board.openCompose({});
  assert.equal(q(env, "#forum-compose-draft").hidden, false);
  const timers = fakeTimers(env);
  click(composeZoneButtons(env)[2]);
  assert.equal(timers.queue.length, 1);
  void Board.submitCompose();
  await tick();
  env.respond(env.calls[0], 201, { id: 21 });
  await tick();
  assert.equal(store.has("forum-draft:v1:1"), false);
  assert.equal(q(env, "#forum-compose-title-input").value, "");
  assert.equal(q(env, "#forum-compose-body").value, "");
  assert.ok(composeZoneButtons(env).every((chip) => chip.getAttribute("aria-pressed") === "false"));
  assert.equal(q(env, "#forum-compose-draft").hidden, true);
  assert.deepEqual(plain(state.published), [{ id: 21 }]);
  assert.equal(timers.queue.length, 0, "no draft timer left to resurrect the draft");
});

test("compose submit: a response after sign-out (or a different account) neither navigates nor touches the new session", async () => {
  const { store, storage } = seedStorage();
  const { env, Board, state } = setup({ storage });
  Board.openCompose({});
  fill(env, "标题", "正文");
  void Board.submitCompose();
  await tick();
  Board.reset();
  state.epoch += 1;
  state.user = { id: 2, username: "别人", timezone: "UTC", is_trial: false };
  env.respond(env.calls[0], 201, { id: 30 });
  await tick();
  assert.equal(state.published.length, 0);
  assert.equal(q(env, "#forum-compose-send").disabled, false);

  // 失败的迟到响应也不能把错误写进新用户的界面
  state.epoch -= 1;
  state.user = { id: 1, username: "我", timezone: "UTC", is_trial: false };
  Board.openCompose({});
  fill(env, "第二次", "正文");
  void Board.submitCompose();
  await tick();
  state.epoch += 1;
  state.user = { id: 2, username: "别人", timezone: "UTC", is_trial: false };
  env.respond(env.calls[1], 500, { detail: "旧账号的错误" });
  await tick();
  assert.notEqual(text(env, "#forum-compose-status"), "旧账号的错误");
  assert.equal(store.has("forum-draft:v1:2"), false);
});

test("ask card: 好问题模板 opens the compose page with the template requested", () => {
  const { env, state } = setup();
  click(q(env, "#board-ask-apply"));
  assert.deepEqual(plain(state.composed), [{ template: true }]);
});
