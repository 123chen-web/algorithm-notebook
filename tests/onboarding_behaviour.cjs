"use strict";

/* 首次清单 / 示例数据 / 空状态下一步 的行为测试（Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器）。
   覆盖字符串断言测不到的东西：步骤推导、显示规则、串行请求、失败重试、迟到响应、登出再登录。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const PREFIX = "【示例】";
const plain = (value) => JSON.parse(JSON.stringify(value));

/* ---------------- 测试装置 ---------------- */
function setup({ user = { id: 7, is_trial: false }, storage } = {}) {
  const env = load(["onboarding.js"]);
  if (storage) env.window.localStorage = storage;
  const calls = [];
  const api = (path, options = {}) => {
    const call = { path, options, ...deferred() };
    calls.push(call);
    return call.promise;
  };
  const Onboarding = env.window.Onboarding;
  Onboarding.configure({ api });
  const home = env.document.createElement("section");
  home.id = "home-page";
  env.document.body.append(home);
  const fire = (type, detail) => env.document.dispatchEvent(new FakeEvent(type, { detail }));
  const eventsOf = (type) => env.events.filter((event) => event.type === type);
  const ok = (call, body) => call.resolve(body);
  const fail = (call, message, status = 500) => call.reject(Object.assign(new Error(message), { status }));
  const card = () => home.querySelector("#onboarding-card");
  const buttons = (root = home) => root.querySelectorAll("button");
  const buttonByText = (text, root = home) => buttons(root).find((item) => item.textContent === text) || null;
  const rendered = async (total, items = null, who = user) => {
    fire("app:view-changed", { view: "home" });
    fire("app:home-rendered", { overview: { total_mistakes: total }, user: who });
    await tick();
    if (items) {
      assert.equal(calls.at(-1).path, "/api/mistakes?due_only=false");
      ok(calls.at(-1), { items });
      await tick();
    }
  };
  return { env, calls, Onboarding, home, fire, eventsOf, ok, fail, card, buttons, buttonByText, rendered, user };
}
const rec = (id, title, reviewed = false, problemId = id + 100) => ({
  id, problem_id: problemId, title, last_reviewed_at: reviewed ? "2026-10-01T08:00:00+00:00" : null,
});
const stepState = (card) => card.querySelectorAll("li").map((row) => ({
  key: row.dataset.step, done: row.classList.contains("is-done"), current: row.classList.contains("is-current"),
}));
const navigations = (ctx) => ctx.eventsOf("app:navigate").map((event) => event.detail.view);

/* ---------------- 纯函数：步骤推导与显示规则 ---------------- */
test("steps: each of the eight combinations is derived from data, not stored", () => {
  const { Onboarding } = setup();
  const reviewed = [{ reviewed: true }];
  const fresh = [{ reviewed: false }];
  const cases = [
    [{ total: 0, records: [], weaknessSeen: false }, [false, false, false]],
    [{ total: 3, records: fresh, weaknessSeen: false }, [true, false, false]],
    [{ total: 3, records: reviewed, weaknessSeen: false }, [true, true, false]],
    [{ total: 0, records: [], weaknessSeen: true }, [false, false, true]],
    [{ total: 3, records: fresh, weaknessSeen: true }, [true, false, true]],
    [{ total: 3, records: reviewed, weaknessSeen: true }, [true, true, true]],
    [{ total: 3, records: null, weaknessSeen: false }, [true, false, false]],
    [{ total: undefined, records: reviewed, weaknessSeen: undefined }, [false, true, false]],
  ];
  for (const [input, expected] of cases) assert.deepEqual(plain(Onboarding.deriveSteps(input)), expected, JSON.stringify(input));
});

