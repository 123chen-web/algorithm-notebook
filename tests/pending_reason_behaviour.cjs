"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, FakeEvent, deferred } = require("./js_harness.cjs");
const APP = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");

function productionFunction(name) {
  const match = APP.match(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
  assert.ok(match, `production ${name} exists`);
  return APP.slice(match.index, APP.indexOf("\n}", match.index) + 2);
}

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0), []));
const settle = async () => { await tick(); await tick(); };
const item = () => ({ id: 11, version: 7, pending_reason: true,
  description: "（待补：为什么错）", tags: [], due_date: "2026-10-10", repetitions: 2 });

function setup(view = "all") {
  const env = load(["pending-reason.js"]);
  env.document.querySelector("#home-page");
  const state = { user: { id: 7 }, epoch: 1, view, detail: 1 };
  const saved = [], opened = [];
  const hooks = {
    getUser: () => state.user, getEpoch: () => state.epoch, getView: () => state.view,
    getDetailGeneration: () => state.detail,
    api: async (path, options = {}) => {
      const response = await env.window.fetch(path, {
        ...options, headers: { "X-CSRF-Protection": "1" },
      });
      const body = await response.json();
      if (!response.ok) { const error = new Error(body.detail || "请求失败"); error.status = response.status; throw error; }
      return body;
    },
    onSaved: (id) => saved.push(id),
    showView: async (next) => {
      state.view = next;
      env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view: next } }));
    },
    openMistake: async (id) => opened.push(id),
  };
  env.window.PendingReason.configure(hooks);
  return { env, state, saved, opened, hooks };
}

async function render(ctx, record = item(), suggestions = { mine: [], builtin: ["边界", "粗心", "溢出", "漏条件"] }) {
  const form = ctx.env.window.PendingReason.render(record);
  ctx.env.document.body.append(form);
  assert.equal(ctx.env.calls[0].url, "/api/tags/suggest");
  ctx.env.respond(ctx.env.calls[0], 200, suggestions);
  await settle();
  return form;
}
const q = (form, selector) => form.querySelector(selector);
function submit(form, text = "循环边界少了等号。") {
  q(form, "textarea").value = text;
  form.dispatchEvent(new FakeEvent("submit"));
}

test("pending reason: ordinary records keep the existing renderer", () => {
  const ctx = setup();
  assert.equal(ctx.env.window.PendingReason.render({ ...item(), pending_reason: false }), null);
  assert.equal(ctx.env.calls.length, 0);
});

test("pending reason: text is safe, suggestions deduplicate and selection stops at three", async () => {
  const ctx = setup();
  const form = await render(ctx, item(), { mine: [{ tag: "<img src=x onerror=alert(1)>" }, { tag: "边界" }], builtin: ["边界", "粗心", "溢出", "漏条件"] });
  assert.equal(q(form, "img"), null);
  assert.equal(form.textContent.includes("（待补：为什么错）"), false);
  const tags = form.querySelectorAll(".pending-reason-tag");
  assert.equal(tags.length, 4); // oversized remote tag is discarded; duplicate is removed
  for (const index of [0, 1, 2]) form.querySelectorAll(".pending-reason-tag")[index].click();
  assert.equal(form.querySelectorAll(".pending-reason-tag")[3].disabled, true);
  form.querySelectorAll(".pending-reason-tag")[3].click();
  assert.equal(form.querySelectorAll('[aria-pressed="true"]').length, 3);
  assert.equal(q(form, ".pending-reason-count").textContent, "已选 3 / 3 个标签");
  form.querySelectorAll(".pending-reason-tag")[0].click();
  assert.equal(form.querySelectorAll(".pending-reason-tag")[3].disabled, false);
});

test("pending reason: existing and custom tags stay as literal text", async () => {
  const ctx = setup();
  const form = await render(ctx, { ...item(), tags: ["<b>标签</b>"] });
  assert.equal(q(form, ".pending-reason-tag").textContent, "<b>标签</b>");
  assert.equal(q(form, "b"), null);
  q(form, ".pending-reason-custom").value = "  漏   条件  ";
  q(form, ".pending-reason-add").click();
  assert.equal(form.querySelectorAll('[aria-pressed="true"]').length, 2);
  assert.ok(form.textContent.includes("漏 条件"));
});

