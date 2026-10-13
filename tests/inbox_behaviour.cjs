"use strict";

/* 待分类箱（static/inbox.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   覆盖：加载渲染（卡片 + 桶 + 推荐发光）、拖拽放下开气泡、点选、键盘全流程、
   跳过、撤销、失败回滚、迟到响应丢弃、三种空状态。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const CATALOG = [
  {
    id: "p-bs", title: "二分查找", category: "二分", level: "入门",
    signals: ["数组有序"], idea: "砍一半。", complexity: "O(log n)",
    pitfalls: ["边界"], confusions: "x", examples: [], related: [],
    template_python: "x\n", template_cpp: "x\n", sample_input: "1\n", sample_output: "1",
  },
  {
    id: "p-bsr", title: "旋转数组二分", category: "二分", level: "入门",
    signals: ["旋转"], idea: "一半有序。", complexity: "O(log n)",
    pitfalls: ["边界"], confusions: "x", examples: [], related: [],
    template_python: "x\n", template_cpp: "x\n", sample_input: "1\n", sample_output: "1",
  },
  {
    id: "p-sw", title: "滑动窗口", category: "双指针", level: "入门",
    signals: ["连续"], idea: "右进左出。", complexity: "O(n)",
    pitfalls: ["边界"], confusions: "x", examples: [], related: [],
    template_python: "x\n", template_cpp: "x\n", sample_input: "1\n", sample_output: "1",
  },
];

const item = (overrides = {}) => ({
  problem_id: 101,
  title: "过河卒",
  snippet: "棋盘上的马走日。",
  source_url: "https://www.luogu.com.cn/problem/P1002",
  created_at: "2026-10-01",
  tags: ["dp"],
  suggestions: [
    { pattern_id: "p-bs", title: "二分查找", category: "二分", score: 60, reason: "关键词" },
  ],
  ...overrides,
});

function setup({ user = { id: 7 }, epoch = 1 } = {}) {
  const env = load(["inbox.js"]);
  const vars = { user, epoch };
  const hooks = { getUser: () => vars.user, getEpoch: () => vars.epoch };
  hooks.api = async (path, options = {}) => {
    const response = await env.window.fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.detail || "请求失败，请稍后重试");
      error.status = response.status;
      throw error;
    }
    return data;
  };
  env.window.Inbox.configure(hooks);
  return { env, vars };
}

const q = (root, selector) => root.querySelector(selector);
const qa = (root, selector) => Array.from(root.querySelectorAll(selector));
const settle = async () => { await tick(); await tick(); await tick(); };

/** load() 之后依次回应：静态清单、/api/roadmap/inbox、/api/roadmap。 */
async function loadWith(ctx, { items = [item()], total = null, state = {} } = {}) {
  const loading = ctx.env.window.Inbox.load();
  await tick();
  const catalogCall = ctx.env.calls.at(-1);
  assert.equal(catalogCall.url, "/static/data/patterns.json");
  ctx.env.respond(catalogCall, 200, JSON.parse(JSON.stringify(CATALOG)));
  await tick();
  await tick();
  const inboxCall = ctx.env.calls.find((call) => call.url.startsWith("/api/roadmap/inbox"));
  const stateCall = ctx.env.calls.find((call) => call.url === "/api/roadmap");
  assert.ok(inboxCall, "inbox request issued");
  assert.ok(stateCall, "roadmap state request issued");
  ctx.env.respond(inboxCall, 200, { total: total ?? items.length, items });
  ctx.env.respond(stateCall, 200, {
    progress: {}, examples_done: {},
    mistake_counts: { "p-sw": 4 },
    ...state,
  });
  await loading;
  await settle();
}

const topCard = (env) => q(env.document, "#inbox-card-stack .inbox-card");
const binOf = (env, category) =>
  qa(env.document, "#inbox-bins .inbox-bin").find((b) => b.dataset.category === category);
const putCalls = (env) => env.calls.filter((call) => call.url.includes("/api/problems/"));

/* ---------------- 加载与渲染 ---------------- */

test("inbox: renders the top card, bins and suggested glow", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item(), item({ problem_id: 102, title: "第二题" })] });
  assert.equal(topCard(ctx.env).dataset.problemId, "101");
  assert.equal(q(topCard(ctx.env), ".inbox-card-title").textContent, "过河卒");
  assert.equal(q(topCard(ctx.env), ".inbox-source").textContent, "luogu.com.cn");
  const bins = qa(ctx.env.document, "#inbox-bins .inbox-bin");
  assert.deepEqual(bins.map((b) => b.dataset.category), ["二分", "双指针"]);
  // 推荐命中的分类发光；已有 4 道的桶显示计数。
  assert.ok(binOf(ctx.env, "二分").classList.contains("inbox-bin--suggested"));
  assert.ok(!binOf(ctx.env, "双指针").classList.contains("inbox-bin--suggested"));
  assert.equal(q(binOf(ctx.env, "双指针"), ".inbox-bin-count").textContent, "4");
  assert.equal(q(ctx.env.document, "#inbox-remaining").textContent, "还剩 2 道");
});

