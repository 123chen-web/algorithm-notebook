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

const ORDER_POLL_INTERVAL = 3000;
const ORDER_POLL_DURATION = 5 * 60 * 1000;

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
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
  $("#plan-payment").replaceChildren();
  $("#plan-order-details").replaceChildren();
  $("#plan-order-status").textContent = "";
  $("#plan-order").hidden = true;
  user = null;
  $("#auth").hidden = false;
  $("#app").hidden = true;
  $("#logout").hidden = true;
  $("#user-info").textContent = "";
  $("#email-prompt").hidden = true;
  $("#trial-banner").hidden = true;
  showAuthPanels(["login-form", "register-form"]);
  $("#cards").replaceChildren();
  $("#detail").replaceChildren();
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
    }

    const error = new Error(String(detail));
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

function updateUserInfo() {
  $("#user-info").textContent =
    `${user.username} · ${user.timezone} · ${user.today}`;
  $("#email-prompt").hidden = Boolean(user.email) || Boolean(user.is_trial);
  $("#trial-banner").hidden = !user.is_trial;
}

async function enterApp() {
  user = await api("/api/me");
  $("#auth").hidden = true;
  $("#app").hidden = false;
  $("#logout").hidden = false;
  updateUserInfo();
  await showView("today");
}

async function showView(nextView) {
  stopOrderPolling();
  view = nextView;
  $("#new-page").hidden = view !== "new";
  $("#list-page").hidden = view !== "today" && view !== "all";
  $("#plan-page").hidden = view !== "plan";

  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  });

  if (view === "plan") await loadPlanPage();
  else if (view === "today" || view === "all") await loadList();
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
  user = await api("/api/me");
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
        if (purchase.order.status === "pending") startOrderPolling();
        else await finishPlanOrder();
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
  // 支付内容来自接口，仅允许预期的链接协议；原文始终以文本节点展示。
  if (/^(https?:\/\/|alipays?:\/\/|mock:\/\/)/i.test(paymentText)) {
    const link = element("a", isMock ? "Mock 支付链接（仅占位）" : "打开支付宝完成支付", "plan-payment-link");
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
    : "手机上点击链接直接跳转支付宝完成支付；电脑上可先将上面的原始文本生成二维码，再用支付宝扫一扫。当前页面暂不提供图形二维码，不能直接扫描这段文字。",
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
  if (status === "paid") {
    await refreshPlanSubscription();
    message("购买成功，套餐已生效。");
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
  if (order.status !== "pending") await finishPlanOrder();
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
    if (Date.now() >= deadline) {
      stopOrderPolling();
      const text = "暂未检测到支付结果，可以稍后刷新这个页面查看。";
      $("#plan-order-status").textContent = text;
      message(text);
      return;
    }
    // run 会串行处理异步操作；其他操作进行中时跳过本次查询。
    if (busy) return;
    run(async () => {
      await checkPlanOrder(generation);
    });
  }, ORDER_POLL_INTERVAL);
}

async function loadPlanPage() {
  stopOrderPolling();
  await refreshPlanSubscription();
  if (user.is_trial) planPurchase = null;
  const { plans } = await api("/api/plans");
  renderPlans(plans);
  renderPlanOrder();
  if (planPurchase) {
    if (planPurchase.order.status === "pending") startOrderPolling();
    await checkPlanOrder(orderPollGeneration);
  }
}

async function loadList() {
  const data = await api(`/api/mistakes?due_only=${view === "today"}`);
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
      element("span", item.description, "record-description"),
      element("small", `${item.due_date <= data.today ? "待复习" : "下次复习"} · ${item.due_date}`, "muted")
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

function renderVariant(variant) {
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
  const result = document.createElement("select");

  for (const [value, label] of Object.entries(resultLabels)) {
    const option = element("option", label);
    option.value = value;
    result.append(option);
  }
  result.value = variant.result;

  const code = textarea(variant.answer_code, 40000, 7, true);
  const notes = textarea(variant.notes, 8000, 3);
  const save = element("button", "保存练习结果", "primary");
  save.type = "submit";

  form.append(
    field("练习结果", result),
    field("我的解答代码 · 仅保存，不运行", code),
    field("这次还犯了同样的错误吗？", notes),
    save
  );

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      const updated = await api(`/api/variants/${variant.id}/result`, {
        method: "PUT",
        body: JSON.stringify({
          result: result.value,
          answer_code: code.value,
          notes: notes.value,
        }),
      });
      summary.textContent =
        `变体 #${updated.id} · ${resultLabels[updated.result]}`;
      savedAt.textContent =
        `结果保存于：${timestamp(updated.result_updated_at)}`;
      message("练习结果已保存。这次练习不会改变复习日期。");
    });
  });

  box.append(
    summary,
    element("pre", variant.description, "prose"),
    savedAt,
    form
  );
  return box;
}

