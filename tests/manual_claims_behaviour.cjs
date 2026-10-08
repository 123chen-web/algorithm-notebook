"use strict";

/* 付款登记（ManualClaims / ManualClaimsAdmin）的异步行为测试：
   请求晚回来、登出再登录、换账号、关闭面板、切换状态 / 翻页之后才返回的响应一律丢弃；
   重复点击只发一次请求；明确已处理的409才刷新。Node 内置测试运行器 + tests/js_harness.cjs。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const PLANS = [
  { id: "pro", name: "专业版", price_text: "¥12.00 / 30 天" },
  { id: "max", name: "旗舰版", price_text: "¥30.00 / 90 天" },
];
const CLAIM = (id, overrides = {}) => ({
  id, plan_id: "pro", plan_name: "专业版", payer_note: "微信昵称小王", contact: "",
  status: "pending", reject_reason: null, created_at: "2026-10-05T09:30:00+00:00", decided_at: null,
  amount_cents: 1200, period_days: 30, plan_name_snapshot: "专业版",
  verified_amount_cents: null, receipt_reference: null,
  ...overrides,
});
const httpError = (status, detail) => Object.assign(new Error(detail), { status });

function fillConfirm(row, amount = "12.00", receipt = "alipay:TEST_PAYMENT_001") {
  row.querySelector(".mca-amount").value = amount;
  row.querySelector(".mca-receipt").value = receipt;
}

function setup() {
  const timers = [];
  const env = load(["manual-claims.js"], {
    extra: {
      setTimeout: (fn, ms) => { const timer = { fn, ms, cleared: false }; timers.push(timer); return timer; },
      clearTimeout: (timer) => { if (timer) timer.cleared = true; },
    },
  });
  const state = { epoch: 1, user: { id: 1, is_admin: false } };
  const calls = [];
  const hooks = {
    api: (url, init) => { const call = { url, init, ...deferred() }; calls.push(call); return call.promise; },
    getUser: () => state.user,
    getEpoch: () => state.epoch,
  };
  env.window.ManualClaims.configure(hooks);
  env.window.ManualClaimsAdmin.configure(hooks);
  const container = env.document.createElement("div");
  env.document.body.append(container);
  const submit = (selector) => container.querySelector(selector).dispatchEvent(new FakeEvent("submit"));
  return { ...env, state, calls, container, timers, submit };
}

async function mountUser(env, data = { claims: [], plans: PLANS }) {
  const mounted = env.window.ManualClaims.mount(env.container);
  await tick();
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, "/api/manual-claims");
  env.calls[0].resolve(data);
  await mounted;
  await tick();
}

async function mountAdmin(env, data = { claims: [], page: 1, pages: 1 }) {
  env.state.user = { id: 9, is_admin: true };
  const mounted = env.window.ManualClaimsAdmin.mount(env.container);
  await tick();
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, "/api/admin/manual-claims?status=pending&page=1");
  env.calls[0].resolve(data);
  await mounted;
  await tick();
}

/* ==================== 用户端：加载与渲染 ==================== */

test("user: mount asks for the list and paints plans with their price text", async () => {
  const env = setup();
  await mountUser(env);
  const select = env.container.querySelector(".mc-plan");
  assert.equal(select.children.length, 2);
  assert.equal(select.children[0].value, "pro");
  assert.match(select.children[0].textContent, /专业版（¥12\.00 \/ 30 天）/);
  assert.equal(env.container.querySelector(".mc-list-status").textContent, "还没有付款登记。");
});

test("user: pending claims render symbol plus text, never color alone", async () => {
  const env = setup();
  await mountUser(env, { claims: [CLAIM(5)], plans: PLANS });
  const row = env.container.querySelector(".mc-claim");
  assert.ok(row.className.includes("is-pending"));
  const state = row.querySelector(".mc-state");
  assert.match(state.textContent, /⏳/);
  assert.match(state.textContent, /待确认/);
  assert.equal(state.querySelector(".mc-state-symbol").getAttribute("aria-hidden"), "true");
  assert.match(row.textContent, /2026-10-05 09:30:00/);
  assert.equal(row.textContent.includes("驳回原因"), false);
});

test("user: confirmed and rejected claims show their words and the reject reason", async () => {
  const env = setup();
  await mountUser(env, {
    claims: [
      CLAIM(5, { status: "confirmed", decided_at: "2026-10-05T10:00:00+00:00" }),
      CLAIM(6, { status: "rejected", reject_reason: "账单里没查到", decided_at: "2026-10-05T11:00:00+00:00" }),
    ],
    plans: PLANS,
  });
  const rows = env.container.querySelectorAll(".mc-claim");
  assert.match(rows[0].querySelector(".mc-state").textContent, /✓已开通/);
  assert.match(rows[0].textContent, /处理时间/);
  assert.match(rows[1].querySelector(".mc-state").textContent, /✕已驳回/);
  assert.match(rows[1].textContent, /驳回原因/);
  assert.match(rows[1].textContent, /账单里没查到/);
});

test("user: an unknown status falls back to its raw text", async () => {
  const env = setup();
  await mountUser(env, { claims: [CLAIM(5, { status: "mystery" })], plans: PLANS });
  assert.match(env.container.querySelector(".mc-state").textContent, /mystery/);
});