test("mode: old users, finished users, dismissed users and the one-time celebration", () => {
  const { Onboarding } = setup();
  const mode = (extra) => Onboarding.decideMode({
    total: 3, steps: [true, false, false], dismissed: false, sawIncomplete: false, demoCount: 0, flashText: false, ...extra,
  });
  assert.equal(mode({}), "checklist");
  assert.equal(mode({ total: 10 }), "none", "10 records is an old user");
  assert.equal(mode({ total: 9 }), "checklist");
  assert.equal(mode({ total: 40, demoCount: 2 }), "demo", "an old user can still clear the samples");
  assert.equal(mode({ dismissed: true }), "none");
  assert.equal(mode({ dismissed: true, demoCount: 1 }), "demo");
  assert.equal(mode({ steps: [true, true, true] }), "none", "never saw it unfinished: no celebration for veterans");
  assert.equal(mode({ steps: [true, true, true], sawIncomplete: true }), "celebrate");
  assert.equal(mode({ steps: [true, true, true], sawIncomplete: true, dismissed: true }), "none");
});

/* ---------------- 显示与步骤 ---------------- */
test("card: a brand-new user sees it first in #home-page with step one highlighted, and no list request", async () => {
  const ctx = setup();
  await ctx.rendered(0);
  assert.equal(ctx.calls.length, 0, "no records means nothing to look up");
  const card = ctx.card();
  assert.ok(card);
  assert.equal(ctx.home.firstChild, card, "inserted at the very top");
  assert.match(card.textContent, /开始之前 · 3 步走完第一轮/);
  assert.match(card.querySelector(".ob-ring-label").textContent, /0 \/ 3/);
  assert.deepEqual(plain(stepState(card)), [
    { key: "record", done: false, current: true },
    { key: "review", done: false, current: false },
    { key: "weakness", done: false, current: false },
  ]);
  const go = card.querySelectorAll("button").filter((item) => item.textContent === "去做");
  assert.equal(go.length, 1, "only the first open step has the button");
  go[0].click();
  assert.deepEqual(navigations(ctx), ["new"]);
});

test("card: step two is current once there is a record, step three once something was reviewed", async () => {
  const ctx = setup();
  await ctx.rendered(2, [rec(1, "自己的题"), rec(2, "另一道")]);
  assert.deepEqual(plain(stepState(ctx.card()).map((step) => [step.done, step.current])), [[true, false], [false, true], [false, false]]);
  assert.match(ctx.card().querySelector(".ob-ring-label").textContent, /1 \/ 3/);
  ctx.buttonByText("去做", ctx.card()).click();
  assert.deepEqual(navigations(ctx), ["today"]);

  await ctx.rendered(2, [rec(1, "自己的题", true), rec(2, "另一道")]);
  assert.deepEqual(plain(stepState(ctx.card()).map((step) => [step.done, step.current])), [[true, false], [true, false], [false, true]]);
  assert.equal(ctx.home.querySelectorAll("#onboarding-card").length, 1, "rendering again reuses the same card");
  ctx.buttonByText("去做", ctx.card()).click();
  assert.deepEqual(navigations(ctx), ["today", "insights"]);
});

test("card: step three is done only after the weakness page was opened by this user", async () => {
  const ctx = setup();
  await ctx.rendered(2, [rec(1, "题"), rec(2, "题二")]);
  assert.equal(stepState(ctx.card())[2].done, false);
  ctx.fire("app:view-changed", { view: "insights" });
  assert.equal(ctx.env.window.localStorage.getItem("onboarding-weakness-seen:7"), "1");
  assert.equal(ctx.env.window.localStorage.getItem("onboarding-weakness-seen:8"), null, "keyed by user id");
  await ctx.rendered(2, [rec(1, "题"), rec(2, "题二")]);
  assert.equal(stepState(ctx.card())[2].done, true);
  assert.equal(stepState(ctx.card())[1].current, true, "the first open step is still step two");
});

test("card: veterans with 10 or more records get no card and no list request", async () => {
  const ctx = setup();
  await ctx.rendered(10);
  assert.equal(ctx.card(), null);
  assert.equal(ctx.calls.length, 0);
  await ctx.rendered(250);
  assert.equal(ctx.card(), null);
  assert.equal(ctx.calls.length, 0);
});

