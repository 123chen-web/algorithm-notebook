"use strict";

/* 总览"趋势"区：trendModel 纯函数 + 渲染状态 + 迟到响应（Node 内置测试运行器 + 假浏览器）。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});
const plain = (value) => JSON.parse(JSON.stringify(value));

function summary(overrides = {}) {
  const days = overrides.days ?? 30;
  const daily = Array.from({ length: days }, (_, index) => ({
    date: `2026-09-${String((index % 28) + 1).padStart(2, "0")}`, count: index % 4,
  }));
  const forecast = Array.from({ length: 14 }, (_, index) => ({
    date: `2026-10-${String(index + 4).padStart(2, "0")}`, due: index === 0 ? 25 : index % 3,
  }));
  const weekly = Array.from({ length: 8 }, (_, index) => ({
    week_start: `2026-08-${String(10 + index).padStart(2, "0")}`, reviews: 9, passed: 8, rate: index === 3 ? null : 0.889,
  }));
  return {
    timezone: "Asia/Shanghai", today: "2026-10-04", days,
    due: { today: 25, overdue: 22 }, streak_days: 21,
    reviews: { current: 92, previous: 80 },
    retention: {
      current: { reviews: 70, passed: 61, rate: 0.871 },
      previous: { reviews: 60, passed: 53, rate: 0.883 },
      weekly,
    },
    forecast, daily_reviews: daily,
    ...overrides,
  };
}

function mountDom(env) {
  const { document } = env;
  const make = (tag, id, parent = document.body) => {
    const element = document.createElement(tag);
    if (id) element.id = id;
    parent.append(element);
    return element;
  };
  make("section", "home-trend");
  const range = make("div", "home-trend-range");
  for (const days of [7, 30, 90]) {
    const button = document.createElement("button");
    button.dataset.days = String(days);
    button.setAttribute("aria-pressed", "false");
    range.append(button);
  }
  for (const id of ["home-trend-skeleton", "home-trend-error", "home-trend-error-text", "home-trend-body", "home-trend-metrics",
    "home-trend-panel", "home-trend-caption", "home-trend-chart", "home-trend-readout", "home-trend-how", "home-trend-table-body"]) make("div", id);
  make("button", "home-trend-retry");
  // Overview.reset() 也会清理页面其余部分，给它最小的一组固定节点。
  const wrap = make("div");
  wrap.className = "tile-count-wrap";
  make("span", "home-due-count", wrap);
  const tile = make("section", "tile-review");
  const stamp = document.createElement("span");
  stamp.className = "ov-stamp";
  tile.append(stamp);
  return range;
}
function boot(extra) {
  const env = load(["overview.js"], extra);
  const range = mountDom(env);
  const $ = (id) => env.document.querySelector(`#${id}`);
  return { env, range, $, O: env.window.Overview };
}
const tabs = ($) => $("home-trend-metrics").querySelectorAll('[role="tab"]');

/* ---------------- trendModel ---------------- */
test("trendModel: metric copy, up/down/flat/new and null retention", () => {
  const { O } = boot();
  const model = plain(O.trendModel(summary()));
  const [due, streak, reviews, retention] = model.metrics;
  assert.deepEqual([due.value, due.sub], ["25", "其中逾期 22"]);
  assert.deepEqual([streak.value, streak.unit], ["21", "天"]);
  assert.equal(reviews.label, "近 30 天复习");
  assert.deepEqual([reviews.value, reviews.trend.dir, reviews.trend.text], ["92", "up", "↗ +15%"]);
  assert.equal(retention.value, "87%");
  assert.deepEqual([retention.trend.dir, retention.trend.text], ["down", "↘ −1.2 个点"]);

  const down = plain(O.trendModel(summary({ reviews: { current: 40, previous: 80 } }))).metrics[2];
  assert.deepEqual([down.trend.dir, down.trend.text], ["down", "↘ −50%"]);
  const fresh = plain(O.trendModel(summary({ reviews: { current: 5, previous: 0 } }))).metrics[2];
  assert.deepEqual([fresh.trend.dir, fresh.trend.text], ["new", "新"]);
  assert.equal(/%/.test(fresh.trend.text), false, "no percentage when previous is 0");
  const flat = plain(O.trendModel(summary({ reviews: { current: 0, previous: 0 } }))).metrics[2];
  assert.equal(flat.trend.dir, "flat");
  const same = plain(O.trendModel(summary({ reviews: { current: 10, previous: 10 } }))).metrics[2];
  assert.equal(same.trend.dir, "flat");

  const empty = summary();
  empty.retention.current = { reviews: 0, passed: 0, rate: null };
  const nullModel = plain(O.trendModel(empty)).metrics[3];
  assert.equal(nullModel.value, "—");
  assert.equal(nullModel.trend, null);
  assert.match(nullModel.sub, /非首次复习/);
  const noOverdue = plain(O.trendModel(summary({ due: { today: 3, overdue: 0 } }))).metrics[0];
  assert.equal(noOverdue.sub, "没有逾期");
});

