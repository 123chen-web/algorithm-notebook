"use strict";

/* 错因专题页"我的三大典型失误"卡 + 考前一页纸（static/typical.js）的行为测试：
   Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。覆盖渲染、文案分支、
   点击进入标签筛选、打印一页纸结构、迟到响应丢弃（登出 / 换号 / 离开页面 / 乱序）、reset。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

function item(over = {}) {
  return {
    key: "边界", name: "边界", count: 4,
    last_wrong_at: "2026-09-15T04:00:00+00:00",
    hint: "动笔前先把区间端点写出来。",
    ids: [
      { id: 101, title: "闭区间终点", description: "最后一点没验证" },
      { id: 102, title: "二分循环条件", description: "退出时多走一步" },
      { id: 103, title: "空数组", description: "忘了空输入" },
    ],
    ...over,
  };
}

function payload(over = {}) {
  return {
    enough: true,
    items: [
      item(),
      item({ key: "概念混淆", name: "概念混淆", count: 3, last_wrong_at: "2026-09-12T04:00:00+00:00", hint: "各写一个正例和反例。" }),
      item({ key: "粗心", name: "粗心", count: 2, last_wrong_at: "2026-09-10T04:00:00+00:00", hint: "回读一遍。", ids: [item().ids[0]] }),
    ],
    checklist: ["第一条自查", "第二条自查", "第三条自查", "第四条自查", "第五条自查", "第六条自查", "第七条自查"],
    ...over,
  };
}

function setup({ view = "clusters", user = { id: 7 } } = {}) {
  const env = load(["typical.js"], { extra: { URL } });
  const state = { user, epoch: 1, view };
  const prints = [];
  const hooks = {
    api: async (path, options = {}) => {
      const response = await env.window.fetch(path, options);
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || "请求失败");
      return data;
    },
    getUser: () => state.user,
    getEpoch: () => state.epoch,
    getView: () => state.view,
    print: () => prints.push(1),
  };
  env.window.Typical.configure(hooks);
  const $ = (selector) => env.document.querySelector(selector);
  return { env, state, $, hooks, prints };
}

const byPath = (env, path) => env.calls.filter((call) => call.url === path);
const latest = (env, path) => byPath(env, path).at(-1);
async function settle() { await tick(); await tick(); }

async function mountAndRespond(h, body, status = 200) {
  const loading = h.env.window.Typical.mount(h.$("#typical-card"));
  await tick();
  h.env.respond(latest(h.env, "/api/stats/typical"), status, body);
  await loading;
  await settle();
}

/* ---------------- 请求与守卫 ---------------- */
test("typical: no request and card hidden when signed out", async () => {
  const { env, $ } = setup({ user: null });
  await env.window.Typical.mount($("#typical-card"));
  await settle();
  assert.equal(byPath(env, "/api/stats/typical").length, 0);
  assert.equal($("#typical-card").hidden, true);
});

test("typical: no request while another page is active", async () => {
  const { env, $ } = setup({ view: "home" });
  await env.window.Typical.mount($("#typical-card"));
  await settle();
  assert.equal(byPath(env, "/api/stats/typical").length, 0);
  assert.equal($("#typical-card").hidden, true);
});

test("typical: late answers are dropped after reset, account switch or leaving the page", async () => {
  const h = setup();
  const first = h.env.window.Typical.mount(h.$("#typical-card"));
  await tick();
  h.env.window.Typical.reset();
  h.env.respond(latest(h.env, "/api/stats/typical"), 200, payload());
  await first;
  await settle();
  assert.equal(h.$("#typical-card").hidden, true, "reset wins over an in-flight answer");
  assert.equal(h.$("#typical-list").children.length, 0);

  const h2 = setup();
  const second = h2.env.window.Typical.mount(h2.$("#typical-card"));
  await tick();
  h2.state.user = { id: 8 };
  h2.env.respond(latest(h2.env, "/api/stats/typical"), 200, payload());
  await second;
  await settle();
  assert.equal(h2.$("#typical-list").children.length, 0, "answer for the previous account is dropped");

  const h3 = setup();
  const third = h3.env.window.Typical.mount(h3.$("#typical-card"));
  await tick();
  h3.state.view = "home";
  h3.env.respond(latest(h3.env, "/api/stats/typical"), 200, payload());
  await third;
  await settle();
  assert.equal(h3.$("#typical-list").children.length, 0, "answer after leaving clusters is dropped");
});

test("typical: a failed request leaves the card hidden without throwing", async () => {
  const h = setup();
  await mountAndRespond(h, { detail: "失败" }, 500);
  assert.equal(h.$("#typical-card").hidden, true);
  assert.equal(h.$("#typical-list").children.length, 0);
});

