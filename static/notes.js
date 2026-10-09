"use strict";

/* 记笔记页面（Memos 式）：顶部 composer、搜索框、标签筛选行、时间线列表、"加载更多"分页。
   对外契约：window.Notes = { 纯函数…, configure(hooks), load(), loadMore(), openWithProblem(problemId, problemTitle), reset(), snapshot() }。
   纯函数不碰 DOM，Node 测试（tests/notes_behaviour.cjs）直接调用；控制器只通过 hooks 打交道：
     api(path, options)   带 CSRF 的请求（失败时抛出带中文 message 的错误）
     getUser()            当前用户
     getEpoch()           登录代次
     getView()            当前页面
     confirm(message)     删除确认框
     notify(text)         顶部提示条（app.js 的 message）
   所有异步响应都带"票据"（本控制器的代数 + 登录代次 + 用户 id），登出再登录之后迟到的响应一律丢弃。
   后端不返回标签聚合，所以标签筛选行从"当前已加载的笔记"里收集标签（见 collectTags 注释）。 */
(() => {
  const PAGE_SIZE = 20;
  const PROBLEMS_LIMIT = 30;
  const SEARCH_DELAY = 250;
  const TITLE_FALLBACK_LEN = 30;
  const TAG_MAX = 10;

  let hooks = null;
  let generation = 0;
  let mounted = false;
  let searchTimer = null;
  let loading = false;
  let listRequest = 0;

  const notes = [];
  let total = 0;
  let hasMore = false;
  let query = "";
  let activeTag = "";
  let problemFilter = null; // { id, title } 或 null
  let problems = [];
  let problemsLoaded = false;
  let pendingPrefill = null; // { id, title }：从题目详情"记笔记"跳过来时预填关联题目

  const $ = (selector) => document.querySelector(selector);

  /* ───────────── 纯函数 ───────────── */

  /** 先转义再渲染是安全底线：所有 HTML 特殊字符在这里变成实体，后续只拼接我们自己生成的标签。 */
  function escapeHtml(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** 标签输入按逗号（半角/全角）、顿号、空白分隔，去重保序，最多 TAG_MAX 个。 */
  function parseTags(raw) {
    const seen = new Set();
    for (const part of String(raw ?? "").split(/[\s,，、]+/)) {
      const tag = part.trim();
      if (!tag || seen.has(tag)) continue;
      seen.add(tag);
      if (seen.size >= TAG_MAX) break;
    }
    return [...seen];
  }

  /** 笔记显示标题：有 title 用 title，否则取 content 首个非空行前 30 字。 */
  function noteTitle(note) {
    const title = String(note?.title ?? "").trim();
    if (title) return title;
    const firstLine = String(note?.content ?? "").split("\n").map((line) => line.trim()).find((line) => line) || "";
    return firstLine.slice(0, TITLE_FALLBACK_LEN);
  }

  /** 列表查询串：只带非默认值；limit 钳在 1..100。 */
  function buildNotesQuery({ q = "", tag = "", problemId = "", limit = PAGE_SIZE, offset = 0 } = {}) {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (tag) params.set("tag", tag);
    if (problemId !== "" && problemId !== null && problemId !== undefined) params.set("problem_id", String(problemId));
    const safeLimit = Math.min(100, Math.max(1, Number(limit) || PAGE_SIZE));
    if (safeLimit !== PAGE_SIZE) params.set("limit", String(safeLimit));
    if (offset > 0) params.set("offset", String(offset));
    const queryString = params.toString();
    return queryString ? `/api/notes?${queryString}` : "/api/notes";
  }

  // Markdown 子集直接构建 DOM。文本不经过 HTML 解析，也不使用占位符。
  function renderNoteMarkdown(source) {
    source = String(source ?? "");
    // N2：把 mermaid 围栏 / 块级公式 / 行内公式先换成私有区占位符，再走行解析；
    // 代码围栏与行内代码里的 $ / [[ ]] 由 NotesLinks.codeRanges 保护，不被替换。
    if (window.NotesRich && typeof window.NotesRich.prepareSource === "function") {
      source = window.NotesRich.prepareSource(source);
    }
    const root = document.createDocumentFragment();
    function inline(parent, text) {
      // 图片语法 !\[alt\]\(url\) 必须排在普通链接前面，否则 ! 后面的 [alt] 会被当成链接文本。
      const pattern = /`([^`\n]+)`|!\[([^\]]*)\]\(([^)\s]*)\)|\[([^\]]*)\]\(([^)\s]*)\)|\*\*([^*]+)\*\*|\*([^*]+)\*/g;
      let from = 0;
      for (const match of text.matchAll(pattern)) {
        parent.append(document.createTextNode(text.slice(from, match.index)));
        let node;
        if (match[1] !== undefined) node = h("code", match[1]);
        else if (match[2] !== undefined) {
          // N2：图片先落成 data-* 占位 span，是否真的渲染成 <img> 由 NotesRich 决定
          // （仅本人 attachment:ID 渲染；外链一律回退为纯文本，不发请求）。
          node = document.createElement("span");
          node.setAttribute("data-nr-img", "1");
          node.setAttribute("data-alt", match[2]);
          node.setAttribute("data-src", match[3]);
        } else if (match[4] !== undefined) {
          let safe = false;
          try { safe = /^https?:\/\//i.test(match[5]) && ["http:", "https:"].includes(new URL(match[5]).protocol); } catch {}
          if (safe) {
            node = h("a", match[4]);
            node.setAttribute("href", match[5]);
            node.setAttribute("target", "_blank");
            node.setAttribute("rel", "noopener");
          } else node = document.createTextNode(match[0]);
        } else node = h(match[6] !== undefined ? "strong" : "em", match[6] ?? match[7]);
        parent.append(node);
        from = match.index + match[0].length;
      }
      parent.append(document.createTextNode(text.slice(from)));
    }
    const lines = String(source ?? "").split("\n");
    let list = null;
    for (let index = 0; index < lines.length; index += 1) {
      const line = lines[index];
      if (line.startsWith("```")) {
        list = null;
        const body = [];
        while (++index < lines.length && !lines[index].startsWith("```")) body.push(lines[index]);
        const pre = h("pre"); pre.append(h("code", body.join("\n"))); root.append(pre);
        continue;
      }
      const heading = line.match(/^(#{1,3})\s+(.*)$/);
      const item = line.match(/^-\s+(.*)$/);
      if (item) {
        if (!list) { list = h("ul"); root.append(list); }
        const li = h("li"); inline(li, item[1]); list.append(li); continue;
      }
      list = null;
      if (!line.trim()) continue;
      const block = h(heading ? `h${heading[1].length}` : "p");
      inline(block, heading ? heading[2] : line); root.append(block);
    }
    // N1：代码块/行内代码已落成 <pre><code>/<code>，再对剩余文本节点做 [[ ]] 链接落位。
    // 注：仅在 notes-links.js 已加载时生效；Node 测试只加载 notes.js 时为 no-op。
    if (window.NotesLinks && typeof window.NotesLinks.attachInlineLinks === "function") {
      window.NotesLinks.attachInlineLinks(root);
    }
    // N2：attachment 图片落位 + 公式 / mermaid 占位渲染（按需懒加载 vendor）。
    if (window.NotesRich && typeof window.NotesRich.attachRich === "function") {
      window.NotesRich.attachRich(root);
    }
    return root;
  }

  /* 后端列表接口不返回标签聚合：这里从当前已加载的笔记里收集（按出现次数降序，
     次数相同按标签名升序），只够做"当前页/已加载范围"的筛选提示，这是取舍后的简化实现。 */
  function collectTags(loaded) {
    const counts = new Map();
    for (const note of loaded || []) {
      for (const tag of note?.tags || []) {
        const name = String(tag).trim();
        if (!name) continue;
        counts.set(name, (counts.get(name) || 0) + 1);
      }
    }
    return [...counts.entries()]
      .map(([tag, count]) => ({ tag, count }))
      .sort((a, b) => b.count - a.count || (a.tag < b.tag ? -1 : a.tag > b.tag ? 1 : 0));
  }

  /** 刚刚 / N 分钟前 / N 小时前 / N 天前（不足 30 天）/ 否则 YYYY-MM-DD；非法输入返回空串。 */
  function noteTime(value, nowMs = Date.now()) {
    const then = value === null || value === undefined || value === "" ? NaN : Date.parse(String(value));
    if (!Number.isFinite(then) || !Number.isFinite(nowMs)) return "";
    const seconds = Math.floor((nowMs - then) / 1000);
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 30 * 86400) return `${Math.floor(seconds / 86400)} 天前`;
    const date = new Date(then);
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  /* ───────────── 控制器 ───────────── */

  function configure(next) {
    hooks = next;
  }

  const alive = (token) => token.generation === generation
    && (!hooks?.getEpoch || hooks.getEpoch() === token.epoch)
    && (!hooks?.getUser || hooks.getUser()?.id === token.userId);
  const takeToken = () => ({
    generation,
    epoch: hooks?.getEpoch ? hooks.getEpoch() : null,
    userId: hooks?.getUser ? hooks.getUser()?.id : null,
  });

  function h(tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function composerNotice(text) {
    const notice = $("#notes-composer-notice");
    if (!notice) return;
    notice.hidden = !text;
    notice.textContent = text || "";
  }

  function sortNotes() {
    notes.sort((a, b) => {
      const pin = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
      if (pin) return pin;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });
  }

  function renderTagRow() {
    const row = $("#notes-tag-row");
    if (!row) return;
    row.replaceChildren();
    const all = h("button", "全部", "notes-chip");
    all.type = "button";
    all.setAttribute("aria-pressed", activeTag === "" ? "true" : "false");
    if (activeTag === "") all.classList.add("is-active");
    all.addEventListener("click", () => setTagFilter(""));
    row.append(all);
    for (const { tag, count } of collectTags(notes)) {
      const chip = h("button", `${tag} ${count}`, "notes-chip");
      chip.type = "button";
      chip.dataset.tag = tag;
      chip.setAttribute("aria-pressed", activeTag === tag ? "true" : "false");
      if (activeTag === tag) chip.classList.add("is-active");
      chip.addEventListener("click", () => setTagFilter(activeTag === tag ? "" : tag));
      row.append(chip);
    }
  }

  function setTagFilter(tag) {
    activeTag = tag;
    void reloadList();
  }

  function setProblemFilter(id, title) {
    problemFilter = id ? { id, title: title || "" } : null;
    void reloadList();
  }

  function renderNoteCard(note) {
    const card = h("article", null, "notes-card");
    card.setAttribute("data-note-id", String(note.id));
    if (note.pinned) card.classList.add("is-pinned");

    const head = h("div", null, "notes-card-head");
    const titleWrap = h("div", null, "notes-card-title");
    if (note.pinned) {
      const pin = h("span", "📌", "notes-pin");
      pin.setAttribute("aria-label", "已置顶");
      titleWrap.append(pin);
    }
    titleWrap.append(h("strong", noteTitle(note)));
    head.append(titleWrap);
    head.append(h("time", noteTime(note.updated_at || note.created_at), "notes-time"));
    card.append(head);

    const body = h("div", null, "notes-body");
    body.replaceChildren(renderNoteMarkdown(note.content));
    card.append(body);

    // N1：关联区（出链 + 反向链接）占位，由 window.NotesLinks 异步填充。
    const related = h("div", null, "nl-related");
    related.setAttribute("data-nl-related", "");
    card.append(related);

    const meta = h("div", null, "notes-meta");
    for (const tag of note.tags || []) {
      const chip = h("button", `#${tag}`, "notes-chip notes-chip-sm");
      chip.type = "button";
      chip.dataset.tag = tag;
      chip.setAttribute("aria-label", `按标签 ${tag} 筛选`);
      chip.addEventListener("click", () => setTagFilter(tag));
      meta.append(chip);
    }
    if (note.problem_id) {
      // 本应用没有独立的题目详情路由（详情是易错点视角），关联题目 chip 用"按题目筛选笔记"代替跳转。
      const problemChip = h("button", `📝 ${note.problem_title || `题目 ${note.problem_id}`}`, "notes-chip notes-chip-sm notes-problem-chip");
      problemChip.type = "button";
      problemChip.setAttribute("aria-label", `只看关联「${note.problem_title || note.problem_id}」的笔记`);
      problemChip.addEventListener("click", () => setProblemFilter(note.problem_id, note.problem_title));
      meta.append(problemChip);
    }
    card.append(meta);

    const actions = h("div", null, "notes-actions");
    const pinButton = h("button", note.pinned ? "取消置顶" : "置顶", "notes-action");
    pinButton.type = "button";
    pinButton.setAttribute("aria-label", note.pinned ? "取消置顶" : "置顶这条笔记");
    pinButton.addEventListener("click", () => togglePin(note));
    const editButton = h("button", "编辑", "notes-action");
    editButton.type = "button";
    editButton.setAttribute("aria-label", "编辑这条笔记");
    editButton.addEventListener("click", () => startEdit(card, note));
    const deleteButton = h("button", "删除", "notes-action is-danger");
    deleteButton.type = "button";
    deleteButton.setAttribute("aria-label", "删除这条笔记");
    deleteButton.addEventListener("click", () => removeNote(note));
    actions.append(pinButton, editButton, deleteButton);
    card.append(actions);
    return card;
  }

  function renderList() {
    const list = $("#notes-list");
    const status = $("#notes-status");
    const more = $("#notes-more");
    if (!list) return;
    list.replaceChildren();
    for (const note of notes) list.append(renderNoteCard(note));
    const filtersOn = query.trim() || activeTag || problemFilter;
    if (status) {
      status.hidden = false;
      if (!notes.length) {
        status.textContent = filtersOn ? "没有符合筛选的笔记，换个条件试试。" : "还没有笔记，在上面写下第一条吧。";
      } else {
        status.textContent = `共 ${total} 条${filtersOn ? "（已筛选）" : ""}`;
      }
    }
    if (more) more.hidden = !hasMore;
    renderTagRow();
  }

  async function reloadList() {
    if (!hooks?.api) return;
    const token = takeToken();
    const request = ++listRequest;
    loading = true;
    try {
      const data = await hooks.api(buildNotesQuery({
        q: query, tag: activeTag, problemId: problemFilter?.id ?? "", offset: 0,
      }));
      if (!alive(token) || request !== listRequest) return;
      notes.length = 0;
      notes.push(...(data?.notes || []));
      total = Number(data?.total) || 0;
      hasMore = notes.length < total;
      if (window.NotesRich && typeof window.NotesRich.configure === "function"
          && Array.isArray(data?.attachment_ids)) {
        window.NotesRich.configure({ ownedAttachmentIds: data.attachment_ids });
      }
      sortNotes();
      renderList();
    } catch (error) {
      if (!alive(token) || request !== listRequest) return;
      const status = $("#notes-status");
      if (status) { status.hidden = false; status.textContent = error?.message || "加载笔记失败，请稍后重试。"; }
    } finally {
      if (alive(token) && request === listRequest) loading = false;
    }
  }

  async function loadMore() {
    if (!hooks?.api || loading || !hasMore) return;
    const token = takeToken();
    const request = ++listRequest;
    loading = true;
    try {
      const data = await hooks.api(buildNotesQuery({
        q: query, tag: activeTag, problemId: problemFilter?.id ?? "", offset: notes.length,
      }));
      if (!alive(token) || request !== listRequest) return;
      notes.push(...(data?.notes || []));
      total = Number(data?.total) || 0;
      hasMore = notes.length < total;
      if (window.NotesRich && typeof window.NotesRich.configure === "function"
          && Array.isArray(data?.attachment_ids)) {
        window.NotesRich.configure({ ownedAttachmentIds: data.attachment_ids });
      }
      sortNotes();
      renderList();
    } catch (error) {
      if (!alive(token) || request !== listRequest) return;
      hooks?.notify?.(error?.message || "加载更多失败，请稍后重试。");
    } finally {
      if (alive(token) && request === listRequest) loading = false;
    }
  }

  function fillProblemOptions() {
    const select = $("#notes-problem");
    if (!select) return;
    const current = select.value;
    select.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "不关联";
    select.append(none);
    for (const problem of problems) {
      const option = document.createElement("option");
      option.value = String(problem.id);
      option.textContent = problem.title || `题目 ${problem.id}`;
      select.append(option);
    }
    // 预填的题目可能不在最近 30 条里：补一个选项，保证 openWithProblem 的值能选中。
    if (pendingPrefill && !problems.some((problem) => String(problem.id) === String(pendingPrefill.id))) {
      const option = document.createElement("option");
      option.value = String(pendingPrefill.id);
      option.textContent = pendingPrefill.title || `题目 ${pendingPrefill.id}`;
      select.append(option);
    }
    select.value = current || (pendingPrefill ? String(pendingPrefill.id) : "");
  }

  async function ensureProblems(token) {
    if (problemsLoaded || !hooks?.api) return;
    try {
      const data = await hooks.api(`/api/problems?limit=${PROBLEMS_LIMIT}`);
      if (!alive(token)) return;
      problems = data?.problems || [];
      problemsLoaded = true;
      fillProblemOptions();
    } catch {
      // 题目下拉加载失败不阻塞笔记列表；用户仍可写不关联题目的笔记。
    }
  }

  function applyPrefill() {
    if (!pendingPrefill) return;
    fillProblemOptions();
    const select = $("#notes-problem");
    if (select) select.value = String(pendingPrefill.id);
    const content = $("#notes-content");
    // N1：新建笔记正文预填 [[题:题目标题]]，直接建立题目双向链接。
    if (content && pendingPrefill.title && !content.value) {
      content.value = `[[题:${pendingPrefill.title}]]`;
    }
    if (content && hooks?.getView?.() === "notes") content.focus();
    pendingPrefill = null;
  }

  /** 从题目详情"记笔记"进入：先切到笔记页（showView 是 async，load 可能还没跑完），
      只把意图记在 pendingPrefill 里；已经 load 完就直接预填，否则 load() 结尾消费，避免竞态。 */
  function openWithProblem(problemId, problemTitle) {
    if (!problemId) return;
    pendingPrefill = { id: problemId, title: problemTitle || "" };
    if (problemsLoaded) applyPrefill();
  }

  function clearComposer() {
    const content = $("#notes-content");
    const tags = $("#notes-tags-input");
    const select = $("#notes-problem");
    if (content) content.value = "";
    if (tags) tags.value = "";
    if (select) select.value = "";
    composerNotice("");
  }

  async function saveComposer() {
    if (!hooks?.api) return;
    const token = takeToken();
    const contentEl = $("#notes-content");
    const content = String(contentEl?.value ?? "").trim();
    if (!content) {
      composerNotice("至少写点什么");
      contentEl?.focus();
      return;
    }
    const tags = parseTags($("#notes-tags-input")?.value);
    const problemId = $("#notes-problem")?.value || "";
    const draft = { content: contentEl?.value, tags: $("#notes-tags-input")?.value, problem: $("#notes-problem")?.value };
    const saveButton = $("#notes-save");
    if (saveButton) saveButton.disabled = true;
    try {
      const body = { content };
      if (tags.length) body.tags = tags;
      if (problemId) body.problem_id = Number(problemId) || problemId;
      const created = await hooks.api("/api/notes", { method: "POST", body: JSON.stringify(body) });
      if (!alive(token)) return;
      // 保存成功：清空筛选保证新笔记可见，插到列表顶部，不整页刷新。
      const filtered = query.trim() || activeTag || problemFilter;
      listRequest += 1;
      query = "";
      activeTag = "";
      problemFilter = null;
      const search = $("#notes-search");
      if (search) search.value = "";
      notes.unshift(created);
      total += 1;
      hasMore = notes.length < total;
      sortNotes();
      if (contentEl?.value === draft.content && $("#notes-tags-input")?.value === draft.tags && $("#notes-problem")?.value === draft.problem) clearComposer();
      if (filtered) await reloadList();
      else renderList();
      if (!alive(token)) return;
      hooks?.notify?.("笔记已保存。");
    } catch (error) {
      if (!alive(token)) return;
      composerNotice(error?.message || "保存失败，请稍后重试。");
    } finally {
      if (alive(token) && saveButton) saveButton.disabled = false;
    }
  }

  async function togglePin(note) {
    if (!hooks?.api) return;
    const token = takeToken();
    try {
      const updated = await hooks.api(`/api/notes/${note.id}`, {
        method: "PUT", body: JSON.stringify({ pinned: !note.pinned }),
      });
      if (!alive(token)) return;
      Object.assign(note, updated);
      sortNotes();
      renderList();
    } catch (error) {
      if (!alive(token)) return;
      hooks?.notify?.(error?.message || "操作失败，请稍后重试。");
    }
  }

  async function removeNote(note) {
    if (!hooks?.api) return;
    const token = takeToken();
    const ok = hooks.confirm ? hooks.confirm("确定删除这条笔记吗？删除后无法恢复。") : true;
    if (!ok) return;
    try {
      await hooks.api(`/api/notes/${note.id}`, { method: "DELETE" });
      if (!alive(token)) return;
      const index = notes.findIndex((item) => item.id === note.id);
      if (index >= 0) notes.splice(index, 1);
      total = Math.max(0, total - 1);
      renderList();
      hooks?.notify?.("笔记已删除。");
    } catch (error) {
      if (!alive(token)) return;
      hooks?.notify?.(error?.message || "删除失败，请稍后重试。");
    }
  }

  function startEdit(card, note) {
    const body = card.querySelector(".notes-body");
    const actions = card.querySelector(".notes-actions");
    if (!body || !actions) return;
    actions.hidden = true;
    const editor = h("div", null, "notes-editor");
    const titleInput = h("input", null, "notes-edit-title");
    titleInput.type = "text";
    titleInput.maxLength = 200;
    titleInput.value = note.title || "";
    titleInput.placeholder = "标题（可选，空则取正文首行）";
    titleInput.setAttribute("aria-label", "笔记标题");
    const area = h("textarea", null, "notes-edit-content");
    area.rows = 6;
    area.value = note.content || "";
    area.setAttribute("aria-label", "笔记正文");
    const tagInput = h("input", null, "notes-edit-tags");
    tagInput.type = "text";
    tagInput.value = (note.tags || []).join(" ");
    tagInput.placeholder = "标签，用逗号或空格分隔";
    tagInput.setAttribute("aria-label", "笔记标签");
    const row = h("div", null, "notes-editor-row");
    const save = h("button", "保存", "notes-action primary");
    save.type = "button";
    const cancel = h("button", "取消", "notes-action");
    cancel.type = "button";
    const error = h("p", "", "notes-notice");
    error.hidden = true;
    error.setAttribute("role", "status");
    row.append(save, cancel);
    editor.append(titleInput, area, tagInput, row, error);
    body.replaceWith(editor);
    area.focus();

    cancel.addEventListener("click", () => {
      editor.replaceWith(body);
      actions.hidden = false;
    });
    save.addEventListener("click", async () => {
      const content = area.value.trim();
      if (!content) {
        error.hidden = false;
        error.textContent = "至少写点什么";
        return;
      }
      const token = takeToken();
      save.disabled = true;
      try {
        const updated = await hooks.api(`/api/notes/${note.id}`, {
          method: "PUT",
          body: JSON.stringify({
            title: titleInput.value.trim(),
            content: area.value,
            tags: parseTags(tagInput.value),
          }),
        });
        if (!alive(token)) return;
        Object.assign(note, updated);
        sortNotes();
        renderList();
        hooks?.notify?.("笔记已更新。");
      } catch (requestError) {
        if (!alive(token)) return;
        error.hidden = false;
        error.textContent = requestError?.message || "保存失败，请稍后重试。";
        save.disabled = false;
      }
    });
  }

  function bindDom() {
    const saveButton = $("#notes-save");
    if (saveButton && !saveButton.dataset.notesBound) {
      saveButton.dataset.notesBound = "1";
      saveButton.addEventListener("click", () => void saveComposer());
    }
    const search = $("#notes-search");
    if (search && !search.dataset.notesBound) {
      search.dataset.notesBound = "1";
      search.addEventListener("input", () => {
        if (searchTimer) window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
          query = search.value;
          void reloadList();
        }, SEARCH_DELAY);
      });
    }
    const more = $("#notes-more");
    if (more && !more.dataset.notesBound) {
      more.dataset.notesBound = "1";
      more.addEventListener("click", () => void loadMore());
    }
    mounted = true;
  }

  /** 进入笔记页：拉题目下拉 + 第一页列表；消费 openWithProblem 留下的预填。 */
  async function load() {
    generation += 1;
    const token = takeToken();
    bindDom();
    await ensureProblems(token);
    if (!alive(token)) return;
    await reloadList();
    if (!alive(token)) return;
    if (pendingPrefill) applyPrefill();
  }

  /** 登出 / 换账号：丢掉所有数据、作废在途请求。 */
  function reset() {
    generation += 1;
    listRequest += 1;
    const saveButton = $("#notes-save");
    if (saveButton) saveButton.disabled = false;
    if (searchTimer) window.clearTimeout(searchTimer);
    searchTimer = null;
    loading = false;
    notes.length = 0;
    total = 0;
    hasMore = false;
    query = "";
    activeTag = "";
    problemFilter = null;
    problems = [];
    problemsLoaded = false;
    pendingPrefill = null;
    const search = $("#notes-search");
    if (search) search.value = "";
    $("#notes-list")?.replaceChildren();
    const status = $("#notes-status");
    if (status) status.hidden = true;
    const more = $("#notes-more");
    if (more) more.hidden = true;
    $("#notes-tag-row")?.replaceChildren();
    clearComposer();
  }

  function snapshot() {
    return {
      count: notes.length, total, hasMore, query, activeTag,
      problemFilter: problemFilter ? { ...problemFilter } : null,
      pendingPrefill: pendingPrefill ? { ...pendingPrefill } : null,
      problemsLoaded, loading,
    };
  }

  window.Notes = Object.freeze({
    escapeHtml, parseTags, noteTitle, buildNotesQuery, renderNoteMarkdown, collectTags, noteTime,
    configure, load, loadMore, openWithProblem, reset, snapshot,
    generation: () => generation,
  });
})();
