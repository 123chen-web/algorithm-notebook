"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const CODE = "ABCD-EFGH-JKMN-PQRS";
const PLAN = { id: 7, name: "内测套餐", price_cents: 1200, period_days: 30 };
const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), []));

function setup(view = "plan", user = { id: 1, is_admin: true, is_trial: false }) {
  const clipboard = [];
  const env = load(["redeem.js"], { extra: { navigator: { clipboard: { writeText: async (value) => clipboard.push(value) } }, confirm: () => true } });
  const state = { epoch: 1, user, view, refreshes: 0 };
  const calls = [];
  const $ = (id) => env.document.querySelector(`#${id}`);
  $("admin-redeem-card").open = true;
  for (const id of ["manual-settings-save", "redeem-generate", "manual-grant-submit"]) {
    $(id).tagName = "BUTTON";
    $("admin-redeem-card").append($(id));
  }
  $("redeem-form").dataset.state = "idle";
  $("redeem-code").value = CODE;
  $("redeem-count").value = "1";
  $("redeem-plan").value = "7";
  $("grant-plan").value = "7";
  $("redeem-generated-codes").select = () => { state.selected = true; };
  env.window.Redeem.configure({
    getEpoch: () => state.epoch, getUser: () => state.user, getView: () => state.view,
    refreshPlanSubscription: async () => { state.refreshes += 1; state.user = { ...state.user, plan_name: PLAN.name }; return true; },
    api: (url, init) => { const call = { url, init, ...deferred() }; calls.push(call); return call.promise; },
  });
  const submit = (id) => $(id).dispatchEvent(new FakeEvent("submit"));
  const route = (view) => {
    state.view = view;
    env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view } }));
  };
  return { ...env, state, calls, $, submit, route, clipboard };
}

test("redeem: idle → submitting → success refreshes subscription once", async () => {
  const env = setup();
  assert.equal(env.$("redeem-form").dataset.state, "idle");
  env.submit("redeem-form");
  assert.equal(env.$("redeem-form").dataset.state, "submitting");
  assert.equal(env.$("redeem-submit").disabled, true);
  assert.equal(env.calls.length, 1);
  assert.deepEqual(JSON.parse(env.calls[0].init.body), { code: "ABCDEFGHJKMNPQRS" });
  env.calls[0].resolve({ plan_expires_at: "2026-11-03T12:00:00+00:00" });
  await tick();
  assert.equal(env.$("redeem-form").dataset.state, "success");
  assert.equal(env.$("redeem-status").textContent, "套餐已开通，有效期至 2026-11-03");
  assert.equal(env.$("redeem-submit").disabled, false);
  assert.equal(env.$("redeem-code").value, "");
  assert.equal(env.state.refreshes, 1);
});

test("redeem: failure renders server detail and permits retry", async () => {
  const env = setup();
  env.submit("redeem-form");
  env.calls[0].reject(new Error("兑换码不正确、已使用或已过期"));
  await tick();
  assert.equal(env.$("redeem-form").dataset.state, "failure");
  assert.equal(env.$("redeem-status").textContent, "兑换码不正确、已使用或已过期");
  assert.equal(env.$("redeem-submit").disabled, false);
  env.submit("redeem-form");
  assert.equal(env.calls.length, 2);
  env.calls[1].reject(new Error("尝试次数过多，请稍后再试"));
  await tick();
});

test("redeem: repeat clicks issue only one request", async () => {
  const env = setup();
  env.submit("redeem-form");
  env.submit("redeem-form");
  env.submit("redeem-form");
  assert.equal(env.calls.length, 1);
  env.calls[0].resolve({ plan_expires_at: "2026-11-03" });
  await tick();
});

test("redeem: late success after logout and another login is discarded", async () => {
  const env = setup();
  env.submit("redeem-form");
  env.state.epoch += 1;
  env.state.user = null;
  env.window.Redeem.reset();
  env.state.epoch += 1;
  env.state.user = { id: 2, is_admin: true };
  env.calls[0].resolve({ plan_expires_at: "2099-01-01" });
  await tick();
  assert.equal(env.state.refreshes, 0);
  assert.equal(env.$("redeem-status").textContent, "");
  assert.equal(env.$("redeem-form").dataset.state, "idle");
  assert.equal(env.$("redeem-submit").disabled, false);
});

test("redeem: epoch blocks even the same user after login again", async () => {
  const env = setup();
  env.submit("redeem-form");
  env.state.epoch += 2;
  env.$("redeem-status").textContent = "新会话";
  env.calls[0].reject(new Error("旧会话错误"));
  await tick();
  assert.equal(env.$("redeem-status").textContent, "新会话");
});

