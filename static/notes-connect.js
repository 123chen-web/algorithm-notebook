/* notes-connect.js —— 笔记“串联”：把一篇笔记和别的笔记、套路、错题连起来（只读，不新增任务）。
 *
 * 对外：window.NotesConnect = { configure, load, decorate, renderPanel, reset }。
 * - load(noteId)：取 /api/notes/{id}/connections（按笔记缓存，笔记改过后重新取）。
 * - decorate(root, data)：在阅读视图正文里，给提到的笔记标题、套路名加一条虚线下划线，
 *   悬停 / 点一下出预览卡；每个词只标第一次出现，代码、链接、按钮里的不动。
 * - renderPanel(box, data)：正文下面的“串联”面板：这篇提到的笔记、提到这篇的笔记、相关错题、
 *   相关套路、同标签笔记。点一下就跳过去。
 * 用户文字一律 textContent / createElement；预览卡的位置用 CSSOM（style.left/top），不写行内 style 属性。
 */
(function () {
  "use strict";

  let hooks = null;
  let patternsPromise = null;
  const noteCache = new Map();
  let popover = null;
  let hideTimer = 0;
  let showTimer = 0;

  function configure(next) {
    hooks = next;
  }

  function h(tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function button(text, className, onClick) {
    const node = h("button", text, className);
    node.type = "button";
    if (onClick) node.addEventListener("click", onClick);
    return node;
  }

  async function load(noteId) {
    if (!hooks?.api) return null;
    try {
      return await hooks.api(`/api/notes/${noteId}/connections`);
    } catch (_error) {
      return null; // 串联是锦上添花：取不到就不显示，不影响看笔记
    }
  }

  function patterns() {
    if (!patternsPromise) {
      patternsPromise = (hooks?.fetchPatterns ? hooks.fetchPatterns() : Promise.resolve([]))
        .then((list) => new Map((list || []).map((item) => [item.id, item])))
        .catch(() => new Map());
    }
    return patternsPromise;
  }

  /* ---------- 正文里的“提到了” ---------- */
  const SKIP = new Set(["A", "BUTTON", "CODE", "PRE", "SUMMARY", "INPUT", "TEXTAREA"]);

  function textNodes(root) {
    const out = [];
    const walk = (node) => {
      for (const child of [...(node.childNodes || [])]) {
        if (child.nodeType === 3) out.push(child);
        else if (child.nodeType === 1 && !SKIP.has(child.tagName) && !child.classList?.contains("nc-mention")
          && !child.classList?.contains("nl-link")) walk(child);
      }
    };
    walk(root);
    return out;
  }

  /** 正文里的 [[题:…]]：单独占一行的换成一张活的错题卡（题名、错因、下次复习），
      夹在句子里的点一下直接打开那道错题。找不到对应错题的保持原样。 */
  function upgradeProblemLinks(root, data) {
    const byTitle = new Map();
    for (const item of data.mistakes || []) if (!byTitle.has(item.title)) byTitle.set(item.title, item);
    let count = 0;
    for (const anchor of [...root.querySelectorAll('a[data-note-link="problem"]')]) {
      const mistake = byTitle.get(anchor.getAttribute("data-link-target") || "");
      if (!mistake) continue;
      const parent = anchor.parentNode;
      const alone = parent && parent.tagName === "P"
        && [...(parent.childNodes || [])].every((node) => node === anchor || (node.nodeType === 3 && !String(node.nodeValue ?? node.textContent).trim()));
      if (alone) {
        const card = button("", "nc-mistake-card", () => hooks?.openMistake?.(mistake.id));
        card.append(h("span", "错题", "nc-mistake-kind"), h("strong", mistake.title, "nc-mistake-title"));
        const sub = [mistake.zone, tagText(mistake.tags), mistake.suspended ? "已暂停" : dueText(mistake.due_date, hooks?.today ? hooks.today() : "")]
          .filter(Boolean).join(" · ");
        if (sub) card.append(h("span", sub, "nc-mistake-sub"));
        card.setAttribute("aria-label", `打开错题「${mistake.title}」`);
        parent.replaceWith(card);
      } else {
        const inline = button(anchor.textContent, "nc-mention nc-mistake-link", () => hooks?.openMistake?.(mistake.id));
        inline.dataset.kind = "mistake";
        inline.dataset.id = String(mistake.id);
        anchor.replaceWith(inline);
      }
      count += 1;
    }
    return count;
  }

  /** 每个词只在第一次出现处标一次；长词优先，避免“二分”抢了“二分查找”。 */
  function decorate(root, data) {
    if (!root || !data) return 0;
    upgradeProblemLinks(root, data);
    const terms = [];
    for (const note of data.note_mentions || []) terms.push({ text: note.title, kind: "note", id: note.id, label: note.title });
    for (const item of data.patterns || []) terms.push({ text: item.term, kind: "pattern", id: item.id, label: item.title });
    terms.sort((a, b) => b.text.length - a.text.length);
    let count = 0;
    const used = new Set();
    for (const term of terms) {
      if (!term.text || used.has(term.text.toLowerCase())) continue;
      for (const node of textNodes(root)) {
        const value = node.nodeValue || "";
        const at = value.toLowerCase().indexOf(term.text.toLowerCase());
        if (at < 0) continue;
        const before = value.slice(0, at);
        const hit = value.slice(at, at + term.text.length);
        const after = value.slice(at + term.text.length);
        const mention = button(hit, `nc-mention nc-${term.kind}`);
        mention.dataset.kind = term.kind;
        mention.dataset.id = String(term.id);
        mention.setAttribute("aria-label", term.kind === "note" ? `提到了笔记「${term.label}」` : `提到了套路「${term.label}」`);
        const parent = node.parentNode;
        if (!parent) continue;
        if (before) parent.insertBefore(document.createTextNode(before), node);
        parent.insertBefore(mention, node);
        if (after) parent.insertBefore(document.createTextNode(after), node);
        parent.removeChild(node);
        used.add(term.text.toLowerCase());
        count += 1;
        break;
      }
    }
    if (!root.dataset.ncBound) {
      root.dataset.ncBound = "1";
      root.addEventListener("click", (event) => {
        const target = event.target.closest ? event.target.closest(".nc-mention") : null;
        if (!target || target.dataset.kind === "mistake") return; // 错题链接自己处理点击
        event.preventDefault();
        open(target.dataset.kind, target.dataset.id, target);
      });
      root.addEventListener("mouseover", (event) => {
        const target = event.target.closest ? event.target.closest(".nc-mention") : null;
        if (!target) return;
        window.clearTimeout(showTimer);
        showTimer = window.setTimeout(() => void preview(target.dataset.kind, target.dataset.id, target), 350);
      });
      root.addEventListener("mouseout", (event) => {
        if (!(event.target.closest && event.target.closest(".nc-mention"))) return;
        window.clearTimeout(showTimer);
        scheduleHide();
      });
    }
    return count;
  }

  function open(kind, id, anchor) {
    if (kind === "note") hooks?.openNote?.(Number(id));
    else if (kind === "pattern") void preview(kind, id, anchor, { sticky: true });
    else if (kind === "mistake") hooks?.openMistake?.(Number(id));
  }

  /* ---------- 预览卡 ---------- */
  function ensurePopover() {
    if (popover && popover.isConnected) return popover;
    popover = h("div", null, "nc-pop");
    popover.setAttribute("role", "dialog");
    popover.hidden = true;
    popover.addEventListener("mouseenter", () => window.clearTimeout(hideTimer));
    popover.addEventListener("mouseleave", scheduleHide);
    document.body.append(popover);
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") hidePopover(); });
    document.addEventListener("click", (event) => {
      if (popover && !popover.hidden && !popover.contains(event.target)
          && !(event.target.closest && event.target.closest(".nc-mention"))) hidePopover();
    });
    return popover;
  }

  function scheduleHide() {
    window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(hidePopover, 250);
  }

  function hidePopover() {
    if (popover) popover.hidden = true;
  }

  function place(anchor) {
    const pop = ensurePopover();
    const rect = anchor.getBoundingClientRect ? anchor.getBoundingClientRect() : { left: 0, bottom: 0 };
    const width = Math.min(340, (window.innerWidth || 360) - 24);
    const left = Math.max(12, Math.min(rect.left + (window.scrollX || 0), (window.scrollX || 0) + (window.innerWidth || 360) - width - 12));
    pop.style.width = `${width}px`;
    pop.style.left = `${left}px`;
    pop.style.top = `${rect.bottom + (window.scrollY || 0) + 6}px`;
  }

  async function noteById(id) {
    if (!noteCache.has(id)) {
      noteCache.set(id, hooks.api(`/api/notes/${id}`).catch((error) => { noteCache.delete(id); throw error; }));
    }
    return noteCache.get(id);
  }

  async function preview(kind, id, anchor, { sticky = false } = {}) {
    if (!hooks?.api) return;
    const pop = ensurePopover();
    window.clearTimeout(hideTimer);
    const body = [];
    if (kind === "note") {
      let note;
      try { note = await noteById(Number(id)); } catch (_error) { return; }
      const title = hooks.noteTitle ? hooks.noteTitle(note) : note.title;
      const text = hooks.previewText ? hooks.previewText(note, 160) : String(note.content || "").slice(0, 160);
      body.push(h("p", "笔记", "nc-pop-kind"), h("strong", title || "笔记", "nc-pop-title"));
      if (text) body.push(h("p", text, "nc-pop-text"));
      body.push(button("打开这篇", "nc-pop-open", () => { hidePopover(); hooks.openNote?.(Number(id)); }));
    } else if (kind === "pattern") {
      const map = await patterns();
      const item = map.get(id);
      if (!item) return;
      body.push(h("p", `套路 · ${item.category || ""}`, "nc-pop-kind"), h("strong", item.title, "nc-pop-title"));
      if (item.idea) body.push(h("p", item.idea, "nc-pop-text"));
      const signals = (item.signals || []).slice(0, 3);
      if (signals.length) {
        const list = h("ul", null, "nc-pop-signals");
        for (const signal of signals) list.append(h("li", signal));
        body.push(h("p", "看到这些就该想到它：", "nc-pop-kind"), list);
      }
      body.push(button("在学习路线里看", "nc-pop-open", () => { hidePopover(); hooks.openPattern?.(item); }));
    } else {
      return;
    }
    if (!anchor.isConnected) return;
    pop.replaceChildren(...body);
    pop.hidden = false;
    place(anchor);
    if (sticky) pop.querySelector("button")?.focus();
  }

  /* ---------- 串联面板 ---------- */
  function section(title, items) {
    const box = h("section", null, "nc-section");
    box.append(h("h4", `${title}（${items.length}）`, "nc-section-title"));
    const list = h("ul", null, "nc-list");
    for (const item of items) list.append(item);
    box.append(list);
    return box;
  }

  function row(label, sub, onClick, extraClass) {
    const li = h("li");
    const target = button("", `nc-item${extraClass ? ` ${extraClass}` : ""}`, onClick);
    target.append(h("span", label, "nc-item-label"));
    if (sub) target.append(h("span", sub, "nc-item-sub"));
    li.append(target);
    return li;
  }

  // 错因标签存的是固定词表 code（boundary 等），显示时换成中文名。
  function tagText(tags) {
    const label = hooks?.tagLabel || ((tag) => tag);
    return (tags || []).slice(0, 2).map((tag) => label(tag) || tag).join("、");
  }

  function dueText(due, today) {
    if (!due) return "";
    if (today && due <= today) return "今天该复习";
    return `下次复习 ${due}`;
  }

  function renderPanel(box, data) {
    if (!box) return;
    box.replaceChildren();
    if (!data) { box.hidden = true; return; }
    const today = hooks?.today ? hooks.today() : "";
    const sections = [];
    if (data.mistakes?.length) {
      sections.push(section("相关错题", data.mistakes.map((item) => row(
        item.title,
        [tagText(item.tags), item.suspended ? "已暂停" : dueText(item.due_date, today)].filter(Boolean).join(" · "),
        () => hooks?.openMistake?.(item.id), "nc-mistake",
      ))));
    }
    if (data.patterns?.length) {
      sections.push(section("相关套路", data.patterns.map((item) => {
        const li = row(item.title, item.category, null, "nc-pattern-item");
        const target = li.querySelector("button");
        target.addEventListener("click", () => void preview("pattern", item.id, target, { sticky: true }));
        return li;
      })));
    }
    if (data.note_mentions?.length) {
      sections.push(section("这篇提到的笔记", data.note_mentions.map((item) => row(item.title, "", () => hooks?.openNote?.(item.id)))));
    }
    if (data.mentioned_by?.length) {
      sections.push(section("提到这篇的笔记", data.mentioned_by.map((item) => row(item.title, item.snippet, () => hooks?.openNote?.(item.id)))));
    }
    if (data.same_tag?.length) {
      sections.push(section("同标签的笔记", data.same_tag.map((item) => row(item.title, item.shared.map((tag) => `#${tag}`).join(" "), () => hooks?.openNote?.(item.id)))));
    }
    box.hidden = !sections.length;
    if (!sections.length) return;
    box.append(h("h3", "串联", "nc-panel-title"), ...sections);
  }

  function reset() {
    noteCache.clear();
    hidePopover();
  }

  window.NotesConnect = Object.freeze({ configure, load, decorate, renderPanel, reset, forget: (id) => noteCache.delete(id) });
})();
