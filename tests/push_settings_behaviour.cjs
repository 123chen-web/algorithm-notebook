"use strict";

/* 微信提醒卡片（PushSettings）行为测试：
   首次加载只显示尾号不回填密钥；保存 / 发送测试消息的迟到响应一律丢弃；
   未配置时测试按钮禁用；连续失败被自动关闭后展示提示；体验账号不加载；reset 清空。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const DEFAULT_STATE = {
  channel: null, enabled: false, configured: false, tail: "", fail_count: 0,
};
const CONFIGURED = {
  channel: "serverchan", enabled: true, configured: true, tail: "a1B2", fail_count: 0,
};
const httpError = (status, detail) => Object.assign(new Error(detail), { status });

function setup(user = { id: 1, is_trial: false }) {
  const env = load(["push-settings.js"]);
  const state = { epoch: 1, user };
  const calls = [];
  const hooks = {
    api: (url, init) => { const call = { url, init, ...deferred() }; calls.push(call); return call.promise; },
    getUser: () => state.user,
    getEpoch: () => state.epoch,
  };
  env.window.PushSettings.configure(hooks);
  const container = env.document.createElement("div");
  env.document.body.append(container);
  const q = (selector) => container.querySelector(selector);
  const click = (selector) => q(selector).dispatchEvent(new FakeEvent("click", { bubbles: true }));
  const submit = () => q("form").dispatchEvent(new FakeEvent("submit", { bubbles: true }));
  return { ...env, state, calls, container, q, click, submit };
}

async function mount(env) {
  const mounted = env.window.PushSettings.mount(env.container);
  await tick();
  return mounted;
}

test("mount loads state and renders unconfigured defaults", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, "/api/me/push");
  env.calls[0].resolve(DEFAULT_STATE);
  await mounted;
  await tick();
  assert.equal(env.q(".ps-card").hidden, false);
  assert.equal(env.q(".ps-channel").value, "serverchan");
  assert.equal(env.q(".ps-enabled").checked, false);
  assert.equal(env.q(".ps-tail").textContent, "");
  assert.equal(env.q(".ps-secret").value, "");
  assert.equal(env.q(".ps-test").disabled, true);
  assert.equal(env.q(".ps-note").hidden, true);
});

test("configured state shows tail only, never the secret itself", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(CONFIGURED);
  await mounted;
  await tick();
  assert.equal(env.q(".ps-channel").value, "serverchan");
  assert.equal(env.q(".ps-enabled").checked, true);
  assert.match(env.q(".ps-tail").textContent, /尾号 a1B2/);
  assert.equal(env.q(".ps-secret").value, "");
  assert.match(env.q(".ps-secret").getAttribute("placeholder"), /a1B2/);
  assert.equal(env.q(".ps-test").disabled, false);
  assert.equal(env.container.textContent.includes("SCTa1B2a1B2"), false);
});

test("pushplus channel is selected when the server reports it", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve({
    channel: "pushplus", enabled: false, configured: true, tail: "6789", fail_count: 2,
  });
  await mounted;
  assert.equal(env.q(".ps-channel").value, "pushplus");
  assert.match(env.q(".ps-tail").textContent, /尾号 6789/);
});

test("save sends secret when typed and null when left blank", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(CONFIGURED);
  await mounted;

  // 已配置后不改密钥：输入框留空 → secret 为 null。
  env.q(".ps-enabled").checked = false;
  env.click(".ps-save");
  await tick();
  assert.equal(env.calls[1].url, "/api/me/push");
  assert.equal(env.calls[1].init.method, "PUT");
  assert.deepEqual(JSON.parse(env.calls[1].init.body), {
    channel: "serverchan", secret: null, enabled: false,
  });
  env.calls[1].resolve({ ...CONFIGURED, enabled: false });
  await tick();

  // 输入新密钥时随保存发送，成功后输入框清空并显示新尾号。
  env.q(".ps-secret").value = "abcdef0123456789abcdef0123456789";
  env.q(".ps-channel").value = "pushplus";
  env.q(".ps-enabled").checked = true;
  env.click(".ps-save");
  await tick();
  assert.deepEqual(JSON.parse(env.calls[2].init.body), {
    channel: "pushplus",
    secret: "abcdef0123456789abcdef0123456789",
    enabled: true,
  });
  env.calls[2].resolve({
    channel: "pushplus", enabled: true, configured: true, tail: "6789", fail_count: 0,
  });
  await tick();
  assert.equal(env.q(".ps-secret").value, "");
  assert.match(env.q(".ps-tail").textContent, /尾号 6789/);
  assert.match(env.q(".ps-status").textContent, /已保存/);
});

test("save failure shows the server message and keeps the form", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(DEFAULT_STATE);
  await mounted;
  env.q(".ps-secret").value = "short";
  env.click(".ps-save");
  await tick();
  env.calls[1].reject(httpError(422, "密钥格式不正确"));
  await tick();
  await tick();
  assert.match(env.q(".ps-status").textContent, /密钥格式不正确/);
  assert.equal(env.q(".ps-secret").value, "short");
});

test("test button posts and paints the Chinese result", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(CONFIGURED);
  await mounted;

  env.click(".ps-test");
  await tick();
  assert.equal(env.calls[1].url, "/api/me/push/test");
  assert.equal(env.calls[1].init.method, "POST");
  env.calls[1].resolve({ ok: true, message: "测试消息已发送，请在微信里确认收到。" });
  await tick();
  assert.match(env.q(".ps-status").textContent, /测试消息已发送/);

  env.click(".ps-test");
  await tick();
  env.calls[2].resolve({ ok: false, message: "服务商拒绝了密钥（认证失败），请核对后重新保存。" });
  await tick();
  assert.match(env.q(".ps-status").textContent, /认证失败/);
});

test("test button shows our own rate-limit error", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(CONFIGURED);
  await mounted;
  env.click(".ps-test");
  await tick();
  env.calls[1].reject(httpError(429, "测试消息发送太频繁，请每小时最多发送 5 条"));
  await tick();
  await tick();
  assert.match(env.q(".ps-status").textContent, /每小时最多发送 5 条/);
});

test("auto-disabled note appears after five failures and clears after resave", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve({
    channel: "serverchan", enabled: false, configured: true, tail: "a1B2", fail_count: 5,
  });
  await mounted;
  assert.equal(env.q(".ps-note").hidden, false);
  assert.match(env.q(".ps-note").textContent, /自动关闭/);
  assert.match(env.q(".ps-note").textContent, /检查 Key/);

  // 保存新密钥后后端清零失败计数，提示消失。
  env.q(".ps-secret").value = "SCT" + "a1B2" * 10;
  env.q(".ps-enabled").checked = true;
  env.click(".ps-save");
  await tick();
  env.calls[1].resolve({ ...CONFIGURED, fail_count: 0 });
  await tick();
  assert.equal(env.q(".ps-note").hidden, true);
});

test("late GET response after reset is discarded", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.window.PushSettings.reset();
  assert.equal(env.container.children.length, 0);
  env.calls[0].resolve(CONFIGURED);
  await tick();
  assert.equal(env.container.children.length, 0);
});

test("late save response after switching user is discarded", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(CONFIGURED);
  await mounted;
  env.q(".ps-secret").value = "SCT" + "a1B2" * 10;
  env.click(".ps-save");
  await tick();
  // 登出再登录成另一个人。
  env.state.epoch += 1;
  env.state.user = { id: 2, is_trial: false };
  env.calls[1].resolve({ ...CONFIGURED, tail: "9999" });
  await tick();
  assert.doesNotMatch(env.q(".ps-status").textContent, /已保存/);
});

test("late test response after epoch change is discarded", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(CONFIGURED);
  await mounted;
  env.click(".ps-test");
  env.state.epoch += 1;
  env.calls[1].resolve({ ok: true, message: "测试消息已发送，请在微信里确认收到。" });
  await tick();
  assert.doesNotMatch(env.q(".ps-status").textContent, /测试消息已发送/);
});

test("trial account renders nothing and makes no request", async () => {
  const env = setup({ id: 7, is_trial: true });
  await mount(env);
  assert.equal(env.calls.length, 0);
  assert.equal(env.q(".ps-card").hidden, true);
});

test("logged-out state renders nothing and makes no request", async () => {
  const env = setup(null);
  await mount(env);
  assert.equal(env.calls.length, 0);
});

test("double clicking save while pending sends only one request", async () => {
  const env = setup();
  const mounted = mount(env);
  await tick();
  env.calls[0].resolve(DEFAULT_STATE);
  await mounted;
  env.q(".ps-secret").value = "SCT" + "a1B2" * 10;
  env.q(".ps-enabled").checked = true;
  env.click(".ps-save");
  env.click(".ps-save");
  await tick();
  const puts = env.calls.filter((call) => call.url === "/api/me/push" && call.init?.method === "PUT");
  assert.equal(puts.length, 1);
});
