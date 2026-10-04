"use strict";

/* AN：掌握度页（四档等级、总览列表、选中分区的曲线）与"现在就练 5 条"共用模块的行为测试。
   Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器；覆盖字符串断言测不到的东西：
   等级边界、排序与"优先"标记、选择上限、迟到响应、键盘操作。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const plain = (value) => JSON.parse(JSON.stringify(value));
const POINTS = Array.from({ length: 13 }, (_, index) => `2026-07-${String(10 + index * 7).padStart(2, "0")}`);
function zone(name, mastery, { due = 0, series } = {}) {
  return {
    zone: name, total: 5, due, overdue: 0, at_risk: 0, mastery, change: null,
    series: series || POINTS.map(() => mastery),
  };
}
const report = (zones, alert = null) => ({ today: "2026-10-02", points: POINTS, threshold: 70, overall: 50, zones, alert });

function mount(extra = {}) {
  const started = [];
  const env = load(["practice.js", "mastery.js"], { extra: { FocusReview: { start: (args) => started.push(plain(args)) }, ...extra } });
  env.started = started;
  env.get = (selector) => env.document.querySelector(selector);
  env.get("#mastery-chart").clientWidth = 640;
  return env;
}
async function show(env, zones, alert = null) {
  const loading = env.window.Mastery.load();
  await tick();
  env.respond(env.calls[env.calls.length - 1], 200, report(zones, alert));
  await loading;
  await tick();
}
const rows = (env) => env.get("#mastery-overview").querySelectorAll(".mastery-row");
const rowNames = (env) => rows(env).map((row) => row.dataset.zone);
const rowOf = (env, name) => rows(env).find((row) => row.dataset.zone === name);
const mainOf = (env, name) => rowOf(env, name).querySelector(".mastery-row-main");
const drawn = (env) => env.get("#mastery-chart").querySelectorAll(".mastery-line").map((line) => line.dataset.zone);
const selectedNames = (env) => rows(env).filter((row) => row.classList.contains("is-selected")).map((row) => row.dataset.zone);

/* ---------------- 等级 ---------------- */
test("tier boundaries: 40 / 70 / 90 are inclusive lower bounds", () => {
  const { tierOf, TIERS, TIER_BOUNDS } = mount().window.Mastery;
  assert.deepEqual(plain(TIER_BOUNDS), { familiar: 40, proficient: 70, mastered: 90 });
  assert.deepEqual(plain(TIERS).map((tier) => tier.label), ["陌生", "熟悉", "熟练", "精通"]);
  const expected = [
    [0, 0], [0.1, 0], [39.9, 0], [40, 1], [40.1, 1], [69.9, 1], [70, 2], [89.9, 2], [90, 3], [99.9, 3], [100, 3],
  ];
  for (const [value, tier] of expected) assert.equal(tierOf(value), tier, `${value}%`);
  for (const nothing of [null, undefined, NaN, Infinity, "50", {}]) assert.equal(tierOf(nothing), -1, String(nothing));
});

/* ---------------- 排序与"优先" ---------------- */
test("ranking: lower tier first, then more due first, then original order; no-data rows last", () => {
  const { rankZones } = mount().window.Mastery;
  const order = (zones) => rankZones(zones).map((row) => row.entry.zone);
  assert.deepEqual(order([zone("A", 95), zone("B", 20), zone("C", 75), zone("D", 50)]), ["B", "D", "C", "A"]);
  // 同级：到期多的在前；到期数相同时保持原来的顺序（并列）。
  assert.deepEqual(order([zone("A", 10, { due: 1 }), zone("B", 30, { due: 4 }), zone("C", 20, { due: 4 }), zone("D", 39.9, { due: 0 })]), ["B", "C", "A", "D"]);
  // 没有数据（mastery 为 null）：排最后，哪怕到期很多。
  assert.deepEqual(order([zone("A", null, { due: 9 }), zone("B", 99), zone("C", 5)]), ["C", "B", "A"]);
  // 等级边界上：40 属于熟悉，排在 39.9（陌生）之后。
  assert.deepEqual(order([zone("A", 40), zone("B", 39.9)]), ["B", "A"]);
});