test("card: a user who already finished all three (and never saw it unfinished) gets nothing", async () => {
  const ctx = setup();
  ctx.env.window.localStorage.setItem("onboarding-weakness-seen:7", "1");
  await ctx.rendered(3, [rec(1, "a", true), rec(2, "b"), rec(3, "c")]);
  assert.equal(ctx.card(), null);
});

test("card: finishing the third step shows the celebration once, then it collapses and never returns", async () => {
  const ctx = setup();
  await ctx.rendered(2, [rec(1, "a", true), rec(2, "b")]);
  assert.equal(ctx.card().dataset.mode, "checklist");
  ctx.fire("app:view-changed", { view: "insights" });
  await ctx.rendered(2, [rec(1, "a", true), rec(2, "b")]);
  assert.equal(ctx.card().dataset.mode, "celebrate");
  assert.match(ctx.card().textContent, /第一轮完成/);
  assert.equal(ctx.env.window.localStorage.getItem("onboarding-dismissed:7"), "1", "shown once");
  assert.equal(ctx.buttonByText("不再显示", ctx.card()), null);

  await ctx.rendered(2, [rec(1, "a", true), rec(2, "b")]);
  assert.equal(ctx.card(), null, "the next visit shows nothing");
});

test("card: 'dismiss' hides it for this user only", async () => {
  const ctx = setup();
  await ctx.rendered(1, [rec(1, "a")]);
  ctx.buttonByText("不再显示", ctx.card()).click();
  assert.equal(ctx.card(), null);
  assert.equal(ctx.env.window.localStorage.getItem("onboarding-dismissed:7"), "1");
  await ctx.rendered(1, [rec(1, "a")]);
  assert.equal(ctx.card(), null, "stays hidden");

  ctx.Onboarding.reset();
  await ctx.rendered(1, [rec(1, "a")], { id: 8, is_trial: false });
  assert.ok(ctx.card(), "another account is unaffected");
});

test("card: a trial account sees the checklist and every 'go' button works", async () => {
  const ctx = setup({ user: { id: 3, is_trial: true } });
  await ctx.rendered(0);
  assert.ok(ctx.card());
  ctx.buttonByText("去做", ctx.card()).click();
  assert.deepEqual(navigations(ctx), ["new"]);
  await ctx.rendered(2, [rec(1, "a", true), rec(2, "b")]);
  const go = ctx.buttonByText("去做", ctx.card());
  assert.ok(go && !go.disabled);
  go.click();
  assert.equal(navigations(ctx).at(-1), "insights");
});

test("card: a failed record lookup removes the card instead of guessing", async () => {
  const ctx = setup();
  ctx.fire("app:home-rendered", { overview: { total_mistakes: 2 }, user: ctx.user });
  await tick();
  ctx.fail(ctx.calls[0], "boom");
  await tick();
  assert.equal(ctx.card(), null);
});

test("card: localStorage that throws never breaks rendering or clicking", async () => {
  const angry = {
    getItem() { throw new Error("denied"); },
    setItem() { throw new Error("denied"); },
    removeItem() { throw new Error("denied"); },
  };
  const ctx = setup({ storage: angry });
  await ctx.rendered(2, [rec(1, "a", true), rec(2, "b")]);
  assert.ok(ctx.card(), "treated as unseen / not dismissed");
  assert.equal(stepState(ctx.card())[2].done, false);
  ctx.buttonByText("不再显示", ctx.card()).click(); // setItem 抛异常也不崩
  assert.ok(ctx.card(), "the dismissal could not be saved, so the card simply stays");
  ctx.fire("app:view-changed", { view: "insights" }); // 同上：写不进去也不崩
});

