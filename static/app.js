"use strict";

const $ = (selector) => document.querySelector(selector);

const grades = [
  [0, "忘记 / 答错"],
  [3, "困难，但答对"],
  [4, "记得"],
  [5, "熟练"],
];

const resultLabels = {
  unattempted: "待练习",
  solved: "已解决",
  partial: "部分解决",
  failed: "未解决",
};

let user = null;
let view = "today";
let busy = false;
let planPurchase = null;
let orderPollTimer = null;
let orderPollGeneration = 0;
let forumPost = null;
let zones = [];
let codeZones = new Set();

const ORDER_POLL_INTERVAL = 3000;
const ORDER_POLL_DURATION = 5 * 60 * 1000;

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function avatarHue(username) {
  let hash = 0;
  for (const char of username) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 360;
}

// avatarVersion > 0 时用户上传过头像，走真实图片；否则用用户名首字母的
// 纯色圆形占位，颜色由用户名哈希决定——同一个人每次看到的颜色都一样。
function avatarElement(userId, username, avatarVersion, { small = false } = {}) {
  const className = small ? "avatar avatar-sm" : "avatar";
  if (avatarVersion > 0) {
    const img = document.createElement("img");
    img.className = className;
    img.src = `/api/users/${userId}/avatar?v=${avatarVersion}`;
    img.alt = `${username} 的头像`;
    return img;
  }
  const fallback = element("span", (username || "?").slice(0, 1).toUpperCase(), className);
  fallback.style.background = `hsl(${avatarHue(username || "")}, 55%, 45%)`;
  fallback.setAttribute("aria-hidden", "true");
  return fallback;
}

// 举报某个用户的头像；用在帖子/评论作者旁边，跟举报帖子/评论内容是两件事。
// onCancel 通常是把界面切回举报前的只读状态。
function avatarReportForm(userId, onCancel) {
  const reason = textarea("", 500, 3);
  const submit = element("button", "提交举报", "primary");
  submit.type = "submit";
  const cancel = element("button", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", onCancel);

  const form = document.createElement("form");
  form.append(field("举报头像的原因（可选）", reason), submit, cancel);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      await api(`/api/users/${userId}/avatar/report`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.value }),
      });
      message("已提交头像举报，管理员会尽快处理。");
      onCancel();
    });
  });
  return form;
}

function message(text = "", error = false) {
  $("#notice").textContent = text;
  $("#notice").classList.toggle("error", error);
}

function setBusy(value) {
  $("#app").setAttribute("aria-busy", String(value));
  $("#auth").setAttribute("aria-busy", String(value));
  $("#notice").classList.toggle("pending", value);
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = value || button.dataset.blocked === "1";
  });
}

const AUTH_PANELS = ["login-form", "register-form", "forgot-form", "reset-form"];

function showAuthPanels(visibleIds) {
  for (const id of AUTH_PANELS) {
    $(`#${id}`).hidden = !visibleIds.includes(id);
  }
}

function signedOut() {
  stopOrderPolling();
  planPurchase = null;
  $("#plan-subscription").replaceChildren();
  $("#plan-list").replaceChildren();
  $("#plan-orders-list").replaceChildren();
  $("#plan-payment").replaceChildren();
  $("#plan-order-details").replaceChildren();
  $("#plan-order-status").textContent = "";
  $("#plan-order").hidden = true;
  user = null;
  $("#auth").hidden = false;
  $("#app").hidden = true;
  $("#logout").hidden = true;
  $("#user-info-wrap").replaceChildren();
  $("#my-avatar-wrap").hidden = true;
  $("#avatar-file-input").value = "";
  $("#email-prompt").hidden = true;
  $("#trial-banner").hidden = true;
  showAuthPanels(["login-form", "register-form"]);
  $("#cards").replaceChildren();
  $("#detail").replaceChildren();
  $("#leaderboard-me").replaceChildren();
  $("#leaderboard-entries").replaceChildren();
  $("#leaderboard-status").textContent = "";
  $("#leaderboard-table-wrap").hidden = true;
  $("#leaderboard-page").setAttribute("aria-busy", "false");
  forumPost = null;
  $("#forum-posts").replaceChildren();
  $("#forum-list-status").textContent = "";
  $("#forum-post").replaceChildren();
  $("#forum-comments").replaceChildren();
  $("#forum-compose-form").reset();
  $("#forum-comment-form").reset();
  $("#admin-tab").hidden = true;
  $("#admin-reports").replaceChildren();
  $("#admin-status").textContent = "";
  $("#problem-form").reset();
  $("#mistake-inputs").replaceChildren();
  addMistakeInput();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Protection": "1",
      ...(options.headers || {}),
    },
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    if (response.status === 401) signedOut();

    let detail = data.detail || "请求失败，请稍后重试";
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => `${item.loc.join(".")}: ${item.msg}`)
        .join("；");
    } else if (typeof detail === "object") {
      detail = detail.message || "请求失败，请稍后重试";
    }

    const error = new Error(String(detail));
    error.status = response.status;
    throw error;
  }

  return data;
}

async function uploadAvatarFile(file) {
  const body = new FormData();
  body.append("file", file);
  // 不能像 api() 那样固定 Content-Type: application/json——multipart 请求
  // 的 boundary 必须由浏览器自己生成，手动设置反而会破坏它。
  const response = await fetch("/api/me/avatar", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Protection": "1" },
    body,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) signedOut();
    const detail = data.detail;
    const text = typeof detail === "object" && detail !== null ? detail.message : detail;
    const error = new Error(String(text || "上传失败，请稍后重试"));
    error.status = response.status;
    throw error;
  }
  return data;
}

async function run(action) {
  if (busy) return;
  busy = true;
  setBusy(true);

  try {
    await action();
  } catch (error) {
    if (error.status === 409 && user && (view === "today" || view === "all")) {
      try {
        await loadList();
      } catch {
        // 保留原始冲突提示。
      }
    }
    message(error.message || "操作失败，请检查网络后重试", true);
  } finally {
    busy = false;
    setBusy(false);
  }
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function field(labelText, control) {
  const label = element("label", labelText);
  label.append(control);
  return label;
}

function textarea(value, maxLength, rows, code = false) {
  const control = document.createElement("textarea");
  control.value = value;
  control.maxLength = maxLength;
  control.rows = rows;
  if (code) {
    control.className = "code";
    control.spellcheck = false;
  }
  return control;
}

function timestamp(value) {
  if (!value) return "尚无";
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: user.timezone,
    hour12: false,
  });
}

