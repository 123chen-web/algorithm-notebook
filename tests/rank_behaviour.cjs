"use strict";

/* 榜单页新区块（static/rank.js）和管理后台“今日一条”（static/rank-admin.js）的行为测试：
   Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。覆盖字符串断言测不到的东西：
   渲染、迟到响应被丢弃（登出 / 换号 / 换页 / 乱序）、设置开关、管理员表单。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const plain = (value) => JSON.parse(JSON.stringify(value));

function setup({ admin = false, trial = false, view = "leaderboard" } = {}) {
  const env = load(["rank.js", "rank-admin.js"], { extra: { URL } });
  const state = { user: { id: 7, is_admin: admin, is_trial: trial, public_rank_opt_out: false }, epoch: 1, view };
  const hooks = {
    api: (path, options = {}) => env.window.fetch(path, options).then((response) => response.json()),
    getUser: () => state.user,
    getEpoch: () => state.epoch,
    getView: () => state.view,
  };
  // 假 fetch 的响应对象只有 json()；api() 里失败要抛错，这里照着宿主 app.js 的样子包一层。
  hooks.api = async (path, options = {}) => {
    const response = await env.window.fetch(path, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "请求失败");
    return data;
  };
  env.window.Rank.configure(hooks);
  env.window.RankAdmin.configure(hooks);
  const $ = (selector) => env.document.querySelector(selector);
  return { env, state, $, hooks };
}

const ENTRY = (over = {}) => ({
  rank: 1, user_id: 3, username: "小明", avatar_version: 0, count: 42, streak_days: 12,
  praise: "昨天复习了 42 次，连续第 12 天，稳！", is_me: false, ...over,
});
const YESTERDAY = (entries = [ENTRY()], me = {}) => ({
  day: "2026-10-03", timezone: "Asia/Shanghai", top_size: 10, entries,
  me: { count: 5, rank: null, in_top: false, gap: 3, is_trial: false, opted_out: false, ...me },
});
const HOT = (entries = []) => ({ from: "2026-09-27", to: "2026-10-03", timezone: "Asia/Shanghai", min_users: 5, entries });
const HOT_ENTRY = (over = {}) => ({
  source: "leetcode", source_label: "LeetCode", name: "Two Sum", title: "LeetCode · Two Sum", users: 8,
  url: "https://leetcode.cn/problems/two-sum/", ...over,
});

const byPath = (env, path) => env.calls.filter((call) => call.url === path);
async function settle() { await tick(); await tick(); }

/* ---------------- 渲染 ---------------- */
test("rank: renders the yesterday list, my line, hot problems and the notice", async () => {
  const { env, $ } = setup();
  const loading = env.window.Rank.load();
  await tick();
  assert.deepEqual(env.calls.map((call) => call.url).sort(), ["/api/rank/hot-problems", "/api/rank/notice", "/api/rank/yesterday"]);
  env.respond(byPath(env, "/api/rank/yesterday")[0], 200, YESTERDAY(
    [ENTRY(), ENTRY({ rank: 2, user_id: 4, username: "小红", count: 30, streak_days: 3, praise: "p", is_me: true })]));
  env.respond(byPath(env, "/api/rank/hot-problems")[0], 200, HOT([HOT_ENTRY()]));
  env.respond(byPath(env, "/api/rank/notice")[0], 200, { notice: { text: "今天也加油", link: "https://example.com/a" } });
  await loading;
  const rows = $("#rank-yesterday-list").children;
  assert.equal(rows.length, 2);
  assert.ok(rows[0].classList.contains("is-top") && rows[0].classList.contains("is-first"));
  assert.ok(rows[1].classList.contains("is-me"));
  assert.match(rows[0].textContent, /小明/);
  assert.match(rows[0].textContent, /昨天复习了 42 次，连续第 12 天，稳！/);
  assert.match(rows[0].textContent, /42 次/);
  assert.match(rows[0].textContent, /连续 12 天/);
  assert.equal($("#rank-yesterday-date").textContent, "2026-10-03");
  assert.equal($("#rank-yesterday-me").textContent, "你昨天复习了 5 次，距离前十还差 3 次。");
  assert.equal($("#rank-yesterday").getAttribute("aria-busy"), "false");
  const hot = $("#rank-hot-list").children[0].querySelector("a");
  assert.equal(hot.getAttribute("href"), "https://leetcode.cn/problems/two-sum/");
  assert.equal(hot.getAttribute("rel"), "noopener noreferrer");
  assert.equal(hot.getAttribute("target"), "_blank");
  assert.match(hot.textContent, /LeetCode.*Two Sum.*8 人/);
  assert.equal($("#rank-notice").hidden, false);
  assert.equal($("#rank-notice-text").textContent, "今天也加油");
  const link = $("#rank-notice-link");
  assert.equal(link.hidden, false);
  assert.equal(link.getAttribute("rel"), "noopener noreferrer");
  assert.equal(link.getAttribute("target"), "_blank");
});