/* ---------------- 载入示例 ---------------- */
test("demos: four samples, one per zone, all with the prefix and a real reason", () => {
  const { Onboarding } = setup();
  const demos = plain(Onboarding.demos());
  assert.equal(demos.length, 4);
  assert.deepEqual(demos.map((demo) => demo.zone), ["算法", "数据库", "高等数学", "概率统计"]);
  for (const demo of demos) {
    assert.ok(demo.title.startsWith(PREFIX));
    assert.ok(demo.code.trim().length > 20 && demo.thinking.trim().length > 20 && demo.mistakes[0].trim().length > 10);
    assert.ok(!/示例示例/.test(JSON.stringify(demo)));
  }
  assert.equal(new Set(demos.map((demo) => demo.title)).size, 4);
});

test("load: only offered to a user with no records at all", async () => {
  const ctx = setup();
  await ctx.rendered(0);
  assert.ok(ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()));
  await ctx.rendered(1, [rec(1, "a")]);
  assert.equal(ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()), null);
  assert.doesNotMatch(ctx.card().textContent, /示例记录也会计入统计/, "no sample controls, so no note about them");
});

test("load: runs strictly one request at a time, shows progress, ignores repeated clicks", async () => {
  const ctx = setup();
  await ctx.rendered(0);
  const start = ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card());
  start.click();
  start.click(); // 重复点击
  ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card())?.click();
  await tick();
  assert.equal(ctx.calls.length, 1, "first a check that there really are no records");
  assert.equal(ctx.calls[0].path, "/api/mistakes?due_only=false");
  ctx.ok(ctx.calls[0], { items: [] });
  await tick();

  const titles = [];
  for (let index = 0; index < 4; index += 1) {
    assert.equal(ctx.calls.length, 2 + index, "the next request waits for the previous one");
    const call = ctx.calls[1 + index];
    assert.equal(call.path, "/api/problems");
    assert.equal(call.options.method, "POST");
    const body = JSON.parse(call.options.body);
    assert.ok(body.title.startsWith(PREFIX));
    assert.equal(body.mistakes.length, 1);
    titles.push(body.title);
    const busy = ctx.buttonByText(`正在载入 ${index + 1} / 4`, ctx.card());
    assert.ok(busy, `progress shows ${index + 1} / 4`);
    assert.equal(busy.disabled, true);
    ctx.ok(call, { id: index + 1, mistake_ids: [index + 1] });
    await tick();
  }
  assert.equal(new Set(titles).size, 4);
  assert.equal(ctx.calls.length, 5, "exactly one check plus four creations");
  assert.equal(ctx.eventsOf("app:data-changed").length, 1);
  assert.deepEqual(navigations(ctx), ["home"], "the home page is reloaded to show the new numbers");
});

test("load: after finishing the card says what to do next, and sample controls switch to 'clear'", async () => {
  const ctx = setup();
  await ctx.rendered(0);
  ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()).click();
  await tick();
  ctx.ok(ctx.calls[0], { items: [] });
  for (let index = 1; index <= 4; index += 1) {
    await tick();
    ctx.ok(ctx.calls[index], { id: index, mistake_ids: [index] });
  }
  await tick();
  // 总览还没重新读取之前，手里的记录是旧的：不能再给"载入"按钮（否则会重复载入），也不给"清除"。
  assert.equal(ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()), null);
  assert.equal(ctx.buttonByText("清除示例数据", ctx.card()), null);
  assert.match(ctx.card().textContent, /已载入示例，可以去今日复习试试/);
  const demos = ctx.Onboarding.demos();
  // 应用重新渲染总览后，卡片带着提示
  await ctx.rendered(4, demos.map((demo, index) => rec(index + 1, demo.title)));
  assert.match(ctx.card().textContent, /已载入示例，可以去今日复习试试/);
  assert.ok(ctx.buttonByText("清除示例数据", ctx.card()));
  assert.equal(ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()), null);
  assert.match(ctx.card().textContent, /示例记录也会计入统计/);
  await ctx.rendered(4, demos.map((demo, index) => rec(index + 1, demo.title)));
  assert.doesNotMatch(ctx.card().textContent, /已载入示例，可以去今日复习试试/, "the one-off message is not repeated");
});

