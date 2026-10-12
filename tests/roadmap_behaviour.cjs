"use strict";

/* 主站「学习路线」（static/roadmap.js + static/pattern-render.js）的行为测试：
   Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   覆盖：渲染与进度、三态切换、失败回滚、迟到响应丢弃、例题勾选与"都勾完了"提示、
   状态筛选、错题多的套路提醒。 */
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
    id: "p-one", title: "二分查找", category: "二分", level: "入门",
    signals: ["数组有序", "找一个位置"], idea: "每次砍掉一半。", complexity: "O(log n)",
    pitfalls: ["结束条件写成 left < right", "mid 溢出"], confusions: "和双指针区分",
    examples: [
      { source: "luogu", id: "P2249", title: "查找" },
      { source: "leetcode-cn", id: "704", title: "二分查找" },
    ],
    related: ["p-two"], template_python: "print(1)\n", template_cpp: "int main(){}\n",
    sample_input: "1\n", sample_output: "1",
  },
  {
    id: "p-two", title: "滑动窗口", category: "双指针", level: "入门",
    signals: ["连续区间", "最长/最短"], idea: "右端进、左端出。", complexity: "O(n)",
    pitfalls: ["左端收缩条件", "窗口为空"], confusions: "和前缀和区分",
    examples: [
      { source: "luogu", id: "P1886", title: "滑动窗口" },
      { source: "leetcode-cn", id: "3", title: "无重复字符的最长子串" },
    ],
    related: [], template_python: "print(2)\n", template_cpp: "int main(){}\n",
  },
];

const STATE = {
  progress: { "p-one": "learning" },
  examples_done: { "p-one": ["luogu:P2249"] },
  mistake_counts: { "p-two": 3 },
};

function setup({ user = { id: 7 }, epoch = 1, state = STATE } = {}) {
  const env = load(["pattern-render.js", "roadmap.js"]);
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
  env.window.Roadmap.configure(hooks);
  return { env, vars, state: JSON.parse(JSON.stringify(state)) };
}

const q = (root, selector) => root.querySelector(selector);
const qa = (root, selector) => Array.from(root.querySelectorAll(selector));
const settle = async () => { await tick(); await tick(); await tick(); };

/** load() 之后回应静态清单 + /api/roadmap。 */
async function loadWith(ctx, status = 200, body = null) {
  const loading = ctx.env.window.Roadmap.load();
  await tick();
  const catalogCall = ctx.env.calls.at(-1);
  assert.equal(catalogCall.url, "/static/data/patterns.json");
  ctx.env.respond(catalogCall, 200, JSON.parse(JSON.stringify(CATALOG)));
  await tick();
  await tick();
  const call = ctx.env.calls.at(-1);
  assert.equal(call.url, "/api/roadmap");
  ctx.env.respond(call, status, body || ctx.state);
  await loading;
  await settle();
}

const cards = (env) => qa(env.document, "#roadmap-list .pattern-card");
const stateButtons = (card) => qa(card, ".pattern-state");
const pressed = (card) => stateButtons(card).filter((b) => b.getAttribute("aria-pressed") === "true")
  .map((b) => b.dataset.status);

/* ---------------- 渲染 ---------------- */

test("roadmap: renders every pattern with a three-state group", async () => {
  const ctx = setup();
  await loadWith(ctx);
  assert.equal(cards(ctx.env).length, 2);
  const first = cards(ctx.env)[0];
  assert.equal(q(first, ".pattern-title").textContent, "二分查找");
  assert.equal(q(first, ".pattern-chip").textContent, "二分");
  assert.deepEqual(stateButtons(first).map((b) => b.textContent), ["未学", "学习中", "已掌握"]);
  assert.deepEqual(pressed(first), ["learning"]);
  // 未学的那个默认停在"未学"。
  assert.deepEqual(pressed(cards(ctx.env)[1]), ["none"]);
  // 错题数 > 0 才有徽标。
  assert.equal(q(cards(ctx.env)[0], ".pattern-mistakes"), null);
  assert.equal(q(cards(ctx.env)[1], ".pattern-mistakes").textContent, "你记过 3 道错题");
});

test("roadmap: overall and per-category progress reflect the saved state", async () => {
  const ctx = setup();
  await loadWith(ctx);
  assert.match(q(ctx.env.document, "#roadmap-summary-text").textContent, /已掌握 0 \/ 2/);
  assert.match(q(ctx.env.document, "#roadmap-summary-text").textContent, /学习中 1/);
  assert.equal(q(ctx.env.document, "#roadmap-progress").value, 0);
  const rows = qa(ctx.env.document, "#roadmap-cats .roadmap-cat-row");
  assert.equal(rows.length, 2);
  // 掌握比例低的排前面：双指针 0/1 排在二分 0/1 之前（同比例按分类名）。
  assert.equal(q(rows[0], ".roadmap-cat-name").textContent, "二分");
  assert.equal(q(rows[0], ".roadmap-cat-num").textContent, "0 / 1");
});

