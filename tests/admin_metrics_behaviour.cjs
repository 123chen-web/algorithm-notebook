"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const SCRIPT = fs.readFileSync(path.join(__dirname, "..", "static", "admin-metrics.js"), "utf8");
const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), []));

const pair = (current, previous) => ({ current, previous });
function payload(days = 7, overrides = {}) {
  const daily = Array.from({ length: days }, (_item, index) => ({
    date: new Date(Date.UTC(2026, 8, 28 - (days - 1 - index))).toISOString().slice(0, 10),
    new_users: index % 3, active_users: index, reviews: index * 10,
  }));
  return {
    days, generated_at: "2026-09-28T12:00:00+00:00",
    new_users: pair(12, 9), active_users: pair(31, 28), records: pair(140, 98), reviews: pair(620, 620),
    posts: pair(4, 3), comments: pair(21, 17),
    ai: {
      calls: pair(55, 0), failed: pair(3, 2), tokens: { prompt: 120000, completion: 31000 },
      by_feature: [{ feature: "variant", calls: 30, failed: 1, tokens: 90000 }, { feature: "photo", calls: 25, failed: 2, tokens: 61000 }],
    },
    pending_reports: 2, redeem: { created: 10, redeemed: 6, unused: 4 },
    funnel: { registered: 12, first_record: 9, first_review: 6, returned_next_day: 3 },
    daily, ...overrides,
  };
}

/** 在假浏览器里先摆好页面上的固定按钮，再执行脚本（脚本加载时按 data-* 绑定点击）。 */
function setup({ user = { id: 1, is_admin: true }, view = "admin", extra = {}, storedDays = null } = {}) {
  const env = load([], { extra });
  if (storedDays) env.window.localStorage.setItem("admin-metrics-days", String(storedDays));
  const originalCreate = env.document.createElement.bind(env.document);
  env.document.createElement = (tag) => {
    const element = originalCreate(tag);
    element.styleValues = {};
    element.style = { setProperty: (name, value) => { element.styleValues[name] = value; } };
    return element;
  };
  const root = env.document.createElement("section");
  root.id = "admin-metrics";
  env.document.body.append(root);
  const buttons = {};
  for (const [attribute, values] of [["amDays", ["7", "30"]], ["amSeries", ["new_users", "active_users", "reviews"]]]) {
    for (const value of values) {
      const button = env.document.createElement("button");
      button.dataset[attribute] = value;
      root.append(button);
      buttons[value] = button;
    }
  }
  vm.runInContext(SCRIPT, env.context, { filename: "admin-metrics.js" });
  const state = { epoch: 1, user, view };
  const requests = [];
  env.window.AdminMetrics.configure({
    getEpoch: () => state.epoch, getUser: () => state.user, getView: () => state.view,
    api: (url, init) => { const call = { url, init, ...deferred() }; requests.push(call); return call.promise; },
  });
  const $ = (id) => env.document.querySelector(`#${id}`);
  const text = (id) => $(id).textContent;
  const cards = () => $("admin-metrics-strip").children;
  const card = (label) => cards().find((item) => item.children[0].textContent === label);
  const cardText = (label) => card(label).children.map((child) => child.textContent);
  return { ...env, root, buttons, state, requests, $, text, cards, card, cardText,
    api: env.window.AdminMetrics, helpers: env.window.AdminMetrics.helpers };
}

/* ---------------- 纯函数 ---------------- */

test("delta: up, down, flat, new and zero over zero", () => {
  const { delta } = setup().helpers;
  assert.deepEqual({ ...delta(12, 9) }, { trend: "up", text: "↑ 33%" });
  assert.deepEqual({ ...delta(9, 12) }, { trend: "down", text: "↓ 25%" });
  assert.deepEqual({ ...delta(5, 5) }, { trend: "flat", text: "→ 持平" });
  assert.deepEqual({ ...delta(0, 0) }, { trend: "flat", text: "→ 持平" });
  assert.deepEqual({ ...delta(3, 0) }, { trend: "new", text: "新" });
  assert.deepEqual({ ...delta(0, 4) }, { trend: "down", text: "↓ 100%" });
  assert.equal(delta(301, 300).text, "↑ 1%", "tiny change never rounds to 0%");
  assert.equal(delta(200, 100).text, "↑ 100%");
});