test("inbox: empty inbox shows the done seal", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [], total: 0 });
  assert.equal(q(ctx.env.document, "#inbox-done").hidden, false);
  assert.equal(q(ctx.env.document, ".inbox-done-seal").textContent, "毕");
  assert.equal(qa(ctx.env.document, "#inbox-bins .inbox-bin").length, 0);
});

test("inbox: one item still renders normally", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  assert.equal(topCard(ctx.env).dataset.problemId, "101");
  assert.equal(q(ctx.env.document, "#inbox-remaining").textContent, "还剩 1 道");
});

/* ---------------- 拖拽 ---------------- */

function pointer(card, type, props) {
  card.dispatchEvent(new FakeEvent(type, { bubbles: true, props }));
}

test("inbox: drag into a bin opens the pattern bubble", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "二分");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  assert.ok(bin.classList.contains("inbox-bin--hot"), "hovered bin is highlighted");
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  const bubble = q(ctx.env.document, "#inbox-bubble");
  assert.equal(bubble.hidden, false);
  const picks = qa(bubble, ".inbox-bubble-pick");
  // 推荐命中的排第一且高亮。
  assert.equal(picks[0].textContent.includes("二分查找"), true);
  assert.ok(picks[0].classList.contains("inbox-bubble-pick--hit"));
  assert.equal(picks.length, 2);
});

test("inbox: confirming a pattern files the card and offers undo", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item(), item({ problem_id: 102, title: "第二题" })] });
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "二分");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  const pick = q(ctx.env.document, "#inbox-bubble .inbox-bubble-pick");
  pick.dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  const calls = putCalls(ctx.env);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/problems/101/patterns");
  assert.deepEqual(JSON.parse(calls[0].init.body), { pattern_ids: ["p-bs"] });
  ctx.env.respond(calls[0], 200, { ok: true, problem_id: 101, pattern_ids: ["p-bs"] });
  await settle();
  // 卡片离场（第二题顶上来） + 桶计数 +1 + 撤销条。
  assert.equal(topCard(ctx.env).dataset.problemId, "102");
  assert.equal(q(binOf(ctx.env, "二分"), ".inbox-bin-count").textContent, "1");
  const toast = q(ctx.env.document, "#inbox-toast");
  assert.equal(toast.hidden, false);
  assert.match(toast.textContent, /已放入「二分」/);
});

test("inbox: undo sends [] and brings the card back", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item(), item({ problem_id: 102, title: "第二题" })] });
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "二分");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  q(ctx.env.document, "#inbox-bubble .inbox-bubble-pick").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  ctx.env.respond(putCalls(ctx.env)[0], 200, { ok: true, problem_id: 101, pattern_ids: ["p-bs"] });
  await settle();
  q(ctx.env.document, ".inbox-undo").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  const calls = putCalls(ctx.env);
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[1].init.body), { pattern_ids: [] });
  ctx.env.respond(calls[1], 200, { ok: true, problem_id: 101, pattern_ids: [] });
  await settle();
  assert.equal(topCard(ctx.env).dataset.problemId, "101");
  assert.equal(q(binOf(ctx.env, "二分"), ".inbox-bin-count").textContent, "0");
});

test("inbox: failed PUT rolls the card back silently", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "二分");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  q(ctx.env.document, "#inbox-bubble .inbox-bubble-pick").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  ctx.env.respond(putCalls(ctx.env)[0], 500, { detail: "炸了" });
  await settle();
  assert.equal(topCard(ctx.env).dataset.problemId, "101", "the card is back at the front");
  assert.equal(q(ctx.env.document, "#inbox-remaining").textContent, "还剩 1 道");
});

test("inbox: dropping outside any bin springs back", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  const card = topCard(ctx.env);
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 400, clientY: 400, target: card });
  pointer(card, "pointerup", { pointerId: 1, clientX: 400, clientY: 400 });
  await settle();
  assert.equal(putCalls(ctx.env).length, 0, "no request on a missed drop");
  assert.equal(topCard(ctx.env).dataset.problemId, "101");
});

/* ---------------- 点选 ---------------- */