test("trendModel: chart data and scales use the same maxima that are drawn", () => {
  const { O } = boot();
  const model = plain(O.trendModel(summary()));
  const bars = model.charts.due;
  assert.deepEqual(bars.items.slice(0, 4).map((item) => item.label), ["今天", "明", "后", "10-07"]);
  assert.equal(bars.items[0].today, true);
  assert.deepEqual(bars.scale, { max: 25, ticks: [0, 5, 10, 15, 20, 25] });
  assert.ok(bars.scale.ticks.every((tick) => tick <= bars.scale.max));
  assert.deepEqual(plain(O.trendModel(summary({ forecast: summary().forecast.map((item) => ({ ...item, due: 0 })) })).charts.due.scale), { max: 1, ticks: [0, 1] });
  assert.equal(model.charts.retention.target, 0.85);
  assert.deepEqual(model.charts.retention.scale.ticks, [0, 0.25, 0.5, 0.75, 1]);
  assert.equal(model.charts.retention.items[3].value, null);
  assert.equal(model.charts.reviews.items.length, 30);
  assert.equal(model.charts.reviews.table.rows.length, 30);
  const empty = plain(O.trendModel(summary({ daily_reviews: summary().daily_reviews.map((item) => ({ ...item, count: 0 })) })));
  assert.equal(empty.charts.reviews.empty, true);
  assert.equal(empty.charts.streak.empty, true);
  assert.equal(empty.charts.due.empty, false);
});

/* ---------------- 渲染状态 ---------------- */
test("render: loading skeleton, then ready; error shows retry; retry reloads", async () => {
  const { env, $, O } = boot();
  const api = (path) => { env.calls.push({ path }); return new Promise((resolve, reject) => { env.pending.push({ resolve, reject, path }); }); };
  env.pending = [];
  const mounted = O.mountTrend({ api });
  assert.equal($("home-trend").dataset.state, "loading");
  assert.equal($("home-trend-skeleton").hidden, false);
  assert.equal($("home-trend").getAttribute("aria-busy"), "true");
  assert.equal(env.pending[0].path, "/api/stats/summary?days=30");
  env.pending[0].reject(new Error("boom"));
  await mounted;
  assert.equal($("home-trend").dataset.state, "error");
  assert.equal($("home-trend-error").hidden, false);
  assert.equal($("home-trend-body").hidden, true);

  $("home-trend-retry").click();
  await tick();
  assert.equal(env.pending.length, 2);
  env.pending[1].resolve(summary());
  await tick();
  assert.equal($("home-trend").dataset.state, "ready");
  assert.equal($("home-trend-body").hidden, false);
  assert.equal($("home-trend-error").hidden, true);
  assert.equal($("home-trend").getAttribute("aria-busy"), null);
  assert.equal(tabs($).length, 4);
});

test("render: empty data shows an explanation instead of an empty chart", async () => {
  const { $, O } = boot();
  const data = summary({ daily_reviews: summary().daily_reviews.map((item) => ({ ...item, count: 0 })) });
  O.renderTrend(data);
  O.mountTrend; // 模块已加载
  tabs($)[2].click();
  assert.equal($("home-trend-chart").querySelector("svg"), null);
  assert.match($("home-trend-chart").textContent, /还没有复习记录/);
  tabs($)[3].click();
  assert.ok($("home-trend-chart").querySelector("svg"), "retention has data");
});

test("render: switching metrics swaps the chart and the table; labels and values are in-scale", () => {
  const { $, O } = boot();
  O.renderTrend(summary());
  const first = $("home-trend-chart").querySelector("svg");
  assert.equal($("home-trend-panel").dataset.chart, "due");
  assert.equal(first.querySelectorAll(".ov-ch-bar").length, 10, "zero days draw a thin line, not a bar");
  assert.equal(first.querySelectorAll(".ov-ch-zero").length, 4);
  assert.ok(first.querySelector(".ov-ch-bar.is-today"));
  assert.match(first.textContent, /含逾期/);
  assert.match($("home-trend-caption").textContent, /未来 14 天/);

  tabs($)[3].click();
  const line = $("home-trend-chart").querySelector("svg");
  assert.equal($("home-trend-panel").dataset.chart, "retention");
  assert.match(line.textContent, /目标线 85%/);
  assert.equal(line.querySelectorAll(".ov-ch-line").length, 2, "a null week breaks the line into two segments");
  assert.equal(line.querySelectorAll(".ov-ch-dot").length, 7);
  assert.equal($("home-trend-table-body").querySelectorAll("tbody tr").length, 8);

  tabs($)[2].click();
  assert.ok($("home-trend-chart").querySelector(".ov-ch-area"));
  tabs($)[1].click();
  assert.ok($("home-trend-chart").querySelectorAll(".ov-ch-cell").length === 30);
  assert.equal(tabs($).filter((tab) => tab.getAttribute("aria-selected") === "true").length, 1);
});

