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
    // 复习=重做：三种复习方式（只在算法分区），与主复习页同一套流程。
    const redoModes = node("div", "redo-modes");
    redoModes.setAttribute("role", "group");
    redoModes.setAttribute("aria-label", "选择复习方式");
    const redoModeButtons = {};
    for (const [mode, label] of [["redo", "去原题限时重做"], ["similar", "做一道类似题"], ["rewrite", "默写关键代码"], ["recall", "只回忆（自评）"]]) {
      const button = node("button", "redo-mode", label);
      button.type = "button";
      button.dataset.mode = mode;
      button.setAttribute("aria-pressed", "false");
      button.addEventListener("click", () => focusSelectRedoMode(mode));
      redoModes.append(button);
      redoModeButtons[mode] = button;
    }
    const redoHint = node("p", "redo-hint", "这条用重做更靠谱");
    redoHint.hidden = true;
    const recallBadge = node("span", "redo-badge", "自评");
    recallBadge.hidden = true;
    const redoPanel = node("div", "redo-panel-host");
    const hint = node("p", "focus-hint", "空格 显示思路 · 1–5 打分 · S 稍后再看 · Esc 退出");
    grades.append(redoModes, redoHint, gradeRow, recallBadge, redoPanel, defer, hint);

    const status = node("p", "focus-status");
    status.id = "focus-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    shell.append(bar, zones, stage, grades, status);
    root.append(shell);
    document.body.append(root);
    parts = { shell, exit, track, fill, count, zones, stage, grades, gradeRow, gradeButtons, defer, hint, status,
      redoModes, redoModeButtons, redoHint, recallBadge, redoPanel };
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
    // 复习手感：错因翻牌。toggle 刚触发时先保持未翻，由 render() 在下一帧翻过去播动画；
    // 其他已揭示的渲染直接呈现背面，不播动画。
    if (modern()) window.ReviewFeel?.wrapFlip(cause, Boolean(session.revealed && !session.flipPending));

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
      if (item.correct_code) {
        const right = node("section", "focus-section");
        right.append(node("h3", "focus-label", "正确代码"));
        const rightBlock = node("pre", "code focus-code");
        rightBlock.textContent = item.correct_code;
        rightBlock.tabIndex = 0;
        rightBlock.setAttribute("aria-label", "正确代码，可滚动");
        right.append(rightBlock);
        detail.append(right);
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
      if (focusMenu) card.append(focusMenu);
    }
    // 复习手感：滑动评分（走原来那颗评分按钮的流程）
    if (modern()) {
      window.ReviewFeel?.attachSwipe(card, {
        gradeButton: (quality) => parts.gradeButtons.find((button) => button.dataset.quality === String(quality)),
        canSwipe: () => Boolean(session && session.revealed && !session.submitting),
        onCommitTarget: () => parts.stage,
      });
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
    // 复习手感：滑动提交后，下一张轻轻升起
    window.ReviewFeel?.consumeEnter(parts.stage);
    // 复习手感：刚点"显示错因"时，把错因卡翻过去（下一帧播翻牌动画）
    if (session.flipPending) {
      session.flipPending = false;
      const flipped = parts.stage.querySelector(".rf-flip");
      if (flipped) {
        const run = () => window.ReviewFeel?.setFlipped(flipped, true);
        if (typeof window.requestAnimationFrame === "function") window.requestAnimationFrame(() => window.requestAnimationFrame(run));
        else run();
      }
    }
    parts.gradeRow.hidden = modern() && !session.revealed;
    // 算法题的自评没有“很熟”：想拿它必须做题验证（服务端也会封顶）。
    const capped = item.zone === "算法";
    for (const button of parts.gradeButtons) {
      const top = button.dataset.quality === "5";
      button.hidden = top && capped;
      button.disabled = session.submitting || (top && capped);
    }
    parts.defer.hidden = remaining().length < 2;
    parts.status.textContent = `${item.zone}：${item.title}`;
    // 复习=重做：切题时重置方式选择并异步选默认。
    if (window.RedoFlow) focusSyncRedoModes(item);
    else { parts.redoModes.hidden = true; parts.redoHint.hidden = true; parts.recallBadge.hidden = true; parts.redoPanel.replaceChildren(); }
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

  /* ---------- 复习=重做：三种复习方式 ---------- */
  function focusRedoAvailable(item) {
    return {
      rewrite: Boolean((item.correct_code || "").trim()),
      redo: Boolean((item.source_url || "").trim()),
      similar: true,
    };
  }

  // 上一次复习只是自评：这次建议用做题验证（队列接口给每条带上 last_review_mode）。
  function focusNeedsVerification(item) {
    return (item.last_review_mode || "") === "recall";
  }

  async function focusRedoPreference() {
    if (session.redoPreference) return session.redoPreference;
    try {
      const response = await request("/api/me/review-settings", { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } });
      const data = await response.json().catch(() => ({}));
      session.redoPreference = data.redo_preference === "recall_only" ? "recall_only" : "redo_first";
    } catch (error) {
      session.redoPreference = "recall_only";
    }
    return session.redoPreference;
  }

  async function focusPickDefaultMode(item) {
    const avail = focusRedoAvailable(item);
    let hint = false;
    if (window.RedoFlow && (avail.rewrite || avail.redo)) {
      try {
        const response = await request(`/api/mistakes/${item.id}/redo-hint`, { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } });
        hint = Boolean((await response.json().catch(() => ({}))).hint);
      } catch (error) { hint = false; }
    }
    const preference = await focusRedoPreference();
    if (hint && avail.rewrite) return { mode: "rewrite", hint };
    if (hint && avail.redo) return { mode: "redo", hint };
    const nudge = focusNeedsVerification(item);
    if (nudge || preference !== "recall_only") {
      if (avail.redo) return { mode: "redo", hint };
      if (avail.rewrite) return { mode: "rewrite", hint };
      if (nudge && avail.similar) return { mode: "similar", hint };
    }
    return { mode: "recall", hint };
  }

  function focusApplyRedoMode() {
    const item = current();
    // 复习方式在“显示错因”之前就能选：重做和类似题要先动手，不能先看到答案。
    const show = Boolean(session && modern() && item && item.zone === "算法" && window.RedoFlow);
    parts.redoModes.hidden = !show;
    parts.redoPanel.replaceChildren();
    const mode = session.redoMode;
    const recall = !show || !mode || mode === "recall";
    parts.gradeRow.hidden = !recall || (modern() && !session.revealed);
    parts.recallBadge.hidden = !show || !recall || !session.revealed;
    if (show && mode === "rewrite") parts.redoPanel.append(focusBuildRewritePanel(item));
    else if (show && mode === "redo") parts.redoPanel.append(focusBuildRedoPanel(item));
    else if (show && mode === "similar") parts.redoPanel.append(focusBuildSimilarPanel(item));
  }

  function focusSelectRedoMode(mode) {
    const item = current();
    if (!session || !item || session.redoMode === mode) return;
    if (session.redoTimer) { session.redoTimer.cancel(); session.redoTimer = null; }
    session.redoMode = mode;
    for (const [name, button] of Object.entries(parts.redoModeButtons)) {
      button.setAttribute("aria-pressed", String(name === mode));
    }
    focusApplyRedoMode();
  }

  // 每道题第一次显示时异步选默认方式；切题时重置。
  function focusSyncRedoModes(item) {
    if (session.redoItemId !== item.id) {
      session.redoItemId = item.id;
      session.redoMode = null;
      if (session.redoTimer) { session.redoTimer.cancel(); session.redoTimer = null; }
      const avail = focusRedoAvailable(item);
      parts.redoModeButtons.rewrite.disabled = !avail.rewrite;
      parts.redoModeButtons.redo.disabled = !avail.redo;
      parts.redoModeButtons.similar.disabled = !avail.similar;
      for (const button of Object.values(parts.redoModeButtons)) button.setAttribute("aria-pressed", "false");
      parts.redoHint.hidden = true;
      const active = session;
      (async () => {
        const { mode, hint } = await focusPickDefaultMode(item);
        if (session !== active || current()?.id !== item.id) return;
        parts.redoHint.hidden = !hint;
        session.redoMode = mode;
        for (const [name, button] of Object.entries(parts.redoModeButtons)) {
          button.setAttribute("aria-pressed", String(name === mode));
        }
        focusApplyRedoMode();
      })();
    }
    focusApplyRedoMode();
  }

  // 做题验证交卷：成绩已由结果算出，直接提交，不再让用户二次选分。
  function focusSubmitVerified(mode, picked) {
    return gradeCurrent(picked.quality, mode, picked.evidence);
  }

  function focusBuildRewritePanel(item) {
    const panel = node("div", "redo-panel");
    panel.append(node("h4", "", "默写关键代码"));
    if ((item.counterexample || "").trim()) {
      panel.append(node("p", "section-label", "最小反例"));
      const pre = node("pre", "code", item.counterexample);
      panel.append(pre);
    }
    panel.append(node("p", "section-label", "你记的错因"));
    panel.append(node("p", "focus-text multiline", item.description || ""));
    const area = document.createElement("textarea");
    area.className = "redo-write-area";
    area.setAttribute("aria-label", "默写代码输入区");
    area.placeholder = "把关键几行写出来，不用整段照抄";
    const compare = node("button", "primary", "对照");
    compare.type = "button";
    const resultHost = node("div");
    compare.addEventListener("click", () => {
      const diff = window.RedoFlow.diffLines(item.correct_code, area.value);
      resultHost.replaceChildren();
      resultHost.append(node("p", "redo-suggest", `相似度 ${Math.round(diff.similarity * 100)}%`));
      const diffBox = node("div", "redo-diff");
      for (const line of diff.missing) diffBox.append(node("pre", "redo-missing", `− 没写出：${line}`));
      for (const line of diff.extra) diffBox.append(node("pre", "redo-extra", `＋ 多写了：${line}`));
      if (!diff.missing.length && !diff.extra.length) diffBox.append(node("p", "muted", "逐行一致，漂亮。"));
      resultHost.append(diffBox);
      resultHost.append(window.RedoFlow.buildRewriteSubmit({
        similarity: diff.similarity,
        onPick: (picked) => focusSubmitVerified("rewrite", picked),
      }));
      compare.disabled = true;
    });
    panel.append(area, compare, resultHost);
    return panel;
  }

  function focusBuildRedoPanel(item) {
    const panel = node("div", "redo-panel");
    panel.append(node("h4", "", "去原题限时重做"));
    const limitRow = node("div", "redo-limit-row");
    limitRow.append(node("span", "", "时限"));
    const select = document.createElement("select");
    select.setAttribute("aria-label", "重做时限");
    for (const minutes of window.RedoFlow.REDO_LIMITS) {
      const option = document.createElement("option");
      option.value = String(minutes);
      option.textContent = `${minutes} 分钟`;
      if (minutes === window.RedoFlow.DEFAULT_REDO_LIMIT) option.selected = true;
      select.append(option);
    }
    limitRow.append(select);
    const start = node("button", "primary", "开始，在新标签页打开原题");
    start.type = "button";
    const timerEl = node("p", "redo-timer", "");
    timerEl.setAttribute("role", "timer");
    const askHost = node("div");
    const showAsk = () => {
      askHost.replaceChildren(window.RedoFlow.buildResultAsk({
        mode: "redo",
        seconds: () => (session.redoTimer ? session.redoTimer.elapsed() : 0),
        limitSeconds: Number(select.value) * 60,
        onPick: (picked) => {
          if (session.redoTimer) { session.redoTimer.cancel(); session.redoTimer = null; }
          focusSubmitVerified("redo", picked);
        },
      }));
    };
    start.addEventListener("click", () => {
      window.open(item.source_url, "_blank", "noopener");
      const limitSeconds = Number(select.value) * 60;
      start.disabled = true;
      select.disabled = true;
      session.redoTimer = window.RedoFlow.startTimer(
        limitSeconds,
        (remaining) => { timerEl.textContent = `剩余 ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`; },
        () => { timerEl.textContent = "时间到"; showAsk(); },
      );
      showAsk();
    });
    panel.append(limitRow, start, timerEl, askHost);
    return panel;
  }

  function focusBuildSimilarPanel(item) {
    const active = session;
    const call = async (path, options = {}) => {
      const response = await request(path, {
        method: "GET", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" }, ...options,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "请求失败");
      return data;
    };
    return window.RedoFlow.buildSimilarPanel({
      api: call,
      mistakeId: item.id,
      isAlive: () => session === active && current()?.id === item.id && session.redoMode === "similar",
      quickAdd: (picked) => (window.Similar ? window.Similar.quickAdd(picked, call) : Promise.resolve()),
      onTimer: (timer) => { session.redoTimer = timer; },
      onPick: (picked) => focusSubmitVerified("similar", picked),
    });
  }

  /* ---------- 动作 ---------- */
  function toggleReveal() {
    if (!session || !current()) return;
    if (modern() && session.revealed) return;
    session.revealed = !session.revealed;
    if (modern() && session.revealed) session.flipPending = true;
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

  async function gradeCurrent(quality, mode = "recall", evidence = "") {
    // 评分请求回来时，这一轮可能已经被关掉、甚至换成了新的一轮：所有收尾都认准发请求时的那一轮。
    const active = session;
    const item = current();
    if (!active || !item || active.submitting) return;
    // 只回忆要先看错因再自评；做题验证（默写、重做、类似题）不需要先看答案。
    if (modern() && !active.revealed && mode === "recall") return;
    // 算法题的自评没有“很熟”。
    if (mode === "recall" && quality === 5 && item.zone === "算法") return;
    const valid = capture(active);
    if (!valid()) return;
    active.submitting = true;
    parts.grades.setAttribute("aria-busy", "true");
    let message = "";
    const payload = { quality, version: item.version };
    if (mode !== "recall") payload.mode = mode;
    if (evidence) payload.evidence = evidence;
    try {
      const response = await request(`/api/mistakes/${item.id}/review`, {
        method: "POST", credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" },
        body: JSON.stringify(payload),
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
          window.stampSeal(stamp, { anchor: button, tone: quality <= 2 ? "ink" : undefined });
          window.ReviewFeel?.buzz();
        }
        message = `已保存，下次复习约 ${data.interval_days} 天后`;
        if (data.leech_suspended) {
          message = "这道题连续失败 8 次，已自动暂停，建议换种方式学习，可随时恢复。";
        }
      }
      active.revealed = modern() && !hiddenByDefault();
      // 评分已提交：停掉可能还在跑的重做计时器。
      if (active.redoTimer) { active.redoTimer.cancel(); active.redoTimer = null; }
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
    const arrowQuality = event.key === "ArrowRight" ? 4 : event.key === "ArrowLeft" ? 0 : null;
    const grade = arrowQuality !== null
      ? GRADES.find((entry) => entry.quality === arrowQuality)
      : GRADES.find((entry) => entry.key === event.key);
    if (grade && current()) {
      // 重做模式（默写/限时）进行中时，数字键不触发普通的回忆评分。
      if (["rewrite", "redo", "similar"].includes(session.redoMode)) return;
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
