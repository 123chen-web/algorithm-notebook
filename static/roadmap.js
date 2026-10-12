"use strict";

/* 主站「学习路线」视图：把 static/data/patterns.json 的套路搬进登录后的界面，
   可以给每个套路标记「未学 / 学习中 / 已掌握」，看分类与总体进度，并和自己记过的错题联动。
   对外契约：window.Roadmap = { configure({ api, getUser, getEpoch }), load(), reset() }。
   - 请求走宿主传入的 api()；每个响应都按登录代次（getEpoch()）、账号（getUser().id）和
     请求序号校验，登出 / 换号 / reset() 之后晚到的响应一律丢弃；
   - 勾状态、勾例题都是"先动按钮再发请求"，失败或离线时静默回滚到原来的样子；
   - 一切来自服务器的文字都用 textContent。 */
(() => {
  const R = window.PatternRender;
  const $ = (selector) => document.querySelector(selector);
  const node = R.node;

  // 套路清单是静态资源，和登录状态无关，进程内缓存一份即可。
  let catalog = null;
  let catalogPromise = null;

  let hooks = null;
  let generation = 0;
  let sequence = 0;

  let patterns = [];
  let byId = new Map();
  let progress = {};        // {pattern_id: "learning" | "mastered"}；没有记录 = 未学
  let examplesDone = {};    // {pattern_id: Set(key)}
  let mistakeCounts = {};   // {pattern_id: n}

  let category = "";
  let level = "";
  let status = "";
  let query = "";

  /* ---------- 迟到响应守卫（与 recommend.js 同款） ---------- */

  function ticket() {
    sequence += 1;
    return { sequence, generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }

  function current(request) {
    const user = hooks?.getUser();
    return Boolean(hooks && user && request.generation === generation
      && request.sequence === sequence
      && request.epoch === hooks.getEpoch() && request.userId === user.id);
  }

  /* ---------- 数据 ---------- */

  async function loadCatalog() {
    if (catalog) return catalog;
    if (!catalogPromise) {
      catalogPromise = fetch("/static/data/patterns.json", { credentials: "omit" })
        .then((response) => {
          if (!response.ok) throw new Error(String(response.status));
          return response.json();
        });
    }
    catalog = await catalogPromise;
    return catalog;
  }

  function applyState(data) {
    progress = {};
    for (const [patternId, value] of Object.entries(data.progress || {})) {
      if (value === "learning" || value === "mastered") progress[patternId] = value;
    }
    examplesDone = {};
    for (const [patternId, keys] of Object.entries(data.examples_done || {})) {
      examplesDone[patternId] = new Set(Array.isArray(keys) ? keys : []);
    }
    mistakeCounts = {};
    for (const [patternId, value] of Object.entries(data.mistake_counts || {})) {
      const count = Number(value);
      if (Number.isFinite(count) && count > 0) mistakeCounts[patternId] = count;
    }
  }

  function statusOf(patternId) {
    return progress[patternId] || "none";
  }

  /* ---------- 写入 ---------- */

  async function putStatus(patternId, next) {
    const request = ticket();
    try {
      await hooks.api(`/api/roadmap/${encodeURIComponent(patternId)}`, {
        method: "PUT",
        body: JSON.stringify({ status: next }),
      });
      if (!current(request)) return false;
      if (next === "none") delete progress[patternId];
      else progress[patternId] = next;
      renderSummary();
      return true;
    } catch {
      return false; // 离线 / 失败 / 换了账号：静默回滚
    }
  }

  async function putExample(patternId, key, done) {
    const request = ticket();
    try {
      await hooks.api(
        `/api/roadmap/${encodeURIComponent(patternId)}/examples/${encodeURIComponent(key)}`,
        { method: "PUT", body: JSON.stringify({ done }) },
      );
      if (!current(request)) return false;
      const set = examplesDone[patternId] || new Set();
      if (done) set.add(key); else set.delete(key);
      examplesDone[patternId] = set;
      return true;
    } catch {
      return false;
    }
  }

  /* ---------- 渲染：进度 ---------- */

  function categoryStats() {
    const map = new Map();
    for (const pattern of patterns) {
      const entry = map.get(pattern.category) || { total: 0, mastered: 0, learning: 0 };
      entry.total += 1;
      const state = statusOf(pattern.id);
      if (state === "mastered") entry.mastered += 1;
      else if (state === "learning") entry.learning += 1;
      map.set(pattern.category, entry);
    }
    return map;
  }

  function renderSummary() {
    const total = patterns.length;
    const mastered = patterns.filter((pattern) => statusOf(pattern.id) === "mastered").length;
    const learning = patterns.filter((pattern) => statusOf(pattern.id) === "learning").length;
    const card = $("#roadmap-summary-card");
    if (!total) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    $("#roadmap-summary-text").textContent =
      `已掌握 ${mastered} / ${total}　学习中 ${learning}　还没学 ${total - mastered - learning}`;
    const bar = $("#roadmap-progress");
    bar.max = total;
    bar.value = mastered;

    const list = $("#roadmap-cats");
    const stats = categoryStats();
    list.replaceChildren(...[...stats.entries()].sort((a, b) => {
      // 掌握比例低的排前面，先看到最欠的那一类。
      const ratioA = a[1].mastered / a[1].total;
      const ratioB = b[1].mastered / b[1].total;
      return ratioA - ratioB || a[0].localeCompare(b[0], "zh");
    }).map(([name, entry]) => {
      const item = node("li", "roadmap-cat-row");
      const head = node("div", "roadmap-cat-head");
      head.append(
        node("span", "roadmap-cat-name", name),
        node("span", "roadmap-cat-num", `${entry.mastered} / ${entry.total}`),
      );
      const bar2 = node("progress", "roadmap-cat-bar");
      bar2.max = entry.total;
      bar2.value = entry.mastered;
      bar2.setAttribute("aria-label", `${name}：已掌握 ${entry.mastered} / ${entry.total}`);
      item.append(head, bar2);
      return item;
    }));
  }

  /* ---------- 渲染：薄弱提示 ---------- */

  const WEAK_MAX = 3;

  function weakPatterns() {
    return patterns
      .filter((pattern) => (mistakeCounts[pattern.id] || 0) > 0 && statusOf(pattern.id) === "none")
      .sort((a, b) => (mistakeCounts[b.id] - mistakeCounts[a.id])
        || a.title.localeCompare(b.title, "zh"))
      .slice(0, WEAK_MAX);
  }

  function renderWeak() {
    const box = $("#roadmap-weak");
    const weak = weakPatterns();
    if (!weak.length) {
      box.hidden = true;
      box.replaceChildren();
      return;
    }
    const title = node("strong", "roadmap-weak-title", "这几个套路你错过题但还没学");
    const list = node("ul", "roadmap-weak-list");
    for (const pattern of weak) {
      const row = node("li", "roadmap-weak-row");
      row.append(node("span", "", `《${pattern.title}》你错过 ${mistakeCounts[pattern.id]} 题但还没学，建议先学这个`));
      const go = node("button", "roadmap-weak-go", "去看看");
      go.type = "button";
      go.addEventListener("click", () => {
        category = "";
        level = "";
        status = "";
        query = pattern.title;
        $("#roadmap-cat").value = "";
        $("#roadmap-level").value = "";
        $("#roadmap-state").value = "";
        $("#roadmap-q").value = query;
        renderList();
        jumpTo(pattern.id);
      });
      row.append(go);
      list.append(row);
    }
    box.replaceChildren(title, list);
    box.hidden = false;
  }

  /* ---------- 渲染：列表 ---------- */

  function jumpTo(patternId) {
    const card = $("#roadmap-list").querySelector(`[data-pattern-id="${patternId}"]`);
    if (!card) return;
    const details = card.querySelector("details");
    if (details) details.open = true;
    if (typeof card.scrollIntoView === "function") card.scrollIntoView({ block: "center" });
    const summary = card.querySelector("summary");
    if (summary && typeof summary.focus === "function") summary.focus();
  }

  function renderList() {
    const list = $("#roadmap-list");
    const shown = patterns.filter((pattern) => {
      if (!R.matches(pattern, { category, level, query })) return false;
      return !status || statusOf(pattern.id) === status;
    });
    list.replaceChildren(...shown.map((pattern) => R.renderCard(pattern, {
      byId,
      status: statusOf(pattern.id),
      doneKeys: examplesDone[pattern.id] || new Set(),
      mistakeCount: mistakeCounts[pattern.id] || 0,
      onStatus: putStatus,
      onExample: putExample,
      onJumpRelated: jumpTo,
      withStatus: true,
    })));
    $("#roadmap-count").textContent = shown.length
      ? `共 ${shown.length} 个套路`
      : "没有符合条件的套路，放宽筛选试试。";
  }

  function render() {
    renderSummary();
    renderWeak();
    renderList();
  }

  /* ---------- 筛选控件 ---------- */

  function fillSelect(select, values, allLabel) {
    select.replaceChildren();
    const all = node("option", "", allLabel);
    all.value = "";
    select.append(all);
    for (const value of values) {
      const option = node("option", "", value);
      option.value = value;
      select.append(option);
    }
  }

  function renderFilters() {
    fillSelect($("#roadmap-cat"), [...new Set(patterns.map((p) => p.category))].sort(
      (a, b) => a.localeCompare(b, "zh")), "全部分类");
    fillSelect($("#roadmap-level"), R.LEVEL_ORDER, "全部难度");
    const stateSelect = $("#roadmap-state");
    stateSelect.replaceChildren();
    const all = node("option", "", "全部状态");
    all.value = "";
    stateSelect.append(all);
    for (const key of R.STATUS_ORDER) {
      const option = node("option", "", R.STATUS_LABELS[key]);
      option.value = key;
      stateSelect.append(option);
    }
  }

  function setStatus(text, { retry = false } = {}) {
    $("#roadmap-status").textContent = text;
    $("#roadmap-retry").hidden = !retry;
  }

  /* ---------- 入口 ---------- */

  async function load() {
    const page = $("#roadmap-page");
    const mine = ++generation;
    page.setAttribute("aria-busy", "true");
    setStatus("正在加载学习路线…");
    try {
      const data = await loadCatalog();
      if (mine !== generation) return;
      patterns = [...data].sort(
        (a, b) => R.LEVEL_ORDER.indexOf(a.level) - R.LEVEL_ORDER.indexOf(b.level)
          || a.category.localeCompare(b.category, "zh")
          || a.title.localeCompare(b.title, "zh")
      );
      byId = new Map(patterns.map((pattern) => [pattern.id, pattern]));
      renderFilters();
      render(); // 先按"全部未学"画出来，进度读到后再重画一次。
    } catch {
      if (mine !== generation) return;
      setStatus("套路库加载失败，请稍后重试。", { retry: true });
      page.setAttribute("aria-busy", "false");
      return;
    }
    try {
      const state = ticket();
      const data = await hooks.api("/api/roadmap");
      if (!current(state)) return;
      applyState(data);
      render();
      setStatus("");
    } catch {
      if (mine !== generation) return;
      setStatus("暂时读不到你的进度，请稍后重试。", { retry: true });
    } finally {
      if (mine === generation) page.setAttribute("aria-busy", "false");
    }
  }

  function reset() {
    generation += 1;
    progress = {};
    examplesDone = {};
    mistakeCounts = {};
    $("#roadmap-list").replaceChildren();
    $("#roadmap-cats").replaceChildren();
    $("#roadmap-weak").hidden = true;
    $("#roadmap-weak").replaceChildren();
    $("#roadmap-summary-card").hidden = true;
    setStatus("");
  }

  function bind() {
    $("#roadmap-retry").addEventListener("click", () => { void load(); });
    $("#roadmap-cat").addEventListener("change", (event) => {
      category = event.target.value; renderList();
    });
    $("#roadmap-level").addEventListener("change", (event) => {
      level = event.target.value; renderList();
    });
    $("#roadmap-state").addEventListener("change", (event) => {
      status = event.target.value; renderList();
    });
    $("#roadmap-q").addEventListener("input", (event) => {
      query = event.target.value; renderList();
    });
    // 记过新题 / 删了题之后，错题数会变。
    document.addEventListener("app:data-changed", () => {
      if (hooks?.getUser() && !$("#roadmap-page").hidden) void load();
    });
  }

  let bound = false;

  window.Roadmap = {
    configure(next) {
      hooks = next;
      if (!bound) {
        bind();
        bound = true;
      }
    },
    load, reset,
    // 纯函数，供行为测试直接断言
    statusOf, weakPatterns, categoryStats,
  };
})();
