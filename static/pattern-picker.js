"use strict";

/* 「属于哪个套路」选择器：新增记录页和编辑题目弹窗共用，选项来自
   /static/data/patterns.json（与套路库、学习路线同一份数据）。
   对外契约：window.PatternPicker = { load(), create({ selected, max, legend }) }。
   create() 返回 { el, values(), setValues(list) }；纯 DOM，不联网写数据、不读本地存储。 */
(() => {
  const R = window.PatternRender;
  const node = R.node;
  let catalog = null;
  let pending = null;

  function load() {
    if (catalog) return Promise.resolve(catalog);
    if (!pending) {
      pending = fetch("/static/data/patterns.json", { credentials: "omit" })
        .then((response) => {
          if (!response.ok) throw new Error(String(response.status));
          return response.json();
        })
        .then((data) => {
          catalog = [...data].sort((a, b) => a.title.localeCompare(b.title, "zh"));
          return catalog;
        });
    }
    return pending;
  }

  const MAX = 3;
  const SHOWN = 8;

  function create({ selected = [], max = MAX, legend = "属于哪个套路（可选，最多 3 个）" } = {}) {
    let picked = [...selected];
    let patterns = catalog || [];

    const el = document.createElement("fieldset");
    el.className = "pattern-picker";
    const title = node("legend", "pattern-picker-legend", legend);
    el.append(title);

    const chosen = node("ul", "pattern-picker-chosen");
    chosen.setAttribute("aria-label", "已选的套路");
    const search = node("input", "pattern-picker-input");
    search.type = "search";
    search.placeholder = "例如：二分、背包、最短路";
    search.autocomplete = "off";
    const searchField = node("label", "pattern-picker-search", "搜索套路");
    searchField.append(search);
    const options = node("ul", "pattern-picker-options");
    options.setAttribute("aria-label", "可选的套路");
    const hint = node("p", "pattern-picker-hint", "");
    hint.setAttribute("role", "status");
    hint.setAttribute("aria-live", "polite");
    el.append(chosen, searchField, options, hint);

    function byId(id) {
      return patterns.find((pattern) => pattern.id === id);
    }

    function paintChosen() {
      chosen.replaceChildren(...picked.map((id) => {
        const pattern = byId(id);
        const row = node("li", "pattern-picker-chip");
        row.append(node("span", "", pattern ? pattern.title : id));
        const remove = node("button", "pattern-picker-remove", "移除");
        remove.type = "button";
        remove.setAttribute("aria-label", `移除${pattern ? pattern.title : id}`);
        remove.addEventListener("click", () => {
          picked = picked.filter((item) => item !== id);
          paint();
        });
        row.append(remove);
        return row;
      }));
    }

    function paintOptions() {
      const needle = String(search.value || "").trim().toLowerCase();
      const matched = patterns.filter((pattern) => !needle || R.searchText(pattern).includes(needle));
      const shown = matched.slice(0, SHOWN);
      options.replaceChildren(...shown.map((pattern) => {
        const row = node("li", "pattern-picker-option");
        const button = node("button", "pattern-picker-pick",
          `${pattern.title}　${pattern.category} · ${pattern.level}`);
        button.type = "button";
        const on = picked.includes(pattern.id);
        button.setAttribute("aria-pressed", String(on));
        button.disabled = !on && picked.length >= max;
        button.addEventListener("click", () => {
          if (picked.includes(pattern.id)) {
            picked = picked.filter((item) => item !== pattern.id);
          } else if (picked.length < max) {
            picked = [...picked, pattern.id];
          }
          paint();
        });
        row.append(button);
        return row;
      }));
      if (!shown.length) {
        options.append(node("li", "pattern-picker-empty", "没有匹配的套路，换个词试试。"));
        return;
      }
      if (matched.length > shown.length) {
        options.append(node("li", "pattern-picker-more", `还有 ${matched.length - shown.length} 个，继续输入关键词缩小范围。`));
      }
    }

    function paintHint() {
      if (picked.length >= max) hint.textContent = `已经选了 ${max} 个，再选要先移除一个。`;
      else hint.textContent = picked.length ? `已选 ${picked.length} / ${max}` : "";
    }

    function paint() {
      paintChosen();
      paintOptions();
      paintHint();
    }

    search.addEventListener("input", paintOptions);
    paint();

    return {
      el,
      values: () => [...picked],
      setValues(list) {
        picked = [...(list || [])].slice(0, max);
        paint();
      },
      refresh(list) {
        patterns = [...(list || patterns)].sort((a, b) => a.title.localeCompare(b.title, "zh"));
        paint();
      },
    };
  }

  window.PatternPicker = {
    load, create, MAX,
    titleOf(id) {
      const found = (catalog || []).find((pattern) => pattern.id === id);
      return found ? found.title : id;
    },
  };
})();
