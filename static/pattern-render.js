"use strict";

/* 套路卡片的渲染逻辑：独立页（patterns.js）和主站「学习路线」（roadmap.js）共用同一份，
   不复制两份。这里是纯渲染 + 纯函数：不 fetch、不读本地存储、不碰页面上的固定节点
   （只在被调用时按传入的数据建 DOM），所有文本一律 textContent。
   对外契约见文件末尾的 window.PatternRender。 */
(() => {
  const LEVEL_ORDER = ["入门", "进阶", "竞赛"];
  const STATUS_ORDER = ["none", "learning", "mastered"];
  const STATUS_LABELS = { none: "未学", learning: "学习中", mastered: "已掌握" };
  const SOURCE_NAMES = { "leetcode-cn": "力扣", luogu: "洛谷", codeforces: "Codeforces" };
  // 洛谷能按题号拼出确定的题目地址；力扣的题目地址用 slug、题号拼不出来，
  // 所以链到按题号搜索的页面，不去猜 slug。
  const SOURCE_LINKS = {
    luogu: {
      text: "去洛谷看题",
      url: (id) => `https://www.luogu.com.cn/problem/${encodeURIComponent(id)}`,
    },
    "leetcode-cn": {
      text: "去力扣搜索",
      url: (id) => `https://leetcode.cn/problemset/?search=${encodeURIComponent(id)}`,
    },
  };

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function exampleKey(example) {
    return `${example.source}:${example.id}`;
  }

  function sourceLabel(example) {
    return SOURCE_NAMES[example.source] || example.source;
  }

  function searchText(pattern) {
    return [
      pattern.title, pattern.category, pattern.level, pattern.idea, pattern.complexity,
      ...(pattern.signals || []), ...(pattern.pitfalls || []), pattern.confusions || "",
      ...(pattern.examples || []).map((example) => `${example.id} ${example.title} ${sourceLabel(example)}`),
    ].join("\n").toLowerCase();
  }

  /** 分类 / 难度 / 关键词三道筛选；status 由调用方另有状态表，不在这里比。 */
  function matches(pattern, { category = "", level = "", query = "" } = {}) {
    if (category && pattern.category !== category) return false;
    if (level && pattern.level !== level) return false;
    const needle = String(query || "").trim().toLowerCase();
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

  function copyButton(label, className, getText) {
    const button = node("button", className, label);
    button.type = "button";
    button.addEventListener("click", () => { void copyText(getText(), button); });
    return button;
  }

  /* ---------- 模板：Python / C++ 标签页 ---------- */

  function templates(pattern) {
    const wrap = node("div", "pattern-codes");
    const tabs = node("div", "pattern-tabs");
    tabs.setAttribute("role", "group");
    tabs.setAttribute("aria-label", `${pattern.title} 的模板语言`);
    const panes = { python: null, cpp: null };
    const buttons = {};
    const make = (lang, label, code) => {
      const button = node("button", "pattern-tab", label);
      button.type = "button";
      button.dataset.lang = lang;
      const pane = node("div", "pattern-pane");
      pane.dataset.lang = lang;
      pane.append(codeBlock(code), copyButton("复制这段代码", "pattern-copy-code", () => code));
      panes[lang] = pane;
      buttons[lang] = button;
      button.addEventListener("click", () => show(lang));
      return button;
    };
    function show(lang) {
      for (const key of Object.keys(panes)) {
        panes[key].hidden = key !== lang;
        buttons[key].setAttribute("aria-pressed", String(key === lang));
      }
    }
    tabs.append(
      make("python", "Python", pattern.template_python || ""),
      make("cpp", "C++", pattern.template_cpp || ""),
    );
    wrap.append(tabs, panes.python, panes.cpp);
    show("python");
    return wrap;
  }

  /* ---------- 经典例题：勾选框 + 能确定的才给链接 ---------- */

  function examplesList(pattern, { doneKeys = new Set(), onExample = null, onChange = null } = {}) {
    const list = node("ul", "pattern-examples");
    const done = new Set(doneKeys);
    const items = pattern.examples || [];

    const allDone = () => items.length > 0 && items.every((example) => done.has(exampleKey(example)));
    const notify = () => { if (onChange) onChange(allDone()); };

    for (const example of items) {
      const key = exampleKey(example);
      const row = node("li", "pattern-example");
      const label = node("label", "pattern-example-label");
      const box = node("input", "pattern-example-box");
      box.type = "checkbox";
      box.checked = done.has(key);
      const text = node("span", "pattern-example-text",
        `${sourceLabel(example)} ${example.id}　${example.title}`);
      label.append(box, text);
      row.append(label);
      const build = SOURCE_LINKS[example.source];
      if (build) {
        const link = node("a", "pattern-example-link", build.text);
        link.setAttribute("href", build.url(example.id));
        link.setAttribute("target", "_blank");
        link.setAttribute("rel", "noopener noreferrer");
        row.append(link);
      }
      box.addEventListener("change", async () => {
        const next = box.checked;
        if (!onExample) {
          if (next) done.add(key); else done.delete(key);
          notify();
          return;
        }
        box.disabled = true;
        const ok = await onExample(pattern.id, key, next);
        box.disabled = false;
        if (!ok) {
          box.checked = !next; // 请求失败：静默回滚，不弹错误打断阅读
          return;
        }
        if (next) done.add(key); else done.delete(key);
        notify();
      });
      list.append(row);
    }
    return { list, allDone: allDone() };
  }

  /* ---------- 三态按钮组 ---------- */

  function statusGroup(pattern, status, onStatus, onChanged = null) {
    const group = node("div", "pattern-states");
    group.setAttribute("role", "group");
    group.setAttribute("aria-label", `${pattern.title} 的学习状态`);
    let value = STATUS_ORDER.includes(status) ? status : "none";
    const buttons = STATUS_ORDER.map((key) => {
      const button = node("button", "pattern-state", STATUS_LABELS[key]);
      button.type = "button";
      button.dataset.status = key;
      button.setAttribute("aria-pressed", String(key === value));
      button.addEventListener("click", () => { void submit(key); });
      return button;
    });
    function paint(next) {
      for (const button of buttons) {
        button.setAttribute("aria-pressed", String(button.dataset.status === next));
      }
      group.dataset.status = next;
      if (onChanged) onChanged(next);
    }
    async function submit(next) {
      if (next === value) return;
      const previous = value;
      paint(next);
      value = next;
      if (!onStatus) return;
      const ok = await onStatus(pattern.id, next);
      if (!ok) {
        value = previous;   // 离线 / 请求失败：按钮回到原来的状态
        paint(previous);
      }
    }
    paint(value);
    group.append(...buttons);
    return { group, get value() { return value; }, paint };
  }

  /* ---------- 整张卡片 ---------- */

  function renderCard(pattern, options = {}) {
    const {
      byId = new Map(), status = "none", doneKeys = new Set(), mistakeCount = 0,
      onStatus = null, onExample = null, onJumpRelated = null, onMistakeClick = null,
      withStatus = Boolean(onStatus),
    } = options;

    const item = node("li", "pattern-card");
    item.dataset.patternId = pattern.id;
    item.dataset.level = pattern.level;

    const details = node("details", "pattern-details");
    const summary = node("summary", "pattern-summary");
    summary.append(
      node("span", "pattern-title", pattern.title),
      node("span", "pattern-chip", pattern.category),
      node("span", "pattern-chip pattern-level", pattern.level),
    );
    if (mistakeCount > 0) {
      if (onMistakeClick) {
        const badge = node("button", "pattern-chip pattern-mistakes", `你记过 ${mistakeCount} 道错题`);
        badge.type = "button";
        badge.setAttribute("aria-label", `查看《${pattern.title}》的分类结果`);
        badge.addEventListener("click", (event) => {
          event.stopPropagation();
          onMistakeClick(pattern);
        });
        summary.append(badge);
      } else {
        summary.append(node("span", "pattern-chip pattern-mistakes", `你记过 ${mistakeCount} 道错题`));
      }
    }
    details.append(summary);

    const body = node("div", "pattern-body");
    body.append(node("p", "pattern-idea", pattern.idea));
    body.append(section("什么时候该想到它", bulletList(pattern.signals)));
    body.append(section("复杂度", node("p", "", pattern.complexity)));
    body.append(section("常见坑", bulletList(pattern.pitfalls)));
    if (pattern.confusions) body.append(section("容易混淆", node("p", "", pattern.confusions)));
    if (pattern.sample_input && pattern.sample_output) {
      const sample = node("div", "pattern-sample");
      sample.append(
        node("h5", "", "输入"), codeBlock(pattern.sample_input),
        node("h5", "", "输出"), codeBlock(pattern.sample_output),
      );
      body.append(section("样例（两份模板都能跑出这个结果）", sample));
    }
    body.append(section("模板代码", templates(pattern)));

    let hint = null;
    const { list } = examplesList(pattern, {
      doneKeys, onExample,
      onChange: (allDone) => {
        if (!hint) return;
        // 只提示，不自动改状态。
        hint.hidden = !allDone || status === "mastered";
      },
    });
    body.append(section("经典例题（勾掉你做过的）", list));

    const related = (pattern.related || []).map((id) => byId.get(id)).filter(Boolean);
    if (related.length) {
      const box = node("div", "pattern-related");
      box.append(node("span", "pattern-related-label", "相关套路："));
      for (const entry of related) {
        const link = node("button", "pattern-related-link", entry.title);
        link.type = "button";
        link.dataset.goto = entry.id;
        link.addEventListener("click", () => { if (onJumpRelated) onJumpRelated(entry.id); });
        box.append(link);
      }
      body.append(box);
    }

    const copy = copyButton("复制为笔记模板", "pattern-copy", () => noteMarkdown(pattern));
    body.append(copy);
    details.append(body);
    item.append(details);

    if (withStatus) {
      const statusRow = node("div", "pattern-card-actions");
      const group = statusGroup(pattern, status, onStatus, (value) => {
        item.dataset.status = value;
        // 勾完例题的提示只在"还没掌握"时出现；改成已掌握就收起来。
        if (hint) hint.hidden = hint.hidden || value === "mastered";
      });
      statusRow.append(group.group);
      hint = node("p", "pattern-master-hint", "例题都勾完了，要不要把状态改成「已掌握」？");
      hint.setAttribute("role", "status");
      const all = (pattern.examples || []).length > 0
        && (pattern.examples || []).every((example) => doneKeys.has(exampleKey(example)));
      hint.hidden = !all || status === "mastered";
      statusRow.append(hint);
      item.append(statusRow);
    } else {
      item.dataset.status = "none";
    }
    return item;
  }

  window.PatternRender = {
    LEVEL_ORDER, STATUS_ORDER, STATUS_LABELS, SOURCE_NAMES,
    node, exampleKey, searchText, matches, renderCard, templates,
    examplesList, statusGroup, noteMarkdown, copyText,
  };
})();
