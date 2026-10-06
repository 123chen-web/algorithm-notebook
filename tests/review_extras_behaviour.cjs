"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

function setup(api) {
  const env = load(["review-extras.js"]);
  const state = { epoch: 1, user: { id: 7 }, view: "today" };
  const calls = [];
  env.window.ReviewExtras.configure({
    api: api || ((url, init = {}) => {
      const call = { url, init, ...deferred() };
      calls.push(call);
      return call.promise;
    }),
    getEpoch: () => state.epoch,
    getUser: () => state.user,
    getView: () => state.view,
  });
  return { ...env, state, calls, extras: env.window.ReviewExtras };
}
const item = { id: 42, version: 3, title: "边界检查" };
test("extras: hidden parent dialogs in the app shell do not block review shortcuts", () => {
  const env = setup();
  const sheet = env.document.createElement("div");
  sheet.hidden = true;
  const panel = env.document.createElement("div");
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-modal", "true");
  sheet.append(panel);
  env.document.body.append(sheet);
  const event = new FakeEvent("keydown", { props: { key: " ", target: env.document.body } });
  assert.equal(env.extras.blockedKey(event), false, "closed more-sheet must leave review shortcuts usable");
  sheet.hidden = false;
  assert.equal(env.extras.blockedKey(event), true, "an open sheet blocks scoring");
  sheet.setAttribute("aria-hidden", "true");
  assert.equal(env.extras.blockedKey(event), false);
});
const fail = (status, message) => Object.assign(new Error(message), { status });
function key(env, key, props = {}) {
  const event = new FakeEvent("keydown", { props: { key, target: env.document.body, ...props } });
  env.document.dispatchEvent(event);
  return event;
}

test("extras: hide preference defaults on, persists off and survives denied storage", () => {
  const env = setup();
  assert.equal(env.extras.hideReason(), true);
  env.extras.setHideReason(false);
  assert.equal(env.window.localStorage.getItem("review-hide-reason"), "false");
  assert.equal(env.extras.hideReason(), false);
  env.window.localStorage.getItem = () => { throw new Error("denied"); };
  env.window.localStorage.setItem = () => { throw new Error("denied"); };
  assert.equal(env.extras.hideReason(), true);
  assert.doesNotThrow(() => env.extras.setHideReason(false));
});

test("extras: previews share id+version requests and actual interval labels", async () => {
  const env = setup();
  const first = env.extras.preview(item);
  const second = env.extras.preview(item);
  assert.equal(env.calls.length, 1);
  env.calls[0].resolve({ version: 3, previews: { 0: { interval_days: 1 }, 4: { interval_days: 6 } } });
  assert.equal((await first)[4].interval_days, 6);
  assert.equal((await second)[0].interval_days, 1);
  assert.equal(env.extras.intervalText(1), "明天");
  assert.equal(env.extras.intervalText(6), "6 天后");
  await env.extras.preview(item);
  assert.equal(env.calls.length, 1);
  const revised = env.extras.preview({ ...item, version: 4 });
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve({ version: 4, previews: {} });
  await revised;
});

test("extras: preview missing and failed endpoints degrade without a visible interval", async () => {
  for (const status of [404, 503]) {
    const env = setup(async () => { throw fail(status, "暂时不可用"); });
    const button = env.document.createElement("button");
    button.dataset.quality = "4";
    env.document.body.append(button);
    await env.extras.decorateGrades([button], item);
    assert.equal(button.querySelector(".review-interval"), null);
    assert.equal(await env.extras.preview(item), null);
  }
});

test("extras: stale preview after account or view change does not decorate or contaminate cache", async () => {
  const env = setup();
  const button = env.document.createElement("button");
  button.dataset.quality = "4";
  env.document.body.append(button);
  const pending = env.extras.decorateGrades([button], item);
  env.state.epoch += 1;
  env.state.user = { id: 8 };
  env.calls[0].resolve({ version: 3, previews: { 4: { interval_days: 6 } } });
  await pending;
  assert.equal(button.querySelector(".review-interval"), null);
  const fresh = env.extras.preview(item);
  assert.equal(env.calls.length, 2);
  env.state.view = "all";
  env.calls[1].resolve({ version: 3, previews: { 4: { interval_days: 6 } } });
  assert.equal(await fresh, null);
});