test("roadmap: weak hints name the patterns with mistakes that are still unlearned", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const weak = ctx.env.document.querySelector("#roadmap-weak");
  assert.equal(weak.hidden, false);
  const text = weak.textContent;
  assert.match(text, /《滑动窗口》你错过 3 题但还没学/);
  assert.doesNotMatch(text, /二分查找/);
});

test("roadmap: no weak hint when every mistake pattern is already being learned", async () => {
  const ctx = setup({
    state: { progress: { "p-two": "learning" }, examples_done: {}, mistake_counts: { "p-two": 3 } },
  });
  await loadWith(ctx);
  assert.equal(ctx.env.document.querySelector("#roadmap-weak").hidden, true);
});

test("roadmap: examples already done are checked and the master hint shows when all are done", async () => {
  const ctx = setup({
    state: {
      progress: {}, examples_done: { "p-one": ["luogu:P2249", "leetcode-cn:704"] },
      mistake_counts: {},
    },
  });
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  const boxes = qa(first, ".pattern-example-box");
  assert.equal(boxes.length, 2);
  assert.deepEqual(boxes.map((box) => box.checked), [true, true]);
  assert.equal(q(first, ".pattern-master-hint").hidden, false);
  // 已掌握的不再提示。
  const mastered = setup({
    state: {
      progress: { "p-one": "mastered" }, examples_done: { "p-one": ["luogu:P2249", "leetcode-cn:704"] },
      mistake_counts: {},
    },
  });
  await loadWith(mastered);
  assert.equal(q(cards(mastered.env)[0], ".pattern-master-hint").hidden, true);
});

test("roadmap: luogu examples link to the problem and leetcode ones to a search", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  const links = qa(first, ".pattern-example-link");
  // 洛谷链到题目本身；力扣拼不出确定的 slug，链到按题号搜索。
  const hrefs = links.map((link) => link.getAttribute("href"));
  assert.ok(hrefs.includes("https://www.luogu.com.cn/problem/P2249"), hrefs.join(","));
  assert.ok(hrefs.some((href) => href.startsWith("https://leetcode.cn/problemset/?search=")), hrefs.join(","));
  for (const link of links) assert.equal(link.getAttribute("rel"), "noopener noreferrer");
});

/* ---------------- 三态切换 ---------------- */

test("roadmap: clicking a state sends a PUT and moves the pressed button", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  stateButtons(first).find((b) => b.dataset.status === "mastered").click();
  await tick();
  const put = ctx.env.calls.at(-1);
  assert.equal(put.url, "/api/roadmap/p-one");
  assert.equal(put.init.method, "PUT");
  assert.deepEqual(JSON.parse(put.init.body), { status: "mastered" });
  ctx.env.respond(put, 200, { ok: true, pattern_id: "p-one", status: "mastered" });
  await settle();
  assert.deepEqual(pressed(cards(ctx.env)[0]), ["mastered"]);
  assert.match(q(ctx.env.document, "#roadmap-summary-text").textContent, /已掌握 1 \/ 2/);
});

test("roadmap: a failed state change rolls the button back silently", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  stateButtons(first).find((b) => b.dataset.status === "none").click();
  await tick();
  ctx.env.respond(ctx.env.calls.at(-1), 500, { detail: "炸了" });
  await settle();
  assert.deepEqual(pressed(cards(ctx.env)[0]), ["learning"], "回到原来的状态");
  assert.equal(cards(ctx.env)[0].dataset.status, "learning");
});

test("roadmap: a late state response after reset is discarded", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  stateButtons(first).find((b) => b.dataset.status === "mastered").click();
  await tick();
  const put = ctx.env.calls.at(-1);
  ctx.env.window.Roadmap.reset();
  ctx.env.respond(put, 200, { ok: true });
  await settle();
  // reset() 之后列表已清空，迟到的响应不能把旧内容画回来。
  assert.equal(cards(ctx.env).length, 0);
});

test("roadmap: a late state response after a user switch is discarded", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  stateButtons(first).find((b) => b.dataset.status === "mastered").click();
  await tick();
  const put = ctx.env.calls.at(-1);
  ctx.vars.user = { id: 8 }; // 换号
  ctx.env.respond(put, 200, { ok: true });
  await settle();
  assert.deepEqual(pressed(cards(ctx.env)[0]), ["learning"], "旧账号的响应不算数，按钮回滚");
});