test("formatTokens: plain below 10k, 万 / 亿 above", () => {
  const { formatTokens } = setup().helpers;
  assert.equal(formatTokens(0), "0");
  assert.equal(formatTokens(9999), "9999");
  assert.equal(formatTokens(10000), "1 万");
  assert.equal(formatTokens(151000), "15.1 万");
  assert.equal(formatTokens(120000), "12 万");
  assert.equal(formatTokens(250000000), "2.5 亿");
  assert.equal(formatTokens(null), "0");
});

test("funnelRows: conversion is relative to the previous step; zero denominators are safe", () => {
  const { funnelRows } = setup().helpers;
  const { empty, rows } = funnelRows({ registered: 12, first_record: 9, first_review: 6, returned_next_day: 3 });
  assert.equal(empty, false);
  assert.deepEqual([...rows.map((row) => row.count)], [12, 9, 6, 3]);
  assert.equal(rows[0].rate, null);
  assert.equal(rows[1].rate, 0.75);
  assert.equal(rows[2].rate, 6 / 9);
  assert.equal(rows[3].rate, 0.5);
  assert.deepEqual([...rows.map((row) => row.width)], [100, 75, 50, 25]);

  const none = funnelRows({ registered: 0, first_record: 0, first_review: 0, returned_next_day: 0 });
  assert.equal(none.empty, true);
  assert.equal(none.rows.length, 0);
  const broken = funnelRows({ registered: 5, first_record: 0, first_review: 0, returned_next_day: 2 }).rows;
  assert.equal(broken[2].rate, null, "previous step is zero → no rate, no NaN / Infinity");
  assert.equal(broken[3].rate, null);
  assert.ok(funnelRows({ registered: 2, first_record: 2, first_review: 2, returned_next_day: 2 }).rows.every((row) => row.width <= 100));
});

test("chartModel: proportional geometry, integer ticks, zero baseline", () => {
  const { chartModel, niceStep } = setup().helpers;
  assert.deepEqual([0, 1, 4, 5, 9, 40, 41, 100, 1234].map(niceStep), [1, 1, 1, 2, 5, 10, 20, 50, 500]);
  const daily = [0, 5, 10].map((value, index) => ({ date: `2026-09-2${index}`, reviews: value }));
  const model = chartModel(daily, "reviews", 400, 220);
  assert.equal(model.yMax % model.step, 0);
  assert.ok(model.yMax >= 10);
  assert.equal(model.ticks.length, 5);
  assert.equal(model.ticks[0].value, 0);
  assert.equal(model.ticks[0].y, model.baseline);
  assert.ok(model.ticks.every((tick) => Number.isInteger(tick.value)));
  const [low, mid, high] = model.points;
  assert.equal(low.y, model.baseline, "zero sits on the baseline");
  assert.ok(Math.abs((model.baseline - mid.y) * 2 - (model.baseline - high.y)) < 0.01, "heights are proportional to values");
  assert.ok(low.x < mid.x && mid.x < high.x);
  assert.equal(high.x, 400 - model.pad.right);
  assert.ok(model.area.startsWith("M") && model.area.endsWith("Z"));
});

test("chartModel: all zeros and a single day do not break", () => {
  const { chartModel } = setup().helpers;
  const zeros = chartModel([{ date: "2026-09-27", reviews: 0 }, { date: "2026-09-28", reviews: 0 }], "reviews", 320, 220);
  assert.equal(zeros.yMax, 4);
  assert.ok(zeros.points.every((point) => point.y === zeros.baseline && Number.isFinite(point.x)));
  const single = chartModel([{ date: "2026-09-28", reviews: 3 }], "reviews", 320, 220);
  assert.ok(Number.isFinite(single.points[0].x) && Number.isFinite(single.points[0].y));
});