test("extras: decorating uses each quality, rejects mismatched preview version", async () => {
  const env = setup(async () => ({ version: 3, previews: { 0: { interval_days: 1 }, 4: { interval_days: 6 } } }));
  const buttons = [0, 4].map((quality) => {
    const button = env.document.createElement("button");
    button.dataset.quality = String(quality);
    env.document.body.append(button);
    return button;
  });
  await env.extras.decorateGrades(buttons, item);
  assert.equal(buttons[0].querySelector(".review-interval").textContent, "明天");
  assert.equal(buttons[1].querySelector(".review-interval").textContent, "6 天后");
  env.extras.reset();
  env.extras.configure({ api: async () => ({ version: 2, previews: { 4: { interval_days: 99 } } }) });
  assert.equal(await env.extras.preview(item), null);
});

test("extras: capture and key guard protect inputs, modifiers, dialogs and open menus", async () => {
  const env = setup();
  const guard = env.extras.capture();
  assert.equal(guard(), true);
  env.state.view = "all";
  assert.equal(guard(), false);
  const input = env.document.createElement("input");
  assert.equal(env.extras.blockedKey({ target: input, key: "1" }), true);
  assert.equal(env.extras.blockedKey({ target: env.document.body, ctrlKey: true }), true);
  const dialog = env.document.createElement("dialog");
  env.document.body.append(dialog);
  dialog.showModal();
  assert.equal(env.extras.blockedKey({ target: env.document.body }), true);
  dialog.close();
  assert.equal(env.extras.blockedKey({ target: env.document.body }), false);
});

test("extras: undo uses reviewed version once, restores before status and ignores editable Ctrl+Z", async () => {
  const requests = [];
  const env = setup(async (url, init = {}) => {
    requests.push({ url, init });
    if (!init.method || init.method === "GET") throw fail(405, "Method Not Allowed");
    return { id: 42, version: 5, due_date: "2026-10-04" };
  });
  let undone = 0;
  env.extras.rememberReview({ item, result: { version: 4, interval_days: 6 }, quality: 4, onUndo(result) {
    assert.equal(result.version, 5);
    assert.match(env.document.querySelector(".review-toast").textContent, /已评分/);
    undone += 1;
  } });
  await tick();
  const input = env.document.createElement("textarea");
  key(env, "z", { ctrlKey: true, target: input });
  assert.equal(requests.length, 1);
  const event = key(env, "z", { ctrlKey: true });
  assert.equal(event.defaultPrevented, true);
  await tick();
  assert.equal(undone, 1);
  assert.deepEqual(JSON.parse(requests[1].init.body), { version: 4 });
  assert.equal(env.document.querySelector(".review-toast").textContent, "已撤销");
  key(env, "z", { ctrlKey: true });
  await tick();
  assert.equal(undone, 1);
  env.extras.reset();
});

test("extras: undo missing hides action; conflict preserves detail and offers retry", async () => {
  const missing = setup(async () => { throw fail(404, "不存在"); });
  missing.extras.rememberReview({ item, result: { version: 4, interval_days: 6 }, quality: 4 });
  await tick();
  assert.equal(missing.document.querySelector(".review-toast-action"), null);
  missing.extras.reset();
  const env = setup(async (_url, init = {}) => { throw fail(init.method === "POST" ? 409 : 405, init.method === "POST" ? "超过 24 小时，不能撤销" : "method"); });
  env.extras.rememberReview({ item, result: { version: 4 }, quality: 4 });
  await tick();
  env.document.querySelector(".review-toast-action").click();
  await tick();
  assert.match(env.document.querySelector(".review-toast").textContent, /超过 24 小时，不能撤销/);
  env.extras.reset();
});