/* ---------------- 例题勾选 ---------------- */

test("roadmap: unchecking an example sends the key and rolls back on failure", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const box = q(cards(ctx.env)[0], ".pattern-example-box");
  assert.equal(box.checked, true);
  box.checked = false;
  box.dispatchEvent(new FakeEvent("change"));
  await tick();
  const put = ctx.env.calls.at(-1);
  assert.equal(put.url, "/api/roadmap/p-one/examples/luogu%3AP2249");
  assert.equal(put.init.method, "PUT");
  assert.deepEqual(JSON.parse(put.init.body), { done: false });
  ctx.env.respond(put, 500, { detail: "炸了" });
  await settle();
  assert.equal(q(cards(ctx.env)[0], ".pattern-example-box").checked, true, "失败则勾回原样");
});

test("roadmap: checking the last example shows the mastered hint", async () => {
  const ctx = setup({ state: { progress: {}, examples_done: { "p-one": ["luogu:P2249"] }, mistake_counts: {} } });
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  assert.equal(q(first, ".pattern-master-hint").hidden, true);
  const box = qa(first, ".pattern-example-box")[1];
  box.checked = true;
  box.dispatchEvent(new FakeEvent("change"));
  await tick();
  ctx.env.respond(ctx.env.calls.at(-1), 200, { ok: true });
  await settle();
  assert.equal(q(cards(ctx.env)[0], ".pattern-master-hint").hidden, false);
});

/* ---------------- 筛选 ---------------- */

test("roadmap: the status filter keeps only the matching cards", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const select = ctx.env.document.querySelector("#roadmap-state");
  select.value = "none";
  select.dispatchEvent(new FakeEvent("change"));
  await tick();
  const shown = cards(ctx.env);
  assert.equal(shown.length, 1);
  assert.equal(q(shown[0], ".pattern-title").textContent, "滑动窗口");
  assert.equal(ctx.env.document.querySelector("#roadmap-count").textContent, "共 1 个套路");
});

test("roadmap: the search box filters on titles and signals", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const input = ctx.env.document.querySelector("#roadmap-q");
  input.value = "连续区间";
  input.dispatchEvent(new FakeEvent("input"));
  await tick();
  const shown = cards(ctx.env);
  assert.equal(shown.length, 1);
  assert.equal(q(shown[0], ".pattern-title").textContent, "滑动窗口");
});

/* ---------------- 失败与清空 ---------------- */

test("roadmap: a failing roadmap request shows a retry button and no stale numbers", async () => {
  const ctx = setup();
  const loading = ctx.env.window.Roadmap.load();
  await tick();
  ctx.env.respond(ctx.env.calls.at(-1), 200, JSON.parse(JSON.stringify(CATALOG)));
  await tick();
  await tick();
  ctx.env.respond(ctx.env.calls.at(-1), 500, { detail: "炸了" });
  await loading;
  await settle();
  assert.match(ctx.env.document.querySelector("#roadmap-status").textContent, /暂时读不到/);
  assert.equal(ctx.env.document.querySelector("#roadmap-retry").hidden, false);
});

test("roadmap: reset clears the list and the progress card", async () => {
  const ctx = setup();
  await loadWith(ctx);
  assert.equal(cards(ctx.env).length, 2);
  ctx.env.window.Roadmap.reset();
  assert.equal(cards(ctx.env).length, 0);
  assert.equal(ctx.env.document.querySelector("#roadmap-summary-card").hidden, true);
  assert.equal(ctx.env.document.querySelector("#roadmap-weak").hidden, true);
});

test("roadmap: template tabs switch panes and stay in sync", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const first = cards(ctx.env)[0];
  const tabs = qa(first, ".pattern-tab");
  assert.deepEqual(tabs.map((t) => t.textContent), ["Python", "C++"]);
  const panes = qa(first, ".pattern-pane");
  assert.equal(panes[0].hidden, false);
  assert.equal(panes[1].hidden, true);
  tabs[1].click();
  assert.equal(panes[0].hidden, true);
  assert.equal(panes[1].hidden, false);
  assert.equal(tabs[1].getAttribute("aria-pressed"), "true");
  assert.equal(tabs[0].getAttribute("aria-pressed"), "false");
});

test("roadmap: related patterns are clickable and jump within the page", async () => {
  const ctx = setup();
  await loadWith(ctx);
  const link = q(cards(ctx.env)[0], ".pattern-related-link");
  assert.equal(link.textContent, "滑动窗口");
  assert.equal(link.dataset.goto, "p-two");
  link.click();
  const second = cards(ctx.env)[1];
  assert.equal(second.querySelector("details").open, true);
});
