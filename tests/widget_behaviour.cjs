"use strict";

/* 小部件的异步行为测试（Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器）。
   覆盖字符串断言测不到的东西：请求晚回来、登出再登录、强制刷新、重复挂载。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const LIMITS = { per_mistake: 8, length: 20 };
// 脚本在独立的 vm 环境里跑，数组的原型和这里不是同一个：比较前先转成普通 JSON。
const plain = (value) => JSON.parse(JSON.stringify(value));

/* ---------------- 标签 ---------------- */
test("tags: an answer that arrives after sign-out never reaches the next account", async () => {
  const env = load(["tags.js"]);
  const sidebar = env.document.createElement("div");
  const first = env.window.TagFilters.mountSidebar(sidebar);
  await tick();
  assert.equal(env.calls.length, 1);

  env.window.TagFilters.reset(); // 登出
  env.respond(env.calls[0], 200, { tags: [{ tag: "A的私人标签", count: 3 }], suggestions: ["边界"], limits: LIMITS });
  await first;
  await tick();
  assert.deepEqual(plain(env.window.TagFilters.tags()), []);
  assert.equal(sidebar.textContent.includes("A的私人标签"), false);

  const second = env.window.TagFilters.mountSidebar(sidebar); // 下一位用户
  await tick();
  assert.equal(env.calls.length, 2, "the new account asks for its own tags");
  env.respond(env.calls[1], 200, { tags: [{ tag: "B的标签", count: 1 }], suggestions: [], limits: LIMITS });
  await second;
  await tick();
  assert.deepEqual(plain(env.window.TagFilters.tags()), [{ tag: "B的标签", count: 1 }]);
  assert.equal(sidebar.textContent.includes("A的私人标签"), false);
});

test("tags: a forced refresh requests again and an older answer cannot overwrite the newer one", async () => {
  const env = load(["tags.js"]);
  const sidebar = env.document.createElement("div");
  const mounted = env.window.TagFilters.mountSidebar(sidebar);
  await tick();
  env.respond(env.calls[0], 200, { tags: [], suggestions: [], limits: LIMITS });
  await mounted;

  const slow = env.window.TagFilters.refresh();
  const fast = env.window.TagFilters.refresh();
  await tick();
  assert.equal(env.calls.length, 3, "each forced refresh really asks the server");
  env.respond(env.calls[2], 200, { tags: [{ tag: "新", count: 2 }], suggestions: [], limits: LIMITS });
  await fast;
  env.respond(env.calls[1], 200, { tags: [{ tag: "旧", count: 1 }], suggestions: [], limits: LIMITS });
  await slow;
  await tick();
  assert.deepEqual(plain(env.window.TagFilters.tags()), [{ tag: "新", count: 2 }]);
});

/* ---------------- 掌握度提醒 ---------------- */
function overviewCard(env) {
  const card = env.document.createElement("section");
  card.id = "ov-fading-card";
  card.hidden = true;
  const target = env.document.createElement("div");
  target.id = "ov-fading";
  card.append(target);
  env.document.body.append(card);
  return { card, target };
}
const REPORT = (alert) => ({ today: "2026-10-02", points: [], threshold: 70, overall: 60, zones: [], alert });

test("mastery: a stale answer after sign-out neither shows the alert nor fills the cache", async () => {
  const env = load(["mastery.js"]);
  const { card, target } = overviewCard(env);
  const pending = env.window.Mastery.mountAlert(card);
  await tick();
  assert.equal(env.calls.length, 1);

  env.window.Mastery.reset(); // 登出
  env.respond(env.calls[0], 200, REPORT({ zone: "A的分区", mastery: 30, due: 2, overdue: 2, at_risk: 2 }));
  await pending;
  await tick();
  assert.equal(card.hidden, true, "the previous account's alert stays hidden");
  assert.equal(target.textContent, "");

  const again = env.window.Mastery.mountAlert(card);
  await tick();
  assert.equal(env.calls.length, 2, "the stale answer did not populate the cache");
  env.respond(env.calls[1], 200, REPORT(null));
  await again;
  assert.equal(card.hidden, true);
});

/* ---------------- 热力图 / 日历 ---------------- */
const ACTIVITY = (reviews) => ({
  today: "2026-10-02", from: "2026-04-06", weeks: 26,
  days: [{ date: "2026-10-02", reviews, records: 0 }],
  totals: { reviews, records: 0, active_days: 1 }, streak_days: 1,
});

test("activity: a forced refresh is a new request and a late older answer cannot win", async () => {
  const env = load(["activity.js"]);
  const el = env.document.createElement("div");
  env.document.body.append(el);
  const mounted = env.window.ActivityWidgets.mountHeatmap(el, { weeks: 26 });
  await tick();
  assert.equal(env.calls.length, 1);

  const refreshed = env.window.ActivityWidgets.refresh(); // 数据刚变过
  await tick();
  assert.equal(env.calls.length, 2, "the in-flight (pre-change) request is not reused");
  env.respond(env.calls[1], 200, ACTIVITY(9));
  await refreshed;
  env.respond(env.calls[0], 200, ACTIVITY(1)); // 旧请求晚到
  await mounted;
  await tick();
  assert.match(el.querySelector(".heatmap-figure").getAttribute("aria-label"), /共复习 9 次/);

  // 之后不强制的请求直接用最新那份缓存，不会被旧结果污染。
  const other = env.document.createElement("div");
  env.document.body.append(other);
  await env.window.ActivityWidgets.mountHeatmap(other, { weeks: 26 });
  assert.equal(env.calls.length, 2);
  assert.match(other.querySelector(".heatmap-figure").getAttribute("aria-label"), /共复习 9 次/);
});

