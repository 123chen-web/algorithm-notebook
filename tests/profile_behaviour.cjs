"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, FakeEvent, FakeDialog, tick } = require("./js_harness.cjs");
const settle = async () => { await tick(); await tick(); };
const profile = (id = 7) => ({ user_id: id, display_name: "<b>同学</b>", bio: "<script>简介</script>",
  has_avatar: false, avatar_version: 5, lifetime_problem_count: 8, joined_at: "2026-10-01",
  settings: { bio: "<script>简介</script>", rank_display_name: "", profile_public_bio: true, profile_public_count: true, profile_public_joined: true } });

test("profile ignores an old queued close event after immediate reopening", async () => {
  const ctx = setup();
  ctx.env.window.Profile.open(7);
  ctx.dialog.close();
  ctx.env.window.Profile.open(7);
  ctx.dialog.dispatchEvent(new FakeEvent("close"));
  ctx.env.respond(ctx.env.calls[1], 200, profile(7)); await settle();
  assert.equal(ctx.dialog.open, true);
  assert.match(ctx.content.textContent, /累计录入题目/);
  ctx.env.respond(ctx.env.calls[0], 200, { ...profile(7), bio: "旧简介" }); await settle();
  assert.equal(ctx.content.textContent.includes("旧简介"), false);
});

function setup() {
  const env = load([]);
  const dialog = new FakeDialog(env.document);
  dialog.id = "profile-dialog";
  env.document.body.append(dialog);
  const content = env.document.createElement("div");
  content.id = "profile-content"; dialog.append(content);
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../static/profile.js"), "utf8"), env.context);
  const state = { user: { id: 7 }, epoch: 1, view: "forum" };
  const hooks = {
    getUser: () => state.user, getEpoch: () => state.epoch, getView: () => state.view,
    api: async (path, options = {}) => {
      const response = await env.window.fetch(path, { ...options, headers: { "X-CSRF-Protection": "1" } });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || "失败");
      return body;
    },
    avatar: () => env.document.createElement("span"),
  };
  env.window.Profile.configure(hooks);
  return { env, dialog, content, state };
}
async function open(ctx, id = 7, data = profile(id)) {
  ctx.env.window.Profile.open(id);
  ctx.env.respond(ctx.env.calls.at(-1), 200, data); await settle();
}

