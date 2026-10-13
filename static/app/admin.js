// static/app/admin.js — 管理后台。
// 由 static/app.js 按领域拆分（C2），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

// ---- 领域状态 ----
let adminDashboardGeneration = 0;

function resetAdminDashboard() {
  adminDashboardGeneration += 1;
  $("#admin-dashboard").setAttribute("aria-busy", "false");
  $("#admin-dashboard-content").hidden = true;
  $("#admin-dashboard-cards").replaceChildren();
  $("#admin-dashboard-zones").replaceChildren();
  $("#admin-dashboard-zones-empty").hidden = true;
  $("#admin-dashboard-status").textContent = "";
  $("#admin-dashboard-status").classList.remove("error");
  $("#admin-dashboard-updated").textContent = "";
  $("#admin-dashboard-refresh").textContent = "刷新数据";
}

function renderAdminDashboard(data) {
  const count = (value) => {
    if (!Number.isSafeInteger(value) || value < 0) throw new Error("统计数据不完整，请重试");
    return value.toLocaleString("zh-CN");
  };
  const money = (value) => {
    count(value);
    return (value / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const definitions = [
    ["用户总数", data.users.total, "人", [
      ["体验账号", `${count(data.users.trial)} 人`],
      ["正式账号", `${count(data.users.registered)} 人`],
      ["最近 7 天新增", `${count(data.users.new_7_days)} 人`],
      ["最近 30 天新增", `${count(data.users.new_30_days)} 人`],
    ], "正式账号指非体验账号，与是否付费订阅无关。"],
    ["最近 7 天活跃用户", data.activity.active_users_7_days, "人", [],
      "新增过题目或提交过复习评分的用户；同一人只计算一次。"],
    ["今天 AI 调用", data.ai_usage.today, "次", [
      ["最近 7 天调用", `${count(data.ai_usage.last_7_days)} 次`],
    ]],
    ["题目总数", data.content.problems, "道", [
      ["易错点总数", `${count(data.content.mistakes)} 个`],
      ["讨论区帖子", `${count(data.content.posts)} 篇`],
    ], "帖子数量不含已删除的帖子。"],
    ["待处理举报", data.pending_reports, "条", [], "包含讨论区内容举报与头像举报。"],
    ["当前有效订阅", data.subscriptions.active, "份", [
      ["已支付订单", `${count(data.subscriptions.paid_orders)} 笔`],
      ["已支付订单总金额", `${money(data.subscriptions.paid_amount_cents)} 元`],
    ], "订阅尚未到期；订单与金额为全站累计，仅统计当前状态为已支付的订单。"],
  ];
  const cards = definitions.map(([title, value, unit, facts, note]) => {
    const card = element("article", "", "panel admin-stat-card");
    const number = element("p", count(value), "admin-stat-value");
    number.append(element("small", unit));
    card.append(element("h4", title), number);
    if (facts.length) {
      const list = element("dl", "", "admin-stat-facts");
      for (const [label, text] of facts) list.append(element("dt", label), element("dd", text));
      card.append(list);
    }
    if (note) card.append(element("p", note, "muted admin-dashboard-note"));
    return card;
  });
  const zoneRows = data.zones.map((zone) => {
    const row = element("li");
    row.append(element("span", zone.zone), element("strong", `${count(zone.mistake_count)} 个`));
    return row;
  });
  const updated = new Date(data.generated_at);
  if (Number.isNaN(updated.getTime())) throw new Error("统计时间无效，请重试");
  $("#admin-dashboard-cards").replaceChildren(...cards);
  $("#admin-dashboard-zones").replaceChildren(...zoneRows);
  $("#admin-dashboard-zones-empty").hidden = zoneRows.length > 0;
  $("#admin-dashboard-updated").textContent = `数据更新于 ${updated.toLocaleString("zh-CN", { timeZone: "UTC", hour12: false })} UTC`;
  $("#admin-dashboard-content").hidden = false;
}

async function loadAdminDashboard() {
  const generation = ++adminDashboardGeneration;
  const currentUser = user;
  if (!currentUser?.is_admin) return false;
  const isCurrent = () => generation === adminDashboardGeneration && user === currentUser && view === "admin";
  const status = $("#admin-dashboard-status");
  const refresh = $("#admin-dashboard-refresh");
  $("#admin-dashboard").setAttribute("aria-busy", "true");
  $("#admin-dashboard-content").hidden = true;
  $("#admin-dashboard-updated").textContent = "";
  status.classList.remove("error");
  status.textContent = "正在加载全站数据…";
  refresh.textContent = "加载中…";
  try {
    const data = await api("/api/admin/dashboard");
    if (!isCurrent()) return false;
    renderAdminDashboard(data);
    status.textContent = "";
    return true;
  } catch (error) {
    if (!isCurrent()) return false;
    status.textContent = error.status === 403
      ? "无权查看数据看板，请使用管理员账号登录。"
      : "数据看板加载失败，请检查网络后点击「重试加载」。";
    status.classList.add("error");
    return false;
  } finally {
    if (isCurrent()) {
      $("#admin-dashboard").setAttribute("aria-busy", "false");
      refresh.textContent = status.classList.contains("error") ? "重试加载" : "刷新数据";
    }
  }
}

async function loadAdminPage() {
  configureRedeem();
  configureAdminMetrics();
  configureRank();
  window.ManualClaimsAdmin?.mount($("#manual-claims-admin"));
  // 各区块独立加载，看板失败不会阻断原有的举报处理。
  const [dashboard, reports] = await Promise.allSettled([loadAdminDashboard(), loadAdminReports(), window.Redeem?.loadAdmin(), window.AdminMetrics?.load(), window.RankAdmin?.load()]);
  if (reports.status === "rejected") throw reports.reason;
  return dashboard.status === "fulfilled" && dashboard.value;
}

function configureRedeem() {
  window.Redeem?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch,
    getView: () => view, refreshPlanSubscription });
  window.ManualClaims?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  window.ManualClaimsAdmin?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
}

function configureAdminMetrics() {
  window.AdminMetrics?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch, getView: () => view });
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
  const preview = avatarElement(authorId, authorName, report.avatar_owner_avatar_version, {
    hasAvatar: report.avatar_owner_has_avatar,
  });
  preview.style.width = "96px";
  preview.style.height = "96px";
  preview.style.fontSize = "36px";
  card.append(preview);
  if (!report.avatar_owner_has_avatar) {
    card.append(element("p", "该用户目前没有自定义头像（可能已被清除或本人移除），只能忽略这条举报。", "muted"));
  }

  const actions = element("div", "", "actions");
  const clearBtn = element("button", "清除该头像", "danger");
  clearBtn.type = "button";
  clearBtn.disabled = !report.avatar_owner_has_avatar;
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