test("chartModel: x labels never crowd each other and always include the last day", () => {
  const { chartModel } = setup().helpers;
  const daily = Array.from({ length: 30 }, (_item, index) => ({ date: `2026-09-${String(index + 1).padStart(2, "0")}`, reviews: index }));
  for (const width of [280, 360, 760]) {
    const model = chartModel(daily, "reviews", width, 220);
    const xs = model.xTicks.map((tick) => tick.x);
    assert.equal(xs[xs.length - 1], model.points[29].x);
    for (let index = 1; index < xs.length; index += 1) assert.ok(xs[index] - xs[index - 1] >= 40, `labels overlap at width ${width}`);
    assert.equal(model.xTicks[xs.length - 1].text, "09-30");
  }
});

/* ---------------- 渲染 ---------------- */

test("loading shows the skeleton, then six metric cards with deltas", async () => {
  const env = setup();
  const loading = env.api.load();
  assert.equal(env.root.dataset.state, "loading");
  assert.equal(env.root.getAttribute("aria-busy"), "true");
  assert.equal(env.$("admin-metrics-skeleton").hidden, false);
  assert.equal(env.$("admin-metrics-body").hidden, true);
  assert.equal(env.requests.length, 1);
  assert.equal(env.requests[0].url, "/api/admin/metrics?days=7");
  env.requests[0].resolve(payload());
  assert.equal(await loading, true);
  assert.equal(env.root.dataset.state, "ready");
  assert.equal(env.root.getAttribute("aria-busy"), "false");
  assert.equal(env.$("admin-metrics-skeleton").hidden, true);
  assert.equal(env.$("admin-metrics-body").hidden, false);
  assert.deepEqual(env.cards().map((item) => item.children[0].textContent),
    ["新增用户", "活跃用户", "新增记录", "复习次数", "AI 调用", "待处理举报"]);
  assert.deepEqual(env.cardText("新增用户"), ["新增用户", "12", "↑ 33%"]);
  assert.deepEqual(env.cardText("复习次数"), ["复习次数", "620", "→ 持平"]);
  assert.equal(env.card("新增记录").children[2].dataset.trend, "up");
  assert.deepEqual(env.cardText("AI 调用"), ["AI 调用", "55", "新", "失败 3 · 令牌 15.1 万"]);
  assert.equal(env.card("AI 调用").children[2].dataset.trend, "new");
});

test("pending reports: warning style and a jump link only when there is something to handle", async () => {
  const env = setup();
  const loading = env.api.load();
  env.requests[0].resolve(payload());
  await loading;
  const alert = env.card("待处理举报");
  assert.equal(alert.dataset.alert, "true");
  const link = alert.querySelector("a");
  assert.equal(link.textContent, "去处理");
  assert.equal(link.getAttribute("href"), "#admin-reports-heading");
  const heading = env.document.createElement("h3");
  heading.id = "admin-reports-heading";
  env.document.body.append(heading);
  let scrolled = false;
  let focused = false;
  heading.scrollIntoView = () => { scrolled = true; };
  heading.focus = () => { focused = true; };
  const event = new FakeEvent("click");
  link.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.deepEqual([scrolled, focused], [true, true]);

  const again = env.api.load();
  env.requests[1].resolve(payload(7, { pending_reports: 0 }));
  await again;
  assert.equal(env.card("待处理举报").dataset.alert, undefined);
  assert.equal(env.card("待处理举报").querySelector("a"), null);
  assert.deepEqual(env.cardText("待处理举报"), ["待处理举报", "0", "队列已清空"]);
});