test("rank: server text is shown as text, never parsed as HTML", async () => {
  const { env, $ } = setup();
  const loading = env.window.Rank.load();
  await tick();
  env.respond(byPath(env, "/api/rank/yesterday")[0], 200,
    YESTERDAY([ENTRY({ username: "<img src=x onerror=alert(1)>", praise: "<b>粗体</b>" })]));
  env.respond(byPath(env, "/api/rank/hot-problems")[0], 200, HOT([]));
  env.respond(byPath(env, "/api/rank/notice")[0], 200, { notice: { text: "<script>alert(1)</script>", link: null } });
  await loading;
  const row = $("#rank-yesterday-list").children[0];
  assert.equal(row.querySelectorAll("img, b, script").length, 0);
  assert.match(row.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal($("#rank-notice-text").textContent, "<script>alert(1)</script>");
  assert.equal($("#rank-notice").querySelectorAll("script").length, 0);
  assert.equal($("#rank-notice-link").hidden, true);
});

test("rank: unsafe notice links and non-whitelisted hot links never become clickable", async () => {
  const { env, $ } = setup();
  for (const link of ["javascript:alert(1)", "data:text/html,x", "https://user:pw@example.com/", "ftp://example.com/"]) {
    const loading = env.window.Rank.load();
    await tick();
    const latest = (path) => byPath(env, path).at(-1);
    env.respond(latest("/api/rank/yesterday"), 200, YESTERDAY([]));
    env.respond(latest("/api/rank/hot-problems"), 200, HOT([
      HOT_ENTRY({ url: link }), HOT_ENTRY({ url: "https://evil.example/problems/two-sum/" }),
    ]));
    env.respond(latest("/api/rank/notice"), 200, { notice: { text: "文字", link } });
    await loading;
    assert.equal($("#rank-notice-link").hidden, true, link);
    assert.equal($("#rank-notice-link").getAttribute("href"), null, link);
    assert.equal($("#rank-hot-list").querySelectorAll("a").length, 0, link);
    assert.equal($("#rank-hot-list").children.length, 2, "行仍显示，只是不可点击");
  }
});

test("rank: empty states and the different 'me' sentences", async () => {
  const { env, $ } = setup();
  const { meText } = env.window.Rank.helpers;
  assert.equal(meText({ count: 4, in_top: true, rank: 2 }), "你昨天复习了 4 次，排第 2 名。");
  assert.equal(meText({ count: 0, gap: 1 }), "你昨天复习了 0 次，距离前十还差 1 次。");
  assert.match(meText({ count: 2, opted_out: true }), /不参与公开榜单/);
  assert.match(meText({ count: 2, is_trial: true }), /体验账号不参与榜单/);
  const loading = env.window.Rank.load();
  await tick();
  env.respond(byPath(env, "/api/rank/yesterday")[0], 200, YESTERDAY([]));
  env.respond(byPath(env, "/api/rank/hot-problems")[0], 200, HOT([]));
  env.respond(byPath(env, "/api/rank/notice")[0], 200, { notice: null });
  await loading;
  assert.match($("#rank-yesterday-status").textContent, /还没有人上榜/);
  assert.match($("#rank-hot-status").textContent, /5 人的门槛/);
  assert.equal($("#rank-notice").hidden, true);
});

test("rank: a failing section shows a retry button and does not break the others", async () => {
  const { env, $ } = setup();
  const loading = env.window.Rank.load();
  await tick();
  env.respond(byPath(env, "/api/rank/yesterday")[0], 500, { detail: "服务器出错了" });
  env.respond(byPath(env, "/api/rank/hot-problems")[0], 200, HOT([HOT_ENTRY()]));
  env.respond(byPath(env, "/api/rank/notice")[0], 500, { detail: "x" });
  await loading;
  assert.equal($("#rank-yesterday-status").textContent, "服务器出错了");
  assert.equal($("#rank-yesterday-retry").hidden, false);
  assert.equal($("#rank-hot-list").children.length, 1);
  assert.equal($("#rank-notice").hidden, true, "今日一条失败时安静地不显示");
  $("#rank-yesterday-retry").click();
  await tick();
  assert.equal(byPath(env, "/api/rank/yesterday").length, 2);
  assert.equal($("#rank-yesterday-retry").hidden, true);
});

/* ---------------- 迟到响应 ---------------- */
function startLoad(ctx) {
  const loading = ctx.env.window.Rank.load();
  return { loading, answer: async (yesterday, hot, notice) => {
    await tick();
    const last = (path) => byPath(ctx.env, path).at(-1);
    ctx.env.respond(last("/api/rank/yesterday"), 200, yesterday);
    ctx.env.respond(last("/api/rank/hot-problems"), 200, hot);
    ctx.env.respond(last("/api/rank/notice"), 200, notice);
    await loading;
  } };
}

test("rank: answers that arrive after sign-out never reach the page", async () => {
  const ctx = setup();
  const pending = startLoad(ctx);
  await tick();
  ctx.env.window.Rank.reset(); // 登出
  await pending.answer(YESTERDAY([ENTRY({ username: "上一位用户看到的" })]), HOT([HOT_ENTRY()]), { notice: { text: "旧", link: null } });
  assert.equal(ctx.$("#rank-yesterday-list").children.length, 0);
  assert.equal(ctx.$("#rank-hot-list").children.length, 0);
  assert.equal(ctx.$("#rank-notice").hidden, true);
  assert.equal(ctx.$("#rank-yesterday-me").textContent, "");
});

test("rank: answers for a previous account are dropped after switching accounts", async () => {
  const ctx = setup();
  const pending = startLoad(ctx);
  await tick();
  ctx.state.user = { id: 99, is_admin: false, is_trial: false }; // 同一页面换了号
  ctx.state.epoch += 1;
  await pending.answer(YESTERDAY([ENTRY()]), HOT([HOT_ENTRY()]), { notice: { text: "旧", link: null } });
  assert.equal(ctx.$("#rank-yesterday-list").children.length, 0);
  assert.equal(ctx.$("#rank-hot-list").children.length, 0);
});

test("rank: answers are dropped after leaving the leaderboard page", async () => {
  const ctx = setup();
  const pending = startLoad(ctx);
  await tick();
  ctx.state.view = "home";
  ctx.env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view: "home" } }));
  await pending.answer(YESTERDAY([ENTRY()]), HOT([HOT_ENTRY()]), { notice: { text: "旧", link: null } });
  assert.equal(ctx.$("#rank-yesterday-list").children.length, 0);
});