function renderMistakeText(item) {
  const wrap = element("div", "", "mistake-text");

  function readOnly() {
    const editBtn = element("button", "编辑这条错因");
    editBtn.type = "button";
    editBtn.addEventListener("click", editForm);
    wrap.replaceChildren(element("p", item.description, "multiline"), editBtn);
  }

  function editForm() {
    const input = textarea(item.description, 2000, 4);
    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("哪里容易错，为什么会错？", input), save, cancel);
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
      element("h4", "当时的代码"),
      element("pre", item.code, "code"),
      editBtn
    );
  }

  function editForm() {
    const title = document.createElement("input");
    title.value = item.title;
    title.maxLength = 200;
    title.required = true;

    const language = document.createElement("input");
    language.value = item.language;
    language.maxLength = 40;
    language.required = true;

    const code = textarea(item.code, 40000, 10, true);
    const thinking = textarea(item.thinking, 8000, 4);

    const save = element("button", "保存题目信息", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(
      field("标题", title),
      field("编程语言", language),
      field("代码", code),
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

  const heading = element("div", "", "detail-heading");
  heading.append(
    element("h2", item.title),
    element("p", item.language, "language-badge")
  );
  root.append(
    heading,
    element("h3", "这次需要记住的错因", "section-label"),
    renderMistakeText(item),
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
    element("h3", "同一薄弱点，再练一道"),
    element(
      "p",
      "生成时会将这道题的代码、思路和当前易错点发送给 OpenAI。" +
      `每天最多 ${user.ai_daily_limit} 次尝试，失败也计入次数。`,
      "muted"
    ),
    element(
      "p",
      "生成后请先核对题意，自己解答，再记录练习结果。这里不会运行代码或自动判题。",
      "muted"
    )
  );

  const variants = element("div");
  const empty = element("p", "还没有变体题。换一道题，检查自己是否真的理解了。", "muted");
  if (!item.variants.length) variants.append(empty);
  for (const variant of item.variants) {
    variants.append(renderVariant(variant));
  }

  const generate = element(
    "button",
    user.ai_enabled ? "围绕这个错因，出一道新题" : "AI 尚未配置，暂时无法出题",
    "primary"
  );
  generate.type = "button";
  generate.dataset.blocked = user.ai_enabled ? "0" : "1";
  generate.disabled = !user.ai_enabled;

  generate.addEventListener("click", () => run(async () => {
    message("AI 正在围绕这条错因出题，可能需要一两分钟。请保持页面打开。");
    const variant = await api(`/api/mistakes/${item.id}/variants`, {
      method: "POST",
    });
    empty.remove();
    variants.prepend(renderVariant(variant));
    message("变体题已生成并保存。");
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
  input.required = true;
  input.placeholder = "例如：忘了检查数据范围，用 int 存 10¹⁰ 导致溢出。下次先估算范围，再选类型。";

  const remove = element("button", "移除这条输入");
  remove.type = "button";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
  });

  row.append(field("哪里容易错，为什么会错？", input), remove);
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

$("#refresh").addEventListener("click", () => run(async () => {
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
        language: data.get("language"),
        code: data.get("code"),
        thinking: data.get("thinking"),
        mistakes: data.getAll("mistake"),
      }),
    });

    form.reset();
    $("#mistake-inputs").replaceChildren();
    addMistakeInput();
    await showView("today");
    await openMistake(created.mistake_ids[0]);
    message("记录已保存，新的易错点已加入今日复习。");
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