test("priority: at most two, only 陌生 / 熟悉, none when everything is 熟练 or 精通", () => {
  const { rankZones, priorityZones } = mount().window.Mastery;
  const priority = (zones) => plain(priorityZones(rankZones(zones)));
  assert.deepEqual(priority([zone("A", 10), zone("B", 20), zone("C", 30), zone("D", 95)]), ["A", "B"], "never more than two");
  assert.deepEqual(priority([zone("A", 10), zone("B", 75), zone("C", 95)]), ["A"], "the second row is 熟练 → not marked");
  assert.deepEqual(priority([zone("A", 69.9), zone("B", 70), zone("C", 95)]), ["A"], "69.9 is still 熟悉, 70 is not");
  assert.deepEqual(priority([zone("A", 70), zone("B", 85), zone("C", 99)]), [], "all 熟练 / 精通 → no priority");
  assert.deepEqual(priority([zone("A", 91), zone("B", 99)]), []);
  assert.deepEqual(priority([zone("A", null), zone("B", null)]), [], "no data is never priority");
  assert.deepEqual(priority([]), []);
  // 并列：两个同样陌生、同样到期数 → 保持原顺序，两个都标。
  assert.deepEqual(priority([zone("X", 10, { due: 2 }), zone("Y", 10, { due: 2 }), zone("Z", 10, { due: 2 })]), ["X", "Y"]);
});

/* ---------------- 页面：总览列表与主图 ---------------- */
test("overview: one row per zone in priority order with tier text, percentage, due count and the 优先 marker", async () => {
  const env = mount();
  await show(env, [zone("算法", 95), zone("前端", 55, { due: 3 }), zone("后端", 20, { due: 1 }), zone("数据库", 75), zone("系统设计", null, { series: POINTS.map(() => null) })]);
  assert.deepEqual(rowNames(env), ["后端", "前端", "数据库", "算法", "系统设计"]);
  const text = (name, selector) => rowOf(env, name).querySelector(selector)?.textContent;
  assert.equal(text("后端", ".mastery-tier-text"), "陌生");
  assert.equal(text("前端", ".mastery-tier-text"), "熟悉");
  assert.equal(text("数据库", ".mastery-tier-text"), "熟练");
  assert.equal(text("算法", ".mastery-tier-text"), "精通");
  assert.equal(text("系统设计", ".mastery-tier-text"), "还没有记录");
  assert.equal(text("前端", ".mastery-row-pct"), "55%");
  assert.equal(text("系统设计", ".mastery-row-pct"), "—");
  assert.equal(text("前端", ".mastery-row-due"), "有 3 条已到期");
  assert.equal(text("算法", ".mastery-row-due"), "", "nothing due → nothing written");
  // 优先：前两行（陌生、熟悉）才有；等级不只靠颜色：每个等级胶囊带文字 + 阶梯图标。
  assert.deepEqual(rows(env).filter((row) => row.querySelector(".mastery-priority")).map((row) => row.dataset.zone), ["后端", "前端"]);
  assert.equal(rowOf(env, "后端").querySelector(".mastery-priority").textContent, "优先");
  assert.ok(rowOf(env, "后端").querySelector(".mastery-tier .mastery-stair-icon"));
  assert.equal(rowOf(env, "系统设计").querySelector(".mastery-stair-icon"), null, "no data → no icon, text only");
  // 阶梯进度条：四段，已达到的段点亮（陌生 1 段 … 精通 4 段）。
  const lit = (name) => rowOf(env, name).querySelectorAll(".mastery-step.is-on").length;
  assert.deepEqual(["后端", "前端", "数据库", "算法", "系统设计"].map((name) => rowOf(env, name).querySelectorAll(".mastery-step").length), [4, 4, 4, 4, 4]);
  assert.deepEqual(["后端", "前端", "数据库", "算法", "系统设计"].map(lit), [1, 2, 3, 4, 0]);
  // 迷你走势：每行都有；没有数据的分区没有。
  assert.ok(rowOf(env, "算法").querySelector(".mastery-spark"));
  assert.equal(rowOf(env, "系统设计").querySelector(".mastery-spark"), null);
  // 没有数据的行不能选。
  assert.equal(mainOf(env, "系统设计").disabled, true);
  assert.equal(env.get("#mastery-overview-card").hidden, false);
});

