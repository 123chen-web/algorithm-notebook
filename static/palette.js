"use strict";

/* Ctrl K 命令面板：搜我的题目/错因、讨论区帖子，或者输入页面名回车跳转。
   对外契约：window.CommandPalette = { open, close, isOpen }。
   选中后派发 document 事件 app:navigate { view?, recordId?, postId? }，由 app.js 完成页面切换。
   所有用户文本一律走 textContent（高亮也是拆成文本节点 + <mark>），不拼 HTML。 */
(() => {
  const DEBOUNCE_MS = 160;
  const REMOTE_LIMIT = 6;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const ALIASES = {
    home: "首页 大厅 总览", today: "复习 今日 到期", all: "记录 列表 笔记 错题", new: "新增 添加 录入 记录",
    insights: "薄弱 分析 规律", achievements: "成就 徽章", "weekly-recap": "战报 周报 本周",
    groups: "小组 学习小组", forum: "讨论 评论 帖子 社区", leaderboard: "榜单 排行榜 排行 昨日之星 热门题目 打卡",
    plan: "套餐 会员 订阅 额度 支付", admin: "后台 管理 举报", clusters: "专题 归并 相似",
    print: "打印 考前 错题本", mastery: "掌握度 趋势 曲线 遗忘",
  };
  const ICONS = {
    page: "M5 12h14M13 6l6 6-6 6",
    action: "M13 3 5 14h6l-1 7 8-11h-6z",
    record: "M7 3h7l5 5v13H7zM14 3v5h5",
    post: "M21 11a8 8 0 0 1-8 8H7l-4 3V11a9 9 0 0 1 18 0Z",
    search: "M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13zM16 16l4.5 4.5",
  };
  const GROUP_TITLES = { page: "页面", action: "操作", record: "我的记录", post: "讨论区" };

  let root = null;
  let input = null;
  let list = null;
  let status = null;
  let opener = null;
  let timer = 0;
  let controller = null;
  let sequence = 0;
  let query = "";
  let remote = { state: "idle", records: [], posts: [] };
  let options = [];
  let activeIndex = 0;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function icon(kind, className = "palette-icon") {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", className);
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("fill", "none");
    svg.setAttribute("stroke", "currentColor");
    svg.setAttribute("stroke-width", "1.8");
    svg.setAttribute("stroke-linecap", "round");
    svg.setAttribute("stroke-linejoin", "round");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", ICONS[kind]);
    svg.append(path);
    return svg;
  }

  /* ---------- 本地条目：页面与操作 ---------- */
  function navigate(detail) {
    document.dispatchEvent(new CustomEvent("app:navigate", { detail }));
  }
  function pageItems() {
    const items = [];
    for (const button of document.querySelectorAll(".app-sidebar .nav-item[data-view]")) {
      if (button.hidden) continue;
      const view = button.dataset.view;
      const label = button.querySelector(".nav-label")?.textContent.trim() || view;
      items.push({
        kind: "page", id: `page-${view}`, label, sub: "", search: `${label} ${ALIASES[view] || ""}`,
        run: () => navigate({ view }),
      });
    }
    return items;
  }
  function actionItems() {
    const items = [{
      kind: "action", id: "act-new", label: "新增一条记录", sub: "把这次的错因记下来",
      search: "新增 添加 录入 记录 写", run: () => navigate({ view: "new" }),
    }];
    if (window.FocusReview) {
      items.unshift({
        kind: "action", id: "act-focus", label: "开始专注复习", sub: "全屏一次一张卡，键盘打分",
        search: "专注 复习 卡片 全屏", run: () => window.FocusReview.start({}),
      });
    }
    items.push({
      kind: "action", id: "act-refresh", label: "刷新当前页", sub: "",
      search: "刷新 重新加载", run: () => document.querySelector("#refresh")?.click(),
    });
    return items;
  }
  function matches(item, needle) {
    return item.search.toLowerCase().includes(needle) || item.label.toLowerCase().includes(needle);
  }

  /* ---------- 渲染 ---------- */
  function highlight(text, needle) {
    const wrap = document.createDocumentFragment();
    const lower = text.toLowerCase();
    const at = needle && lower.length === text.length ? lower.indexOf(needle) : -1;
    if (at < 0) {
      wrap.append(text);
      return wrap;
    }
    wrap.append(text.slice(0, at), node("mark", "", text.slice(at, at + needle.length)), text.slice(at + needle.length));
    return wrap;
  }

  function buildModel() {
    const needle = query.trim().toLowerCase();
    const pages = pageItems().filter((item) => !needle || matches(item, needle));
    const actions = actionItems().filter((item) => !needle || matches(item, needle));
    const records = (remote.records || []).map((item) => ({
      kind: "record", id: `record-${item.mistake_id}`, label: item.title,
      sub: item.snippet || item.description || item.zone, badge: item.zone,
      run: () => navigate({ view: "all", recordId: item.mistake_id }),
    }));
    const posts = (remote.posts || []).map((item) => ({
      kind: "post", id: `post-${item.id}`, label: item.title,
      sub: `${item.username} · ${item.comment_count} 条评论`, badge: "",
      run: () => navigate({ view: "forum", postId: item.id }),
    }));
    return [...pages, ...actions, ...records, ...posts];
  }

  function render() {
    options = buildModel();
    if (activeIndex >= options.length) activeIndex = Math.max(0, options.length - 1);
    const needle = query.trim().toLowerCase();
    const fragment = document.createDocumentFragment();
    let counter = 0;
    for (const kind of ["page", "action", "record", "post"]) {
      const group = options.filter((item) => item.kind === kind);
      if (!group.length) continue;
      const section = node("div", "palette-group");
      section.setAttribute("role", "group");
      section.setAttribute("aria-label", GROUP_TITLES[kind]);
      section.append(node("div", "palette-group-title", GROUP_TITLES[kind]));
      section.lastChild.setAttribute("aria-hidden", "true");
      for (const item of group) {
        const index = counter;
        counter += 1;
        const row = node("div", "palette-option");
        row.id = `palette-option-${index}`;
        row.setAttribute("role", "option");
        row.setAttribute("aria-selected", String(index === activeIndex));
        row.dataset.index = String(index);
        const text = node("span", "palette-option-text");
        const label = node("span", "palette-option-label");
        label.append(highlight(item.label, needle));
        text.append(label);
        if (item.sub) {
          const sub = node("span", "palette-option-sub");
          sub.append(highlight(item.sub, needle));
          text.append(sub);
        }
        row.append(icon(kind), text);
        if (item.badge) row.append(node("span", "palette-badge", item.badge));
        section.append(row);
      }
      fragment.append(section);
    }
    if (!options.length && remote.state !== "loading") {
      fragment.append(node("p", "palette-empty", needle ? `没有找到「${query.trim()}」相关的内容` : "没有可用的条目"));
    }
    if (remote.state === "loading" && needle) {
      fragment.append(node("p", "palette-loading", "正在搜索题目和帖子…"));
    } else if (remote.state === "error") {
      fragment.append(node("p", "palette-loading", "暂时无法搜索题目和帖子，页面与操作仍可使用。"));
    }
    list.replaceChildren(fragment);
    input.setAttribute("aria-activedescendant", options.length ? `palette-option-${activeIndex}` : "");
  }

  function announce() {
    if (remote.state === "ready" && query.trim()) {
      status.textContent = options.length ? `找到 ${options.length} 个结果` : "没有找到相关内容";
    }
  }

  function setActive(index, { scroll = true } = {}) {
    if (!options.length) return;
    activeIndex = (index + options.length) % options.length;
    for (const row of list.querySelectorAll(".palette-option")) {
      const selected = Number(row.dataset.index) === activeIndex;
      row.setAttribute("aria-selected", String(selected));
      if (selected && scroll) row.scrollIntoView({ block: "nearest" });
    }
    input.setAttribute("aria-activedescendant", `palette-option-${activeIndex}`);
  }

  /* ---------- 远程搜索 ---------- */
  async function fetchRemote(text, ticket) {
    controller?.abort();
    controller = new AbortController();
    try {
      const response = await fetch(`/api/search?q=${encodeURIComponent(text)}&limit=${REMOTE_LIMIT}`, {
        credentials: "same-origin", headers: { "X-CSRF-Protection": "1" }, signal: controller.signal,
      });
      if (!response.ok) throw new Error(`search ${response.status}`);
      const data = await response.json();
      if (ticket !== sequence) return;
      remote = { state: "ready", records: data.records || [], posts: data.posts || [] };
    } catch (error) {
      if (error.name === "AbortError" || ticket !== sequence) return;
      remote = { state: "error", records: [], posts: [] };
    }
    render();
    announce();
  }

  function onInput() {
    query = input.value;
    activeIndex = 0;
    window.clearTimeout(timer);
    const text = query.trim();
    sequence += 1;
    controller?.abort();
    if (!text) {
      remote = { state: "idle", records: [], posts: [] };
      render();
      return;
    }
    remote = { state: "loading", records: [], posts: [] };
    render();
    const ticket = sequence;
    timer = window.setTimeout(() => fetchRemote(text, ticket), DEBOUNCE_MS);
  }

  /* ---------- 键盘与鼠标 ---------- */
  function activate(index) {
    const item = options[index];
    if (!item) return;
    close({ restore: false });
    item.run();
  }

  function onKeydown(event) {
    // 中文输入法组词时的回车/方向键属于输入法，不能当成"打开/移动"。
    if (event.isComposing || event.keyCode === 229) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive(activeIndex + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive(activeIndex - 1);
    } else if (event.key === "Home" && event.target !== input) {
      event.preventDefault();
      setActive(0);
    } else if (event.key === "Enter") {
      event.preventDefault();
      activate(activeIndex);
    } else if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      close();
    } else if (event.key === "Tab") {
      // 焦点始终留在输入框里，用 aria-activedescendant 指示当前选项。
      event.preventDefault();
      setActive(activeIndex + (event.shiftKey ? -1 : 1));
    }
  }

  function build() {
    root = node("div", "palette");
    root.id = "palette";
    root.hidden = true;
    const backdrop = node("div", "palette-backdrop");
    backdrop.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      close();
    });
    const panel = node("div", "palette-panel");
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-modal", "true");
    panel.setAttribute("aria-label", "搜索与跳转");
    const row = node("div", "palette-input-row");
    input = node("input", "palette-input");
    input.type = "text";
    input.id = "palette-input";
    input.placeholder = "搜索题目、错因，或输入页面名跳转";
    input.autocomplete = "off";
    input.spellcheck = false;
    input.maxLength = 100;
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-label", "搜索题目、错因或页面");
    input.setAttribute("aria-expanded", "true");
    input.setAttribute("aria-controls", "palette-list");
    input.setAttribute("aria-autocomplete", "list");
    const close_ = node("button", "palette-close", "Esc");
    close_.type = "button";
    close_.setAttribute("aria-label", "关闭搜索");
    close_.addEventListener("click", () => close());
    row.append(icon("search", "palette-search-icon"), input, close_);
    list = node("div", "palette-list");
    list.id = "palette-list";
    list.setAttribute("role", "listbox");
    list.setAttribute("aria-label", "搜索结果");
    status = node("p", "palette-sr-only");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const foot = node("div", "palette-foot");
    foot.append(node("span", "", "↑↓ 选择"), node("span", "", "回车 打开"), node("span", "", "Esc 关闭"));
    panel.append(row, list, status, foot);
    root.append(backdrop, panel);
    document.body.append(root);

    input.addEventListener("input", onInput);
    root.addEventListener("keydown", onKeydown);
    list.addEventListener("pointermove", (event) => {
      const row = event.target.closest(".palette-option");
      if (row && Number(row.dataset.index) !== activeIndex) setActive(Number(row.dataset.index), { scroll: false });
    });
    list.addEventListener("click", (event) => {
      const row = event.target.closest(".palette-option");
      if (row) activate(Number(row.dataset.index));
    });
    // 鼠标点选项时别让输入框失焦。
    list.addEventListener("mousedown", (event) => event.preventDefault());
  }

  /* ---------- 开关 ---------- */
  function isOpen() {
    return Boolean(root && !root.hidden);
  }

  function open() {
    if (document.documentElement.dataset.view !== "app" || isOpen() || window.FocusReview?.isOpen()) return;
    window.EmojiPicker?.close();
    window.AppShell?.closeMore?.({ restore: false });
    if (!root) build();
    opener = document.activeElement;
    query = "";
    activeIndex = 0;
    remote = { state: "idle", records: [], posts: [] };
    input.value = "";
    root.hidden = false;
    document.body.classList.add("palette-open");
    status.textContent = "";
    render();
    input.focus();
  }

  function close({ restore = true } = {}) {
    if (!isOpen()) return;
    window.clearTimeout(timer);
    controller?.abort();
    sequence += 1;
    root.hidden = true;
    document.body.classList.remove("palette-open");
    if (restore && opener?.isConnected && opener.offsetParent !== null) opener.focus({ preventScroll: true });
    opener = null;
  }

  document.addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === "k") {
      event.preventDefault();
      if (isOpen()) close();
      else open();
    }
  });
  document.addEventListener("app:view-changed", () => close({ restore: false }));

  window.CommandPalette = { open, close, isOpen };
})();