test("rank: an older response cannot overwrite a newer one (out of order)", async () => {
  const ctx = setup();
  const first = ctx.env.window.Rank.load();
  await tick();
  const second = ctx.env.window.Rank.load();
  await tick();
  const yesterday = byPath(ctx.env, "/api/rank/yesterday");
  assert.equal(yesterday.length, 2);
  ctx.env.respond(yesterday[1], 200, YESTERDAY([ENTRY({ username: "新的" })]));
  await tick();
  ctx.env.respond(yesterday[0], 200, YESTERDAY([ENTRY({ username: "旧的" })]));
  for (const call of [...byPath(ctx.env, "/api/rank/hot-problems"), ...byPath(ctx.env, "/api/rank/notice")]) {
    ctx.env.respond(call, 200, call.url.endsWith("notice") ? { notice: null } : HOT([]));
  }
  await Promise.all([first, second]);
  assert.match(ctx.$("#rank-yesterday-list").children[0].textContent, /新的/);
  assert.equal(ctx.$("#rank-yesterday-list").children.length, 1);
});

test("rank: nothing is requested while signed out", async () => {
  const ctx = setup();
  ctx.state.user = null;
  assert.equal(await ctx.env.window.Rank.load(), false);
  assert.equal(ctx.env.calls.length, 0);
});

/* ---------------- 设置开关 ---------------- */
function toggle(ctx, wanted) {
  const box = ctx.$("#account-public-rank");
  box.checked = wanted;
  box.dispatchEvent(new FakeEvent("change", { bubbles: true }));
  return box;
}

