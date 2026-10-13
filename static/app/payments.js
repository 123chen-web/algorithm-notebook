// static/app/payments.js — 支付：套餐/订单/退款/轮询。
// 由 static/app.js 按领域拆分（C2），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

// ---- 领域状态 ----
let planPurchase = null;
let planCatalog = [];
let orderPollTimer = null;
let orderPollGeneration = 0;

function yuan(cents) {
  return `¥${(cents / 100).toFixed(2)}`;
}

function planFacts(entries) {
  const facts = element("dl", "", "plan-facts");
  for (const [label, value] of entries) {
    facts.append(element("dt", label), element("dd", value));
  }
  return facts;
}

async function refreshPlanSubscription(isCurrent = () => true) {
  const currentUser = user;
  const updated = await api("/api/me");
  if (user !== currentUser || !user || view !== "plan" || !isCurrent()) return false;
  user = updated;
  updateUserInfo();
  renderPlanStatus();
  // “当前套餐”标记和价格倍数依赖最新的 /api/me，已有目录时一并重画。
  if (planCatalog.length) renderPlans(planCatalog);
  $("#plan-trial-note").hidden = !user.is_trial;
  return true;
}

function renderPlanStatus() {
  window.PlanView.renderStatus($("#plan-subscription"), window.PlanView.model(planCatalog, user, Date.now()), user, planCatalog);
}

async function loadPlanOrders(isCurrent = () => true) {
  if (!isCurrent()) return false;
  const currentUser = user;
  const { orders } = await api("/api/orders");
  if (user !== currentUser || !user || view !== "plan" || !isCurrent()) return false;
  window.PlanView.renderOrders($("#plan-orders-list"), orders, {
    yuan, time: timestamp, busy,
    onRefund: (order) => run(() => refundPlanOrder(order)),
  });
  return true;
}

async function refundPlanOrder(order) {
  if (!confirm(`确认申请退回「${order.plan_name}」订单 ${order.id} 的全部款项 ${yuan(order.amount_cents)}？\n\n退款成功后会立即清空当前整体套餐，即使当前套餐来自其他订单；不按剩余天数折算，也不恢复今天已用掉的 AI 次数。此操作不可撤销。`)) return;
  const currentUser = user;
  message();
  let refunded;
  try {
    const result = await api(`/api/orders/${encodeURIComponent(order.id)}/refund`, { method: "POST" });
    refunded = result.order;
  } catch (error) {
    // 另一页面可能已经退款；刷新状态后仍保留原始错误，未确认结果时允许重试。
    if (error.status === 409 && user === currentUser && view === "plan") {
      try {
        if (await refreshPlanSubscription()) await loadPlanOrders();
      } catch {
        // 保留原始退款错误。
      }
    }
    throw error;
  }
  if (user !== currentUser || !user || view !== "plan") return;
  if (planPurchase && planPurchase.order.id === refunded.id) {
    planPurchase.order = refunded;
    renderPlanOrder();
  }
  try {
    if (!await refreshPlanSubscription()) return;
    if (!await loadPlanOrders()) return;
    message("退款成功，当前套餐已收回，今日已用 AI 次数保持不变。");
  } catch (error) {
    if (error.status === 401) throw error;
    message("退款成功，当前套餐已收回；页面刷新失败，请稍后刷新查看。", true);
  }
}

async function buyPlan(plan, channel) {
  if (!user || user.is_trial || view !== "plan") return;
  if (!["alipay", "wechat"].includes(channel)) {
    message("请选择支付宝或微信支付。", true);
    return;
  }
  const owner = user.id;
  const epoch = sessionEpoch;
  const page = rvfPageGeneration;
  const current = () => Boolean(user) && user.id === owner && sessionEpoch === epoch
    && page === rvfPageGeneration && view === "plan";
  message();
  try {
    const purchase = await api("/api/orders", {
      method: "POST",
      body: JSON.stringify({ plan_id: plan.id, channel }),
    });
    if (!current()) return;
    stopOrderPolling();
    planPurchase = purchase;
    renderPlanOrder();
    if (purchase.order.status === "pending") {
      startOrderPolling();
      if (!await loadPlanOrders(current) || !current()) return;
    } else {
      await finishPlanOrder(current);
      if (!current()) return;
    }
  } catch (error) {
    if (current()) throw error;
  }
}

function renderPlans(plans) {
  planCatalog = plans;
  const list = $("#plan-list");
  if (!plans.length) {
    list.replaceChildren(element("p", "暂无可购买的套餐。", "muted"));
    return;
  }
  window.PlanView.renderCards(list, window.PlanView.model(plans, user, Date.now()), plans, {
    isTrial: Boolean(user.is_trial),
    onBuy: (plan, channel) => run(() => buyPlan(plan, channel)),
    onHow: () => window.PlanView.scrollTo($("#plan-how")),
  });
}