test("render: keyboard arrows move the selection and wrap; tabindex roves", () => {
  const { $, O } = boot();
  O.renderTrend(summary());
  const press = (key) => {
    const current = tabs($).find((tab) => tab.getAttribute("aria-selected") === "true");
    const event = new FakeEvent("keydown", { bubbles: true, props: { key } });
    current.dispatchEvent(event);
    return event;
  };
  const selected = () => tabs($).findIndex((tab) => tab.getAttribute("aria-selected") === "true");
  assert.equal(selected(), 0);
  assert.equal(press("ArrowRight").defaultPrevented, true);
  assert.equal(selected(), 1);
  press("ArrowLeft"); press("ArrowLeft");
  assert.equal(selected(), 3, "wraps to the last tab");
  press("Home");
  assert.equal(selected(), 0);
  press("End");
  assert.equal(selected(), 3);
  assert.equal(press("a").defaultPrevented, false);
  assert.deepEqual(tabs($).map((tab) => tab.tabIndex), [-1, -1, -1, 0]);
  assert.equal(tabs($)[3].getAttribute("role"), "tab");
});

test("render: pointer hover shows the value of that day in the readout", () => {
  const { $, O } = boot();
  O.renderTrend(summary());
  tabs($)[2].click();
  const svg = $("home-trend-chart").querySelector("svg");
  assert.match($("home-trend-readout").textContent, /复习 \d 次/, "defaults to the latest day");
  const event = new FakeEvent("pointermove", { props: { clientX: 0 } });
  svg.dispatchEvent(event);
  assert.match($("home-trend-readout").textContent, /^2026-09-01 · 复习 0 次$/);
});

test("range: choice is stored, reloads with the new days; storage failure is tolerated", async () => {
  const { env, range, $, O } = boot();
  const asked = [];
  const api = (path) => { asked.push(path); return Promise.resolve(summary({ days: Number(path.split("=")[1]) })); };
  await O.mountTrend({ api });
  assert.deepEqual(asked, ["/api/stats/summary?days=30"]);
  range.querySelectorAll("button")[2].click();
  await tick();
  assert.equal(asked[1], "/api/stats/summary?days=90");
  assert.equal(env.window.localStorage.getItem("home-trend-days"), "90");
  assert.equal(range.querySelectorAll("button")[2].getAttribute("aria-pressed"), "true");
  assert.equal(range.querySelectorAll("button")[1].getAttribute("aria-pressed"), "false");
  assert.match(tabs($)[2].textContent, /近 90 天复习/);
  range.querySelectorAll("button")[2].click();
  await tick();
  assert.equal(asked.length, 2, "same range does not refetch");

  const broken = boot({ extra: { localStorage: { getItem() { throw new Error("denied"); }, setItem() { throw new Error("denied"); } } } });
  await broken.O.mountTrend({ api });
  broken.range.querySelectorAll("button")[0].click();
  await tick();
  assert.equal(asked.at(-1), "/api/stats/summary?days=7");
});

test("range: a stored value is used on first mount; junk is ignored", async () => {
  const stored = boot();
  stored.env.window.localStorage.setItem("home-trend-days", "7");
  const asked = [];
  await stored.O.mountTrend({ api: (path) => { asked.push(path); return Promise.resolve(summary({ days: 7 })); } });
  assert.deepEqual(asked, ["/api/stats/summary?days=7"]);
  const junk = boot();
  junk.env.window.localStorage.setItem("home-trend-days", "14");
  await junk.O.mountTrend({ api: (path) => { asked.push(path); return Promise.resolve(summary()); } });
  assert.equal(asked.at(-1), "/api/stats/summary?days=30");
});

/* ---------------- 迟到响应 ---------------- */
test("late responses: an older request and a pre-reset (sign-out) request never render", async () => {
  const { $, O } = boot();
  const pending = [];
  const api = (path) => new Promise((resolve) => pending.push({ resolve, path }));
  const first = O.mountTrend({ api });
  const second = O.mountTrend({ api });
  pending[1].resolve(summary({ reviews: { current: 7, previous: 7 } }));
  await second;
  pending[0].resolve(summary({ reviews: { current: 999, previous: 1 } }));
  await first;
  assert.match($("home-trend-metrics").textContent, /7/);
  assert.equal($("home-trend-metrics").textContent.includes("999"), false);

  const third = O.mountTrend({ api });
  O.reset(); // 登出 / 切账号
  pending[2].resolve(summary({ streak_days: 888 }));
  await third;
  assert.equal($("home-trend-metrics").textContent, "");
  assert.equal($("home-trend").dataset.state, "loading");
});

test("late failures are dropped too", async () => {
  const { $, O } = boot();
  let fail;
  const stuck = O.mountTrend({ api: () => new Promise((_, reject) => { fail = reject; }) });
  O.reset();
  fail(new Error("late"));
  await stuck;
  assert.equal($("home-trend-error").hidden, true);
});
