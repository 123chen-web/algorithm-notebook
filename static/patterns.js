"use strict";

/* 算法套路库：读取 /static/data/patterns.json，纯前端搜索与分类筛选。
   所有文本一律 textContent，不拼接 HTML 字符串，不发送任何用户数据。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const LEVEL_ORDER = ["入门", "进阶", "竞赛"];
  const SOURCE_NAMES = { "leetcode-cn": "力扣", luogu: "洛谷", codeforces: "Codeforces" };
  let patterns = [];
  let category = "";
  let query = "";

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function searchText(pattern) {
    return [
      pattern.title, pattern.category, pattern.idea, pattern.complexity,
      ...(pattern.signals || []), ...(pattern.pitfalls || []), pattern.confusions || "",
      ...(pattern.examples || []).map((example) => `${example.id} ${example.title}`),
    ].join("\n").toLowerCase();
  }

  function matches(pattern) {
    if (category && pattern.category !== category) return false;
    const needle = query.trim().toLowerCase();
    return !needle || searchText(pattern).includes(needle);
  }

  function section(title, child) {
    const wrap = node("section", "pattern-section");
    wrap.append(node("h4", "", title), child);
    return wrap;
  }

  function bulletList(items) {
    const list = node("ul", "pattern-bullets");
    for (const text of items || []) list.append(node("li", "", text));
    return list;
  }

  function codeBlock(code) {
    const pre = node("pre", "pattern-code");
    pre.append(node("code", "", code || ""));
    return pre;
  }

  function noteMarkdown(pattern) {
    const lines = [
      `# ${pattern.title}`, "", `> ${pattern.idea}`, "",
      "## 什么时候想到它", ...(pattern.signals || []).map((s) => `- ${s}`), "",
      "## 常见坑", ...(pattern.pitfalls || []).map((s) => `- ${s}`), "",
      `复杂度：${pattern.complexity}`, "",
      "## 模板（Python）", "```python", pattern.template_python || "", "```", "",
      "## 我的理解", "", "____", "", "## 我踩过的坑", "", "____", "",
    ];
    return lines.join("\n");
  }

  async function copyText(text, button) {
    const original = button.textContent;
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = "已复制";
    } catch {
      button.textContent = "复制失败，请手动选中";
    }
    window.setTimeout(() => { button.textContent = original; }, 1800);
  }

  function renderCard(pattern, byId) {
    const item = node("li", "pattern-card");
    const details = node("details", "pattern-details");
    const summary = node("summary", "pattern-summary");
    summary.append(
      node("span", "pattern-title", pattern.title),
      node("span", "pattern-chip", pattern.category),
      node("span", "pattern-chip pattern-level", pattern.level),
    );
    details.append(summary);
    const body = node("div", "pattern-body");
    body.append(node("p", "pattern-idea", pattern.idea));
    body.append(section("什么时候该想到它", bulletList(pattern.signals)));
    body.append(section("复杂度", node("p", "", pattern.complexity)));
    body.append(section("常见坑", bulletList(pattern.pitfalls)));
    if (pattern.confusions) body.append(section("容易混淆", node("p", "", pattern.confusions)));
    const codes = node("div", "pattern-codes");
    codes.append(
      node("h5", "", "Python 模板"), codeBlock(pattern.template_python),
      node("h5", "", "C++ 模板"), codeBlock(pattern.template_cpp),
    );
    body.append(section("模板代码", codes));
    const examples = node("ul", "pattern-bullets");
    for (const example of pattern.examples || []) {
      examples.append(node("li", "", `${SOURCE_NAMES[example.source] || example.source} ${example.id}　${example.title}`));
    }
    body.append(section("经典例题（只列题号和题名，请自行到对应网站查看）", examples));
    const related = (pattern.related || []).map((id) => byId.get(id)).filter(Boolean);
    if (related.length) {
      body.append(node("p", "pattern-related", `相关套路：${related.map((entry) => entry.title).join("、")}`));
    }
    const copy = node("button", "pattern-copy", "复制为笔记模板");
    copy.type = "button";
    copy.addEventListener("click", () => { void copyText(noteMarkdown(pattern), copy); });
    body.append(copy);
    details.append(body);
    item.append(details);
    return item;
  }

  function render() {
    const list = $("#patterns-list");
    const byId = new Map(patterns.map((pattern) => [pattern.id, pattern]));
    const shown = patterns.filter(matches);
    list.replaceChildren(...shown.map((pattern) => renderCard(pattern, byId)));
    $("#patterns-status").textContent = shown.length ? `共 ${shown.length} 个套路` : "没有找到相关套路，换个词试试。";
  }

  function renderCategories() {
    const box = $("#patterns-cats");
    const names = [...new Set(patterns.map((pattern) => pattern.category))];
    const make = (name, label) => {
      const button = node("button", "patterns-cat", label);
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
      patterns = (await response.json()).sort((a, b) => LEVEL_ORDER.indexOf(a.level) - LEVEL_ORDER.indexOf(b.level));
      renderCategories();
      render();
    } catch {
      $("#patterns-status").textContent = "套路库加载失败，请刷新页面重试。";
    }
  }

  $("#patterns-q").addEventListener("input", (event) => { query = event.target.value; render(); });
  void load();
})();
