"use strict";

/* 掌握度趋势页：每个分区一条曲线 + 分区卡片 + "快被遗忘"提醒。
   对外契约：window.Mastery = { load(), mountAlert(card), reset() }。
   数据来自 GET /api/stats/mastery?weeks=12；曲线用 SVG 按容器宽度实绘，文字始终是 12px。
   所有用户文本一律 textContent。 */
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const WEEKS = 12;
  const CACHE_MS = 60000;
  const DASHES = ["", "8 4", "2 4", "10 3 2 3", "", "8 4", "2 4", "10 3 2 3"];
  const $ = (selector) => document.querySelector(selector);

  let data = null;
  let cache = null;
  let generation = 0;
  let epoch = 0; // 只在登出/换账号时加一，用来丢弃旧账号发出的请求结果
  let hidden = new Set();
  let activeIndex = -1;
  let observer = null;
  let frame = 0;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function svg(tag, attributes = {}) {
    const item = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) item.setAttribute(name, String(value));
    return item;
  }
  function percent(value) {
    return `${value.toFixed(1).replace(/\.0$/, "")}%`;
  }
  function shortDate(text) {
    const [, month, day] = text.split("-").map(Number);
    return `${month}/${day}`;
  }
  function longDate(text) {
    const [, month, day] = text.split("-").map(Number);
    return `${month} 月 ${day} 日`;
  }

  async function fetchReport(force = false) {
    if (!force && cache && Date.now() - cache.at < CACHE_MS) return cache.data;
    const startedIn = epoch;
    const response = await fetch(`/api/stats/mastery?weeks=${WEEKS}`, {
      credentials: "same-origin", headers: { "X-CSRF-Protection": "1" },
    });
    if (!response.ok) throw new Error(`mastery ${response.status}`);
    const report = await response.json();
    if (startedIn === epoch) cache = { at: Date.now(), data: report };
    return report;
  }

  /* ---------- 提醒 ---------- */
  function alertText(alert) {
    const overdue = alert.overdue ? `，其中 ${alert.overdue} 条已逾期` : "";
    return `「${alert.zone}」掌握度只剩 ${percent(alert.mastery)}，有 ${alert.due} 条到期${overdue}。建议先复习它。`;
  }
  function alertActions(alert) {
    const wrap = node("div", "mastery-alert-actions");
    if (window.FocusReview) {
      const practise = node("button", "primary", `只练「${alert.zone}」`);
      practise.type = "button";
      practise.addEventListener("click", () => window.FocusReview.start({ zone: alert.zone }));
      wrap.append(practise);
    }
    const view = node("button", "", "查看这个分区的记录");
    view.type = "button";
    view.addEventListener("click", () => document.dispatchEvent(new CustomEvent("records:filter", { detail: { zone: alert.zone } })));
    wrap.append(view);
    return wrap;
  }
  function renderAlert() {
    const box = $("#mastery-alert");
    if (!data.alert) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const title = node("strong", "mastery-alert-title", "有一科快被遗忘了");
    box.replaceChildren(title, node("p", "", alertText(data.alert)), alertActions(data.alert));
    box.hidden = false;
  }

  async function mountAlert(card) {
    if (!card) return;
    const ticket = epoch;
    try {
      const report = await fetchReport();
      if (ticket !== epoch || !card.isConnected) return;
      const target = card.querySelector("#ov-fading");
      if (!report.alert) {
        card.hidden = true;
        target.replaceChildren();
        return;
      }
      target.replaceChildren(node("p", "ov-card-note", alertText(report.alert)), alertActions(report.alert));
      card.hidden = false;
    } catch {
      card.hidden = true;
    }
  }

  /* ---------- 图例与卡片 ---------- */
  function seriesIndex(zone) {
    const names = ["算法", "前端", "后端", "数据库", "系统设计", "高等数学", "线性代数", "概率统计"];
    const found = names.indexOf(zone);
    return found >= 0 ? found : 0;
  }

  function renderLegend() {
    const legend = $("#mastery-legend");
    legend.replaceChildren(...data.zones.map((entry) => {
      const button = node("button", "mastery-legend-item");
      button.type = "button";
      button.dataset.zone = entry.zone;
      button.setAttribute("aria-pressed", String(!hidden.has(entry.zone)));
      const swatch = node("span", "mastery-swatch");
      swatch.setAttribute("aria-hidden", "true");
      swatch.dataset.dash = String(seriesIndex(entry.zone));
      button.append(swatch, node("span", "", entry.zone));
      button.addEventListener("click", () => {
        if (hidden.has(entry.zone)) hidden.delete(entry.zone);
        else if (hidden.size < data.zones.length - 1) hidden.add(entry.zone);
        renderLegend();
        drawChart();
      });
      return button;
    }));
  }

  function sparkline(entry) {
    const width = 120;
    const height = 34;
    const box = svg("svg", { class: "mastery-spark", viewBox: `0 0 ${width} ${height}`, "aria-hidden": "true", focusable: "false" });
    const values = entry.series;
    const step = width / (values.length - 1);
    let path = "";
    values.forEach((value, index) => {
      if (value === null) return;
      const x = index * step;
      const y = height - 3 - (value / 100) * (height - 6);
      path += `${path && values[index - 1] !== null ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)} `;
    });
    box.append(svg("path", { d: path.trim(), class: "mastery-spark-line", "stroke-dasharray": DASHES[seriesIndex(entry.zone)] }));
    return box;
  }

  function renderZones() {
    const wrap = $("#mastery-zones");
    wrap.replaceChildren(...data.zones.map((entry) => {
      const card = node("article", "mastery-zone panel");
      card.dataset.zone = entry.zone;
      card.setAttribute("aria-label", `${entry.zone}：掌握度 ${percent(entry.mastery)}`);
      const fading = entry.mastery < data.threshold;
      const head = node("div", "mastery-zone-head");
      const dot = node("span", "zone-dot");
      dot.dataset.zone = entry.zone;
      dot.setAttribute("aria-hidden", "true");
      head.append(dot, node("h3", "", entry.zone), node("span", `mastery-chip ${fading ? "is-fading" : "is-steady"}`, fading ? "快被遗忘" : "状态良好"));
      const number = node("p", "mastery-number");
      number.append(node("strong", "", percent(entry.mastery)));
      let change = "—";
      let tone = "";
      if (entry.change !== null) {
        const delta = Math.abs(entry.change).toFixed(1).replace(/\.0$/, "");
        if (entry.change > 0.05) { change = `较两周前 ▲ ${delta}`; tone = "is-up"; }
        else if (entry.change < -0.05) { change = `较两周前 ▼ ${delta}`; tone = "is-down"; }
        else { change = "与两周前持平"; }
      }
      number.append(node("span", `mastery-change ${tone}`.trim(), change));
      const counts = node("p", "mastery-counts", `${entry.total} 条 · ${entry.due} 条到期 · ${entry.overdue} 条逾期 · ${entry.at_risk} 条快忘了`);
      const actions = node("div", "mastery-zone-actions");
      if (entry.due > 0 && window.FocusReview) {
        const practise = node("button", "", "只练这个分区");
        practise.type = "button";
        practise.addEventListener("click", () => window.FocusReview.start({ zone: entry.zone }));
        actions.append(practise);
      }
      const view = node("button", "mastery-link", "查看记录");
      view.type = "button";
      view.addEventListener("click", () => document.dispatchEvent(new CustomEvent("records:filter", { detail: { zone: entry.zone } })));
      actions.append(view);
      card.append(head, number, sparkline(entry), counts, actions);
      return card;
    }));
  }

  function renderTable() {
    const table = node("table", "mastery-table");
    table.append(node("caption", "mastery-sr-only", "各分区近 12 周的掌握度（百分数），空白表示当时还没有这个分区的记录"));
    const head = node("tr");
    head.append(Object.assign(node("th", "", "日期"), { scope: "col" }));
    for (const entry of data.zones) head.append(Object.assign(node("th", "", entry.zone), { scope: "col" }));
    const thead = node("thead");
    thead.append(head);
    const body = node("tbody");
    data.points.forEach((day, index) => {
      const row = node("tr");
      row.append(Object.assign(node("th", "", index === data.points.length - 1 ? `${shortDate(day)}（今天）` : shortDate(day)), { scope: "row" }));
      for (const entry of data.zones) {
        const value = entry.series[index];
        row.append(node("td", "", value === null ? "" : percent(value)));
      }
      body.append(row);
    });
    table.append(thead, body);
    $("#mastery-table").replaceChildren(table);
  }

  /* ---------- 曲线 ---------- */
  function drawChart() {
    const container = $("#mastery-chart");
    if (!data || !data.zones.length) return;
    const width = Math.max(280, Math.floor(container.clientWidth));
    if (!container.clientWidth) return;
    const height = width < 520 ? 240 : 300;
    const margin = { top: 14, right: 14, bottom: 30, left: 42 };
    const innerWidth = width - margin.left - margin.right;
    const innerHeight = height - margin.top - margin.bottom;
    const count = data.points.length;
    const x = (index) => margin.left + (innerWidth * index) / (count - 1);
    const y = (value) => margin.top + innerHeight * (1 - value / 100);
    const chart = svg("svg", { class: "mastery-svg", width, height, viewBox: `0 0 ${width} ${height}`, role: "img" });
    const zonesShown = data.zones.filter((entry) => !hidden.has(entry.zone));
    chart.setAttribute("aria-label", `近 ${count - 1} 周各分区掌握度曲线：${data.zones.map((entry) => `${entry.zone}现在 ${percent(entry.mastery)}`).join("，")}`);

    for (const tick of [0, 25, 50, 75, 100]) {
      chart.append(svg("line", { class: "mastery-grid", x1: margin.left, x2: width - margin.right, y1: y(tick), y2: y(tick) }));
      const label = svg("text", { class: "mastery-axis", x: margin.left - 8, y: y(tick) + 4, "text-anchor": "end" });
      label.textContent = `${tick}%`;
      chart.append(label);
    }
    chart.append(svg("line", { class: "mastery-threshold", x1: margin.left, x2: width - margin.right, y1: y(data.threshold), y2: y(data.threshold) }));
    const thresholdLabel = svg("text", { class: "mastery-axis mastery-threshold-label", x: width - margin.right - 4, y: y(data.threshold) - 5, "text-anchor": "end" });
    thresholdLabel.textContent = `${data.threshold}% 快被遗忘线`;
    chart.append(thresholdLabel);

    const every = width < 520 ? 3 : 2;
    data.points.forEach((day, index) => {
      if ((count - 1 - index) % every !== 0) return;
      const label = svg("text", { class: "mastery-axis", x: x(index), y: height - 9, "text-anchor": index === 0 ? "start" : index === count - 1 ? "end" : "middle" });
      label.textContent = index === count - 1 ? "今天" : shortDate(day);
      chart.append(label);
    });

    for (const entry of zonesShown) {
      let path = "";
      entry.series.forEach((value, index) => {
        if (value === null) return;
        path += `${path && entry.series[index - 1] !== null ? "L" : "M"}${x(index).toFixed(1)} ${y(value).toFixed(1)} `;
      });
      const line = svg("path", { class: "mastery-line", d: path.trim(), "stroke-dasharray": DASHES[seriesIndex(entry.zone)] });
      line.dataset.zone = entry.zone;
      chart.append(line);
      const last = entry.series[count - 1];
      if (last !== null) {
        const dot = svg("circle", { class: "mastery-end", cx: x(count - 1), cy: y(last), r: 4 });
        dot.dataset.zone = entry.zone;
        chart.append(dot);
      }
    }

    const cursor = svg("line", { class: "mastery-cursor", y1: margin.top, y2: margin.top + innerHeight, visibility: "hidden" });
    chart.append(cursor);
    const marks = zonesShown.map((entry) => {
      const mark = svg("circle", { class: "mastery-mark", r: 5, visibility: "hidden" });
      mark.dataset.zone = entry.zone;
      chart.append(mark);
      return mark;
    });
    const tooltip = node("div", "mastery-tooltip");
    tooltip.hidden = true;
    tooltip.setAttribute("aria-hidden", "true");

    function show(index) {
      activeIndex = index;
      const px = x(index);
      cursor.setAttribute("x1", px);
      cursor.setAttribute("x2", px);
      cursor.setAttribute("visibility", "visible");
      const rows = [];
      zonesShown.forEach((entry, position) => {
        const value = entry.series[index];
        const mark = marks[position];
        if (value === null) {
          mark.setAttribute("visibility", "hidden");
          return;
        }
        mark.setAttribute("cx", px);
        mark.setAttribute("cy", y(value));
        mark.setAttribute("visibility", "visible");
        rows.push([entry.zone, value]);
      });
      rows.sort((a, b) => b[1] - a[1]);
      const heading = node("strong", "", index === count - 1 ? `今天（${longDate(data.points[index])}）` : longDate(data.points[index]));
      tooltip.replaceChildren(heading, ...rows.map(([zone, value]) => {
        const line = node("div", "mastery-tooltip-row");
        const dot = node("span", "zone-dot");
        dot.dataset.zone = zone;
        line.append(dot, node("span", "", zone), node("b", "", percent(value)));
        return line;
      }));
      tooltip.hidden = rows.length === 0;
      const left = Math.min(width - 168, Math.max(4, px + 12));
      tooltip.style.left = `${left}px`;
      tooltip.style.top = `${margin.top + 4}px`;
      $("#mastery-chart-summary").textContent = rows.length
        ? `${longDate(data.points[index])}：${rows.map(([zone, value]) => `${zone} ${percent(value)}`).join("，")}`
        : `${longDate(data.points[index])}：还没有记录`;
    }
    function hide() {
      activeIndex = -1;
      cursor.setAttribute("visibility", "hidden");
      for (const mark of marks) mark.setAttribute("visibility", "hidden");
      tooltip.hidden = true;
    }
    const overlay = svg("rect", { class: "mastery-hit", x: margin.left, y: margin.top, width: innerWidth, height: innerHeight, fill: "transparent" });
    overlay.addEventListener("pointermove", (event) => {
      const box = overlay.getBoundingClientRect();
      const ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
      show(Math.round(ratio * (count - 1)));
    });
    overlay.addEventListener("pointerleave", hide);
    chart.append(overlay);

    container.onkeydown = (event) => {
      const last = count - 1;
      let next = null;
      if (event.key === "ArrowLeft") next = activeIndex < 0 ? last : Math.max(0, activeIndex - 1);
      else if (event.key === "ArrowRight") next = activeIndex < 0 ? last : Math.min(last, activeIndex + 1);
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = last;
      else if (event.key === "Escape" && activeIndex >= 0) { hide(); return; }
      else return;
      event.preventDefault();
      show(next);
    };
    container.onblur = hide;
    container.replaceChildren(chart, tooltip);
    if (activeIndex >= 0) show(Math.min(activeIndex, count - 1));
  }

  function scheduleRedraw() {
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      if (!$("#mastery-page").hidden) drawChart();
    });
  }

  /* ---------- 页面 ---------- */
  function setStatus(text, { retry = false } = {}) {
    $("#mastery-status").textContent = text;
    $("#mastery-retry").hidden = !retry;
  }

  function render() {
    const empty = !data.zones.length;
    $("#mastery-chart-card").hidden = empty;
    $("#mastery-explain").hidden = false;
    renderAlert();
    if (empty) {
      $("#mastery-zones").replaceChildren();
      setStatus("还没有记录。先去「新增记录」留下几条易错点，过一阵这里就会出现每个分区的曲线。");
      return;
    }
    setStatus("");
    $("#mastery-chart-title").textContent = `近 ${data.points.length - 1} 周`;
    hidden = new Set([...hidden].filter((zone) => data.zones.some((entry) => entry.zone === zone)));
    renderLegend();
    renderZones();
    renderTable();
    drawChart();
  }

  async function load() {
    const page = $("#mastery-page");
    const ticket = ++generation;
    page.setAttribute("aria-busy", "true");
    setStatus("正在计算各分区的掌握度…");
    try {
      const report = await fetchReport(true);
      if (ticket !== generation) return;
      data = report;
      render();
    } catch {
      if (ticket !== generation) return;
      data = null;
      $("#mastery-chart-card").hidden = true;
      $("#mastery-zones").replaceChildren();
      $("#mastery-alert").hidden = true;
      setStatus("暂时无法读取掌握度，请稍后重试。", { retry: true });
    } finally {
      if (ticket === generation) page.setAttribute("aria-busy", "false");
    }
  }

  function reset() {
    generation += 1;
    epoch += 1;
    data = null;
    cache = null;
    hidden = new Set();
    activeIndex = -1;
    $("#mastery-chart").replaceChildren();
    $("#mastery-zones").replaceChildren();
    $("#mastery-legend").replaceChildren();
    $("#mastery-table").replaceChildren();
    $("#mastery-alert").hidden = true;
    $("#mastery-chart-card").hidden = true;
    $("#mastery-explain").hidden = true;
    setStatus("");
    const card = $("#ov-fading-card");
    if (card) {
      card.hidden = true;
      card.querySelector("#ov-fading")?.replaceChildren();
    }
  }

  $("#mastery-retry").addEventListener("click", () => load());
  if (typeof ResizeObserver === "function") {
    observer = new ResizeObserver(scheduleRedraw);
    observer.observe($("#mastery-chart"));
  } else {
    window.addEventListener("resize", scheduleRedraw);
  }
  // 复习、新增、删除之后曲线会变；缓存作废，下次进入页面或总览时重新取。
  document.addEventListener("app:data-changed", () => { cache = null; });

  window.Mastery = { load, mountAlert, reset };
})();
