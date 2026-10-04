"use strict";

/* 总览「趋势」区的行为测试（Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器）。
   覆盖：trendModel 纯函数（涨跌 / “新” / 空保持率 / 刻度）、各状态渲染（加载 / 错误 / 空数据 /
   切换指标 / 键盘）、时间范围与 localStorage、迟到响应。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

// 脚本在独立的 vm 环境里跑，数组的原型和这里不是同一个：比较前先转成普通 JSON。
const plain = (value) => JSON.parse(JSON.stringify(value));

function isoDay(base, offset) {
  const date = new Date(Date.UTC(base[0], base[1] - 1, base[2] + offset));
  return date.toISOString().slice(0, 10);
}
const TODAY = [2026, 10, 4];

function week(start, reviews, passed) {
  return { week_start: start, reviews, passed, rate: reviews ? Math.round((passed / reviews) * 1000) / 1000 : null };
}
/** 一份完整的 /api/stats/summary 响应；overrides 浅合并。 */
function summary(overrides = {}) {
  const days = overrides.days || 30;
  return {
    timezone: "Asia/Shanghai", today: isoDay(TODAY, 0), days,
    due: { today: 25, overdue: 22 },
    streak_days: 21,
    reviews: { current: 92, previous: 80 },
    retention: {
      current: { reviews: 70, passed: 61, rate: 0.871 },
      previous: { reviews: 60, passed: 53, rate: 0.883 },
      weekly: [
        week("2026-08-17", 9, 8), week("2026-08-24", 0, 0), week("2026-08-31", 10, 9), week("2026-09-07", 11, 9),
        week("2026-09-14", 12, 10), week("2026-09-21", 0, 0), week("2026-09-28", 13, 12), week("2026-10-05", 5, 5),
      ],
    },
    forecast: Array.from({ length: 14 }, (_, index) => ({ date: isoDay(TODAY, index), due: index === 0 ? 25 : [3, 0, 0, 5, 2, 0, 0, 1, 0, 4, 0, 0, 2][index - 1] })),
    daily_reviews: Array.from({ length: days }, (_, index) => ({ date: isoDay(TODAY, index - days + 1), count: index % 4 === 0 ? 0 : (index % 7) + 1 })),
    ...overrides,
  };
}

function setup() {
  return { env: load(["overview.js"]) };
}
/** api() 的替身：每个请求记下来，由测试决定何时成功 / 失败。 */
function setupWithRejection() {
  const env = load(["overview.js"]);
  const requests = [];
  const context = {
    api: (path) => new Promise((resolve, reject) => requests.push({ path, resolve, reject })),
    isCurrent: () => true,
  };
  return { env, context, requests };
}

/** reset() 还会清理总览页上原有的卡片：给它们一个最小的页面骨架（假浏览器不会替我们造 closest 要找的父节点）。 */
function homeSkeleton(env) {
  const { document } = env;
  const wrap = document.createElement("span");
  wrap.className = "tile-count-wrap";
  const count = document.createElement("span");
  count.id = "home-due-count";
  wrap.append(count);
  const tile = document.createElement("section");
  tile.id = "tile-review";
  const stamp = document.createElement("span");
  stamp.className = "ov-stamp";
  tile.append(stamp);
  document.body.append(wrap, tile);
}

const text = (node) => node.textContent;
const tabs = (env) => env.document.querySelectorAll("#home-trend-tabs [role=tab]");
const selectedKey = (env) => tabs(env).find((tab) => tab.getAttribute("aria-selected") === "true").dataset.metric;
const chart = (env) => env.document.querySelector("#home-trend-chart");

/* ---------------- trendModel ---------------- */
test("trendModel: four metric blocks with the spec's wording", () => {
  const { env } = setup();
  const model = plain(env.window.Overview.trendModel(summary()));
  assert.deepEqual(model.metrics.map((metric) => metric.key), ["due", "streak", "reviews", "retention"]);
  const [due, streak, reviews, retention] = model.metrics;
  assert.equal(due.value, "25");
  assert.equal(due.sub, "其中逾期 22");
  assert.equal(streak.value, "21");
  assert.equal(streak.unit, "天");
  assert.equal(reviews.label, "近 30 天复习");
  assert.equal(reviews.value, "92");
  assert.deepEqual(reviews.badge, { direction: "up", text: "+15%" });
  assert.equal(retention.value, "87%");
  assert.deepEqual(retention.badge, { direction: "down", text: "−1.2 个点" });
});

