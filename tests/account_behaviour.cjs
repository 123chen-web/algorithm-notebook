"use strict";

/* 账号对话框复用真实 api()，只替换 fetch 和页面会话钩子。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const appSource = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
const apiStart = appSource.indexOf("async function api(path, options = {}) {");
const apiEnd = appSource.indexOf("\nasync function uploadAvatarFile", apiStart);
assert.ok(apiStart >= 0 && apiEnd > apiStart, "extract the real app.js api() contract");
const apiSource = appSource.slice(apiStart, apiEnd);
const accountSource = fs.readFileSync(path.join(__dirname, "..", "static", "account.js"), "utf8");
const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

function environment() {
  const env = load([]);
  const { document, context } = env;
  const menu = document.createElement("details");
  menu.className = "account-menu";
  const summary = document.createElement("summary");
  summary.setAttribute("aria-label", "账号菜单");
  const panel = document.createElement("div");
  panel.className = "account-menu-panel";
  for (const id of ["account-password", "account-revoke-others", "account-delete"]) {
    const button = document.createElement("button");
    button.id = id;
    button.type = "button";
    panel.append(button);
  }
  menu.append(summary, panel);
  const wrapper = document.createElement("div");
  wrapper.id = "my-avatar-wrap";
  wrapper.append(menu);
  const dialog = document.createElement("dialog");
  dialog.id = "account-dialog";
  dialog.setAttribute("aria-labelledby", "account-dialog-title");
  const content = document.createElement("div");
  content.id = "account-dialog-content";
  dialog.append(content);
  const logout = document.createElement("button");
  logout.id = "logout";
  document.body.append(wrapper, dialog, logout);
  env.window.probe = { messages: [], signedOut: 0, menuCloses: 0, history: [] };
  context.location = { pathname: "/" };
  context.history = {
    replaceState: (...args) => env.window.probe.history.push(args),
  };
  vm.runInContext(`
    let user = { id: 7, is_trial: false };
    let sessionEpoch = 0;
    function message(text = "", error = false) { window.probe.messages.push([String(text), Boolean(error)]); }
    function signedOut() {
      window.probe.signedOut += 1;
      user = null;
      sessionEpoch += 1;
      window.Account?.reset();
    }
    function closeAccountMenu() {
      window.probe.menuCloses += 1;
      document.querySelector(".account-menu").open = false;
    }
    function run() { throw new Error("account actions must own their pending state"); }
  `, context);
  vm.runInContext(apiSource, context, { filename: "app.js:api" });
  vm.runInContext(accountSource, context, { filename: "account.js" });
  return { ...env, menu, summary, dialog, content, probe: env.window.probe };
}

function get(env, id) {
  const element = env.document.documentElement.querySelector(`#${id}`);
  assert.ok(element, `${id} exists in the dialog`);
  return element;
}
function input(env, id, value) {
  const element = get(env, id);
  element.value = value;
  element.dispatchEvent(new FakeEvent("input", { bubbles: true }));
  return element;
}
function confirmDelete(env, checked) {
  const checkbox = get(env, "account-delete-confirm");
  checkbox.checked = checked;
  checkbox.dispatchEvent(new FakeEvent("change", { bubbles: true }));
  return checkbox;
}
function passwords(env, current = "old-pass88", next = "new-pass99", confirmed = next) {
  input(env, "account-current-password", current);
  input(env, "account-new-password", next);
  input(env, "account-confirm-password", confirmed);
}
function submit(env) {
  get(env, "account-dialog-form").dispatchEvent(new FakeEvent("submit", { bubbles: true }));
}
async function settle() { await tick(); await tick(); }
function request(env, endpoint, body) {
  assert.equal(env.calls.length, 1, "only one request");
  const call = env.calls[0];
  assert.equal(call.url, endpoint);
  assert.equal(call.init.method, "POST");
  assert.equal(call.init.credentials, "same-origin");
  assert.equal(call.init.headers["X-CSRF-Protection"], "1");
  assert.equal(call.init.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(call.init.body), body);
  return call;
}
function pageMessage(env, text) {
  assert.deepEqual(JSON.parse(JSON.stringify(env.probe.messages)), [[text, false]]);
}

for (const [label, current, next, confirmed] of [
  ["missing current password", "", "new-pass99", "new-pass99"],
  ["missing new password", "old-pass88", "", ""],
  ["missing confirmation", "old-pass88", "new-pass99", ""],
  ["new password shorter than eight", "old-pass88", "seven77", "seven77"],
  ["different confirmation", "old-pass88", "new-pass99", "another99"],
  ["new password equals current", "old-pass88", "old-pass88", "old-pass88"],
]) {
  test(`password: ${label} stays local and shows a dialog error`, async () => {
    const env = environment();
    env.window.Account.openPassword();
    passwords(env, current, next, confirmed);
    submit(env);
    await settle();
    assert.equal(env.calls.length, 0);
    assert.equal(env.dialog.open, true);
    assert.ok(get(env, "account-dialog-error").textContent.trim());
    assert.equal(get(env, "account-dialog-error").getAttribute("role"), "alert");
    assert.deepEqual(env.probe.messages, []);
    assert.equal(env.probe.signedOut, 0);
  });
}

for (const revoked of [0, 4]) {
  test(`password: successful update with ${revoked} revoked sessions`, async () => {
    const env = environment();
    env.window.Account.openPassword();
    passwords(env);
    submit(env);
    const call = request(env, "/api/me/password", { current_password: "old-pass88", new_password: "new-pass99" });
    const button = get(env, "account-dialog-submit");
    assert.equal(button.disabled, true);
    assert.equal(button.textContent, "处理中…");
    assert.equal(get(env, "account-dialog-form").getAttribute("aria-busy"), "true");
    env.respond(call, 200, { ok: true, revoked_sessions: revoked });
    await settle();
    assert.equal(env.dialog.open, false);
    assert.equal(env.probe.signedOut, 0);
    pageMessage(env, revoked ? "密码已更新，其他设备已退出登录。" : "密码已更新。");
  });
}

test("password: backend 400 detail remains in the dialog and does not sign out", async () => {
  const env = environment();
  env.window.Account.openPassword();
  passwords(env);
  submit(env);
  env.respond(env.calls[0], 400, { detail: "当前密码不正确，请重试。" });
  await settle();
  assert.equal(env.dialog.open, true);
  assert.equal(get(env, "account-dialog-error").textContent, "当前密码不正确，请重试。");
  assert.equal(get(env, "account-dialog-submit").disabled, false);
  assert.equal(get(env, "account-dialog-form").getAttribute("aria-busy"), "false");
  assert.equal(env.probe.signedOut, 0);
  assert.deepEqual(env.probe.messages, []);
});

test("password: repeated submission while fetch is pending makes one request", async () => {
  const env = environment();
  env.window.Account.openPassword();
  passwords(env);
  submit(env);
  submit(env);
  get(env, "account-dialog-submit").click();
  await settle();
  assert.equal(env.calls.length, 1);
  env.respond(env.calls[0], 200, { ok: true, revoked_sessions: 0 });
  await settle();
  pageMessage(env, "密码已更新。");
});

for (const revoked of [0, 3]) {
  test(`revoke: confirmation posts an empty object and reports ${revoked} sessions`, async () => {
    const env = environment();
    get(env, "account-revoke-others").click();
    assert.equal(env.dialog.open, true);
    assert.ok(env.content.textContent.includes("将退出你在其他浏览器和设备上的登录，当前设备不受影响。"));
    assert.equal(env.document.activeElement, get(env, "account-dialog-cancel"));
    assert.equal(env.calls.length, 0, "opening is only a confirmation");
    submit(env);
    const call = request(env, "/api/me/sessions/revoke-others", {});
    env.respond(call, 200, { ok: true, revoked });
    await settle();
    assert.equal(env.dialog.open, false);
    pageMessage(env, revoked ? `已退出其他设备（共 ${revoked} 处）。` : "没有其他已登录的设备。");
  });
}

test("delete: password and explicit checkbox consent both gate submission", async () => {
  const env = environment();
  env.window.Account.openDelete();
  const button = get(env, "account-dialog-submit");
  assert.equal(button.disabled, true);
  submit(env);
  assert.equal(env.calls.length, 0);
  input(env, "account-delete-password", "secret88");
  assert.equal(button.disabled, true);
  submit(env);
  assert.equal(env.calls.length, 0);
  confirmDelete(env, true);
  assert.equal(button.disabled, false);
  input(env, "account-delete-password", "");
  assert.equal(button.disabled, true);
  submit(env);
  await settle();
  assert.equal(env.calls.length, 0);
});

test("delete: consequences name retained content and offer personal export", () => {
  const env = environment();
  env.window.Account.openDelete();
  for (const copy of ["无法恢复", "全部题目", "易错点", "复习记录", "变体题", "错因标签", "AI 分析结果", "头像", "成员身份", "帖子和评论", "已注销用户", "订单记录", "不含个人资料", "先导出我的数据"]) {
    assert.ok(env.content.textContent.includes(copy), `explains ${copy}`);
  }
  assert.ok(env.content.querySelectorAll("a").some((link) => link.href === "/api/export"));
  assert.equal(get(env, "account-dialog-submit").textContent, "永久注销账号");
});

test("delete: successful request signs out, clears dialog and routes to welcome", async () => {
  const env = environment();
  env.window.Account.openDelete();
  const password = input(env, "account-delete-password", "secret88");
  const checkbox = confirmDelete(env, true);
  submit(env);
  const call = request(env, "/api/me/delete-account", { password: "secret88" });
  env.respond(call, 200, { ok: true });
  await settle();
  assert.equal(env.probe.signedOut, 1);
  assert.equal(env.dialog.open, false);
  assert.equal(password.value, "");
  assert.equal(checkbox.checked, false);
  assert.deepEqual(env.probe.history, [[null, "", "/#/welcome"]]);
  pageMessage(env, "账号已注销。");
});

test("delete: group conflict detail is shown locally without signing out", async () => {
  const env = environment();
  env.window.Account.openDelete();
  input(env, "account-delete-password", "secret88");
  confirmDelete(env, true);
  submit(env);
  env.respond(env.calls[0], 409, { detail: "你创建的小组里还有其他成员，请先移交或解散小组。" });
  await settle();
  assert.equal(env.dialog.open, true);
  assert.equal(get(env, "account-dialog-error").textContent, "你创建的小组里还有其他成员，请先移交或解散小组。");
  assert.equal(get(env, "account-dialog-submit").disabled, false);
  assert.equal(env.probe.signedOut, 0);
  assert.deepEqual(env.probe.messages, []);
});

for (const invalidation of ["reset", "change user", "same user new session"]) {
  for (const status of [200, 409]) {
    test(`guards: ${invalidation} ignores a late delete ${status} response`, async () => {
      const env = environment();
      env.window.Account.openDelete();
      const oldPassword = input(env, "account-delete-password", "secret88");
      confirmDelete(env, true);
      submit(env);
      if (invalidation === "change user") {
        vm.runInContext("user = { id: 8, is_trial: false }; sessionEpoch += 1;", env.context);
      } else if (invalidation === "same user new session") {
        vm.runInContext("sessionEpoch += 1;", env.context);
      } else if (invalidation === "reset") {
        env.window.Account.reset();
        assert.equal(oldPassword.value, "");
        assert.equal(env.window.Account.isPending(), true, "forced reset keeps the authentication lock");
        env.window.Account.openPassword();
        assert.equal(env.dialog.open, false, "cannot start a new authentication operation yet");
      } else {
        env.window.Account.close();
        assert.equal(oldPassword.value, "");
        if (invalidation === "close and reopen") env.window.Account.openPassword();
      }
      const before = env.content.textContent;
      const openBefore = env.dialog.open;
      env.respond(env.calls[0], status, status === 200 ? { ok: true } : { detail: "旧账号的小组冲突" });
      await settle();
      assert.equal(env.content.textContent, before);
      assert.equal(env.dialog.open, openBefore);
      assert.equal(env.probe.signedOut, 0);
      assert.deepEqual(env.probe.messages, []);
      assert.deepEqual(env.probe.history, []);
      if (invalidation === "reset") {
        env.window.Account.openPassword();
        assert.equal(get(env, "account-dialog-submit").disabled, false, "old finally does not disable the new form");
      }
    });
  }
}

test("guards: reset clears all three password fields and ignores late success", async () => {
  const env = environment();
  env.window.Account.openPassword();
  passwords(env);
  const fields = ["account-current-password", "account-new-password", "account-confirm-password"].map((id) => get(env, id));
  submit(env);
  env.window.Account.reset();
  assert.equal(env.dialog.open, false);
  assert.deepEqual(fields.map((field) => field.value), ["", "", ""]);
  env.respond(env.calls[0], 200, { ok: true, revoked_sessions: 9 });
  await settle();
  assert.deepEqual(env.probe.messages, []);
});

test("guards: pending authentication change locks close, reopening and logout until success", async () => {
  const env = environment();
  env.window.Account.openPassword();
  passwords(env);
  submit(env);
  assert.equal(env.window.Account.isPending(), true);
  assert.equal(get(env, "logout").disabled, true);
  env.window.Account.close();
  get(env, "account-dialog-close").click();
  get(env, "account-dialog-cancel").click();
  env.dialog.dispatchEvent(new FakeEvent("cancel"));
  env.dialog.dispatchEvent(new FakeEvent("keydown", { props: { key: "Escape" } }));
  assert.equal(env.dialog.open, true);
  env.window.Account.openRevokeOthers();
  submit(env);
  assert.equal(env.calls.length, 1);
  assert.ok(get(env, "account-current-password"));
  env.respond(env.calls[0], 200, { ok: true, revoked_sessions: 0 });
  await settle();
  assert.equal(env.dialog.open, false);
  assert.equal(env.window.Account.isPending(), false);
  assert.equal(get(env, "logout").disabled, false);
  pageMessage(env, "密码已更新。");
});

test("focus: entry closes the menu, focuses current password and restores trigger", () => {
  const env = environment();
  const trigger = get(env, "account-password");
  env.menu.open = true;
  trigger.click();
  assert.equal(env.probe.menuCloses, 1);
  assert.equal(env.menu.open, false);
  assert.equal(env.dialog.open, true);
  assert.equal(env.document.activeElement, get(env, "account-current-password"));
  get(env, "account-dialog-close").click();
  assert.equal(env.dialog.open, false);
  assert.equal(env.menu.open, true);
  assert.equal(env.document.activeElement, trigger);
});

test("focus: hidden trigger returns focus to the account summary", () => {
  const env = environment();
  const trigger = get(env, "account-delete");
  env.window.Account.openDelete(trigger);
  trigger.hidden = true;
  env.window.Account.close();
  assert.equal(env.document.activeElement, env.summary);
});

test("focus: dialog cancel (native Escape) closes and restores focus", () => {
  const env = environment();
  const trigger = get(env, "account-password");
  env.window.Account.openPassword(trigger);
  const event = new FakeEvent("cancel");
  env.dialog.dispatchEvent(event);
  assert.equal(env.dialog.open, false);
  assert.equal(env.document.activeElement, trigger);
});

test("focus: Escape key and the cancel button both close the dialog", () => {
  const env = environment();
  env.window.Account.openPassword();
  env.dialog.dispatchEvent(new FakeEvent("keydown", { props: { key: "Escape" } }));
  assert.equal(env.dialog.open, false);
  env.window.Account.openDelete();
  get(env, "account-dialog-cancel").click();
  assert.equal(env.dialog.open, false);
});

test("focus: Tab and Shift+Tab cannot leave the modal", () => {
  const env = environment();
  env.window.Account.openPassword();
  const first = get(env, "account-dialog-close");
  const last = get(env, "account-dialog-submit");
  last.focus();
  const forward = new FakeEvent("keydown", { props: { key: "Tab", shiftKey: false } });
  env.dialog.dispatchEvent(forward);
  assert.equal(forward.defaultPrevented, true);
  assert.equal(env.document.activeElement, first);
  first.focus();
  const back = new FakeEvent("keydown", { props: { key: "Tab", shiftKey: true } });
  env.dialog.dispatchEvent(back);
  assert.equal(back.defaultPrevented, true);
  assert.equal(env.document.activeElement, last);
});

test("focus: backdrop closes, while clicking dialog padding stays open", () => {
  const env = environment();
  env.window.Account.openPassword();
  env.dialog.dispatchEvent(new FakeEvent("click", { props: { clientX: 50, clientY: 10 } }));
  assert.equal(env.dialog.open, true);
  env.dialog.dispatchEvent(new FakeEvent("click", { props: { clientX: 150, clientY: 30 } }));
  assert.equal(env.dialog.open, false);
});

test("native close: pending authentication keeps its lock and dialog until response", async () => {
  const env = environment();
  env.window.Account.openPassword();
  passwords(env);
  const password = get(env, "account-current-password");
  submit(env);
  env.dialog.close();
  assert.equal(password.value, "old-pass88");
  assert.equal(env.dialog.open, true);
  assert.equal(env.window.Account.isPending(), true);
  env.respond(env.calls[0], 200, { ok: true, revoked_sessions: 0 });
  await settle();
  assert.equal(env.window.Account.isPending(), false);
  pageMessage(env, "密码已更新。");
});

for (const mode of ["revoke", "delete"]) {
  test(`guards: pending ${mode} also locks all session entrances until failure`, async () => {
    const env = environment();
    if (mode === "revoke") env.window.Account.openRevokeOthers();
    else {
      env.window.Account.openDelete();
      input(env, "account-delete-password", "secret88");
      confirmDelete(env, true);
    }
    submit(env);
    assert.equal(env.window.Account.isPending(), true);
    assert.equal(get(env, "logout").disabled, true);
    assert.equal(get(env, "account-dialog-close").disabled, true);
    assert.equal(get(env, "account-dialog-cancel").disabled, true);
    env.window.Account.close();
    env.window.Account.openPassword();
    assert.equal(env.dialog.open, true);
    assert.equal(env.calls.length, 1);
    env.respond(env.calls[0], 400, {detail: "请求失败"});
    await settle();
    assert.equal(env.window.Account.isPending(), false);
    assert.equal(get(env, "logout").disabled, false);
    assert.equal(get(env, "account-dialog-close").disabled, false);
    assert.equal(get(env, "account-dialog-cancel").disabled, false);
    assert.equal(get(env, "account-dialog-error").textContent, "请求失败");
    env.window.Account.close();
    assert.equal(env.dialog.open, false);
  });
}

test("guards: failed authentication releases logout and allows closing", async () => {
  const env = environment();
  env.window.Account.openRevokeOthers();
  submit(env);
  assert.equal(get(env, "logout").disabled, true);
  env.respond(env.calls[0], 429, { detail: "尝试次数过多" });
  await settle();
  assert.equal(env.window.Account.isPending(), false);
  assert.equal(get(env, "logout").disabled, false);
  env.window.Account.close();
  assert.equal(env.dialog.open, false);
});

test("trial account: exported open methods do not open restricted actions", () => {
  const env = environment();
  vm.runInContext("user = { id: 9, is_trial: true };", env.context);
  for (const open of ["openPassword", "openRevokeOthers", "openDelete"]) {
    env.window.Account[open]();
    assert.equal(env.dialog.open, false);
  }
  assert.equal(env.calls.length, 0);
});