function renderPlanOrder() {
  $("#plan-catalog").hidden = Boolean(planPurchase);
  $("#plan-order").hidden = !planPurchase;
  if (!planPurchase) return;

  const { order, payment } = planPurchase;
  $("#plan-order-details").replaceChildren(planFacts([
    ["订单号", order.id],
    ["金额", yuan(order.amount_cents)],
  ]));
  $("#plan-order-details").append(element("p", "应付金额来自服务端订单，支付平台按这笔订单收款；无需自行输入金额。", "muted"));
  const statuses = {
    pending: "等待支付，每 3 秒自动查询一次，最多查询 5 分钟。",
    paid: "支付成功。",
    failed: "支付失败，可以返回套餐列表重新下单。",
    closed: "订单已关闭，可以返回套餐列表重新下单。",
    refunded: "订单已退款，可以返回套餐列表。",
  };
  $("#plan-order-status").textContent = statuses[order.status] || "未知订单状态，请稍后刷新。";
  const paymentBox = $("#plan-payment");
  paymentBox.replaceChildren();
  if (order.status !== "pending") return;

  const paymentText = payment.qr_code_url || "";
  const isMock = payment.provider === "mock" || paymentText.startsWith("mock://");
  const providerName = { alipay: "支付宝", wechat: "微信支付" }[payment.provider] || "对应 App";
  // 支付内容来自接口，仅允许预期的链接协议；原文始终以文本节点展示。
  if (/^(https?:\/\/|alipays?:\/\/|weixin:\/\/|mock:\/\/)/i.test(paymentText)) {
    const link = element("a", isMock ? "Mock 支付链接（仅占位）" : `打开${providerName}完成支付`, "plan-payment-link");
    link.href = paymentText;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    paymentBox.append(link);
  }
  if (paymentText) {
    paymentBox.append(element("pre", paymentText, "code plan-payment-text"));
  }
  paymentBox.append(element("p", isMock
    ? "这是本地 Mock 支付占位内容，不能扫码或完成真实付款。"
    : `手机上点击链接直接跳转${providerName}完成支付；电脑上可先将上面的原始文本生成二维码，再用${providerName}扫一扫。当前页面暂不提供图形二维码，不能直接扫描这段文字。`,
  "muted"));
  if (!paymentText) {
    paymentBox.append(element("p", "暂未取得支付链接，请稍后刷新查看订单状态。", "muted"));
  }
}

function stopOrderPolling() {
  clearInterval(orderPollTimer);
  orderPollTimer = null;
  // 使已经发出的旧查询失效，避免切换页面或账号后写回旧订单。
  orderPollGeneration += 1;
}

async function finishPlanOrder(isCurrent = () => true) {
  if (!isCurrent()) return;
  stopOrderPolling();
  const status = planPurchase.order.status;
  if (!await refreshPlanSubscription(isCurrent) || !isCurrent()) return;
  if (!await loadPlanOrders(isCurrent) || !isCurrent()) return;
  if (status === "paid") {
    message(user.plan_active
      ? "购买成功，套餐已生效。"
      : "订单已支付；当前无有效套餐，请查看当前订阅。");
  } else {
    message($("#plan-order-status").textContent, true);
  }
}

async function checkPlanOrder(generation) {
  const purchase = planPurchase;
  const { order } = await api(`/api/orders/${encodeURIComponent(purchase.order.id)}`);
  if (generation !== orderPollGeneration || planPurchase !== purchase || view !== "plan" || !user) return;
  const statusChanged = purchase.order.status !== order.status;
  purchase.order = order;
  // pending 时保留支付文本的选区和链接焦点，方便复制或打开。
  if (statusChanged) renderPlanOrder();
  if (statusChanged && order.status !== "pending") await finishPlanOrder();
}

function startOrderPolling() {
  stopOrderPolling();
  const generation = orderPollGeneration;
  const deadline = Date.now() + ORDER_POLL_DURATION;
  orderPollTimer = setInterval(() => {
    if (generation !== orderPollGeneration) return;
    if (view !== "plan" || !user) {
      stopOrderPolling();
      return;
    }
    // run 会串行处理异步操作；其他操作进行中时跳过查询和超时提示。
    if (busy) return;
    if (Date.now() >= deadline) {
      stopOrderPolling();
      const text = "暂未检测到支付结果，可以稍后刷新这个页面查看。";
      $("#plan-order-status").textContent = text;
      message(text);
      return;
    }
    run(async () => {
      await checkPlanOrder(generation);
    });
  }, ORDER_POLL_INTERVAL);
}

async function loadPlanPage() {
  configureRedeem();
  stopOrderPolling();
  if (!await refreshPlanSubscription()) return;
  if (user.is_trial) planPurchase = null;
  const currentUser = user;
  const { plans } = await api("/api/plans");
  if (user !== currentUser || !user || view !== "plan") return;
  renderPlans(plans);
  renderPlanStatus();
  renderPlanOrder();
  await window.Redeem?.loadPlan(plans);
  window.ManualClaims?.mount($("#manual-claims"));
  if (user !== currentUser || !user || view !== "plan") return;
  if (!await loadPlanOrders()) return;
  if (planPurchase) {
    if (planPurchase.order.status === "pending") startOrderPolling();
    await checkPlanOrder(orderPollGeneration);
  }
}