test("funnel renders four bars with counts and conversion; zero registrations show an explanation", async () => {
  const env = setup();
  const loading = env.api.load();
  env.requests[0].resolve(payload());
  await loading;
  const rows = env.$("admin-metrics-funnel").children;
  assert.equal(rows.length, 4);
  assert.deepEqual(rows.map((row) => row.querySelector(".am-funnel-count").textContent), ["12", "9", "6", "3"]);
  assert.deepEqual(rows.map((row) => row.querySelector(".am-funnel-rate").textContent), ["", "转化 75%", "转化 66.7%", "转化 50%"]);
  assert.deepEqual(rows.map((row) => row.querySelector(".am-bar").styleValues.width), ["100%", "75%", "50%", "25%"]);
  assert.equal(env.$("admin-metrics-funnel-empty").hidden, true);

  const empty = env.api.load();
  env.requests[1].resolve(payload(7, { funnel: { registered: 0, first_record: 0, first_review: 0, returned_next_day: 0 } }));
  await empty;
  assert.equal(env.$("admin-metrics-funnel").hidden, true);
  assert.equal(env.$("admin-metrics-funnel").children.length, 0);
  assert.equal(env.$("admin-metrics-funnel-empty").hidden, false);
});

test("AI table lists features with tabular numbers, totals, and never mentions money", async () => {
  const env = setup();
  const loading = env.api.load();
  env.requests[0].resolve(payload());
  await loading;
  const rows = env.$("admin-metrics-ai-table").children;
  const cells = (row) => row.children.map((cell) => cell.textContent);
  assert.deepEqual(cells(rows[0]), ["功能", "调用", "失败", "令牌"]);
  assert.deepEqual(cells(rows[1]), ["变式练习", "30", "1", "9 万"]);
  assert.deepEqual(cells(rows[2]), ["拍照识别", "25", "2", "6.1 万"]);
  assert.deepEqual(cells(rows[3]), ["合计", "55", "3", "15.1 万"]);
  assert.equal(rows[1].children[1].className, "am-num");
  assert.equal(env.text("admin-metrics-ai-split"), "输入令牌 12 万 · 输出令牌 3.1 万");
  assert.equal(env.$("admin-metrics-ai-empty").hidden, true);

  const unknown = env.api.load();
  env.requests[1].resolve(payload(7, { ai: { calls: pair(0, 0), failed: pair(0, 0), tokens: { prompt: 0, completion: 0 }, by_feature: [] } }));
  await unknown;
  assert.equal(env.$("admin-metrics-ai-table").hidden, true);
  assert.equal(env.$("admin-metrics-ai-empty").hidden, false);
  assert.deepEqual(env.cardText("AI 调用"), ["AI 调用", "0", "→ 持平", "失败 0 · 令牌 0"]);

  const everything = env.root.textContent;
  assert.doesNotMatch(everything, /[¥￥$]|元\/|USD|RMB/);
});

test("an unknown feature name is shown as is, as text", async () => {
  const env = setup();
  const loading = env.api.load();
  env.requests[0].resolve(payload(7, { ai: { ...payload().ai, by_feature: [{ feature: "<b>x</b>", calls: 1, failed: 0, tokens: 0 }] } }));
  await loading;
  assert.equal(env.$("admin-metrics-ai-table").children[1].children[0].textContent, "<b>x</b>");
});

test("chart: series switch redraws locally with accessible label and a data table", async () => {
  const env = setup();
  const loading = env.api.load();
  env.requests[0].resolve(payload());
  await loading;
  const chart = env.$("admin-metrics-chart");
  assert.match(chart.getAttribute("aria-label"), /^新增用户：最近 7 天每日趋势，合计 \d+，单日最高 \d+$/);
  const dots = () => chart.children.filter((item) => item.getAttribute("class") === "am-dot");
  assert.equal(dots().length, 7);
  assert.match(dots()[3].children[0].textContent, /^2026-09-\d\d：\d+$/);
  const line = () => chart.children.find((item) => item.getAttribute("class") === "am-line").getAttribute("d");
  const before = line();
  env.buttons.reviews.click();
  assert.equal(env.requests.length, 1, "switching the series does not refetch");
  assert.match(chart.getAttribute("aria-label"), /^复习次数：/);
  assert.notEqual(line(), before);
  assert.equal(env.buttons.reviews.getAttribute("aria-pressed"), "true");
  assert.equal(env.buttons.new_users.getAttribute("aria-pressed"), "false");
  const table = env.$("admin-metrics-table");
  assert.equal(table.children.length, 1 + 1 + 7);
  assert.deepEqual(table.children[1].children.map((cell) => cell.textContent), ["日期", "新增用户", "活跃用户", "复习次数"]);
  assert.equal(table.children[2].children[0].getAttribute("scope"), "row");
});

