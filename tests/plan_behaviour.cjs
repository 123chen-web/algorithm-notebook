"use strict";

/* 套餐页视图（static/plan.js）：model 的边界、卡片与按钮分支、状态区、订单表、滚动。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, deferred, tick } = require("./js_harness.cjs");

const NOW = Date.parse("2026-10-04T12:00:00Z");
const DAY = 86400000;
const plain = (value) => JSON.parse(JSON.stringify(value));
const PLAN_A = { id: 1, name: "月度", price_cents: 990, period_days: 30, ai_daily_limit: 50, purchasable: 1 };
const PLAN_B = { id: 2, name: "季度", price_cents: 2490, period_days: 90, ai_daily_limit: 45, purchasable: 0 };
const FREE_ME = { is_trial: false, plan_active: false, plan_id: null, plan_expires_at: null, ai_daily_limit: 20, ai_daily_used: 3, ai_daily_remaining: 17, timezone: "UTC" };
const activeMe = (days, extra = {}) => ({
  ...FREE_ME, plan_active: true, plan_id: 1, plan_name: "月度", plan_expires_at: new Date(NOW + days * DAY - 1000).toISOString(),
  ai_daily_limit: 50, ai_daily_used: 4, ai_daily_remaining: 46, ...extra,
});

function env(files = ["plan.js"], extra = {}) {
  const loaded = load(files, { extra });
  return { ...loaded, View: loaded.window.PlanView };
}
const texts = (node) => node.textContent;
const find = (root, selector) => root.querySelector(selector);

test("formatPrice: cents become two-decimal yuan, bad input is safe", () => {
  const { View } = env();
  assert.equal(View.formatPrice(990), "¥9.90");
  assert.equal(View.formatPrice(0), "¥0.00");
  assert.equal(View.formatPrice(5), "¥0.05");
  assert.equal(View.formatPrice(100000), "¥1000.00");
  assert.equal(View.formatPrice(undefined), "¥0.00");
  assert.equal(View.formatPrice("abc"), "¥0.00");
});

test("daysLeft: rounds up, expired/invalid is 0", () => {
  const { View } = env();
  assert.equal(View.daysLeft(new Date(NOW + 29 * DAY - 1000).toISOString(), NOW), 29);
  assert.equal(View.daysLeft(new Date(NOW + 29 * DAY + 1000).toISOString(), NOW), 30);
  assert.equal(View.daysLeft(new Date(NOW + 1).toISOString(), NOW), 1);
  assert.equal(View.daysLeft(new Date(NOW).toISOString(), NOW), 0);
  assert.equal(View.daysLeft(new Date(NOW - DAY).toISOString(), NOW), 0);
  assert.equal(View.daysLeft("not a date", NOW), 0);
  assert.equal(View.daysLeft(null, NOW), 0);
});

test("model: free account has free card current, usage ratio and no urgency", () => {
  const { View } = env();
  const m = plain(View.model([PLAN_A], FREE_ME, NOW));
  assert.equal(m.freeLimit, 20);
  assert.deepEqual(m.status, { active: false, daysLeft: 0, expiresAt: null, urgent: false });
  assert.deepEqual(m.usage, { used: 3, limit: 20, remaining: 17, ratio: 0.15, exhausted: false });
  assert.equal(m.cards[0].id, "free");
  assert.equal(m.cards[0].priceText, "¥0 · 永久");
  assert.equal(m.cards[0].current, true);
  assert.equal(m.cards[1].priceText, "¥9.90 / 30 天");
  assert.equal(m.cards[1].perDayText, "每天 50 次 AI 生成");
  assert.equal(m.cards[1].multiplierText, "是免费版的 2.5 倍");
  assert.equal(m.cards[1].periodText, "30 天有效，到期前续费会顺延");
});

test("model: standard and advanced quotas yield 2.5 and 6 times the free allowance", () => {
  const { View } = env();
  const m = plain(View.model([
    { ...PLAN_A, name: "标准版" },
    { ...PLAN_B, name: "进阶版", period_days: 30, price_cents: 1990, ai_daily_limit: 120 },
  ], FREE_ME, NOW));
  assert.equal(m.cards[1].perDayText, "每天 50 次 AI 生成");
  assert.equal(m.cards[1].multiplierText, "是免费版的 2.5 倍");
  assert.equal(m.cards[2].perDayText, "每天 120 次 AI 生成");
  assert.equal(m.cards[2].multiplierText, "是免费版的 6 倍");
  assert.equal(m.cards[2].priceText, "¥19.90 / 30 天");
});

test("model: active plan — days left, urgent at <=3 days, current marker, free limit falls back to 20", () => {
  const { View } = env();
  const m = plain(View.model([PLAN_A, PLAN_B], activeMe(29), NOW));
  assert.equal(m.status.active, true);
  assert.equal(m.status.daysLeft, 29);
  assert.equal(m.status.urgent, false);
  assert.equal(m.freeLimit, 20, "active plan limit is not the free allowance");
  assert.equal(m.cards[0].current, false);
  assert.equal(m.cards[1].current, true);
  assert.equal(m.cards[2].current, false);
  assert.equal(plain(View.model([PLAN_A], activeMe(3), NOW)).status.urgent, true);
  assert.equal(plain(View.model([PLAN_A], activeMe(4), NOW)).status.urgent, false);
});

test("model: expired plan (expires <= now) counts as no plan even if plan_active is stale", () => {
  const { View } = env();
  const expired = { ...activeMe(1), plan_expires_at: new Date(NOW).toISOString() };
  const m = plain(View.model([PLAN_A], expired, NOW));
  assert.equal(m.status.active, false);
  assert.equal(m.cards[0].current, true);
  assert.equal(m.cards[1].current, false);
  const flagOff = plain(View.model([PLAN_A], { ...activeMe(10), plan_active: false }, NOW));
  assert.equal(flagOff.status.active, false);
});

test("model: free limit follows the user's allowance, non-integer multiplier keeps one decimal", () => {
  const { View } = env();
  const m = plain(View.model([{ ...PLAN_A, ai_daily_limit: 50 }], { ...FREE_ME, ai_daily_limit: 40, ai_daily_used: 0, ai_daily_remaining: 40 }, NOW));
  assert.equal(m.freeLimit, 40);
  assert.equal(m.cards[0].perDayText, "每天 40 次 AI 生成");
  assert.equal(m.cards[1].multiplierText, "是免费版的 1.3 倍");
  const same = plain(View.model([{ ...PLAN_A, ai_daily_limit: 20 }], FREE_ME, NOW));
  assert.equal(same.cards[1].multiplierText, "", "no multiplier when not larger than free");
});

test("model: ai_daily_limit 0, trial and missing data do not crash or divide by zero", () => {
  const { View } = env();
  const zero = plain(View.model([PLAN_A], { ...FREE_ME, ai_daily_limit: 0, ai_daily_used: 0, ai_daily_remaining: 0 }, NOW));
  assert.equal(zero.freeLimit, 20);
  assert.equal(zero.usage.ratio, 1);
  assert.equal(zero.usage.exhausted, true);
  const trial = plain(View.model([PLAN_A], { ...FREE_ME, is_trial: true, ai_daily_limit: 4, ai_daily_used: 1, ai_daily_remaining: 3 }, NOW));
  assert.equal(trial.freeLimit, 20);
  const empty = plain(View.model(undefined, undefined, NOW));
  assert.equal(empty.freeLimit, 20);
  assert.equal(empty.cards.length, 1);
  assert.equal(empty.cards[0].id, "free");
  assert.equal(plain(View.model([], FREE_ME, NOW)).cards.length, 1);
});

test("model: used above limit clamps ratio to 1 and marks exhausted", () => {
  const { View } = env();
  const m = plain(View.model([], { ...FREE_ME, ai_daily_used: 22, ai_daily_remaining: 0 }, NOW));
  assert.equal(m.usage.ratio, 1);
  assert.equal(m.usage.exhausted, true);
});

test("model: recommendation goes to the cheapest paid plan, never with a single paid plan", () => {
  const { View } = env();
  const two = plain(View.model([PLAN_B, PLAN_A], FREE_ME, NOW));
  assert.deepEqual(two.cards.map((card) => card.recommended), [false, false, true]);
  const one = plain(View.model([PLAN_A], FREE_ME, NOW));
  assert.equal(one.cards.some((card) => card.recommended), false);
  const withZero = plain(View.model([{ ...PLAN_A, id: 5, price_cents: 0 }, PLAN_B], FREE_ME, NOW));
  assert.deepEqual(withZero.cards.map((card) => card.recommended), [false, false, true], "a 0-yuan plan is never the recommended one");
});

test("model: purchasable flag is boolean per plan, free card is never purchasable", () => {
  const { View } = env();
  const m = plain(View.model([PLAN_A, PLAN_B], FREE_ME, NOW));
  assert.deepEqual(m.cards.map((card) => card.purchasable), [false, true, false]);
});

function renderCards(plans, me, options = {}) {
  const e = env();
  const host = e.document.createElement("div");
  const calls = { buy: [], how: 0 };
  e.View.renderCards(host, e.View.model(plans, me, NOW), plans, {
    isTrial: Boolean(me.is_trial),
    onBuy: (plan) => calls.buy.push(plan.id),
    onHow: () => { calls.how += 1; },
    ...options,
  });
  return { ...e, host, calls };
}

test("cards: free card first with fixed content, paid cards carry real benefits and refund text", () => {
  const { host } = renderCards([PLAN_A, PLAN_B], FREE_ME);
  const cards = host.querySelectorAll(".pl-card");
  assert.equal(cards.length, 3);
  assert.match(texts(cards[0]), /免费版/);
  assert.match(texts(cards[0]), /¥0 · 永久/);
  assert.match(texts(cards[0]), /每天 20 次 AI 生成/);
  assert.match(texts(cards[0]), /全部复习、统计、小组、讨论区功能/);
  assert.equal(find(cards[0], "button"), null, "free card has no button");
  assert.match(texts(cards[1]), /每天 50 次 AI 生成（是免费版的 2.5 倍）/);
  assert.match(texts(cards[1]), /其余功能与免费版相同/);
  assert.match(texts(cards[1]), /30 天有效，到期前续费会顺延/);
  assert.match(texts(cards[1]), /官方支付的订单可在“我的订单”自助全额退款（当天已用的 AI 次数不退）；手动付款请联系站长/);
  assert.match(texts(cards[1]), /推荐/);
  assert.doesNotMatch(texts(cards[2]), /推荐/);
});

test("cards: recommended card is marked featured; single paid plan is not", () => {
  assert.equal(renderCards([PLAN_A, PLAN_B], FREE_ME).host.querySelectorAll(".pl-card-featured").length, 1);
  assert.equal(renderCards([PLAN_A], FREE_ME).host.querySelectorAll(".pl-card-featured").length, 0);
});

test("cards: purchasable plan buys, non-purchasable plan says 开通方式 and scrolls instead", () => {
  const { host, calls } = renderCards([PLAN_A, PLAN_B], FREE_ME);
  const [, a, b] = host.querySelectorAll(".pl-card");
  const buy = find(a, "button");
  const how = find(b, "button");
  assert.equal(buy.textContent, "购买");
  assert.equal(buy.getAttribute("aria-label"), "购买月度");
  assert.equal(how.textContent, "开通方式");
  buy.click();
  how.click();
  assert.deepEqual(calls.buy, [1]);
  assert.equal(calls.how, 1);
});

test("cards: trial account sees read-only cards without any button", () => {
  const { host } = renderCards([PLAN_A, PLAN_B], { ...FREE_ME, is_trial: true });
  assert.equal(host.querySelectorAll("button").length, 0);
});

test("cards: the plan in use is marked 当前套餐, the free card 当前使用 only without a plan", () => {
  const active = renderCards([PLAN_A, PLAN_B], activeMe(10)).host.querySelectorAll(".pl-card");
  assert.match(texts(active[1]), /当前套餐/);
  assert.doesNotMatch(texts(active[0]), /当前使用/);
  assert.doesNotMatch(texts(active[2]), /当前套餐/);
  const free = renderCards([PLAN_A], FREE_ME).host.querySelectorAll(".pl-card");
  assert.match(texts(free[0]), /当前使用/);
});

test("cards: plan names are rendered as text, never as markup", () => {
  const evil = { ...PLAN_A, name: "<img src=x onerror=alert(1)>" };
  const { host } = renderCards([evil], FREE_ME);
  assert.equal(host.querySelectorAll("img").length, 0);
  assert.match(texts(host), /<img src=x onerror=alert\(1\)>/);
});

function renderStatus(me, plans = [PLAN_A]) {
  const e = env();
  const host = e.document.createElement("div");
  e.View.renderStatus(host, e.View.model(plans, me, NOW), me, plans);
  return { ...e, host };
}

test("status: free account shows 免费版 and the usage line, no renew hint", () => {
  const { host } = renderStatus(FREE_ME);
  assert.match(texts(host), /免费版/);
  assert.match(texts(host), /今日 AI 3 \/ 20 次 · 午夜重置/);
  assert.equal(find(host, ".pl-usage-warning"), null);
  assert.equal(find(host, ".pl-renew-hint"), null);
  const bar = find(host, ".pl-meter");
  assert.equal(bar.getAttribute("aria-valuenow"), "3");
  assert.equal(bar.getAttribute("aria-valuemax"), "20");
});

test("status: exhausted quota shows the warning and marks the bar", () => {
  const { host } = renderStatus({ ...FREE_ME, ai_daily_used: 20, ai_daily_remaining: 0 });
  assert.match(texts(host), /今天的次数用完了，明天零点恢复/);
  assert.equal(find(host, ".pl-usage").dataset.exhausted, "true");
});

test("status: active plan shows pill, expiry date, days left and the carry-over note", () => {
  const { host } = renderStatus(activeMe(29));
  assert.match(texts(host), /月度/);
  assert.match(texts(host), /到期 \d{4}-\d{2}-\d{2} · 还剩 29 天/);
  assert.match(texts(host), /提前续费会顺延，不浪费剩余天数/);
  assert.equal(find(host, ".pl-renew-hint"), null);
});

test("status: three days or fewer flips to the urgent style and shows the renew hint", () => {
  const { host } = renderStatus(activeMe(2));
  assert.equal(find(host, ".pl-expiry").dataset.urgent, "true");
  assert.match(texts(host), /续费/);
  assert.match(texts(host), /还剩 2 天/);
});

test("status: trial account is labelled 体验账号", () => {
  const { host } = renderStatus({ ...FREE_ME, is_trial: true, ai_daily_limit: 4, ai_daily_used: 0, ai_daily_remaining: 4 });
  assert.match(texts(host), /体验账号/);
  assert.match(texts(host), /今日 AI 0 \/ 4 次 · 午夜重置/);
});

const ORDERS = [
  { id: "o1", plan_name: "月度", amount_cents: 990, channel: "alipay", status: "paid", created_at: "2026-10-01T00:00:00Z" },
  { id: "o2", plan_name: "月度", amount_cents: 990, channel: "wechat", status: "pending", created_at: "2026-10-02T00:00:00Z" },
  { id: "o3", plan_name: "季度", amount_cents: 2490, channel: "alipay", status: "refunded", created_at: "2026-10-03T00:00:00Z" },
  { id: "o4", plan_name: "季度", amount_cents: 2490, channel: "other", status: "closed", created_at: "2026-10-03T00:00:00Z" },
];

test("orders: empty list renders the stamp empty state", () => {
  const e = env();
  const host = e.document.createElement("div");
  e.View.renderOrders(host, [], {});
  assert.ok(find(host, ".pl-stamp"));
  assert.match(texts(host), /暂无订单。/);
  assert.equal(find(host, "table"), null);
});

test("orders: table rows, status pills by tone, refund only for paid, confirm callback gets the order", () => {
  const e = env();
  const host = e.document.createElement("div");
  const refunded = [];
  e.View.renderOrders(host, ORDERS, { yuan: e.View.formatPrice, time: (v) => `T:${v}`, onRefund: (order) => refunded.push(order.id) });
  const headers = host.querySelectorAll("th").map(texts);
  assert.deepEqual(headers, ["时间", "套餐", "金额", "渠道", "状态", "操作"]);
  const rows = host.querySelectorAll("tbody tr");
  assert.equal(rows.length, 4);
  const pills = host.querySelectorAll(".pl-pill").map((pill) => [pill.textContent, pill.className.split(" ").pop()]);
  assert.deepEqual(pills, [["已支付", "pl-pill-paid"], ["待支付", "pl-pill-pending"], ["已退款", "pl-pill-warn"], ["已关闭", "pl-pill-warn"]]);
  assert.match(texts(rows[0]), /¥9\.90/);
  assert.match(texts(rows[0]), /支付宝/);
  assert.match(texts(rows[1]), /微信支付/);
  assert.match(texts(rows[3]), /other/);
  const buttons = host.querySelectorAll("button");
  assert.equal(buttons.length, 1, "only the paid order can be refunded");
  assert.equal(buttons[0].getAttribute("aria-label"), "申请退款：月度，订单 o1");
  buttons[0].click();
  assert.deepEqual(refunded, ["o1"]);
  assert.equal(rows[1].querySelectorAll("td").pop().className, "pl-cell-none");
});

test("orders: refund buttons are disabled while another operation is busy", () => {
  const e = env();
  const host = e.document.createElement("div");
  e.View.renderOrders(host, ORDERS, { busy: true });
  assert.equal(host.querySelector("button").disabled, true);
});

test("orders: user-controlled text never becomes markup", () => {
  const e = env();
  const host = e.document.createElement("div");
  e.View.renderOrders(host, [{ ...ORDERS[0], plan_name: "<b>x</b>", id: "<i>1</i>" }], {});
  assert.equal(host.querySelectorAll("b").length + host.querySelectorAll("i").length, 0);
});

test("scrollTo: smooth normally, instant under reduced motion, tolerates a missing target", () => {
  for (const [reduce, expected] of [[false, "smooth"], [true, "auto"]]) {
    const e = env(["plan.js"], { matchMedia: (query) => ({ matches: reduce && /reduce/.test(query), addEventListener() {} }) });
    const target = e.document.createElement("section");
    const seen = [];
    target.scrollIntoView = (options) => seen.push(options);
    e.View.scrollTo(target);
    assert.deepEqual(plain(seen), [{ behavior: expected, block: "start" }]);
  }
  assert.doesNotThrow(() => env().View.scrollTo(null));
});

test("re-rendering replaces the previous content instead of stacking", () => {
  const e = env();
  const host = e.document.createElement("div");
  for (let i = 0; i < 2; i += 1) e.View.renderStatus(host, e.View.model([], FREE_ME, NOW), FREE_ME, []);
  assert.equal(host.querySelectorAll(".pl-status").length, 1);
});

test("cards: official purchases offer both channels and never offer an amount field", () => {
  const bought = [];
  const e = renderCards([PLAN_A, PLAN_B], FREE_ME, { onBuy: (plan, channel) => bought.push([plan.id, channel]) });
  const card = e.host.querySelectorAll(".pl-card")[1];
  const channel = card.querySelector("select");
  assert.ok(channel);
  assert.deepEqual(channel.querySelectorAll("option").map((option) => [option.value, option.textContent]),
    [["alipay", "支付宝"], ["wechat", "微信支付"]]);
  assert.equal(card.querySelectorAll("input").length, 0);
  assert.match(card.textContent, /金额由后台订单确定/);
  channel.value = "wechat"; card.querySelector("button").click();
  channel.value = "alipay"; card.querySelector("button").click();
  assert.deepEqual(bought, [[1, "wechat"], [1, "alipay"]]);
  channel.value = "unsupported"; card.querySelector("button").click();
  assert.equal(bought.length, 2);
  assert.equal(e.host.querySelectorAll(".pl-card")[2].querySelector("select"), null);
});

const APP_SOURCE = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
function appFunction(name) {
  const start = APP_SOURCE.search(new RegExp(`^(?:async )?function ${name}\\(`, "m"));
  assert.ok(start >= 0, name);
  const end = APP_SOURCE.indexOf("\n}", start) + 2;
  return APP_SOURCE.slice(start, end);
}
const PURCHASE = { order: { id: "fixed-order", amount_cents: 990, status: "pending" },
  payment: { provider: "mock", qr_code_url: "mock://wechat/fixed-order" } };
function purchaseEnv({ immediate = false } = {}) {
  const e = env();
  const calls = [], effects = [];
  Object.assign(e.context, {
    user: { id: 7, is_trial: false }, sessionEpoch: 3, view: "plan", rvfPageGeneration: 4, planPurchase: null,
    message: (text, error) => effects.push(["message", text, error]),
    stopOrderPolling: () => effects.push(["stop"]), startOrderPolling: () => effects.push(["start"]),
    renderPlanOrder: () => effects.push(["render"]),
    loadPlanOrders: async () => effects.push(["list"]), finishPlanOrder: async () => effects.push(["finish"]),
    api: (url, options) => { const call = { url, options, ...deferred() }; calls.push(call); return immediate ? Promise.resolve(PURCHASE) : call.promise; },
  });
  vm.runInContext(appFunction("buyPlan"), e.context);
  return { ...e, calls, effects };
}
for (const channel of ["alipay", "wechat"]) {
  test(`purchase: ${channel} submits only plan and channel; the server supplies amount`, async () => {
    const e = purchaseEnv();
    const buying = e.context.buyPlan({ ...PLAN_A, price_cents: 1 }, channel);
    assert.equal(e.calls[0].url, "/api/orders");
    assert.deepEqual(JSON.parse(e.calls[0].options.body), { plan_id: 1, channel });
    e.calls[0].resolve(PURCHASE); await buying;
    assert.equal(e.context.planPurchase.order.amount_cents, 990);
    assert.equal(e.effects.filter(([name]) => name === "render").length, 1);
  });
}
test("purchase: invalid channels and unavailable accounts cannot submit an order", async () => {
  for (const channel of ["unsupported", "", undefined, null]) {
    const e = purchaseEnv({ immediate: true });
    await e.context.buyPlan(PLAN_A, channel); assert.equal(e.calls.length, 0);
  }
  for (const user of [null, { id: 7, is_trial: true }]) {
    const e = purchaseEnv({ immediate: true }); e.context.user = user;
    await e.context.buyPlan(PLAN_A, "alipay"); assert.equal(e.calls.length, 0);
  }
});
for (const change of ["account", "epoch", "view"]) {
  for (const failure of [false, true]) {
    test(`purchase: late ${failure ? "failure" : "success"} after ${change} leaves the next screen untouched`, async () => {
      const e = purchaseEnv(); const buying = e.context.buyPlan(PLAN_A, "wechat");
      if (change === "account") e.context.user = { id: 8, is_trial: false };
      if (change === "epoch") e.context.sessionEpoch += 1;
      if (change === "view") e.context.view = "home";
      const next = { order: { id: "new-screen" } }; e.context.planPurchase = next;
      const count = e.effects.length;
      if (failure) e.calls[0].reject(new Error("old payment failure")); else e.calls[0].resolve(PURCHASE);
      await assert.doesNotReject(buying);
      assert.equal(e.context.planPurchase, next); assert.equal(e.effects.length, count);
    });
  }
}
function leaveAndReturnToPlan(e) {
  e.context.view = "home"; e.context.rvfPageGeneration += 1;
  e.context.view = "plan"; e.context.rvfPageGeneration += 1;
}
for (const failure of [false, true]) {
  test(`purchase: late ${failure ? "failure" : "success"} after leaving and returning cannot overwrite the new plan page`, async () => {
    const e = purchaseEnv(); const buying = e.context.buyPlan(PLAN_A, "wechat");
    leaveAndReturnToPlan(e);
    const next = { order: { id: "new-page" } }; e.context.planPurchase = next;
    const count = e.effects.length;
    if (failure) e.calls[0].reject(new Error("old page failure")); else e.calls[0].resolve(PURCHASE);
    await assert.doesNotReject(buying);
    assert.equal(e.context.planPurchase, next); assert.equal(e.effects.length, count);
  });
}

function chainedPurchaseEnv() {
  const e = purchaseEnv();
  for (const id of ["plan-orders-list", "plan-order-status", "plan-trial-note"]) {
    const node = e.document.createElement("div"); node.id = id; e.document.body.append(node);
  }
  Object.assign(e.context, {
    $: (selector) => e.document.querySelector(selector), planCatalog: [], busy: false,
    yuan: e.View.formatPrice, timestamp: (value) => value,
    updateUserInfo: () => e.effects.push(["user-info"]),
    renderPlanStatus: () => e.effects.push(["subscription"]),
  });
  for (const name of ["refreshPlanSubscription", "loadPlanOrders", "finishPlanOrder"]) {
    vm.runInContext(appFunction(name), e.context);
  }
  return e;
}
const OLD_ORDERS = [{ ...PURCHASE.order, plan_name: "旧页面套餐", channel: "wechat", created_at: "2026-10-07" }];
const PAID_PURCHASE = { ...PURCHASE, order: { ...PURCHASE.order, status: "paid" } };
for (const stage of ["pending-orders", "paid-me", "paid-orders"]) {
  for (const failure of [false, true]) {
    test(`purchase: late ${stage} ${failure ? "failure" : "success"} after returning leaves orders, subscription and messages untouched`, async () => {
      const e = chainedPurchaseEnv(); const buying = e.context.buyPlan(PLAN_A, "wechat");
      e.calls[0].resolve(stage === "pending-orders" ? PURCHASE : PAID_PURCHASE); await tick();
      assert.equal(e.calls[1].url, stage === "pending-orders" ? "/api/orders" : "/api/me");
      if (stage === "paid-orders") {
        e.calls[1].resolve({ ...e.context.user, plan_active: true }); await tick();
        assert.equal(e.calls[2].url, "/api/orders");
      }
      const orders = e.document.querySelector("#plan-orders-list"); orders.textContent = "新页面订单";
      const nextUser = e.context.user; const count = e.effects.length; const requests = e.calls.length;
      leaveAndReturnToPlan(e);
      const pending = e.calls.at(-1);
      // Unexpected follow-up requests must finish so the regression fails rather than hanging.
      const api = e.context.api;
      e.context.api = (...args) => {
        const response = api(...args); e.calls.at(-1).resolve({ orders: OLD_ORDERS }); return response;
      };
      if (failure) pending.reject(new Error("old refresh failure"));
      else pending.resolve(stage === "paid-me" ? { ...nextUser, plan_active: true } : { orders: OLD_ORDERS });
      await assert.doesNotReject(buying);
      assert.equal(orders.textContent, "新页面订单");
      assert.equal(e.context.user, nextUser);
      assert.equal(e.effects.length, count);
      assert.equal(e.calls.length, requests);
    });
  }
}

test("purchase: a current paid order refreshes subscription and orders before reporting success", async () => {
  const e = chainedPurchaseEnv(); const buying = e.context.buyPlan(PLAN_A, "wechat");
  e.calls[0].resolve(PAID_PURCHASE); await tick();
  const updated = { ...e.context.user, plan_active: true };
  e.calls[1].resolve(updated); await tick();
  e.calls[2].resolve({ orders: [{ ...OLD_ORDERS[0], status: "paid" }] }); await buying;
  assert.equal(e.context.user, updated);
  assert.match(e.document.querySelector("#plan-orders-list").textContent, /旧页面套餐/);
  assert.equal(e.effects.filter(([name]) => name === "user-info").length, 1);
  assert.ok(e.effects.some(([name, text]) => name === "message" && text === "购买成功，套餐已生效。"));
});

test("purchase: current errors remain visible to the host and keep prior order state", async () => {
  const e = purchaseEnv(); const prior = { order: { id: "prior" } }; e.context.planPurchase = prior;
  const buying = e.context.buyPlan(PLAN_A, "wechat");
  e.calls[0].reject(new Error("channel unavailable"));
  await assert.rejects(buying, /channel unavailable/);
  assert.equal(e.context.planPurchase, prior);
});
test("payment display: the order amount is text from the server, with no editable amount", () => {
  const e = env();
  for (const id of ["plan-catalog", "plan-order", "plan-order-details", "plan-order-status", "plan-payment"]) {
    const node = e.document.createElement("div"); node.id = id; e.document.body.append(node);
  }
  Object.assign(e.context, { $: (selector) => e.document.querySelector(selector),
    planPurchase: PURCHASE, yuan: e.View.formatPrice });
  for (const name of ["element", "planFacts", "renderPlanOrder"]) vm.runInContext(appFunction(name), e.context);
  e.context.renderPlanOrder();
  const details = e.document.querySelector("#plan-order-details");
  assert.match(details.textContent, /¥9\.90/);
  assert.match(details.textContent, /金额来自服务端订单/);
  assert.equal(details.querySelectorAll("input").length, 0);
  assert.match(e.document.querySelector("#plan-payment").textContent, /Mock.*不能扫码/);
});