function renderUserInfo() {
  const wrap = $("#user-info-wrap");

  function readOnly() {
    wrap.replaceChildren(
      element("span", `${user.username} · ${user.timezone} · ${user.today}`)
    );
    if (!user.is_trial) {
      const editBtn = element("button", "改用户名", "link-button");
      editBtn.type = "button";
      editBtn.addEventListener("click", editForm);
      wrap.append(editBtn);
    }
  }

  function editForm() {
    const input = document.createElement("input");
    input.value = user.username;
    input.maxLength = 32;
    input.required = true;
    input.autocomplete = "off";
    const save = element("button", "保存", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.className = "inline-form";
    form.append(field("新用户名", input), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api("/api/me/username", {
          method: "PUT",
          body: JSON.stringify({ username: input.value }),
        });
        user.username = updated.username;
        message("用户名已更新。");
        readOnly();
      });
    });

    wrap.replaceChildren(form);
  }

  readOnly();
}

function updateUserInfo() {
  renderUserInfo();
  $("#email-prompt").hidden = Boolean(user.email) || Boolean(user.is_trial);
  $("#trial-banner").hidden = !user.is_trial;
  $("#admin-tab").hidden = !user.is_admin;

  $("#my-avatar-wrap").hidden = false;
  $("#my-avatar").replaceWith(
    Object.assign(avatarElement(user.id, user.username, user.avatar_version), { id: "my-avatar" })
  );
  $(".avatar-upload-label").hidden = user.is_trial;
  $("#remove-avatar-btn").hidden = user.is_trial || !(user.avatar_version > 0);
}

async function loadZones() {
  const data = await api("/api/zones");
  zones = data.zones;
  codeZones = new Set(data.code_zones);

  const problemZone = $("#problem-zone");
  problemZone.replaceChildren();
  for (const zone of zones) {
    const option = element("option", zone);
    option.value = zone;
    problemZone.append(option);
  }

  const zoneFilter = $("#zone-filter");
  for (const zone of zones) {
    const option = element("option", zone);
    option.value = zone;
    zoneFilter.append(option);
  }

  applyZoneFieldMode($("#problem-form"), problemZone.value);
}

// 编程类分区要求填写编程语言，"代码"字段就是字面意义的代码；
// 数学类分区没有编程语言，同一个字段改用来记录解题过程/演算。
function applyZoneFieldMode(form, zoneValue) {
  const isCode = codeZones.has(zoneValue);
  const languageField = form.querySelector("[data-role=language-field]");
  const languageInput = form.querySelector("[name=language]");
  const codeLabel = form.querySelector("[data-role=code-label]");
  languageField.hidden = !isCode;
  languageInput.required = isCode;
  if (!isCode) languageInput.value = "";
  codeLabel.textContent = isCode ? "当时的代码" : "当时的解题过程";
  const codeInput = codeLabel.nextElementSibling;
  if (codeInput) {
    codeInput.placeholder = isCode
      ? "粘贴当时的代码，保留错误也没关系。"
      : "写下当时的解题过程或演算，保留错误也没关系。";
  }
}

async function enterApp() {
  user = await api("/api/me");
  $("#auth").hidden = true;
  $("#app").hidden = false;
  $("#logout").hidden = false;
  updateUserInfo();
  await loadZones();
  await showView("today");
}

async function showView(nextView) {
  stopOrderPolling();
  view = nextView;
  $("#new-page").hidden = view !== "new";
  $("#list-page").hidden = view !== "today" && view !== "all";
  $("#plan-page").hidden = view !== "plan";
  $("#leaderboard-page").hidden = view !== "leaderboard";
  $("#forum-page").hidden = view !== "forum";
  $("#admin-page").hidden = view !== "admin";

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  });

  if (view === "admin") await loadAdminReports();
  else if (view === "forum") await showForumList();
  else if (view === "leaderboard") await loadLeaderboard();
  else if (view === "plan") await loadPlanPage();
  else if (view === "today" || view === "all") await loadList();
}

async function loadLeaderboard() {
  const currentUser = user;
  const page = $("#leaderboard-page");
  const status = $("#leaderboard-status");
  const entries = $("#leaderboard-entries");
  const mine = $("#leaderboard-me");
  page.setAttribute("aria-busy", "true");
  status.textContent = "正在加载连续打卡排行榜…";
  entries.replaceChildren();
  mine.replaceChildren();
  $("#leaderboard-table-wrap").hidden = true;

  try {
    const data = await api("/api/leaderboard");
    if (user !== currentUser || !user || view !== "leaderboard") return;
    const { me } = data;
    $("#leaderboard-list-title").textContent = `排行榜 · 前 ${data.leaderboard_size} 名`;
    const rankText = me.is_trial
      ? "体验账号不参与排名，你仍可查看自己的连续打卡天数。"
      : me.rank === null
        ? "暂无排名，连续打卡至少 1 天即可参与排名。"
        : `第 ${me.rank} 名${me.rank > data.leaderboard_size ? `（未进入前 ${data.leaderboard_size} 名）` : ""}`;
    mine.append(
      element("p", `${me.streak_days} 天`, "leaderboard-streak"),
      element("p", rankText, "leaderboard-my-rank")
    );
    for (const entry of data.entries) {
      const row = element("tr");
      row.append(
        element("td", `第 ${entry.rank} 名`, "leaderboard-rank"),
        element("td", entry.display_name),
        element("td", `${entry.streak_days} 天`, "leaderboard-days")
      );
      entries.append(row);
    }
    status.textContent = data.entries.length ? "" : "暂无上榜用户，完成一次复习评分，开始连续打卡吧。";
    $("#leaderboard-table-wrap").hidden = !data.entries.length;
  } catch (error) {
    if (user === currentUser && user && view === "leaderboard") {
      status.textContent = "排行榜加载失败，请点击上方“刷新”重试。";
    }
    throw error;
  } finally {
    if (user === currentUser && user && view === "leaderboard") {
      page.setAttribute("aria-busy", "false");
    }
  }
}

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

async function refreshPlanSubscription() {
  const currentUser = user;
  const updated = await api("/api/me");
  if (user !== currentUser || !user || view !== "plan") return false;
  user = updated;
  updateUserInfo();
  const entries = [
    ["套餐", user.plan_name || "当前使用免费额度"],
    ["状态", user.plan_active ? "有效" : "无有效套餐，当前使用免费额度"],
  ];
  if (user.plan_expires_at) {
    entries.push(["到期时间", timestamp(user.plan_expires_at)]);
  }
  entries.push(
    ["今日 AI 用量", `${user.ai_daily_used} / ${user.ai_daily_limit} 次`],
    ["今日剩余", `${user.ai_daily_remaining} 次`]
  );
  $("#plan-subscription").replaceChildren(planFacts(entries));
  $("#plan-trial-note").hidden = !user.is_trial;
  return true;
}