test("setting: turning the switch off PUTs participate=false, updates the user and reloads the board", async () => {
  const ctx = setup();
  ctx.env.window.Rank.syncSetting(ctx.state.user);
  assert.equal(ctx.$("#account-public-rank").checked, true);
  assert.equal(ctx.$("#account-public-rank-label").hidden, false);
  const box = toggle(ctx, false);
  await tick();
  const call = byPath(ctx.env, "/api/me/public-rank")[0];
  assert.equal(call.init.method, "PUT");
  assert.deepEqual(plain(JSON.parse(call.init.body)), { participate: false });
  assert.equal(box.disabled, true, "保存期间不能再点");
  ctx.env.respond(call, 200, { participate: false });
  await settle();
  assert.equal(ctx.state.user.public_rank_opt_out, true);
  assert.equal(box.disabled, false);
  assert.match(ctx.$("#account-public-rank-status").textContent, /已退出公开榜单/);
  assert.equal(byPath(ctx.env, "/api/rank/yesterday").length, 1, "在榜单页上改设置会立刻刷新榜单");
});

test("setting: when the host can refresh the whole page it does that instead of reloading only the new blocks", async () => {
  const ctx = setup();
  let refreshed = 0;
  ctx.hooks.refreshPage = () => { refreshed += 1; };
  ctx.env.window.Rank.syncSetting(ctx.state.user);
  toggle(ctx, false);
  await tick();
  ctx.env.respond(byPath(ctx.env, "/api/me/public-rank")[0], 200, { participate: false });
  await settle();
  assert.equal(refreshed, 1);
  assert.equal(byPath(ctx.env, "/api/rank/yesterday").length, 0);
});

test("setting: a failed save puts the switch back and says so", async () => {
  const ctx = setup();
  ctx.env.window.Rank.syncSetting(ctx.state.user);
  const box = toggle(ctx, false);
  await tick();
  ctx.env.respond(byPath(ctx.env, "/api/me/public-rank")[0], 500, { detail: "出错了" });
  await settle();
  assert.equal(box.checked, true);
  assert.equal(box.disabled, false);
  assert.equal(ctx.state.user.public_rank_opt_out, false);
  assert.match(ctx.$("#account-public-rank-status").textContent, /没有保存成功：出错了/);
});

test("setting: a save answered after sign-out changes nothing", async () => {
  const ctx = setup();
  ctx.env.window.Rank.syncSetting(ctx.state.user);
  const oldUser = ctx.state.user;
  toggle(ctx, false);
  await tick();
  ctx.env.window.Rank.reset(); // 登出
  ctx.state.user = { id: 8, is_admin: false, is_trial: false, public_rank_opt_out: false };
  ctx.state.epoch += 1;
  ctx.env.window.Rank.syncSetting(ctx.state.user);
  ctx.env.respond(byPath(ctx.env, "/api/me/public-rank")[0], 200, { participate: false });
  await settle();
  assert.equal(oldUser.public_rank_opt_out, false);
  assert.equal(ctx.state.user.public_rank_opt_out, false, "下一位用户的资料不受影响");
  assert.equal(ctx.$("#account-public-rank").checked, true);
});

test("setting: a profile refresh while saving does not flip the switch back", async () => {
  const ctx = setup();
  ctx.env.window.Rank.syncSetting(ctx.state.user);
  const box = toggle(ctx, false);
  await tick();
  ctx.env.window.Rank.syncSetting({ ...ctx.state.user }); // 正在保存时来了一次资料刷新
  assert.equal(box.checked, false);
  ctx.env.respond(byPath(ctx.env, "/api/me/public-rank")[0], 200, { participate: false });
  await settle();
  assert.equal(box.checked, false);
});

test("setting: trial accounts do not see the switch, signed-out hides it", async () => {
  const ctx = setup({ trial: true });
  ctx.env.window.Rank.syncSetting(ctx.state.user);
  assert.equal(ctx.$("#account-public-rank-label").hidden, true);
  ctx.env.window.Rank.syncSetting({ id: 1, is_trial: false, public_rank_opt_out: true });
  assert.equal(ctx.$("#account-public-rank-label").hidden, false);
  assert.equal(ctx.$("#account-public-rank").checked, false);
  ctx.env.window.Rank.reset();
  assert.equal(ctx.$("#account-public-rank-label").hidden, true);
});