/* ---------------- 渲染 ---------------- */
test("typical: not enough data shows the guide and hides the one-pager button", async () => {
  const h = setup();
  await mountAndRespond(h, { enough: false, items: [], checklist: [] });
  assert.equal(h.$("#typical-card").hidden, false);
  assert.equal(h.$("#typical-guide").hidden, false);
  assert.match(h.$("#typical-guide").textContent, /错因标签/);
  assert.equal(h.$("#typical-list").children.length, 0);
  assert.equal(h.$("#typical-print-open").hidden, true);
});

test("typical: enough but no recurring category shows encouragement, no print button", async () => {
  const h = setup();
  await mountAndRespond(h, payload({ items: [] }));
  assert.equal(h.$("#typical-guide").hidden, true);
  assert.equal(h.$("#typical-print-open").hidden, true);
  assert.match(h.$("#typical-status").textContent, /保持/);
});

test("typical: renders up to three categories with count, time and hint", async () => {
  const h = setup();
  await mountAndRespond(h, payload());
  const rows = h.$("#typical-list").children;
  assert.equal(rows.length, 3);
  assert.match(rows[0].textContent, /边界/);
  assert.match(rows[0].textContent, /4 条/);
  assert.match(rows[0].textContent, /9 月 15 日/);
  assert.match(rows[0].textContent, /动笔前先把区间端点写出来。/);
  assert.match(rows[1].textContent, /概念混淆/);
  assert.equal(h.$("#typical-print-open").hidden, false);
});

test("typical: category names render as text, never HTML", async () => {
  const h = setup();
  const evil = item({ key: "<img src=x onerror=alert(1)>", name: "<img src=x onerror=alert(1)>" });
  await mountAndRespond(h, payload({ items: [evil] }));
  const row = h.$("#typical-list").children[0];
  assert.equal(row.querySelectorAll("img").length, 0);
  assert.match(row.textContent, /<img src=x onerror=alert\(1\)>/);
});

test("typical: clicking a category opens the tag-filtered record list", async () => {
  const h = setup();
  await mountAndRespond(h, payload());
  const row = h.$("#typical-list").children[0];
  row.click();
  const filter = h.env.events.find((event) => event.type === "records:filter");
  assert.ok(filter);
  assert.equal(filter.detail.tag, "边界");
});

/* ---------------- 考前一页纸 ---------------- */
test("typical: one-pager has three sections, two examples each and the checklist", async () => {
  const h = setup();
  await mountAndRespond(h, payload());
  h.$("#typical-print-open").click();
  assert.equal(h.$("#typical-sheet-wrap").hidden, false);
  assert.equal(h.env.document.documentElement.classList.contains("typical-sheet-open"), true);
  const sheet = h.$("#typical-sheet");
  assert.match(sheet.textContent, /考前一页纸/);
  const sections = sheet.querySelectorAll(".typical-sheet-category");
  assert.equal(sections.length, 3);
  // 每类最多 2 道代表性错题（题名 + 一句话错因），即使接口给了 3 条。
  const examples = sections[0].querySelectorAll(".typical-sheet-example");
  assert.equal(examples.length, 2);
  assert.match(examples[0].textContent, /闭区间终点/);
  assert.match(examples[0].textContent, /最后一点没验证/);
  const checklist = sheet.querySelectorAll(".typical-sheet-check");
  assert.equal(checklist.length, 7);
});

test("typical: print button calls the host printer and close hides the sheet", async () => {
  const h = setup();
  await mountAndRespond(h, payload());
  h.$("#typical-print-open").click();
  h.$("#typical-sheet-print").click();
  assert.deepEqual(h.prints, [1]);
  h.$("#typical-sheet-close").click();
  assert.equal(h.$("#typical-sheet-wrap").hidden, true);
  assert.equal(h.env.document.documentElement.classList.contains("typical-sheet-open"), false);
});

test("typical: reset hides and empties card and sheet", async () => {
  const h = setup();
  await mountAndRespond(h, payload());
  h.$("#typical-print-open").click();
  h.env.window.Typical.reset();
  assert.equal(h.$("#typical-card").hidden, true);
  assert.equal(h.$("#typical-sheet-wrap").hidden, true);
  assert.equal(h.$("#typical-list").children.length, 0);
  assert.equal(h.$("#typical-sheet").children.length, 0);
  assert.equal(h.env.document.documentElement.classList.contains("typical-sheet-open"), false);
});

test("typical: remounting after data changed issues a fresh request and redraws", async () => {
  const h = setup();
  await mountAndRespond(h, payload({ items: [item()] }));
  assert.equal(h.$("#typical-list").children.length, 1);
  const before = byPath(h.env, "/api/stats/typical").length;
  h.env.document.dispatchEvent(new FakeEvent("app:data-changed", { bubbles: true }));
  await settle();
  assert.equal(byPath(h.env, "/api/stats/typical").length, before + 1);
});