async function loadPlanOrders() {
  const currentUser = user;
  const { orders } = await api("/api/orders");
  if (user !== currentUser || !user || view !== "plan") return false;
  const list = $("#plan-orders-list");
  list.replaceChildren();
  if (!orders.length) {
    list.append(element("p", "暂无订单。", "muted"));
    return true;
  }
  const statuses = {
    pending: "待支付",
    paid: "已支付",
    failed: "支付失败",
    closed: "已关闭",
    refunded: "已退款",
  };
  for (const order of orders) {
    const row = element("article", "", "plan-order-row");
    const heading = element("div", "", "plan-order-heading");
    heading.append(
      element("h4", order.plan_name),
      element("span", yuan(order.amount_cents), "plan-order-amount")
    );
    row.append(heading, planFacts([
      ["订单号", order.id],
      ["状态", statuses[order.status] || "未知状态"],
      ["创建时间", timestamp(order.created_at)],
    ]));
    if (order.status === "paid") {
      const refund = element("button", "申请退款", "danger");
      refund.type = "button";
      refund.disabled = busy;
      refund.setAttribute("aria-label", `申请退款：${order.plan_name}，订单 ${order.id}`);
      refund.addEventListener("click", () => run(() => refundPlanOrder(order)));
      const actions = element("div", "", "actions");
      actions.append(refund);
      row.append(actions);
    }
    list.append(row);
  }
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

function renderPlans(plans) {
  const list = $("#plan-list");
  list.replaceChildren();
  if (!plans.length) {
    list.append(element("p", "暂无可购买的套餐。", "muted"));
    return;
  }
  for (const plan of plans) {
    const card = element("article", "", "plan-card panel");
    card.append(
      element("h4", plan.name),
      element("p", yuan(plan.price_cents), "plan-price"),
      element("p", `${plan.period_days} 天`, "muted"),
      element("p", `每天 ${plan.ai_daily_limit} 次 AI 生成`)
    );
    if (!user.is_trial) {
      const buy = element("button", "购买", "primary");
      buy.type = "button";
      buy.setAttribute("aria-label", `购买${plan.name}`);
      buy.addEventListener("click", () => run(async () => {
        if (user.is_trial) return;
        message();
        const purchase = await api("/api/orders", {
          method: "POST",
          body: JSON.stringify({ plan_id: plan.id, channel: "alipay" }),
        });
        stopOrderPolling();
        planPurchase = purchase;
        renderPlanOrder();
        if (purchase.order.status === "pending") {
          startOrderPolling();
          await loadPlanOrders();
        } else await finishPlanOrder();
      }));
      const actions = element("div", "", "actions");
      actions.append(buy);
      card.append(actions);
    }
    list.append(card);
  }
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

async function finishPlanOrder() {
  stopOrderPolling();
  const status = planPurchase.order.status;
  if (!await refreshPlanSubscription()) return;
  if (!await loadPlanOrders()) return;
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
  stopOrderPolling();
  if (!await refreshPlanSubscription()) return;
  if (user.is_trial) planPurchase = null;
  const currentUser = user;
  const { plans } = await api("/api/plans");
  if (user !== currentUser || !user || view !== "plan") return;
  renderPlans(plans);
  renderPlanOrder();
  if (!await loadPlanOrders()) return;
  if (planPurchase) {
    if (planPurchase.order.status === "pending") startOrderPolling();
    await checkPlanOrder(orderPollGeneration);
  }
}

async function loadList() {
  const zoneParam = $("#zone-filter").value;
  const query = `due_only=${view === "today"}` + (zoneParam ? `&zone=${encodeURIComponent(zoneParam)}` : "");
  const data = await api(`/api/mistakes?${query}`);
  user.today = data.today;
  updateUserInfo();

  $("#list-title").textContent =
    view === "today" ? "今日复习" : "全部记录";
  $("#list-summary").textContent = view === "today"
    ? `${data.today} · 今天有 ${data.items.length} 条易错点待复习（含逾期）`
    : `共 ${data.items.length} 条易错点 · 每一条，都有自己的复习节奏`;

  $("#cards").replaceChildren();
  clearDetail();

  if (!data.items.length) {
    $("#cards").append(element(
      "p",
      view === "today" ? "今天没有待复习的易错点。" : "你的第一条记录，会出现在这里。",
      "muted empty-list"
    ));
    clearDetail(
      view === "today" ? "今天的复习，告一段落" : "从一道做错的题开始",
      view === "today"
        ? "可以去「全部记录」回看笔记，也可以在「新增记录」留下今天的新发现。"
        : "点击「新增记录」，留下代码、思路和错因。每条易错点都会单独安排复习。"
    );
    return;
  }

  for (const item of data.items) {
    const button = element("button", "", "record-button");
    button.type = "button";
    button.dataset.id = String(item.id);
    button.setAttribute("aria-pressed", "false");
    button.append(
      element("strong", item.title),
      element("span", item.description || "错因待 AI 诊断", "record-description"),
      element("small", `${item.zone} · ${item.due_date <= data.today ? "待复习" : "下次复习"} · ${item.due_date}`, "muted")
    );
    button.addEventListener("click", () => run(() => openMistake(item.id)));
    $("#cards").append(button);
  }
}

async function openMistake(id) {
  const item = await api(`/api/mistakes/${id}`);
  user.today = item.today;
  updateUserInfo();

  document.querySelectorAll(".record-button").forEach((button) => {
    button.classList.toggle("selected", Number(button.dataset.id) === id);
    button.setAttribute("aria-pressed", String(Number(button.dataset.id) === id));
  });

  renderDetail(item);
}

const VARIANT_SECTION_PATTERN =
  /^【错因】\s*\n([\s\S]*?)\n【讲解】\s*\n([\s\S]*?)\n【核心知识点】\s*\n([\s\S]*?)\n【练习题】\s*\n([\s\S]*)$/;

function renderVariantDescription(text) {
  const match = text.trim().match(VARIANT_SECTION_PATTERN);
  if (!match) {
    // 新变体只含单题正文（编程题还带样例）；也兼容更早的纯文本题目。
    return element("pre", text, "prose");
  }
  const [, summary, explanation, knowledge, question] = match;
  const wrap = element("div", "", "variant-sections");
  for (const [label, content] of [
    ["错因", summary], ["讲解", explanation],
    ["核心知识点", knowledge], ["练习题", question],
  ]) {
    wrap.append(element("h4", label), element("p", content.trim(), "multiline"));
  }
  return wrap;
}

function renderVariant(variant, isCodeZone) {
  const box = document.createElement("details");
  box.className = "variant";
  box.open = variant.result === "unattempted";

  const summary = element(
    "summary",
    `变体 #${variant.id} · ${resultLabels[variant.result]}`
  );
  const savedAt = element(
    "p",
    variant.result_updated_at
      ? `结果保存于：${timestamp(variant.result_updated_at)}`
      : "做完后，在下面记下这次的结果。",
    "muted"
  );

  const form = document.createElement("form");
  // 旧数学题没有参考答案，继续允许用户自行记录结果。
  const autoJudge = !isCodeZone && Boolean(variant.expected_answer);
  let result;
  if (!autoJudge) {
    result = document.createElement("select");
    for (const [value, label] of Object.entries(resultLabels)) {
      const option = element("option", label);
      option.value = value;
      result.append(option);
    }
    result.value = variant.result;
    form.append(field("练习结果", result));
  }

  let code;
  let answer;
  if (isCodeZone) {
    code = textarea(variant.answer_code, 40000, 7, true);
    form.append(field("我的解答代码 · 仅保存，不运行", code));
  } else {
    answer = document.createElement("input");
    answer.name = "answer";
    answer.value = variant.answer || "";
    answer.maxLength = 500;
    answer.required = autoJudge;
    answer.placeholder = "多个数值用英文逗号分隔，例如：1, 1, 4";
    form.append(field("我的答案", answer));
    if (!autoJudge) {
      form.append(element("p", "这道旧练习题没有参考答案，请自行记录结果。", "muted"));
    }
  }
  const judgment = element("p");
  const reference = element("p", "", "multiline");

  function updateJudgment(updated) {
    const hasSavedResult = autoJudge && Boolean(updated.result_updated_at);
    judgment.hidden = !hasSavedResult;
    reference.hidden = !hasSavedResult;
    judgment.textContent = updated.result === "solved"
      ? "系统判定：正确"
      : updated.result === "failed" ? "系统判定：错误" : "系统判定：待作答";
    reference.textContent = hasSavedResult ? `参考答案：${updated.expected_answer}` : "";
  }
  updateJudgment(variant);

  const save = element("button", "保存练习结果", "primary");
  save.type = "submit";
  form.append(save);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      const updated = await api(`/api/variants/${variant.id}/result`, {
        method: "PUT",
        body: JSON.stringify(isCodeZone
          ? { result: result.value, answer_code: code.value }
          : { result: result ? result.value : variant.result, answer: answer.value }),
      });
      variant = updated;
      summary.textContent =
        `变体 #${updated.id} · ${resultLabels[updated.result]}`;
      savedAt.textContent =
        `结果保存于：${timestamp(updated.result_updated_at)}`;
      updateJudgment(updated);
      message("练习结果已保存。这次练习不会改变复习日期。");
    });
  });

  box.append(
    summary,
    renderVariantDescription(variant.description),
    savedAt,
    form,
    judgment,
    reference
  );
  return box;
}

