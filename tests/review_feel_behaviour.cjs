"use strict";

/* RVF integration: run the production app detail/list functions in the repository's
   fake browser, replacing only API I/O and unrelated editors/AI widgets. */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");
const SOURCE = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");

function topFunction(name, source = SOURCE) {
  const match = source.match(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
  assert.ok(match, `production function ${name} must exist`);
  const end = source.indexOf("\n}", match.index);
  assert.ok(end > match.index, `production function ${name} must end`);
  return source.slice(match.index, end + 2);
}

const CARD = (id = 1, changes = {}) => ({
  id, problem_id: id, title: `题目${id}`, zone: "算法", language: "Python",
  description: "边界容易漏掉", thinking: "原始思路", code: "pass", tags: ["边界"],
  interval_days: 2, repetitions: 1, ease_factor: 2.5, version: 3,
  today: "2026-10-04", due_date: "2026-10-04", last_reviewed_at: null,
  reviews: [], variants: [], suspended_at: null, ...changes,
});

function seedList(env, items = [CARD(), CARD(2)]) {
  env.context.user.today = "2026-10-04";
  env.context.rvfListItems = new Map(items.map((item) => [item.id, item]));
  env.$("#cards").replaceChildren(...items.map((item) => env.context.rvfRecordButton(item, "2026-10-04")));
}
async function enableMenu(env) {
  const menu = env.$("#detail").querySelector(".review-more");
  assert.ok(menu);
  for (const call of env.calls.filter((request) => request.options.method === "GET" && !request.answered)) {
    env.fail(call, 405, "Method Not Allowed");
  }
  await menu.ready;
  return menu;
}
async function supportUndo(env) {
  const probe = env.calls.find((call) => call.url.endsWith("/review/undo") && call.options.method === "GET" && !call.answered);
  assert.ok(probe, "undo support must be read without a write");
  env.fail(probe, 405, "Method Not Allowed");
  await tick();
}

function environment(source = SOURCE) {
  const extrasPath = path.join(__dirname, "../static/review-extras.js");
  const env = load(fs.existsSync(extrasPath) ? ["review-extras.js"] : []);
  const { document, context } = env;
  const calls = [], messages = [], notifications = [];
  const $ = (selector) => document.querySelector(selector);
  for (const id of ["detail", "cards", "list-page", "list-focus", "list-title", "list-summary",
    "list-filter", "list-filter-text", "zone-filter", "tag-filter", "review-cap-controls",
    "review-daily-cap", "review-done-today", "review-cap-note", "review-queue-status"]) $("#" + id);
  const cap = document.createElement("select");
  cap.id = "review-daily-cap";
  for (const value of ["", "10", "20", "30", "50", "100"]) {
    const option = document.createElement("option"); option.value = value; cap.append(option);
  }
  const label = document.createElement("label"); label.append(cap);
  $("#review-daily-cap").replaceWith(label);
  Object.assign(context, {
    $, user: { id: 1, ai_enabled: false, ai_daily_limit: 10 }, view: "today", sessionEpoch: 1,
    busy: false, codeZones: new Set(["算法"]), listCreatedOn: "",
    rvfDetailState: null, rvfDetailGeneration: 0, rvfListGeneration: 0, rvfPageGeneration: 0,
    rvfHeaderGeneration: 0, rvfListItems: new Map(), rvfQueue: null,
    localStorage: env.window.localStorage,
    timestamp: (value) => value || "尚未复习", confirm: () => false,
    message: (text, error) => messages.push({ text, error }),
    notifyDataChanged: (reason) => notifications.push(reason), updateUserInfo() {},
    sealAnchorPoint() { return {}; }, stampSeal() {},
    run: (action) => Promise.resolve().then(action),
    api(url, options = {}) { const call = { url, options, ...deferred() }; calls.push(call); return call.promise; },
    renderMistakeText: (item) => context.element("pre", item.description, "prose"),
    renderProblemEditor: (item) => context.element("pre", item.thinking + item.code, "prose"),
  });
  env.window.ReviewExtras?.configure({
    api: context.api, getEpoch: () => context.sessionEpoch, getUser: () => context.user,
    getView: () => context.view,
  });
  vm.runInContext(source.slice(source.indexOf("const grades = ["), source.indexOf("\n];", source.indexOf("const grades = [")) + 3), context);
  for (const name of ["element", "field"]) vm.runInContext(topFunction(name, source), context);
  const names = ["clearDetail", "renderDetail", "loadList", "openMistake", "rvfRestoreItem"];
  for (const match of source.matchAll(/^(?:async )?function (rvf\w+)\(/gm)) names.push(match[1]);
  for (const name of [...new Set(names)]) {
    if (new RegExp(`^(?:async )?function ${name}\\(`, "m").test(source)) vm.runInContext(topFunction(name, source), context);
  }
  return { ...env, $, calls, messages, notifications,
    render(item = CARD()) { context.user.today = item.today; context.renderDetail(item); return $("#detail"); },
    answer(call, data) {
      call.answered = true; call.resolve(data);
      if (call.url.startsWith("/api/review/queue")) {
        for (const settings of calls.filter((request) => request.url === "/api/me/review-settings" && !request.options.method && !request.answered)) {
          settings.answered = true; settings.resolve({ daily_review_cap: data.cap ?? null });
        }
      }
    },
    fail(call, status, detail) {
      call.answered = true; call.reject(Object.assign(new Error(detail), { status }));
      if (call.url.startsWith("/api/review/queue")) {
        for (const settings of calls.filter((request) => request.url === "/api/me/review-settings" && !request.options.method && !request.answered)) {
          settings.answered = true; settings.resolve({ daily_review_cap: null });
        }
      }
    },
    key(key, target = document.body, props = {}) {
      const event = new FakeEvent("keydown", { props: { key, target, ...props } });
      context.rvfDetailKeydown(event); return event;
    },
  };
}

function navigationEnvironment() {
  const env = environment();
  Object.assign(env.context, {
    finishHomeOpening: null, sealStamps: [], planPurchase: null, planCatalog: [], sessionReady: true,
    resetToken: null, forumPost: null, forumDetailGeneration: 0, forumSummaryController: null,
    forumCodeOnly: false, forumMentionOnly: false, forumCurrentComment: null, forumMutations: new Map(),
    forumCommentOrder: "earliest", forumOnlyOp: false, forumListGeneration: 0, groupsGeneration: 0,
    achievementsGeneration: 0,
  });
  for (const name of ["stopOrderPolling", "resetWeaknessAnalysis", "resetAchievements", "resetWeeklyRecap",
    "resetGroups", "renderPageRoute", "closeAccountMenu", "showAuthPanels", "setForumPreview",
    "clearForumReply", "secClearEmailForm", "resetAdminDashboard", "addMistakeInput", "resetPhotoForm", "resetHomeSummary",
    "resetQuickMode",
    "cancelAchievementStamps", "renderUserInfo", "renderHomeQuota", "loadHome"]) env.context[name] = () => {};
  for (const id of ["forum-comment-form", "problem-form"]) env.$("#" + id).reset = () => {};
  env.window.scrollTo = () => {};
  for (const name of ["signedOut", "showView"]) vm.runInContext(topFunction(name), env.context);
  return env;
}

test("detail reveal: starts hidden and reveals the answer only after the large button is activated", () => {
  const env = environment();
  const root = env.render();
  const reveal = root.querySelector("#review-reveal");
  assert.ok(reveal, "the recall-first detail must expose a reveal control");
  assert.equal(root.querySelector(".review-section").hidden, true);
  reveal.click();
  assert.equal(root.querySelector(".review-section").hidden, false);
  assert.equal(env.document.activeElement, root.querySelector('[data-quality="0"]'));
});

test("detail reveal: source path and tags stay visible while reason and original are covered", () => {
  const env = environment();
  const root = env.render();
  assert.match(root.querySelector(".review-source").textContent, /算法.*题目1.*边界/);
  assert.equal(root.querySelector("#review-reason").hidden, true);
  assert.equal(root.querySelector("#review-original").hidden, true);
  root.querySelector("#review-reveal").click();
  assert.equal(root.querySelector("#review-reason").hidden, false);
  assert.equal(root.querySelector("#review-original").hidden, false);
});

test("detail reveal: recall is retained for comparison and never put in an API payload", () => {
  const env = environment();
  const root = env.render();
  const recall = root.querySelector("#review-recall");
  assert.ok(recall);
  recall.value = "我猜是边界条件";
  root.querySelector("#review-reveal").click();
  assert.equal(root.querySelector("#review-recall"), recall);
  assert.equal(recall.value, "我猜是边界条件");
  assert.ok(env.calls.every((call) => !(call.options.body || "").includes(recall.value)));
});

test("detail preference: default is on and persisted off restores the old visible-detail flow", () => {
  const env = environment();
  const first = env.render();
  const toggle = first.querySelector("#review-hide-reason");
  assert.equal(toggle.checked, true);
  toggle.checked = false;
  toggle.dispatchEvent(new FakeEvent("change"));
  assert.equal(env.window.localStorage.getItem("review-hide-reason"), "false");
  const second = env.render(CARD(2));
  assert.equal(second.querySelector("#review-hide-reason").checked, false);
  assert.equal(second.querySelector(".review-section").hidden, false);
  assert.equal(second.querySelector("#review-reason").hidden, false);
});

test("detail preference: denied localStorage is tolerated with recall-first default", () => {
  const env = environment();
  env.window.localStorage.getItem = () => { throw new Error("denied"); };
  env.window.localStorage.setItem = () => { throw new Error("denied"); };
  assert.doesNotThrow(() => env.render());
  assert.equal(env.$(".review-section").hidden, true);
  const toggle = env.$("#review-hide-reason");
  toggle.checked = false;
  assert.doesNotThrow(() => toggle.dispatchEvent(new FakeEvent("change")));
});

test("detail keyboard: Space reveals once and leaves focus on the first score button", () => {
  const env = environment();
  env.render();
  const event = env.key(" ");
  assert.equal(event.defaultPrevented, true);
  assert.equal(env.$(".review-section").hidden, false);
  const first = env.$('[data-quality="0"]');
  assert.equal(env.document.activeElement, first);
  const repeat = env.key(" ");
  assert.equal(repeat.defaultPrevented, false);
  assert.equal(env.document.activeElement, first);
});

test("detail keyboard: Space never steals typing or native button/link activation", () => {
  for (const tag of ["input", "textarea", "select", "button", "a"]) {
    const env = environment();
    env.render();
    const target = env.document.createElement(tag);
    env.document.body.append(target);
    target.focus();
    const event = env.key(" ", target);
    assert.equal(event.defaultPrevented, false, tag);
    assert.equal(env.$(".review-section").hidden, true, tag);
  }
});

test("detail keyboard: modifiers, IME and modal dialogs protect the hidden answer", () => {
  for (const props of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { shiftKey: true }, { isComposing: true }, { repeat: true }]) {
    const env = environment();
    env.render();
    assert.equal(env.key(" ", env.document.body, props).defaultPrevented, false);
    assert.equal(env.$(".review-section").hidden, true);
  }
  const env = environment();
  env.render();
  const dialog = env.document.createElement("dialog");
  env.document.body.append(dialog);
  dialog.showModal();
  assert.equal(env.key(" ").defaultPrevented, false);
  assert.equal(env.$(".review-section").hidden, true);
});

test("detail scoring: five labels map to scheduler qualities and scores use api without global run", async () => {
  const env = environment();
  let runs = 0;
  env.context.run = () => { runs += 1; throw new Error("review must not use run"); };
  env.render();
  env.$("#review-reveal").click();
  const buttons = env.$("#review-grade-row").querySelectorAll("button");
  assert.deepEqual(buttons.map((button) => button.dataset.quality), ["0", "2", "3", "4", "5"]);
  assert.match(buttons[0].textContent, /完全忘了/);
  assert.match(buttons[1].textContent, /答错了/);
  assert.match(buttons[2].textContent, /很吃力/);
  buttons[3].click();
  await tick();
  const review = env.calls.find((call) => call.url === "/api/mistakes/1/review");
  assert.ok(review);
  assert.deepEqual(JSON.parse(review.options.body), { quality: 4, version: 3 });
  assert.equal(runs, 0);
});

test("detail keyboard: unrevealed, future and suspended cards cannot be graded", async () => {
  for (const card of [CARD(), CARD(1, { due_date: "2026-10-09" }), CARD(1, { suspended_at: "2026-10-01" })]) {
    const env = environment();
    env.render(card);
    if (card.due_date !== card.today || card.suspended_at) env.$("#review-reveal")?.click();
    const before = env.calls.length;
    env.key("4");
    await tick();
    assert.equal(env.calls.length, before);
  }
});

test("detail keyboard: digits score after reveal and respect editable/modifier/menu gates", async () => {
  const env = environment();
  env.render(); env.$("#review-reveal").click();
  const input = env.$("#review-recall");
  for (const [target, props] of [[input, {}], [env.document.body, { ctrlKey: true }], [env.document.body, { metaKey: true }]]) {
    env.key("4", target, props);
  }
  assert.equal(env.calls.filter((call) => call.url.endsWith("/review")).length, 0);
  assert.equal(env.key("2").defaultPrevented, true);
  await tick();
  assert.deepEqual(JSON.parse(env.calls.find((call) => call.url.endsWith("/review")).options.body), { quality: 2, version: 3 });
});

test("detail opening: a later selection wins even when the old card responds last", async () => {
  const env = environment();
  const first = env.context.openMistake(1);
  const second = env.context.openMistake(2);
  env.answer(env.calls.find((call) => call.url === "/api/mistakes/2"), CARD(2));
  await second;
  env.answer(env.calls.find((call) => call.url === "/api/mistakes/1"), CARD(1));
  await first;
  assert.match(env.$(".detail-heading").textContent, /题目2/);
  assert.doesNotMatch(env.$(".detail-heading").textContent, /题目1/);
});

test("detail opening: old account and page responses never repaint a cleared panel", async () => {
  for (const leave of [
    (env) => { env.context.sessionEpoch += 1; env.context.user = { id: 2 }; },
    (env) => { env.context.view = "home"; env.context.rvfPageGeneration += 1; },
  ]) {
    const env = environment();
    const pending = env.context.openMistake(1);
    leave(env);
    env.context.clearDetail("已经离开");
    env.answer(env.calls[0], CARD(1));
    await pending;
    assert.match(env.$("#detail").textContent, /已经离开/);
    assert.equal(env.$("#detail").querySelector(".detail-heading"), null);
  }
});

test("detail review: duplicate activation submits once and a 409 is spoken locally", async () => {
  const env = environment();
  env.render(); env.$("#review-reveal").click();
  env.$('[data-quality="4"]').click(); env.$('[data-quality="4"]').click();
  await tick();
  const requests = env.calls.filter((call) => call.url.endsWith("/review"));
  assert.equal(requests.length, 1);
  env.fail(requests[0], 409, "这条记录刚刚已被更新，请刷新后重试");
  await tick();
  assert.match(env.$("#review-detail-status").textContent, /这条记录刚刚已被更新，请刷新后重试/);
  assert.equal(env.$('[data-quality="4"]').disabled, false);
});

test("detail review: a result from a departed page cannot remove a new card or show its toast", async () => {
  const env = environment();
  env.render(); env.$("#review-reveal").click();
  env.$('[data-quality="4"]').click(); await tick();
  const pending = env.calls.find((call) => call.url.endsWith("/review"));
  env.context.rvfPageGeneration += 1;
  env.context.view = "all";
  env.render(CARD(2));
  env.answer(pending, { version: 4, interval_days: 6, due_date: "2026-10-10" });
  await tick();
  assert.match(env.$(".detail-heading").textContent, /题目2/);
  assert.equal(env.document.documentElement.querySelector(".review-toast"), null);
});

test("queue header: cap and done_today come from queue metadata while filters are retained", async () => {
  const env = environment();
  const pending = env.context.rvfLoadQueueHeader(() => true, "数学", "边界");
  const call = env.calls[0];
  assert.ok(call.url.startsWith("/api/review/queue?"));
  const query = new URLSearchParams(call.url.split("?")[1]);
  assert.equal(query.get("zone"), "数学");
  assert.equal(query.get("tag"), "边界");
  env.answer(call, { today: "2026-10-04", cap: 20, done_today: 12, remaining_today: 8, total_due: 23, capped: true, items: [CARD()] });
  await pending;
  assert.equal(env.$("#review-cap-controls").hidden, false);
  assert.equal(env.$("#review-daily-cap").value, "20");
  assert.match(env.$("#review-done-today").textContent, /今天已复习 12 \/ 20/);
  assert.match(env.$("#review-cap-note").textContent, /今日队列上限 20 条，其余明天再说/);
  assert.equal(env.$("#review-cap-note").hidden, false);
});

test("queue header: unlimited has no capped note and missing API keeps old review available", async () => {
  const env = environment();
  const pending = env.context.rvfLoadQueueHeader(() => true, "", "");
  env.answer(env.calls[0], { cap: null, done_today: 12, remaining_today: null, total_due: 1, capped: false, items: [CARD()] });
  await pending;
  assert.equal(env.$("#review-daily-cap").value, "");
  assert.match(env.$("#review-done-today").textContent, /12/);
  assert.equal(env.$("#review-cap-note").hidden, true);
  const missing = env.context.rvfLoadQueueHeader(() => true, "", "");
  env.fail(env.calls.filter((call) => call.url.startsWith("/api/review/queue")).at(-1), 404, "Not Found");
  await missing;
  assert.equal(env.$("#review-cap-controls").hidden, true);
  assert.equal(env.$("#review-queue-status").textContent, "");
});

test("queue header: late metadata is ignored after filter/page generation becomes obsolete", async () => {
  const env = environment();
  env.$("#review-done-today").textContent = "当前用户";
  let current = true;
  const pending = env.context.rvfLoadQueueHeader(() => current, "", "");
  current = false;
  env.answer(env.calls[0], { cap: 20, done_today: 99, capped: true, items: [] });
  await pending;
  assert.equal(env.$("#review-done-today").textContent, "当前用户");
});

test("queue header: backend Chinese errors are announced verbatim", async () => {
  const env = environment();
  const pending = env.context.rvfLoadQueueHeader(() => true, "", "");
  env.fail(env.calls[0], 503, "现在无法读取复习队列，请稍后重试");
  await pending;
  assert.equal(env.$("#review-queue-status").textContent, "现在无法读取复习队列，请稍后重试");
});

test("daily cap: selection saves the numeric value using PUT and refreshes metadata", async () => {
  const env = environment();
  env.$("#review-daily-cap").value = "30";
  const pending = env.context.rvfSetDailyCap();
  const write = env.calls[0];
  assert.equal(write.url, "/api/me/review-settings");
  assert.equal(write.options.method, "PUT");
  assert.deepEqual(JSON.parse(write.options.body), { daily_review_cap: 30 });
  env.answer(write, { daily_review_cap: 30 });
  await tick();
  const queue = env.calls.find((call) => call.url.startsWith("/api/review/queue"));
  assert.ok(queue);
  env.answer(queue, { cap: 30, done_today: 12, total_due: 23, capped: false, items: [CARD()] });
  await pending;
  assert.match(env.$("#review-done-today").textContent, /12 \/ 30/);
});

test("daily cap: unlimited saves null, errors restore control availability and show detail", async () => {
  const env = environment();
  env.$("#review-daily-cap").value = "";
  const pending = env.context.rvfSetDailyCap();
  assert.deepEqual(JSON.parse(env.calls[0].options.body), { daily_review_cap: null });
  env.fail(env.calls[0], 422, "每日上限应为 5 到 200 条");
  await pending;
  assert.equal(env.$("#review-queue-status").textContent, "每日上限应为 5 到 200 条");
  assert.equal(env.$("#review-daily-cap").disabled, false);
});

test("daily cap: account changes during PUT never update the new account metadata", async () => {
  const env = environment();
  env.$("#review-daily-cap").value = "20";
  const pending = env.context.rvfSetDailyCap();
  env.context.sessionEpoch += 1;
  env.context.user = { id: 2 };
  env.$("#review-done-today").textContent = "新账号";
  env.answer(env.calls[0], { daily_review_cap: 20 });
  await pending;
  assert.equal(env.$("#review-done-today").textContent, "新账号");
  assert.equal(env.calls.length, 1);
});

test("daily cap: leaving and returning during PUT gives the new page an enabled control", async () => {
  const env = environment(); env.$("#review-daily-cap").value = "20";
  const oldWrite = env.context.rvfSetDailyCap();
  assert.equal(env.$("#review-daily-cap").disabled, true);
  env.context.rvfPageGeneration += 2; env.context.rvfHeaderGeneration += 2;
  const freshHeader = env.context.rvfLoadQueueHeader(() => true, "", "");
  assert.equal(env.$("#review-daily-cap").disabled, false);
  const queue = env.calls.find((call) => call.url.startsWith("/api/review/queue"));
  env.answer(queue, { cap: 30, done_today: 12, capped: false, items: [] }); await freshHeader;
  env.answer(env.calls[0], { daily_review_cap: 20 }); await oldWrite;
  assert.equal(env.$("#review-daily-cap").disabled, false);
  assert.match(env.$("#review-done-today").textContent, /12 \/ 30/);
});

test("list: suspended records retain their label in all-records", async () => {
  const env = environment();
  env.context.view = "all";
  const pending = env.context.loadList();
  const call = env.calls.find((request) => request.url.startsWith("/api/mistakes?"));
  env.answer(call, { today: "2026-10-04", items: [CARD(1, { suspended_at: "2026-10-01" }), CARD(2)] });
  await pending;
  assert.match(env.$('.record-button[data-id="1"]').textContent, /已暂停/);
  assert.equal(env.$("#cards").querySelectorAll(".record-button").length, 2);
});

test("list: today's left pane keeps every due record while queue metadata reports a smaller cap", async () => {
  const env = environment();
  const pending = env.context.loadList();
  const list = env.calls.find((call) => call.url.startsWith("/api/mistakes?"));
  assert.match(list.url, /due_only=true/);
  env.answer(list, { today: "2026-10-04", items: [CARD(1), CARD(2), CARD(3)] }); await pending;
  const queue = env.calls.find((call) => call.url.startsWith("/api/review/queue?"));
  env.answer(queue, { cap: 10, done_today: 9, remaining_today: 1, total_due: 3, capped: true, items: [CARD()] });
  await tick();
  assert.equal(env.$("#cards").querySelectorAll(".record-button").length, 3);
  assert.match(env.$("#list-summary").textContent, /有 3 条/);
  assert.match(env.$("#review-cap-note").textContent, /今日队列上限 10 条，其余明天再说/);
  assert.doesNotMatch(env.$("#cards").textContent, /边界容易漏掉/, "recall-first due cards do not reveal their cause in the left pane");
});

test("queue header: missing settings hides only the cap editor and preserves queue progress", async () => {
  const env = environment();
  const pending = env.context.rvfLoadQueueHeader(() => true, "", "");
  env.fail(env.calls.find((call) => call.url === "/api/me/review-settings"), 404, "Not Found");
  env.answer(env.calls.find((call) => call.url.startsWith("/api/review/queue?")), { cap: 20, done_today: 12, capped: false, items: [CARD()] });
  await pending;
  assert.equal(env.$("#review-daily-cap").closest("label").hidden, true);
  assert.equal(env.$("#review-cap-controls").hidden, false);
  assert.match(env.$("#review-done-today").textContent, /12 \/ 20/);
});

test("list: late list response after account/page change is discarded", async () => {
  const env = environment();
  const pending = env.context.loadList();
  env.context.sessionEpoch += 1;
  env.context.user = { id: 2 };
  env.$("#cards").textContent = "新账号的记录";
  const call = env.calls.find((request) => request.url.startsWith("/api/mistakes?"));
  env.answer(call, { today: "2026-10-04", items: [CARD()] });
  const queue = env.calls.find((request) => request.url.startsWith("/api/review/queue"));
  if (queue) env.answer(queue, { cap: null, done_today: 0, items: [] });
  await pending;
  assert.equal(env.$("#cards").textContent, "新账号的记录");
});

test("detail opening: logging back into the same account still discards the prior session response", async () => {
  const env = environment();
  const pending = env.context.openMistake(1);
  env.context.sessionEpoch += 1;
  env.context.user = { id: 1 };
  env.context.clearDetail("新的登录会话");
  env.answer(env.calls[0], CARD(1));
  await pending;
  assert.match(env.$("#detail").textContent, /新的登录会话/);
});

test("app navigation: the real showView invalidates active detail requests before page change", async () => {
  const env = navigationEnvironment(); env.render();
  const pending = env.context.openMistake(2);
  await env.context.showView("home", { refreshUser: false });
  assert.equal(env.context.rvfDetailState, null);
  env.answer(env.calls.find((call) => call.url === "/api/mistakes/2"), CARD(2)); await pending;
  assert.equal(env.$("#list-page").hidden, true);
  assert.doesNotMatch(env.$("#detail").textContent, /题目2/);
});

test("app logout: the real signedOut clears the detail, list ownership and latest undo", async () => {
  const env = navigationEnvironment(); seedList(env); env.render();
  env.window.ReviewExtras.rememberReview({ item: CARD(), result: { version: 4, interval_days: 6 }, quality: 4 });
  assert.ok(env.document.documentElement.querySelector(".review-toast"));
  const pending = env.context.openMistake(2);
  env.context.signedOut();
  assert.equal(env.context.user, null);
  assert.equal(env.context.rvfDetailState, null);
  assert.equal(env.context.rvfListItems.size, 0);
  assert.equal(env.document.documentElement.querySelector(".review-toast"), null);
  env.answer(env.calls.find((call) => call.url === "/api/mistakes/2"), CARD(2)); await pending;
  assert.equal(env.$("#detail").textContent, "");
});

test("detail preview: grade intervals match the saved result and undo restores the list head and detail", async () => {
  const env = environment(); seedList(env); env.render();
  const preview = env.calls.find((call) => call.url.endsWith("/preview"));
  env.answer(preview, { version: 3, previews: { 0: { interval_days: 1 }, 4: { interval_days: 6 } } });
  await tick();
  assert.equal(env.$('[data-quality="0"] .review-interval').textContent, "明天");
  assert.equal(env.$('[data-quality="4"] .review-interval').textContent, "6 天后");
  env.$("#review-reveal").click(); env.$('[data-quality="4"]').click(); await tick();
  env.answer(env.calls.find((call) => call.url.endsWith("/review")), { version: 4, interval_days: 6, due_date: "2026-10-10" });
  await tick();
  assert.deepEqual(env.$("#cards").querySelectorAll(".record-button").map((button) => button.dataset.id), ["2"]);
  assert.match(env.document.documentElement.querySelector(".review-toast").textContent, /已评分「记得」.*下次 6 天后/);
  await supportUndo(env);
  env.document.documentElement.querySelector(".review-toast-action").click(); await tick();
  const undo = env.calls.find((call) => call.url.endsWith("/review/undo") && call.options.method === "POST");
  assert.deepEqual(JSON.parse(undo.options.body), { version: 4 });
  env.answer(undo, { version: 5, due_date: "2026-10-04", suspended_at: null }); await tick();
  const detail = env.calls.find((call) => call.url === "/api/mistakes/1");
  env.answer(detail, CARD(1, { version: 5 })); await tick();
  assert.deepEqual(env.$("#cards").querySelectorAll(".record-button").map((button) => button.dataset.id), ["1", "2"]);
  assert.match(env.$(".detail-heading").textContent, /题目1/);
  assert.match(env.document.documentElement.querySelector(".review-toast").textContent, /已撤销/);
});

test("detail menu: unavailable routes hide the control and the original grading flow still works", async () => {
  const env = environment(); env.render();
  const menu = env.$("#detail").querySelector(".review-more");
  for (const call of env.calls.filter((request) => request.options.method === "GET")) env.fail(call, 404, "Not Found");
  await menu.ready;
  assert.equal(menu.hidden, true);
  env.$("#review-reveal").click(); env.$('[data-quality="4"]').click(); await tick();
  assert.ok(env.calls.some((call) => call.url.endsWith("/review") && call.options.method === "POST"));
});

test("detail menu: open menu suppresses grading; Escape closes it and restores trigger focus", async () => {
  const env = environment(); env.render(); env.$("#review-reveal").click();
  const menu = await enableMenu(env);
  const trigger = menu.querySelector(".review-more-button"); trigger.click();
  assert.equal(menu.querySelector('[role="menu"]').hidden, false);
  assert.equal(env.key("4").defaultPrevented, false);
  assert.equal(env.calls.filter((call) => call.url.endsWith("/review")).length, 0);
  const escape = new FakeEvent("keydown", { props: { key: "Escape", target: env.document.activeElement } });
  env.document.dispatchEvent(escape);
  assert.equal(menu.querySelector('[role="menu"]').hidden, true);
  assert.equal(env.document.activeElement, trigger);
});

test("detail menu: T snoozes tomorrow using current version and removes it from today's full due list", async () => {
  const env = environment(); seedList(env); env.render(); await enableMenu(env);
  assert.equal(env.key("t").defaultPrevented, true); await tick();
  const write = env.calls.find((call) => call.url.endsWith("/snooze") && call.options.method === "POST");
  assert.deepEqual(JSON.parse(write.options.body), { version: 3, days: 1 });
  env.answer(write, { version: 4, due_date: "2026-10-05" }); await tick();
  assert.deepEqual(env.$("#cards").querySelectorAll(".record-button").map((button) => button.dataset.id), ["2"]);
  const toast = env.document.documentElement.querySelector(".review-toast");
  assert.match(toast.textContent, /已推迟到明天/);
  assert.equal(toast.querySelector("button"), null, "snooze has no undo action");
});

test("detail menu: P suspends, restore uses the new version and returns the item to list head", async () => {
  const env = environment(); seedList(env); env.render(); await enableMenu(env);
  env.key("p"); await tick();
  const suspend = env.calls.find((call) => call.url.endsWith("/suspend") && call.options.method === "POST");
  env.answer(suspend, { version: 4, suspended_at: "2026-10-04" }); await tick();
  // 恢复按钮只在 unsuspend 路由的只读能力探测完成后出现。
  for (const call of env.calls.filter((request) => request.url.endsWith("/unsuspend") && request.options.method === "GET" && !request.answered)) env.fail(call, 405, "Method Not Allowed");
  await tick();
  assert.deepEqual(env.$("#cards").querySelectorAll(".record-button").map((button) => button.dataset.id), ["2"]);
  const toast = env.document.documentElement.querySelector(".review-toast");
  assert.match(toast.textContent, /已暂停.*恢复/);
  toast.querySelector("button").click(); await tick();
  const restore = env.calls.find((call) => call.url.endsWith("/unsuspend") && call.options.method === "POST");
  assert.deepEqual(JSON.parse(restore.options.body), { version: 4 });
  env.answer(restore, { version: 5, suspended_at: null, due_date: "2026-10-04" }); await tick();
  env.answer(env.calls.find((call) => call.url === "/api/mistakes/1"), CARD(1, { version: 5 })); await tick();
  assert.equal(env.$("#cards").querySelector(".record-button").dataset.id, "1");
  assert.match(env.$(".detail-heading").textContent, /题目1/);
});

test("detail menu: suspended all-records detail exposes Restore and updates its badge", async () => {
  const env = environment(); env.context.view = "all";
  const suspended = CARD(1, { suspended_at: "2026-10-04" });
  seedList(env, [suspended, CARD(2)]); env.render(suspended);
  const menu = await enableMenu(env);
  assert.match(menu.textContent, /恢复复习/);
  assert.equal(menu.querySelector(".review-more-button"), null);
  menu.querySelector("button").click(); await tick();
  const write = env.calls.find((call) => call.url.endsWith("/unsuspend") && call.options.method === "POST");
  env.answer(write, { version: 4, suspended_at: null }); await tick();
  env.answer(env.calls.find((call) => call.url === "/api/mistakes/1"), CARD(1, { version: 4 })); await tick();
  assert.doesNotMatch(env.$('.record-button[data-id="1"]').textContent, /已暂停/);
});

test("detail menu: a pending action after switching cards cannot remove or toast the new detail", async () => {
  const env = environment(); seedList(env); env.render(); await enableMenu(env);
  env.key("t"); await tick();
  const write = env.calls.find((call) => call.url.endsWith("/snooze") && call.options.method === "POST");
  env.render(CARD(2)); env.answer(write, { version: 4, due_date: "2026-10-05" }); await tick();
  assert.equal(env.$("#cards").querySelectorAll(".record-button").length, 2);
  assert.match(env.$(".detail-heading").textContent, /题目2/);
  assert.equal(env.document.documentElement.querySelector(".review-toast"), null);
});

test("pending reason: production detail opens the editor while ordinary records remain concealed", async () => {
  const env = environment();
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/pending-reason.js"), "utf8"), env.context);
  env.window.PendingReason.configure({
    api: env.context.api, getEpoch: () => env.context.sessionEpoch,
    getUser: () => env.context.user, getView: () => env.context.view,
    getDetailGeneration: () => env.context.rvfDetailGeneration,
  });
  vm.runInContext(topFunction("renderMistakeText"), env.context);
  env.render(CARD(1, { pending_reason: true, description: "（待补：为什么错）" }));
  const reason = env.$("#review-reason");
  assert.equal(reason.hidden, false);
  assert.ok(reason.querySelector(".pending-reason-form"));
  assert.equal(reason.querySelector("textarea").value, "");
  assert.doesNotMatch(reason.textContent, /（待补：为什么错）/);
  env.answer(env.calls.find((call) => call.url === "/api/tags/suggest"), { mine: [], builtin: ["边界"] });
  await tick();
  assert.ok(reason.querySelector(".pending-reason-tag"));
  env.render(CARD(2, { pending_reason: false }));
  assert.equal(env.$("#review-reason").hidden, true);
  assert.equal(env.$("#review-reason").querySelector(".pending-reason-form"), null);
});

test("mutation checks: revealing by default, omitting selection order and removing epoch each kill a probe", async () => {
  const revealMutation = SOURCE.replace('revealed: Boolean(item.pending_reason) || window.ReviewExtras?.hideReason() === false', "revealed: true");
  assert.notEqual(revealMutation, SOURCE, "recall default mutation must apply");
  const revealed = environment(revealMutation); revealed.render();
  assert.throws(() => assert.equal(revealed.$(".review-section").hidden, true), assert.AssertionError);

  const orderMutation = SOURCE.replace("generation !== rvfDetailGeneration || ", "");
  assert.notEqual(orderMutation, SOURCE, "selection order mutation must apply");
  const ordered = environment(orderMutation);
  const first = ordered.context.openMistake(1), second = ordered.context.openMistake(2);
  ordered.answer(ordered.calls.find((call) => call.url === "/api/mistakes/2"), CARD(2)); await second;
  ordered.answer(ordered.calls.find((call) => call.url === "/api/mistakes/1"), CARD(1)); await first;
  assert.throws(() => assert.match(ordered.$(".detail-heading").textContent, /题目2/), assert.AssertionError);

  const epochMutation = SOURCE.replace("epoch === sessionEpoch && page === rvfPageGeneration", "page === rvfPageGeneration");
  assert.notEqual(epochMutation, SOURCE, "session epoch mutation must apply");
  const stale = environment(epochMutation);
  const pending = stale.context.openMistake(1);
  stale.context.sessionEpoch += 1; stale.context.user = { id: 1 };
  stale.$("#detail").textContent = "新的登录会话";
  stale.answer(stale.calls[0], CARD(1)); await pending;
  assert.throws(() => assert.match(stale.$("#detail").textContent, /新的登录会话/), assert.AssertionError);
});