test("inbox: tap card then tap bin works without dragging", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  topCard(ctx.env).dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  assert.ok(topCard(ctx.env).classList.contains("inbox-card--selected"));
  binOf(ctx.env, "双指针").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  assert.equal(q(ctx.env.document, "#inbox-bubble").hidden, false);
});

test("inbox: tapping the card again deselects", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  topCard(ctx.env).dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  topCard(ctx.env).dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  assert.ok(!topCard(ctx.env).classList.contains("inbox-card--selected"));
});

/* ---------------- 键盘 ---------------- */

function key(target, keyName) {
  target.dispatchEvent(new FakeEvent("keydown", { bubbles: true, props: { key: keyName, preventDefault() {} } }));
}

test("inbox: keyboard pickup, move, drop and announcements", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  const card = topCard(ctx.env);
  key(card, " ");
  await settle();
  const live = q(ctx.env.document, "#inbox-live").textContent;
  assert.match(live, /已拿起《过河卒》/);
  assert.match(live, /当前在桶「二分」/);
  // 往右移到“双指针”。
  const bins = qa(ctx.env.document, "#inbox-bins .inbox-bin");
  key(bins[0], "ArrowRight");
  await settle();
  assert.match(q(ctx.env.document, "#inbox-live").textContent, /当前在桶「双指针」/);
  key(bins[1], "Enter");
  await settle();
  assert.equal(q(ctx.env.document, "#inbox-bubble").hidden, false);
});

test("inbox: Escape cancels the keyboard pickup", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  topCard(ctx.env).dispatchEvent(new FakeEvent("keydown", { bubbles: true, props: { key: "Enter", preventDefault() {} } }));
  await settle();
  assert.ok(topCard(ctx.env).classList.contains("inbox-card--held"));
  topCard(ctx.env).dispatchEvent(new FakeEvent("keydown", { bubbles: true, props: { key: "Escape", preventDefault() {} } }));
  await settle();
  assert.ok(!q(ctx.env.document, "#inbox-card-stack .inbox-card").classList.contains("inbox-card--held"));
  assert.match(q(ctx.env.document, "#inbox-live").textContent, /已取消拿起/);
});

/* ---------------- 跳过 / 气泡选项 ---------------- */

test("inbox: skip sends the card to the bottom of the pile", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item(), item({ problem_id: 102, title: "第二题" })] });
  q(ctx.env.document, ".inbox-skip").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  assert.equal(topCard(ctx.env).dataset.problemId, "102");
  assert.equal(q(ctx.env.document, "#inbox-remaining").textContent, "还剩 2 道");
  assert.equal(putCalls(ctx.env).length, 0, "skipping never touches the server");
});

test("inbox: empty suggestions show an honest bubble title", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item({ suggestions: [] })] });
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "双指针");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  assert.match(
    q(ctx.env.document, "#inbox-bubble .inbox-bubble-title").textContent,
    /没有系统推荐/,
  );
});

test("inbox: empty suggestions still open the bubble for manual choice", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item({ suggestions: [] })] });
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "双指针");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  const bubble = q(ctx.env.document, "#inbox-bubble");
  assert.equal(bubble.hidden, false);
  assert.equal(qa(bubble, ".inbox-bubble-pick").length, 1, "only the bin category patterns are listed");
});

test("inbox: defer-to-later returns the card without any request", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "二分");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  q(ctx.env.document, ".inbox-bubble-defer").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  assert.equal(putCalls(ctx.env).length, 0);
  assert.equal(topCard(ctx.env).dataset.problemId, "101");
});

/* ---------------- 迟到响应 ---------------- */

test("inbox: a late inbox response after reset is discarded", async () => {
  const ctx = setup();
  const loading = ctx.env.window.Inbox.load();
  await tick();
  const catalogCall = ctx.env.calls.at(-1);
  ctx.env.respond(catalogCall, 200, JSON.parse(JSON.stringify(CATALOG)));
  await tick();
  await tick();
  const inboxCall = ctx.env.calls.find((call) => call.url.startsWith("/api/roadmap/inbox"));
  const stateCall = ctx.env.calls.find((call) => call.url === "/api/roadmap");
  ctx.vars.epoch += 1; // 登出再登录：代次变了
  ctx.env.window.Inbox.reset();
  ctx.env.respond(inboxCall, 200, { total: 1, items: [item()] });
  ctx.env.respond(stateCall, 200, { progress: {}, examples_done: {}, mistake_counts: {} });
  await loading;
  await settle();
  assert.equal(topCard(ctx.env), null, "the late response must not render");
});