function renderMistakeText(item) {
  const wrap = element("div", "", "mistake-text");

  function readOnly() {
    const editBtn = element("button", "编辑这条错因");
    editBtn.type = "button";
    editBtn.addEventListener("click", editForm);
    const text = item.description
      ? element("p", item.description, "multiline")
      : element("p", "还没有错因描述，点击下面「诊断错因并出两道新题」让 AI 帮你反推。", "muted");
    wrap.replaceChildren(text, editBtn);
  }

  function editForm() {
    const input = textarea(item.description, 2000, 4);
    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("哪里容易错，为什么会错？（可选）", input), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/mistakes/${item.id}`, {
          method: "PUT",
          body: JSON.stringify({
            description: input.value,
            version: item.version,
          }),
        });
        message("易错点描述已更新。");
        await openMistake(updated.id);
      });
    });

    wrap.replaceChildren(form);
  }

  readOnly();
  return wrap;
}

function renderProblemEditor(item) {
  const wrap = element("div", "", "problem-editor");

  function readOnly() {
    const editBtn = element("button", "编辑题目、代码和思路");
    editBtn.type = "button";
    editBtn.addEventListener("click", editForm);
    wrap.replaceChildren(
      element("h4", "当时的思路"),
      element("p", item.thinking, "multiline"),
      element("h4", codeZones.has(item.zone) ? "当时的代码" : "当时的解题过程"),
      element("pre", item.code, "code"),
      editBtn
    );
  }

  function editForm() {
    const title = document.createElement("input");
    title.value = item.title;
    title.maxLength = 200;
    title.required = true;

    const zoneSelect = document.createElement("select");
    zoneSelect.required = true;
    for (const zone of zones) {
      const option = element("option", zone);
      option.value = zone;
      zoneSelect.append(option);
    }
    zoneSelect.value = item.zone;

    const language = document.createElement("input");
    language.name = "language";
    language.value = item.language;
    language.maxLength = 40;
    const languageField = element("label", "编程语言", "");
    languageField.dataset.role = "language-field";
    languageField.append(language);

    const code = textarea(item.code, 40000, 10, true);
    const codeLabelText = element("span", "当时的代码");
    codeLabelText.dataset.role = "code-label";
    const codeField = element("label");
    codeField.append(codeLabelText, code);

    const thinking = textarea(item.thinking, 8000, 4);

    const save = element("button", "保存题目信息", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    zoneSelect.addEventListener("change", () => applyZoneFieldMode(form, zoneSelect.value));
    form.append(
      field("标题", title),
      field("分区", zoneSelect),
      languageField,
      codeField,
      field("思路", thinking),
      save,
      cancel
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/problems/${item.problem_id}`, {
          method: "PUT",
          body: JSON.stringify({
            title: title.value,
            zone: zoneSelect.value,
            language: language.value,
            code: code.value,
            thinking: thinking.value,
          }),
        });
        message("题目信息已更新。");
        await openMistake(item.id);
      });
    });

    wrap.replaceChildren(form);
    applyZoneFieldMode(form, zoneSelect.value);
  }

  readOnly();
  return wrap;
}

