"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "static", "view-history.js"), "utf8");

function setup() {
  const docHandlers = {};
  const winHandlers = {};
  const observers = [];
  const calls = [];
  const shown = [];
  const root = { dataset: { view: "app" } };
  const history = {
    state: null,
    replaceState(state) { calls.push(["replace", state]); this.state = state; },
    pushState(state) { calls.push(["push", state]); this.state = state; },
  };
  const window = {
    addEventListener(name, fn) { winHandlers[name] = fn; },
    showView(view) { shown.push(view); fire(view); return Promise.resolve(); },
  };
  const document = {
    documentElement: root,
    addEventListener(name, fn) { docHandlers[name] = fn; },
  };
  class FakeObserver {
    constructor(fn) { this.fn = fn; observers.push(this); }
    observe() {}
  }
  const location = { href: "https://ouyeoy.com/#/app" };
  const context = { window, document, history, location, MutationObserver: FakeObserver, Promise };
  vm.createContext(context);
  vm.runInContext(SOURCE, context);
  function fire(view) { docHandlers["app:view-changed"]({ detail: { view } }); }
  async function pop(state) {
    history.state = state;
    winHandlers.popstate({ state });
    await new Promise((resolve) => setImmediate(resolve));
  }
  function setPage(value) { root.dataset.view = value; observers.forEach((o) => o.fn()); }
  return { fire, pop, setPage, calls, shown, history, window };
}

test("the first page after login only marks the current history entry", () => {
  const env = setup();
  env.fire("home");
  assert.equal(JSON.stringify(env.calls), JSON.stringify([["replace", { oyView: "home" }]]));
});

test("switching pages pushes one history entry each, but not for the same page", () => {
  const env = setup();
  env.fire("home"); env.fire("notes"); env.fire("notes"); env.fire("roadmap");
  assert.equal(env.calls.map((c) => c[0]).join(), "replace,push,push");
  assert.equal(JSON.stringify(env.calls.at(-1)[1]), JSON.stringify({ oyView: "roadmap" }));
});

test("back goes to the previous page without pushing history again", async () => {
  const env = setup();
  env.fire("home"); env.fire("notes");
  const before = env.calls.length;
  await env.pop({ oyView: "home" });
  assert.equal(env.shown.join(), "home");
  assert.equal(env.calls.length, before, "回退本身不能再压入历史");
  env.fire("plan"); // 回退结束后的正常换页要继续压历史
  assert.equal(env.calls.at(-1)[0], "push");
});

test("history entries without oyView (e.g. the drawing panel) are ignored", async () => {
  const env = setup();
  env.fire("home"); env.fire("notes");
  await env.pop({ oyDraw: true });
  await env.pop(null);
  assert.equal(env.shown.length, 0);
});

test("popping to the page that is already shown does nothing", async () => {
  const env = setup();
  env.fire("home"); env.fire("notes");
  await env.pop({ oyView: "notes" });
  assert.equal(env.shown.length, 0);
});

test("nothing happens outside the app (welcome / login pages)", async () => {
  const env = setup();
  env.fire("home");
  env.setPage("welcome");
  env.calls.length = 0;
  env.fire("notes");
  await env.pop({ oyView: "home" });
  assert.equal(env.calls.length, 0);
  assert.equal(env.shown.length, 0);
});

test("after signing out and in again the first page only marks the entry again", () => {
  const env = setup();
  env.fire("home"); env.fire("notes");
  env.setPage("welcome");
  env.setPage("app");
  env.calls.length = 0;
  env.fire("home");
  assert.equal(env.calls.map((c) => c[0]).join(), "replace");
});