test("pending reason: empty reason sends nothing and keeps the form", async () => {
  const ctx = setup(); const form = await render(ctx);
  submit(form, " \n\t ");
  await settle();
  assert.equal(ctx.env.calls.length, 1);
  assert.match(q(form, ".pending-reason-status").textContent, /一句原因/);
});

test("pending reason: submit uses CSRF api, version and at most three tags without scheduling fields", async () => {
  const ctx = setup(); const record = item(); const form = await render(ctx, record);
  q(form, ".pending-reason-tag").click();
  submit(form, "  边界少了等号。  ");
  submit(form); // double-submit while busy is ignored
  assert.equal(ctx.env.calls.length, 2);
  const call = ctx.env.calls[1];
  assert.equal(call.url, "/api/mistakes/11/reason");
  assert.equal(call.init.method, "POST");
  assert.equal(call.init.headers["X-CSRF-Protection"], "1");
  assert.deepEqual(JSON.parse(call.init.body), { description: "边界少了等号。", tags: ["边界"], version: 7 });
  ctx.env.respond(call, 200, { id: 11, version: 8, pending_reason: false, description: "边界少了等号。", tags: ["边界"] });
  await settle();
  assert.deepEqual(ctx.saved, [11]);
  assert.equal(record.version, 8);
  assert.equal(record.pending_reason, false);
  assert.equal(record.due_date, "2026-10-10");
  assert.equal(record.repetitions, 2);
});

test("pending reason: a failed save retains the draft and selection", async () => {
  const ctx = setup(); const form = await render(ctx);
  q(form, ".pending-reason-tag").click(); submit(form, "保留这句草稿");
  ctx.env.respond(ctx.env.calls[1], 422, { detail: "标签过多" }); await settle();
  assert.equal(q(form, "textarea").value, "保留这句草稿");
  assert.equal(form.querySelectorAll('[aria-pressed="true"]').length, 1);
  assert.match(q(form, ".pending-reason-status").textContent, /标签过多/);
  assert.equal(q(form, ".pending-reason-save").disabled, false);
  assert.deepEqual(ctx.saved, []);
});

test("pending reason: conflict refreshes the version but requires another explicit save", async () => {
  const ctx = setup(); const form = await render(ctx);
  submit(form, "我的草稿"); ctx.env.respond(ctx.env.calls[1], 409, { detail: "这条记录已更新" }); await settle();
  assert.equal(q(form, "textarea").value, "我的草稿");
  assert.equal(q(form, ".pending-reason-refresh").hidden, false);
  q(form, ".pending-reason-refresh").click();
  assert.equal(ctx.env.calls[2].url, "/api/mistakes/11");
  ctx.env.respond(ctx.env.calls[2], 200, { ...item(), version: 9 }); await settle();
  assert.equal(ctx.env.calls.length, 3);
  assert.equal(q(form, "textarea").value, "我的草稿");
  submit(form, "我的草稿");
  assert.equal(JSON.parse(ctx.env.calls[3].init.body).version, 9);
  ctx.env.respond(ctx.env.calls[3], 200, { ...item(), version: 10, pending_reason: false, description: "我的草稿" }); await settle();
});

test("pending reason: another device's completed reason is shown without replacing the draft", async () => {
  const ctx = setup(); const form = await render(ctx);
  submit(form, "我的草稿"); ctx.env.respond(ctx.env.calls[1], 409, { detail: "冲突" }); await settle();
  q(form, ".pending-reason-refresh").click();
  ctx.env.respond(ctx.env.calls[2], 200, { ...item(), version: 9, pending_reason: false, description: "其他设备的原因" }); await settle();
  assert.equal(q(form, "textarea").value, "我的草稿");
  assert.match(q(form, ".pending-reason-status").textContent, /其他设备已补充/);
  assert.equal(q(form, ".pending-reason-server").textContent, "其他设备的原因");
  assert.equal(q(form, ".pending-reason-save").disabled, true);
  submit(form, "我的草稿"); assert.equal(ctx.env.calls.length, 3);
  q(form, ".pending-reason-reopen").click(); await settle();
  assert.deepEqual(ctx.opened, [11]);
});