test("chart is measured after the body is visible and uses the container width", async () => {
  const env = setup();
  const wrap = env.$("admin-metrics-chart-wrap");
  let width = 300;
  Object.defineProperty(wrap, "clientWidth", { get: () => (env.$("admin-metrics-body").hidden ? 0 : width) });
  const loading = env.api.load();
  env.requests[0].resolve(payload());
  await loading;
  assert.equal(env.$("admin-metrics-chart").getAttribute("viewBox"), "0 0 300 220");
  width = 500;
  env.buttons.reviews.click();
  assert.equal(env.$("admin-metrics-chart").getAttribute("viewBox"), "0 0 500 220");
});

test("updated-at and the forum / redeem footnote are plain text", async () => {
  const env = setup();
  const loading = env.api.load();
  env.requests[0].resolve(payload());
  await loading;
  assert.equal(env.text("admin-metrics-updated"), "数据更新于 2026-09-28 12:00 UTC");
  assert.match(env.text("admin-metrics-other"), /帖子 4（上一周期 3） · 评论 21（上一周期 17）；兑换码：本期生成 10 · 兑换 6 · 现存未使用 4/);
});

/* ---------------- 时间范围与存储 ---------------- */

test("range switch: one request with the new days, remembered in localStorage", async () => {
  const env = setup();
  const first = env.api.load();
  env.requests[0].resolve(payload());
  await first;
  assert.equal(env.buttons["7"].getAttribute("aria-pressed"), "true");
  env.buttons["30"].click();
  assert.equal(env.requests.length, 2);
  assert.equal(env.requests[1].url, "/api/admin/metrics?days=30");
  assert.equal(env.window.localStorage.getItem("admin-metrics-days"), "30");
  assert.equal(env.buttons["30"].getAttribute("aria-pressed"), "true");
  assert.equal(env.buttons["7"].getAttribute("aria-pressed"), "false");
  assert.equal(env.$("admin-metrics-body").hidden, true, "stale 7-day numbers are not shown under the 30-day label");
  env.requests[1].resolve(payload(30));
  await tick();
  assert.equal(env.$("admin-metrics-table").children.length, 32);
  assert.equal(env.$("admin-metrics-chart").children.filter((item) => item.getAttribute("class") === "am-dot").length, 30);
  env.buttons["30"].click();
  assert.equal(env.requests.length, 2, "clicking the active range does not reload");
});

test("a remembered range is used for the first request; bad values fall back to 7", async () => {
  const stored = setup({ storedDays: 30 });
  stored.api.load();
  assert.equal(stored.requests[0].url, "/api/admin/metrics?days=30");
  const bad = setup({ storedDays: 14 });
  bad.api.load();
  assert.equal(bad.requests[0].url, "/api/admin/metrics?days=7");
});

test("localStorage that throws never breaks loading or switching", async () => {
  const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() {} };
  const env = setup({ extra: { localStorage: broken } });
  const loading = env.api.load();
  assert.equal(env.requests[0].url, "/api/admin/metrics?days=7");
  env.requests[0].resolve(payload());
  await loading;
  env.buttons["30"].click();
  assert.equal(env.requests[1].url, "/api/admin/metrics?days=30");
});

/* ---------------- 权限、失败与迟到响应 ---------------- */

test("non-admins and signed-out visitors never request and stay hidden", async () => {
  for (const user of [{ id: 2, is_admin: false }, { id: 3, is_trial: true, is_admin: false }, null]) {
    const env = setup({ user });
    assert.equal(await env.api.load(), false);
    assert.equal(env.requests.length, 0);
    assert.equal(env.root.hidden, true);
    env.buttons["30"].click();
    assert.equal(env.requests.length, 0, "range buttons cannot trigger a request either");
  }
});