test("user: server text stays text and never becomes elements", async () => {
  const env = setup();
  await mountUser(env, { claims: [CLAIM(5, { payer_note: "<img src=x onerror=alert(1)>" })], plans: PLANS });
  const row = env.container.querySelector(".mc-claim");
  assert.ok(row.textContent.includes("<img src=x onerror=alert(1)>"));
  assert.equal(row.querySelectorAll("img").length, 0);
});

test("user: without plans the submit stays disabled and explains why", async () => {
  const env = setup();
  await mountUser(env, { claims: [], plans: [] });
  assert.equal(env.container.querySelector(".mc-submit").disabled, true);
  assert.equal(env.container.querySelectorAll(".mc-hint")[1].hidden, false);
  env.submit(".mc-form");
  assert.equal(env.calls.length, 1, "no claim is created without plans");
});

test("user: mounting before configure asks nothing and invites login", async () => {
  const env = load(["manual-claims.js"]);
  const container = env.document.createElement("div");
  env.document.body.append(container);
  await env.window.ManualClaims.mount(container);
  await tick();
  assert.equal(env.calls.length, 0);
  assert.match(container.querySelector(".mc-list-status").textContent, /登录后可以登记付款/);
});

/* ==================== 用户端：提交 ==================== */

test("user: submit posts plan, note and trimmed contact as JSON", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-plan").value = "max";
  env.container.querySelector(".mc-note").value = "  支付宝 * 王  ";
  env.container.querySelector(".mc-contact").value = " wx-123 ";
  env.submit(".mc-form");
  await tick();
  assert.equal(env.calls.length, 2);
  const call = env.calls[1];
  assert.equal(call.url, "/api/manual-claims");
  assert.equal(call.init.method, "POST");
  assert.deepEqual(JSON.parse(call.init.body), { plan_id: "max", payer_note: "支付宝 * 王", contact: "wx-123" });
  call.resolve({ claim: CLAIM(7) });
  await tick();
  env.calls[2].resolve({ claims: [CLAIM(7)], plans: PLANS });
  await tick();
});

test("user: a numeric plan id is posted as a number (the backend validates a strict integer)", async () => {
  const env = setup();
  await mountUser(env);
  const select = env.container.querySelector(".mc-plan");
  select.children[0].value = "7";
  select.value = "7";
  env.container.querySelector(".mc-note").value = "wx";
  env.submit(".mc-form");
  await tick();
  const body = JSON.parse(env.calls[1].init.body);
  assert.strictEqual(body.plan_id, 7);
});

test("user: an empty contact is sent as an empty string", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-note").value = "微信昵称";
  env.submit(".mc-form");
  await tick();
  assert.equal(JSON.parse(env.calls[1].init.body).contact, "");
  env.calls[1].resolve({ claim: CLAIM(7) });
  await tick();
  env.calls[2].resolve({ claims: [CLAIM(7)], plans: PLANS });
  await tick();
});

test("user: empty note or overlong fields never reach the network", async () => {
  const env = setup();
  await mountUser(env);
  env.submit(".mc-form");
  assert.equal(env.calls.length, 1);
  assert.match(env.container.querySelector(".mc-status").textContent, /请填付款备注/);
  assert.equal(env.document.activeElement, env.container.querySelector(".mc-note"));
  env.container.querySelector(".mc-note").value = "好".repeat(61);
  env.submit(".mc-form");
  assert.equal(env.calls.length, 1);
  assert.match(env.container.querySelector(".mc-status").textContent, /不能超过 60 个字/);
  env.container.querySelector(".mc-note").value = "微信昵称";
  env.container.querySelector(".mc-contact").value = "长".repeat(61);
  env.submit(".mc-form");
  assert.equal(env.calls.length, 1);
  assert.match(env.container.querySelector(".mc-status").textContent, /不能超过 60 个字/);
});

test("user: repeat submits issue exactly one request", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-note").value = "微信昵称";
  env.submit(".mc-form");
  env.submit(".mc-form");
  env.submit(".mc-form");
  assert.equal(env.calls.length, 2);
  assert.equal(env.container.querySelector(".mc-submit").disabled, true);
  assert.equal(env.container.querySelector(".mc-submit").textContent, "正在登记…");
  env.calls[1].resolve({ claim: CLAIM(7) });
  await tick();
  env.calls[2].resolve({ claims: [CLAIM(7)], plans: PLANS });
  await tick();
  assert.equal(env.container.querySelector(".mc-submit").disabled, false);
});

test("user: success clears the form, tells the user and refreshes the list", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-note").value = "微信昵称";
  env.container.querySelector(".mc-contact").value = "wx";
  env.submit(".mc-form");
  env.calls[1].resolve({ claim: CLAIM(7, { payer_note: "微信昵称" }) });
  await tick();
  assert.equal(env.container.querySelector(".mc-note").value, "");
  assert.equal(env.container.querySelector(".mc-contact").value, "");
  assert.equal(env.container.querySelector(".mc-status").textContent, "已收到，站长确认后会自动开通。");
  assert.equal(env.calls.length, 3, "the list refreshes after a successful claim");
  env.calls[2].resolve({ claims: [CLAIM(7, { payer_note: "微信昵称" })], plans: PLANS });
  await tick();
  assert.equal(env.container.querySelectorAll(".mc-claim").length, 1);
});