test("load: a mid-way failure keeps what was created, shows the reason, and retries only the rest", async () => {
  const ctx = setup();
  await ctx.rendered(0);
  ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()).click();
  await tick();
  ctx.ok(ctx.calls[0], { items: [] });
  await tick();
  ctx.ok(ctx.calls[1], { id: 1, mistake_ids: [1] });
  await tick();
  ctx.ok(ctx.calls[2], { id: 2, mistake_ids: [2] });
  await tick();
  ctx.fail(ctx.calls[3], "服务暂时不可用");
  await tick();
  assert.equal(ctx.calls.length, 4, "stops at the failure, no further creations");
  assert.match(ctx.card().textContent, /服务暂时不可用/);
  assert.match(ctx.card().textContent, /已完成 2 条/);
  assert.equal(ctx.eventsOf("app:data-changed").length, 1, "the sidebar learns about the two that exist");

  const done = ctx.Onboarding.demos().slice(0, 2).map((demo, index) => rec(index + 1, demo.title));
  ctx.buttonByText("重试剩下的", ctx.card()).click();
  await tick();
  assert.equal(ctx.calls.at(-1).path, "/api/mistakes?due_only=false", "looks at what really exists first");
  ctx.ok(ctx.calls.at(-1), { items: done });
  await tick();
  const retried = [];
  for (let index = 0; index < 2; index += 1) {
    const call = ctx.calls.at(-1);
    assert.equal(call.path, "/api/problems");
    retried.push(JSON.parse(call.options.body).title);
    ctx.ok(call, { id: 9 + index, mistake_ids: [9 + index] });
    await tick();
  }
  assert.deepEqual(retried, plain(ctx.Onboarding.demos()).slice(2).map((demo) => demo.title), "only the missing two");
  assert.equal(ctx.calls.length, 4 + 1 + 2);
  assert.deepEqual(navigations(ctx), ["home"]);
});

test("load: a late answer after sign-out sends nothing more and never touches the next account", async () => {
  const ctx = setup();
  await ctx.rendered(0);
  ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()).click();
  await tick();
  ctx.ok(ctx.calls[0], { items: [] });
  await tick();
  assert.equal(ctx.calls.length, 2);
  ctx.Onboarding.reset(); // 登出
  ctx.ok(ctx.calls[1], { id: 1, mistake_ids: [1] });
  await tick();
  assert.equal(ctx.calls.length, 2, "no further creations for the signed-out account");
  assert.equal(ctx.eventsOf("app:data-changed").length, 0);
  assert.deepEqual(navigations(ctx), []);
  assert.equal(ctx.card(), null);

  // 下一位用户：干净的状态
  await ctx.rendered(0, null, { id: 9, is_trial: false });
  assert.ok(ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()), "a fresh start, no leftover progress");
});

test("load: a late failure after sign-out shows nothing either", async () => {
  const ctx = setup();
  await ctx.rendered(0);
  ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()).click();
  await tick();
  ctx.Onboarding.reset();
  ctx.fail(ctx.calls[0], "旧账号的错误");
  await tick();
  assert.equal(ctx.card(), null);
  await ctx.rendered(0, null, { id: 9, is_trial: false });
  assert.doesNotMatch(ctx.card().textContent, /旧账号的错误/);
});

test("load: refuses to create samples when the server says records already exist", async () => {
  const ctx = setup();
  await ctx.rendered(0); // 总览说 0 条，但服务器上其实已经有了（另一个标签页刚加的）
  ctx.buttonByText("载入 4 条示例错题体验一下", ctx.card()).click();
  await tick();
  ctx.ok(ctx.calls[0], { items: [rec(1, "刚在别处添加的")] });
  await tick();
  assert.equal(ctx.calls.length, 1, "nothing was created");
  assert.match(ctx.card().textContent, /已经有记录了/);
});

/* ---------------- 清除示例 ---------------- */
const MIXED = () => [
  rec(1, `${PREFIX}二分查找`, false, 11), rec(2, "我自己的题", true, 12),
  rec(3, `${PREFIX}LEFT JOIN`, true, 13), rec(4, `带有${PREFIX}但不在开头`, false, 14), rec(5, "示例（没有括号）", false, 15),
];