test("overview: the sparkline is dropped when fewer than two weeks have data", async () => {
  const env = mount();
  const sparse = POINTS.map((_, index) => (index === 12 ? 50 : null));
  await show(env, [zone("算法", 50, { series: sparse }), zone("前端", 60, { series: POINTS.map((_, index) => (index >= 11 ? 60 : null)) })]);
  assert.equal(rowOf(env, "算法").querySelector(".mastery-spark"), null);
  assert.ok(rowOf(env, "前端").querySelector(".mastery-spark"));
});

test("chart: only the selected zones are drawn; default is the priority rows", async () => {
  const env = mount();
  await show(env, [zone("算法", 95), zone("前端", 55, { due: 3 }), zone("后端", 20), zone("数据库", 30), zone("系统设计", 99)]);
  // 优先 = 后端、数据库（前端 55 排在第三）。
  assert.deepEqual(selectedNames(env).sort(), ["后端", "数据库"].sort());
  assert.deepEqual(drawn(env).sort(), ["后端", "数据库"].sort());
  assert.equal(mainOf(env, "后端").getAttribute("aria-pressed"), "true");
  assert.equal(mainOf(env, "前端").getAttribute("aria-pressed"), "false");
  assert.equal(env.get("#mastery-chart-hint").textContent.includes("后端"), true);
  // 75% 参考线仍在，数据表折叠还在、含全部分区。
  assert.equal(env.get("#mastery-chart").querySelectorAll(".mastery-threshold").length, 1);
  assert.ok(env.get("#mastery-chart").querySelector(".mastery-threshold-label").textContent.includes("70% 快被遗忘线"));
});

test("chart: when nothing needs attention the first row is still drawn so the chart is never empty", async () => {
  const env = mount();
  await show(env, [zone("算法", 95), zone("前端", 75)]);
  assert.equal(rows(env).filter((row) => row.querySelector(".mastery-priority")).length, 0);
  assert.deepEqual(drawn(env), ["前端"], "the lowest tier row (前端 75%) is drawn");
});

test("selection: click adds / removes, max 4 at once, at least 1, state announced in the status line", async () => {
  const env = mount();
  await show(env, ["算法", "前端", "后端", "数据库", "系统设计", "高等数学"].map((name, index) => zone(name, 10 + index * 5)));
  assert.deepEqual(selectedNames(env).sort(), ["前端", "算法"].sort(), "default: top two priority rows");
  const note = () => env.get("#mastery-select-note").textContent;
  mainOf(env, "后端").click();
  mainOf(env, "数据库").click();
  assert.equal(selectedNames(env).length, 4);
  assert.deepEqual(drawn(env).sort(), ["后端", "数据库", "算法", "前端"].sort());
  // 第 5 个：被拒绝，说明原因，选择不变。
  mainOf(env, "系统设计").click();
  assert.equal(selectedNames(env).length, 4);
  assert.equal(mainOf(env, "系统设计").getAttribute("aria-pressed"), "false");
  assert.ok(note().includes("最多同时看 4 个"), note());
  // 取消一个后再选就可以了。
  mainOf(env, "算法").click();
  assert.equal(note().includes("去掉"), true);
  mainOf(env, "系统设计").click();
  assert.deepEqual(selectedNames(env).sort(), ["前端", "后端", "数据库", "系统设计"].sort());
  assert.deepEqual(drawn(env).sort(), ["前端", "后端", "数据库", "系统设计"].sort());
  // 取消到只剩 1 个：最后一个不能取消。
  for (const name of ["前端", "后端", "数据库"]) mainOf(env, name).click();
  assert.deepEqual(selectedNames(env), ["系统设计"]);
  mainOf(env, "系统设计").click();
  assert.deepEqual(selectedNames(env), ["系统设计"]);
  assert.ok(note().includes("至少"), note());
  assert.deepEqual(drawn(env), ["系统设计"]);
  // 每行的无障碍名称写明状态；选中行才显示线型样例（swatch 在 CSS 里按 is-selected 显示）。
  assert.ok(mainOf(env, "系统设计").getAttribute("aria-label").includes("已显示在曲线图里"));
  assert.ok(mainOf(env, "算法").getAttribute("aria-label").includes("按下加进曲线图"));
});

