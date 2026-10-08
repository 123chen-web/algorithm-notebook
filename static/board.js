"use strict";

/* 讨论区主页（"信号台"）：列表、筛选排序、分页，以及发帖页的草稿 / 模板 / 代码块 / 预览。
   对外契约：window.Board = { 纯函数…, configure(hooks), mount(hooks), show(), reset(), … }。
   纯函数不碰 DOM，Node 测试（tests/board_behaviour.cjs）直接调用；控制器只通过 hooks 和 app.js 打交道：
     api(path, options)            带 CSRF 的请求（失败时抛出带中文 message 的错误）
     getUser() / getEpoch() / getView()   当前用户、登录代次、当前页面
     isListVisible() / isDetailVisible()  列表 / 帖子详情是否可见
     openPost(id)                  打开帖子详情
     openCompose({ template })     打开发帖页
     afterPublish(post)            发布成功之后的跳转
     avatar(userId, name, version, { small, hasAvatar })   头像元素
     renderBody(text)              正文渲染（Thread.renderBody）
     timestamp(iso)                绝对时间文字
   所有异步响应都带"票据"（本控制器的代数 + 登录代次 + 用户 id），登出再登录、换页之后才回来的响应一律丢弃。 */
(() => {
  const PREFS_KEY = "forum-board:v1";
  const DRAFT_PREFIX = "forum-draft:v1:";
  const ZONES = Object.freeze(["算法", "前端", "后端", "数据库", "系统设计", "高等数学", "线性代数", "概率统计"]);
  const NO_ZONE = "none";
  const TABS = Object.freeze(["all", "unanswered", "solved", "mine"]);
  const SORTS = Object.freeze(["activity", "new", "hot"]);
  const PAGE_SIZE = 20;
  const MAX_LIMIT = 50;
  const SEARCH_DELAY = 250;
  const DRAFT_DELAY = 800;
  const TITLE_MAX = 200;
  const BODY_MAX = 8000;
  const QUERY_MAX = 200;
  const NEW_POST_MS = 6 * 3600 * 1000;
  const SKELETON_ROWS = 4;
  const SVG_NS = "http://www.w3.org/2000/svg";
  const TEMPLATE = "【我想做什么 / 题目是什么】\n\n【我已经试过什么】\n\n【具体卡在哪一步（可贴代码或报错）】\n```\n\n```\n";
  const TEMPLATE_CONFIRM = "正文里已经有内容了，要把「好问题模板」追加到末尾吗？";

  /* ───────────── 纯函数 ───────────── */

  function parseTime(value) {
    if (value === null || value === undefined || value === "") return NaN;
    return Date.parse(String(value));
  }

  function dateInZone(ms, timeZone) {
    const options = { year: "numeric", month: "2-digit", day: "2-digit" };
    let parts;
    try {
      parts = new Intl.DateTimeFormat("en-CA", timeZone ? { ...options, timeZone } : options).formatToParts(ms);
    } catch (error) {
      // 不用 instanceof：Intl 的错误对象可能来自另一个 JS 环境（iframe / 测试里的 vm）。
      if (error?.name !== "RangeError") throw error;
      parts = new Intl.DateTimeFormat("en-CA", { ...options, timeZone: "UTC" }).formatToParts(ms);
    }
    const pick = (type) => parts.find((part) => part.type === type)?.value || "";
    return `${pick("year")}-${pick("month")}-${pick("day")}`;
  }

  /** 刚刚 / N 分钟前 / N 小时前 / N 天前（不足 30 天）/ 否则 YYYY-MM-DD（按 timeZone）；非法输入返回空串；未来时间算"刚刚"。 */
  function relativeTime(value, nowMs = Date.now(), timeZone) {
    const then = parseTime(value);
    if (!Number.isFinite(then) || !Number.isFinite(nowMs)) return "";
    const seconds = Math.floor((nowMs - then) / 1000);
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 30 * 86400) return `${Math.floor(seconds / 86400)} 天前`;
    return dateInZone(then, timeZone);
  }

  function normalizeSelection(raw) {
    const source = raw && typeof raw === "object" ? raw : {};
    return {
      tab: TABS.includes(source.tab) ? source.tab : "all",
      sort: SORTS.includes(source.sort) ? source.sort : "activity",
      zone: ZONES.includes(source.zone) || source.zone === NO_ZONE ? source.zone : "",
    };
  }

  /** 状态 → 查询串：只带非默认值（默认排序 / 全部 / 第一页 / 每页 20 条都省略），zone=none 表示未分区。 */
  function buildQuery(state, { limit = PAGE_SIZE, offset = 0 } = {}) {
    const selection = normalizeSelection(state);
    const query = String(state?.q ?? "").trim();
    const parts = [];
    if (query) parts.push(["q", query]);
    if (selection.sort !== "activity") parts.push(["sort", selection.sort]);
    if (selection.tab !== "all") parts.push(["filter", selection.tab]);
    if (selection.zone) parts.push(["zone", selection.zone]);
    const pageSize = Math.min(MAX_LIMIT, Math.max(1, Math.floor(Number(limit)) || PAGE_SIZE));
    if (pageSize !== PAGE_SIZE) parts.push(["limit", pageSize]);
    const skip = Math.max(0, Math.floor(Number(offset)) || 0);
    if (skip > 0) parts.push(["offset", skip]);
    if (!parts.length) return "/api/posts";
    return `/api/posts?${parts.map(([key, value]) => `${key}=${encodeURIComponent(value)}`).join("&")}`;
  }

  function filtersActive(state) {
    const selection = normalizeSelection(state);
    return Boolean(String(state?.q ?? "").trim() || selection.tab !== "all" || selection.zone);
  }

  function storageOf(storage) {
    if (storage) return storage;
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }

  function loadPrefs(storage) {
    try {
      const raw = storageOf(storage)?.getItem(PREFS_KEY);
      return normalizeSelection(raw ? JSON.parse(raw) : null);
    } catch {
      return normalizeSelection(null);
    }
  }

  /** 只存标签 / 排序 / 分区，搜索词不存。 */
  function savePrefs(storage, state) {
    try {
      storageOf(storage)?.setItem(PREFS_KEY, JSON.stringify(normalizeSelection(state)));
      return true;
    } catch {
      return false;
    }
  }

  function number(value) {
    return Number.isFinite(Number(value)) ? Number(value) : null;
  }

  function personOf(value) {
    if (!value || typeof value !== "object") return null;
    return {
      userId: value.user_id,
      username: String(value.username ?? ""),
      avatarVersion: Number(value.avatar_version) || 0,
      hasAvatar: value.has_avatar === true,
    };
  }

  /** 响应里的一个帖子 → 渲染用的视图模型；缺失的新字段一律降级成"不显示"。 */
  function postViewModel(post, { nowMs = Date.now(), timeZone } = {}) {
    const count = number(post.comment_count);
    const unanswered = count === 0;
    const solved = post.solved === true;
    const hot = post.hot === true;
    const people = Array.isArray(post.participants) ? post.participants.map(personOf).filter(Boolean) : [];
    const participantCount = number(post.participant_count);
    const total = participantCount === null ? people.length : Math.max(participantCount, people.length);
    const created = parseTime(post.created_at);
    const isNew = Number.isFinite(created) && nowMs - created < NEW_POST_MS;
    const excerpt = typeof post.excerpt === "string" ? post.excerpt : null;
    const pills = [];
    if (solved) pills.push({ kind: "solved", label: "已解决" });
    if (hot) pills.push({ kind: "hot", label: "热门" });
    if (post.has_code === true) pills.push({ kind: "code", label: "含代码" });
    if (unanswered) pills.push({ kind: "open", label: "等你来回" });
    if (post.is_mine === true) pills.push({ kind: "mine", label: "我发的" });
    if (isNew) pills.push({ kind: "new", label: "刚刚" });
    const last = personOf(post.last_commenter);
    return {
      id: post.id,
      title: String(post.title ?? ""),
      idLabel: `#${String(post.id).padStart(4, "0")}`,
      zone: typeof post.zone === "string" && post.zone ? post.zone : null,
      state: solved ? "solved" : unanswered ? "open" : "discussing",
      hot,
      count: count === null ? 0 : count,
      showCount: count !== null,
      statLabel: unanswered ? "待回复" : "回复",
      statAria: count === null ? "" : unanswered ? "还没有回复" : `${count} 条回复${solved ? "，已解决" : ""}`,
      pills,
      excerpt: excerpt === null ? null : excerpt === "" ? "（代码）" : excerpt,
      excerptIsCode: excerpt === "",
      author: { userId: post.user_id, username: String(post.username ?? ""), avatarVersion: Number(post.avatar_version) || 0, hasAvatar: post.has_avatar === true },
      published: relativeTime(post.created_at, nowMs, timeZone),
      createdAt: post.created_at,
      last: last && post.last_activity_at
        ? { person: last, text: relativeTime(post.last_activity_at, nowMs, timeZone), at: post.last_activity_at }
        : null,
      people: people.slice(0, 4),
      peopleMore: Math.max(0, total - Math.min(people.length, 4)),
      peopleTotal: total,
    };
  }

  /** 分区芯片：固定顺序；只显示 zone_counts 里 >0 的分区和当前选中的分区；"未分区"有帖子时才出现。没有 zone_counts 就全部显示、不带数字。 */
  function zoneChips(zoneCounts, selected = "") {
    const known = Boolean(zoneCounts) && typeof zoneCounts === "object";
    const chips = [];
    for (const zone of ZONES) {
      const count = known ? Number(zoneCounts[zone]) || 0 : null;
      if (known && count <= 0 && selected !== zone) continue;
      chips.push({ zone, label: zone, count });
    }
    const none = known ? Number(zoneCounts[NO_ZONE]) || 0 : 0;
    if (none > 0 || selected === NO_ZONE) chips.push({ zone: NO_ZONE, label: "未分区", count: known ? none : null });
    return chips;
  }

  function readoutModel(counts) {
    if (!counts || typeof counts !== "object") return null;
    const pick = (key) => Math.max(0, Number(counts[key]) || 0);
    return { all: pick("all"), unanswered: pick("unanswered"), solved: pick("solved"), mine: pick("mine") };
  }

  /** 键盘门控：只有列表可见、详情页不可见、没有对话框 / 命令面板、焦点不在输入控件、没有修饰键时才生效。 */
  function keyAllowed(event, { listVisible = false, detailVisible = false, dialogOpen = false, paletteOpen = false } = {}) {
    if (!listVisible || detailVisible || dialogOpen || paletteOpen) return false;
    if (!event || event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented || event.isComposing) return false;
    const target = event.target;
    if (target?.isContentEditable) return false;
    return !target?.closest?.("input, textarea, select, [contenteditable]");
  }

  /** 在光标处插入围栏代码块；有选中文字则包住它。围栏必须在行首（thread.js 只认行首的 ```）。 */
  function insertCodeBlock(value, start, end) {
    const text = String(value ?? "");
    const from = Math.min(Math.max(0, Number(start) || 0), text.length);
    const to = Math.min(Math.max(from, Number(end) || 0), text.length);
    const selected = text.slice(from, to);
    const lead = from > 0 && text[from - 1] !== "\n" ? "\n" : "";
    const trail = to < text.length && text[to] !== "\n" ? "\n" : "";
    const before = `${lead}\`\`\`\n`;
    const after = `\n\`\`\`${trail}`;
    const next = text.slice(0, from) + before + selected + after + text.slice(to);
    const caret = from + before.length;
    return { value: next, start: caret, end: caret + selected.length };
  }

  /** 套用"好问题模板"：正文为空直接填入；不为空则追加到末尾并要求先确认；放不下返回 null。 */
  function applyTemplate(value, maxLength = BODY_MAX) {
    const text = String(value ?? "");
    const empty = !text.trim();
    const next = empty ? TEMPLATE : `${text}${text.endsWith("\n\n") ? "" : text.endsWith("\n") ? "\n" : "\n\n"}${TEMPLATE}`;
    if (next.length > maxLength) return null;
    return { value: next, needsConfirm: !empty };
  }

  function draftKey(userId) {
    return `${DRAFT_PREFIX}${userId}`;
  }

  /** 草稿：标题 / 正文 / 分区；全空就删掉键；读写都不抛异常。 */
  function saveDraft(storage, userId, draft) {
    try {
      const target = storageOf(storage);
      if (!target || userId === undefined || userId === null) return false;
      const title = String(draft?.title ?? "");
      const body = String(draft?.body ?? "");
      if (!title.trim() && !body.trim()) {
        target.removeItem(draftKey(userId));
        return true;
      }
      const zone = ZONES.includes(draft?.zone) ? draft.zone : "";
      target.setItem(draftKey(userId), JSON.stringify({ userId, title, body, zone }));
      return true;
    } catch {
      return false;
    }
  }

  /** 读草稿：键里的 userId 和内容里的 userId 都必须等于当前用户，否则当没有。 */
  function loadDraft(storage, userId) {
    try {
      if (userId === undefined || userId === null) return null;
      const raw = storageOf(storage)?.getItem(draftKey(userId));
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || typeof data !== "object" || data.userId !== userId) return null;
      if (typeof data.title !== "string" || typeof data.body !== "string") return null;
      if (!data.title.trim() && !data.body.trim()) return null;
      return {
        title: data.title.slice(0, TITLE_MAX),
        body: data.body.slice(0, BODY_MAX),
        zone: ZONES.includes(data.zone) ? data.zone : "",
      };
    } catch {
      return null;
    }
  }

  function clearDraft(storage, userId) {
    try {
      storageOf(storage)?.removeItem(draftKey(userId));
      return true;
    } catch {
      return false;
    }
  }

  /* ───────────── 控制器 ───────────── */

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  let hooks = null;
  let mounted = false;
  let generation = 0;
  let selection = loadPrefs();
  let query = "";
  let data = emptyData();
  let loading = false;
  let moreLoading = false;
  let selectedId = null;
  let searchTimer = null;
  let draftTimer = null;
  let composeZone = "";
  let composePending = false;
  let composePreview = false;
  let composeOwner = null;

  function emptyData() {
    return { posts: [], total: 0, hasMore: false, counts: null, zoneCounts: null, loaded: false };
  }

  function node(tag, className = "", text = "") {
    const result = document.createElement(tag);
    if (className) result.className = className;
    if (text !== "") result.textContent = text;
    return result;
  }

  function icon(name, className = "") {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("aria-hidden", "true");
    if (className) svg.setAttribute("class", className);
    const use = document.createElementNS(SVG_NS, "use");
    use.setAttribute("href", `#board-i-${name}`);
    svg.append(use);
    return svg;
  }

  function user() {
    return hooks?.getUser?.() || null;
  }

  function ticket() {
    return { generation, epoch: hooks.getEpoch(), userId: user()?.id };
  }

  function current(t) {
    const me = user();
    return Boolean(hooks && me && t.generation === generation && t.epoch === hooks.getEpoch()
      && t.userId === me.id && hooks.getView() === "forum" && hooks.isListVisible());
  }

  function announce(text) {
    const target = $("#forum-list-status");
    if (target) target.textContent = text;
  }

  function timeZone() {
    return user()?.timezone;
  }

  function reducedMotion() {
    return Boolean(window.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches);
  }

  /* —— 渲染：页头 / 标签 / 分区 / 侧栏 —— */

  function renderHead() {
    const counts = readoutModel(data.counts);
    $("#board-count-live").textContent = data.loaded ? `${data.total} 个帖子` : "讨论区";
    const readouts = $("#board-readouts");
    readouts.hidden = !counts;
    if (counts) {
      $("#board-r-all").textContent = String(counts.all);
      $("#board-r-open").textContent = String(counts.unanswered);
      $("#board-r-solved").textContent = String(counts.solved);
      $("#board-r-mine").textContent = String(counts.mine);
    }
    const pulse = $("#board-rail-pulse");
    pulse.hidden = !counts;
    $("#board-rail-help").hidden = !counts;
    if (counts) {
      $("#board-rail-open").textContent = String(counts.unanswered);
      $("#board-rail-help-text").textContent = counts.unanswered ? "个帖子还没人回复" : "所有帖子都有人回复";
      $("#board-rail-help-go").hidden = counts.unanswered === 0;
      $("#board-rail-solved").textContent = String(counts.solved);
      $("#board-rail-all").textContent = String(counts.all);
      $("#board-rail-mine").textContent = String(counts.mine);
    }
    const trial = Boolean(user()?.is_trial);
    $("#forum-new-post-btn").hidden = trial;
    $("#board-ask").hidden = trial;
  }

  function renderTabs() {
    const counts = readoutModel(data.counts);
    const trial = Boolean(user()?.is_trial);
    for (const button of $$("#board-tabs [data-board-tab]")) {
      const tab = button.dataset.boardTab;
      button.setAttribute("aria-pressed", String(tab === selection.tab));
      if (tab === "mine") button.hidden = trial;
      const badge = button.querySelector(".n");
      if (badge) {
        const value = counts ? counts[tab] : null;
        badge.hidden = value === null || value === undefined;
        if (!badge.hidden) badge.textContent = String(value);
      }
    }
    $("#board-sort").value = selection.sort;
  }

  function zoneChip(chip, className, withDot = true) {
    const button = node("button", className);
    button.type = "button";
    button.dataset.zoneFilter = chip.zone;
    if (chip.zone !== NO_ZONE) button.dataset.zone = chip.zone;
    button.setAttribute("aria-pressed", String(selection.zone === chip.zone));
    if (withDot) button.append(node("i"));
    button.append(chip.label);
    return button;
  }

  function renderZones() {
    const chips = zoneChips(data.zoneCounts, selection.zone);
    const bar = $("#board-zones");
    const all = node("button", "board-zone", "全部分区");
    all.type = "button";
    all.dataset.zoneFilter = "";
    all.setAttribute("aria-pressed", String(!selection.zone));
    bar.replaceChildren(all);
    for (const chip of chips) {
      const button = zoneChip(chip, "board-zone");
      if (chip.count !== null) button.append(" ", node("span", "n", String(chip.count)));
      bar.append(button);
    }
    const rail = $("#board-rail-zones");
    rail.replaceChildren();
    for (const chip of chips) {
      const item = node("li");
      const button = zoneChip(chip, "");
      if (chip.count !== null) button.append(node("b", "", String(chip.count)));
      item.append(button);
      rail.append(item);
    }
  }

  /* —— 渲染：帖子行 —— */

  function avatarFor(person, small) {
    if (hooks?.avatar) return hooks.avatar(person.userId, person.username, person.avatarVersion, { small, hasAvatar: person.hasAvatar });
    return node("span", "avatar", (person.username || "?").slice(0, 1).toUpperCase());
  }

  function pillNode(pill) {
    const span = node("span", `board-pill is-${pill.kind}`);
    if (pill.kind === "solved") span.append(icon("check"));
    else if (pill.kind === "hot") span.append(icon("flame"));
    else if (pill.kind === "code") span.append(icon("code"));
    else if (pill.kind === "open") {
      const led = node("i", "thread-led");
      led.setAttribute("aria-hidden", "true");
      span.append(led);
    }
    span.append(pill.label);
    return span;
  }

  function timeNode(text, iso, suffix = "") {
    const time = node("time", "", `${text}${suffix}`);
    time.setAttribute("datetime", String(iso));
    time.title = hooks?.timestamp ? hooks.timestamp(iso) : String(iso);
    return time;
  }

  function rowFor(post, index, nowMs) {
    const model = postViewModel(post, { nowMs, timeZone: timeZone() });
    const item = node("li", "board-item");
    item.dataset.postId = String(model.id);
    item.style.setProperty("--i", String(Math.min(index, 8)));
    const row = node("article", `board-row${model.state === "solved" ? " is-solved" : ""}${model.state === "open" ? " is-open" : ""}${model.hot ? " is-hot" : ""}`);

    if (model.showCount) {
      const stat = node("div", "board-stat");
      stat.setAttribute("role", "img");
      stat.setAttribute("aria-label", model.statAria);
      stat.append(node("b", "", String(model.count)), node("span", "", model.statLabel));
      if (model.state === "solved") stat.append(icon("check", "board-stat-mark"));
      row.append(stat);
    } else {
      row.classList.add("no-stat");
    }

    const body = node("div", "board-body");
    const tags = node("div", "board-tags");
    if (model.zone) {
      const chip = node("span", "board-zone-chip");
      chip.dataset.zone = model.zone;
      chip.append(node("i"), model.zone);
      tags.append(chip);
    }
    for (const pill of model.pills) tags.append(pillNode(pill));
    if (tags.children.length) body.append(tags);

    const title = node("h3", "board-title");
    const open = node("button", "board-open", model.title);
    open.type = "button";
    open.addEventListener("click", () => hooks.openPost(model.id));
    row.addEventListener("click", (event) => {
      if (event.target.closest("button, a, input, textarea, select")) return;
      hooks.openPost(model.id);
    });
    title.append(open);
    body.append(title);

    if (model.excerpt !== null) body.append(node("p", `board-excerpt${model.excerptIsCode ? " is-code" : ""}`, model.excerpt));

    const meta = node("div", "board-meta");
    meta.append(avatarFor(model.author, true), hooks.author?.(model.author.userId, model.author.username)
      || node("span", "who", model.author.username), node("span", "id", model.idLabel));
    if (model.published) meta.append(timeNode(model.published, model.createdAt, "发布"));
    if (model.last && model.last.text) {
      const last = node("span", "board-last");
      last.append(icon("reply"), `${model.last.person.username} · `, timeNode(model.last.text, model.last.at));
      meta.append(last);
    }
    body.append(meta);
    row.append(body);

    if (model.people.length) {
      const people = node("div", "board-people");
      people.setAttribute("role", "img");
      people.setAttribute("aria-label", `${model.peopleTotal} 人参与`);
      for (const person of model.people) people.append(avatarFor(person, true));
      if (model.peopleMore > 0) people.append(node("span", "n", `+${model.peopleMore}`));
      row.append(people);
    }
    item.append(row);
    return item;
  }

  function skeletonRows() {
    return Array.from({ length: SKELETON_ROWS }, () => {
      const item = node("li");
      const card = node("div", "board-skeleton");
      card.setAttribute("aria-hidden", "true");
      const lines = node("div");
      lines.append(node("i", "sk-line w1"), node("i", "sk-line w2"), node("i", "sk-line w3"));
      card.append(node("i", "sk-stat"), lines);
      item.append(card);
      return item;
    });
  }

  function renderList() {
    const nowMs = Date.now();
    $("#forum-posts").replaceChildren(...data.posts.map((post, index) => rowFor(post, index, nowMs)));
    syncSelection();
  }

  function appendRows(posts, firstIndex) {
    const nowMs = Date.now();
    const added = posts.map((post, offset) => rowFor(post, firstIndex + offset, nowMs));
    $("#forum-posts").append(...added);
    return added;
  }

  function syncSelection() {
    for (const row of $$("#forum-posts .board-row")) {
      const item = row.closest("li");
      row.classList.toggle("is-selected", Boolean(item) && selectedId !== null && item.dataset.postId === String(selectedId));
    }
  }

  function renderFoot() {
    const foot = $("#board-foot");
    const more = $("#board-more");
    const end = $("#board-end");
    const shown = data.posts.length;
    foot.hidden = !shown;
    if (!shown) return;
    const remaining = Math.max(0, data.total - shown);
    more.hidden = !data.hasMore;
    more.disabled = moreLoading;
    more.textContent = moreLoading ? "正在加载…" : `加载更多（还有 ${remaining} 个）`;
    end.hidden = data.hasMore;
    end.textContent = `已显示全部 ${shown} 个帖子`;
  }

  function showNotice({ text, button, onClick, seal = false, error = false }) {
    const box = $("#board-notice");
    box.hidden = false;
    box.className = `board-notice${error ? " is-error" : ""}`;
    box.replaceChildren();
    if (seal) box.append(node("span", "forum-empty-seal", "空"));
    box.append(node("p", "", text));
    if (button) {
      const action = node("button", seal ? "primary" : "", button);
      action.type = "button";
      action.addEventListener("click", onClick);
      box.append(action);
    }
  }

  function hideNotice() {
    const box = $("#board-notice");
    box.hidden = true;
    box.replaceChildren();
  }

  function renderEmpty() {
    if (data.posts.length) {
      hideNotice();
      return;
    }
    if (filtersActive({ ...selection, q: query })) {
      showNotice({ text: "没有符合条件的帖子，换个筛选，或者清除筛选。", button: "清除筛选", onClick: () => resetFilters() });
      announce("没有符合条件的帖子");
    } else if (user()?.is_trial) {
      showNotice({ text: "还没有帖子。体验账号只能浏览，注册后就可以发第一条。", seal: true });
      announce("还没有帖子");
    } else {
      showNotice({ text: "还没有帖子，来发第一条吧。", button: "发帖", seal: true, onClick: () => hooks.openCompose({}) });
      announce("还没有帖子，来发第一条吧");
    }
  }

  function renderAll() {
    renderHead();
    renderTabs();
    renderZones();
    renderList();
    renderFoot();
    renderEmpty();
    $("#forum-list-title").textContent = query ? "搜索结果" : "全部帖子";
  }

  /* —— 加载 —— */

  function normalizeResponse(response) {
    const posts = Array.isArray(response?.posts) ? response.posts.filter((post) => post && post.id !== undefined) : [];
    const total = number(response?.total);
    return {
      posts,
      total: total === null ? posts.length : total,
      hasMore: typeof response?.has_more === "boolean" ? response.has_more : total !== null && posts.length < total,
      counts: response?.counts && typeof response.counts === "object" ? response.counts : null,
      zoneCounts: response?.zone_counts && typeof response.zone_counts === "object" ? response.zone_counts : null,
      loaded: true,
    };
  }

  /** keep=true：从详情返回时的刷新——先保留已显示的内容，数据回来再替换；已加载的条数最多取回 50 条。 */
  async function load({ keep = false } = {}) {
    hooks.cancelOpening?.();
    const mine = ++generation;
    const t = { generation: mine, epoch: hooks.getEpoch(), userId: user()?.id };
    const hadContent = keep && data.loaded && data.posts.length > 0;
    loading = true;
    moreLoading = false;
    $("#forum-list-title").textContent = query ? "搜索结果" : "全部帖子";
    if (!hadContent) {
      $("#forum-posts").replaceChildren(...skeletonRows());
      $("#board-foot").hidden = true;
      hideNotice();
      announce("正在加载帖子列表…");
    }
    const limit = hadContent ? Math.min(MAX_LIMIT, Math.max(PAGE_SIZE, data.posts.length)) : PAGE_SIZE;
    let response;
    try {
      response = await hooks.api(buildQuery({ ...selection, q: query }, { limit }));
    } catch (error) {
      if (t.generation === generation) loading = false;
      if (!current(t)) return;
      const message = error?.message || "帖子列表加载失败，请稍后重试。";
      if (!hadContent) $("#forum-posts").replaceChildren();
      showNotice({ text: message, button: "重试", error: true, onClick: () => { void load({ keep: hadContent }); } });
      announce(message);
      return;
    }
    if (t.generation === generation) loading = false;
    if (!current(t)) return;
    data = normalizeResponse(response);
    if (selectedId !== null && !data.posts.some((post) => post.id === selectedId)) selectedId = null;
    renderAll();
    if (data.posts.length) announce(`当前显示 ${data.posts.length} 个帖子`);
  }

  async function loadMore() {
    if (!data.hasMore || moreLoading || loading) return;
    const t = ticket();
    const button = $("#board-more");
    moreLoading = true;
    renderFoot();
    let response;
    try {
      response = await hooks.api(buildQuery({ ...selection, q: query }, { offset: data.posts.length }));
    } catch (error) {
      if (t.generation === generation) moreLoading = false;
      if (!current(t)) return;
      renderFoot();
      announce(error?.message || "加载更多失败，请稍后重试。");
      return;
    }
    if (t.generation === generation) moreLoading = false;
    if (!current(t)) return;
    const next = normalizeResponse(response);
    const known = new Set(data.posts.map((post) => post.id));
    const fresh = next.posts.filter((post) => !known.has(post.id));
    const firstIndex = data.posts.length;
    data = { ...next, posts: [...data.posts, ...fresh] };
    const added = appendRows(fresh, firstIndex);
    renderHead();
    renderTabs();
    renderZones();
    renderFoot();
    renderEmpty();
    announce(`已加载 ${fresh.length} 个`);
    // 焦点留在按钮上；按钮消失（已经加载完）时把焦点交给新加载的第一条。
    if (button.hidden && added[0]) added[0].querySelector(".board-open")?.focus({ preventScroll: true });
  }

  function persist() {
    savePrefs(null, selection);
  }

  function changeSelection(patch) {
    selection = normalizeSelection({ ...selection, ...patch });
    selectedId = null;
    persist();
    return load();
  }

  function resetFilters() {
    window.clearTimeout(searchTimer);
    query = "";
    const input = $("#forum-search");
    input.value = "";
    return changeSelection({ tab: "all", zone: "" });
  }

  function toggleZone(zone) {
    return changeSelection({ zone: !zone || selection.zone === zone ? "" : zone });
  }

  function applySearch() {
    window.clearTimeout(searchTimer);
    const input = $("#forum-search");
    const value = String(input.value ?? "").trim();
    if (Array.from(value).length > QUERY_MAX) {
      announce("搜索关键词不能超过 200 个字符。");
      return null;
    }
    query = value;
    selectedId = null;
    return load();
  }

  /* —— 键盘 —— */

  function dialogOpen() {
    return $$('dialog[open], [role="dialog"], [aria-modal="true"]')
      .some((item) => !item.hidden && !item.closest("[hidden]") && item.getAttribute("aria-hidden") !== "true");
  }

  function rows() {
    return $$("#forum-posts .board-row");
  }

  function moveSelection(delta) {
    const list = rows();
    if (!list.length) return;
    const ids = list.map((row) => row.closest("li")?.dataset.postId);
    const index = selectedId === null ? -1 : ids.indexOf(String(selectedId));
    const next = Math.min(list.length - 1, Math.max(0, index === -1 ? 0 : index + delta));
    selectedId = Number(ids[next]);
    syncSelection();
    list[next].querySelector(".board-open")?.focus({ preventScroll: true });
    list[next].scrollIntoView?.({ block: "center", behavior: reducedMotion() ? "auto" : "smooth" });
  }

  function onKeydown(event) {
    if (!hooks) return;
    const allowed = keyAllowed(event, {
      listVisible: Boolean(user() && hooks.getView() === "forum" && hooks.isListVisible()),
      detailVisible: Boolean(hooks.isDetailVisible?.()),
      dialogOpen: dialogOpen(),
      paletteOpen: Boolean(window.CommandPalette?.isOpen?.()),
    });
    if (!allowed) return;
    const key = String(event.key || "").toLowerCase();
    if (key === "/") {
      event.preventDefault();
      $("#forum-search").focus();
    } else if (key === "j" || key === "k") {
      event.preventDefault();
      moveSelection(key === "j" ? 1 : -1);
    } else if (key === "n" && !user()?.is_trial) {
      event.preventDefault();
      $("#forum-new-post-btn").focus();
    }
  }

  /* ───────────── 发帖页 ───────────── */

  function composeForm() {
    return $("#forum-compose-form");
  }

  function composeValues() {
    return { title: $("#forum-compose-title-input").value, body: $("#forum-compose-body").value, zone: composeZone };
  }

  function renderComposeZones() {
    const bar = $("#forum-compose-zones");
    bar.replaceChildren();
    for (const zone of ZONES) {
      const button = node("button", "board-zone");
      button.type = "button";
      button.dataset.zone = zone;
      button.dataset.composeZone = zone;
      button.setAttribute("aria-pressed", String(composeZone === zone));
      button.append(node("i"), zone);
      bar.append(button);
    }
  }

  function updateComposeCounts() {
    const body = $("#forum-compose-body").value.length;
    const title = $("#forum-compose-title-input").value.length;
    $("#forum-compose-count").textContent = `${body} / ${BODY_MAX}`;
    $("#forum-compose-title-count").textContent = `${title} / ${TITLE_MAX}`;
    $("#forum-compose-progress").style.setProperty("--thread-progress", `${Math.min(100, (body / BODY_MAX) * 100)}%`);
    if (composePreview) renderPreview();
  }

  function renderPreview() {
    const text = $("#forum-compose-body").value;
    const preview = $("#forum-compose-preview");
    if (typeof hooks?.renderBody === "function") {
      preview.replaceChildren(hooks.renderBody(text));
      return;
    }
    // 没有富文本渲染器时退回纯文本，保留换行。
    const plain = node("p");
    plain.textContent = text;
    preview.replaceChildren(plain);
  }

  function setPreview(on) {
    composePreview = Boolean(on);
    $("#forum-compose-body").hidden = composePreview;
    $("#forum-compose-preview").hidden = !composePreview;
    $("#forum-compose-edit-tab").setAttribute("aria-pressed", String(!composePreview));
    $("#forum-compose-preview-tab").setAttribute("aria-pressed", String(composePreview));
    if (composePreview) renderPreview();
  }

  function setComposeZone(zone) {
    composeZone = ZONES.includes(zone) ? zone : "";
    for (const button of $$("#forum-compose-zones [data-compose-zone]")) {
      button.setAttribute("aria-pressed", String(button.dataset.composeZone === composeZone));
    }
  }

  function composeStatus(text, error = false) {
    const target = $("#forum-compose-status");
    target.textContent = text;
    target.classList.toggle("is-error", error);
  }

  function saveDraftNow() {
    window.clearTimeout(draftTimer);
    draftTimer = null;
    const owner = composeOwner ?? user()?.id;
    if (owner === undefined || owner === null) return;
    saveDraft(null, owner, composeValues());
  }

  function scheduleDraft() {
    window.clearTimeout(draftTimer);
    draftTimer = window.setTimeout(saveDraftNow, DRAFT_DELAY);
  }

  function onComposeEdited() {
    updateComposeCounts();
    composeStatus("");
    scheduleDraft();
  }

  function fillCompose({ title = "", body = "", zone = "" }) {
    $("#forum-compose-title-input").value = title;
    $("#forum-compose-body").value = body;
    setComposeZone(zone);
    updateComposeCounts();
  }

  function clearComposeForm() {
    window.clearTimeout(draftTimer);
    draftTimer = null;
    fillCompose({});
    setPreview(false);
    $("#forum-compose-draft").hidden = true;
    composeStatus("");
  }

  function insertTemplate() {
    const body = $("#forum-compose-body");
    const result = applyTemplate(body.value, BODY_MAX);
    if (!result) {
      composeStatus("正文太长，放不下模板了。", true);
      return;
    }
    if (result.needsConfirm) {
      const confirmed = hooks?.confirm ? hooks.confirm(TEMPLATE_CONFIRM) : window.confirm(TEMPLATE_CONFIRM);
      if (!confirmed) return;
    }
    setPreview(false);
    body.value = result.value;
    body.setSelectionRange(result.value.length, result.value.length);
    body.focus();
    onComposeEdited();
  }

  function insertCode() {
    const body = $("#forum-compose-body");
    setPreview(false);
    const start = Number.isFinite(body.selectionStart) ? body.selectionStart : body.value.length;
    const end = Number.isFinite(body.selectionEnd) ? body.selectionEnd : start;
    const result = insertCodeBlock(body.value, start, end);
    if (result.value.length > BODY_MAX) {
      composeStatus("正文太长，放不下代码块了。", true);
      return;
    }
    body.value = result.value;
    body.setSelectionRange(result.start, result.end);
    body.focus();
    onComposeEdited();
  }

  /** 打开发帖页：表单是空的才尝试恢复草稿；template 为真时顺手套用好问题模板。 */
  function openCompose({ template = false } = {}) {
    const me = user();
    if (!me) return;
    composeOwner = me.id;
    composeStatus("");
    const empty = !$("#forum-compose-title-input").value && !$("#forum-compose-body").value;
    renderComposeZones();
    setComposeZone(composeZone);
    const draft = loadDraft(null, me.id);
    if (empty && draft) fillCompose(draft);
    else updateComposeCounts();
    // 有草稿、并且表单里正是这份草稿（刚恢复，或取消后再次打开）时，提示一下并给出"清除"。
    const shown = composeValues();
    $("#forum-compose-draft").hidden = !(draft && draft.title === shown.title && draft.body === shown.body);
    setPreview(false);
    if (template) insertTemplate();
    else $("#forum-compose-title-input").focus({ preventScroll: true });
  }

  function discardDraft() {
    const owner = composeOwner ?? user()?.id;
    if (owner !== undefined && owner !== null) clearDraft(null, owner);
    clearComposeForm();
    composeStatus("草稿已清除。");
  }

  /** 发布：重复点击只发一次请求；失败把 detail 显示在表单下方；登出再登录之后回来的响应一律丢弃。 */
  async function submitCompose() {
    if (composePending || !hooks || !user()) return;
    const values = composeValues();
    const title = values.title.trim();
    const body = values.body.trim();
    if (!title || !body) {
      composeStatus(!title ? "请先写一个标题。" : "请先写下正文。", true);
      return;
    }
    const t = { epoch: hooks.getEpoch(), userId: user().id };
    const payload = { title, body };
    if (values.zone) payload.zone = values.zone;
    composePending = true;
    const send = $("#forum-compose-send");
    send.disabled = true;
    composeStatus("正在发布…");
    let created;
    try {
      created = await hooks.api("/api/posts", { method: "POST", body: JSON.stringify(payload) });
    } catch (error) {
      composePending = false;
      send.disabled = false;
      if (hooks.getEpoch() === t.epoch && user()?.id === t.userId) composeStatus(error?.message || "发布失败，请稍后重试。", true);
      return;
    }
    composePending = false;
    send.disabled = false;
    // 登出（或换了账号）之后才回来：帖子已经发出去了，但不能动现在这位用户的界面。
    if (hooks.getEpoch() !== t.epoch || user()?.id !== t.userId) return;
    clearDraft(null, t.userId);
    clearComposeForm();
    setComposeZone("");
    hooks.afterPublish(created);
  }

  /* ───────────── 挂载 / 重置 ───────────── */

  function bind() {
    $("#forum-search-form").addEventListener("submit", (event) => {
      event.preventDefault();
      applySearch();
    });
    const search = $("#forum-search");
    search.addEventListener("input", () => {
      window.clearTimeout(searchTimer);
      searchTimer = window.setTimeout(() => {
        const value = String(search.value ?? "").trim();
        if (value === query) return;
        applySearch();
      }, SEARCH_DELAY);
    });
    search.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || event.isComposing) return;
      window.clearTimeout(searchTimer);
      const had = Boolean(query) || Boolean(search.value);
      search.value = "";
      search.blur();
      if (had) applySearch();
    });
    $("#board-tabs").addEventListener("click", (event) => {
      const button = event.target.closest("[data-board-tab]");
      if (button) void changeSelection({ tab: button.dataset.boardTab });
    });
    $("#board-sort").addEventListener("change", (event) => {
      void changeSelection({ sort: event.target.value });
    });
    for (const selector of ["#board-zones", "#board-rail-zones"]) {
      $(selector).addEventListener("click", (event) => {
        const button = event.target.closest("[data-zone-filter]");
        if (button) void toggleZone(button.dataset.zoneFilter);
      });
    }
    const helpers = () => { void changeSelection({ tab: "unanswered" }); };
    $("#board-r-open-btn").addEventListener("click", helpers);
    $("#board-rail-help").addEventListener("click", helpers);
    $("#board-more").addEventListener("click", () => { void loadMore(); });
    $("#board-ask-apply").addEventListener("click", () => hooks.openCompose({ template: true }));
    $("#forum-posts").addEventListener("focusin", (event) => {
      const item = event.target.closest?.("li");
      if (item?.dataset.postId) {
        selectedId = Number(item.dataset.postId);
        syncSelection();
      }
    });
    document.addEventListener("keydown", onKeydown);

    // 发帖页
    const form = composeForm();
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      void submitCompose();
    });
    form.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key === "Enter" && !event.isComposing) {
        event.preventDefault();
        void submitCompose();
      }
    });
    $("#forum-compose-title-input").addEventListener("input", onComposeEdited);
    $("#forum-compose-body").addEventListener("input", onComposeEdited);
    $("#forum-compose-zones").addEventListener("click", (event) => {
      const button = event.target.closest("[data-compose-zone]");
      if (!button) return;
      setComposeZone(composeZone === button.dataset.composeZone ? "" : button.dataset.composeZone);
      scheduleDraft();
    });
    $("#forum-compose-code").addEventListener("click", insertCode);
    $("#forum-compose-template").addEventListener("click", insertTemplate);
    $("#forum-compose-edit-tab").addEventListener("click", () => setPreview(false));
    $("#forum-compose-preview-tab").addEventListener("click", () => setPreview(true));
    $("#forum-compose-draft-clear").addEventListener("click", discardDraft);
    window.EmojiPicker?.attach($("#forum-compose-body"), { container: $("#forum-compose-tools") });
  }

  function configure(next) {
    hooks = next;
  }

  function mount(next) {
    configure(next);
    if (mounted) return;
    mounted = true;
    bind();
    renderComposeZones();
  }

  /** 进入（或从详情页回到）列表：保留当前选择和已加载的内容，同时按现有逻辑刷新一次数据。 */
  function show() {
    const input = $("#forum-search");
    if (input) input.value = query;
    const hadContent = data.loaded && data.posts.length > 0;
    if (hadContent) renderAll();
    else {
      renderHead();
      renderTabs();
      renderZones();
    }
    return load({ keep: hadContent });
  }

  /** 登出 / 换账号：丢掉所有数据、作废在途请求，草稿只存在 localStorage（键里带用户 id）。 */
  function reset() {
    if (mounted && composeOwner !== null && ($("#forum-compose-title-input").value || $("#forum-compose-body").value)) saveDraftNow();
    generation += 1;
    window.clearTimeout(searchTimer);
    window.clearTimeout(draftTimer);
    searchTimer = draftTimer = null;
    loading = moreLoading = composePending = false;
    selectedId = null;
    data = emptyData();
    query = "";
    selection = loadPrefs();
    composeOwner = null;
    composeZone = "";
    if (!mounted) return;
    $("#forum-search").value = "";
    $("#forum-posts").replaceChildren();
    $("#board-zones").replaceChildren();
    $("#board-rail-zones").replaceChildren();
    $("#board-foot").hidden = true;
    $("#board-readouts").hidden = true;
    hideNotice();
    announce("");
    $("#forum-list-title").textContent = "全部帖子";
    $("#board-count-live").textContent = "讨论区";
    clearComposeForm();
    $("#forum-compose-send").disabled = false;
  }

  function snapshot() {
    return {
      selection: { ...selection }, query, posts: data.posts.map((post) => post.id), total: data.total,
      hasMore: data.hasMore, loaded: data.loaded, loading, selectedId, counts: data.counts, zoneCounts: data.zoneCounts,
    };
  }

  window.Board = Object.freeze({
    relativeTime, buildQuery, normalizeSelection, filtersActive, loadPrefs, savePrefs, postViewModel,
    zoneChips, readoutModel, keyAllowed, insertCodeBlock, applyTemplate, saveDraft, loadDraft, clearDraft,
    draftKey, TEMPLATE, ZONES, PAGE_SIZE,
    configure, mount, show, reset, load, loadMore, openCompose, submitCompose, snapshot,
    generation: () => generation,
  });
})();
