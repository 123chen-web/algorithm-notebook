"use strict";

/* 考前打印版：把错题按分区整理成一份可以打印（或存成 PDF）的错题本页面。
   对外契约：window.PrintNotebook = { load(), reset() }。
   数据来自 GET /api/mistakes?due_only=false（列表里已带思路、代码、标签）和 GET /api/zones；
   所选分区和内容开关只记在本机浏览器（localStorage，取不到就用默认值）。用户文本一律 textContent。 */
(() => {
  const STORAGE_KEY = "algorithm-notebook-print-options";
  const DEFAULTS = {
    zones: null, scope: "all", tag: "", thinking: true, code: true, tags: true,
    schedule: false, notes: true, pageBreaks: true,
  };
  const $ = (selector) => document.querySelector(selector);

  let items = [];
  let zoneOrder = [];
  let today = "";
  let options = { ...DEFAULTS };
  let generation = 0;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function readOptions() {
    try {
      const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "null");
      if (saved && typeof saved === "object") return { ...DEFAULTS, ...saved };
    } catch {
      // 读不到就用默认值。
    }
    return { ...DEFAULTS };
  }
  function saveOptions() {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(options));
    } catch {
      // 隐私模式等取不到存储时，只是下次不记得选项。
    }
  }

  /* ---------- 过滤与分组 ---------- */
  function selectedZones() {
    const present = [...new Set(items.map((item) => item.zone))];
    const chosen = options.zones ? present.filter((zone) => options.zones.includes(zone)) : present;
    const rank = (zone) => (zoneOrder.indexOf(zone) === -1 ? 99 : zoneOrder.indexOf(zone));
    return chosen.sort((a, b) => rank(a) - rank(b));
  }
  function included() {
    const zones = new Set(selectedZones());
    return items.filter((item) => {
      if (!zones.has(item.zone)) return false;
      if (options.scope === "weak" && !(item.repetitions < 3 || item.due_date <= today)) return false;
      if (options.tag && !(item.tags || []).some((tag) => tag.toLowerCase() === options.tag.toLowerCase())) return false;
      return true;
    });
  }
  function groupProblems(list) {
    const problems = new Map();
    for (const item of list) {
      if (!problems.has(item.problem_id)) problems.set(item.problem_id, { first: item, mistakes: [] });
      problems.get(item.problem_id).mistakes.push(item);
    }
    return [...problems.values()];
  }

  /* ---------- 选项面板 ---------- */
  function checkbox(label, key, hint = "") {
    const wrap = node("label", "print-check");
    const input = node("input");
    input.type = "checkbox";
    input.checked = Boolean(options[key]);
    input.addEventListener("change", () => {
      options[key] = input.checked;
      saveOptions();
      renderSheet();
    });
    wrap.append(input, node("span", "", label));
    if (hint) wrap.append(node("small", "", hint));
    return wrap;
  }

  function renderOptions() {
    const panel = $("#print-options");
    const zones = [...new Set(items.map((item) => item.zone))].sort((a, b) => zoneOrder.indexOf(a) - zoneOrder.indexOf(b));
    const zoneSet = new Set(options.zones || zones);
    const zoneBox = node("fieldset", "print-fieldset");
    zoneBox.append(node("legend", "", "要打印的分区"));
    const all = node("button", "print-link", zoneSet.size === zones.length ? "全部取消" : "全选");
    all.type = "button";
    all.addEventListener("click", () => {
      options.zones = zoneSet.size === zones.length ? [] : [...zones];
      saveOptions();
      renderOptions();
      renderSheet();
    });
    zoneBox.append(all);
    for (const zone of zones) {
      const count = items.filter((item) => item.zone === zone).length;
      const wrap = node("label", "print-check");
      const input = node("input");
      input.type = "checkbox";
      input.checked = zoneSet.has(zone);
      input.addEventListener("change", () => {
        const next = new Set(options.zones || zones);
        if (input.checked) next.add(zone);
        else next.delete(zone);
        options.zones = [...next];
        saveOptions();
        renderOptions();
        renderSheet();
      });
      const dot = node("span", "zone-dot");
      dot.dataset.zone = zone;
      dot.setAttribute("aria-hidden", "true");
      wrap.append(input, dot, node("span", "", zone), node("small", "", `${count} 条`));
      zoneBox.append(wrap);
    }

    const scopeBox = node("fieldset", "print-fieldset");
    scopeBox.append(node("legend", "", "范围"));
    for (const [value, label] of [["all", "全部易错点"], ["weak", "只要还没记牢的（复习不满 3 次，或已到期）"]]) {
      const wrap = node("label", "print-check");
      const input = node("input");
      input.type = "radio";
      input.name = "print-scope";
      input.value = value;
      input.checked = options.scope === value;
      input.addEventListener("change", () => {
        options.scope = value;
        saveOptions();
        renderSheet();
      });
      wrap.append(input, node("span", "", label));
      scopeBox.append(wrap);
    }
    const tags = window.TagFilters?.tags?.() || [];
    if (tags.length) {
      const tagWrap = node("label", "print-select");
      tagWrap.append("只要带这个标签的");
      const select = node("select");
      const everything = node("option", "", "不限标签");
      everything.value = "";
      select.append(everything);
      for (const entry of tags) {
        const option = node("option", "", `${entry.tag} · ${entry.count}`);
        option.value = entry.tag;
        select.append(option);
      }
      select.value = options.tag;
      select.addEventListener("change", () => {
        options.tag = select.value;
        saveOptions();
        renderSheet();
      });
      tagWrap.append(select);
      scopeBox.append(tagWrap);
    }

    const contentBox = node("fieldset", "print-fieldset");
    contentBox.append(node("legend", "", "每题带上"));
    contentBox.append(
      checkbox("当时的思路", "thinking"),
      checkbox("当时的代码", "code"),
      checkbox("错因标签", "tags"),
      checkbox("复习情况", "schedule", "次数和下次复习日"),
      checkbox("留几行空白写笔记", "notes"),
      checkbox("每个分区另起一页", "pageBreaks"),
    );
    panel.replaceChildren(zoneBox, scopeBox, contentBox);
  }

  /* ---------- 打印页 ---------- */
  function mistakeLine(item) {
    const line = node("li", "print-mistake");
    line.append(node("span", "print-box", "☐"), node("span", "print-mistake-text", item.description || "（错因待补充）"));
    if (options.tags && item.tags?.length) {
      line.append(node("span", "print-tags", item.tags.map((tag) => `【${tag}】`).join("")));
    }
    if (options.schedule) {
      line.append(node("span", "print-schedule", `复习 ${item.repetitions} 次 · 下次 ${item.due_date.slice(5)}`));
    }
    return line;
  }

  function problemBlock(problem) {
    const first = problem.first;
    const block = node("article", "print-problem");
    const head = node("header", "print-problem-head");
    head.append(node("h4", "", first.title));
    if (first.language) head.append(node("span", "print-meta", first.language));
    block.append(head);
    const list = node("ol", "print-mistakes");
    for (const item of problem.mistakes) list.append(mistakeLine(item));
    block.append(list);
    if (options.thinking && first.thinking) {
      const section = node("section", "print-block");
      section.append(node("h5", "", "当时的思路"), node("p", "multiline", first.thinking));
      block.append(section);
    }
    if (options.code && first.code) {
      const section = node("section", "print-block");
      section.append(node("h5", "", "当时的代码"));
      const pre = node("pre", "print-code");
      pre.textContent = first.code;
      section.append(pre);
      block.append(section);
    }
    if (options.notes) {
      const notes = node("div", "print-notes");
      notes.setAttribute("aria-hidden", "true");
      notes.append(node("span", "print-notes-label", "我的笔记"));
      for (let line = 0; line < 3; line += 1) notes.append(node("span", "print-line"));
      block.append(notes);
    }
    return block;
  }

  function renderSheet() {
    const sheet = $("#print-sheet");
    const list = included();
    const zones = selectedZones().filter((zone) => list.some((item) => item.zone === zone));
    const problemCount = groupProblems(list).length;
    const summary = list.length
      ? `已整理 ${zones.length} 个分区、${problemCount} 道题、${list.length} 条易错点。`
      : "按现在的选择没有可打印的内容，换个分区或范围试试。";
    $("#print-summary").textContent = summary;
    const go = $("#print-go");
    go.disabled = list.length === 0;
    go.dataset.blocked = list.length === 0 ? "1" : "0"; // 全局"忙碌"状态结束时不要把它误开
    if (!list.length) {
      sheet.replaceChildren(node("p", "print-empty", summary));
      return;
    }
    const cover = node("header", "print-cover");
    cover.append(node("h2", "", "我的错题本 · 考前版"));
    const username = $("#account-summary-name")?.textContent || "";
    cover.append(node("p", "print-cover-meta", `${username ? `${username} · ` : ""}${today} · ${problemCount} 道题 · ${list.length} 条易错点`));
    const toc = node("ul", "print-toc");
    for (const zone of zones) {
      const inZone = list.filter((item) => item.zone === zone);
      toc.append(node("li", "", `${zone}：${groupProblems(inZone).length} 题 / ${inZone.length} 条`));
    }
    cover.append(toc);
    sheet.replaceChildren(cover);
    zones.forEach((zone, index) => {
      const inZone = list.filter((item) => item.zone === zone);
      const section = node("section", "print-zone");
      if (options.pageBreaks && index > 0) section.classList.add("is-new-page");
      const heading = node("h3", "print-zone-title", zone);
      heading.append(node("small", "", ` ${groupProblems(inZone).length} 题 · ${inZone.length} 条`));
      section.append(heading);
      for (const problem of groupProblems(inZone)) section.append(problemBlock(problem));
      sheet.append(section);
    });
  }

  /* ---------- 页面 ---------- */
  function setStatus(text, { retry = false } = {}) {
    $("#print-status").textContent = text;
    $("#print-retry").hidden = !retry;
  }

  async function load() {
    const ticket = ++generation;
    const page = $("#print-page");
    page.setAttribute("aria-busy", "true");
    setStatus("正在整理你的错题…");
    try {
      const [mistakes, zoneData] = await Promise.all([
        fetch("/api/mistakes?due_only=false", { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } }),
        fetch("/api/zones", { credentials: "same-origin" }),
      ]);
      if (!mistakes.ok || !zoneData.ok) throw new Error("load failed");
      const list = await mistakes.json();
      const zones = await zoneData.json();
      if (ticket !== generation) return;
      items = list.items;
      today = list.today;
      zoneOrder = zones.zones;
      options = readOptions();
      await window.TagFilters?.refresh(); // 标签下拉要用最新的标签
      if (ticket !== generation) return;
      // 记住的选项可能来自上一个账号或已经删掉的标签：对不上就回到"不限"，否则会一直过滤、又没有控件可解除。
      const present = new Set(items.map((item) => item.zone));
      if (options.zones && !options.zones.some((zone) => present.has(zone))) options.zones = null;
      const knownTags = (window.TagFilters?.tags?.() || []).map((entry) => entry.tag.toLowerCase());
      if (options.tag && !knownTags.includes(options.tag.toLowerCase())) options.tag = "";
      if (!items.length) {
        $("#print-body").hidden = true;
        setStatus("还没有可打印的记录。先去「新增记录」留下几条易错点吧。");
        return;
      }
      setStatus("");
      $("#print-body").hidden = false;
      renderOptions();
      renderSheet();
    } catch {
      if (ticket !== generation) return;
      $("#print-body").hidden = true;
      setStatus("暂时无法读取记录，请稍后重试。", { retry: true });
    } finally {
      if (ticket === generation) page.setAttribute("aria-busy", "false");
    }
  }

  function reset() {
    generation += 1;
    items = [];
    $("#print-sheet").replaceChildren();
    $("#print-options").replaceChildren();
    $("#print-body").hidden = true;
    setStatus("");
    $("#print-summary").textContent = "";
    $("#print-go").disabled = true;
    $("#print-go").dataset.blocked = "1";
  }

  $("#print-go").addEventListener("click", () => window.print());
  $("#print-retry").addEventListener("click", () => load());

  window.PrintNotebook = { load, reset };
})();