for (const change of ["epoch", "user", "view", "detail", "detach", "leave-return"]) {
  test(`pending reason: late successful save is ignored after ${change}`, async () => {
    const ctx = setup(); const record = item(); const form = await render(ctx, record);
    submit(form);
    if (change === "epoch") ctx.state.epoch += 1;
    if (change === "user") ctx.state.user = { id: 8 };
    if (change === "view") ctx.state.view = "home";
    if (change === "detail") ctx.state.detail += 1;
    if (change === "detach") form.remove();
    if (change === "leave-return") {
      await ctx.hooks.showView("home"); await ctx.hooks.showView("all");
    }
    ctx.env.respond(ctx.env.calls[1], 200, { id: 11, version: 8, pending_reason: false, description: "晚到" }); await settle();
    assert.deepEqual(ctx.saved, []);
    assert.equal(record.pending_reason, true);
    assert.equal(record.version, 7);
  });
}

test("pending reason: detached and replaced forms ignore late tag suggestions", async () => {
  const ctx = setup(); const form = ctx.env.window.PendingReason.render(item());
  ctx.env.document.body.append(form); form.remove();
  ctx.env.respond(ctx.env.calls[0], 200, { mine: [], builtin: ["旧请求"] }); await settle();
  assert.equal(form.textContent.includes("旧请求"), false);
});

test("pending reason: failed suggestions allow a reason with no tag", async () => {
  const ctx = setup(); const form = ctx.env.window.PendingReason.render(item());
  ctx.env.document.body.append(form);
  ctx.env.respond(ctx.env.calls[0], 503, { detail: "建议暂不可用" }); await settle();
  submit(form); assert.equal(ctx.env.calls.length, 2);
  assert.deepEqual(JSON.parse(ctx.env.calls[1].init.body).tags, []);
  ctx.env.respond(ctx.env.calls[1], 200, { ...item(), pending_reason: false, version: 8 }); await settle();
});

function home(ctx, count = 2) {
  ctx.env.document.dispatchEvent(new FakeEvent("app:home-rendered", {
    detail: { user: { id: 7 }, overview: { pending_reason_count: count } },
  }));
  return ctx.env.document.getElementById("pending-reason-reminder");
}

function productionHome(ctx) {
  Object.assign(ctx.env.context, {
    $: (selector) => ctx.env.document.querySelector(selector),
    user: ctx.state.user, sessionEpoch: ctx.state.epoch, view: ctx.state.view,
    finishHomeOpening: null, api: ctx.hooks.api, updateUserInfo() {}, startHomeOpening() {},
  });
  ctx.env.window.Overview = { reset() {}, loadTrend() {}, renderError() {} };
  for (const name of ["resetHomeSummary", "loadHome"]) {
    vm.runInContext(productionFunction(name), ctx.env.context);
  }
  return ctx.env.context.loadHome({ refreshUser: false });
}

function productionSavedHook(ctx) {
  const context = ctx.env.context;
  ctx.messages = [];
  for (const [name, key] of [["user", "user"], ["sessionEpoch", "epoch"], ["view", "view"],
    ["rvfDetailGeneration", "detail"]]) {
    Object.defineProperty(context, name, { configurable: true,
      get: () => ctx.state[key], set: (value) => { ctx.state[key] = value; } });
  }
  Object.assign(context, { api: ctx.hooks.api, rvfPageGeneration: 1, rvfDetailState: null,
    notifyDataChanged() {}, updateUserInfo() {},
    message: (text, error) => ctx.messages.push({ text, error }),
    renderDetail: (record) => ctx.opened.push(record.id),
  });
  for (const name of ["rvfPageGuard", "rvfClearDetail", "openMistake"]) {
    vm.runInContext(productionFunction(name), context);
  }
  if (APP.includes("async function pendingReasonSaved(")) {
    vm.runInContext(productionFunction("pendingReasonSaved"), context);
  }
  const wiring = APP.slice(APP.indexOf("window.PendingReason?.configure("));
  const callback = wiring.split("onSaved: ")[1].split("\n")[0].trim().replace(/,$/, "");
  vm.runInContext("hostSaved = " + callback + ";", context);
  ctx.hooks.onSaved = context.hostSaved;
  ctx.env.window.PendingReason.configure(ctx.hooks);
}