test("redeem: leaving and returning drops the old response", async () => {
  const env = setup();
  env.submit("redeem-form");
  env.route("home");
  env.route("plan");
  env.calls[0].resolve({ plan_expires_at: "2099-01-01" });
  await tick();
  assert.equal(env.state.refreshes, 0);
  assert.equal(env.$("redeem-status").textContent, "");
});

test("redeem: old response cannot unlock a new request after return", async () => {
  const env = setup();
  env.submit("redeem-form");
  env.route("home");
  env.route("plan");
  env.submit("redeem-form");
  assert.equal(env.calls.length, 2);
  env.calls[0].resolve({ plan_expires_at: "2099-01-01" });
  await tick();
  assert.equal(env.$("redeem-submit").disabled, true);
  assert.equal(env.$("redeem-form").dataset.state, "submitting");
  assert.equal(env.state.refreshes, 0);
  env.submit("redeem-form");
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve({ plan_expires_at: "2026-11-03" });
  await tick();
  assert.equal(env.$("redeem-submit").disabled, false);
  assert.equal(env.$("redeem-status").textContent, "套餐已开通，有效期至 2026-11-03");
});

test("redeem: subscription refresh also receives a guard for its own late response", async () => {
  const env = setup();
  let refreshCurrent;
  const refresh = deferred();
  env.window.Redeem.configure({
    getEpoch: () => env.state.epoch, getUser: () => env.state.user, getView: () => env.state.view,
    refreshPlanSubscription: (guard) => { refreshCurrent = guard; return refresh.promise; },
    api: (_url, _init) => { const call = deferred(); env.calls.push(call); return call.promise; },
  });
  env.submit("redeem-form");
  env.calls[0].resolve({ plan_expires_at: "2026-11-03" });
  await tick();
  assert.equal(refreshCurrent(), true);
  env.route("home");
  env.route("plan");
  assert.equal(refreshCurrent(), false);
  env.$("redeem-code").value = CODE;
  env.submit("redeem-form");
  refresh.resolve(false);
  await tick();
  assert.equal(env.$("redeem-submit").disabled, true);
  env.calls[1].reject(new Error("兑换码不正确、已使用或已过期"));
  await tick();
});

test("redeem: full width, lower case, spaces and hyphens normalize before sending", async () => {
  const env = setup();
  env.$("redeem-code").value = "ａｂｃｄ－ｅｆｇｈ　ｊｋｍｎ - pqrs";
  env.$("redeem-code").dispatchEvent(new FakeEvent("input"));
  assert.equal(env.$("redeem-code").value, "ABCD-EFGH JKMN - PQRS");
  env.submit("redeem-form");
  assert.equal(JSON.parse(env.calls[0].init.body).code, "ABCDEFGHJKMNPQRS");
  env.calls[0].resolve({ plan_expires_at: "2026-11-03" });
  await tick();
});

test("redeem: empty input and trial accounts never submit", () => {
  const env = setup();
  env.$("redeem-code").value = " -　";
  env.submit("redeem-form");
  assert.equal(env.calls.length, 0);
  assert.equal(env.$("redeem-status").textContent, "请输入兑换码");
  env.$("redeem-code").value = CODE;
  env.state.user.is_trial = true;
  env.submit("redeem-form");
  assert.equal(env.calls.length, 0);
});

test("manual payment: needs enabled and a QR, contact is plain text and no order is created", async () => {
  const env = setup();
  let loading = env.window.Redeem.loadPlan([PLAN]);
  env.calls[0].resolve({ enabled: true, contact: "<img src=x onerror=alert(1)>", qr: { alipay: true, wechat: false } });
  await loading;
  assert.equal(env.$("manual-payment-panel").hidden, false);
  assert.equal(env.$("manual-payment-contact").textContent, "<img src=x onerror=alert(1)>");
  assert.equal(env.$("manual-payment-contact").children.length, 0);
  assert.equal(env.$("manual-payment-qrs").children.length, 1);
  assert.equal(env.$("manual-payment-prices").textContent.includes("¥12.00"), true);
  assert.equal(env.calls.some((call) => call.url === "/api/orders"), false);
  loading = env.window.Redeem.loadPlan([PLAN]);
  env.calls[1].resolve({ enabled: true, contact: "", qr: { alipay: false, wechat: false } });
  await loading;
  assert.equal(env.$("manual-payment-panel").hidden, true);
  loading = env.window.Redeem.loadPlan([PLAN]);
  env.calls[2].resolve({ enabled: false, contact: "", qr: { alipay: true, wechat: true } });
  await loading;
  assert.equal(env.$("manual-payment-panel").hidden, true);
});

test("manual payment: trial hides redemption and gives registration explanation", async () => {
  const env = setup("plan", { id: 1, is_trial: true });
  const loading = env.window.Redeem.loadPlan([PLAN]);
  env.calls[0].resolve({ enabled: false, contact: "", qr: {} });
  await loading;
  assert.equal(env.$("redeem-panel").hidden, true);
  assert.equal(env.$("redeem-trial-note").hidden, false);
});

