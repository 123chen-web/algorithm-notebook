"use strict";

/* 错因标签：详情页里贴标签（TagEditor），侧栏和"全部记录"页里按标签筛选（TagFilters）。
   标签只能从固定词表里选（GET /api/error-taxonomy），没有自由输入；
   「其他」是词表里的一项，不是兜底的文本框。
   对外契约：
     window.TagEditor = { render(item) → 元素, chips(tags) → 元素, label(code) → 中文名 }
     window.TagFilters = { mountSidebar(el), bindSelect(select), refresh(), setSelected(tag), tags(), reset() }
   事件：保存成功后派发 mistake:tags-changed { id, tags }；点侧栏标签派发 records:filter { tag }。
   用户文本一律 textContent。 */
(() => {
  const state = {
    tags: [], suggestions: [], limits: { per_mistake: 8, length: 20 }, loaded: false, selected: "",
    causes: [], groups: [], verdicts: [], taxonomyLoaded: false,
  };
  let sidebar = null;
  let select = null;
  let loading = null;
  let taxonomyLoading = null;
  let epoch = 0; // 登出/换账号时加一：之前发出的请求回来也不能再写入
  let requestId = 0;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  /* ---------- 数据 ---------- */
  async function load(force = false) {
    if (state.loaded && !force) return state;
    // 强制刷新必须真的重新取一次（数据刚改过）；旧请求的结果不能盖过新请求，更不能写进下一位用户的状态。
    if (!loading || force) {
      const mine = ++requestId;
      const startedIn = epoch;
      const request = fetch("/api/tags", { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } })
        .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`tags ${response.status}`))))
        .then((data) => {
          if (startedIn !== epoch || mine !== requestId) return;
          state.tags = data.tags;
          state.suggestions = data.suggestions;
          state.limits = data.limits;
          state.loaded = true;
        })
        .catch(() => {})
        .finally(() => { if (loading === request) loading = null; });
      loading = request;
    }
    await loading;
    return state;
  }

  async function loadTaxonomy() {
    if (state.taxonomyLoaded) return state;
    if (!taxonomyLoading) {
      const startedIn = epoch;
      taxonomyLoading = fetch("/api/error-taxonomy", { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } })
        .then((response) => (response.ok ? response.json() : Promise.reject(new Error(`taxonomy ${response.status}`))))
        .then((data) => {
          if (startedIn !== epoch) return;
          state.causes = data.causes || [];
          state.groups = data.groups || [];
          state.verdicts = data.verdicts || [];
          state.taxonomyLoaded = true;
        })
        .catch(() => {})
        .finally(() => { taxonomyLoading = null; });
    }
    await taxonomyLoading;
    return state;
  }

  // code → 中文名；词表没加载出来或未知 code 时原样返回。
  function label(code) {
    const found = state.causes.find((cause) => cause.code === code);
    return found ? found.label : code;
  }
  function verdictLabel(code) {
    const found = state.verdicts.find((verdict) => verdict.code === code);
    return found ? found.label : code;
  }

  /* ---------- 侧栏 ---------- */
  function renderSidebar() {
    if (!sidebar) return;
    const section = node("section", "sidebar-section");
    section.setAttribute("aria-labelledby", "sidebar-tags-title");
    const head = node("div", "sidebar-section-head");
    const title = node("h2", "", "错因标签");
    title.id = "sidebar-tags-title";
    head.append(title);
    section.append(head);
    if (!state.tags.length) {
      section.append(node("p", "sidebar-hint", state.loaded ? "打开一条记录，从固定词表里给它选错因标签，之后可以按标签筛选。" : ""));
    } else {
      const list = node("ul", "tag-pill-list");
      for (const item of state.tags.slice(0, 12)) {
        const entry = node("li");
        const pill = node("button", "tag-pill");
        pill.type = "button";
        pill.setAttribute("aria-pressed", String(state.selected.toLowerCase() === item.tag.toLowerCase()));
        pill.title = `只看带「${label(item.tag)}」标签的记录`;
        pill.append(node("span", "tag-pill-name", label(item.tag)), node("span", "tag-pill-count", String(item.count)));
        pill.addEventListener("click", () => {
          const same = state.selected.toLowerCase() === item.tag.toLowerCase();
          document.dispatchEvent(new CustomEvent("records:filter", { detail: { tag: same ? "" : item.tag } }));
        });
        entry.append(pill);
        list.append(entry);
      }
      section.append(list);
    }
    sidebar.replaceChildren(section);
    sidebar.hidden = !state.loaded;
  }

  function renderSelect() {
    if (!select) return;
    const current = select.value || state.selected;
    const options = [["", "全部标签"], ...state.tags.map((item) => [item.tag, `${label(item.tag)} · ${item.count}`])];
    if (current && !state.tags.some((item) => item.tag.toLowerCase() === current.toLowerCase())) options.push([current, label(current)]);
    select.replaceChildren(...options.map(([value, text]) => {
      const option = node("option", "", text);
      option.value = value;
      return option;
    }));
    select.value = current;
    if (select.value !== current) select.value = "";
    const wrapper = select.closest("label");
    if (wrapper) wrapper.hidden = !state.tags.length && !current;
  }

  function renderAll() {
    renderSidebar();
    renderSelect();
  }

  /* ---------- 标签小条（列表卡片用） ---------- */
  function chips(tags) {
    const wrap = node("span", "record-tags");
    wrap.setAttribute("aria-label", `标签：${tags.map(label).join("、")}`);
    for (const tag of tags) wrap.append(node("span", "tag-chip tag-chip-static", label(tag)));
    return wrap;
  }

  /** “汇成笔记”的草稿：标题、每道题一行 [[题:题名]]：当时的错因、留一节“我的对策”给自己写。只用自己记过的内容。 */
  function recurrenceNote(current, group) {
    const title = (value) => String(value || "未命名").replace(/[[\]|]/g, " ").replace(/\s+/g, " ").trim() || "未命名";
    const why = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const line = (entry) => `- [[题:${title(entry.title)}]]${why(entry.description) ? `：${why(entry.description)}` : ""}`;
    const name = label(group.tag);
    const entries = [current, ...(group.items || [])];
    const count = Number(group.total || (group.items || []).length) + 1;
    return [
      `# 我的老毛病：${name}`,
      "",
      `「${name}」这类错，我在 ${count} 道题里都犯过：`,
      "",
      ...entries.map(line),
      "",
      "## 我的对策",
      "",
      "- 下次看到……，先……",
      "",
    ].join("\n");
  }

  /* ---------- 详情页的标签编辑器 ---------- */
  function render(item) {
    let tags = [...(item.tags || [])];
    let saving = false;
    const section = node("section", "tag-editor");
    const titleId = `tag-editor-title-${item.id}`;
    section.setAttribute("aria-labelledby", titleId);
    const title = node("h3", "section-label", "错因标签");
    title.id = titleId;
    const hint = node("p", "tag-editor-hint", "从下面点选，最多 8 个；想法写进「错因描述」。");
    hint.id = `${titleId}-hint`;
    const groupsWrap = node("div", "tag-taxonomy");
    groupsWrap.setAttribute("role", "group");
    groupsWrap.setAttribute("aria-describedby", hint.id);
    const status = node("p", "tag-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    // 老毛病：同一个错因，以前在别的题里也犯过。只是把它们串起来，点题名就能看那条。
    const recurrence = node("div", "tag-recurrence");
    recurrence.hidden = true;
    let recurrenceSeq = 0;

    /** 点“汇成笔记”：取这个错因下最多 20 道题，拼成草稿放进笔记的新建区。 */
    async function gatherNote(tag, button) {
      if (typeof window.showView !== "function" || !window.Notes?.newFromText) return;
      button.disabled = true;
      const startEpoch = epoch;
      try {
        const response = await fetch(`/api/mistakes/${item.id}/same-cause?limit=20`, {
          credentials: "same-origin", headers: { "X-CSRF-Protection": "1" },
        });
        const data = response.ok ? await response.json() : null;
        if (startEpoch !== epoch) return;
        const group = (data?.groups || []).find((entry) => entry.tag === tag);
        if (!group) {
          status.textContent = "没取到同错因的题，稍后再试。";
          return;
        }
        await window.showView("notes");
        await window.Notes.newFromText(recurrenceNote(item, group));
      } catch {
        status.textContent = "网络出错，没能汇成笔记。";
      } finally {
        button.disabled = false;
      }
    }

    async function showRecurrence() {
      const mine = ++recurrenceSeq;
      const startEpoch = epoch;
      if (!tags.length) {
        recurrence.hidden = true;
        recurrence.replaceChildren();
        return;
      }
      let data = null;
      try {
        const response = await fetch(`/api/mistakes/${item.id}/same-cause`, {
          credentials: "same-origin", headers: { "X-CSRF-Protection": "1" },
        });
        if (response.ok) data = await response.json();
      } catch { data = null; }
      if (mine !== recurrenceSeq || startEpoch !== epoch || !section.isConnected) return;
      const groups = Array.isArray(data?.groups) ? data.groups : [];
      recurrence.replaceChildren();
      for (const group of groups) {
        const line = node("p", "tag-recurrence-line");
        line.append(node("span", "tag-recurrence-badge", "老毛病"), document.createTextNode(`「${label(group.tag)}」：你在 `));
        group.items.forEach((entry, index) => {
          if (index) line.append(document.createTextNode("、"));
          const link = node("button", "tag-recurrence-link", `《${entry.title || "未命名"}》`);
          link.type = "button";
          if (entry.description) link.title = entry.description;
          link.setAttribute("aria-label", `打开《${entry.title || "未命名"}》这条易错点`);
          link.addEventListener("click", () => {
            if (typeof window.openMistake === "function") void window.openMistake(entry.id);
          });
          line.append(link);
        });
        const more = group.total > group.items.length ? ` 等 ${group.total} 道题里` : " 里";
        line.append(document.createTextNode(`${more}也是这样错的。`));
        const gather = node("button", "tag-recurrence-gather", "汇成笔记");
        gather.type = "button";
        gather.title = "把这几道题和当时写的错因放进一篇新笔记草稿，自己写对策；不保存就不会留下";
        gather.addEventListener("click", () => void gatherNote(group.tag, gather));
        line.append(document.createTextNode(" "), gather);
        recurrence.append(line);
      }
      recurrence.hidden = !recurrence.childNodes.length;
    }

    function draw() {
      const selected = new Set(tags.map((tag) => tag.toLowerCase()));
      groupsWrap.replaceChildren(...state.groups.map((group) => {
        const members = state.causes.filter((cause) => cause.group === group.code);
        if (!members.length) return null;
        const block = node("div", "tag-group");
        block.append(node("h4", "tag-group-title", group.label));
        const list = node("ul", "tag-chip-list");
        for (const cause of members) {
          const entry = node("li");
          const chip = node("button", "tag-seal");
          chip.type = "button";
          const on = selected.has(cause.code.toLowerCase());
          chip.setAttribute("aria-pressed", String(on));
          chip.title = cause.hint;
          chip.append(node("span", "tag-seal-label", cause.label));
          if (on) {
            const remove = node("span", "tag-seal-remove", "×");
            remove.setAttribute("aria-hidden", "true");
            chip.append(remove);
            chip.setAttribute("aria-label", `移除标签 ${cause.label}`);
          } else {
            chip.setAttribute("aria-label", `添加标签 ${cause.label}：${cause.hint}`);
          }
          chip.addEventListener("click", () => {
            if (on) remove(cause.code, cause.label);
            else add(cause.code, cause.label);
          });
          entry.append(chip);
          list.append(entry);
        }
        block.append(list);
        return block;
      }).filter(Boolean));
      const empty = node("p", "tag-editor-empty", tags.length ? "" : "还没选错因标签，点上面的印章选一个。");
      empty.hidden = tags.length > 0;
      groupsWrap.append(empty);
    }

    function add(code, display) {
      if (tags.some((value) => value.toLowerCase() === code.toLowerCase())) {
        status.textContent = `已经有「${display}」了`;
        return;
      }
      if (tags.length >= state.limits.per_mistake) {
        status.textContent = `每条最多 ${state.limits.per_mistake} 个标签`;
        return;
      }
      commit([...tags, code], `已添加「${display}」`);
    }

    function remove(code, display) {
      const next = tags.filter((value) => value.toLowerCase() !== code.toLowerCase());
      // 完整记录（非待补）至少保留 1 个；服务端也会 422，这里先给提示。
      if (!next.length && !item.pending_reason) {
        status.textContent = "完整记录请至少保留 1 个错因标签";
        return;
      }
      commit(next, `已移除「${display}」`);
    }

    async function commit(next, doneText) {
      if (saving) return;
      saving = true;
      section.setAttribute("aria-busy", "true");
      try {
        const response = await fetch(`/api/mistakes/${item.id}/tags`, {
          method: "PUT", credentials: "same-origin",
          headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" },
          body: JSON.stringify({ tags: next }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) {
          status.textContent = typeof data.detail === "string" ? data.detail : "标签没有保存，请再试一次。";
          return;
        }
        tags = data.tags;
        item.tags = tags;
        status.textContent = doneText;
        draw();
        void showRecurrence();
        document.dispatchEvent(new CustomEvent("mistake:tags-changed", { detail: { id: item.id, tags } }));
        refresh();
      } catch {
        status.textContent = "网络出错，标签没有保存。";
      } finally {
        saving = false;
        section.removeAttribute("aria-busy");
      }
    }

    section.append(title, hint, groupsWrap, status, recurrence);
    draw();
    void showRecurrence();
    // 词表取回来再画一次。
    loadTaxonomy().then(() => { if (section.isConnected) draw(); });
    load().then(() => { if (section.isConnected) draw(); });
    return section;
  }

  /* ---------- 对外 ---------- */
  function refresh() {
    return load(true).then(renderAll);
  }
  function mountSidebar(element) {
    sidebar = element;
    sidebar.hidden = true;
    return Promise.all([load(), loadTaxonomy()]).then(renderAll);
  }
  function bindSelect(element) {
    select = element;
    return Promise.all([load(), loadTaxonomy()]).then(renderAll);
  }
  function setSelected(tag) {
    state.selected = tag || "";
    renderSidebar();
  }
  function reset() {
    epoch += 1;
    requestId += 1;
    loading = null;
    taxonomyLoading = null;
    state.tags = [];
    state.loaded = false;
    state.taxonomyLoaded = false;
    state.selected = "";
    if (sidebar) {
      sidebar.replaceChildren();
      sidebar.hidden = true;
    }
    if (select) {
      select.replaceChildren(Object.assign(node("option", "", "全部标签"), { value: "" }));
      const wrapper = select.closest("label");
      if (wrapper) wrapper.hidden = true;
    }
  }

  // 新增/删除/评分之后各标签的数量会变。
  document.addEventListener("app:data-changed", () => { if (sidebar || select) refresh(); });

  window.TagEditor = { render, chips, label, verdictLabel, recurrenceNote };
  window.TagFilters = { mountSidebar, bindSelect, refresh, setSelected, tags: () => state.tags, reset };
})();