test("selection survives a reload once the user has chosen; an untouched default follows the new data", async () => {
  const env = mount();
  await show(env, [zone("算法", 10), zone("前端", 20), zone("后端", 30)]);
  assert.deepEqual(selectedNames(env).sort(), ["前端", "算法"].sort());
  // 没点过：数据变了，默认选择跟着变（后端变成最需要关注）。
  await show(env, [zone("算法", 90), zone("前端", 95), zone("后端", 5)]);
  assert.deepEqual(selectedNames(env), ["后端"]);
  // 点过之后：重新加载不改动用户的选择；已经不存在的分区会被去掉。
  mainOf(env, "前端").click();
  assert.deepEqual(selectedNames(env).sort(), ["前端", "后端"].sort());
  await show(env, [zone("算法", 1), zone("前端", 95), zone("后端", 5)]);
  assert.deepEqual(selectedNames(env).sort(), ["前端", "后端"].sort());
  await show(env, [zone("算法", 1), zone("前端", 95)]);
  assert.deepEqual(selectedNames(env).sort(), ["前端"], "the vanished zone is dropped");
});

test("data table lists every zone for every week, blank where there was no record", async () => {
  const env = mount();
  const gaps = POINTS.map((_, index) => (index < 3 ? null : 61.5));
  await show(env, [zone("算法", 61.5, { series: gaps }), zone("前端", 40)]);
  const table = env.get("#mastery-table").querySelector("table");
  assert.deepEqual(table.querySelectorAll("thead th").map((cell) => cell.textContent), ["日期", "算法", "前端"]);
  const body = table.querySelectorAll("tbody tr");
  assert.equal(body.length, POINTS.length);
  assert.deepEqual(body[0].querySelectorAll("td").map((cell) => cell.textContent), ["", "40%"]);
  assert.deepEqual(body[12].querySelectorAll("td").map((cell) => cell.textContent), ["61.5%", "40%"]);
  assert.ok(body[12].querySelector("th").textContent.endsWith("（今天）"));
});

test("row actions: 只练这个分区 only when something is due; both use the existing entry points", async () => {
  const env = mount();
  await show(env, [zone("算法", 20, { due: 2 }), zone("前端", 50, { due: 0 })]);
  const buttons = (name) => rowOf(env, name).querySelectorAll(".mastery-zone-actions button").map((button) => button.textContent);
  assert.deepEqual(buttons("算法"), ["只练这个分区", "查看记录"]);
  assert.deepEqual(buttons("前端"), ["查看记录"]);
  rowOf(env, "算法").querySelectorAll(".mastery-zone-actions button")[0].click();
  assert.deepEqual(env.started, [{ zone: "算法" }]);
  rowOf(env, "前端").querySelectorAll(".mastery-zone-actions button")[0].click();
  assert.deepEqual(plain(env.events.filter((event) => event.type === "records:filter")), [{ type: "records:filter", detail: { zone: "前端" } }]);
});

test("the 'about to be forgotten' alert keeps its text and both buttons", async () => {
  const env = mount();
  const alert = { zone: "后端", mastery: 31.5, due: 4, overdue: 2, at_risk: 3 };
  await show(env, [zone("后端", 31.5, { due: 4 })], alert);
  const box = env.get("#mastery-alert");
  assert.equal(box.hidden, false);
  assert.ok(box.textContent.includes("有一科快被遗忘了"));
  assert.ok(box.textContent.includes("「后端」掌握度只剩 31.5%，有 4 条到期，其中 2 条已逾期"));
  const [practise, view] = box.querySelectorAll("button");
  practise.click();
  view.click();
  assert.deepEqual(env.started, [{ zone: "后端" }]);
  assert.equal(env.events.some((event) => event.type === "records:filter" && event.detail.zone === "后端"), true);
  await show(env, [zone("后端", 31.5)], null);
  assert.equal(box.hidden, true);
});