test("clear: the button appears when sample records exist, confirmation is inline and nothing is sent before it", async () => {
  const ctx = setup();
  await ctx.rendered(5, MIXED());
  const clear = ctx.buttonByText("清除示例数据", ctx.card());
  assert.ok(clear);
  clear.click();
  assert.equal(ctx.calls.length, 1, "only the initial lookup so far");
  assert.match(ctx.card().textContent, /将删除 2 条以【示例】开头的记录，你自己的记录不受影响/);
  ctx.buttonByText("取消", ctx.card()).click();
  assert.doesNotMatch(ctx.card().textContent, /将删除/);
  assert.ok(ctx.buttonByText("清除示例数据", ctx.card()));
  assert.equal(ctx.calls.length, 1);
});

test("clear: deletes only problems whose title starts with the prefix, one at a time", async () => {
  const ctx = setup();
  await ctx.rendered(5, MIXED());
  ctx.buttonByText("清除示例数据", ctx.card()).click();
  ctx.buttonByText("确认清除", ctx.card()).click();
  ctx.buttonByText("确认清除", ctx.card())?.click();
  await tick();
  assert.equal(ctx.calls.at(-1).path, "/api/mistakes?due_only=false", "re-reads the list so it never deletes from stale data");
  ctx.ok(ctx.calls.at(-1), { items: MIXED() });
  await tick();
  assert.equal(ctx.calls.at(-1).path, "/api/problems/11");
  assert.equal(ctx.calls.at(-1).options.method, "DELETE");
  assert.ok(ctx.buttonByText("正在清除 1 / 2", ctx.card()));
  const before = ctx.calls.length;
  ctx.ok(ctx.calls.at(-1), { ok: true });
  await tick();
  assert.equal(ctx.calls.length, before + 1);
  assert.equal(ctx.calls.at(-1).path, "/api/problems/13");
  ctx.ok(ctx.calls.at(-1), { ok: true });
  await tick();
  const deleted = ctx.calls.filter((call) => call.options?.method === "DELETE").map((call) => call.path);
  assert.deepEqual(deleted, ["/api/problems/11", "/api/problems/13"], "the user's own problems (12, 14, 15) are never touched");
  assert.equal(ctx.eventsOf("app:data-changed").length, 1);
  assert.deepEqual(navigations(ctx), ["home"]);
});

test("clear: a failure keeps going no further, names the reason, and retrying finishes the rest", async () => {
  const ctx = setup();
  await ctx.rendered(5, MIXED());
  ctx.buttonByText("清除示例数据", ctx.card()).click();
  ctx.buttonByText("确认清除", ctx.card()).click();
  await tick();
  ctx.ok(ctx.calls.at(-1), { items: MIXED() });
  await tick();
  ctx.ok(ctx.calls.at(-1), { ok: true }); // 11 删掉了
  await tick();
  ctx.fail(ctx.calls.at(-1), "网络中断"); // 13 失败
  await tick();
  assert.match(ctx.card().textContent, /网络中断/);
  assert.match(ctx.card().textContent, /已完成 1 条/);
  const after = ctx.calls.length;

  ctx.buttonByText("重试剩下的", ctx.card()).click();
  await tick();
  ctx.ok(ctx.calls.at(-1), { items: MIXED().filter((item) => item.id !== 1) }); // 11 已经不在了
  await tick();
  assert.equal(ctx.calls.length, after + 2);
  assert.equal(ctx.calls.at(-1).path, "/api/problems/13", "only the remaining sample is deleted");
  ctx.ok(ctx.calls.at(-1), { ok: true });
  await tick();
  assert.deepEqual(navigations(ctx), ["home"]);
});

