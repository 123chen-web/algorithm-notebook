"use strict";

/* 找回密码表单：提示文案、提交后 60 秒按钮倒计时、迟到响应守卫。
   复用 app.js 里真实的 message / setBusy / run / api 和忘记密码那一段代码，
   只用假浏览器（tests/js_harness.cjs）和可手动拨动的假 setInterval 驱动。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const appSource = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
function between(start, end) {
  const from = appSource.indexOf(start);
  const to = appSource.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `extract the real app.js section: ${start}`);
  return appSource.slice(from, to);
}
const busySource = between("function message(text = \"\", error = false) {", "const AUTH_PANELS");
const runSource = between("async function run(action) {", "\nfunction formObject");
const apiSource = between("async function api(path, options = {}) {", "\nasync function uploadAvatarFile");
const forgotSource = between("// 找回密码：提交成功后", "$(\"#reset-form\").addEventListener");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

const NOTICE = "如果该邮箱已注册，邮件会在几分钟内送达；没收到请查看垃圾邮件箱。";

function environment() {
  const env = load([]);
  const { document, context } = env;
  const form = document.createElement("form");
  form.id = "forgot-form";
  const email = document.createElement("input");
  email.value = "alice@example.com";
  form.email = email;
  form.reset = () => { email.value = ""; form.resets = (form.resets || 0) + 1; };
  const button = document.createElement("button");
  button.setAttribute("type", "submit");
  button.textContent = "发送重置邮件";
  form.append(email, button);
  const other = document.createElement("button");
  other.id = "other-button";
  document.body.append(form, other);

  const timers = new Map();
  let nextTimer = 1;
  context.setInterval = (callback) => { timers.set(nextTimer, callback); return nextTimer++; };
  context.clearInterval = (id) => { timers.delete(id); };
  context.location = { pathname: "/" };
  context.history = { replaceState() {} };
  env.window.probe = { panels: [] };
  vm.runInContext(`
    const $ = (selector) => document.querySelector(selector);
    let user = null;
    let view = "auth";
    let busy = false;
    let sessionEpoch = 0;
    function signedOut() { sessionEpoch += 1; }
    async function loadList() {}
    function showAuthPanels(ids) { window.probe.panels.push(ids.join(",")); }
  `, context);
  vm.runInContext(busySource, context, { filename: "app.js:message" });
  vm.runInContext(runSource, context, { filename: "app.js:run" });
  vm.runInContext(apiSource, context, { filename: "app.js:api" });
  vm.runInContext(forgotSource, context, { filename: "app.js:forgot" });
  return {
    ...env, form, button, other, panels: env.window.probe.panels,
    notice: document.querySelector("#notice"),
    logout: () => vm.runInContext("sessionEpoch += 1", context),
    setBusy: (value) => vm.runInContext(`setBusy(${value})`, context),
    // 走完 n 秒：每秒触发一次当前所有的间隔计时器。
    seconds(n) { for (let i = 0; i < n; i += 1) for (const callback of [...timers.values()]) callback(); },
    timerCount: () => timers.size,
  };
}
const submit = (env) => env.form.dispatchEvent(new FakeEvent("submit", { bubbles: true }));
async function settle() { await tick(); await tick(); }

async function submitAndAnswer(env, status = 200, body = { ok: true }) {
  submit(env);
  await settle();
  env.respond(env.calls[env.calls.length - 1], status, body);
  await settle();
}

test("forgot: success shows the new notice and a 60 second disabled countdown", async () => {
  const env = environment();
  await submitAndAnswer(env);
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, "/api/auth/forgot-password");
  assert.deepEqual(JSON.parse(env.calls[0].init.body), { email: "alice@example.com" });
  assert.equal(env.notice.textContent, NOTICE);
  assert.equal(env.notice.classList.contains("error"), false);
  assert.equal(env.form.resets, 1);
  assert.deepEqual(env.panels, ["login-form"]);

  assert.equal(env.button.disabled, true);
  assert.equal(env.button.getAttribute("data-blocked"), "1");
  assert.match(env.button.textContent, /60/);
  env.seconds(30);
  assert.equal(env.button.disabled, true);
  assert.match(env.button.textContent, /30/);
  env.seconds(29);
  assert.equal(env.button.disabled, true, "still locked on second 59");
  assert.match(env.button.textContent, /1 秒/);
  env.seconds(1);
  assert.equal(env.button.disabled, false, "unlocked after exactly 60 seconds");
  assert.equal(env.button.getAttribute("data-blocked"), "0");
  assert.equal(env.button.textContent, "发送重置邮件");
  assert.equal(env.timerCount(), 0, "the interval is cleared");
});

test("forgot: submitting again during the countdown sends nothing; after it, it works", async () => {
  const env = environment();
  await submitAndAnswer(env);
  env.form.email.value = "alice@example.com";
  submit(env);
  await settle();
  assert.equal(env.calls.length, 1, "no second request while locked");

  env.seconds(60);
  submit(env);
  await settle();
  assert.equal(env.calls.length, 2, "a new request is allowed after the countdown");
});

test("forgot: the global busy switch cannot unlock the countdown early", async () => {
  const env = environment();
  await submitAndAnswer(env);
  env.setBusy(true);
  env.setBusy(false); // 别的操作结束后全局恢复按钮
  assert.equal(env.button.disabled, true);
  assert.equal(env.other.disabled, false);
});

test("forgot: a failed request shows the error and does not start a countdown", async () => {
  const env = environment();
  await submitAndAnswer(env, 429, { detail: "尝试次数过多，请稍后再试" });
  assert.equal(env.notice.textContent, "尝试次数过多，请稍后再试");
  assert.equal(env.notice.classList.contains("error"), true);
  assert.equal(env.button.disabled, false);
  assert.equal(env.button.getAttribute("data-blocked"), null);
  assert.equal(env.timerCount(), 0);
  assert.deepEqual(env.panels, []);
  assert.equal(env.form.resets, undefined, "the typed address is kept for a retry");
});

test("forgot: a response that arrives after the session changed is ignored", async () => {
  const env = environment();
  submit(env);
  await settle();
  env.logout(); // 请求在途时会话已变
  env.respond(env.calls[0], 200, { ok: true });
  await settle();
  assert.equal(env.notice.textContent, "", "no notice for a stale answer");
  assert.deepEqual(env.panels, []);
  assert.equal(env.form.resets, undefined);
  assert.equal(env.button.disabled, false);
  assert.equal(env.timerCount(), 0, "no countdown for a stale answer");
});

test("forgot: a countdown from an older session stops at its next tick", async () => {
  const env = environment();
  await submitAndAnswer(env);
  assert.equal(env.button.disabled, true);
  env.logout();
  env.seconds(1);
  assert.equal(env.button.disabled, false);
  assert.equal(env.button.textContent, "发送重置邮件");
  assert.equal(env.timerCount(), 0);
});