test("an empty report shows the guidance text and no list or chart", async () => {
  const env = mount();
  await show(env, []);
  assert.equal(env.get("#mastery-overview-card").hidden, true);
  assert.equal(env.get("#mastery-chart-card").hidden, true);
  assert.ok(env.get("#mastery-status").textContent.includes("还没有记录"));
});

/* ---------------- 迟到响应 ---------------- */
test("late answers: an older load never overwrites a newer one; sign-out drops everything in flight", async () => {
  const env = mount();
  const slow = env.window.Mastery.load();
  const fast = env.window.Mastery.load();
  await tick();
  assert.equal(env.calls.length, 2);
  env.respond(env.calls[1], 200, report([zone("新分区", 20)]));
  await fast;
  env.respond(env.calls[0], 200, report([zone("旧分区", 20)]));
  await slow;
  await tick();
  assert.deepEqual(rowNames(env), ["新分区"]);

  const pending = env.window.Mastery.load();
  await tick();
  env.window.Mastery.reset(); // 登出
  env.respond(env.calls[2], 200, report([zone("上一个账号的分区", 20)]));
  await pending;
  await tick();
  assert.deepEqual(rowNames(env), []);
  assert.equal(env.get("#mastery-overview-card").hidden, true);
  assert.equal(env.get("#mastery-chart").children.length, 0);
  // 换账号后：上一个账号的选择不带过来。
  await show(env, [zone("甲", 10), zone("乙", 20)]);
  assert.deepEqual(selectedNames(env).sort(), ["乙", "甲"].sort());
});

test("a failed load shows the retry button and hides the list", async () => {
  const env = mount();
  const loading = env.window.Mastery.load();
  await tick();
  env.respond(env.calls[0], 500, {});
  await loading;
  assert.equal(env.get("#mastery-retry").hidden, false);
  assert.equal(env.get("#mastery-overview-card").hidden, true);
  assert.ok(env.get("#mastery-status").textContent.includes("暂时无法读取"));
});

/* ---------------- 键盘 ---------------- */
test("keyboard: rows are real buttons, Up / Down move between them, the chart answers arrow keys", async () => {
  const env = mount();
  await show(env, [zone("算法", 10), zone("前端", 20), zone("后端", 30)]);
  const mains = rows(env).map((row) => row.querySelector(".mastery-row-main"));
  assert.ok(mains.every((main) => main.tagName === "BUTTON" && main.type === "button"));
  const press = (main, key) => main.dispatchEvent(new FakeEvent("keydown", { props: { key } }));
  mains[0].focus();
  press(mains[0], "ArrowDown");
  assert.equal(env.document.activeElement, mains[1]);
  press(mains[1], "ArrowDown");
  press(mains[2], "ArrowDown"); // 到底了：不动
  assert.equal(env.document.activeElement, mains[2]);
  press(mains[2], "ArrowUp");
  assert.equal(env.document.activeElement, mains[1]);
  press(mains[0], "ArrowUp"); // 到顶了：不动，不报错
  press(mains[1], "Tab"); // 其它键不拦截
  assert.equal(env.document.activeElement, mains[1]);

  const chart = env.get("#mastery-chart");
  const summary = env.get("#mastery-chart-summary");
  const key = (name) => { const event = new FakeEvent("keydown", { props: { key: name } }); chart.onkeydown(event); return event; };
  assert.equal(key("ArrowLeft").defaultPrevented, true);
  assert.ok(summary.textContent.includes("月"), summary.textContent);
  const firstSummary = summary.textContent;
  key("Home");
  assert.notEqual(summary.textContent, firstSummary);
  key("End");
  assert.equal(summary.textContent, firstSummary, "End goes back to the latest week");
  assert.equal(key("a").defaultPrevented, false);
});