/* ---------------- 管理后台 ---------------- */
const NOTICES = (items = []) => ({ today: "2026-10-04", notices: items });
const ITEM = (over = {}) => ({
  id: 1, text: "第一条", link: "https://example.com/x", start_date: "2026-10-01", end_date: "2026-10-09",
  is_active: true, status: "active", created_at: "", updated_at: "", ...over,
});
function fillAdmin(ctx, { text = "新的一句话", link = "", start = "2026-10-04", end = "2026-10-05", active = true } = {}) {
  ctx.$("#rank-admin-text").value = text;
  ctx.$("#rank-admin-link").value = link;
  ctx.$("#rank-admin-start").value = start;
  ctx.$("#rank-admin-end").value = end;
  ctx.$("#rank-admin-active").checked = active;
}
const submitAdmin = (ctx) => ctx.$("#rank-admin-form").dispatchEvent(new FakeEvent("submit", { bubbles: true }));

test("admin: non-admins never request the notices", async () => {
  const ctx = setup({ admin: false });
  assert.equal(await ctx.env.window.RankAdmin.load(), false);
  assert.equal(ctx.env.calls.length, 0);
});

test("admin: lists history with status badges, defaults the dates to Beijing today", async () => {
  const ctx = setup({ admin: true, view: "admin" });
  const loading = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.env.respond(ctx.env.calls[0], 200, NOTICES([
    ITEM(), ITEM({ id: 2, text: "<b>停用的</b>", link: "javascript:alert(1)", is_active: false, status: "disabled" }),
  ]));
  await loading;
  const rows = ctx.$("#rank-admin-list").children;
  assert.equal(rows.length, 2);
  assert.match(rows[0].textContent, /生效中.*2026-10-01 至 2026-10-09.*第一条/);
  assert.match(rows[1].textContent, /已停用/);
  assert.equal(rows[1].querySelectorAll("b").length, 0);
  assert.equal(rows[1].querySelectorAll("a").length, 0, "非法链接不渲染成 <a>");
  const link = rows[0].querySelector("a");
  assert.equal(link.getAttribute("rel"), "noopener noreferrer");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(ctx.$("#rank-admin-start").value, "2026-10-04");
  assert.equal(ctx.$("#rank-admin-end").value, "2026-10-04");
});

test("admin: client-side checks stop bad input before any request", async () => {
  const ctx = setup({ admin: true, view: "admin" });
  const { formProblem, linkProblem, countChars } = ctx.env.window.RankAdmin.helpers;
  assert.equal(countChars("😀字"), 2);
  const good = { text: "好", link: "", start: "2026-10-04", end: "2026-10-04" };
  assert.equal(formProblem(good), "");
  assert.equal(formProblem({ ...good, text: "  " }), "请填写一句话");
  assert.match(formProblem({ ...good, text: "字".repeat(81) }), /最多 80/);
  assert.equal(formProblem({ ...good, text: "字".repeat(80) }), "");
  assert.match(formProblem({ ...good, end: "2026-10-03" }), /结束日期不能早于开始日期/);
  for (const bad of ["javascript:alert(1)", "ftp://a.com/", "https://u:p@a.com/", "https://a.com/a b", "not a url"]) {
    assert.notEqual(linkProblem(bad), "", bad);
  }
  assert.equal(linkProblem("https://example.com/a?b=1"), "");
  const loading = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.env.respond(ctx.env.calls[0], 200, NOTICES());
  await loading;
  fillAdmin(ctx, { link: "javascript:alert(1)" });
  submitAdmin(ctx);
  await tick();
  assert.equal(ctx.env.calls.length, 1, "只有一次加载，没有发出写请求");
  assert.match(ctx.$("#rank-admin-form-status").textContent, /http/);
});