test("user: the success hint fades on its timer and reset cancels that timer", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-note").value = "微信昵称";
  env.submit(".mc-form");
  env.calls[1].resolve({ claim: CLAIM(7) });
  await tick();
  env.calls[2].resolve({ claims: [], plans: PLANS });
  await tick();
  assert.equal(env.timers.length, 1);
  assert.equal(env.timers[0].ms, 12000);
  env.timers[0].fn();
  assert.equal(env.container.querySelector(".mc-status").textContent, "");

  env.container.querySelector(".mc-note").value = "再一次";
  env.submit(".mc-form");
  env.calls[3].resolve({ claim: CLAIM(8) });
  await tick();
  assert.equal(env.timers.length, 2);
  env.window.ManualClaims.reset();
  assert.equal(env.timers[1].cleared, true, "reset cancels the pending flash timer");
  env.timers[1].fn();
  assert.equal(env.container.querySelector(".mc-status"), null, "reset cleared the DOM");
});

test("user: 422 and 429 show the server detail and allow another try", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-note").value = "微信昵称";
  env.submit(".mc-form");
  env.calls[1].reject(httpError(422, "付款备注必填"));
  await tick();
  assert.equal(env.container.querySelector(".mc-status").textContent, "付款备注必填");
  assert.equal(env.container.querySelector(".mc-submit").disabled, false);
  env.submit(".mc-form");
  env.calls[2].reject(httpError(429, "待处理的登记已满 3 条，请等站长先确认"));
  await tick();
  assert.equal(env.container.querySelector(".mc-status").textContent, "待处理的登记已满 3 条，请等站长先确认");
});

/* ==================== 用户端：迟到响应守卫 ==================== */

test("user: a list answer that arrives after reset is discarded", async () => {
  const env = setup();
  const mounted = env.window.ManualClaims.mount(env.container);
  await tick();
  env.window.ManualClaims.reset(); // 登出
  env.calls[0].resolve({ claims: [CLAIM(5, { payer_note: "A的备注" })], plans: PLANS });
  await mounted;
  await tick();
  assert.equal(env.container.children.length, 0);

  const again = env.window.ManualClaims.mount(env.container); // 下一位用户
  await tick();
  assert.equal(env.calls.length, 2, "the new account asks for its own claims");
  env.calls[1].resolve({ claims: [CLAIM(6, { payer_note: "B的备注" })], plans: PLANS });
  await again;
  await tick();
  assert.equal(env.container.textContent.includes("A的备注"), false);
  assert.ok(env.container.textContent.includes("B的备注"));
});

test("user: an epoch bump alone discards the old answer and poisons no cache", async () => {
  const env = setup();
  const mounted = env.window.ManualClaims.mount(env.container);
  await tick();
  env.state.epoch += 1; // 应用层登出
  env.calls[0].resolve({ claims: [CLAIM(5, { payer_note: "旧账号的备注" })], plans: PLANS });
  await mounted;
  await tick();
  assert.equal(env.container.querySelectorAll(".mc-claim").length, 0);

  env.state.user = { id: 2 };
  const again = env.window.ManualClaims.mount(env.container);
  await tick();
  assert.equal(env.calls.length, 2, "the stale answer did not fill the cache");
  env.calls[1].resolve({ claims: [], plans: PLANS });
  await again;
});

test("user: switching accounts discards the previous account's answer", async () => {
  const env = setup();
  const mounted = env.window.ManualClaims.mount(env.container);
  await tick();
  env.state.user = { id: 2 }; // 同一代次，另一个人
  env.calls[0].resolve({ claims: [CLAIM(5, { payer_note: "A的备注" })], plans: PLANS });
  await mounted;
  await tick();
  assert.equal(env.container.querySelectorAll(".mc-claim").length, 0);
  env.window.ManualClaims.reset();
});

test("user: a forced refresh wins over a slower older answer", async () => {
  const env = setup();
  await mountUser(env, { claims: [CLAIM(5, { payer_note: "第一版" })], plans: PLANS });
  const slow = env.window.ManualClaims.refresh();
  const fast = env.window.ManualClaims.refresh();
  await tick();
  assert.equal(env.calls.length, 3);
  env.calls[2].resolve({ claims: [CLAIM(5, { payer_note: "新版" })], plans: PLANS });
  await fast;
  env.calls[1].resolve({ claims: [CLAIM(5, { payer_note: "旧版" })], plans: PLANS });
  await slow;
  await tick();
  assert.ok(env.container.textContent.includes("新版"));
  assert.equal(env.container.textContent.includes("旧版"), false);
});

test("user: closing the panel (container removed) discards the late answer", async () => {
  const env = setup();
  await mountUser(env, { claims: [CLAIM(5, { payer_note: "原来的备注" })], plans: PLANS });
  const refreshed = env.window.ManualClaims.refresh();
  await tick();
  env.container.remove(); // 面板关闭
  env.calls[1].resolve({ claims: [CLAIM(5, { payer_note: "晚到的备注" })], plans: PLANS });
  await refreshed;
  await tick();
  assert.ok(env.container.textContent.includes("原来的备注"));
  assert.equal(env.container.textContent.includes("晚到的备注"), false);
});