test("activity: a failed load stops announcing 'busy' and offers a retry", async () => {
  const env = load(["activity.js"]);
  const el = env.document.createElement("div");
  env.document.body.append(el);
  const mounted = env.window.ActivityWidgets.mountHeatmap(el, { weeks: 26 });
  await tick();
  env.respond(env.calls[0], 500, {});
  await mounted;
  await tick();
  assert.equal(el.getAttribute("aria-busy"), "false");
  assert.ok(el.querySelector(".heatmap-error"));
  assert.ok(el.querySelector(".heatmap-retry"));
});

test("activity: signing out and in again keeps exactly one set of calendar listeners, and the live instance answers", async () => {
  const env = load(["activity.js"]);
  const cal = env.document.createElement("div");
  env.document.body.append(cal);
  const picked = [];
  const firstMount = env.window.ActivityWidgets.mountHeatmap(cal, { weeks: 5, calendar: true, onSelectDay: (day) => picked.push(["old", day]) });
  await tick();
  env.respond(env.calls[0], 200, ACTIVITY(2));
  await firstMount;

  env.window.ActivityWidgets.reset(); // 登出
  const secondMount = env.window.ActivityWidgets.mountHeatmap(cal, { weeks: 5, calendar: true, onSelectDay: (day) => picked.push(["new", day]) });
  await tick();
  env.respond(env.calls[1], 200, ACTIVITY(3));
  await secondMount;
  await tick();

  for (const type of ["click", "keydown", "focusin"]) assert.equal(cal.listeners[type].length, 1, `${type} is bound once`);
  const cell = cal.querySelector("button.cal-cell");
  assert.ok(cell, "calendar cells are rendered as buttons");
  cell.click();
  assert.equal(picked.length, 1);
  assert.equal(picked[0][0], "new");
});

/* ---------------- 专注复习 ---------------- */
const CARD = (id) => ({
  id, problem_id: id, title: `题目${id}`, zone: "算法", language: "Python", code: "pass", thinking: "想法",
  description: "错因", repetitions: 1, interval_days: 2, ease_factor: 2.5, due_date: "2026-10-01", version: 3, tags: [],
});
async function startSession(env, ids) {
  const finished = env.window.FocusReview.start({});
  await tick();
  const request = env.calls.at(-1);
  assert.ok(request.url.startsWith("/api/mistakes?"));
  env.respond(request, 200, { today: "2026-10-02", items: ids.map(CARD) });
  await tick();
  return { finished }; // 包一层：直接返回 promise 的话 await 会一直等到关闭
}
const countText = (env) => env.document.querySelector(".focus-count").textContent;

test("focus: a grade answer that arrives after the overlay was closed is harmless and still tells the widgets", async () => {
  const env = load(["focus.js"], { extra: { stampSeal() {} } });
  const { finished } = await startSession(env, [1, 2]);
  assert.equal(countText(env), "1 / 2");

  env.document.querySelector('[data-quality="4"]').click();
  await tick();
  const grade = env.calls.at(-1);
  assert.equal(grade.url, "/api/mistakes/1/review");
  assert.equal(JSON.parse(grade.init.body).version, 3);

  env.window.FocusReview.close(); // 评分还没回来就按了 Esc
  env.respond(grade, 200, { interval_days: 3, version: 4 });
  await tick();
  await finished;
  assert.equal(env.window.FocusReview.isOpen(), false);
  assert.ok(env.events.some((event) => event.type === "app:data-changed" && event.detail.reason === "focus-review"),
    "the saved grade still refreshes the counters elsewhere");
});

test("focus: a late answer from the previous round cannot touch a round opened afterwards", async () => {
  const env = load(["focus.js"], { extra: { stampSeal() {} } });
  await startSession(env, [1, 2]);
  env.document.querySelector('[data-quality="5"]').click();
  await tick();
  const lateGrade = env.calls.at(-1);

  env.window.FocusReview.close();
  await startSession(env, [1, 2]); // 新的一轮
  assert.equal(countText(env), "1 / 2");

  env.respond(lateGrade, 200, { interval_days: 6, version: 4 });
  await tick();
  assert.equal(countText(env), "1 / 2", "the new round's progress is untouched");

  const before = env.calls.length;
  env.document.querySelector('[data-quality="3"]').click(); // 新一轮仍可正常评分（没有被旧请求的"提交中"标记卡住）
  await tick();
  assert.equal(env.calls.length, before + 1);
  assert.equal(env.calls.at(-1).url, "/api/mistakes/1/review");
});

test("focus: the zone filter and tag are passed through, an unauthorized answer closes the overlay", async () => {
  const env = load(["focus.js"], { extra: { stampSeal() {} } });
  const finished = env.window.FocusReview.start({ tag: "边界" });
  await tick();
  assert.match(env.calls[0].url, /tag=%E8%BE%B9%E7%95%8C/);
  env.respond(env.calls[0], 200, { today: "2026-10-02", items: [CARD(1)] });
  await tick();
  env.document.querySelector('[data-quality="4"]').click();
  await tick();
  env.respond(env.calls.at(-1), 401, { detail: "请先登录" });
  await tick();
  await finished;
  assert.equal(env.window.FocusReview.isOpen(), false);
});
