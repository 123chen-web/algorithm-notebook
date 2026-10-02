"use strict";

/* 专注复习：全屏一次只看一张卡，键盘 1–5 打分，上方进度条，可以只练某一个分区。
   对外契约：window.FocusReview = { start({ zone, tag, ids }) → Promise（关闭时 resolve）, close, isOpen }。
   数据与评分都走现有接口：GET /api/mistakes?due_only=true、POST /api/mistakes/{id}/review。
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

    const status = node("p", "focus-sr-only");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    shell.append(bar, zones, stage, grades, status);
    root.append(shell);
    document.body.append(root);
    parts = { shell, exit, track, fill, count, zones, stage, grades, gradeButtons, defer, hint, status };
    root.addEventListener("keydown", onKeydown);
  }

  /* ---------- 队列 ---------- */
  function remaining() {
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
    parts.count.textContent = total ? `${Math.min(session.done + 1, total)} / ${total}` : "0 / 0";
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
        session.zone = zone;
        session.revealed = false;
        render();
        parts.stage.focus({ preventScroll: true });
      });
      return chip;
    }));
  }

  function renderCard(item) {
    const card = node("article", "focus-card");
    const meta = node("div", "focus-meta");
    meta.append(node("span", "focus-zone", item.zone));
    const reviewed = item.repetitions > 0 ? `已复习 ${item.repetitions} 次` : "第一次复习";
    meta.append(node("span", "", reviewed));
    const late = item.overdue_days > 0 ? `逾期 ${item.overdue_days} 天` : "今天到期";
    meta.append(node("span", item.overdue_days > 0 ? "focus-late" : "", late));
    const title = node("h2", "focus-title", item.title);
    title.id = "focus-title";
    const cause = node("section", "focus-section");
    cause.append(node("h3", "focus-label", "你当时的错因"), node("p", "focus-text multiline", item.description || "错因待 AI 诊断"));
    card.append(meta, title, cause);

    const reveal = node("button", "focus-reveal");
    reveal.type = "button";
    reveal.setAttribute("aria-expanded", String(session.revealed));
    reveal.setAttribute("aria-keyshortcuts", "Space");
    reveal.textContent = session.revealed ? "收起思路与代码" : "显示我当时的思路与代码（空格）";
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
    parts.defer.hidden = remaining().length < 2;
    parts.status.textContent = `${item.zone}：${item.title}`;
  }

  function setGrading(enabled) {
    parts.grades.hidden = !enabled;
  }

  /* ---------- 动作 ---------- */
  function toggleReveal() {
    if (!session || !current()) return;
    session.revealed = !session.revealed;
    render();
    parts.stage.focus({ preventScroll: true });
  }

  function deferCurrent() {
    const item = current();
    if (!item || remaining().length < 2) return;
    session.order = session.order.filter((id) => id !== item.id).concat(item.id);
    session.revealed = false;
    render();
    parts.stage.focus({ preventScroll: true });
  }

  async function gradeCurrent(quality) {
    // 评分请求回来时，这一轮可能已经被关掉、甚至换成了新的一轮：所有收尾都认准发请求时的那一轮。
    const active = session;
    const item = current();
    if (!active || !item || active.submitting) return;
    active.submitting = true;
    parts.grades.setAttribute("aria-busy", "true");
    try {
      const response = await fetch(`/api/mistakes/${item.id}/review`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" },
        body: JSON.stringify({ quality, version: item.version }),
      });
      const data = await response.json().catch(() => ({}));
      if (session !== active) {
        // 已经退出（或开了新一轮）：这条评分在服务器上是存下了的，通知各处刷新数字，但不碰界面和新一轮的状态。
        if (response.ok) document.dispatchEvent(new CustomEvent("app:data-changed", { detail: { reason: "focus-review" } }));
        return;
      }
      if (response.status === 401) {
        close();
        return;
      }
      if (response.status === 409) {
        // 别处已经评过分或还没到期：跳过这一条，不算本轮成绩。
        active.finished.add(item.id);
        parts.status.textContent = "这一条已在别处更新，已跳过。";
      } else if (!response.ok) {
        parts.status.textContent = typeof data.detail === "string" ? data.detail : "评分没有保存，请再试一次。";
        return;
      } else {
        active.finished.add(item.id);
        active.done += 1;
        active.graded += 1;
        active.results.push({ id: item.id, quality, next: data.interval_days });
        const stamp = STAMPS.get(quality);
        if (stamp && typeof window.stampSeal === "function") {
          const button = parts.grades.querySelector(`[data-quality="${quality}"]`);
          window.stampSeal(stamp, { anchor: button });
        }
        parts.status.textContent = `已保存，下次复习约 ${data.interval_days} 天后`;
      }
      active.revealed = false;
      render();
      parts.stage.focus({ preventScroll: true });
    } catch {
      if (session === active) parts.status.textContent = "网络出错，评分没有保存，请再试一次。";
    } finally {
      active.submitting = false;
      if (session === active) parts.grades.removeAttribute("aria-busy");
    }
  }

  /* ---------- 键盘 ---------- */
  function onKeydown(event) {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
      return;
    }
    if (event.key === "Tab") {
      // 只算真正能聚焦的：被全局"忙碌"状态临时禁用的按钮不能当首尾。
      const items = [...parts.shell.querySelectorAll("button:not([hidden]):not(:disabled), pre[tabindex]")].filter((item) => item.offsetParent !== null);
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
    const onButton = Boolean(event.target.closest?.("button"));
    if ((event.key === " " || event.key === "Enter") && !onButton) {
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
  }

  /* ---------- 加载 ---------- */
  async function load() {
    session.loading = true;
    session.error = "";
    render();
    const ticket = session.ticket;
    try {
      // 始终取全部分区的到期条目，"只练某个分区"在本地过滤，随时能切回全部。
      const params = new URLSearchParams({ due_only: "true" });
      if (session.tag) params.set("tag", session.tag);
      const response = await fetch(`/api/mistakes?${params}`, { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } });
      if (!response.ok) throw new Error(`mistakes ${response.status}`);
      const data = await response.json();
      if (!session || ticket !== session.ticket) return;
      const today = data.today;
      let items = data.items;
      if (session.ids) items = items.filter((item) => session.ids.includes(item.id));
      session.items = new Map(items.map((item) => [item.id, {
        ...item,
        overdue_days: Math.max(0, Math.round((Date.parse(today) - Date.parse(item.due_date)) / 86400000)),
      }]));
      session.order = items.map((item) => item.id);
      session.finished = new Set();
      session.loading = false;
    } catch {
      if (!session || ticket !== session.ticket) return;
      session.loading = false;
      session.error = "请检查网络后重试。";
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
      ticket: Math.random(), zone, tag, ids, items: new Map(), order: [], finished: new Set(),
      results: [], done: 0, graded: 0, revealed: false, loading: true, error: "", submitting: false, summaryAnnounced: false,
    };
    root.hidden = false;
    document.body.classList.add("focus-open");
    parts.shell.focus({ preventScroll: true });
    const closed = new Promise((resolve) => { resolveClosed = resolve; });
    load();
    return closed;
  }

  function close({ view = "" } = {}) {
    if (!isOpen()) return;
    const graded = session?.graded || 0;
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

  window.FocusReview = { start, close, isOpen };
})();