test("admin: publishing POSTs the trimmed values, reloads the list and leaves the form usable", async () => {
  const ctx = setup({ admin: true, view: "admin" });
  let loading = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.env.respond(ctx.env.calls[0], 200, NOTICES());
  await loading;
  fillAdmin(ctx, { text: "  今天复习一遍  ", link: " https://example.com/a " });
  submitAdmin(ctx);
  await tick();
  const post = ctx.env.calls[1];
  assert.equal(post.url, "/api/admin/daily-notices");
  assert.equal(post.init.method, "POST");
  assert.deepEqual(plain(JSON.parse(post.init.body)), {
    text: "今天复习一遍", link: "https://example.com/a", start_date: "2026-10-04", end_date: "2026-10-05", is_active: true,
  });
  assert.equal(ctx.$("#rank-admin-save").disabled, true);
  ctx.env.respond(post, 201, { id: 5 });
  await tick();
  ctx.env.respond(ctx.env.calls[2], 200, NOTICES([ITEM({ id: 5, text: "今天复习一遍" })]));
  await settle();
  assert.equal(ctx.$("#rank-admin-save").disabled, false, "保存完成后按钮必须恢复");
  assert.equal(ctx.$("#rank-admin-form-status").textContent, "已发布。");
  assert.equal(ctx.$("#rank-admin-list").children.length, 1);
  assert.equal(ctx.$("#rank-admin-text").value, "");
});

test("admin: editing PUTs to the item, and disable/enable flips is_active with the stored fields", async () => {
  const ctx = setup({ admin: true, view: "admin" });
  const loading = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.env.respond(ctx.env.calls[0], 200, NOTICES([ITEM()]));
  await loading;
  const buttons = ctx.$("#rank-admin-list").querySelectorAll("button");
  assert.deepEqual(buttons.map((button) => button.textContent), ["编辑", "停用"]);
  buttons[0].click();
  assert.equal(ctx.$("#rank-admin-text").value, "第一条");
  assert.equal(ctx.$("#rank-admin-save").textContent, "保存修改");
  assert.equal(ctx.$("#rank-admin-cancel").hidden, false);
  ctx.$("#rank-admin-text").value = "改过的";
  submitAdmin(ctx);
  await tick();
  const put = ctx.env.calls[1];
  assert.equal(put.url, "/api/admin/daily-notices/1");
  assert.equal(put.init.method, "PUT");
  assert.equal(JSON.parse(put.init.body).text, "改过的");
  ctx.env.respond(put, 200, { ok: true });
  await tick();
  ctx.env.respond(ctx.env.calls[2], 200, NOTICES([ITEM({ text: "改过的" })]));
  await settle();
  assert.equal(ctx.$("#rank-admin-save").textContent, "发布");
  ctx.$("#rank-admin-list").querySelectorAll("button")[1].click();
  await tick();
  const disable = ctx.env.calls[3];
  assert.deepEqual(plain(JSON.parse(disable.init.body)), {
    text: "改过的", link: "https://example.com/x", start_date: "2026-10-01", end_date: "2026-10-09", is_active: false,
  });
});

test("admin: a failed save shows the server message and re-enables the form", async () => {
  const ctx = setup({ admin: true, view: "admin" });
  const loading = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.env.respond(ctx.env.calls[0], 200, NOTICES());
  await loading;
  fillAdmin(ctx);
  submitAdmin(ctx);
  await tick();
  ctx.env.respond(ctx.env.calls[1], 403, { detail: "需要管理员权限" });
  await settle();
  assert.equal(ctx.$("#rank-admin-form-status").textContent, "需要管理员权限");
  assert.equal(ctx.$("#rank-admin-save").disabled, false);
});

test("admin: a list answer after sign-out or after leaving the admin page is dropped", async () => {
  const ctx = setup({ admin: true, view: "admin" });
  const first = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.env.window.RankAdmin.reset();
  ctx.env.respond(ctx.env.calls[0], 200, NOTICES([ITEM({ text: "上一位管理员的" })]));
  await first;
  assert.equal(ctx.$("#rank-admin-list").children.length, 0);

  const second = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.state.view = "home";
  ctx.env.document.dispatchEvent(new FakeEvent("app:view-changed", { detail: { view: "home" } }));
  ctx.env.respond(ctx.env.calls[1], 200, NOTICES([ITEM({ text: "不该出现" })]));
  await second;
  assert.equal(ctx.$("#rank-admin-list").children.length, 0);
});

test("admin: an older list answer cannot overwrite a newer one", async () => {
  const ctx = setup({ admin: true, view: "admin" });
  const first = ctx.env.window.RankAdmin.load();
  await tick();
  const second = ctx.env.window.RankAdmin.load();
  await tick();
  ctx.env.respond(ctx.env.calls[1], 200, NOTICES([ITEM({ text: "新" })]));
  await second;
  ctx.env.respond(ctx.env.calls[0], 200, NOTICES([ITEM({ text: "旧" })]));
  await first;
  assert.match(ctx.$("#rank-admin-list").children[0].textContent, /新/);
});