test("trendModel: review change is up / down / flat and a zero previous period reads 新", () => {
  const { env } = setup();
  const badge = (current, previous) => plain(env.window.Overview.trendModel(summary({ reviews: { current, previous } })).metrics[2].badge);
  assert.deepEqual(badge(92, 80), { direction: "up", text: "+15%" });
  assert.deepEqual(badge(40, 80), { direction: "down", text: "−50%" });
  assert.deepEqual(badge(80, 80), { direction: "flat", text: "持平" });
  assert.deepEqual(badge(7, 0), { direction: "new", text: "新" }, "no percentage when the previous period is empty");
  assert.deepEqual(badge(0, 0), { direction: "flat", text: "持平" });
  assert.deepEqual(badge(0, 5), { direction: "down", text: "−100%" });
  assert.deepEqual(badge(1001, 1000), { direction: "flat", text: "持平" }, "a change that rounds to 0% is not an arrow");
});

test("trendModel: retention delta is in points, null rates show a dash and no delta", () => {
  const { env } = setup();
  const model = (current, previous) => plain(env.window.Overview.trendModel(summary({
    retention: { current, previous, weekly: [] },
  })).metrics[3]);
  const up = model({ reviews: 10, passed: 9, rate: 0.9 }, { reviews: 10, passed: 8, rate: 0.8 });
  assert.deepEqual(up.badge, { direction: "up", text: "+10 个点" });
  assert.deepEqual(model({ reviews: 10, passed: 8, rate: 0.8 }, { reviews: 10, passed: 8, rate: 0.8 }).badge, { direction: "flat", text: "持平" });
  const none = model({ reviews: 0, passed: 0, rate: null }, { reviews: 10, passed: 8, rate: 0.8 });
  assert.equal(none.value, "—");
  assert.equal(none.badge, null);
  assert.match(none.explain, /至少一次非首次复习/);
  assert.equal(model({ reviews: 10, passed: 8, rate: 0.8 }, { reviews: 0, passed: 0, rate: null }).badge, null);
});

test("trendModel: scales reach the data and labels stay inside the axis", () => {
  const { env } = setup();
  const model = plain(env.window.Overview.trendModel(summary()));
  const due = model.charts.due;
  assert.equal(due.bars.length, 14);
  assert.deepEqual(due.bars.slice(0, 4).map((bar) => bar.label), ["今天", "明", "后", "10-07"]);
  assert.equal(due.bars[0].today, true);
  assert.ok(due.ticks.length <= 5 && due.ticks[0] === 0);
  assert.equal(due.ticks[due.ticks.length - 1], due.top);
  assert.ok(due.top >= 25, "the top tick covers the tallest bar");
  const retention = model.charts.retention;
  assert.equal(retention.target, 0.85);
  assert.equal(retention.top, 1);
  assert.ok(retention.bottom <= 0.85 && retention.bottom <= 0.889 && retention.bottom >= 0);
  assert.equal(retention.ticks[0], retention.bottom);
  assert.equal(retention.ticks[retention.ticks.length - 1], 1);
  assert.deepEqual(retention.points.map((point) => point.rate === null), [false, true, false, false, false, true, false, false]);
});

test("trendModel: missing or empty data yields an explanatory empty state, not an empty chart", () => {
  const { env } = setup();
  const empty = plain(env.window.Overview.trendModel({ days: 30, today: "2026-10-04" }));
  assert.equal(empty.metrics.every((metric) => metric.value === "—"), true);
  for (const key of ["due", "streak", "reviews", "retention"]) assert.ok(empty.charts[key].empty, `${key} has an explanation`);

  const zero = plain(env.window.Overview.trendModel(summary({
    forecast: summary().forecast.map((item) => ({ ...item, due: 0 })),
    daily_reviews: summary().daily_reviews.map((item) => ({ ...item, count: 0 })),
    retention: { current: { reviews: 0, passed: 0, rate: null }, previous: { reviews: 0, passed: 0, rate: null }, weekly: summary().retention.weekly.map((item) => week(item.week_start, 0, 0)) },
  })));
  for (const key of ["due", "streak", "reviews", "retention"]) assert.ok(zero.charts[key].empty, `${key} (all zero) has an explanation`);
  assert.match(zero.charts.retention.empty, /非首次复习/);
});