test("failure shows the message with a retry that works", async () => {
  const env = setup();
  const loading = env.api.load();
  env.requests[0].reject(new Error("服务器开小差了"));
  assert.equal(await loading, false);
  assert.equal(env.root.dataset.state, "error");
  assert.equal(env.text("admin-metrics-status"), "服务器开小差了");
  assert.equal(env.$("admin-metrics-retry").hidden, false);
  assert.equal(env.$("admin-metrics-skeleton").hidden, true);
  assert.equal(env.$("admin-metrics-body").hidden, true);
  assert.equal(env.root.getAttribute("aria-busy"), "false");
  env.$("admin-metrics-retry").click();
  assert.equal(env.requests.length, 2);
  assert.equal(env.root.dataset.state, "loading");
  assert.equal(env.$("admin-metrics-retry").hidden, true);
  env.requests[1].resolve(payload());
  await tick();
  assert.equal(env.root.dataset.state, "ready");
  assert.equal(env.text("admin-metrics-status"), "");
  assert.equal(env.cards().length, 6);
});

test("a late answer after logout / account switch is dropped", async () => {
  const env = setup();
  const loading = env.api.load();
  env.state.epoch += 1; // 登出
  env.state.user = { id: 9, is_admin: true }; // 再登录别的管理员
  env.requests[0].resolve(payload());
  assert.equal(await loading, false);
  assert.equal(env.cards().length, 0);
  assert.equal(env.$("admin-metrics-body").hidden, true);

  const second = env.api.load();
  env.state.user = { id: 10, is_admin: false };
  env.requests[1].resolve(payload());
  assert.equal(await second, false);
  assert.equal(env.cards().length, 0);
});

test("a late answer is dropped when the same admin signed out and back in (epoch changed, same id)", async () => {
  const env = setup();
  const loading = env.api.load();
  env.state.epoch += 1;
  env.requests[0].resolve(payload());
  assert.equal(await loading, false);
  assert.equal(env.cards().length, 0);
  assert.equal(env.$("admin-metrics-body").hidden, true);
});

test("a late answer after leaving the admin page or after reset is dropped", async () => {
  const env = setup();
  const loading = env.api.load();
  env.state.view = "home";
  env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view: "home" } }));
  env.requests[0].resolve(payload());
  assert.equal(await loading, false);
  assert.equal(env.cards().length, 0);

  env.state.view = "admin";
  const again = env.api.load();
  env.api.reset();
  env.requests[1].resolve(payload());
  assert.equal(await again, false);
  assert.equal(env.cards().length, 0);
  assert.equal(env.root.hidden, true);
  assert.equal(env.root.dataset.state, "idle");
});

test("a late error after the user moved on does not paint an error", async () => {
  const env = setup();
  const loading = env.api.load();
  env.api.reset();
  env.requests[0].reject(new Error("晚到的错误"));
  assert.equal(await loading, false);
  assert.equal(env.text("admin-metrics-status"), "");
  assert.equal(env.root.dataset.state, "idle");
});

test("only the newest range request may render (older answer arrives last)", async () => {
  const env = setup();
  env.api.load();
  env.buttons["30"].click();
  assert.equal(env.requests.length, 2);
  env.requests[1].resolve(payload(30));
  await tick();
  env.requests[0].resolve(payload(7, { new_users: pair(999, 1) }));
  await tick();
  assert.equal(env.$("admin-metrics-table").children.length, 32);
  assert.deepEqual(env.cardText("新增用户")[1], "12");
});

test("the page re-fetches when it is opened again (view-changed does not reuse stale data)", async () => {
  const env = setup();
  const first = env.api.load();
  env.requests[0].resolve(payload());
  await first;
  env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view: "admin" } }));
  const second = env.api.load();
  assert.equal(env.requests.length, 2);
  env.requests[1].resolve(payload(7, { new_users: pair(1, 1) }));
  await second;
  assert.equal(env.cardText("新增用户")[1], "1");
});
