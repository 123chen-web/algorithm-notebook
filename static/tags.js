"use strict";

/* 错因标签：详情页里贴标签（TagEditor），侧栏和"全部记录"页里按标签筛选（TagFilters）。
   对外契约：
     window.TagEditor = { render(item) → 元素, chips(tags) → 元素 }
     window.TagFilters = { mountSidebar(el), bindSelect(select), refresh(), setSelected(tag), tags(), reset() }
   事件：保存成功后派发 mistake:tags-changed { id, tags }；点侧栏标签派发 records:filter { tag }。
   用户文本一律 textContent。 */
(() => {
  const state = { tags: [], suggestions: [], limits: { per_mistake: 8, length: 20 }, loaded: false, selected: "" };
  let sidebar = null;
  let select = null;
  let loading = null;
  let epoch = 0; // 登出/换账号时加一：之前发出的请求回来也不能再写入
  let requestId = 0;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function normalize(value) {
    return value.replace(/\s+/gu, " ").trim();
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
      section.append(node("p", "sidebar-hint", state.loaded ? "打开一条记录，给它贴上「边界」「粗心」这样的标签，之后可以按标签筛选。" : ""));
    } else {
      const list = node("ul", "tag-pill-list");
      for (const item of state.tags.slice(0, 12)) {
        const entry = node("li");
        const pill = node("button", "tag-pill");
        pill.type = "button";
        pill.setAttribute("aria-pressed", String(state.selected.toLowerCase() === item.tag.toLowerCase()));
        pill.title = `只看带「${item.tag}」标签的记录`;
        pill.append(node("span", "tag-pill-name", item.tag), node("span", "tag-pill-count", String(item.count)));
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
    const options = [["", "全部标签"], ...state.tags.map((item) => [item.tag, `${item.tag} · ${item.count}`])];
    if (current && !state.tags.some((item) => item.tag.toLowerCase() === current.toLowerCase())) options.push([current, current]);
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
    wrap.setAttribute("aria-label", `标签：${tags.join("、")}`);
    for (const tag of tags) wrap.append(node("span", "tag-chip tag-chip-static", tag));
    return wrap;
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
    const list = node("ul", "tag-list");
    list.setAttribute("aria-label", "已添加的标签");
    const form = node("form", "tag-add");
    form.autocomplete = "off";
    const inputId = `tag-input-${item.id}`;
    const label = node("label", "tag-sr-only", "添加标签");
    label.htmlFor = inputId;
    const input = node("input", "tag-input");
    input.id = inputId;
    input.type = "text";
    input.maxLength = state.limits.length;
    input.placeholder = "添加标签，回车确认";
    const submit = node("button", "tag-submit", "添加");
    submit.type = "submit";
    form.append(label, input, submit);
    const suggest = node("div", "tag-suggest");
    suggest.setAttribute("role", "group");
    suggest.setAttribute("aria-label", "常用标签，点一下添加");
    const status = node("p", "tag-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    function draw() {
      list.replaceChildren(...tags.map((tag) => {
        const entry = node("li", "tag-chip");
        entry.append(node("span", "", tag));
        const remove = node("button", "tag-remove", "×");
        remove.type = "button";
        remove.setAttribute("aria-label", `移除标签 ${tag}`);
        remove.addEventListener("click", () => commit(tags.filter((value) => value !== tag), `已移除「${tag}」`));
        entry.append(remove);
        return entry;
      }));
      list.hidden = tags.length === 0;
      const taken = new Set(tags.map((tag) => tag.toLowerCase()));
      const mine = state.tags.map((entry) => entry.tag);
      const pool = [...new Set([...mine.slice(0, 6), ...state.suggestions])].filter((tag) => !taken.has(tag.toLowerCase())).slice(0, 10);
      suggest.replaceChildren(...pool.map((tag) => {
        const button = node("button", "tag-suggestion", `＋ ${tag}`);
        button.type = "button";
        button.addEventListener("click", () => add(tag));
        return button;
      }));
      suggest.hidden = pool.length === 0 || tags.length >= state.limits.per_mistake;
      form.hidden = tags.length >= state.limits.per_mistake;
    }

    function add(raw) {
      const tag = normalize(raw);
      if (!tag) return;
      if (tag.length > state.limits.length) {
        status.textContent = `标签最多 ${state.limits.length} 个字`;
        return;
      }
      if (tags.some((value) => value.toLowerCase() === tag.toLowerCase())) {
        status.textContent = `已经有「${tag}」了`;
        input.value = "";
        return;
      }
      if (tags.length >= state.limits.per_mistake) {
        status.textContent = `每条最多 ${state.limits.per_mistake} 个标签`;
        return;
      }
      commit([...tags, tag], `已添加「${tag}」`);
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
        input.value = "";
        status.textContent = doneText;
        draw();
        document.dispatchEvent(new CustomEvent("mistake:tags-changed", { detail: { id: item.id, tags } }));
        refresh();
      } catch {
        status.textContent = "网络出错，标签没有保存。";
      } finally {
        saving = false;
        section.removeAttribute("aria-busy");
      }
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      add(input.value);
    });
    section.append(title, list, form, suggest, status);
    draw();
    // 建议里要用到"我自己的常用标签"，取回来再画一次。
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
    return load().then(renderAll);
  }
  function bindSelect(element) {
    select = element;
    return load().then(renderAll);
  }
  function setSelected(tag) {
    state.selected = tag || "";
    renderSidebar();
  }
  function reset() {
    epoch += 1;
    requestId += 1;
    loading = null;
    state.tags = [];
    state.loaded = false;
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

  window.TagEditor = { render, chips };
  window.TagFilters = { mountSidebar, bindSelect, refresh, setSelected, tags: () => state.tags, reset };
})();
