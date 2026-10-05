"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, FakeEvent } = require("./js_harness.cjs");
const source = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
function between(start, end) {
  const from = source.indexOf(start), to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `real app section exists: ${start}`);
  return source.slice(from, to);
}
const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), []));
async function settle() { await tick(); await tick(); }
function environment() {
  const env = load([]), { document, context } = env;
  for (const id of ["reset-form", "email-prompt", "login-form", "register-form"]) {
    const form = document.createElement("form"); form.id = id;
    for (const name of ["password", "email", "username", "accept_terms"]) {
      const input = document.createElement("input"); input.name = name;
      input.setAttribute("name", name); input.value = name === "password" ? "password88" : "alice@example.com";
      input.checked = true; form[name] = input; form.append(input);
    }
    form.reset = () => { for (const name of ["password", "email", "username"]) form[name].value = ""; };
    document.body.append(form);
  }
  for (const id of ["logout", "trial-start", "auth-trial-start", "forgot-link", "back-to-login-link", "login-username-hint"]) {
    const item = document.createElement("button"); item.id = id; document.body.append(item);
  }
  env.window.probe = { messages: [], panels: [], history: [], logout: 0, enter: 0 };
  env.window.Account = { isPending: () => false };
  context.location = { pathname: "/" };
  context.history = { replaceState: (...args) => env.window.probe.history.push(args) };
  vm.runInContext(`
    const $ = (selector) => document.querySelector(selector);
    let user = { id: 7, email: null }, sessionEpoch = 0, resetToken = "original-token", busy = false, view = "auth";
    function message(text = "", error = false) { window.probe.messages.push([String(text), Boolean(error)]); }
    function setBusy() {}
    async function loadList() {}
    function showAuthPanels(ids) { window.probe.panels.push(ids.join(",")); }
    function formObject(form) { return {username: form.username.value, password: form.password.value}; }
    function signedOut() { user = null; sessionEpoch += 1; window.probe.logout += 1; }
    async function enterApp() { window.probe.enter += 1; }
  `, context);
  vm.runInContext(between("async function api(path, options = {}) {", "\nasync function uploadAvatarFile"), context);
  vm.runInContext(between("async function run(action) {", "\nfunction formObject"), context);
  vm.runInContext(between('$("#login-form").addEventListener', "// 找回密码：提交成功后"), context);
  vm.runInContext(between('$("#reset-form").addEventListener', "// 侧栏、底栏"), context);
  env.get = (id) => document.querySelector(`#${id}`);
  env.submit = (id) => env.get(id).dispatchEvent(new FakeEvent("submit"));
  env.state = (expression) => vm.runInContext(expression, context);
  return env;
}

