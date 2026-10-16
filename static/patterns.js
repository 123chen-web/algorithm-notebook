"use strict";

/* 算法套路库（独立只读页）：读取 /static/data/patterns.json，纯前端搜索与分类筛选。
   卡片渲染走 static/pattern-render.js，和主站「学习路线」共用同一份，不复制两份。
   这里不登录、不带 Cookie、不发送任何用户数据，也不读写本地存储。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const R = window.PatternRender;
  let patterns = [];
  let category = "";
  let query = "";

  function render() {
    const list = $("#patterns-list");
    const byId = new Map(patterns.map((pattern) => [pattern.id, pattern]));
    const shown = patterns.filter((pattern) => R.matches(pattern, { category, query }));
    list.replaceChildren(...shown.map((pattern) => R.renderCard(pattern, { byId })));
    $("#patterns-status").textContent = shown.length
      ? `共 ${shown.length} 个套路`
      : "没有找到相关套路，换个词试试。";
  }

  function renderCategories() {
    const box = $("#patterns-cats");
    const names = R.groupsOf(patterns);
    const make = (name, label) => {
      const button = R.node("button", "patterns-cat", label);
      button.type = "button";
      button.setAttribute("aria-pressed", name === category ? "true" : "false");
      button.addEventListener("click", () => {
        category = name;
        for (const other of box.querySelectorAll("button")) {
          other.setAttribute("aria-pressed", other === button ? "true" : "false");
        }
        render();
      });
      return button;
    };
    box.replaceChildren(make("", "全部"), ...names.map((name) => make(name, name)));
  }

  async function load() {
    try {
      const response = await fetch("/static/data/patterns.json", { credentials: "omit" });
      if (!response.ok) throw new Error(String(response.status));
      patterns = (await response.json()).sort(
        (a, b) => R.LEVEL_ORDER.indexOf(a.level) - R.LEVEL_ORDER.indexOf(b.level)
      );
      renderCategories();
      render();
    } catch {
      $("#patterns-status").textContent = "套路库加载失败，请刷新页面重试。";
    }
  }

  $("#patterns-q").addEventListener("input", (event) => { query = event.target.value; render(); });
  void load();
})();