test("extras: only newest review is undoable and a late undo after account switch is dropped", async () => {
  const writes = [];
  const env = setup((url, init = {}) => {
    if (init.method === "GET") return Promise.reject(fail(405, "method"));
    const call = { url, init, ...deferred() };
    writes.push(call);
    return call.promise;
  });
  let restored = 0;
  env.extras.rememberReview({ item, result: { version: 4 }, quality: 4, onUndo() { restored += 1; } });
  await tick();
  env.extras.rememberReview({ item: { ...item, id: 43 }, result: { version: 9 }, quality: 5, onUndo() { restored += 1; } });
  await tick();
  key(env, "z", { metaKey: true });
  assert.match(writes[0].url, /43\/review\/undo$/);
  env.state.epoch += 1;
  writes[0].resolve({ version: 10 });
  await tick();
  assert.equal(restored, 0);
  env.extras.reset();
});

test("extras: more menu probes read-only, moves focus, escapes and sends snooze version", async () => {
  const calls = [];
  const env = setup(async (url, init = {}) => {
    calls.push({ url, init });
    if (init.method === "GET") throw fail(405, "method");
    return { version: 4, due_date: "2026-10-07" };
  });
  let action;
  const menu = env.extras.menu({ item, onAction(...args) { action = args; } });
  env.document.body.append(menu);
  await menu.ready;
  assert.equal(calls.every((call) => call.init.method === "GET"), true);
  const trigger = menu.querySelector(".review-more-button");
  trigger.click();
  assert.equal(menu.querySelector('[role="menu"]').hidden, false);
  assert.equal(env.document.activeElement.dataset.days, "1");
  key(env, "Escape");
  assert.equal(menu.querySelector('[role="menu"]').hidden, true);
  assert.equal(env.document.activeElement, trigger);
  menu.querySelector('[data-days="3"]').click();
  await tick();
  assert.equal(action[0], "snooze");
  assert.equal(action[1], 3);
  assert.deepEqual(JSON.parse(calls.at(-1).init.body), { version: 3, days: 3 });
  menu.destroy();
  env.extras.reset();
});

test("extras: missing menu APIs stay hidden; suspended record gets a restore control", async () => {
  const missing = setup(async () => { throw fail(404, "不存在"); });
  const absent = missing.extras.menu({ item });
  missing.document.body.append(absent);
  await absent.ready;
  assert.equal(absent.hidden, true);
  let restored;
  const env = setup(async (_url, init = {}) => {
    if (init.method === "GET") throw fail(405, "method");
    return { suspended_at: null, version: 4 };
  });
  const menu = env.extras.menu({ item: { ...item, suspended_at: "2026-10-04" }, onAction(action) { restored = action; } });
  env.document.body.append(menu);
  await menu.ready;
  menu.querySelector('[data-review-action="unsuspend"]').click();
  await tick();
  assert.equal(restored, "unsuspend");
  env.extras.reset();
});

test("extras: refreshing the same account preserves cache, capture and undo", async () => {
  const env = setup();
  const guard = env.extras.capture();
  const first = env.extras.preview(item);
  env.calls[0].resolve({ version: 3, previews: { 4: { interval_days: 6 } } });
  await first;
  env.state.user = { id: 7, display_name: "更新后的资料" };
  assert.equal(guard(), true);
  assert.equal((await env.extras.preview(item))[4].interval_days, 6);
  assert.equal(env.calls.length, 1);
});

test("extras: capability network failure hides menu but is retried on next open", async () => {
  let connected = false;
  const calls = [];
  const env = setup(async (url) => {
    calls.push(url);
    throw connected ? fail(405, "method") : new Error("Failed to fetch");
  });
  const first = env.extras.menu({ item });
  env.document.body.append(first);
  await first.ready;
  assert.equal(first.hidden, true);
  first.destroy();
  connected = true;
  const second = env.extras.menu({ item });
  env.document.body.append(second);
  await second.ready;
  assert.equal(second.hidden, false);
  assert.equal(calls.length, 4);
  env.extras.reset();
});