test("pending reason: production saved hook reports failed detail refresh without freezing or resubmitting", async () => {
  const ctx = setup(); productionSavedHook(ctx); const form = await render(ctx);
  submit(form); ctx.env.respond(ctx.env.calls[1], 200, {
    id: 11, version: 8, pending_reason: false, description: "已保存", tags: [],
  }); await settle();
  assert.equal(ctx.env.calls[2].url, "/api/mistakes/11");
  assert.equal(form.getAttribute("aria-busy"), "false");
  assert.equal(q(form, ".pending-reason-save").disabled, true);
  ctx.env.respond(ctx.env.calls[2], 503, { detail: "详情不可用" }); await settle();
  assert.equal(ctx.messages.length, 1);
  assert.match(ctx.messages[0].text, /原因已保存.*重新/);
  submit(form); await settle(); assert.equal(ctx.env.calls.length, 3);
});

for (const change of ["epoch", "user", "view", "detail"]) {
  test(`pending reason: production saved refresh ignores late failures after ${change}`, async () => {
    const ctx = setup(); productionSavedHook(ctx); const form = await render(ctx);
    submit(form); ctx.env.respond(ctx.env.calls[1], 200, {
      id: 11, version: 8, pending_reason: false, description: "已保存", tags: [],
    }); await settle();
    if (change === "epoch") ctx.state.epoch += 1;
    if (change === "user") ctx.state.user = { id: 8 };
    if (change === "view") ctx.state.view = "home";
    if (change === "detail") ctx.state.detail += 1;
    ctx.env.respond(ctx.env.calls[2], 503, { detail: "晚到的详情错误" }); await settle();
    assert.deepEqual(ctx.messages, []);
  });
}

test("pending reason: production home failure clears old reminder and its in-flight request", async () => {
  const ctx = setup("home"); const old = home(ctx); q(old, "button").click();
  const loading = productionHome(ctx);
  assert.equal(Boolean(ctx.env.document.getElementById("pending-reason-reminder")), false);
  ctx.env.respond(ctx.env.calls[0], 200, { items: [{ id: 11 }] });
  ctx.env.respond(ctx.env.calls[1], 503, { detail: "总览不可用" });
  await loading; await settle();
  assert.equal(Boolean(ctx.env.document.getElementById("pending-reason-reminder")), false);
  assert.deepEqual(ctx.opened, []);
});

test("pending reason: production home failure cannot show a previous account's count", async () => {
  const ctx = setup("home"); home(ctx, 9);
  ctx.state.user = { id: 8 }; ctx.state.epoch += 1;
  const loading = productionHome(ctx);
  ctx.env.respond(ctx.env.calls[0], 503, { detail: "总览不可用" });
  await loading;
  assert.equal(Boolean(ctx.env.document.getElementById("pending-reason-reminder")), false);
});

test("pending reason: overview reminder opens the owner's pending record through all records", async () => {
  const ctx = setup("home"); const reminder = home(ctx);
  assert.match(reminder.textContent, /2 条/);
  q(reminder, "button").click();
  assert.equal(ctx.env.calls[0].url, "/api/mistakes?due_only=false&pending_reason=1");
  ctx.env.respond(ctx.env.calls[0], 200, { items: [{ id: 11 }] }); await settle();
  assert.equal(ctx.state.view, "all"); assert.deepEqual(ctx.opened, [11]);
});

test("pending reason: overview refresh replaces reminders and ignores old pending responses", async () => {
  const ctx = setup("home"); const old = home(ctx); q(old, "button").click();
  home(ctx, 0);
  ctx.env.respond(ctx.env.calls[0], 200, { items: [{ id: 11 }] }); await settle();
  assert.equal(ctx.env.document.getElementById("pending-reason-reminder"), null);
  assert.deepEqual(ctx.opened, []);
});

test("pending reason: navigating away during the transition cannot open a late record", async () => {
  const ctx = setup("home"); const waiting = deferred();
  ctx.hooks.showView = async (view) => {
    ctx.state.view = view;
    ctx.env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view } }));
    await waiting.promise;
  };
  ctx.env.window.PendingReason.configure(ctx.hooks);
  const reminder = home(ctx); q(reminder, "button").click();
  ctx.env.respond(ctx.env.calls[0], 200, { items: [{ id: 11 }] }); await settle();
  ctx.state.view = "new";
  ctx.env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view: "new" } }));
  waiting.resolve(); await settle(); assert.deepEqual(ctx.opened, []);
});
