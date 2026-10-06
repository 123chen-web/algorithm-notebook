"use strict";

/* 总览页「今日推荐题」卡（static/recommend.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   覆盖：正常渲染、空状态、点击"做完了 / 不感兴趣"、请求失败静默隐藏、
   迟到响应被丢弃（reset / 换号 / 换代次）、reset 清空。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const ITEMS = [
  {
    id: "4A", contest_id: 4, idx: "A", name: "Watermelon", rating: 800,
    tags: ["math", "brute force"], url: "https://codeforces.com/problemset/problem/4/A",
    reason: "你在数学上有2条未掌握的错题", state: "new",
  },
  {
    id: "5A", contest_id: 5, idx: "A", name: "Way Too Long Words", rating: 900,
    tags: ["strings"], url: "https://codeforces.com/problemset/problem/5/A",
    reason: "你在字符串上有1条未掌握的错题", state: "new",
  },
];

function setup({ user = { id: 7 }, epoch = 1 } = {}) {
  const env = load(["recommend.js"]);
  const state = { user, epoch };
  const hooks = { getUser: () => state.user, getEpoch: () => state.epoch };
  // 假 fetch 的响应对象只有 json()；api() 里失败要抛错，这里照着宿主 app.js 的样子包一层。
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
  env.window.RecommendCard.configure(hooks);
  const container = env.document.createElement("div");
  env.document.body.append(container);
  return { env, state, container };
}

const q = (container, selector) => container.querySelector(selector);
const qa = (container, selector) => Array.from(container.querySelectorAll(selector));
const settle = async () => { await tick(); await tick(); };
// 每个测试用深拷贝：mark() 会就地改 item.state，避免测试间污染。
const freshItems = () => JSON.parse(JSON.stringify(ITEMS));

/** mount 并回应最近一次 GET /api/recommend。 */
async function mountWith(ctx, status, body) {
  const mounted = ctx.env.window.RecommendCard.mount(ctx.container);
  await tick();
  ctx.env.respond(ctx.env.calls.at(-1), status, body);
  await mounted;
  await tick();
}

/* ---------------- 正常渲染 ---------------- */

test("recommend: renders items with name, rating, tags, reason and links", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  assert.equal(ctx.env.calls[0].url, "/api/recommend");
  const rows = qa(ctx.container, ".rc-item");
  assert.equal(rows.length, 2);
  assert.equal(q(ctx.container, ".rc-title").textContent, "今日推荐题");
  assert.equal(q(rows[0], ".rc-item-name").textContent, "Watermelon");
  assert.equal(q(rows[0], ".rc-item-rating").textContent, "难度 800");
  assert.equal(q(rows[0], ".rc-item-tags").textContent, "标签：math · brute force");
  assert.equal(q(rows[0], ".rc-item-reason").textContent, "你在数学上有2条未掌握的错题");
  const link = q(rows[0], ".rc-item-link");
  assert.equal(link.textContent, "去做题");
  assert.equal(link.getAttribute("href"), "https://codeforces.com/problemset/problem/4/A");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(link.getAttribute("rel"), "noopener noreferrer");
  assert.equal(
    q(ctx.container, ".rc-footnote").textContent,
    "题目来自 Codeforces，点击跳转原站；欧叶OY 不保存题面。",
  );
  assert.equal(qa(ctx.container, ".rc-done").length, 2);
  assert.equal(qa(ctx.container, ".rc-dismiss").length, 2);
});

test("recommend: shows empty state with the server hint", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: [], hint: "先记几条错题，我才知道推荐什么" });
  assert.equal(q(ctx.container, ".rc-note").textContent, "先记几条错题，我才知道推荐什么");
  assert.equal(qa(ctx.container, ".rc-item").length, 0);
});

/* ---------------- 标记状态 ---------------- */

test("recommend: clicking done posts and switches the row to finished", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  qa(ctx.container, ".rc-done")[0].click();
  await tick();
  const post = ctx.env.calls.at(-1);
  assert.equal(post.url, "/api/recommend/4/A");
  assert.equal(post.init.method, "POST");
  assert.deepEqual(JSON.parse(post.init.body), { state: "done" });
  ctx.env.respond(post, 200, { ok: true });
  await settle();
  const rows = qa(ctx.container, ".rc-item");
  assert.equal(q(rows[0], ".rc-item-state").textContent, "已完成");
  assert.equal(qa(rows[0], ".rc-done").length, 0);
  // 另一行不受影响。
  assert.equal(qa(qa(ctx.container, ".rc-item")[1], ".rc-dismiss").length, 1);
});

