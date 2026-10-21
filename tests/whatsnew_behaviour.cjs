"use strict";

/* 「更新了什么」（static/whatsnew.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs。 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { load, tick } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const release = (date, items, title = "标题") => ({ date, title, items });
const SIX = [
  release("2026-10-21", ["最新一条"]),
  release("2026-10-20", ["b"]),
  release("2026-10-18", ["c"]),
  release("2026-10-17", ["d"]),
  release("2026-10-14", ["e"]),
  release("2026-10-01", ["最旧一条"]),
];

/** 页面元素要先摆好再执行脚本（脚本一加载就会发请求），所以不用 load([...files])，自己执行。 */
function boot(env) {
  const source = fs.readFileSync(path.join(__dirname, "..", "static", "whatsnew.js"), "utf8");
  vm.runInContext(source, env.context, { filename: "whatsnew.js" });
}

function helpPage(extra = {}) {
  const env = load([], { extra });
  const list = env.document.createElement("div");
  list.id = "whats-new-list";
  const section = env.document.createElement("section");
  section.id = "whats-new";
  section.append(list);
  env.document.body.append(section);
  return { env, list, section };
}

function addDot(env) {
  const dot = env.document.createElement("span");
  dot.setAttribute("data-whatsnew-dot", "");
  dot.hidden = true;
  env.document.body.append(dot);
  return dot;
}

async function answer(env, status, body) {
  await tick();
  env.respond(env.calls.at(-1), status, body);
  await tick();
  await tick();
}

test("renders newest first, only the latest 5, with textContent", async () => {
  const { env, list } = helpPage();
  boot(env);
  await answer(env, 200, SIX);
  assert.equal(env.calls[0].url, "/static/data/changelog.json");
  const articles = list.querySelectorAll("article");
  assert.equal(articles.length, 5);
  assert.deepEqual(
    articles.map((a) => a.querySelector("time").textContent),
    ["2026-10-21", "2026-10-20", "2026-10-18", "2026-10-17", "2026-10-14"],
  );
  assert.ok(!list.textContent.includes("最旧一条"));
  assert.ok(list.textContent.includes("最新一条"));
});

test("item text is shown literally, never parsed as HTML", async () => {
  const { env, list } = helpPage();
  boot(env);
  await answer(env, 200, [release("2026-10-21", ["<img src=x onerror=alert(1)> & <b>粗</b>"], "<i>t</i>")]);
  assert.equal(list.querySelectorAll("img").length, 0);
  assert.equal(list.querySelectorAll("b").length, 0);
  assert.equal(list.querySelectorAll("i").length, 0);
  assert.ok(list.textContent.includes("<img src=x onerror=alert(1)> & <b>粗</b>"));
});

test("a failed fetch shows a friendly message instead of crashing", async () => {
  for (const fail of [(env) => env.respond(env.calls.at(-1), 500, {}), (env) => env.calls.at(-1).reject(new Error("offline"))]) {
    const { env, list } = helpPage();
    boot(env);
    await tick();
    fail(env);
    await tick();
    await tick();
    assert.ok(list.textContent.includes("暂时没加载出来"));
    assert.equal(list.querySelectorAll("article").length, 0);
  }
});

test("garbage data (not an array) shows the friendly message", async () => {
  const { env, list } = helpPage();
  boot(env);
  await answer(env, 200, { nope: true });
  assert.ok(list.textContent.includes("暂时没加载出来"));
});

test("opening the section (no IntersectionObserver) records the newest date and hides the dot", async () => {
  const { env, list } = helpPage();
  const dot = addDot(env);
  dot.hidden = false;
  boot(env);
  await answer(env, 200, SIX);
  assert.equal(env.window.localStorage.getItem("oy-whatsnew-seen"), "2026-10-21");
  assert.equal(dot.hidden, true);
  assert.equal(list.querySelectorAll("article").length, 5);
});

test("with IntersectionObserver the date is only recorded once the section is seen", async () => {
  let observer = null;
  class FakeObserver {
    constructor(callback) { this.callback = callback; observer = this; }
    observe(node) { this.node = node; }
    disconnect() {}
  }
  const { env, section } = helpPage({ IntersectionObserver: FakeObserver });
  boot(env);
  await answer(env, 200, SIX);
  assert.equal(env.window.localStorage.getItem("oy-whatsnew-seen"), null);
  assert.equal(observer.node, section);
  observer.callback([{ isIntersecting: false }]);
  assert.equal(env.window.localStorage.getItem("oy-whatsnew-seen"), null);
  observer.callback([{ isIntersecting: true }]);
  assert.equal(env.window.localStorage.getItem("oy-whatsnew-seen"), "2026-10-21");
});

test("main page: dot shows when the newest release is newer than the stored date", async () => {
  for (const [seen, visible] of [[undefined, true], ["2026-10-20", true], ["2026-10-21", false], ["2026-12-01", false]]) {
    const env = load([]);
    if (seen !== undefined) env.window.localStorage.setItem("oy-whatsnew-seen", seen);
    const dot = addDot(env);
    boot(env);
    await answer(env, 200, SIX);
    assert.equal(dot.hidden, !visible, `seen=${seen}`);
  }
});

test("main page: a failed fetch leaves the dot hidden", async () => {
  const env = load([]);
  const dot = addDot(env);
  boot(env);
  await answer(env, 500, {});
  assert.equal(dot.hidden, true);
});

test("main page: unusable localStorage never throws and counts as not seen", async () => {
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const env = load([], { extra: { localStorage: blocked } });
  const dot = addDot(env);
  boot(env);
  await answer(env, 200, SIX);
  assert.equal(dot.hidden, false);
});
