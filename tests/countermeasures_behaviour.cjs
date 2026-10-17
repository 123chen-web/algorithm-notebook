"use strict";

/* “我给自己定的对策”（static/countermeasures.js）：复习时把自己写过的对策串回来。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

function setup(response) {
  const env = load(["countermeasures.js"]);
  const calls = [];
  const opened = [];
  const host = env.document.createElement("div");
  env.document.body.append(host);
  const hooks = {
    api: async (path) => { calls.push(path); if (response instanceof Error) throw response; return response; },
    isCurrent: () => true,
    openNote: (id) => opened.push(id),
  };
  return { env, host, hooks, calls, opened };
}

test("有对策时按错因分块列出，点“打开笔记”打开那篇；用户写的 <b> 只是文字", async () => {
  const { env, host, hooks, calls, opened } = setup({ items: [
    { note_id: 7, label: "边界/特例", tag: "boundary", lines: ["先写清 <b>区间</b>", "代入 n=0 走一遍"] },
  ] });
  await env.window.Countermeasures.mount(host, { id: 3 }, hooks);
  assert.deepEqual(calls, ["/api/mistakes/3/countermeasures"]);
  assert.equal(host.querySelector(".cm-title").textContent, "我给自己定的对策");
  assert.match(host.querySelector(".cm-label").textContent, /「边界\/特例」/);
  assert.deepEqual([...host.querySelectorAll(".cm-lines li")].map((li) => li.textContent), ["先写清 <b>区间</b>", "代入 n=0 走一遍"]);
  assert.equal(host.querySelector("b"), null);
  host.querySelector(".cm-open").click();
  assert.deepEqual(opened, [7]);
});

test("没写过对策、请求失败、或已经切到别的题时什么也不显示", async () => {
  for (const response of [{ items: [] }, { items: [{ note_id: 1, label: "x", lines: [] }] }, new Error("offline")]) {
    const { env, host, hooks } = setup(response);
    await env.window.Countermeasures.mount(host, { id: 3 }, hooks);
    assert.equal(host.childNodes.length, 0);
  }
  const { env, host, hooks } = setup({ items: [{ note_id: 1, label: "x", lines: ["y"] }] });
  hooks.isCurrent = () => false;
  await env.window.Countermeasures.mount(host, { id: 3 }, hooks);
  await tick();
  assert.equal(host.childNodes.length, 0);
});