/* ---------------- 共用模块 PracticeNow ---------------- */
test("PracticeNow.pickDueIds: due only, earliest first, ties by id, at most 5, scoped by zone / ids", () => {
  const { PracticeNow } = mount().window;
  const item = (id, zone, day) => ({ id, zone, due_date: day });
  const items = [item(5, "算法", "2026-10-02"), item(4, "算法", "2026-10-01"), item(3, "算法", "2026-10-01"), item(2, "前端", "2026-09-01"), item(1, "算法", "2026-10-03"), item(6, "算法", "2026-09-30"), item(7, "算法", "2026-09-29"), item(8, "算法", "2026-09-28")];
  assert.equal(PracticeNow.LIMIT, 5);
  assert.deepEqual(plain(PracticeNow.pickDueIds(items, { today: "2026-10-02" })), [2, 8, 7, 6, 3]);
  assert.deepEqual(plain(PracticeNow.pickDueIds(items, { zone: "算法", today: "2026-10-02" })), [8, 7, 6, 3, 4]);
  assert.deepEqual(plain(PracticeNow.pickDueIds(items, { ids: [4, 3, 1, 99], today: "2026-10-02" })), [3, 4], "1 is not due yet, 99 does not exist");
  assert.deepEqual(plain(PracticeNow.pickDueIds(items, { zone: "前端", ids: [3], today: "2026-10-02" })), [], "zone and ids intersect");
  assert.deepEqual(plain(PracticeNow.pickDueIds(items, { today: "2026-10-02", limit: 2 })), [2, 8]);
  // 今天恰好到期算到期；少于 5 条就是全部。
  assert.deepEqual(plain(PracticeNow.pickDueIds([item(1, "a", "2026-10-02")], { today: "2026-10-02" })), [1]);
  assert.deepEqual(plain(PracticeNow.pickDueIds([item(1, "a", "2026-10-03")], { today: "2026-10-02" })), []);
  // 专题成员用 mistake_id；重复的只取一次；坏数据（没有日期、id 不是整数、null）被跳过。
  const members = [{ mistake_id: 9, due_date: "2026-10-01" }, { mistake_id: 9, due_date: "2026-10-01" }, { mistake_id: "x", due_date: "2026-10-01" }, { mistake_id: 10, due_date: null }, { mistake_id: 11 }, null, { mistake_id: 12, due_date: "10/01" }];
  assert.deepEqual(plain(PracticeNow.pickDueIds(members, { today: "2026-10-02" })), [9]);
  assert.deepEqual(plain(PracticeNow.pickDueIds("nope", {})), []);
  // 不给 today：信任调用方只传了到期项，仍按到期日排序。
  assert.deepEqual(plain(PracticeNow.pickDueIds([item(2, "a", "2026-10-03"), item(1, "a", "2026-10-04")], {})), [2, 1]);
});

test("PracticeNow.fill: button label follows the count; empty state is disabled with a note and an optional records link", () => {
  const env = mount();
  const { PracticeNow } = env.window;
  const box = env.document.createElement("div");
  PracticeNow.fill(box, { ids: [1, 2, 3, 4, 5, 6, 7] });
  const [button] = box.querySelectorAll("button");
  assert.equal(button.textContent, "现在就练 5 条", "never more than 5");
  button.click();
  assert.deepEqual(env.started, [{ ids: [1, 2, 3, 4, 5] }]);
  PracticeNow.fill(box, { ids: [1, 2], guard: () => false });
  box.querySelector("button").click();
  assert.equal(env.started.length, 1, "a failing guard (sign-out / page change) blocks the start");

  PracticeNow.fill(box, { ids: [], emptyText: "这一块今天没有要复习的", link: { zone: "前端" } });
  const [disabled, link] = box.querySelectorAll("button");
  assert.equal(disabled.disabled, true);
  assert.equal(link.textContent, "去看看这一块的记录");
  assert.equal(box.querySelector(".practice-now-note").textContent, "这一块今天没有要复习的");
  link.click();
  assert.deepEqual(plain(env.events.at(-1)), { type: "records:filter", detail: { zone: "前端" } });
  PracticeNow.fill(box, { ids: [] });
  assert.equal(box.querySelectorAll("button").length, 1, "no link unless asked for");
});
