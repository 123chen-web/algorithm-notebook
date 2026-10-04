"use strict";

/* 管理后台“运营概览”：指标条 + 每日趋势图 + 新用户漏斗 + AI 明细。
   只读；请求走宿主传入的 api()，响应按登录代次 / 页面 / 请求序号隔离；用户文字一律 textContent。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const root = $("#admin-metrics");
  if (!root) return;

  const DAYS_KEY = "admin-metrics-days";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const SERIES = [
    { key: "new_users", label: "新增用户" },
    { key: "active_users", label: "活跃用户" },
    { key: "reviews", label: "复习次数" },
  ];
  const FEATURE_NAMES = { variant: "变式练习", weakness: "弱点分析", photo: "拍照识别", clusters: "错因聚类" };
  const FUNNEL_STEPS = [
    { key: "registered", label: "注册" },
    { key: "first_record", label: "记第一条错题" },
    { key: "first_review", label: "完成第一次复习" },
    { key: "returned_next_day", label: "次日回访" },
  ];
  let hooks = null;
  let generation = 0;
  let days = readDays();
  let series = "new_users";
  let data = null;

  /* ---------- 纯函数（也挂在 window.AdminMetrics 上供测试） ---------- */

  function readDays() {
    try {
      return Number(window.localStorage.getItem(DAYS_KEY)) === 30 ? 30 : 7;
    } catch {
      return 7;
    }
  }

  function saveDays(value) {
    try { window.localStorage.setItem(DAYS_KEY, String(value)); } catch { /* 隐私模式等：只是不记住 */ }
  }

  /** 与上一周期比：上一周期为 0 且本周期有数 → “新”。 */
  function delta(current, previous) {
    if (current === previous) return { trend: "flat", text: "→ 持平" };
    if (!previous) return { trend: "new", text: "新" };
    const percent = Math.max(1, Math.round((Math.abs(current - previous) * 100) / previous));
    return current > previous
      ? { trend: "up", text: `↑ ${percent}%` }
      : { trend: "down", text: `↓ ${percent}%` };
  }

  function formatTokens(value) {
    const count = Math.max(0, Math.round(Number(value) || 0));
    if (count < 10000) return String(count);
    const scaled = count >= 100000000 ? count / 100000000 : count / 10000;
    return `${scaled.toFixed(1).replace(/\.0$/, "")} ${count >= 100000000 ? "亿" : "万"}`;
  }

  function percentText(ratio) {
    return `${Math.round(ratio * 1000) / 10}%`;
  }

  /** 漏斗：每行的人数、条宽（相对注册人数）、相对上一根的转化率；没人注册 → empty。 */
  function funnelRows(funnel) {
    const registered = Number(funnel?.registered) || 0;
    if (registered === 0) return { empty: true, rows: [] };
    const rows = FUNNEL_STEPS.map((step, index) => {
      const count = Number(funnel[step.key]) || 0;
      const previous = index === 0 ? null : Number(funnel[FUNNEL_STEPS[index - 1].key]) || 0;
      return {
        key: step.key,
        label: step.label,
        count,
        width: Math.min(100, (count / registered) * 100),
        rate: previous === null || previous === 0 ? null : count / previous,
      };
    });
    return { empty: false, rows };
  }

  /** 1 / 2 / 5 × 10^k 的“好看”刻度步长，保证 4 格能盖住最大值。 */
  function niceStep(max) {
    const raw = Math.max(4, max) / 4;
    const power = 10 ** Math.floor(Math.log10(raw));
    for (const factor of [1, 2, 5, 10]) if (factor * power >= raw) return factor * power;
    return 10 * power;
  }

  /** 折线 / 面积图的几何：纵轴从 0 起、按比例，刻度取整。 */
  function chartModel(daily, key, width, height) {
    const pad = { left: 40, right: 24, top: 12, bottom: 28 };
    const plotWidth = Math.max(1, width - pad.left - pad.right);
    const plotHeight = Math.max(1, height - pad.top - pad.bottom);
    const values = daily.map((item) => Number(item[key]) || 0);
    const step = niceStep(Math.max(0, ...values));
    const yMax = step * 4;
    const y = (value) => pad.top + plotHeight - (value / yMax) * plotHeight;
    const x = (index) => pad.left + (daily.length > 1 ? (index * plotWidth) / (daily.length - 1) : plotWidth / 2);
    const points = daily.map((item, index) => ({ date: item.date, value: values[index], x: x(index), y: y(values[index]) }));
    const ticks = [0, 1, 2, 3, 4].map((index) => ({ value: index * step, y: y(index * step) }));
    const maxLabels = Math.max(2, Math.floor(plotWidth / 56));
    const labelStep = Math.ceil(daily.length / maxLabels);
    const xTicks = points.filter((_point, index) => (daily.length - 1 - index) % labelStep === 0)
      .map((point) => ({ x: point.x, text: point.date.slice(5) }));
    const line = points.map((point, index) => `${index ? "L" : "M"}${round(point.x)} ${round(point.y)}`).join("");
    const baseline = round(pad.top + plotHeight);
    const area = points.length
      ? `${line}L${round(points[points.length - 1].x)} ${baseline}L${round(points[0].x)} ${baseline}Z`
      : "";
    return { width, height, pad, yMax, step, points, ticks, xTicks, line, area, baseline };
  }

  function round(value) {
    return Math.round(value * 100) / 100;
  }

  /* ---------- DOM 辅助 ---------- */

  function node(tag, text = "", className = "") {
    const result = document.createElement(tag);
    if (text !== "") result.textContent = text;
    if (className) result.className = className;
    return result;
  }

  function svg(tag, attributes = {}, text = "") {
    const result = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) result.setAttribute(name, String(value));
    if (text !== "") result.textContent = text;
    return result;
  }

  function setState(state, message = "") {
    root.dataset.state = state;
    root.setAttribute("aria-busy", state === "loading" ? "true" : "false");
    $("#admin-metrics-skeleton").hidden = state !== "loading" || Boolean(data);
    $("#admin-metrics-body").hidden = !data;
    $("#admin-metrics-retry").hidden = state !== "error";
    const status = $("#admin-metrics-status");
    status.textContent = message;
    status.classList.toggle("error", state === "error");
  }

  function syncRangeButtons() {
    root.querySelectorAll("[data-am-days]").forEach((button) => {
      button.setAttribute("aria-pressed", String(Number(button.dataset.amDays) === days));
    });
    root.querySelectorAll("[data-am-series]").forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.amSeries === series));
    });
  }

  /* ---------- 渲染 ---------- */

  function metricCard({ label, value, change, sub, alert }) {
    const card = node("article", "", "am-metric");
    card.setAttribute("role", "listitem");
    if (alert) card.dataset.alert = "true";
    card.append(node("h4", label, "am-metric-label"), node("p", String(value), "am-metric-value"));
    if (change) {
      const item = node("p", change.text, "am-delta");
      item.dataset.trend = change.trend;
      card.append(item);
    }
    if (sub) card.append(node("p", sub, "am-metric-sub"));
    return card;
  }

  function renderStrip(result) {
    const ai = result.ai;
    const cards = [
      metricCard({ label: "新增用户", value: result.new_users.current, change: delta(result.new_users.current, result.new_users.previous) }),
      metricCard({ label: "活跃用户", value: result.active_users.current, change: delta(result.active_users.current, result.active_users.previous) }),
      metricCard({ label: "新增记录", value: result.records.current, change: delta(result.records.current, result.records.previous) }),
      metricCard({ label: "复习次数", value: result.reviews.current, change: delta(result.reviews.current, result.reviews.previous) }),
      metricCard({
        label: "AI 调用", value: ai.calls.current, change: delta(ai.calls.current, ai.calls.previous),
        sub: `失败 ${ai.failed.current} · 令牌 ${formatTokens(ai.tokens.prompt + ai.tokens.completion)}`,
      }),
    ];
    const pending = Number(result.pending_reports) || 0;
    const reports = metricCard({
      label: "待处理举报", value: pending, alert: pending > 0,
      sub: pending > 0 ? "" : "队列已清空",
    });
    if (pending > 0) {
      const link = node("a", "去处理", "am-link");
      link.setAttribute("href", "#admin-reports-heading");
      link.addEventListener("click", (event) => {
        event.preventDefault();
        const target = $("#admin-reports-heading");
        target.scrollIntoView({ block: "start" });
        target.focus();
      });
      reports.append(link);
    }
    cards.push(reports);
    $("#admin-metrics-strip").replaceChildren(...cards);
  }

  function chartWidth() {
    const available = $("#admin-metrics-chart-wrap").clientWidth;
    return Math.max(280, Math.min(760, Number(available) || 640));
  }

  function renderChart() {
    if (!data) return;
    const label = SERIES.find((item) => item.key === series).label;
    const model = chartModel(data.daily, series, chartWidth(), 220);
    const chart = $("#admin-metrics-chart");
    chart.setAttribute("viewBox", `0 0 ${model.width} ${model.height}`);
    const total = model.points.reduce((sum, point) => sum + point.value, 0);
    chart.setAttribute("aria-label", `${label}：最近 ${data.days} 天每日趋势，合计 ${total}，单日最高 ${Math.max(0, ...model.points.map((point) => point.value))}`);
    const parts = [];
    for (const tick of model.ticks) {
      parts.push(svg("line", { class: "am-grid", x1: model.pad.left, x2: model.width - model.pad.right, y1: round(tick.y), y2: round(tick.y) }));
      parts.push(svg("text", { class: "am-tick", x: model.pad.left - 6, y: round(tick.y + 4), "text-anchor": "end" }, String(tick.value)));
    }
    for (const tick of model.xTicks) {
      parts.push(svg("text", { class: "am-tick", x: round(tick.x), y: model.height - 8, "text-anchor": "middle" }, tick.text));
    }
    parts.push(svg("path", { class: "am-area", d: model.area }));
    parts.push(svg("path", { class: "am-line", d: model.line }));
    for (const point of model.points) {
      const dot = svg("circle", { class: "am-dot", cx: round(point.x), cy: round(point.y), r: 3 });
      dot.append(svg("title", {}, `${point.date}：${point.value}`));
      parts.push(dot);
    }
    chart.replaceChildren(...parts);
    renderTable();
  }

  function renderTable() {
    const head = node("tr");
    ["日期", ...SERIES.map((item) => item.label)].forEach((text) => {
      const cell = node("th", text);
      cell.setAttribute("scope", "col");
      head.append(cell);
    });
    const rows = data.daily.map((item) => {
      const row = node("tr");
      const date = node("th", item.date);
      date.setAttribute("scope", "row");
      row.append(date, ...SERIES.map((entry) => node("td", String(item[entry.key]))));
      return row;
    });
    $("#admin-metrics-table").replaceChildren(node("caption", `最近 ${data.days} 天每日数据（UTC）`), head, ...rows);
  }

  function renderFunnel(result) {
    const { empty, rows } = funnelRows(result.funnel);
    $("#admin-metrics-funnel-empty").hidden = !empty;
    $("#admin-metrics-funnel").hidden = empty;
    $("#admin-metrics-funnel").replaceChildren(...rows.map((row) => {
      const item = node("li", "", "am-funnel-row");
      item.dataset.step = row.key;
      const head = node("div", "", "am-funnel-head");
      head.append(node("span", row.label, "am-funnel-label"), node("b", String(row.count), "am-funnel-count"));
      head.append(node("span", row.rate === null ? "" : `转化 ${percentText(row.rate)}`, "am-funnel-rate"));
      const track = node("div", "", "am-bar-track");
      const bar = node("div", "", "am-bar");
      bar.style.setProperty("width", `${row.count > 0 ? Math.max(row.width, 1.5) : 0}%`);
      track.append(bar);
      item.append(head, track);
      return item;
    }));
  }

  function renderAi(result) {
    const ai = result.ai;
    const features = ai.by_feature;
    $("#admin-metrics-ai-empty").hidden = features.length > 0;
    $("#admin-metrics-ai-table").hidden = features.length === 0;
    const head = node("tr");
    ["功能", "调用", "失败", "令牌"].forEach((text, index) => {
      const cell = node("th", text, index ? "am-num" : "");
      cell.setAttribute("scope", "col");
      head.append(cell);
    });
    const rows = features.map((item) => {
      const row = node("tr");
      const name = node("th", FEATURE_NAMES[item.feature] || item.feature);
      name.setAttribute("scope", "row");
      row.append(name, node("td", String(item.calls), "am-num"), node("td", String(item.failed), "am-num"),
        node("td", formatTokens(item.tokens), "am-num"));
      return row;
    });
    const total = node("tr", "", "am-total");
    const label = node("th", "合计");
    label.setAttribute("scope", "row");
    total.append(label, node("td", String(ai.calls.current), "am-num"), node("td", String(ai.failed.current), "am-num"),
      node("td", formatTokens(ai.tokens.prompt + ai.tokens.completion), "am-num"));
    $("#admin-metrics-ai-table").replaceChildren(head, ...rows, total);
    $("#admin-metrics-ai-split").textContent = `输入令牌 ${formatTokens(ai.tokens.prompt)} · 输出令牌 ${formatTokens(ai.tokens.completion)}`;
  }

  function renderOther(result) {
    $("#admin-metrics-other").textContent = `论坛：帖子 ${result.posts.current}（上一周期 ${result.posts.previous}） · 评论 ${result.comments.current}（上一周期 ${result.comments.previous}）`
      + `；兑换码：本期生成 ${result.redeem.created} · 兑换 ${result.redeem.redeemed} · 现存未使用 ${result.redeem.unused}`;
    const updated = new Date(result.generated_at);
    $("#admin-metrics-updated").textContent = Number.isNaN(updated.getTime())
      ? "" : `数据更新于 ${updated.toISOString().slice(0, 16).replace("T", " ")} UTC`;
  }

  function render(result) {
    data = result;
    renderStrip(result);
    renderFunnel(result);
    renderAi(result);
    renderOther(result);
  }

  /* ---------- 加载与隔离 ---------- */

  function ticket() {
    return { generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id, days };
  }

  function current(request) {
    const user = hooks?.getUser();
    return Boolean(hooks && user && user.is_admin && request.generation === generation
      && request.epoch === hooks.getEpoch() && request.userId === user.id && hooks.getView() === "admin");
  }

  function clear() {
    data = null;
    $("#admin-metrics-strip").replaceChildren();
    $("#admin-metrics-chart").replaceChildren();
    $("#admin-metrics-table").replaceChildren();
    $("#admin-metrics-funnel").replaceChildren();
    $("#admin-metrics-ai-table").replaceChildren();
    $("#admin-metrics-other").textContent = "";
    $("#admin-metrics-updated").textContent = "";
  }

  async function load() {
    generation += 1;
    if (!hooks || !hooks.getUser()?.is_admin) {
      clear();
      root.hidden = true;
      setState("idle");
      return false;
    }
    root.hidden = false;
    const request = ticket();
    syncRangeButtons();
    setState("loading", "正在加载运营概览…");
    try {
      const result = await hooks.api(`/api/admin/metrics?days=${request.days}`);
      if (!current(request)) return false;
      render(result);
      setState("ready");
      // 图要按容器实际宽度画：区块还藏着的时候量到的宽度是 0，所以放在显示之后。
      renderChart();
      return true;
    } catch (error) {
      if (!current(request)) return false;
      setState("error", error?.message || "加载失败，请重试。");
      return false;
    }
  }

  function reset() {
    generation += 1;
    clear();
    root.hidden = true;
    setState("idle");
  }

  root.querySelectorAll("[data-am-days]").forEach((button) => {
    button.addEventListener("click", () => {
      const value = Number(button.dataset.amDays) === 30 ? 30 : 7;
      if (value === days && data) return;
      days = value;
      saveDays(value);
      clear();
      load();
    });
  });
  root.querySelectorAll("[data-am-series]").forEach((button) => {
    button.addEventListener("click", () => {
      series = button.dataset.amSeries;
      syncRangeButtons();
      renderChart();
    });
  });
  $("#admin-metrics-retry").addEventListener("click", () => { load(); });
  window.addEventListener("resize", () => { if (data && !root.hidden) renderChart(); });
  document.addEventListener("app:view-changed", () => { generation += 1; });
  if (typeof MutationObserver !== "undefined") {
    new MutationObserver(() => {
      if (document.documentElement.dataset.view !== "app") reset();
    }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-view"] });
  }
  syncRangeButtons();

  window.AdminMetrics = {
    configure: (value) => { hooks = value; },
    load,
    reset,
    helpers: { delta, formatTokens, funnelRows, chartModel, niceStep },
  };
})();
