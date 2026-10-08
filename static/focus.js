"use strict";

/* 专注复习：全屏一次只看一张卡，键盘 1–5 打分，上方进度条，可以只练某一个分区。
   对外契约：window.FocusReview = { configure, start({ zone, tag, ids }) → Promise（关闭时 resolve）, close, isOpen }。
   复习队列支持每日上限；新接口未部署时回退到原来的到期列表。
   用户文本一律 textContent；评分请求不经过 app.js 的 run()，所以不会触发全局"禁用所有按钮"。 */
(() => {
  const GRADES = [
    { key: "1", quality: 0, label: "完全忘了", tone: "low", stamp: "再练" },
    { key: "2", quality: 2, label: "答错了", tone: "low", stamp: "再练" },
    { key: "3", quality: 3, label: "很吃力", tone: "mid", stamp: "过关" },
    { key: "4", quality: 4, label: "记得", tone: "good", stamp: "记住" },
    { key: "5", quality: 5, label: "很熟", tone: "good", stamp: "掌握" },
  ];
  const STAMPS = new Map(GRADES.map((grade) => [grade.quality, grade.stamp]));

  let root = null;
  let parts = null;
  let resolveClosed = null;
  let session = null;
  let returnFocus = null;
  let focusConfig = {};
  let focusClock = null;
  let focusMenu = null;

  function configure(options = {}) { focusConfig = { ...focusConfig, ...options }; }
  function modern() { return Boolean(window.ReviewExtras); }
  function userKey() {
    const value = focusConfig.getUser?.();
    return value && typeof value === "object" ? value.id : value;
  }
  function capture(active) {
    const { epoch, user, view } = active.context;
    return () => session === active && epoch === focusConfig.getEpoch?.()
      && user === userKey() && view === focusConfig.getView?.();
  }
  function hiddenByDefault() {
    try { return window.localStorage.getItem("review-hide-reason") !== "false"; } catch { return true; }
  }
  async function request(url, options = {}) {
    if (!focusConfig.api) return fetch(url, { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" }, ...options });
    try {
      const data = await focusConfig.api(url, options);
      return { ok: true, status: 200, json: async () => data };
    } catch (error) {
      if (!error.status) throw error;
      return { ok: false, status: error.status, json: async () => ({ detail: error.message }) };
    }
  }
  function tickClock() {
    if (!session) return;
    renderProgress();
    focusClock = setTimeout(tickClock, 1000);
    focusClock.unref?.();
  }
  function clearMenu() {
    focusMenu?.destroy?.();
    focusMenu = null;
  }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  /* ---------- 骨架 ---------- */
  function build() {
    root = node("div", "focus");
    root.id = "focus";
    root.hidden = true;
    const shell = node("div", "focus-shell");
    shell.setAttribute("role", "dialog");
    shell.setAttribute("aria-modal", "true");
    shell.setAttribute("aria-label", "专注复习");
    shell.tabIndex = -1;

    const bar = node("header", "focus-bar");
    const exit = node("button", "focus-exit", "×");
    exit.type = "button";
    exit.setAttribute("aria-label", "退出专注复习（Esc）");
    exit.addEventListener("click", () => close());
    const track = node("div", "focus-track");
    track.setAttribute("role", "progressbar");
    track.setAttribute("aria-label", "复习进度");
    track.setAttribute("aria-valuemin", "0");
    const fill = node("span", "focus-fill");
    track.append(fill);
    const count = node("span", "focus-count");
    bar.append(exit, track, count);

    const zones = node("div", "focus-zones");
    zones.setAttribute("role", "group");
    zones.setAttribute("aria-label", "只练某个分区");

    const stage = node("main", "focus-stage");
    stage.tabIndex = -1;

    const grades = node("footer", "focus-grades");
    const gradeRow = node("div", "focus-grade-row");
    gradeRow.setAttribute("role", "group");
    gradeRow.setAttribute("aria-label", "这条你掌握得怎么样");
    const gradeButtons = GRADES.map((grade) => {
      const button = node("button", `focus-grade focus-grade-${grade.tone}`);
      button.type = "button";
      button.dataset.quality = String(grade.quality);
      button.setAttribute("aria-keyshortcuts", grade.key);
      button.append(node("kbd", "focus-key", grade.key), node("span", "focus-grade-label", grade.label));
      button.addEventListener("click", () => gradeCurrent(grade.quality));
      gradeRow.append(button);
      return button;
    });
    const defer = node("button", "focus-defer", "稍后再看");
    defer.type = "button";
    defer.setAttribute("aria-keyshortcuts", "S");
    defer.addEventListener("click", () => deferCurrent());
    const hint = node("p", "focus-hint", "空格 显示思路 · 1–5 打分 · S 稍后再看 · Esc 退出");
    grades.append(gradeRow, defer, hint);

    const status = node("p", "focus-status");
    status.id = "focus-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    shell.append(bar, zones, stage, grades, status);
    root.append(shell);
    document.body.append(root);
    parts = { shell, exit, track, fill, count, zones, stage, grades, gradeRow, gradeButtons, defer, hint, status };
    root.addEventListener("keydown", onKeydown);
  }

  /* ---------- 队列 ---------- */
  function remaining() {
    if (!session) return [];
    return session.order
      .map((id) => session.items.get(id))
      .filter((item) => !session.finished.has(item.id) && (!session.zone || item.zone === session.zone));
  }
  function current() {
    return remaining()[0] || null;
  }
  function totalCount() {
    return session.done + remaining().length;
  }

  /* ---------- 渲染 ---------- */
  function renderProgress() {
    const total = totalCount();
    const position = total ? `${Math.min(session.done + 1, total)} / ${total}` : "0 / 0";
    const seconds = Math.max(0, Math.floor((Date.now() - session.startedAt) / 1000));
    const elapsed = `${String(Math.floor(seconds / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`;
    parts.count.textContent = modern() ? `第 ${position} 条 · 已用 ${elapsed}` : position;
    parts.fill.style.width = total ? `${Math.round((session.done / total) * 100)}%` : "0%";
    parts.track.setAttribute("aria-valuemax", String(total));
    parts.track.setAttribute("aria-valuenow", String(session.done));
    parts.track.setAttribute("aria-valuetext", `已完成 ${session.done} 条，共 ${total} 条`);
  }

  function renderZones() {
    const counts = new Map();
    for (const id of session.order) {
      const item = session.items.get(id);
      if (!session.finished.has(id)) counts.set(item.zone, (counts.get(item.zone) || 0) + 1);
    }
    const all = [...counts.values()].reduce((sum, value) => sum + value, 0);
    const chips = [["", `全部 ${all}`]].concat([...counts.entries()].map(([zone, value]) => [zone, `${zone} ${value}`]));
    parts.zones.hidden = counts.size < 2 && !session.zone;
    parts.zones.replaceChildren(...chips.map(([zone, text]) => {
      const chip = node("button", "focus-chip", text);
      chip.type = "button";
      chip.setAttribute("aria-pressed", String(session.zone === zone));
      chip.addEventListener("click", () => {
        if (!session || session.submitting) return;
        session.zone = zone;
        session.revealed = modern() && !hiddenByDefault();
        if (modern() && !session.ids && session.queueAvailable) { load(); return; }
        render();
        parts.stage.focus({ preventScroll: true });
      });
      return chip;
    }));
  }

  function renderCard(item) {
    const card = node("article", "focus-card");
    const meta = node("div", "focus-meta");
    const position = window.ProblemCards?.position(item, [...session.items.values()]);
    if (position) meta.append(node("span", "problem-position", position));
    meta.append(node("span", "focus-zone", item.zone));
    const reviewed = item.repetitions > 0 ? `已复习 ${item.repetitions} 次` : "第一次复习";
    meta.append(node("span", "", reviewed));
    const late = item.overdue_days > 0 ? `逾期 ${item.overdue_days} 天` : "今天到期";
    meta.append(node("span", item.overdue_days > 0 ? "focus-late" : "", late));
    const title = node("h2", "focus-title", item.title);
    title.id = "focus-title";
    const source = node("p", "focus-source", [item.zone, item.title, ...(item.tags || []).map((tag) => typeof tag === "string" ? tag : tag.name)].filter(Boolean).join(" · "));
    card.append(meta, source, title);
    const cause = node("section", "focus-section");
    cause.append(node("h3", "focus-label", "你当时的错因"), node("p", "focus-text multiline", item.description || "错因待 AI 诊断"));
    if (!modern() || session.revealed) card.append(cause);

    const reveal = node("button", "focus-reveal");
    reveal.type = "button";
    reveal.setAttribute("aria-expanded", String(session.revealed));
    reveal.setAttribute("aria-keyshortcuts", "Space");
    reveal.textContent = modern() ? "显示错因（空格）" : (session.revealed ? "收起思路与代码" : "显示我当时的思路与代码（空格）");
    reveal.hidden = modern() && session.revealed;
    reveal.addEventListener("click", () => toggleReveal());
    card.append(reveal);

    if (session.revealed) {
      const detail = node("div", "focus-detail");
      const thinking = node("section", "focus-section");
      thinking.append(node("h3", "focus-label", "当时的思路"), node("p", "focus-text multiline", item.thinking || "没有记录思路"));
      detail.append(thinking);
      if (item.code) {
        const code = node("section", "focus-section");
        code.append(node("h3", "focus-label", `当时的代码${item.language ? ` · ${item.language}` : ""}`));
        const block = node("pre", "code focus-code");
        block.textContent = item.code;
        block.tabIndex = 0;
        block.setAttribute("aria-label", "当时的代码，可滚动");
        code.append(block);
        detail.append(code);
      }
      card.append(detail);
    }
    if (modern()) {
      const active = session;
      const valid = capture(active);
      let committed = false;
      const validCard = () => valid() && (committed || current()?.id === item.id);
      focusMenu = window.ReviewExtras.menu({ item, isCurrent: validCard,
        status: parts.status, onAction: (action, days, result) => {
          if (!validCard()) return;
          committed = true;
          extraAction(active, item, action, days, result);
        } });
      card.append(focusMenu);
    }
    return card;
  }

  function renderSummary() {
    const stats = session.results;
    const wrap = node("article", "focus-card focus-summary");
    const heading = node("h2", "focus-title", stats.length ? "这一轮复习完成了" : "今天没有待复习的内容");
    heading.id = "focus-title";
    wrap.append(heading);
    if (stats.length) {
      const strong = stats.filter((entry) => entry.quality >= 4).length;
      const again = stats.filter((entry) => entry.quality < 3).length;
      wrap.append(node("p", "focus-text", `共复习 ${stats.length} 条：记得或很熟 ${strong} 条，需要再练 ${again} 条。`));
      const bars = node("div", "focus-dist");
      bars.setAttribute("aria-hidden", "true");
      for (const grade of GRADES) {
        const value = stats.filter((entry) => entry.quality === grade.quality).length;
        if (!value) continue;
        const segment = node("span", `focus-dist-${grade.tone}`);
        segment.style.flexGrow = String(value);
        segment.title = `${grade.label} ${value} 条`;
        bars.append(segment);
      }
      wrap.append(bars);
      const legend = node("ul", "focus-legend");
      for (const grade of GRADES) {
        const value = stats.filter((entry) => entry.quality === grade.quality).length;
        if (value) legend.append(node("li", "", `${grade.label} ${value}`));
      }
      wrap.append(legend);
    } else {
      wrap.append(node("p", "focus-text", "到期的易错点都复习完了。去记录新的发现，或者回总览看看进度。"));
    }
    const actions = node("div", "focus-summary-actions");
    if (session.queueCapped && session.capRemaining > 0) {
      wrap.append(node("p", "focus-cap-note", `今天已达上限，还有 ${session.capRemaining} 条明天再复习`));
      const more = node("button", "focus-continue", "再多练一点");
      more.type = "button";
      more.addEventListener("click", () => { if (session && !session.loading) { session.ignoreCap = true; load(); } });
      actions.append(more);
    }
    const done = node("button", "primary focus-done", "回到总览");
    done.type = "button";
    done.addEventListener("click", () => close({ view: "home" }));
    const stay = node("button", "focus-stay", "留在这里");
    stay.type = "button";
    stay.addEventListener("click", () => close());
    actions.append(done, stay);
    wrap.append(actions);
    return wrap;
  }

  function render() {
    if (!session) return;
    clearMenu();
    renderProgress();
    renderZones();
    if (session.loading) {
      parts.stage.replaceChildren(node("p", "focus-loading", "正在取今天要复习的内容…"));
      setGrading(false);
      return;
    }
    if (session.error) {
      const box = node("div", "focus-card");
      box.append(node("h2", "focus-title", "暂时取不到复习内容"), node("p", "focus-text", session.error));
      const retry = node("button", "primary", "重试");
      retry.type = "button";
      retry.addEventListener("click", () => load());
      box.append(retry);
      parts.stage.replaceChildren(box);
      setGrading(false);
      return;
    }
    const item = current();
    if (!item) {
      parts.stage.replaceChildren(renderSummary());
      setGrading(false);
      if (!session.summaryAnnounced) {
        session.summaryAnnounced = true;
        parts.status.textContent = session.results.length ? "这一轮复习完成了" : "今天没有待复习的内容";
      }
      return;
    }
    parts.stage.replaceChildren(renderCard(item));
    setGrading(true);
    parts.gradeRow.hidden = modern() && !session.revealed;
    for (const button of parts.gradeButtons) button.disabled = session.submitting;
    parts.defer.hidden = remaining().length < 2;
    parts.status.textContent = `${item.zone}：${item.title}`;
    if (modern()) {
      const active = session;
      const valid = capture(active);
      window.ReviewExtras.decorateGrades(parts.gradeButtons, item, () => valid() && current()?.id === item.id && current()?.version === item.version);
      const next = remaining()[1];
      if (next) window.ReviewExtras.preview(next, valid);
    }
  }

  function setGrading(enabled) {
    parts.grades.hidden = !enabled;
  }

  /* ---------- 动作 ---------- */
  function toggleReveal() {
    if (!session || !current()) return;
    if (modern() && session.revealed) return;
    session.revealed = !session.revealed;
    render();
    (modern() && session.revealed ? parts.gradeButtons[0] : parts.stage).focus({ preventScroll: true });
  }

  function deferCurrent() {
    if (!session || session.submitting) return;
    const item = current();
    if (!item || remaining().length < 2) return;
    session.order = session.order.filter((id) => id !== item.id).concat(item.id);
    session.revealed = modern() && !hiddenByDefault();
    render();
    parts.stage.focus({ preventScroll: true });
  }

  async function gradeCurrent(quality) {
    // 评分请求回来时，这一轮可能已经被关掉、甚至换成了新的一轮：所有收尾都认准发请求时的那一轮。
    const active = session;
    const item = current();
    if (!active || !item || active.submitting || (modern() && !active.revealed)) return;
    const valid = capture(active);
    if (!valid()) return;
    active.submitting = true;
    parts.grades.setAttribute("aria-busy", "true");
    let message = "";
    try {
      const response = await request(`/api/mistakes/${item.id}/review`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" },
        body: JSON.stringify({ quality, version: item.version }),
      });
      const data = await response.json().catch(() => ({}));
      if (!valid()) {
        // 已经退出（或开了新一轮）：这条评分在服务器上是存下了的，通知各处刷新数字，但不碰界面和新一轮的状态。
        if (!modern() && response.ok) document.dispatchEvent(new CustomEvent("app:data-changed", { detail: { reason: "focus-review" } }));
        return;
      }
      if (response.status === 401) {
        close();
        return;
      }
      if (response.status === 409) {
        // 别处已经评过分或还没到期：跳过这一条，不算本轮成绩。
        active.finished.add(item.id);
        message = typeof data.detail === "string" ? data.detail : "这一条已在别处更新，已跳过。";
      } else if (!response.ok) {
        parts.status.textContent = typeof data.detail === "string" ? data.detail : "评分没有保存，请再试一次。";
        return;
      } else {
        active.finished.add(item.id);
        active.done += 1;
        active.graded += 1;
        active.results.push({ id: item.id, quality, next: data.interval_days });
        if (modern()) window.ReviewExtras.rememberReview({ item, result: data, quality, isCurrent: valid,
          onUndo: (result) => {
            if (!valid()) return;
            active.items.set(item.id, { ...item, ...result });
            active.order = [item.id, ...active.order.filter((id) => id !== item.id)];
            if (active.zone && active.zone !== item.zone) active.zone = item.zone;
            active.finished.delete(item.id);
            active.results = active.results.filter((entry) => entry.id !== item.id);
            active.done = Math.max(0, active.done - 1);
            active.graded = Math.max(0, active.graded - 1);
            active.revealed = !hiddenByDefault();
            active.summaryAnnounced = false;
            render();
            parts.status.textContent = "已撤销";
            parts.stage.focus({ preventScroll: true });
          } });
        const stamp = STAMPS.get(quality);
        if (stamp && typeof window.stampSeal === "function") {
          const button = parts.grades.querySelector(`[data-quality="${quality}"]`);
          window.stampSeal(stamp, { anchor: button });
        }
        message = `已保存，下次复习约 ${data.interval_days} 天后`;
      }
      active.revealed = modern() && !hiddenByDefault();
      render();
      parts.status.textContent = message;
      parts.stage.focus({ preventScroll: true });
    } catch (error) {
      if (valid()) parts.status.textContent = error.message || "网络出错，评分没有保存，请再试一次。";
    } finally {
      active.submitting = false;
      if (valid()) {
        parts.grades.removeAttribute("aria-busy");
        for (const button of parts.gradeButtons) button.disabled = false;
      }
    }
  }

  function extraAction(active, item, action, days, result) {
    if (!capture(active)()) return;
    active.items.set(item.id, { ...item, ...result });
    if (action === "unsuspend") {
      active.finished.delete(item.id);
      active.order = [item.id, ...active.order.filter((id) => id !== item.id)];
      if (active.zone && active.zone !== item.zone) active.zone = item.zone;
    } else active.finished.add(item.id);
    active.revealed = !hiddenByDefault();
    active.summaryAnnounced = false;
    render();
    parts.status.textContent = action === "snooze" ? `已推迟 ${days} 天` : action === "suspend" ? "已暂停这条" : "已恢复复习";
    document.dispatchEvent(new CustomEvent("app:data-changed", { detail: { reason: "focus-review" } }));
    parts.stage.focus({ preventScroll: true });
  }

  /* ---------- 键盘 ---------- */
  function onKeydown(event) {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.defaultPrevented) return;
    if (event.key === "Escape") {
      if (focusMenu?.querySelector('[role="menu"]:not([hidden])')) {
        event.preventDefault(); event.stopPropagation(); focusMenu.close?.(true); return;
      }
      if (modern() && window.ReviewExtras.blockedKey(event)) return;
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === "Tab") {
      // 只算真正能聚焦的：被全局"忙碌"状态临时禁用的按钮不能当首尾。
      const items = [...parts.shell.querySelectorAll("button:not([hidden]):not(:disabled), input, a[href], pre[tabindex]")].filter((item) => item.offsetParent !== null && !item.closest("[hidden]"));
      if (!items.length) {
        event.preventDefault();
        return;
      }
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && (document.activeElement === first || document.activeElement === parts.shell || document.activeElement === parts.stage)) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
      return;
    }
    if (modern() && window.ReviewExtras.blockedKey(event)) return;
    const onButton = Boolean(event.target.closest?.("button"));
    const protectedTarget = modern() && Boolean(event.target.closest?.("input, textarea, select, a, [contenteditable]"));
    if ((event.key === " " || event.key === "Enter") && !onButton && !protectedTarget) {
      event.preventDefault();
      toggleReveal();
      return;
    }
    const grade = GRADES.find((entry) => entry.key === event.key);
    if (grade && current()) {
      event.preventDefault();
      gradeCurrent(grade.quality);
      return;
    }
    if ((event.key === "s" || event.key === "S") && current()) {
      event.preventDefault();
      deferCurrent();
    }
    if (modern() && current() && !session.submitting) {
      const action = event.key.toLowerCase() === "t" ? '[data-review-action="snooze"][data-days="1"]'
        : event.key.toLowerCase() === "p" ? '[data-review-action="suspend"]' : "";
      const button = action && focusMenu?.querySelector(action);
      if (button && !focusMenu.hidden) { event.preventDefault(); button.click(); }
    }
  }

  /* ---------- 加载 ---------- */
  async function load() {
    const active = session;
    if (!active) return;
    const valid = capture(active);
    if (!valid()) return;
    const ticket = ++active.loadTicket;
    active.loading = true;
    active.error = "";
    render();
    try {
      let data;
      if (modern() && active.ids) {
        const items = await Promise.all(active.ids.map(async (id) => {
          const response = await request(`/api/mistakes/${id}`);
          const detail = await response.json();
          if (!response.ok) throw new Error(typeof detail.detail === "string" ? detail.detail : "暂时取不到复习内容");
          return detail;
        }));
        data = { today: items[0]?.today || new Date().toISOString().slice(0, 10), items };
        active.queueCapped = false;
      } else if (modern()) {
        const params = new URLSearchParams();
        if (active.zone) params.set("zone", active.zone);
        if (active.tag) params.set("tag", active.tag);
        if (active.ignoreCap) params.set("ignore_cap", "true");
        const response = await request(`/api/review/queue?${params}`);
        if (!valid() || ticket !== active.loadTicket) return;
        data = await response.json();
        if (response.status === 404) data = null;
        else if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "暂时取不到复习内容");
        if (data) {
          active.queueAvailable = true;
          active.queueCapped = Boolean(data.capped);
          active.capRemaining = Math.max(0, Number(data.total_due || 0) - data.items.length);
        }
      }
      if (!data) {
        const params = new URLSearchParams({ due_only: "true" });
        if (active.tag) params.set("tag", active.tag);
        const response = await request(`/api/mistakes?${params}`);
        data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "暂时取不到复习内容");
        active.queueAvailable = false;
        active.queueCapped = false;
      }
      if (!valid() || ticket !== active.loadTicket) return;
      const today = data.today;
      let items = data.items;
      if (active.ids) items = items.filter((item) => active.ids.includes(item.id));
      active.items = new Map(items.map((item) => [item.id, {
        ...item,
        overdue_days: Math.max(0, Math.round((Date.parse(today) - Date.parse(item.due_date)) / 86400000)),
      }]));
      active.order = items.map((item) => item.id);
      active.finished = new Set();
      active.loading = false;
      active.summaryAnnounced = false;
    } catch (error) {
      if (!valid() || ticket !== active.loadTicket) return;
      active.loading = false;
      active.error = error.message || "请检查网络后重试。";
    }
    render();
    parts.stage.focus({ preventScroll: true });
  }

  /* ---------- 对外 ---------- */
  function isOpen() {
    return Boolean(root && !root.hidden);
  }

  function start({ zone = "", tag = "", ids = null } = {}) {
    if (document.documentElement.dataset.view !== "app" || isOpen()) return Promise.resolve();
    window.EmojiPicker?.close();
    window.CommandPalette?.close({ restore: false });
    window.AppShell?.closeMore?.({ restore: false });
    if (!root) build();
    returnFocus = document.activeElement;
    session = {
      zone, tag, ids, items: new Map(), order: [], finished: new Set(),
      results: [], done: 0, graded: 0, revealed: modern() && !hiddenByDefault(), loading: true, error: "", submitting: false, summaryAnnounced: false,
      startedAt: Date.now(), loadTicket: 0, queueCapped: false, capRemaining: 0, queueAvailable: false, ignoreCap: false,
      context: { epoch: focusConfig.getEpoch?.(), user: userKey(), view: focusConfig.getView?.() },
    };
    root.hidden = false;
    document.body.classList.add("focus-open");
    parts.shell.focus({ preventScroll: true });
    const closed = new Promise((resolve) => { resolveClosed = resolve; });
    load();
    if (modern()) tickClock();
    return closed;
  }

  function close({ view = "" } = {}) {
    if (!isOpen()) return;
    const graded = session?.graded || 0;
    clearTimeout(focusClock);
    clearMenu();
    session = null;
    root.hidden = true;
    document.body.classList.remove("focus-open");
    parts.stage.replaceChildren();
    if (returnFocus?.isConnected && returnFocus.offsetParent !== null) returnFocus.focus({ preventScroll: true });
    returnFocus = null;
    resolveClosed?.();
    resolveClosed = null;
    // 小部件（热力图、侧栏角标）靠 app:data-changed 刷新；页面本身由 app.js 听 focus:closed 重新加载或跳转。
    if (graded) document.dispatchEvent(new CustomEvent("app:data-changed", { detail: { reason: "focus-review" } }));
    document.dispatchEvent(new CustomEvent("focus:closed", { detail: { graded, view } }));
  }

  document.addEventListener("app:view-changed", () => { if (isOpen()) close(); });

  window.FocusReview = { configure, start, close, isOpen };
})();