test("profile renders public strings literally and labels current retained totals", async () => {
  const ctx = setup(); await open(ctx);
  assert.equal(ctx.dialog.open, true);
  assert.equal(ctx.content.querySelector("script"), null);
  assert.equal(ctx.content.querySelector("b"), null);
  assert.match(ctx.content.textContent, /<b>同学<\/b>/);
  assert.match(ctx.content.textContent, /累计录入题目/);
  assert.match(ctx.content.textContent, /加入时间/);
  const totals = ctx.content.querySelector('dl').children;
  assert.equal(totals.length, 2);
  assert.equal(totals[0].textContent, '累计录入题目8');
  assert.equal(totals[1].textContent, '加入时间2026-10-01');
  assert.match(ctx.content.textContent, /以后删除不减少/);
  assert.equal(ctx.content.querySelector("textarea").value, "<script>简介</script>");
});
test("profile author button opens the selected member without a raw request", async () => {
  const ctx = setup();
  const link = ctx.env.window.Profile.author(9, "成员九");
  assert.equal(link.tagName, "BUTTON");
  assert.equal(link.type, "button");
  link.click();
  assert.equal(ctx.env.calls[0].url, "/api/users/9/public");
  ctx.env.respond(ctx.env.calls[0], 200, profile(9)); await settle();
  assert.equal(ctx.content.querySelector("form"), null);
});
test("profile save uses CSRF api, ignores duplicate submits and preserves failed draft", async () => {
  const ctx = setup(); await open(ctx);
  const form = ctx.content.querySelector("form"), input = form.querySelector("textarea");
  input.value = "公开简介草稿";
  form.dispatchEvent(new FakeEvent("submit")); form.dispatchEvent(new FakeEvent("submit"));
  assert.equal(ctx.env.calls.length, 2);
  const call = ctx.env.calls[1];
  assert.equal(call.url, "/api/me/bio"); assert.equal(call.init.method, "PUT");
  assert.equal(call.init.headers["X-CSRF-Protection"], "1");
  assert.deepEqual(JSON.parse(call.init.body), { bio: "公开简介草稿" });
  ctx.env.respond(call, 422, { detail: "简介太长" }); await settle();
  assert.equal(input.value, "公开简介草稿");
  assert.equal(form.querySelector("button").disabled, false);
  assert.match(ctx.content.textContent, /简介太长/);
});
test("profile successful save and clear update the public bio", async () => {
  const ctx = setup(); await open(ctx);
  const form = ctx.content.querySelector("form"); form.querySelector("textarea").value = "";
  form.dispatchEvent(new FakeEvent("submit")); ctx.env.respond(ctx.env.calls[1], 200, { bio: "" }); await settle();
  assert.match(ctx.content.textContent, /还没有填写简介/);
  assert.match(ctx.content.textContent, /简介已保存/);
});
for (const transition of ["account", "epoch", "view"]) {
  test(`profile late save after ${transition} preserves the next screen`, async () => {
    const ctx = setup(); await open(ctx);
    const form = ctx.content.querySelector("form"); form.querySelector("textarea").value = "旧简介";
    form.dispatchEvent(new FakeEvent("submit"));
    if (transition === "account") ctx.state.user = { id: 8 };
    if (transition === "epoch") ctx.state.epoch++;
    if (transition === "view") ctx.state.view = "groups";
    ctx.env.window.Profile.reset();
    await open(ctx, 8, { ...profile(8), bio: "下一个资料" });
    ctx.env.respond(ctx.env.calls[1], 422, { detail: "旧错误" }); await settle();
    assert.equal(ctx.content.textContent.includes("旧错误"), false);
    assert.equal(ctx.content.textContent.includes("下一个资料"), true);
  });
}
for (const transition of ["account", "epoch", "view", "close", "reset"]) {
  test(`profile ignores late read after ${transition}`, async () => {
    const ctx = setup(); ctx.env.window.Profile.open(9);
    if (transition === "account") ctx.state.user = { id: 8 };
    if (transition === "epoch") ctx.state.epoch++;
    if (transition === "view") { ctx.state.view = "groups"; ctx.env.document.dispatchEvent(new FakeEvent("app:view-changed")); }
    if (transition === "close") ctx.dialog.close();
    if (transition === "reset") ctx.env.window.Profile.reset();
    ctx.env.respond(ctx.env.calls[0], 200, profile(9)); await settle();
    assert.equal(ctx.content.textContent.includes("<b>同学</b>"), false);
  });
}
test("profile newer selection wins reversed response order", async () => {
  const ctx = setup(); ctx.env.window.Profile.open(9); ctx.env.window.Profile.open(10);
  ctx.env.respond(ctx.env.calls[1], 200, { ...profile(10), display_name: "十" }); await settle();
  ctx.env.respond(ctx.env.calls[0], 200, { ...profile(9), display_name: "九" }); await settle();
  assert.equal(ctx.content.querySelector("h3").textContent, "十");
});
test("profile late save cannot replace a reopened profile or leave its controls disabled", async () => {
  const ctx = setup(); await open(ctx);
  const old = ctx.content.querySelector("form"); old.querySelector("textarea").value = "旧";
  old.dispatchEvent(new FakeEvent("submit")); ctx.dialog.close();
  await open(ctx, 7, { ...profile(), bio: "新" });
  ctx.env.respond(ctx.env.calls[1], 200, { bio: "旧" }); await settle();
  assert.equal(ctx.content.querySelector("textarea").value, "新");
  assert.equal(ctx.content.querySelector("form button").disabled, false);
});
test("profile reports current failure, invalid ID and missing user send no extra request", async () => {
  const ctx = setup(); ctx.env.window.Profile.open(0);
  assert.equal(ctx.env.calls.length, 0);
  ctx.env.window.Profile.open(9); ctx.env.respond(ctx.env.calls[0], 404, { detail: "用户不存在" }); await settle();
  assert.match(ctx.content.textContent, /用户不存在/);
  ctx.state.user = null; ctx.env.window.Profile.open(9);
  assert.equal(ctx.env.calls.length, 1);
});

test("feedback: another member's hidden fields and learning details are absent", async () => {
  const ctx = setup();
  await open(ctx, 9, { user_id: 9, display_name: "同学", avatar_version: 0, has_avatar: false });
  assert.equal(ctx.content.querySelector(".profile-bio"), null);
  assert.equal(ctx.content.querySelector("dl").children.length, 0);
  assert.equal(ctx.content.querySelector("form"), null);
  assert.equal(ctx.content.textContent.includes("undefined"), false);
});

test("feedback: owner saves rank name and three public preferences with CSRF api", async () => {
  const ctx = setup(); await open(ctx);
  const input = ctx.content.querySelector('.profile-rank-name');
  const form = input.closest('form'); input.value = '张三';
  // The minimal DOM harness does not reflect the type property into attributes.
  form.querySelectorAll('input').find((field) => field.type === 'checkbox').checked = false;
  form.dispatchEvent(new FakeEvent('submit')); form.dispatchEvent(new FakeEvent('submit'));
  const call = ctx.env.calls[1];
  assert.equal(call.url, '/api/me/profile-settings');
  assert.deepEqual(JSON.parse(call.init.body), { rank_display_name: '张三', profile_public_bio: false,
    profile_public_count: true, profile_public_joined: true });
  ctx.env.respond(call, 422, { detail: '榜单显示名无效' }); await settle();
  assert.match(form.textContent, /榜单显示名无效/);
  assert.equal(form.querySelector('button').disabled, false);
});
