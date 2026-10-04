"use strict";

/* 套餐页视图：纯函数（model / formatPrice / daysLeft）和用 DOM API 的渲染函数。
   数据获取、下单、轮询、退款仍在 app.js，这里只负责把数据画出来。 */
(() => {
  const DAY_MS = 86400000;
  const DEFAULT_FREE_LIMIT = 10;
  const CHANNEL_NAMES = { alipay: "支付宝", wechat: "微信支付", mock: "Mock" };
  const ORDER_STATUS = {
    pending: ["待支付", "pending"],
    paid: ["已支付", "paid"],
    failed: ["支付失败", "warn"],
    closed: ["已关闭", "warn"],
    refunded: ["已退款", "warn"],
  };

  function formatPrice(cents) {
    const value = Number(cents);
    if (!Number.isFinite(value)) return "¥0.00";
    return `¥${(Math.round(value) / 100).toFixed(2)}`;
  }

  /** 距到期还剩几天，向上取整；无效或已过期返回 0。 */
  function daysLeft(expiresIso, nowMs) {
    const expires = Date.parse(expiresIso);
    if (!Number.isFinite(expires) || !Number.isFinite(nowMs)) return 0;
    const diff = expires - nowMs;
    return diff > 0 ? Math.ceil(diff / DAY_MS) : 0;
  }

  function multiplier(limit, freeLimit) {
    if (!(limit > freeLimit) || !(freeLimit > 0)) return "";
    const ratio = limit / freeLimit;
    return Number.isInteger(ratio) ? String(ratio) : ratio.toFixed(1);
  }

  function model(plans, me, now) {
    const user = me || {};
    const nowMs = now instanceof Date ? now.getTime() : Number(now ?? Date.now());
    const list = Array.isArray(plans) ? plans : [];
    const expiresMs = Date.parse(user.plan_expires_at);
    const active = Boolean(user.plan_active) && Number.isFinite(expiresMs) && expiresMs > nowMs;
    const left = active ? daysLeft(user.plan_expires_at, nowMs) : 0;
    const limit = Math.max(0, Number(user.ai_daily_limit) || 0);
    const used = Math.max(0, Number(user.ai_daily_used) || 0);
    const remaining = user.ai_daily_remaining == null ? Math.max(0, limit - used) : Math.max(0, Number(user.ai_daily_remaining) || 0);
    // 免费额度：只有"正式账号且当前没有有效套餐"时 ai_daily_limit 才是免费额度。
    const freeLimit = !active && !user.is_trial && limit > 0 ? limit : DEFAULT_FREE_LIMIT;

    const paid = list.filter((plan) => plan && plan.id != null);
    const priced = paid.filter((plan) => Number(plan.price_cents) > 0);
    let recommendedId = null;
    if (paid.length > 1 && priced.length) {
      recommendedId = priced.reduce((best, plan) => (Number(plan.price_cents) < Number(best.price_cents) ? plan : best)).id;
    }

    const cards = [{
      id: "free", free: true, name: "免费版", priceText: "¥0 · 永久",
      perDayText: `每天 ${freeLimit} 次 AI 生成`, multiplierText: "", periodText: "",
      recommended: false, current: !active, purchasable: false,
    }];
    for (const plan of paid) {
      const planLimit = Number(plan.ai_daily_limit) || 0;
      const ratio = multiplier(planLimit, freeLimit);
      cards.push({
        id: plan.id,
        name: String(plan.name ?? ""),
        priceText: `${formatPrice(plan.price_cents)} / ${plan.period_days} 天`,
        perDayText: `每天 ${planLimit} 次 AI 生成`,
        multiplierText: ratio ? `是免费版的 ${ratio} 倍` : "",
        periodText: `${plan.period_days} 天有效，到期前续费会顺延`,
        recommended: plan.id === recommendedId,
        current: active && user.plan_id === plan.id,
        purchasable: Boolean(plan.purchasable),
      });
    }
    return {
      freeLimit,
      status: { active, daysLeft: left, expiresAt: active ? user.plan_expires_at : null, urgent: active && left <= 3 },
      usage: {
        used, limit, remaining,
        ratio: limit > 0 ? Math.min(1, used / limit) : 1,
        exhausted: remaining <= 0,
      },
      cards,
    };
  }

  function node(tag, text = "", className = "") {
    const item = document.createElement(tag);
    if (text) item.textContent = text;
    if (className) item.className = className;
    return item;
  }

  function dateText(iso, timeZone) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    try {
      return new Intl.DateTimeFormat("sv-SE", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
    } catch {
      return date.toISOString().slice(0, 10);
    }
  }

  function usageBar(usage) {
    const wrap = node("div", "", "pl-usage");
    if (usage.exhausted) wrap.dataset.exhausted = "true";
    const label = node("p", `今日 AI ${usage.used} / ${usage.limit} 次 · 午夜重置`, "pl-usage-label");
    const track = node("div", "", "pl-meter");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "今日 AI 用量");
    track.setAttribute("aria-valuemin", "0");
    track.setAttribute("aria-valuemax", String(usage.limit));
    track.setAttribute("aria-valuenow", String(Math.min(usage.used, usage.limit)));
    const fill = node("span", "", "pl-meter-fill");
    fill.style.setProperty("--pl-fill", `${Math.round(usage.ratio * 100)}%`);
    track.append(fill);
    wrap.append(label, track);
    if (usage.exhausted) wrap.append(node("p", "今天的次数用完了，明天零点恢复", "pl-usage-warning"));
    return wrap;
  }

  /** 当前状态区：免费版 / 有效套餐 + 用量条。 */
  function renderStatus(host, view, me, plans) {
    const { status, usage } = view;
    const box = node("div", "", "pl-status");
    const head = node("div", "", "pl-status-head");
    if (status.active) {
      const current = (plans || []).find((plan) => plan.id === me.plan_id);
      head.append(node("span", me.plan_name || current?.name || "当前套餐", "pl-pill pl-pill-plan"));
      const expiry = node("p", `到期 ${dateText(status.expiresAt, me.timezone)} · 还剩 ${status.daysLeft} 天`, "pl-expiry");
      if (status.urgent) expiry.dataset.urgent = "true";
      head.append(expiry);
    } else {
      head.append(node("h4", me.is_trial ? "体验账号" : "免费版", "pl-status-title"));
    }
    box.append(head, usageBar(usage));
    if (status.active) {
      if (status.urgent) box.append(node("p", "套餐即将到期，续费即可顺延", "pl-renew-hint"));
      box.append(node("p", "提前续费会顺延，不浪费剩余天数。", "pl-note"));
    }
    host.replaceChildren(box);
  }

  function featureList(items) {
    const list = node("ul", "", "pl-features");
    for (const text of items) list.append(node("li", text));
    return list;
  }

  /** 套餐卡片行。handlers.onBuy(planId) 下单；handlers.onHow() 滚到"开通方式"。 */
  function renderCards(host, view, plans, options) {
    const { isTrial = false, onBuy, onHow } = options || {};
    const byId = new Map((plans || []).map((plan) => [plan.id, plan]));
    const cards = view.cards.map((card) => {
      const article = node("article", "", "pl-card");
      if (card.free) article.classList.add("pl-card-free");
      if (card.recommended) article.classList.add("pl-card-featured");
      if (card.current) article.classList.add("pl-card-current");
      const badges = node("div", "", "pl-badges");
      if (card.recommended) badges.append(node("span", "推荐", "pl-badge pl-badge-featured"));
      if (card.current) badges.append(node("span", card.free ? "当前使用" : "当前套餐", "pl-badge"));
      const priceParts = card.priceText.split(" / ");
      const price = node("p", "", "pl-price");
      price.append(node("strong", priceParts[0]));
      if (priceParts[1]) price.append(node("span", ` / ${priceParts[1]}`, "pl-price-unit"));
      article.append(badges, node("h4", card.name, "pl-card-name"), price);
      if (card.free) {
        article.append(featureList([card.perDayText, "全部复习、统计、小组、讨论区功能"]));
      } else {
        article.append(featureList([
          card.multiplierText ? `${card.perDayText}（${card.multiplierText}）` : card.perDayText,
          "其余功能与免费版相同",
          card.periodText,
          "退款：官方支付的订单可在“我的订单”自助全额退款（当天已用的 AI 次数不退）；手动付款请联系站长",
        ]));
        if (!isTrial) {
          const live = card.purchasable;
          const button = node("button", live ? "购买" : "开通方式", live ? "primary pl-card-action" : "pl-card-action");
          button.type = "button";
          button.setAttribute("aria-label", live ? `购买${card.name}` : `${card.name}的开通方式`);
          button.addEventListener("click", () => (live ? onBuy?.(byId.get(card.id)) : onHow?.()));
          article.append(button);
        }
      }
      return article;
    });
    host.replaceChildren(...cards);
  }

  /** 平滑滚动到某个元素；减少动效偏好下直接跳转。 */
  function scrollTo(target) {
    if (!target?.scrollIntoView) return;
    const reduce = Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)").matches);
    target.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "start" });
  }

  /** 订单：桌面是紧凑表格，手机上由样式堆成卡片。 */
  function renderOrders(host, orders, options) {
    const { yuan = formatPrice, time = (value) => value, onRefund, busy = false } = options || {};
    if (!orders.length) {
      const empty = node("div", "", "pl-empty");
      empty.append(node("span", "空", "pl-stamp"), node("p", "暂无订单。", "muted"));
      host.replaceChildren(empty);
      return;
    }
    const table = node("table", "", "pl-orders");
    const head = node("thead");
    const headRow = node("tr");
    for (const title of ["时间", "套餐", "金额", "渠道", "状态", "操作"]) {
      const cell = node("th", title);
      cell.scope = "col";
      headRow.append(cell);
    }
    head.append(headRow);
    const body = node("tbody");
    for (const order of orders) {
      const row = node("tr");
      const [statusText, tone] = ORDER_STATUS[order.status] || ["未知状态", "warn"];
      const pill = node("span", statusText, `pl-pill pl-pill-${tone}`);
      const action = node("div", "", "pl-order-actions");
      if (order.status === "paid") {
        const refund = node("button", "申请退款", "danger");
        refund.type = "button";
        refund.disabled = Boolean(busy);
        refund.setAttribute("aria-label", `申请退款：${order.plan_name}，订单 ${order.id}`);
        refund.addEventListener("click", () => onRefund?.(order));
        action.append(refund);
      }
      const cells = [
        ["时间", node("span", time(order.created_at))],
        ["套餐", node("span", order.plan_name)],
        ["金额", node("span", yuan(order.amount_cents), "pl-amount")],
        ["渠道", node("span", CHANNEL_NAMES[order.channel] || String(order.channel || ""))],
        ["状态", pill],
        ["操作", action],
      ];
      for (const [label, content] of cells) {
        const cell = node("td");
        cell.dataset.label = label;
        if (label === "操作" && !content.children.length) cell.className = "pl-cell-none";
        cell.append(content);
        row.append(cell);
      }
      const idNote = node("span", order.id, "pl-order-id");
      row.children[1].append(idNote);
      body.append(row);
    }
    table.append(head, body);
    host.replaceChildren(table);
  }

  window.PlanView = { model, formatPrice, daysLeft, renderStatus, renderCards, renderOrders, scrollTo };
})();