function clearDetail(
  title = "从一条易错点开始",
  description = "选中列表中的一条记录，先回忆如何避免这个错误，再对照笔记，给这次掌握程度打个分。"
) {
  const empty = element("div", "", "empty-state");
  const symbol = element("span", "≡", "empty-symbol");
  symbol.setAttribute("aria-hidden", "true");
  empty.append(
    symbol,
    element("h3", title),
    element("p", description, "muted")
  );
  $("#detail").replaceChildren(empty);
}

function renderDetail(item) {
  const root = $("#detail");
  root.replaceChildren();
  const isCodeZone = codeZones.has(item.zone);

  const heading = element("div", "", "detail-heading");
  heading.append(
    element("h2", item.title),
    element("p", item.language ? `${item.zone} · ${item.language}` : item.zone, "language-badge")
  );
  let mistakeTextNode = renderMistakeText(item);
  root.append(
    heading,
    element("h3", "这次需要记住的错因", "section-label"),
    mistakeTextNode,
    element(
      "p",
      `下次复习：${item.due_date} · 连续成功：${item.repetitions} 次`,
      "muted review-meta"
    ),
    element(
      "p",
      `上次复习：${timestamp(item.last_reviewed_at)}`,
      "muted review-meta"
    )
  );

  const original = document.createElement("details");
  original.className = "original-record";
  original.append(
    element("summary", "查看当时的思路和代码"),
    renderProblemEditor(item)
  );
  root.append(original);

  const deleteMistakeBtn = element("button", "删除这条易错点", "danger");
  deleteMistakeBtn.type = "button";
  deleteMistakeBtn.addEventListener("click", () => run(async () => {
    if (!confirm("删除这条易错点？它的复习记录和变体题也会一起删除，无法恢复。同一道题的其他易错点会保留。")) {
      return;
    }
    await api(`/api/mistakes/${item.id}`, { method: "DELETE" });
    clearDetail();
    await loadList();
    message("已删除这条易错点。");
  }));

  const deleteProblemBtn = element(
    "button", "删除整道题及全部记录", "danger"
  );
  deleteProblemBtn.type = "button";
  deleteProblemBtn.addEventListener("click", () => run(async () => {
    if (!confirm(
      "确定删除整道题？这道题下的所有易错点、复习记录和变体题都会一起删除，且无法恢复。"
    )) {
      return;
    }
    await api(`/api/problems/${item.problem_id}`, { method: "DELETE" });
    clearDetail();
    await loadList();
    message("已删除整道题。");
  }));

  const dangerZone = element("div", "", "danger-zone");
  dangerZone.append(
    element("p", "删除后无法恢复，请确认这些记录不再需要。", "danger-hint"),
    deleteMistakeBtn,
    deleteProblemBtn
  );

  const reviewSection = element("section", "", "review-section");
  const reviewButtons = element("div", "", "actions review-actions");
  const due = item.due_date <= item.today;
  reviewSection.append(
    element("h3", "这次，你掌握得怎么样？"),
    element(
      "p",
      due
        ? "先回忆，再对照。按真实感受评分，下次复习会据此安排。"
        : "还没到复习日期。先回看笔记或练一道变体题，到期后就能评分。",
      "muted"
    )
  );

  for (const [quality, label] of grades) {
    const button = element("button", label);
    button.type = "button";
    button.dataset.quality = String(quality);
    button.dataset.blocked = due ? "0" : "1";
    button.disabled = !due;
    button.addEventListener("click", () => run(async () => {
      const state = await api(`/api/mistakes/${item.id}/review`, {
        method: "POST",
        body: JSON.stringify({ quality, version: item.version }),
      });
      await loadList();
      message(`评分已保存。${state.due_date} 再来复习这条易错点。`);
    }));
    reviewButtons.append(button);
  }
  reviewSection.append(reviewButtons);
  root.append(reviewSection);

  if (item.reviews.length) {
    const history = document.createElement("details");
    history.className = "review-history";
    history.append(element("summary", `复习历史（${item.reviews.length} 次）`));
    const list = document.createElement("ul");
    for (const review of item.reviews) {
      list.append(element(
        "li",
        `${timestamp(review.reviewed_at)} · 评分 ${review.quality}/5` +
        ` · 下次 ${review.next_due_date}`
      ));
    }
    history.append(list);
    root.append(history);
  }

  const aiSection = element("section", "", "ai-section");
  aiSection.append(
    element("h3", "诊断错因，再练两道"),
    element(
      "p",
      "生成时会把这道题的代码（或解题过程）、思路发送给 OpenAI；" +
      "错因没填时 AI 会自己反推，已经填了则作为参考。" +
      `每天最多 ${user.ai_daily_limit} 次尝试，失败也计入次数。`,
      "muted"
    ),
    element(
      "p",
      isCodeZone
        ? "每次生成两道题。请先核对题意，参考样例自行解答并记录结果；代码仅保存，不运行或自动判题。"
        : "每次生成两道题。提交最终答案后，系统会比对参考答案并显示判定；如有疑问，请自行核对参考答案。",
      "muted"
    )
  );

  const variants = element("div");
  const empty = element("p", "还没有变体题。练两道新题，检查自己是否真的理解了。", "muted");
  if (!item.variants.length) variants.append(empty);
  for (const variant of item.variants) {
    variants.append(renderVariant(variant, isCodeZone));
  }

  const generate = element(
    "button",
    user.ai_enabled ? "诊断错因并出两道新题" : "AI 尚未配置，暂时无法出题",
    "primary"
  );
  generate.type = "button";
  generate.dataset.blocked = user.ai_enabled ? "0" : "1";
  generate.disabled = !user.ai_enabled;

  generate.addEventListener("click", () => run(async () => {
    message("AI 正在诊断错因、准备两道新题，可能需要一两分钟。请保持页面打开。");
    const generated = await api(`/api/mistakes/${item.id}/variants`, {
      method: "POST",
    });
    empty.remove();
    for (const variant of generated.variants) {
      variants.prepend(renderVariant(variant, isCodeZone));
    }
    if (generated.mistake_description !== item.description) {
      item.description = generated.mistake_description;
      const updated = renderMistakeText(item);
      mistakeTextNode.replaceWith(updated);
      mistakeTextNode = updated;
    }
    message("两道新题已生成并保存。");
  }));

  aiSection.append(generate, variants);
  root.append(aiSection, dangerZone);
}