/* ---------------- 渲染 ---------------- */
test("renderTrend: tablist semantics, forecast bars and the shared section title", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary());
  const root = env.document.querySelector("#home-trend");
  assert.equal(root.dataset.state, "ready");
  assert.equal(root.getAttribute("aria-busy"), "false");
  assert.equal(text(env.document.querySelector("#home-trend-title")), "趋势");
  const list = env.document.querySelector("#home-trend-tabs");
  assert.equal(list.getAttribute("role"), "tablist");
  assert.equal(tabs(env).length, 4);
  assert.deepEqual(tabs(env).map((tab) => tab.getAttribute("aria-selected")), ["true", "false", "false", "false"]);
  assert.deepEqual(tabs(env).map((tab) => tab.tabIndex), [0, -1, -1, -1]);
  assert.equal(env.document.querySelector("#home-trend-panel").getAttribute("aria-labelledby"), "home-trend-tab-due");
  const rects = chart(env).querySelectorAll(".ov-bar-rect");
  assert.equal(rects.length, 14);
  assert.equal(rects.filter((rect) => rect.className.includes("is-today")).length, 1);
  assert.equal(rects.filter((rect) => rect.className.includes("is-zero")).length, 7, "zero days draw a thin line");
  assert.match(text(chart(env)), /含逾期/);
  assert.match(text(chart(env)), /今天/);
  assert.match(text(tabs(env)[0]), /待复习/);
  assert.match(text(tabs(env)[0]), /其中逾期 22/);
});

test("renderTrend: the delta line carries arrow, sign and a screen-reader sentence", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary());
  const reviews = tabs(env)[2];
  const delta = reviews.querySelector(".ov-trend-delta");
  assert.ok(delta.className.includes("is-up"));
  assert.match(text(delta), /↗\+15%/);
  assert.match(text(reviews.querySelector(".ov-sr-only")), /较上一周期上升 15%/);
  const retention = tabs(env)[3].querySelector(".ov-trend-delta");
  assert.ok(retention.className.includes("is-down"));
  assert.match(text(retention), /↘−1\.2 个点/);
});

test("renderTrend: a bar chart is bars only, never text from the server as markup", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary({ forecast: summary().forecast.map((item, index) => ({ ...item, date: index === 5 ? "2026-10-09<img>" : item.date })) }));
  assert.equal(chart(env).querySelector("img"), null);
});

test("keyboard: arrows, Home and End move the selection and swap the main chart", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary());
  const list = env.document.querySelector("#home-trend-tabs");
  const press = (key) => {
    const event = new FakeEvent("keydown", { bubbles: true, props: { key } });
    list.dispatchEvent(event);
    return event;
  };
  assert.equal(press("ArrowRight").defaultPrevented, true);
  assert.equal(selectedKey(env), "streak");
  assert.equal(env.document.activeElement.dataset.metric, "streak", "focus follows the selection");
  assert.equal(chart(env).querySelectorAll(".ov-heat-cell").length, 30);
  assert.deepEqual(tabs(env).map((tab) => tab.tabIndex), [-1, 0, -1, -1]);

  press("ArrowRight");
  assert.equal(selectedKey(env), "reviews");
  assert.equal(chart(env).querySelectorAll(".ov-area-line").length, 1);
  assert.equal(env.document.querySelector("#home-trend-panel").getAttribute("aria-labelledby"), "home-trend-tab-reviews");

  press("ArrowRight");
  assert.equal(selectedKey(env), "retention");
  assert.equal(chart(env).querySelectorAll(".ov-chart-target").length, 1);
  assert.match(text(chart(env)), /目标线 85%/);

  press("ArrowRight");
  assert.equal(selectedKey(env), "due", "wraps to the first");
  press("ArrowLeft");
  assert.equal(selectedKey(env), "retention", "wraps to the last");
  press("Home");
  assert.equal(selectedKey(env), "due");
  press("End");
  assert.equal(selectedKey(env), "retention");
  assert.equal(press("a").defaultPrevented, false, "other keys are left alone");
});

test("clicking a tab selects it; the data table and the how-to follow the metric", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary());
  tabs(env)[3].click();
  assert.equal(selectedKey(env), "retention");
  const rows = env.document.querySelectorAll("#home-trend-data tbody tr");
  assert.equal(rows.length, 8);
  assert.equal(text(rows[1].children[3]), "—", "a week without data is a dash in the table");
  assert.match(text(env.document.querySelector("#home-trend-how")), /非首次复习/);
  tabs(env)[0].click();
  assert.equal(env.document.querySelectorAll("#home-trend-data tbody tr").length, 14);
  assert.equal(env.document.querySelector("#home-trend-data table").querySelector("caption").textContent.includes("未来 14 天"), true);
  assert.equal(env.document.querySelectorAll("#home-trend-data th[scope=col]").length, 2);
});

