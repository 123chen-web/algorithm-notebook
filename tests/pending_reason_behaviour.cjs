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
  const state = { user: { id: 7 }, epoch: 1, view, detail: 1, selection: 0 };
  const saved = [], opened = [];
  const hooks = {
    getUser: () => state.user, getEpoch: () => state.epoch, getView: () => state.view,
    getDetailGeneration: () => state.detail,
    getSelectionGeneration: () => state.selection,
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

const TAXONOMY = {
  causes: [
    { code: "wrong_idea", label: "思路错误", group: "reasoning", group_label: "想错了", hint: "方向想错了" },
    { code: "misread", label: "审题/漏条件", group: "reasoning", group_label: "想错了", hint: "漏了一句" },
    { code: "boundary", label: "边界/特例", group: "implementation", group_label: "写错了", hint: "边界" },
    { code: "careless", label: "粗心/笔误", group: "implementation", group_label: "写错了", hint: "手滑" },
    { code: "other", label: "其他", group: "environment", group_label: "环境与其他", hint: "其他" },
  ],
  groups: [
    { code: "reasoning", label: "想错了" },
    { code: "implementation", label: "写错了" },
    { code: "environment", label: "环境与其他" },
  ],
  verdicts: [],
};
async function render(ctx, record = item(), taxonomy = TAXONOMY) {
  const form = ctx.env.window.PendingReason.render(record);
  ctx.env.document.body.append(form);
  assert.equal(ctx.env.calls[0].url, "/api/error-taxonomy");
  ctx.env.respond(ctx.env.calls[0], 200, taxonomy);
  await settle();
  return form;
}
const q = (form, selector) => form.querySelector(selector);
function submit(form, text = "循环边界少了等号。") {
  // 补原因要求至少选 1 个标签：先点第一个词表印章。
  if (!form.querySelectorAll('[aria-pressed="true"]').length) {
    const first = form.querySelector(".pending-reason-tag:not([disabled])");
    if (first) first.click();
  }
  q(form, "textarea").value = text;
  form.dispatchEvent(new FakeEvent("submit"));
}

test("pending reason: ordinary records keep the existing renderer", () => {
  const ctx = setup();
  assert.equal(ctx.env.window.PendingReason.render({ ...item(), pending_reason: false }), null);
  assert.equal(ctx.env.calls.length, 0);
});

test("pending reason: taxonomy chips select and deselect, selection stops at three", async () => {
  const ctx = setup();
  const form = await render(ctx);
  assert.equal(q(form, "img"), null);
  assert.equal(form.textContent.includes("（待补：为什么错）"), false);
  const tags = form.querySelectorAll(".pending-reason-tag");
  assert.equal(tags.length, 5); // 词表里的 5 个
  for (const index of [0, 1, 2]) form.querySelectorAll(".pending-reason-tag")[index].click();
  assert.equal(form.querySelectorAll(".pending-reason-tag")[3].disabled, true);
  form.querySelectorAll(".pending-reason-tag")[3].click();
  assert.equal(form.querySelectorAll('[aria-pressed="true"]').length, 3);
  assert.equal(q(form, ".pending-reason-count").textContent, "已选 3 / 3 个标签");
  form.querySelectorAll(".pending-reason-tag")[0].click();
  assert.equal(form.querySelectorAll(".pending-reason-tag")[3].disabled, false);
  // 中文名显示，不是 code。
  assert.ok(tags[0].textContent.includes("思路错误"));
});

test("pending reason: existing tags are selected, no custom input", async () => {
  const ctx = setup();
  const form = await render(ctx, { ...item(), tags: ["boundary"] });
  // 已有标签（code）被选中，显示中文名。
  assert.equal(form.querySelectorAll('[aria-pressed="true"]').length, 1);
  assert.ok(form.querySelector('[aria-pressed="true"]').textContent.includes("边界/特例"));
  // 没有自定义输入框：标签只能从词表里选。
  assert.equal(q(form, ".pending-reason-custom"), null);
  assert.equal(q(form, ".pending-reason-add"), null);
});

test("pending reason: empty reason sends nothing and keeps the form", async () => {
  const ctx = setup(); const form = await render(ctx);
  submit(form, " \n\t ");
  await settle();
  assert.equal(ctx.env.calls.length, 1);
  assert.match(q(form, ".pending-reason-status").textContent, /一句原因/);
});

test("pending reason: submitting without a tag prompts to pick at least one", async () => {
  const ctx = setup(); const form = await render(ctx);
  // 直接提交，不走 submit helper 的自动选标签。
  q(form, "textarea").value = "循环边界少了等号。";
  form.dispatchEvent(new FakeEvent("submit"));
  await settle();
  assert.equal(ctx.env.calls.length, 1);
  assert.match(q(form, ".pending-reason-status").textContent, /至少选择 1 个/);
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
  assert.deepEqual(JSON.parse(call.init.body), { description: "边界少了等号。", tags: ["wrong_idea"], version: 7 });
  ctx.env.respond(call, 200, { id: 11, version: 8, pending_reason: false, description: "边界少了等号。", tags: ["wrong_idea"] });
  await settle();
  assert.deepEqual(ctx.saved, [11]);
  assert.equal(record.version, 8);
  assert.equal(record.pending_reason, false);
  assert.equal(record.due_date, "2026-10-10");
  assert.equal(record.repetitions, 2);
});

test("pending reason: a failed save retains the draft and selection", async () => {
  const ctx = setup(); const form = await render(ctx);
  submit(form, "保留这句草稿");
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
  ctx.env.respond(ctx.env.calls[0], 200, TAXONOMY); await settle();
  assert.equal(form.textContent.includes("思路错误"), false);
});

test("pending reason: failed taxonomy blocks submit until a tag is picked", async () => {
  const ctx = setup(); const form = ctx.env.window.PendingReason.render(item());
  ctx.env.document.body.append(form);
  ctx.env.respond(ctx.env.calls[0], 503, { detail: "词表暂不可用" }); await settle();
  submit(form);
  await settle();
  assert.equal(ctx.env.calls.length, 1);
  assert.match(q(form, ".pending-reason-status").textContent, /至少选择 1 个/);
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
    ["rvfDetailGeneration", "detail"], ["rvfSelectionGeneration", "selection"]]) {
    Object.defineProperty(context, name, { configurable: true,
      get: () => ctx.state[key], set: (value) => { ctx.state[key] = value; } });
  }
  Object.assign(context, { api: ctx.hooks.api, rvfPageGeneration: 1, rvfDetailState: null,
    rvfListGeneration: 0, rvfListItems: new Map(),
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

async function successfulHomeTransition(userSelects) {
  const ctx = setup("home"); const waiting = deferred();
  const show = ctx.hooks.showView;
  ctx.hooks.showView = async (view) => {
    ctx.state.detail += 1; // showView clears the previous detail
    await show(view);
    await waiting.promise;
    ctx.state.detail += 1; // successful loadList clears detail again
  };
  ctx.hooks.openMistake = async (id) => {
    ctx.state.selection += 1;
    ctx.state.detail += 1;
    ctx.opened.push(id);
  };
  ctx.env.window.PendingReason.configure(ctx.hooks);
  const reminder = home(ctx); q(reminder, "button").click();
  ctx.env.respond(ctx.env.calls[0], 200, { items: [{ id: 11 }] }); await settle();
  assert.equal(ctx.state.view, "all");
  if (userSelects) await ctx.hooks.openMistake(22);
  waiting.resolve(); await settle();
  return ctx.opened;
}

test("pending reason: successful list transition preserves a user's new choice", async () => {
  assert.deepEqual(await successfulHomeTransition(true), [22]);
});

test("pending reason: successful list transition tolerates its own detail cleanup", async () => {
  assert.deepEqual(await successfulHomeTransition(false), [11]);
});

async function failingHomeTransition(stage, change) {
  const ctx = setup("home"); const waiting = deferred(); const errors = [];
  const show = ctx.hooks.showView;
  ctx.hooks.reportError = (text) => errors.push(text);
  ctx.hooks.showView = async (view) => {
    ctx.state.detail += 1;
    await show(view);
    if (stage === "list") await waiting.promise;
  };
  ctx.hooks.openMistake = async () => {
    ctx.state.detail += 1;
    await waiting.promise;
  };
  ctx.env.window.PendingReason.configure(ctx.hooks);
  const reminder = home(ctx); q(reminder, "button").click();
  ctx.env.respond(ctx.env.calls[0], 200, { items: [{ id: 11 }] }); await settle();
  assert.equal(ctx.state.view, "all");
  if (change === "epoch") ctx.state.epoch += 1;
  if (change === "user") ctx.state.user = { id: 8 };
  if (change === "detail") ctx.state.detail += 1;
  if (change === "leave-return") { await show("new"); await show("all"); }
  waiting.reject(new Error("目标请求不可用")); await settle();
  return errors;
}

for (const stage of ["list", "detail"]) {
  test(`pending reason: ${stage} transition errors remain visible on the target page`, async () => {
    const errors = await failingHomeTransition(stage);
    assert.equal(errors.length, 1);
    assert.match(errors[0], /目标请求不可用/);
  });
  for (const change of ["epoch", "user", "detail", "leave-return"]) {
    test(`pending reason: ${stage} transition ignores stale errors after ${change}`, async () => {
      assert.deepEqual(await failingHomeTransition(stage, change), []);
    });
  }
}

for (const stale of [false, true]) {
  test(`pending reason: completed elsewhere reopen failure is ${stale ? "ignored after selection" : "reported"}`, async () => {
    const ctx = setup(); const form = await render(ctx); const errors = [];
    submit(form); ctx.env.respond(ctx.env.calls[1], 409, { detail: "冲突" }); await settle();
    q(form, ".pending-reason-refresh").click();
    ctx.env.respond(ctx.env.calls[2], 200, { ...item(), pending_reason: false, version: 9 }); await settle();
    const waiting = deferred();
    ctx.hooks.reportError = (text) => errors.push(text);
    ctx.hooks.openMistake = async () => { ctx.state.detail += 1; await waiting.promise; };
    q(form, ".pending-reason-reopen").click();
    if (stale) ctx.state.detail += 1;
    waiting.reject(new Error("详情不可用")); await settle();
    assert.equal(errors.length, stale ? 0 : 1);
    assert.equal(q(form, ".pending-reason-save").disabled, true);
  });
}