function addMistakeInput() {
  const container = $("#mistake-inputs");
  if (container.children.length >= 10) {
    message("每道题最多添加 10 条易错点", true);
    return;
  }

  const row = element("div", "", "mistake-row");
  const input = textarea("", 2000, 3);
  input.name = "mistake";
  input.placeholder = "不确定的话可以留空，AI 会从代码和思路里帮你反推错因。";

  const remove = element("button", "移除这条输入");
  remove.type = "button";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
  });

  row.append(field("哪里容易错，为什么会错？（可选）", input), remove);
  container.append(row);
}

$("#login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify(formObject(form)),
    });
    form.reset();
    await enterApp();
    message("已登录，今天的复习已经准备好了。");
  });
});

$("#register-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(formObject(form)),
    });
    form.reset();
    await enterApp();
    message("账号已创建。去「新增记录」留下你的第一条错因吧。");
  });
});

$("#trial-start").addEventListener("click", () => run(async () => {
  await api("/api/auth/trial", {
    method: "POST",
    body: JSON.stringify({
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
    }),
  });
  await enterApp();
  message("已进入体验账号，随便试试看吧——数据可能会被定期清理。");
}));

$("#forgot-link").addEventListener("click", (event) => {
  event.preventDefault();
  message();
  showAuthPanels(["forgot-form"]);
});

$("#back-to-login-link").addEventListener("click", (event) => {
  event.preventDefault();
  message();
  showAuthPanels(["login-form", "register-form"]);
});

$("#forgot-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    await api("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email: form.email.value }),
    });
    form.reset();
    showAuthPanels(["login-form", "register-form"]);
    message("如果这个邮箱注册过账号，重置邮件已经发出，请查收（包括垃圾邮件文件夹）。");
  });
});

$("#reset-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    try {
      await api("/api/auth/reset-password", {
        method: "POST",
        body: JSON.stringify({
          token: resetToken || "",
          password: form.password.value,
        }),
      });
    } catch (error) {
      if (error.status === 400) {
        history.replaceState(null, "", location.pathname);
        showAuthPanels(["forgot-form"]);
        message("重置链接无效或已过期，请重新申请。", true);
        return;
      }
      throw error;
    }
    form.reset();
    history.replaceState(null, "", location.pathname);
    showAuthPanels(["login-form", "register-form"]);
    message("密码已重置，请用新密码登录。");
  });
});

$("#email-prompt").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    const updated = await api("/api/me/email", {
      method: "PUT",
      body: JSON.stringify({ email: form.email.value }),
    });
    user.email = updated.email;
    $("#email-prompt").hidden = true;
    form.reset();
    message("邮箱绑定成功。");
  });
});

$("#logout").addEventListener("click", () => run(async () => {
  await api("/api/auth/logout", { method: "POST" });
  signedOut();
  message("已退出登录。");
}));

document.querySelectorAll("[data-view]").forEach((button) => {
  button.addEventListener("click", () => run(async () => {
    message();
    await showView(button.dataset.view);
  }));
});

$("#zone-filter").addEventListener("change", () => run(loadList));

$("#problem-zone").addEventListener("change", (event) => {
  applyZoneFieldMode($("#problem-form"), event.currentTarget.value);
});

$("#avatar-file-input").addEventListener("change", (event) => {
  const file = event.currentTarget.files[0];
  if (!file) return;
  run(async () => {
    const result = await uploadAvatarFile(file);
    user.avatar_version = result.avatar_version;
    updateUserInfo();
    event.currentTarget.value = "";
    message("头像已更新。");
  });
});

$("#remove-avatar-btn").addEventListener("click", () => run(async () => {
  const result = await api("/api/me/avatar", { method: "DELETE" });
  user.avatar_version = result.avatar_version;
  updateUserInfo();
  message("头像已移除。");
}));

$("#refresh").addEventListener("click", () => run(async () => {
  if (view === "admin") {
    message();
    await loadAdminReports();
    message("已刷新。");
    return;
  }
  if (view === "forum") {
    message();
    if (forumPost) await openForumPost(forumPost.id);
    else await showForumList();
    message("已刷新。");
    return;
  }
  if (view === "leaderboard") {
    message();
    await loadLeaderboard();
    message("已刷新。");
    return;
  }
  if (view === "plan") {
    message();
    await loadPlanPage();
    return;
  }
  if (view !== "new") await loadList();
  message(view === "new" ? "请先保存当前记录。" : "已刷新。");
}));

$("#plan-back").addEventListener("click", () => run(async () => {
  stopOrderPolling();
  planPurchase = null;
  message();
  await loadPlanPage();
}));

$("#add-mistake").addEventListener("click", addMistakeInput);

$("#problem-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;

  run(async () => {
    const data = new FormData(form);
    const created = await api("/api/problems", {
      method: "POST",
      body: JSON.stringify({
        title: data.get("title"),
        zone: data.get("zone"),
        language: data.get("language"),
        code: data.get("code"),
        thinking: data.get("thinking"),
        mistakes: data.getAll("mistake"),
      }),
    });

    form.reset();
    applyZoneFieldMode(form, form.zone.value);
    $("#mistake-inputs").replaceChildren();
    addMistakeInput();
    await showView("today");
    await openMistake(created.mistake_ids[0]);
    message("记录已保存，新的易错点已加入今日复习。");
  });
});

async function showForumList() {
  forumPost = null;
  $("#forum-compose").hidden = true;
  $("#forum-detail").hidden = true;
  $("#forum-list").hidden = false;
  $("#forum-new-post-btn").hidden = user.is_trial;
  await loadForumPosts();
}

async function loadForumPosts() {
  const currentUser = user;
  const status = $("#forum-list-status");
  const list = $("#forum-posts");
  status.textContent = "正在加载帖子列表…";
  list.replaceChildren();
  const { posts } = await api("/api/posts");
  if (user !== currentUser || !user || view !== "forum") return;
  status.textContent = posts.length ? "" : "还没有帖子，来发第一条吧。";
  for (const post of posts) {
    const row = element("button", "", "record-button");
    row.type = "button";
    const authorLine = element("div", "", "author-line muted");
    authorLine.append(
      avatarElement(post.user_id, post.username, post.avatar_version, { small: true }),
      element(
        "small",
        `${post.username} · ${timestamp(post.created_at)} · ${post.comment_count} 条评论`
      )
    );
    row.append(element("strong", post.title), authorLine);
    row.addEventListener("click", () => run(() => openForumPost(post.id)));
    list.append(row);
  }
}

