"use strict";

/* 阅读页里的“导图”和“打印”（notes.js 与 notes-map.js 一起跑）：
   - 正文 / 导图 切换；导图第一次点开才画；关联数据到了多一枝；点 ↗ 回到正文；
   - 打印：给 <html> 打上标记，打印时折叠的答案是展开的、“遮住看”是关掉的，打完（afterprint）全部还原；
   - 关联列表默认收成一行。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

const NOTE = {
  id: 5,
  title: "二分查找",
  content: "# 触发信号\n- 有序数组\n- 要求 O(log n)\n\n# 常见坑\n- 死循环\n\n> [!答案]- 先想想\n> 答案在这里",
  tags: ["二分"], problem_id: null, pinned: false,
  created_at: "2026-10-14T08:00:00+00:00", updated_at: "2026-10-14T08:00:00+00:00",
};

function setup() {
  const listeners = {};
  const prints = [];
  const env = load(["notes-pane.js", "notes.js", "notes-map.js", "notes-connect.js"], {
    extra: {
      addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
      removeEventListener() {},
      print() { prints.push(snapshot()); },
    },
  });
  const document = env.document;
  const snapshot = () => ({
    mode: document.documentElement.dataset.printNote,
    openDetails: document.querySelectorAll("details").filter((d) => d.open).length,
    covered: Boolean(document.querySelector(".notes-reader-body")?.classList.contains("is-covered")),
  });
  env.window.Notes.configure({
    async api(path, options = {}) {
      const response = await env.window.fetch(path, options);
      const body = await response.json();
      if (!response.ok) { const error = new Error(body.detail); error.status = response.status; throw error; }
      return body;
    },
    getUser: () => ({ id: 7 }), getEpoch: () => 1, getView: () => "notes",
    confirm: () => true, notify() {},
  });
  env.window.NotesMap.configure({});
  return { env, document, listeners, prints };
}

async function openNote(env, document) {
  const loading = env.window.Notes.load();
  await tick();
  env.respond(env.calls.at(-1), 200, { problems: [] });
  await tick(); await tick();
  env.respond(env.calls.at(-1), 200, { notes: [{ ...NOTE }], total: 1 });
  await loading;
  document.querySelector("#notes-list").querySelector(".notes-card-open").click();
  await tick(); await tick();
  return document.querySelector("#notes-pane-note");
}

const button = (pane, text) => [...pane.querySelectorAll("button")].find((b) => b.textContent === text);

test("阅读页有“正文 / 导图”切换，默认是正文；导图第一次点开才画", async () => {
  const { env, document } = setup();
  const pane = await openNote(env, document);
  const body = pane.querySelector(".notes-reader-body");
  const host = pane.querySelector(".nm-host");
  assert.equal(body.hidden, false);
  assert.equal(host.hidden, true);
  assert.equal(host.querySelectorAll("svg").length, 0, "没点开之前不画");
  assert.equal(button(pane, "正文").getAttribute("aria-pressed"), "true");
  button(pane, "导图").click();
  assert.equal(body.hidden, true);
  assert.equal(host.hidden, false);
  assert.equal(host.querySelectorAll("svg").length, 1);
  const labels = host.querySelectorAll(".nm-text").map((n) => n.textContent);
  assert.ok(labels.includes("二分查找") && labels.includes("触发信号") && labels.includes("有序数组"), labels.join("|"));
  button(pane, "正文").click();
  assert.equal(body.hidden, false);
  assert.equal(host.hidden, true);
});

test("点导图里标题旁的 ↗：回到正文", async () => {
  const { env, document } = setup();
  const pane = await openNote(env, document);
  const body = pane.querySelector(".notes-reader-body");
  button(pane, "导图").click();
  pane.querySelector(".nm-host").querySelector(".nm-jump").click();
  assert.equal(body.hidden, false);
  assert.equal(pane.querySelector(".nm-host").hidden, true);
});

test("打印：打印时 <html> 有标记、答案展开、遮住看关闭；afterprint 之后全部还原", async () => {
  const { env, document, listeners, prints } = setup();
  const pane = await openNote(env, document);
  const body = pane.querySelector(".notes-reader-body");
  assert.ok(body.querySelectorAll("details").length >= 1, "样例里有一个折叠的答案");
  assert.equal(body.querySelectorAll("details").filter((d) => d.open).length, 0);
  button(pane, "遮住看").click();
  assert.ok(body.classList.contains("is-covered"));
  button(pane, "打印").click();
  assert.equal(prints.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(prints[0])), { mode: "text", openDetails: body.querySelectorAll("details").length, covered: false });
  // 打印对话框关掉之后浏览器发 afterprint
  assert.equal(listeners.afterprint.length, 1);
  listeners.afterprint[0]();
  assert.equal(document.documentElement.dataset.printNote, undefined, "标记去掉");
  assert.equal(body.querySelectorAll("details").filter((d) => d.open).length, 0, "折叠回去");
  assert.ok(body.classList.contains("is-covered"), "遮住看还原");
});

test("打印：在导图页打印的是导图（mode = map）", async () => {
  const { env, document, listeners, prints } = setup();
  const pane = await openNote(env, document);
  button(pane, "导图").click();
  button(pane, "打印").click();
  assert.equal(prints[0].mode, "map");
  listeners.afterprint[0]();
  assert.equal(document.documentElement.dataset.printNote, undefined);
});

test("关联列表默认收成一行：没有数据时整块不显示，有数据时是折叠的", async () => {
  const { env, document } = setup();
  env.window.NotesConnect.configure({
    api: (path) => env.window.fetch(path).then((r) => r.json()),
    today: () => "2026-10-15", noteTitle: (n) => n.title, previewText: () => "",
    fetchPatterns: () => Promise.resolve([]), openNote() {}, openMistake() {}, openPattern() {},
  });
  const loading = env.window.Notes.load();
  await tick();
  env.respond(env.calls.at(-1), 200, { problems: [] });
  await tick(); await tick();
  env.respond(env.calls.at(-1), 200, { notes: [{ ...NOTE }], total: 1 });
  await loading;
  document.querySelector("#notes-list").querySelector(".notes-card-open").click();
  await tick(); await tick();
  const connections = env.calls.find((call) => call.url.includes("/api/notes/5/connections"));
  assert.ok(connections, "会去取关联数据");
  const pane = document.querySelector("#notes-pane-note");
  const fold = pane.querySelector(".nc-fold");
  assert.equal(fold.hidden, true, "数据没到之前不显示");
  env.respond(connections, 200, { mistakes: [{ id: 1, title: "两数之和", tags: [], due_date: "2026-10-16" }], patterns: [], note_mentions: [], mentioned_by: [], same_tag: [] });
  await tick(); await tick(); await tick();
  assert.equal(fold.hidden, false);
  assert.ok(!fold.open, "默认折叠");
  button(pane, "导图").click();
  const labels = pane.querySelector(".nm-host").querySelectorAll(".nm-text").map((n) => n.textContent);
  assert.ok(labels.includes("关联") && labels.includes("两数之和"), labels.join("|"));
});