for (const code of ["weak_password", "invalid_token", undefined]) {
  test(`reset: 400 ${code || "unknown"} clears token only for invalid_token`, async () => {
    const env = environment(); env.submit("reset-form");
    env.respond(env.calls[0], 400, { detail: "密码提示", ...(code ? {code} : {}) }); await settle();
    assert.equal(env.state("resetToken"), code === "invalid_token" ? null : "original-token");
    assert.deepEqual(env.window.probe.panels, code === "invalid_token" ? ["forgot-form"] : []);
    if (code !== "invalid_token") assert.deepEqual(JSON.parse(JSON.stringify(env.window.probe.messages)), [["密码提示", true]]);
  });
}
test("reset: success clears token and password then offers login", async () => {
  const env = environment(); env.submit("reset-form");
  env.respond(env.calls[0], 200, {ok: true}); await settle();
  assert.equal(env.state("resetToken"), null); assert.equal(env.get("reset-form").password.value, "");
  assert.deepEqual(env.window.probe.panels, ["login-form"]);
});
for (const status of [200, 400]) {
  test(`reset: late ${status} after changed epoch keeps the new reset link`, async () => {
    const env = environment(); env.submit("reset-form");
    env.state('sessionEpoch += 1; resetToken = "new-token";');
    env.respond(env.calls[0], status, {detail: "过期", code: "invalid_token"}); await settle();
    assert.equal(env.state("resetToken"), "new-token");
    assert.deepEqual(env.window.probe.panels, []); assert.deepEqual(env.window.probe.messages, []);
  });
}
test("email: success updates requesting account and clears credentials", async () => {
  const env = environment(); env.submit("email-prompt");
  env.respond(env.calls[0], 200, {email: "new@example.com"}); await settle();
  assert.equal(env.state("user.email"), "new@example.com"); assert.equal(env.get("email-prompt").password.value, "");
  assert.equal(env.get("email-prompt").hidden, true);
});
for (const change of ['sessionEpoch += 1;', 'user = {id: 8, email: "other@example.com"};', 'user = null; sessionEpoch += 1;']) {
  test(`email: late success is discarded after ${change}`, async () => {
    const env = environment(); env.submit("email-prompt"); env.state(change);
    env.get("email-prompt").password.value = "new-account-secret";
    env.respond(env.calls[0], 200, {email: "old@example.com"}); await settle();
    assert.notEqual(env.state("user?.email"), "old@example.com");
    assert.equal(env.get("email-prompt").password.value, "new-account-secret");
    assert.deepEqual(env.window.probe.messages, []);
  });
}
test("email: failed binding leaves account email unchanged", async () => {
  const env = environment(); env.submit("email-prompt");
  env.respond(env.calls[0], 400, {detail: "当前密码不正确"}); await settle();
  assert.equal(env.state("user.email"), null); assert.equal(env.get("email-prompt").hidden, false);
});
test("email: failure arriving after switching accounts does not show the old error", async () => {
  const env = environment(); env.submit("email-prompt");
  env.state('user = {id: 8, email: "other@example.com"}; sessionEpoch += 1;');
  env.respond(env.calls[0], 400, {detail: "旧账号密码错误"}); await settle();
  assert.deepEqual(env.window.probe.messages, []);
});
test("logout: late success cannot sign out a new login", async () => {
  const env = environment(); env.get("logout").click();
  env.state('user = {id: 8, email: "other@example.com"}; sessionEpoch += 1;');
  env.respond(env.calls[0], 200, {ok: true}); await settle();
  assert.equal(env.window.probe.logout, 0); assert.deepEqual(env.window.probe.messages, []);
});
for (const entry of ["logout", "login-form", "register-form", "trial-start", "auth-trial-start"]) {
  test(`authentication lock: ${entry} starts no request until pending action settles`, async () => {
    const env = environment(); env.window.Account.isPending = () => true;
    if (entry.endsWith("-form")) env.submit(entry); else env.get(entry).click(); await settle();
    assert.equal(env.calls.length, 0); assert.equal(env.window.probe.logout, 0); assert.equal(env.window.probe.enter, 0);
    env.window.Account.isPending = () => false;
    if (entry.endsWith("-form")) env.submit(entry); else env.get(entry).click();
    assert.equal(env.calls.length, 1);
    env.respond(env.calls[0], 200, {ok: true}); await settle();
    assert.equal(entry === "logout" ? env.window.probe.logout : env.window.probe.enter, 1);
  });
}
test("session reset: signedOut clears email and password before other reset work", () => {
  const env = environment();
  vm.runInContext(between("function secClearEmailForm() {", "function signedOut() {"), env.context);
  vm.runInContext(between("function signedOut() {", "  rvfPageGeneration += 1;") + "}", env.context);
  env.state("signedOut()");
  assert.equal(env.get("email-prompt").password.value, ""); assert.equal(env.get("email-prompt").email.value, "");
});
test("session reset: enterApp clears email credentials before fetching the new account", async () => {
  const env = environment();
  vm.runInContext(between("function secClearEmailForm() {", "function signedOut() {"), env.context);
  vm.runInContext(between("async function enterApp() {", "  resetWeaknessAnalysis();") + "}", env.context);
  await env.state("enterApp()");
  assert.equal(env.get("email-prompt").password.value, ""); assert.equal(env.get("email-prompt").email.value, "");
});