test("retention chart breaks the line at weeks without data", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary());
  tabs(env)[3].click();
  const lines = chart(env).querySelectorAll(".ov-trend-line");
  // 周序列：有 无 有 有 有 无 有 有 → 三段连续线（第一周单独成点，不画线）。
  assert.equal(lines.length, 2);
  assert.equal(chart(env).querySelectorAll(".ov-trend-dot").length, 6);
});

test("empty chart data shows the explanation instead of an empty chart", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary({ daily_reviews: summary().daily_reviews.map((item) => ({ ...item, count: 0 })) }));
  tabs(env)[2].click();
  assert.equal(chart(env).querySelector("svg"), null);
  assert.match(text(chart(env)), /还没有复习记录/);
  assert.equal(env.document.querySelector("#home-trend-data").hidden, true);
});

test("hovering the review chart shows that day's value", () => {
  const { env } = setup();
  env.window.Overview.renderTrend(summary());
  tabs(env)[2].click();
  const overlay = chart(env).querySelector(".ov-hover-overlay");
  const guide = chart(env).querySelector(".ov-hover");
  assert.equal(guide.className.includes("is-on"), false);
  // 假浏览器里 svg 宽 100：clientX = 100 → 最右端（今天），0 → 最左端。
  overlay.dispatchEvent(new FakeEvent("pointermove", { props: { clientX: 100 } }));
  const last = summary().daily_reviews[29];
  assert.ok(guide.className.includes("is-on"));
  assert.equal(text(chart(env).querySelector(".ov-hover-text")), `${last.date.slice(5)} · ${last.count} 次`);
  overlay.dispatchEvent(new FakeEvent("pointermove", { props: { clientX: 0 } }));
  assert.equal(text(chart(env).querySelector(".ov-hover-text")), `${summary().daily_reviews[0].date.slice(5)} · ${summary().daily_reviews[0].count} 次`);
  overlay.dispatchEvent(new FakeEvent("pointerleave"));
  assert.equal(guide.className.includes("is-on"), false);
});

/* ---------------- 请求 ---------------- */
test("loadTrend: skeleton while loading, then the chart; the range comes from localStorage", async () => {
  const { env, context, requests } = setupWithRejection();
  env.window.localStorage.setItem("home-trend-days", "90");
  const loading = env.window.Overview.loadTrend(context);
  const root = env.document.querySelector("#home-trend");
  assert.equal(root.dataset.state, "loading");
  assert.equal(root.getAttribute("aria-busy"), "true");
  assert.equal(env.document.querySelectorAll("#home-trend-tabs .is-skeleton").length, 4);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].path, "/api/stats/summary?days=90");
  const pressed = env.document.querySelectorAll("#home-trend-range button").map((button) => button.getAttribute("aria-pressed"));
  assert.deepEqual(pressed, ["false", "false", "true"]);
  requests[0].resolve(summary({ days: 90 }));
  await loading;
  await tick();
  assert.equal(root.dataset.state, "ready");
  assert.equal(tabs(env).length, 4);
  assert.match(text(tabs(env)[2]), /近 90 天复习/);
});

test("loadTrend: a missing, invalid or unreadable stored range falls back to 30 days", async () => {
  for (const stored of [null, "15", "abc", "30.5"]) {
    const { env, context, requests } = setupWithRejection();
    if (stored !== null) env.window.localStorage.setItem("home-trend-days", stored);
    env.window.Overview.loadTrend(context);
    assert.equal(requests[0].path, "/api/stats/summary?days=30", `stored ${stored}`);
  }
  const { env, context, requests } = setupWithRejection();
  env.window.localStorage = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  env.window.Overview.loadTrend(context);
  assert.equal(requests[0].path, "/api/stats/summary?days=30");
  requests[0].resolve(summary());
  await tick();
  env.document.querySelectorAll("#home-trend-range button")[0].click(); // 写入失败也不能抛
  assert.equal(requests[1].path, "/api/stats/summary?days=7");
});

test("choosing a range requests again, remembers the choice and keeps the selected metric", async () => {
  const { env, context, requests } = setupWithRejection();
  env.window.Overview.loadTrend(context);
  requests[0].resolve(summary());
  await tick();
  tabs(env)[3].click();
  env.document.querySelectorAll("#home-trend-range button")[0].click(); // 7 天
  assert.equal(requests[1].path, "/api/stats/summary?days=7");
  assert.equal(env.window.localStorage.getItem("home-trend-days"), "7");
  assert.equal(env.document.querySelector("#home-trend").dataset.state, "loading");
  requests[1].resolve(summary({ days: 7 }));
  await tick();
  assert.equal(selectedKey(env), "retention");
  assert.match(text(tabs(env)[2]), /近 7 天复习/);
  env.document.querySelectorAll("#home-trend-range button")[0].click(); // 已经是 7 天：不重复请求
  assert.equal(requests.length, 2);
});