test("user: a submit answer that arrives after sign-out shows nothing and refreshes nothing", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-note").value = "微信昵称";
  env.submit(".mc-form");
  await tick();
  env.state.epoch += 1;
  env.window.ManualClaims.reset();
  env.calls[1].resolve({ claim: CLAIM(7) });
  await tick();
  assert.equal(env.calls.length, 2, "no list refresh follows a stale submit");
  assert.equal(env.container.children.length, 0);
});

test("user: a late submit failure cannot unlock a newer submit", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-note").value = "第一次";
  env.submit(".mc-form");
  env.state.epoch += 1;
  env.window.ManualClaims.reset(); // 登出清理提交锁
  const remount = env.window.ManualClaims.mount(env.container);
  await tick();
  assert.equal(env.calls.length, 3);
  env.calls[2].resolve({ claims: [], plans: PLANS });
  await remount;
  await tick();
  env.container.querySelector(".mc-note").value = "第二次";
  env.submit(".mc-form");
  await tick();
  assert.equal(env.calls.length, 4);
  env.calls[1].reject(httpError(422, "旧会话的错误")); // 旧提交晚到
  await tick();
  assert.equal(env.container.querySelector(".mc-submit").disabled, true, "the new submit is untouched");
  assert.equal(env.container.querySelector(".mc-status").textContent, "正在提交…");
  env.calls[3].resolve({ claim: CLAIM(8) });
  await tick();
  env.calls[4].resolve({ claims: [CLAIM(8)], plans: PLANS });
  await tick();
  assert.equal(env.container.querySelector(".mc-submit").disabled, false);
});

test("user: the chosen plan survives a refresh", async () => {
  const env = setup();
  await mountUser(env);
  env.container.querySelector(".mc-plan").value = "max";
  const refreshed = env.window.ManualClaims.refresh();
  await tick();
  env.calls[1].resolve({ claims: [], plans: PLANS });
  await refreshed;
  assert.equal(env.container.querySelector(".mc-plan").value, "max");
});

/* ==================== 管理员端：加载与渲染 ==================== */

test("admin: mount asks for the pending queue and paints every field", async () => {
  const env = setup();
  await mountAdmin(env, {
    claims: [{ ...CLAIM(5), username: "小陈", contact: "wx-chen" }],
    page: 1, pages: 1,
  });
  const row = env.container.querySelector(".mca-claim");
  assert.match(row.textContent, /小陈/);
  assert.match(row.textContent, /专业版/);
  assert.match(row.textContent, /微信昵称小王/);
  assert.match(row.textContent, /wx-chen/);
  assert.match(row.textContent, /2026-10-05 09:30:00/);
  assert.match(row.querySelector(".mca-state").textContent, /⏳待确认/);
  assert.equal(env.container.querySelector(".mca-page-info").textContent, "第 1 / 1 页");
});

test("admin: an empty queue says so in words", async () => {
  const env = setup();
  await mountAdmin(env);
  assert.equal(env.container.querySelector(".mca-status").textContent, "没有待确认的付款登记。");
});

test("admin: 403 shows the server detail as an error", async () => {
  const env = setup();
  env.state.user = { id: 9, is_admin: true };
  const mounted = env.window.ManualClaimsAdmin.mount(env.container);
  await tick();
  env.calls[0].reject(httpError(403, "需要管理员权限"));
  await mounted;
  await tick();
  assert.equal(env.container.querySelector(".mca-status").textContent, "需要管理员权限");
  assert.ok(env.container.querySelector(".mca-status").className.includes("error"));
});

test("admin: decided claims render their status instead of action buttons", async () => {
  const env = setup();
  await mountAdmin(env, {
    claims: [
      { ...CLAIM(5, { status: "confirmed", decided_at: "2026-10-05T10:00:00+00:00" }), username: "小陈" },
      { ...CLAIM(6, { status: "rejected", reject_reason: "金额不对" }), username: "小李" },
    ],
    page: 1, pages: 1,
  });
  const rows = env.container.querySelectorAll(".mca-claim");
  assert.equal(rows[0].querySelector(".mca-actions"), null);
  assert.match(rows[0].querySelector(".mca-state").textContent, /✓已开通/);
  assert.match(rows[1].querySelector(".mca-state").textContent, /✕已驳回/);
  assert.match(rows[1].textContent, /金额不对/);
});

test("admin: a hostile username stays plain text", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "<img src=x onerror=alert(1)>" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  assert.ok(row.textContent.includes("<img src=x"));
  assert.equal(row.querySelectorAll("img").length, 0);
});

/* ==================== 管理员端：确认收款 ==================== */

test("admin: confirming asks the double-check question first", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  assert.equal(row.querySelector(".mca-confirm").hidden, true);
  row.querySelector(".mca-actions button").click();
  const panel = row.querySelector(".mca-confirm");
  assert.equal(panel.hidden, false);
  assert.match(panel.textContent, /确认已在微信\/支付宝账单里核对到这笔款项？/);
  assert.equal(env.calls.length, 1, "the first click sends nothing");
  assert.equal(env.document.activeElement, panel.querySelector(".mca-confirm-buttons button"));
});