test("clear: a sample that is already gone (404) counts as deleted", async () => {
  const ctx = setup();
  await ctx.rendered(2, [rec(1, `${PREFIX}a`, false, 11), rec(2, "mine", false, 12)]);
  ctx.buttonByText("清除示例数据", ctx.card()).click();
  ctx.buttonByText("确认清除", ctx.card()).click();
  await tick();
  ctx.ok(ctx.calls.at(-1), { items: [rec(1, `${PREFIX}a`, false, 11), rec(2, "mine", false, 12)] });
  await tick();
  ctx.fail(ctx.calls.at(-1), "not found", 404);
  await tick();
  assert.doesNotMatch(ctx.card()?.textContent ?? "", /没有全部完成/);
  assert.deepEqual(navigations(ctx), ["home"]);
});

test("clear: still offered to an old user (10+ records) who loaded samples earlier", async () => {
  const ctx = setup();
  ctx.env.window.localStorage.setItem("onboarding-demo-loaded:7", "1");
  const many = Array.from({ length: 12 }, (_, index) => rec(index + 1, index < 2 ? `${PREFIX}样例${index}` : `题${index}`));
  await ctx.rendered(12, many);
  assert.equal(ctx.card().dataset.mode, "demo");
  assert.ok(ctx.buttonByText("清除示例数据", ctx.card()));
  assert.equal(ctx.card().querySelectorAll("li").length, 0, "no checklist for a veteran");
  await ctx.rendered(12); // 没有本地标记的老用户：不查记录
});

/* ---------------- 接线：登出再登录 ---------------- */
test("wiring: reset() removes the card and a fresh login starts clean", async () => {
  const ctx = setup();
  await ctx.rendered(2, [rec(1, "a", true), rec(2, "b")]);
  ctx.fire("app:view-changed", { view: "insights" });
  assert.ok(ctx.card());
  ctx.Onboarding.reset(); // 登出
  assert.equal(ctx.card(), null);
  await ctx.rendered(0, null, { id: 99, is_trial: false });
  assert.deepEqual(plain(stepState(ctx.card()).map((step) => step.done)), [false, false, false], "no completed step leaks across accounts");
});

test("wiring: a home render that answers after sign-out never paints", async () => {
  const ctx = setup();
  ctx.fire("app:home-rendered", { overview: { total_mistakes: 3 }, user: ctx.user });
  await tick();
  ctx.Onboarding.reset();
  ctx.ok(ctx.calls[0], { items: [rec(1, "旧账号的题")] });
  await tick();
  assert.equal(ctx.card(), null);
});

test("wiring: a newer render wins over an older answer", async () => {
  const ctx = setup();
  ctx.fire("app:home-rendered", { overview: { total_mistakes: 1 }, user: ctx.user });
  await tick();
  ctx.fire("app:home-rendered", { overview: { total_mistakes: 3 }, user: ctx.user });
  await tick();
  ctx.ok(ctx.calls[1], { items: [rec(1, "a", true), rec(2, "b"), rec(3, "c")] });
  await tick();
  ctx.ok(ctx.calls[0], { items: [rec(1, "a")] }); // 旧响应晚到
  await tick();
  assert.deepEqual(plain(stepState(ctx.card()).map((step) => step.done)), [true, true, false]);
});

test("wiring: without configure() or without data the event is ignored safely", async () => {
  const env = load(["onboarding.js"]);
  env.document.dispatchEvent(new FakeEvent("app:home-rendered", { detail: { overview: { total_mistakes: 0 }, user: { id: 1 } } }));
  env.document.dispatchEvent(new FakeEvent("app:home-rendered", { detail: null }));
  await tick();
  env.window.Onboarding.reset();
  assert.equal(env.calls.length, 0);
});

/* ---------------- 空状态的下一步 ---------------- */
function emptyHost(ctx) {
  const host = ctx.env.document.createElement("div");
  ctx.env.document.body.append(host);
  return host;
}