test("a failed request shows one error line with a working retry", async () => {
  const { env, context, requests } = setupWithRejection();
  env.window.Overview.loadTrend(context);
  requests[0].reject(new Error("boom"));
  await tick();
  const root = env.document.querySelector("#home-trend");
  assert.equal(root.dataset.state, "error");
  assert.equal(env.document.querySelector("#home-trend-error").hidden, false);
  assert.equal(env.document.querySelector("#home-trend-error").getAttribute("role"), "alert");
  assert.equal(env.document.querySelector("#home-trend-tabs").hidden, true);
  env.document.querySelector("#home-trend-retry").click();
  assert.equal(requests.length, 2);
  assert.equal(env.document.querySelector("#home-trend-error").hidden, true, "retry goes back to the skeleton");
  requests[1].resolve(summary());
  await tick();
  assert.equal(root.dataset.state, "ready");
  assert.equal(tabs(env).length, 4);
});

test("a late answer to an older range never overwrites the newer one", async () => {
  const { env, context, requests } = setupWithRejection();
  env.window.Overview.loadTrend(context);
  const buttons = env.document.querySelectorAll("#home-trend-range button");
  buttons[0].click(); // 7 天（请求 1）
  buttons[2].click(); // 90 天（请求 2）
  assert.deepEqual(requests.map((request) => request.path), [
    "/api/stats/summary?days=30", "/api/stats/summary?days=7", "/api/stats/summary?days=90",
  ]);
  requests[2].resolve(summary({ days: 90 }));
  await tick();
  requests[1].resolve(summary({ days: 7, reviews: { current: 1, previous: 1 } }));
  await tick();
  requests[0].reject(new Error("late failure"));
  await tick();
  assert.equal(env.document.querySelector("#home-trend").dataset.state, "ready");
  assert.match(text(tabs(env)[2]), /近 90 天复习/);
  assert.equal(chart(env).querySelectorAll(".ov-bar-rect").length, 14);
});

test("reset (sign-out / leaving the page) discards the answer that arrives afterwards", async () => {
  const { env, context, requests } = setupWithRejection();
  homeSkeleton(env);
  env.window.Overview.loadTrend(context);
  env.window.Overview.reset();
  requests[0].resolve(summary({ streak_days: 999, due: { today: 777, overdue: 1 } }));
  await tick();
  const root = env.document.querySelector("#home-trend");
  assert.equal(root.dataset.state, "loading", "still the skeleton, the old account's numbers never show");
  assert.equal(root.textContent.includes("777"), false);
  assert.equal(root.textContent.includes("999"), false);

  env.window.Overview.loadTrend(context); // 下一位用户重新进入
  requests[1].resolve(summary({ due: { today: 4, overdue: 0 } }));
  await tick();
  assert.equal(root.dataset.state, "ready");
  assert.match(text(tabs(env)[0]), /4/);
  assert.equal(root.textContent.includes("777"), false);
});

test("an answer is dropped when the caller says it is no longer current", async () => {
  const { env, requests } = setupWithRejection();
  let current = true;
  const context = { api: (path) => new Promise((resolve, reject) => requests.push({ path, resolve, reject })), isCurrent: () => current };
  env.window.Overview.loadTrend(context);
  current = false; // 切到别的账号 / 别的页面
  requests[0].resolve(summary({ due: { today: 555, overdue: 0 } }));
  await tick();
  assert.equal(env.document.querySelector("#home-trend").dataset.state, "loading");
  assert.equal(env.document.querySelector("#home-trend").textContent.includes("555"), false);

  env.window.Overview.loadTrend({ ...context, isCurrent: () => current });
  requests[1].reject(new Error("late error"));
  await tick();
  assert.equal(env.document.querySelector("#home-trend").dataset.state, "loading", "a late failure is dropped too");
});

test("after reset, picking a range does nothing until the page loads the section again", () => {
  const { env, context, requests } = setupWithRejection();
  homeSkeleton(env);
  env.window.Overview.loadTrend(context);
  env.window.Overview.reset();
  env.document.querySelectorAll("#home-trend-range button")[0].click();
  assert.equal(requests.length, 1, "no request without a loaded page context");
});