test("admin: cancelling the double-check closes it and refocuses the opener", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  const opener = row.querySelector(".mca-actions button");
  opener.click();
  row.querySelectorAll(".mca-confirm-buttons button")[1].click();
  assert.equal(row.querySelector(".mca-confirm").hidden, true);
  assert.equal(env.document.activeElement, opener);
  assert.equal(env.calls.length, 1);
});

test("admin: confirm posts once, locks the row and refreshes with a success note", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelector(".mca-actions button").click();
  const yes = row.querySelector(".mca-confirm-buttons button");
  fillConfirm(row);
  yes.click();
  yes.click(); // 防重复点击
  await tick();
  assert.equal(env.calls.length, 2);
  assert.equal(env.calls[1].url, "/api/admin/manual-claims/5/confirm");
  assert.equal(env.calls[1].init.method, "POST");
  assert.deepEqual(JSON.parse(env.calls[1].init.body), {
    verified_amount_cents: 1200, receipt_reference: "alipay:TEST_PAYMENT_001",
  });
  assert.equal(row.querySelectorAll("button").every((item) => item.disabled), true);
  env.calls[1].resolve({ claim: CLAIM(5, { status: "confirmed" }) });
  await tick();
  assert.equal(env.calls.length, 3, "the queue refreshes after confirming");
  env.calls[2].resolve({ claims: [], page: 1, pages: 1 });
  await tick();
  assert.equal(env.container.querySelector(".mca-status").textContent, "已确认收款，套餐会自动开通。");
});

test("admin: a 409 confirm says '已被处理' and refreshes the queue", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelector(".mca-actions button").click();
  fillConfirm(row);
  row.querySelector(".mca-confirm-buttons button").click();
  await tick();
  env.calls[1].reject(httpError(409, "这条登记已经处理过了"));
  await tick();
  assert.equal(env.calls.length, 3, "409 triggers a refresh");
  env.calls[2].resolve({ claims: [], page: 1, pages: 1 });
  await tick();
  assert.equal(env.container.querySelector(".mca-status").textContent, "这条登记已被处理，列表已刷新。");
});

test("admin: other confirm failures re-enable the row without a refresh", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelector(".mca-actions button").click();
  fillConfirm(row);
  row.querySelector(".mca-confirm-buttons button").click();
  await tick();
  env.calls[1].reject(httpError(500, "服务器开小差"));
  await tick();
  assert.equal(env.calls.length, 2, "no refresh on a plain failure");
  assert.equal(env.container.querySelector(".mca-status").textContent, "服务器开小差");
  assert.equal(row.querySelectorAll("button").every((item) => item.disabled), false);
});

/* ==================== 管理员端：驳回 ==================== */

test("admin: rejecting needs a reason and refuses empty or overlong ones", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelectorAll(".mca-actions button")[1].click();
  const panel = row.querySelector(".mca-reject-form");
  assert.equal(panel.hidden, false);
  const reason = panel.querySelector(".mca-reason");
  assert.equal(env.document.activeElement, reason);
  panel.querySelector(".mca-reject-buttons button").click();
  assert.equal(env.calls.length, 1, "empty reason never leaves the browser");
  assert.match(panel.querySelector(".mca-reject-error").textContent, /请填驳回原因/);
  reason.value = "长".repeat(81);
  panel.querySelector(".mca-reject-buttons button").click();
  assert.equal(env.calls.length, 1);
  assert.match(panel.querySelector(".mca-reject-error").textContent, /不能超过 80 个字/);
});

test("admin: reject posts the reason and refreshes with a note", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelectorAll(".mca-actions button")[1].click();
  const panel = row.querySelector(".mca-reject-form");
  panel.querySelector(".mca-reason").value = "  账单里没查到这笔  ";
  panel.querySelector(".mca-reject-buttons button").click();
  await tick();
  assert.equal(env.calls.length, 2);
  assert.equal(env.calls[1].url, "/api/admin/manual-claims/5/reject");
  assert.deepEqual(JSON.parse(env.calls[1].init.body), { reason: "账单里没查到这笔" });
  env.calls[1].resolve({ claim: CLAIM(5, { status: "rejected" }) });
  await tick();
  env.calls[2].resolve({ claims: [], page: 1, pages: 1 });
  await tick();
  assert.equal(env.container.querySelector(".mca-status").textContent, "已驳回。");
});

test("admin: a 409 reject says '已被处理' and refreshes", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelectorAll(".mca-actions button")[1].click();
  row.querySelector(".mca-reason").value = "重复登记";
  row.querySelector(".mca-reject-buttons button").click();
  await tick();
  env.calls[1].reject(httpError(409, "这条登记已经处理过了"));
  await tick();
  env.calls[2].resolve({ claims: [], page: 1, pages: 1 });
  await tick();
  assert.equal(env.container.querySelector(".mca-status").textContent, "这条登记已被处理，列表已刷新。");
});

test("admin: opening the reject form closes the confirm box and vice versa", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  const [openConfirm, openReject] = row.querySelectorAll(".mca-actions button");
  openConfirm.click();
  assert.equal(row.querySelector(".mca-confirm").hidden, false);
  openReject.click();
  assert.equal(row.querySelector(".mca-confirm").hidden, true);
  assert.equal(row.querySelector(".mca-reject-form").hidden, false);
  openConfirm.click();
  assert.equal(row.querySelector(".mca-reject-form").hidden, true);
});