function showForumCompose() {
  $("#forum-list").hidden = true;
  $("#forum-detail").hidden = true;
  $("#forum-compose").hidden = false;
}

async function openForumPost(postId) {
  const post = await api(`/api/posts/${postId}`);
  forumPost = post;
  $("#forum-list").hidden = true;
  $("#forum-compose").hidden = true;
  $("#forum-detail").hidden = false;
  renderForumPost(post);
  renderForumComments(post.comments);
}

function renderForumPost(post) {
  const root = $("#forum-post");

  function readOnly() {
    root.replaceChildren();
    const authorLine = element("p", "", "muted author-line");
    authorLine.append(
      avatarElement(post.user_id, post.username, post.avatar_version, { small: true }),
      element(
        "span",
        `${post.username} · ${timestamp(post.created_at)}` +
          (post.updated_at ? `（编辑于 ${timestamp(post.updated_at)}）` : "")
      )
    );
    root.append(
      element("h2", post.title),
      authorLine,
      element("p", post.body, "multiline")
    );
    if (user.id === post.user_id) {
      const editBtn = element("button", "编辑");
      editBtn.type = "button";
      editBtn.addEventListener("click", editForm);
      const deleteBtn = element("button", "删除这条帖子", "danger");
      deleteBtn.type = "button";
      deleteBtn.addEventListener("click", () => run(async () => {
        if (!confirm("删除这条帖子？帖子下的评论也会一起不可见，无法恢复。")) return;
        await api(`/api/posts/${post.id}`, { method: "DELETE" });
        message("已删除这条帖子。");
        await showForumList();
      }));
      const actions = element("div", "", "actions");
      actions.append(editBtn, deleteBtn);
      root.append(actions);
    } else if (!user.is_trial) {
      const actions = element("div", "", "actions");
      const reportBtn = element("button", "举报这条帖子");
      reportBtn.type = "button";
      reportBtn.addEventListener("click", () => actions.replaceWith(reportForm()));
      const reportAvatarBtn = element("button", "举报头像");
      reportAvatarBtn.type = "button";
      reportAvatarBtn.addEventListener("click", () =>
        actions.replaceWith(avatarReportForm(post.user_id, readOnly))
      );
      actions.append(reportBtn, reportAvatarBtn);
      root.append(actions);
    }
  }

  function editForm() {
    const title = document.createElement("input");
    title.value = post.title;
    title.maxLength = 200;
    title.required = true;

    const body = textarea(post.body, 8000, 8);
    body.required = true;

    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("标题", title), field("正文", body), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/posts/${post.id}`, {
          method: "PUT",
          body: JSON.stringify({ title: title.value, body: body.value }),
        });
        post.title = updated.title;
        post.body = updated.body;
        post.updated_at = updated.updated_at;
        message("帖子已更新。");
        readOnly();
      });
    });

    root.replaceChildren(form);
  }

  // 只替换"举报"按钮所在的操作区，不动上面已经展示的标题和正文。
  function reportForm() {
    const reason = textarea("", 500, 3);
    const submit = element("button", "提交举报", "primary");
    submit.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("举报原因（可选）", reason), submit, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/posts/${post.id}/report`, {
          method: "POST",
          body: JSON.stringify({ reason: reason.value }),
        });
        message("已提交举报，管理员会尽快处理。");
        readOnly();
      });
    });

    return form;
  }

  readOnly();
}

function renderForumComment(comment) {
  const wrap = element("div", "", "forum-comment");

  function readOnly() {
    wrap.replaceChildren();
    const meta = element("p", "", "muted forum-comment-meta author-line");
    meta.append(
      avatarElement(comment.user_id, comment.username, comment.avatar_version, { small: true }),
      element(
        "span",
        `${comment.username} · ${timestamp(comment.created_at)}` +
          (comment.updated_at ? `（编辑于 ${timestamp(comment.updated_at)}）` : "")
      )
    );
    wrap.append(meta, element("p", comment.body, "multiline"));
    if (user.id === comment.user_id) {
      const editBtn = element("button", "编辑");
      editBtn.type = "button";
      editBtn.addEventListener("click", editForm);
      const deleteBtn = element("button", "删除", "danger");
      deleteBtn.type = "button";
      deleteBtn.addEventListener("click", () => run(async () => {
        if (!confirm("删除这条评论？无法恢复。")) return;
        await api(`/api/comments/${comment.id}`, { method: "DELETE" });
        wrap.remove();
        message("已删除这条评论。");
      }));
      const actions = element("div", "", "actions");
      actions.append(editBtn, deleteBtn);
      wrap.append(actions);
    } else if (!user.is_trial) {
      const actions = element("div", "", "actions");
      const reportBtn = element("button", "举报");
      reportBtn.type = "button";
      reportBtn.addEventListener("click", () => actions.replaceWith(reportForm()));
      const reportAvatarBtn = element("button", "举报头像");
      reportAvatarBtn.type = "button";
      reportAvatarBtn.addEventListener("click", () =>
        actions.replaceWith(avatarReportForm(comment.user_id, readOnly))
      );
      actions.append(reportBtn, reportAvatarBtn);
      wrap.append(actions);
    }
  }

  function editForm() {
    const body = textarea(comment.body, 2000, 3);
    body.required = true;
    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("评论内容", body), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/comments/${comment.id}`, {
          method: "PUT",
          body: JSON.stringify({ body: body.value }),
        });
        comment.body = updated.body;
        comment.updated_at = updated.updated_at;
        message("评论已更新。");
        readOnly();
      });
    });

    wrap.replaceChildren(form);
  }

  function reportForm() {
    const reason = textarea("", 500, 3);
    const submit = element("button", "提交举报", "primary");
    submit.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("举报原因（可选）", reason), submit, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/comments/${comment.id}/report`, {
          method: "POST",
          body: JSON.stringify({ reason: reason.value }),
        });
        message("已提交举报，管理员会尽快处理。");
        readOnly();
      });
    });

    return form;
  }

  readOnly();
  return wrap;
}

function renderForumComments(comments) {
  const list = $("#forum-comments");
  list.replaceChildren();
  if (!comments.length) {
    list.append(element("p", "还没有评论，来发表第一条看法吧。", "muted"));
  }
  for (const comment of comments) {
    list.append(renderForumComment(comment));
  }
  $("#forum-comment-form").hidden = user.is_trial;
}

function removeReportCard(card) {
  card.remove();
  if (!$("#admin-reports").children.length) {
    $("#admin-status").textContent = "暂无待处理的举报。";
  }
}

