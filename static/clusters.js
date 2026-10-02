"use strict";

/* 错因专题只读取已保存的结果；明确点击归并才消耗 AI 额度。
   使用 app.js 的账号、请求和额度显示，所有材料均作为纯文本插入。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  let report = null;
  let generation = 0;
  let pending = "";
  let quotaAvailable = false;
  let retryAction = "load";

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function current(ticket, userId) {
    return Boolean(user) && user.id === userId && generation === ticket;
  }

  function setStatus(text, { error = false, retry = false } = {}) {
    const status = $("#clusters-status");
    status.textContent = text;
    status.classList.toggle("error", error);
    $("#clusters-retry").hidden = !retry;
    $("#clusters-retry").textContent = retryAction === "generate" ? "重试归并 · 消耗 1 次 AI 额度" : "重试读取";
  }

  function renderControls() {
    const minimum = report?.minimum_mistakes ?? 6;
    const insufficient = !report || report.mistake_count < minimum;
    const exhausted = quotaAvailable && user?.ai_daily_remaining === 0;
    const blocked = Boolean(pending) || insufficient || exhausted;
    const button = $("#clusters-generate");
    button.setAttribute("aria-disabled", String(blocked));
    button.dataset.blocked = blocked ? "1" : "0";
    button.textContent = pending === "generate"
      ? "正在归并相似错因…"
      : report?.insight ? "重新归并 · 消耗 1 次 AI 额度" : "归并相似错因 · 消耗 1 次 AI 额度";
    $("#clusters-page").setAttribute("aria-busy", String(Boolean(pending)));
    // updateUserInfo 已用现有 renderHomeQuota 格式化顶栏；这里只复用同一段文字。
    const quota = $("#home-quota-text");
    const sharedQuotaVisible = user?.ai_daily_limit > 0 && !$("#home-quota").hidden;
    $("#clusters-quota").textContent = quotaAvailable && sharedQuotaVisible && quota?.textContent
      ? `今日 AI 额度${quota.textContent}${exhausted ? "，明天可再归并。" : "。"}`
      : quotaAvailable && user?.ai_daily_limit === 0
        ? "今日 AI 额度剩余 0 / 0 次，暂无额度，可前往“我的套餐”查看额度。"
        : pending ? "正在读取今日 AI 额度…" : "暂时无法读取剩余额度，归并时由服务端检查额度。";
    const newCount = report?.new_since ?? 0;
    $("#clusters-new").hidden = newCount <= 0;
    $("#clusters-new").textContent = newCount > 0 ? `有 ${newCount} 条新错题还没归并` : "";
  }

  function isDue(member) {
    return typeof member.due_date === "string" && /^\d{4}-\d{2}-\d{2}$/.test(member.due_date)
      && typeof report?.today === "string" && member.due_date <= report.today;
  }

  function dueText(member) {
    if (!member.due_date) return "待安排复习";
    if (member.due_date === report.today) return "今天到期";
    if (isDue(member)) return "已逾期";
    return `${member.due_date} 到期`;
  }

  function memberRow(member, ticket, userId) {
    const row = node("li", "clusters-member");
    const open = node("button", "clusters-member-open");
    open.type = "button";
    const copy = node("span", "clusters-member-copy");
    copy.append(node("strong", "clusters-member-title", member.title), node("span", "clusters-member-description", member.description || "尚未记录具体错因"));
    open.append(node("span", "clusters-zone", member.zone), copy, node("span", `clusters-due${isDue(member) ? " is-due" : ""}`, dueText(member)));
    open.addEventListener("click", () => {
      if (!current(ticket, userId)) return;
      document.dispatchEvent(new CustomEvent("app:navigate", { detail: { view: "all", recordId: member.mistake_id } }));
    });
    row.append(open);
    return row;
  }

  function clusterCard(cluster, index, ticket, userId) {
    const card = node("article", "panel clusters-card clusters-reveal");
    card.style.setProperty("--clusters-order", String(index + 1));
    const head = node("div", "clusters-card-heading");
    const number = node("span", "clusters-number", String(index + 1).padStart(2, "0"));
    number.setAttribute("aria-hidden", "true");
    head.append(number, node("h3", "", cluster.title));
    const explanation = node("p", "clusters-explanation", cluster.explanation);
    const tip = node("div", "clusters-tip");
    tip.append(node("h4", "", "下次复习这样练"), node("p", "", cluster.tip));
    const members = node("ul", "clusters-members");
    members.setAttribute("role", "list");
    members.setAttribute("aria-label", `${cluster.title}的易错点`);
    members.append(...cluster.members.map((member) => memberRow(member, ticket, userId)));
    card.append(head, explanation, tip, members);
    const ids = cluster.members.filter(isDue).map((member) => member.mistake_id);
    if (ids.length && typeof window.FocusReview?.start === "function") {
      const practise = node("button", "clusters-practise", `一起复习这 ${ids.length} 条到期的`);
      practise.type = "button";
      practise.addEventListener("click", () => {
        if (current(ticket, userId)) window.FocusReview?.start({ ids });
      });
      card.append(practise);
    }
    return card;
  }

  function render() {
    renderControls();
    const result = $("#clusters-result");
    result.replaceChildren();
    const insight = report?.insight;
    result.hidden = !insight;
    $("#clusters-empty").hidden = Boolean(insight);
    const minimum = report?.minimum_mistakes ?? 6;
    const count = report?.mistake_count ?? 0;
    const insufficient = count < minimum;
    $("#clusters-empty-title").textContent = insufficient ? `再记录 ${minimum - count} 条易错点` : "还没有归并过错因";
    // 状态行（live region）已经读出服务端的说明；卡片里写"接下来做什么"，不把同一句话再显示一遍。
    $("#clusters-empty-text").textContent = insufficient
      ? `继续记录易错点并写清具体错因，攒够 ${minimum} 条（已有 ${count} 条）就能归并。本次不会调用 AI，也不消耗额度。`
      : "把具体错因积累下来，点击上方归并按钮，就能把有共同根因的记录放在一起复习。";
    if (!insight) return;
    const { content } = insight;
    const overview = node("section", "panel clusters-overview clusters-reveal");
    overview.style.setProperty("--clusters-order", "0");
    overview.append(node("h3", "", "这次归并的发现"), node("p", "clusters-summary", content.summary));
    const updated = typeof timestamp === "function" ? timestamp(insight.created_at) : insight.created_at;
    overview.append(node("p", "clusters-report-meta", `参考 ${content.sample.mistake_count} 条易错点 · 更新于 ${updated} · 只保留最近一次归并`));
    if (insufficient) overview.append(node("p", "clusters-saved-note", "以下是上次保存的专题；当前数量不足，暂时无法重新归并。"));
    result.append(overview);
    const list = node("div", "clusters-cards");
    const ticket = generation;
    const userId = user.id;
    list.append(...content.clusters.map((cluster, index) => clusterCard(cluster, index, ticket, userId)));
    result.append(list);
    if (!content.clusters.length) result.append(node("p", "clusters-no-common", "暂时看不出可归并的共性。继续记录具体错因，再积累一些线索。"));
  }

  function acceptProfile(profile, requestUser) {
    // 离开本页或其它页面已刷新账号信息时，旧请求不得覆盖全局的新额度。
    if (!user || user !== requestUser || profile.id !== user.id || $("#clusters-page").hidden) return;
    user = profile;
    quotaAvailable = Number.isInteger(user.ai_daily_remaining) && Number.isInteger(user.ai_daily_limit);
    updateUserInfo();
  }

  async function load() {
    if (!user || pending === "generate") return;
    const ticket = ++generation;
    const userId = user.id;
    const requestUser = user;
    pending = "load";
    quotaAvailable = false;
    retryAction = "load";
    renderControls();
    setStatus("正在读取已保存的专题，不消耗 AI 额度…");
    const [state, profile] = await Promise.allSettled([
      api("/api/insights/clusters"), api("/api/me"),
    ]);
    if (!current(ticket, userId)) return;
    if (profile.status === "fulfilled") acceptProfile(profile.value, requestUser);
    pending = "";
    if (state.status === "fulfilled") {
      report = state.value;
      render();
      setStatus(report.message || (report.insight ? `已载入 ${report.insight.content.clusters.length} 个错因专题。` : "还没有归并过，可以点击上方按钮开始。"));
    } else {
      if (report) render();
      else renderControls();
      setStatus(`${state.reason.message || "暂时无法读取专题，请稍后重试"}${report?.insight ? "。已保留上次专题结果。" : ""}`, { error: true, retry: true });
    }
  }

  async function generate() {
    if (!user || pending || $("#clusters-page").hidden || $("#clusters-generate").dataset.blocked === "1") return;
    const ticket = ++generation;
    const userId = user.id;
    pending = "generate";
    retryAction = "generate";
    renderControls();
    setStatus("正在寻找相近的根因并归并成专题，请稍候…");
    try {
      const state = await api("/api/insights/clusters", { method: "POST" });
      if (!current(ticket, userId)) return;
      report = state;
      render();
      setStatus(report.message || `归并完成，共 ${report.insight?.content.clusters.length ?? 0} 个专题。`);
    } catch (error) {
      if (!current(ticket, userId)) return;
      render();
      setStatus(`${error.message || "归并失败，请稍后重试"}${report?.insight ? "。已保留上次专题结果。" : ""}`, { error: true, retry: true });
    } finally {
      if (current(ticket, userId)) {
        // AI 失败也可能占额；始终读取真实额度，不在客户端猜测或递减。
        quotaAvailable = false;
        try {
          const requestUser = user;
          const profile = await api("/api/me");
          if (current(ticket, userId)) acceptProfile(profile, requestUser);
        } catch {
          // 额度读取失败时显示未知状态，避免继续展示旧次数。
        }
        if (current(ticket, userId)) {
          pending = "";
          renderControls();
        }
      }
    }
  }

  function reset() {
    generation += 1;
    report = null;
    pending = "";
    quotaAvailable = false;
    retryAction = "load";
    $("#clusters-page").hidden = true;
    $("#clusters-page").setAttribute("aria-busy", "false");
    $("#clusters-result").replaceChildren();
    $("#clusters-result").hidden = true;
    $("#clusters-empty").hidden = true;
    for (const id of ["clusters-empty-title", "clusters-empty-text", "clusters-quota", "clusters-new"]) $("#" + id).textContent = "";
    $("#clusters-new").hidden = true;
    $("#clusters-generate").setAttribute("aria-disabled", "true");
    $("#clusters-generate").dataset.blocked = "1";
    $("#clusters-generate").textContent = "归并相似错因 · 消耗 1 次 AI 额度";
    setStatus("");
  }

  $("#clusters-generate").addEventListener("click", generate);
  $("#clusters-retry").addEventListener("click", () => retryAction === "generate" ? generate() : load());
  document.addEventListener("app:data-changed", () => {
    if (user && !$("#clusters-page").hidden && !pending) void load();
  });
  window.Clusters = { load, reset };
})();