/* ==================== 管理员端：筛选与分页 ==================== */

test("admin: switching filters asks for page 1 of the new status and marks it pressed", async () => {
  const env = setup();
  await mountAdmin(env);
  const rejected = env.container.querySelectorAll(".mca-filters button")[2];
  rejected.click();
  await tick();
  assert.equal(env.calls.length, 2);
  assert.equal(env.calls[1].url, "/api/admin/manual-claims?status=rejected&page=1");
  assert.equal(rejected.getAttribute("aria-pressed"), "true");
  assert.equal(env.container.querySelectorAll(".mca-filters button")[0].getAttribute("aria-pressed"), "false");
  env.calls[1].resolve({ claims: [], page: 1, pages: 1 });
  await tick();
  assert.equal(env.container.querySelector(".mca-status").textContent, "这个状态下没有付款登记。");
});

test("admin: a late answer from the previous filter cannot overwrite the new one", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "待确认的人" }], page: 1, pages: 1 });
  env.container.querySelectorAll(".mca-filters button")[3].click(); // 全部
  await tick();
  env.container.querySelectorAll(".mca-filters button")[2].click(); // 又切到已驳回
  await tick();
  assert.equal(env.calls[2].url, "/api/admin/manual-claims?status=rejected&page=1");
  env.calls[2].resolve({ claims: [{ ...CLAIM(7, { status: "rejected" }), username: "已驳回的人" }], page: 1, pages: 1 });
  await tick();
  env.calls[1].resolve({ claims: [{ ...CLAIM(6, { status: "confirmed" }), username: "全部里的人" }], page: 1, pages: 1 }); // 晚到
  await tick();
  assert.ok(env.container.textContent.includes("已驳回的人"));
  assert.equal(env.container.textContent.includes("全部里的人"), false);
});

test("admin: pagination walks pages and disables the edges", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "第一页" }], page: 1, pages: 3 });
  assert.equal(env.container.querySelector(".mca-pages button").disabled, true, "prev disabled on page 1");
  const next = env.container.querySelectorAll(".mca-pages button")[1];
  assert.equal(next.disabled, false);
  next.click();
  await tick();
  assert.equal(env.calls[1].url, "/api/admin/manual-claims?status=pending&page=2");
  env.calls[1].resolve({ claims: [{ ...CLAIM(6), username: "第二页" }], page: 2, pages: 3 });
  await tick();
  assert.equal(env.container.querySelector(".mca-page-info").textContent, "第 2 / 3 页");
  env.container.querySelectorAll(".mca-pages button")[1].click();
  await tick();
  env.calls[2].resolve({ claims: [{ ...CLAIM(7), username: "第三页" }], page: 3, pages: 3 });
  await tick();
  assert.equal(env.container.querySelectorAll(".mca-pages button")[1].disabled, true, "next disabled on the last page");
  assert.equal(env.container.querySelector(".mca-pages button").disabled, false);
});

test("admin: a late answer from an older page is discarded", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "第一页" }], page: 1, pages: 2 });
  env.container.querySelectorAll(".mca-pages button")[1].click(); // 去第 2 页
  await tick();
  env.container.querySelector(".mca-pages button").click(); // 又回到第 1 页
  await tick();
  assert.equal(env.calls[2].url, "/api/admin/manual-claims?status=pending&page=1");
  env.calls[2].resolve({ claims: [{ ...CLAIM(7), username: "第一页新" }], page: 1, pages: 2 });
  await tick();
  env.calls[1].resolve({ claims: [{ ...CLAIM(6), username: "第二页旧" }], page: 2, pages: 2 }); // 晚到
  await tick();
  assert.ok(env.container.textContent.includes("第一页新"));
  assert.equal(env.container.textContent.includes("第二页旧"), false);
  assert.equal(env.container.querySelector(".mca-page-info").textContent, "第 1 / 2 页");
});

/* ==================== 管理员端：迟到响应守卫 ==================== */

test("admin: a queue answer that arrives after reset is discarded", async () => {
  const env = setup();
  env.state.user = { id: 9, is_admin: true };
  const mounted = env.window.ManualClaimsAdmin.mount(env.container);
  await tick();
  env.window.ManualClaimsAdmin.reset(); // 登出
  env.calls[0].resolve({ claims: [{ ...CLAIM(5), username: "旧管理员的队列" }], page: 1, pages: 1 });
  await mounted;
  await tick();
  assert.equal(env.container.children.length, 0);
  assert.equal(env.container.textContent.includes("旧管理员的队列"), false);
});

test("admin: an epoch bump discards the queue answer", async () => {
  const env = setup();
  env.state.user = { id: 9, is_admin: true };
  const mounted = env.window.ManualClaimsAdmin.mount(env.container);
  await tick();
  env.state.epoch += 1;
  env.calls[0].resolve({ claims: [{ ...CLAIM(5), username: "旧会话的队列" }], page: 1, pages: 1 });
  await mounted;
  await tick();
  assert.equal(env.container.textContent.includes("旧会话的队列"), false);
  env.window.ManualClaimsAdmin.reset();
});

