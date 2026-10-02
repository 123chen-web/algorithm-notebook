"use strict";

/* 复习热力图（大图 / 侧栏日历两种形态）。
   对外契约：window.ActivityWidgets = { mountHeatmap, refresh, reset }。
   数据来自 GET /api/stats/activity?weeks=26；只返回有活动的日子，这里补零。 */
(() => {
  const WEEKS = 26;
  const CACHE_MS = 60000;
  const WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"];
  const instances = new Map();
  let cache = null;
  let pending = null;
  let latest = 0;
  let epoch = 0;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function parseDay(text) {
    const [year, month, day] = text.split("-").map(Number);
    return new Date(year, month - 1, day, 12);
  }
  function isoDay(date) {
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${date.getFullYear()}-${month}-${day}`;
  }
  function addDays(date, count) {
    const next = new Date(date.getTime());
    next.setDate(next.getDate() + count);
    return next;
  }
  function mondayOf(date) {
    return addDays(date, -((date.getDay() + 6) % 7));
  }
  function label(date) {
    return `${date.getMonth() + 1}月${date.getDate()}日`;
  }

  function levelOf(entry) {
    if (!entry) return 0;
    const total = entry.reviews + entry.records;
    if (total >= 10) return 4;
    if (total >= 6) return 3;
    if (total >= 3) return 2;
    return total >= 1 ? 1 : 0;
  }
  function describe(date, entry) {
    if (!entry || (!entry.reviews && !entry.records)) return `${label(date)} · 没有记录`;
    const parts = [];
    if (entry.reviews) parts.push(`复习 ${entry.reviews} 次`);
    if (entry.records) parts.push(`新增 ${entry.records} 条`);
    return `${label(date)} · ${parts.join(" · ")}`;
  }

  // 数据变了（评分、新增、删除、跨午夜）就让缓存和在途的请求一起作废：
  // 作废之前发出的请求即使晚到，也不能再写缓存或冒充"最新"。
  function invalidate() {
    cache = null;
    pending = null;
    latest += 1;
  }

  async function request(force) {
    if (force) invalidate();
    if (cache && Date.now() - cache.at < CACHE_MS) return cache.data;
    if (pending) return pending;
    const startedIn = epoch;
    const mine = latest;
    const current = fetch(`/api/stats/activity?weeks=${WEEKS}`, { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } })
      .then((response) => {
        if (!response.ok) throw new Error(`activity ${response.status}`);
        return response.json();
      })
      .then((data) => {
        if (startedIn === epoch && mine === latest) cache = { at: Date.now(), data };
        return data;
      })
      .finally(() => { if (pending === current) pending = null; });
    pending = current;
    return current;
  }

  function entriesOf(data) {
    const map = new Map();
    for (const item of data.days) map.set(item.date, item);
    return map;
  }

  /* ---------- 大图 ---------- */
  function paintHeatmap(inst) {
    const { el, data } = inst;
    el.classList.add("heatmap");
    const grid = node("div", "heatmap-grid");
    grid.style.setProperty("--weeks", String(inst.weeks));
    const map = data ? entriesOf(data) : new Map();
    const start = data ? parseDay(data.from) : null;
    const today = data ? parseDay(data.today) : null;
    const labels = node("div", "heatmap-months");
    labels.style.setProperty("--weeks", String(inst.weeks));
    let previousMonth = -1;
    for (let column = 0; column < inst.weeks; column += 1) {
      const columnStart = start ? addDays(start, column * 7) : null;
      const month = columnStart ? columnStart.getMonth() : -1;
      const text = columnStart && month !== previousMonth ? `${month + 1}月` : "";
      if (columnStart) previousMonth = month;
      const cell = node("span", "heatmap-month", text);
      cell.style.gridColumn = String(column + 1);
      labels.append(cell);
      for (let row = 0; row < 7; row += 1) {
        const date = columnStart ? addDays(columnStart, row) : null;
        const cellNode = node("span", "heatmap-cell");
        cellNode.style.gridColumn = String(column + 1);
        cellNode.style.gridRow = String(row + 1);
        if (date && date > today) {
          cellNode.classList.add("is-future");
        } else if (date) {
          const entry = map.get(isoDay(date));
          cellNode.dataset.level = String(levelOf(entry));
          cellNode.title = describe(date, entry);
          if (date.getTime() === today.getTime()) cellNode.classList.add("is-today");
        } else {
          cellNode.dataset.level = "0";
        }
        grid.append(cellNode);
      }
    }
    const legend = node("div", "heatmap-legend");
    legend.setAttribute("aria-hidden", "true");
    legend.append(node("span", "", "少"));
    for (let level = 0; level <= 4; level += 1) {
      const swatch = node("span", "heatmap-cell");
      swatch.dataset.level = String(level);
      legend.append(swatch);
    }
    legend.append(node("span", "", "多"));
    const summary = data
      ? `近 ${inst.weeks} 周共复习 ${data.totals.reviews} 次、新增 ${data.totals.records} 条记录，活跃 ${data.totals.active_days} 天，当前连续 ${data.streak_days} 天`
      : "正在读取活动数据";
    const figure = node("div", "heatmap-figure");
    figure.setAttribute("role", "img");
    figure.setAttribute("aria-label", summary);
    labels.setAttribute("aria-hidden", "true");
    grid.setAttribute("aria-hidden", "true");
    figure.append(labels, grid);
    el.replaceChildren(figure, legend);
  }

  /* ---------- 侧栏日历 ---------- */
  function paintCalendar(inst) {
    const { el, data } = inst;
    el.classList.add("heatmap", "heatmap--calendar");
    const head = node("div", "cal-head");
    head.setAttribute("aria-hidden", "true");
    for (const name of WEEKDAYS) head.append(node("span", "", name));
    const grid = node("div", "cal-grid");
    grid.setAttribute("role", "grid");
    grid.setAttribute("aria-label", "近 5 周复习日历，用方向键移动，回车查看当天的记录");
    if (!data) {
      for (let index = 0; index < 35; index += 1) grid.append(node("span", "cal-cell is-skeleton"));
      el.replaceChildren(head, grid);
      return;
    }
    const map = entriesOf(data);
    const today = parseDay(data.today);
    const first = addDays(mondayOf(today), -28);
    const focusDay = inst.focusDay && parseDay(inst.focusDay) <= today && parseDay(inst.focusDay) >= first
      ? inst.focusDay : isoDay(today);
    inst.focusDay = focusDay;
    for (let week = 0; week < 5; week += 1) {
      const row = node("div", "cal-row");
      row.setAttribute("role", "row");
      for (let column = 0; column < 7; column += 1) {
        const date = addDays(first, week * 7 + column);
        const key = isoDay(date);
        const future = date > today;
        const entry = map.get(key);
        const cell = node(future ? "span" : "button", "cal-cell", String(date.getDate()));
        cell.setAttribute("role", "gridcell");
        cell.dataset.date = key;
        if (future) {
          cell.classList.add("is-future");
          cell.setAttribute("aria-hidden", "true");
        } else {
          cell.type = "button";
          cell.dataset.level = String(levelOf(entry));
          cell.tabIndex = key === focusDay ? 0 : -1;
          cell.title = describe(date, entry);
          cell.setAttribute("aria-label", describe(date, entry).replaceAll(" · ", "，"));
          if (date.getTime() === today.getTime()) cell.classList.add("is-today");
        }
        row.append(cell);
      }
      grid.append(row);
    }
    el.replaceChildren(head, grid);
  }

  function calendarCells(inst) {
    return [...inst.el.querySelectorAll("button.cal-cell")];
  }

  // 监听器按元素只绑一次（退出再登录会对同一个元素重新挂载），处理函数里每次取当前的实例。
  const bound = new WeakSet();
  function bindCalendar(el) {
    if (bound.has(el)) return;
    bound.add(el);
    el.addEventListener("click", (event) => {
      const inst = instances.get(el);
      const cell = event.target.closest("button.cal-cell");
      if (!inst || !cell || !el.contains(cell)) return;
      inst.focusDay = cell.dataset.date;
      inst.onSelectDay?.(cell.dataset.date);
    });
    el.addEventListener("keydown", (event) => {
      const inst = instances.get(el);
      const cell = event.target.closest?.("button.cal-cell");
      if (!inst || !cell) return;
      const cells = calendarCells(inst);
      const index = cells.indexOf(cell);
      const step = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 }[event.key];
      let target = null;
      if (step !== undefined) {
        target = cells[index + step] || null;
      } else if (event.key === "Home") {
        target = cells.find((item) => item.dataset.date >= isoDay(mondayOf(parseDay(cell.dataset.date)))) || null;
      } else if (event.key === "End") {
        const sunday = isoDay(addDays(mondayOf(parseDay(cell.dataset.date)), 6));
        target = [...cells].reverse().find((item) => item.dataset.date <= sunday) || null;
      } else {
        return;
      }
      event.preventDefault();
      if (!target) return;
      cell.tabIndex = -1;
      target.tabIndex = 0;
      inst.focusDay = target.dataset.date;
      target.focus();
    });
    el.addEventListener("focusin", (event) => {
      const inst = instances.get(el);
      const cell = event.target.closest?.("button.cal-cell");
      if (!inst || !cell) return;
      for (const item of calendarCells(inst)) item.tabIndex = item === cell ? 0 : -1;
      inst.focusDay = cell.dataset.date;
    });
  }

  /* ---------- 公共流程 ---------- */
  function paint(inst) {
    if (!inst.el.isConnected) return;
    const hadFocus = inst.calendar && inst.el.contains(document.activeElement);
    if (inst.state === "error") {
      inst.el.classList.remove("heatmap--calendar");
      inst.el.classList.add("heatmap");
      const text = node("p", "heatmap-error", "暂时无法读取活动数据。");
      const retry = node("button", "heatmap-retry", "重试");
      retry.type = "button";
      retry.addEventListener("click", () => load(inst, true));
      inst.el.setAttribute("aria-busy", "false");
      inst.el.replaceChildren(text, retry);
      return;
    }
    inst.el.setAttribute("aria-busy", String(inst.state === "loading"));
    if (inst.calendar) paintCalendar(inst);
    else paintHeatmap(inst);
    if (hadFocus) inst.el.querySelector('button.cal-cell[tabindex="0"]')?.focus();
  }

  async function load(inst, force = false) {
    const token = ++inst.token;
    if (!inst.data) inst.state = "loading";
    paint(inst);
    try {
      const data = await request(force);
      if (inst.token !== token || instances.get(inst.el) !== inst) return;
      inst.data = data;
      inst.state = "ready";
      inst.onData?.(data);
    } catch {
      if (inst.token !== token || instances.get(inst.el) !== inst) return;
      inst.state = "error";
    }
    paint(inst);
  }

  function mountHeatmap(el, { weeks = WEEKS, calendar = false, onSelectDay, onData } = {}) {
    if (!el) return Promise.resolve();
    const inst = {
      el, weeks: Math.min(WEEKS, Math.max(1, weeks)), calendar: Boolean(calendar),
      onSelectDay, onData, state: "loading", data: null, token: 0, bound: false, focusDay: null,
    };
    const previous = instances.get(el);
    if (previous) previous.token = -1;
    instances.set(el, inst);
    if (inst.calendar) bindCalendar(el);
    return load(inst);
  }

  function refresh() {
    invalidate();
    return Promise.all([...instances.values()].map((inst) => load(inst)));
  }

  function reset() {
    epoch += 1;
    invalidate();
    for (const inst of instances.values()) {
      inst.token = -1;
      inst.data = null;
      inst.state = "loading";
      inst.focusDay = null;
      inst.el.replaceChildren();
      inst.el.removeAttribute("aria-busy");
    }
  }

  document.addEventListener("app:data-changed", () => { if (instances.size) refresh(); });

  window.ActivityWidgets = { mountHeatmap, refresh, reset, isMounted: (el) => instances.has(el) };
})();