async function generated(env) {
  env.submit("redeem-generate-form");
  const call = env.calls[env.calls.length - 1];
  call.resolve({ codes: [CODE, "2345-6789-ABCD-EFGH"] });
  await tick();
  env.calls[env.calls.length - 1].resolve({ codes: [] });
  await tick();
}

test("admin: one-time plaintext clears on card close, route leave and logout", async () => {
  const env = setup("admin");
  await generated(env);
  assert.equal(env.$("redeem-generated-codes").value, `${CODE}\n2345-6789-ABCD-EFGH`);
  env.$("admin-redeem-card").open = false;
  env.$("admin-redeem-card").dispatchEvent(new FakeEvent("toggle"));
  assert.equal(env.$("redeem-generated-codes").value, "");
  env.$("admin-redeem-card").open = true;
  await generated(env);
  env.route("home");
  assert.equal(env.$("redeem-generated-codes").value, "");
  env.route("admin");
  await generated(env);
  env.window.Redeem.reset();
  assert.equal(env.$("redeem-generated-codes").value, "");
  assert.equal(env.$("redeem-generated").hidden, true);
});

test("admin: generating again clears previous plaintext before response", async () => {
  const env = setup("admin");
  await generated(env);
  env.submit("redeem-generate-form");
  assert.equal(env.$("redeem-generated-codes").value, "");
  assert.equal(env.$("redeem-generated").hidden, true);
  env.calls[env.calls.length - 1].reject(new Error("生成失败"));
  await tick();
});

test("admin: leaving card before response prevents plaintext from reappearing", async () => {
  const env = setup("admin");
  env.submit("redeem-generate-form");
  env.$("admin-redeem-card").open = false;
  env.$("admin-redeem-card").dispatchEvent(new FakeEvent("toggle"));
  env.calls[0].resolve({ codes: [CODE] });
  await tick();
  assert.equal(env.$("redeem-generated-codes").value, "");
  env.calls[1].resolve({ codes: [] });
  await tick();
});

test("admin: clipboard success and failure offer manual copy", async () => {
  const env = setup("admin");
  await generated(env);
  env.$("redeem-copy").click();
  await tick();
  assert.deepEqual(env.clipboard, [`${CODE}\n2345-6789-ABCD-EFGH`]);
  assert.equal(env.$("redeem-copy-status").textContent, "已复制全部兑换码");
  env.window.navigator.clipboard.writeText = async () => { throw new Error("denied"); };
  env.$("redeem-copy").click();
  await tick();
  assert.equal(env.state.selected, true);
  assert.match(env.$("redeem-copy-status").textContent, /Ctrl\+C 或长按复制/);
});

test("admin: a clipboard failure after logout cannot select another account's text", async () => {
  const env = setup("admin");
  await generated(env);
  const copy = deferred();
  env.window.navigator.clipboard.writeText = () => copy.promise;
  env.$("redeem-copy").click();
  env.state.epoch += 2;
  env.window.Redeem.reset();
  copy.reject(new Error("denied"));
  await tick();
  assert.equal(env.state.selected, undefined);
  assert.equal(env.$("redeem-copy-status").textContent, "");
});

test("admin: closing generated result explicitly clears all plaintext", async () => {
  const env = setup("admin");
  await generated(env);
  env.$("redeem-generated-close").click();
  assert.equal(env.$("redeem-generated-codes").value, "");
});

test("admin: leaving and returning clears action lock and drops its old result", async () => {
  const env = setup("admin");
  env.submit("manual-settings-form");
  assert.equal(env.$("manual-settings-save").disabled, true);
  env.route("home");
  assert.equal(env.$("manual-settings-save").disabled, false);
  env.route("admin");
  env.submit("manual-settings-form");
  assert.equal(env.calls.length, 2);
  env.calls[0].resolve({ enabled: true, contact: "旧会话" });
  await tick();
  assert.equal(env.$("manual-settings-save").disabled, true);
  assert.equal(env.$("manual-settings-status").textContent, "正在处理…");
  env.submit("manual-settings-form");
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve({ enabled: true, contact: "新会话" });
  await tick();
  assert.equal(env.$("manual-settings-save").disabled, false);
  assert.equal(env.$("manual-settings-status").textContent, "设置已保存");
});

test("admin: completed response while away does not prevent another submit on return", async () => {
  const env = setup("admin");
  env.submit("manual-settings-form");
  env.route("home");
  env.calls[0].resolve({ enabled: true, contact: "" });
  await tick();
  env.route("admin");
  env.submit("manual-settings-form");
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve({ enabled: false, contact: "" });
  await tick();
  assert.equal(env.$("manual-settings-save").disabled, false);
});