test("admin: closing the panel discards a late queue answer", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "原来的队列" }], page: 1, pages: 1 });
  const refreshed = env.window.ManualClaimsAdmin.refresh();
  await tick();
  env.container.remove();
  env.calls[1].resolve({ claims: [{ ...CLAIM(6), username: "晚到的队列" }], page: 1, pages: 1 });
  await refreshed;
  await tick();
  assert.ok(env.container.textContent.includes("原来的队列"));
  assert.equal(env.container.textContent.includes("晚到的队列"), false);
});

test("admin: an action answer that arrives after sign-out does not refresh or unlock", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [{ ...CLAIM(5), username: "小陈" }], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelector(".mca-actions button").click();
  fillConfirm(row);
  row.querySelector(".mca-confirm-buttons button").click();
  await tick();
  env.state.epoch += 1;
  env.window.ManualClaimsAdmin.reset();
  env.calls[1].resolve({ claim: CLAIM(5, { status: "confirmed" }) });
  await tick();
  assert.equal(env.calls.length, 2, "no refresh follows a stale action");
  assert.equal(env.container.children.length, 0);
});

test("admin: two rows can be processed one after another without poisoning each other", async () => {
  const env = setup();
  await mountAdmin(env, {
    claims: [{ ...CLAIM(5), username: "甲" }, { ...CLAIM(6), username: "乙" }],
    page: 1, pages: 1,
  });
  const first = env.container.querySelector('[data-claim-id="5"]');
  first.querySelector(".mca-actions button").click();
  fillConfirm(first);
  first.querySelector(".mca-confirm-buttons button").click();
  await tick();
  env.calls[1].resolve({ claim: CLAIM(5, { status: "confirmed" }) });
  await tick();
  env.calls[2].resolve({ claims: [{ ...CLAIM(6), username: "乙" }], page: 1, pages: 1 });
  await tick();
  const second = env.container.querySelector('[data-claim-id="6"]');
  assert.ok(second, "the refreshed queue still shows the other claim");
  second.querySelector(".mca-actions button").click();
  fillConfirm(second, "12.00", "wechat:TEST_PAYMENT_002");
  second.querySelector(".mca-confirm-buttons button").click();
  await tick();
  assert.equal(env.calls[3].url, "/api/admin/manual-claims/6/confirm");
  env.calls[3].resolve({ claim: CLAIM(6, { status: "confirmed" }) });
  await tick();
  env.calls[4].resolve({ claims: [], page: 1, pages: 1 });
  await tick();
  assert.equal(env.container.querySelector(".mca-status").textContent, "已确认收款，套餐会自动开通。");
});

test("admin: refresh before mount is a harmless no-op and reset clears everything", async () => {
  const env = setup();
  assert.equal(await env.window.ManualClaimsAdmin.refresh(), false);
  assert.equal(await env.window.ManualClaims.refresh(), false);
  await mountAdmin(env);
  env.window.ManualClaimsAdmin.reset();
  assert.equal(env.container.children.length, 0);
  assert.equal(env.calls.length, 1);
});

test("user: registration price, period and name remain frozen after plan changes", async () => {
  const env = setup();
  await mountUser(env, { claims: [CLAIM(5, { plan_name: "改名后的套餐", amount_cents: 990,
    period_days: 45, plan_name_snapshot: "原套餐" })], plans: [{ ...PLANS[0], price_text: "¥99.00 / 90 天" }] });
  const row = env.container.querySelector(".mc-claim");
  assert.match(row.textContent, /原套餐/);
  assert.match(row.textContent, /¥9\.90/);
  assert.match(row.textContent, /45 天/);
  assert.equal(row.textContent.includes("¥99.00"), false);
  assert.equal(row.textContent.includes("改名后的套餐"), false);
});

test("admin: frozen receipt form is blank and cannot submit without actual account details", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [CLAIM(5)], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  row.querySelector(".mca-actions button").click();
  assert.equal(row.querySelector(".mca-amount").value, "");
  assert.equal(row.querySelector(".mca-receipt").value, "");
  assert.equal(row.querySelector(".mca-legacy-days"), null);
  row.querySelector(".mca-confirm-buttons button").click();
  assert.equal(env.calls.length, 1);
  assert.match(row.querySelector(".mca-confirm-error").textContent, /到账金额/);
});

for (const amount of ["", "0", "0.00", "-12", "+12", "12e0", "12.001", "12,00", "01.00",
  ".50", "十二", "Infinity", "90071992547409.92"]) {
  test(`admin: invalid actual amount ${JSON.stringify(amount)} never reaches the server`, async () => {
    const env = setup();
    await mountAdmin(env, { claims: [CLAIM(5)], page: 1, pages: 1 });
    const row = env.container.querySelector(".mca-claim");
    fillConfirm(row, amount);
    row.querySelector(".mca-confirm-buttons button").click();
    assert.equal(env.calls.length, 1);
    assert.match(row.querySelector(".mca-confirm-error").textContent, /到账金额/);
  });
}

