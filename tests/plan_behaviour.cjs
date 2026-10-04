"use strict";

/* 套餐页视图（static/plan.js）：model 的边界、卡片与按钮分支、状态区、订单表、滚动。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

const NOW = Date.parse("2026-10-04T12:00:00Z");
const DAY = 86400000;
const plain = (value) => JSON.parse(JSON.stringify(value));
const PLAN_A = { id: 1, name: "月度", price_cents: 990, period_days: 30, ai_daily_limit: 30, purchasable: 1 };
const PLAN_B = { id: 2, name: "季度", price_cents: 2490, period_days: 90, ai_daily_limit: 45, purchasable: 0 };
const FREE_ME = { is_trial: false, plan_active: false, plan_id: null, plan_expires_at: null, ai_daily_limit: 10, ai_daily_used: 3, ai_daily_remaining: 7, timezone: "UTC" };
const activeMe = (days, extra = {}) => ({
  ...FREE_ME, plan_active: true, plan_id: 1, plan_name: "月度", plan_expires_at: new Date(NOW + days * DAY - 1000).toISOString(),
  ai_daily_limit: 30, ai_daily_used: 4, ai_daily_remaining: 26, ...extra,
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
  assert.equal(m.freeLimit, 10);
  assert.deepEqual(m.status, { active: false, daysLeft: 0, expiresAt: null, urgent: false });
  assert.deepEqual(m.usage, { used: 3, limit: 10, remaining: 7, ratio: 0.3, exhausted: false });
  assert.equal(m.cards[0].id, "free");
  assert.equal(m.cards[0].priceText, "¥0 · 永久");
  assert.equal(m.cards[0].current, true);
  assert.equal(m.cards[1].priceText, "¥9.90 / 30 天");
  assert.equal(m.cards[1].perDayText, "每天 30 次 AI 生成");
  assert.equal(m.cards[1].multiplierText, "是免费版的 3 倍");
  assert.equal(m.cards[1].periodText, "30 天有效，到期前续费会顺延");
});

test("model: active plan — days left, urgent at <=3 days, current marker, free limit falls back to 10", () => {
  const { View } = env();
  const m = plain(View.model([PLAN_A, PLAN_B], activeMe(29), NOW));
  assert.equal(m.status.active, true);
  assert.equal(m.status.daysLeft, 29);
  assert.equal(m.status.urgent, false);
  assert.equal(m.freeLimit, 10, "active plan limit is not the free allowance");
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
  const m = plain(View.model([{ ...PLAN_A, ai_daily_limit: 25 }], { ...FREE_ME, ai_daily_limit: 20, ai_daily_used: 0, ai_daily_remaining: 20 }, NOW));
  assert.equal(m.freeLimit, 20);
  assert.equal(m.cards[0].perDayText, "每天 20 次 AI 生成");
  assert.equal(m.cards[1].multiplierText, "是免费版的 1.3 倍");
  const same = plain(View.model([{ ...PLAN_A, ai_daily_limit: 10 }], FREE_ME, NOW));
  assert.equal(same.cards[1].multiplierText, "", "no multiplier when not larger than free");
});

test("model: ai_daily_limit 0, trial and missing data do not crash or divide by zero", () => {
  const { View } = env();
  const zero = plain(View.model([PLAN_A], { ...FREE_ME, ai_daily_limit: 0, ai_daily_used: 0, ai_daily_remaining: 0 }, NOW));
  assert.equal(zero.freeLimit, 10);
  assert.equal(zero.usage.ratio, 1);
  assert.equal(zero.usage.exhausted, true);
  const trial = plain(View.model([PLAN_A], { ...FREE_ME, is_trial: true, ai_daily_limit: 2, ai_daily_used: 1, ai_daily_remaining: 1 }, NOW));
  assert.equal(trial.freeLimit, 10);
  const empty = plain(View.model(undefined, undefined, NOW));
  assert.equal(empty.cards.length, 1);
  assert.equal(empty.cards[0].id, "free");
  assert.equal(plain(View.model([], FREE_ME, NOW)).cards.length, 1);
});

test("model: used above limit clamps ratio to 1 and marks exhausted", () => {
  const { View } = env();
  const m = plain(View.model([], { ...FREE_ME, ai_daily_used: 12, ai_daily_remaining: 0 }, NOW));
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
  assert.match(texts(cards[0]), /每天 10 次 AI 生成/);
  assert.match(texts(cards[0]), /全部复习、统计、小组、讨论区功能/);
  assert.equal(find(cards[0], "button"), null, "free card has no button");
  assert.match(texts(cards[1]), /每天 30 次 AI 生成（是免费版的 3 倍）/);
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
  assert.match(texts(host), /今日 AI 3 \/ 10 次 · 午夜重置/);
  assert.equal(find(host, ".pl-usage-warning"), null);
  assert.equal(find(host, ".pl-renew-hint"), null);
  const bar = find(host, ".pl-meter");
  assert.equal(bar.getAttribute("aria-valuenow"), "3");
  assert.equal(bar.getAttribute("aria-valuemax"), "10");
});

test("status: exhausted quota shows the warning and marks the bar", () => {
  const { host } = renderStatus({ ...FREE_ME, ai_daily_used: 10, ai_daily_remaining: 0 });
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
  const { host } = renderStatus({ ...FREE_ME, is_trial: true, ai_daily_limit: 2, ai_daily_used: 0, ai_daily_remaining: 2 });
  assert.match(texts(host), /体验账号/);
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