test("inbox: a late PUT response for another account is discarded", async () => {
  const ctx = setup();
  await loadWith(ctx, { items: [item()] });
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "二分");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  q(ctx.env.document, "#inbox-bubble .inbox-bubble-pick").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  ctx.vars.user = { id: 99 }; // 换号了
  const calls = putCalls(ctx.env);
  ctx.env.respond(calls[0], 200, { ok: true, problem_id: 101, pattern_ids: ["p-bs"] });
  await settle();
  // 迟到的成功响应被丢弃：桶计数不涨（卡片已离场，属乐观更新）。
  assert.equal(q(binOf(ctx.env, "二分"), ".inbox-bin-count").textContent, "0");
});

/* ---------------- 让 AI 猜一下 ---------------- */

async function openBubbleFor(ctx) {
  const card = topCard(ctx.env);
  const bin = binOf(ctx.env, "二分");
  pointer(card, "pointerdown", { pointerId: 1, clientX: 10, clientY: 10 });
  pointer(card, "pointermove", { pointerId: 1, clientX: 200, clientY: 200, target: bin });
  pointer(card, "pointerup", { pointerId: 1, clientX: 200, clientY: 200 });
  await settle();
  return q(ctx.env.document, "#inbox-bubble");
}
const aiCalls = (env) => env.calls.filter((call) => call.url === "/api/roadmap/ai-suggest");

test("inbox AI: nothing is sent until the button is clicked", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const bubble = await openBubbleFor(ctx);
  assert.ok(q(bubble, ".inbox-bubble-ai-btn"), "button is offered");
  assert.equal(aiCalls(ctx.env).length, 0);
});

test("inbox AI: click asks once, shows the suggestions, and picking one files the card", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const bubble = await openBubbleFor(ctx);
  const button = q(bubble, ".inbox-bubble-ai-btn");
  button.dispatchEvent(new FakeEvent("click", { bubbles: true }));
  button.dispatchEvent(new FakeEvent("click", { bubbles: true })); // 连点只发一次
  await settle();
  const calls = aiCalls(ctx.env);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].init.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].init.body), { problem_id: 101 });
  assert.equal(button.disabled, true);
  ctx.env.respond(calls[0], 200, {
    suggestions: [{ pattern_id: "p-sw", title: "滑动窗口", category: "双指针", reason: "连续区间", source: "ai" }],
    ai_daily_remaining: 7,
  });
  await settle();
  const picks = qa(bubble, ".inbox-bubble-pick--ai");
  assert.equal(picks.length, 1);
  assert.match(picks[0].textContent, /滑动窗口（双指针）/);
  assert.match(picks[0].textContent, /AI 建议/);
  assert.match(q(bubble, ".inbox-bubble-ai-status").textContent, /还剩 7 次/);
  picks[0].dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  const puts = putCalls(ctx.env);
  assert.equal(puts.length, 1);
  assert.deepEqual(JSON.parse(puts[0].init.body), { pattern_ids: ["p-sw"] });
});

test("inbox AI: failure is said in the bubble and the button can be tried again", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const bubble = await openBubbleFor(ctx);
  const button = q(bubble, ".inbox-bubble-ai-btn");
  button.dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  ctx.env.respond(aiCalls(ctx.env)[0], 429, { detail: "今天的 AI 生成次数已用完" });
  await settle();
  assert.equal(q(bubble, ".inbox-bubble-ai-status").textContent, "今天的 AI 生成次数已用完");
  assert.equal(button.disabled, false);
  assert.ok(qa(bubble, ".inbox-bubble-pick").length >= 1, "manual choices are still there");
});

test("inbox AI: an empty answer is honest", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const bubble = await openBubbleFor(ctx);
  q(bubble, ".inbox-bubble-ai-btn").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  ctx.env.respond(aiCalls(ctx.env)[0], 200, { suggestions: [], ai_daily_remaining: 3 });
  await settle();
  assert.match(q(bubble, ".inbox-bubble-ai-status").textContent, /拿不准/);
  assert.equal(qa(bubble, ".inbox-bubble-pick--ai").length, 0);
});

test("inbox AI: an answer that arrives after sign-out is discarded", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const bubble = await openBubbleFor(ctx);
  q(bubble, ".inbox-bubble-ai-btn").dispatchEvent(new FakeEvent("click", { bubbles: true }));
  await settle();
  ctx.env.window.Inbox.reset();
  ctx.env.respond(aiCalls(ctx.env)[0], 200, {
    suggestions: [{ pattern_id: "p-sw", title: "滑动窗口", category: "双指针", reason: "x", source: "ai" }],
  });
  await settle();
  assert.equal(qa(bubble, ".inbox-bubble-pick--ai").length, 0);
});