test("recommend: clicking dismiss posts and switches the row to dismissed", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  qa(ctx.container, ".rc-dismiss")[1].click();
  await tick();
  const post = ctx.env.calls.at(-1);
  assert.equal(post.url, "/api/recommend/5/A");
  assert.deepEqual(JSON.parse(post.init.body), { state: "dismissed" });
  ctx.env.respond(post, 200, { ok: true });
  await settle();
  assert.equal(q(qa(ctx.container, ".rc-item")[1], ".rc-item-state").textContent, "已忽略");
});

test("recommend: failed mark keeps the buttons and shows a notice", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  qa(ctx.container, ".rc-done")[0].click();
  await tick();
  const post = ctx.env.calls.at(-1);
  ctx.env.respond(post, 500, { detail: "炸了" });
  await settle();
  assert.equal(qa(ctx.container, ".rc-done").length, 2);
  assert.equal(q(ctx.container, ".rc-error").textContent, "更新失败，请稍后重试。");
});

test("recommend: second click while a mark is in flight does not send another POST", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  const done = qa(ctx.container, ".rc-done")[0];
  done.click();
  await tick();
  const count = ctx.env.calls.length;
  // 按钮 disabled，且 busyKey 阻止第二次提交。
  done.click();
  await tick();
  assert.equal(ctx.env.calls.length, count);
});

/* ---------------- 失败与守卫 ---------------- */

test("recommend: request failure hides the card silently", async () => {
  const ctx = setup();
  await mountWith(ctx, 500, { detail: "炸了" });
  assert.equal(ctx.container.children.length, 0);
  assert.equal(ctx.container.textContent, "");
});

test("recommend: late response after reset is discarded", async () => {
  const ctx = setup();
  const mounted = ctx.env.window.RecommendCard.mount(ctx.container);
  await tick();
  ctx.env.window.RecommendCard.reset();
  ctx.env.respond(ctx.env.calls[0], 200, { items: ITEMS, hint: "" });
  await mounted;
  await tick();
  assert.equal(ctx.container.children.length, 0);
});

test("recommend: late response after epoch change is discarded", async () => {
  const ctx = setup();
  const mounted = ctx.env.window.RecommendCard.mount(ctx.container);
  await tick();
  ctx.state.epoch = 2; // 换号 / 重新登录
  ctx.env.respond(ctx.env.calls[0], 200, { items: freshItems(), hint: "" });
  await mounted;
  await tick();
  // 迟到的数据不渲染（loading 占位由宿主在换号时调 reset() 清掉）。
  assert.equal(qa(ctx.container, ".rc-item").length, 0);
});

test("recommend: late mark after user switch is discarded", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  qa(ctx.container, ".rc-done")[0].click();
  await tick();
  const post = ctx.env.calls.at(-1);
  ctx.state.user = { id: 8 };
  ctx.env.respond(post, 200, { ok: true });
  await settle();
  // 响应被丢弃：行状态保持未标记，按钮仍在（用户换了，旧内容也不该留）。
  assert.equal(qa(ctx.container, ".rc-done").length, 2);
});

test("recommend: reset clears the card", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  assert.equal(qa(ctx.container, ".rc-item").length, 2);
  ctx.env.window.RecommendCard.reset();
  assert.equal(ctx.container.children.length, 0);
});

test("recommend: mount without a user renders nothing and makes no request", async () => {
  const ctx = setup({ user: null });
  const ok = await ctx.env.window.RecommendCard.mount(ctx.container);
  await tick();
  assert.equal(ok, false);
  assert.equal(ctx.env.calls.length, 0);
  assert.equal(ctx.container.children.length, 0);
});

test("recommend: cached render does not refetch", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { items: freshItems(), hint: "" });
  assert.equal(ctx.env.calls.length, 1);
  const ok = await ctx.env.window.RecommendCard.mount(ctx.container);
  await tick();
  assert.equal(ok, true);
  assert.equal(ctx.env.calls.length, 1);
  assert.equal(qa(ctx.container, ".rc-item").length, 2);
});