async function loadAdminReports() {
  const currentUser = user;
  const status = $("#admin-status");
  const list = $("#admin-reports");
  status.textContent = "正在加载举报队列…";
  list.replaceChildren();
  const { reports } = await api("/api/admin/reports");
  if (user !== currentUser || !user || view !== "admin") return;
  status.textContent = reports.length ? "" : "暂无待处理的举报。";
  for (const report of reports) {
    list.append(renderAdminReport(report));
  }
}

function renderAdminReport(report) {
  if (report.type === "avatar") return renderAdminAvatarReport(report);

  const isPost = report.type === "post";
  const kind = isPost ? "帖子" : "评论";
  const authorId = isPost ? report.post_author_id : report.comment_author_id;
  const authorName = isPost
    ? report.post_author_username
    : report.comment_author_username;
  const deletedAt = isPost ? report.post_deleted_at : report.comment_deleted_at;
  const contentPreview = isPost
    ? `${report.post_title}\n${report.post_body}`
    : report.comment_body;

  const card = element("article", "", "panel admin-report");
  card.append(
    element("h3", `举报的${kind} · 作者 ${authorName}`),
    element(
      "p",
      `举报人：${report.reporter_username} · ${timestamp(report.created_at)}`,
      "muted"
    )
  );
  if (report.reason) {
    card.append(element("p", `举报原因：${report.reason}`));
  }
  card.append(element("pre", contentPreview, "prose"));
  if (deletedAt) {
    card.append(element("p", "该内容已被作者自行删除，只能忽略这条举报。", "muted"));
  }

  const actions = element("div", "", "actions");
  const deleteBtn = element("button", `删除这条${kind}`, "danger");
  deleteBtn.type = "button";
  deleteBtn.disabled = Boolean(deletedAt);
  deleteBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`删除这条${kind}？无法恢复，关联的举报会一并标记为已处理。`)) return;
    const path = isPost
      ? `/api/admin/posts/${report.post_id}`
      : `/api/admin/comments/${report.comment_id}`;
    await api(path, { method: "DELETE" });
    removeReportCard(card);
    message("已删除，举报已处理。");
  }));

  const dismissBtn = element("button", "忽略举报");
  dismissBtn.type = "button";
  dismissBtn.addEventListener("click", () => run(async () => {
    await api(`/api/admin/reports/${report.id}/resolve`, { method: "POST" });
    removeReportCard(card);
    message("已忽略这条举报。");
  }));

  const banBtn = element("button", `封禁 ${authorName}`, "danger");
  banBtn.type = "button";
  banBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`封禁账号「${authorName}」？该账号将无法再登录。`)) return;
    await api(`/api/admin/users/${authorId}/ban`, { method: "POST" });
    message(`已封禁 ${authorName}。`);
  }));

  actions.append(deleteBtn, dismissBtn, banBtn);
  card.append(actions);
  return card;
}

function renderAdminAvatarReport(report) {
  const authorId = report.avatar_owner_id;
  const authorName = report.avatar_owner_username;

  const card = element("article", "", "panel admin-report");
  card.append(
    element("h3", `举报的头像 · 用户 ${authorName}`),
    element(
      "p",
      `举报人：${report.reporter_username} · ${timestamp(report.created_at)}`,
      "muted"
    )
  );
  if (report.reason) {
    card.append(element("p", `举报原因：${report.reason}`));
  }
  const preview = avatarElement(authorId, authorName, report.avatar_owner_avatar_version);
  preview.style.width = "96px";
  preview.style.height = "96px";
  preview.style.fontSize = "36px";
  card.append(preview);
  if (!(report.avatar_owner_avatar_version > 0)) {
    card.append(element("p", "该用户目前没有自定义头像（可能已被清除或本人移除），只能忽略这条举报。", "muted"));
  }

  const actions = element("div", "", "actions");
  const clearBtn = element("button", "清除该头像", "danger");
  clearBtn.type = "button";
  clearBtn.disabled = !(report.avatar_owner_avatar_version > 0);
  clearBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`清除「${authorName}」的头像？无法恢复，关联的举报会一并标记为已处理。`)) return;
    await api(`/api/admin/users/${authorId}/avatar`, { method: "DELETE" });
    removeReportCard(card);
    message("已清除头像，举报已处理。");
  }));

  const dismissBtn = element("button", "忽略举报");
  dismissBtn.type = "button";
  dismissBtn.addEventListener("click", () => run(async () => {
    await api(`/api/admin/avatar-reports/${report.id}/resolve`, { method: "POST" });
    removeReportCard(card);
    message("已忽略这条举报。");
  }));

  const banBtn = element("button", `封禁 ${authorName}`, "danger");
  banBtn.type = "button";
  banBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`封禁账号「${authorName}」？该账号将无法再登录。`)) return;
    await api(`/api/admin/users/${authorId}/ban`, { method: "POST" });
    message(`已封禁 ${authorName}。`);
  }));

  actions.append(clearBtn, dismissBtn, banBtn);
  card.append(actions);
  return card;
}

$("#forum-new-post-btn").addEventListener("click", () => {
  message();
  showForumCompose();
});

$("#forum-compose-cancel").addEventListener("click", () => run(async () => {
  message();
  await showForumList();
}));

$("#forum-compose-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    const data = new FormData(form);
    const created = await api("/api/posts", {
      method: "POST",
      body: JSON.stringify({
        title: data.get("title"),
        body: data.get("body"),
      }),
    });
    form.reset();
    await openForumPost(created.id);
    message("帖子已发布。");
  });
});

$("#forum-back").addEventListener("click", () => run(async () => {
  message();
  await showForumList();
}));

$("#forum-comment-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    const data = new FormData(form);
    const created = await api(`/api/posts/${forumPost.id}/comments`, {
      method: "POST",
      body: JSON.stringify({ body: data.get("body") }),
    });
    form.reset();
    forumPost.comments.push(created);
    renderForumComments(forumPost.comments);
    message("评论已发表。");
  });
});

$("#timezone").value =
  Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";

addMistakeInput();

const resetToken = new URLSearchParams(location.search).get("reset_token");

if (resetToken) {
  // 从密码重置邮件点进来的，不管当前是否登录，先处理重置。
  showAuthPanels(["reset-form"]);
} else {
  run(async () => {
    try {
      await enterApp();
    } catch (error) {
      if (error.status !== 401) throw error;
      message("已有账号可以直接登录；首次使用，请准备好邀请码。");
    }
  });
}