test("admin: valid cents are exact and a changed current plan cannot change the receipt snapshot", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [CLAIM(5, { amount_cents: 990, plan_name: "当前不同套餐",
    plan_name_snapshot: "登记套餐" })], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  assert.match(row.textContent, /登记套餐/);
  assert.match(row.textContent, /¥9\.90/);
  fillConfirm(row, "9.89");
  row.querySelector(".mca-confirm-buttons button").click();
  assert.equal(env.calls.length, 1);
  assert.match(row.querySelector(".mca-confirm-error").textContent, /登记金额一致/);
  fillConfirm(row, " 9.90 ", " wechat:FULL_RECEIPT_77 ");
  row.querySelector(".mca-confirm-buttons button").click();
  assert.deepEqual(JSON.parse(env.calls[1].init.body), { verified_amount_cents: 990,
    receipt_reference: "wechat:FULL_RECEIPT_77" });
});

for (const receipt of ["", "昵称小王", "alipay:", "paypal:123", "wechat:有汉字", "alipay:has space",
  "alipay:bad\nreceipt", "\nalipay:123", "alipay:123\n", "\talipay:123", "alipay:123\t",
  "\u00a0alipay:123", "alipay:" + "x".repeat(121)]) {
  test(`admin: incomplete or invalid receipt ${JSON.stringify(receipt)} never reaches the server`, async () => {
    const env = setup();
    await mountAdmin(env, { claims: [CLAIM(5)], page: 1, pages: 1 });
    const row = env.container.querySelector(".mca-claim");
    fillConfirm(row, "12.00", receipt);
    row.querySelector(".mca-confirm-buttons button").click();
    assert.equal(env.calls.length, 1);
    assert.match(row.querySelector(".mca-confirm-error").textContent, /完整到账流水号/);
  });
}

test("admin: legacy unknown snapshots need explicit review and manually entered days", async () => {
  const env = setup();
  const legacy = CLAIM(5, { amount_cents: null, period_days: null, plan_name_snapshot: null });
  await mountAdmin(env, { claims: [legacy], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  assert.match(row.textContent, /原套餐名称、金额与周期未知/);
  assert.match(row.textContent, /当前套餐价格不能代替历史金额/);
  assert.match(row.querySelectorAll(".mca-actions button")[1].getAttribute("aria-label"), /历史登记（套餐信息未知）/);
  assert.equal(row.querySelector(".mca-legacy-days").value, "");
  fillConfirm(row, "8.88");
  const yes = row.querySelector(".mca-confirm-buttons button");
  yes.click();
  assert.equal(env.calls.length, 1);
  assert.match(row.querySelector(".mca-confirm-error").textContent, /人工核查历史账单/);
  row.querySelector(".mca-legacy-reviewed").checked = true;
  for (const days of ["", "0", "3651", "30.5", "30e0", "-30"]) {
    row.querySelector(".mca-legacy-days").value = days;
    yes.click();
    assert.equal(env.calls.length, 1);
    assert.match(row.querySelector(".mca-confirm-error").textContent, /1–3650/);
  }
  row.querySelector(".mca-legacy-days").value = "45";
  yes.click();
  assert.deepEqual(JSON.parse(env.calls[1].init.body), { verified_amount_cents: 888,
    receipt_reference: "alipay:TEST_PAYMENT_001", legacy_reviewed: true, legacy_period_days: 45 });
});

test("admin: a partial snapshot cannot be treated as a legacy unknown record", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [CLAIM(5, { period_days: null })], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  assert.equal(row.querySelector(".mca-legacy-days"), null);
  fillConfirm(row);
  row.querySelector(".mca-confirm-buttons button").click();
  assert.equal(env.calls.length, 1);
  assert.match(row.querySelector(".mca-confirm-error").textContent, /信息不完整/);
});

for (const detail of ["实际到账金额与登记金额不符", "这笔流水已经用于其他登记", "该用户已被禁用", "未知冲突"]) {
  test(`admin: pending 409 ${detail} preserves the form and never pretends it was handled`, async () => {
    const env = setup();
    await mountAdmin(env, { claims: [CLAIM(5)], page: 1, pages: 1 });
    const row = env.container.querySelector(".mca-claim");
    row.querySelector(".mca-actions button").click();
    fillConfirm(row);
    row.querySelector(".mca-confirm-buttons button").click();
    env.calls[1].reject(httpError(409, detail));
    await tick();
    assert.equal(env.calls.length, 2, "no destructive refresh for a still pending conflict");
    assert.equal(env.container.querySelector(".mca-status").textContent, detail);
    assert.equal(row.querySelector(".mca-amount").value, "12.00");
    assert.equal(row.querySelector(".mca-receipt").value, "alipay:TEST_PAYMENT_001");
    assert.equal(row.querySelector(".mca-confirm").hidden, false);
    assert.equal(row.querySelectorAll("button").every((item) => item.disabled), false);
  });
}

test("admin: a late pending 409 cannot modify a newer session", async () => {
  const env = setup();
  await mountAdmin(env, { claims: [CLAIM(5)], page: 1, pages: 1 });
  const row = env.container.querySelector(".mca-claim");
  fillConfirm(row);
  row.querySelector(".mca-confirm-buttons button").click();
  env.state.epoch += 1;
  env.window.ManualClaimsAdmin.reset();
  env.calls[1].reject(httpError(409, "该用户已被禁用"));
  await tick();
  assert.equal(env.calls.length, 2);
  assert.equal(env.container.children.length, 0);
});