test("empty today: with records the next step is the weakness page", async () => {
  const ctx = setup();
  const host = emptyHost(ctx);
  const pending = ctx.Onboarding.emptyNext("today", host, { view: "today" });
  await tick();
  ctx.ok(ctx.calls[0], { items: [rec(1, "a")] });
  await pending;
  assert.match(host.textContent, /今天没有要复习的，去看看薄弱点/);
  ctx.buttonByText("去看薄弱点分析", host).click();
  assert.deepEqual(navigations(ctx), ["insights"]);
  assert.equal(ctx.buttonByText("载入示例", host), null);
});

test("empty today: with no records at all it offers the first record and the samples", async () => {
  const ctx = setup();
  const host = emptyHost(ctx);
  const pending = ctx.Onboarding.emptyNext("today", host, { view: "today" });
  await tick();
  ctx.ok(ctx.calls[0], { items: [] });
  await pending;
  ctx.buttonByText("去新增第一条错题", host).click();
  assert.deepEqual(navigations(ctx), ["new"]);
  assert.ok(ctx.buttonByText("载入示例", host));
  assert.match(host.textContent, /示例记录也会计入统计/);
});

test("empty all / mastery: add-record and load-sample buttons, and sample progress shows in place", async () => {
  const ctx = setup();
  const host = emptyHost(ctx);
  await ctx.Onboarding.emptyNext("all", host, { total: 0, view: "all" });
  ctx.buttonByText("新增记录", host).click();
  assert.deepEqual(navigations(ctx), ["new"]);
  ctx.env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view: "all" } }));
  ctx.buttonByText("载入示例", host).click();
  await tick();
  ctx.ok(ctx.calls[0], { items: [] });
  await tick();
  assert.ok(ctx.buttonByText("正在载入 1 / 4", host), "progress is visible where the button was");

  const other = emptyHost(ctx);
  await ctx.Onboarding.emptyNext("mastery", other, { total: 0, view: "mastery" });
  assert.ok(ctx.buttonByText("去新增记录", other));
  assert.ok(ctx.buttonByText("正在载入 1 / 4", other), "all surfaces show the same running job");
});

test("empty weakness / clusters: say how many more are needed and offer 'add a record' only", async () => {
  const ctx = setup();
  for (const kind of ["weakness", "clusters"]) {
    const host = emptyHost(ctx);
    await ctx.Onboarding.emptyNext(kind, host, { count: 2, minimum: 5, total: 2 });
    assert.match(host.textContent, /还差 3 条/);
    ctx.buttonByText("去新增记录", host).click();
    assert.equal(ctx.buttonByText("载入示例", host), null);
  }
  assert.deepEqual(navigations(ctx), ["new", "new"]);
});

test("empty states: passing null removes the block, and calling twice never stacks two", async () => {
  const ctx = setup();
  const host = emptyHost(ctx);
  await ctx.Onboarding.emptyNext("all", host, { total: 0, view: "all" });
  await ctx.Onboarding.emptyNext("all", host, { total: 0, view: "all" });
  assert.equal(host.querySelectorAll(".ob-next").length, 1);
  await ctx.Onboarding.emptyNext("all", host, null);
  assert.equal(host.querySelectorAll(".ob-next").length, 0);
});

test("empty states: sign-out clears them and a late lookup is dropped", async () => {
  const ctx = setup();
  const host = emptyHost(ctx);
  await ctx.Onboarding.emptyNext("all", host, { total: 0, view: "all" });
  ctx.Onboarding.reset();
  assert.equal(host.querySelectorAll(".ob-next").length, 0);

  const pending = ctx.Onboarding.emptyNext("today", host, { view: "today" });
  await tick();
  ctx.Onboarding.reset();
  ctx.ok(ctx.calls.at(-1), { items: [] });
  await pending;
  assert.equal(host.querySelectorAll(".ob-next").length, 0);
});

test("empty states: a failed lookup leaves the page exactly as it was", async () => {
  const ctx = setup();
  const host = emptyHost(ctx);
  const pending = ctx.Onboarding.emptyNext("today", host, { view: "today" });
  await tick();
  ctx.fail(ctx.calls[0], "boom");
  await pending;
  assert.equal(host.querySelectorAll(".ob-next").length, 0);
});