test("extras: suspend survives menu disposal and restore uses new version with duplicate protection", async () => {
  const writes = [];
  const env = setup((url, init = {}) => {
    if (init.method === "GET") return Promise.reject(fail(405, "method"));
    const call = { url, init, ...deferred() };
    writes.push(call);
    return call.promise;
  });
  const actions = [];
  let menu;
  menu = env.extras.menu({ item, onAction(action, days, result) {
    actions.push({ action, days, result });
    menu.destroy();
    menu.remove();
  } });
  env.document.body.append(menu);
  await menu.ready;
  menu.querySelector('[data-review-action="suspend"]').click();
  menu.querySelector('[data-review-action="suspend"]').click();
  assert.equal(writes.length, 1);
  writes[0].resolve({ suspended_at: "2026-10-04", version: 4 });
  await tick();
  const restore = env.document.querySelector(".review-toast-action");
  assert.equal(restore.textContent, "恢复");
  restore.click(); restore.click();
  assert.equal(writes.length, 2);
  assert.deepEqual(JSON.parse(writes[1].init.body), { version: 4 });
  writes[1].resolve({ suspended_at: null, version: 5 });
  await tick();
  assert.deepEqual(actions.map((entry) => entry.action), ["suspend", "unsuspend"]);
  assert.equal(env.document.querySelector(".review-toast").textContent, "已恢复复习");
  env.extras.reset();
});

test("extras: toast expires at eight active seconds and hover/focus independently pause time", () => {
  const env = setup();
  let now = 0;
  let nextId = 0;
  const timers = new Map();
  env.context.Date = { now: () => now };
  env.context.setTimeout = (callback, delay) => {
    const id = ++nextId;
    timers.set(id, { callback, delay });
    return id;
  };
  env.context.clearTimeout = (id) => timers.delete(id);
  const handle = env.extras.notify("已评分");
  assert.equal([...timers.values()][0].delay, 8000);
  now = 2000;
  handle.root.dispatchEvent(new FakeEvent("mouseenter"));
  assert.equal(timers.size, 0);
  handle.root.dispatchEvent(new FakeEvent("focusin"));
  now = 10000;
  handle.root.dispatchEvent(new FakeEvent("mouseleave"));
  assert.equal(timers.size, 0, "focused toast remains paused after pointer leaves");
  handle.root.dispatchEvent(new FakeEvent("focusout"));
  const timer = [...timers.values()][0];
  assert.equal(timer.delay, 6000);
  timer.callback();
  assert.equal(handle.root.isConnected, false);
  env.extras.reset();
});

test("extras: missing restore API hides restore even when suspend exists", async () => {
  const env = setup(async (url, init = {}) => {
    if (init.method === "GET") throw fail(url.endsWith("/unsuspend") ? 404 : 405, "method");
    return { suspended_at: "2026-10-04", version: 4 };
  });
  const menu = env.extras.menu({ item });
  env.document.body.append(menu);
  await menu.ready;
  menu.querySelector('[data-review-action="suspend"]').click();
  await tick();
  assert.equal(env.document.querySelector(".review-toast").textContent, "已暂停这条复习");
  assert.equal(env.document.querySelector(".review-toast-action"), null);
  env.extras.reset();
});

test("extras: active focus notices live inside its modal shell and close discards them", () => {
  const env = setup();
  const focus = env.document.createElement("div");
  focus.id = "focus";
  const shell = env.document.createElement("div");
  shell.className = "focus-shell";
  shell.setAttribute("role", "dialog");
  shell.setAttribute("aria-modal", "true");
  focus.append(shell);
  env.document.body.append(focus);
  const notice = env.extras.notify("已评分", { actionLabel: "撤销", onAction() {} });
  assert.equal(notice.root.parentNode, shell);
  assert.equal(shell.querySelector(".review-toast-action").textContent, "撤销");
  env.document.dispatchEvent(new FakeEvent("focus:closed"));
  assert.equal(notice.root.isConnected, false);
  env.extras.reset();
});

test("extras: two pending decorations of the same grade row produce one interval label", async () => {
  const env = setup();
  const button = env.document.createElement("button");
  button.dataset.quality = "4";
  env.document.body.append(button);
  const first = env.extras.decorateGrades([button], item);
  const second = env.extras.decorateGrades([button], item);
  assert.equal(env.calls.length, 1);
  env.calls[0].resolve({ version: 3, previews: { 4: { interval_days: 6 } } });
  await Promise.all([first, second]);
  assert.equal(button.querySelectorAll(".review-interval").length, 1);
  assert.equal(button.querySelector(".review-interval").textContent, "6 天后");
  env.extras.reset();
});
