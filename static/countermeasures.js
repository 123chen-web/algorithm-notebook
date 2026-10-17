"use strict";

/* “我给自己定的对策”：复习一条易错点时，把自己在“我的老毛病：错因”笔记里写的对策串回来。
   对外：window.Countermeasures = { mount(host, item, { api, isCurrent, openNote }) → Promise, renderList(items, openNote) → 元素 }。
   - 数据：GET /api/mistakes/{id}/countermeasures（按这条的错因标签找对应的老毛病笔记）；
   - 没写过对策就什么也不显示；只是看，不打分、不提醒；
   - 用户文本一律 textContent。 */
(() => {
  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }

  /** 一组对策（每个错因一块）：标题“「错因」”、几行对策、一个“打开笔记”。 */
  function renderList(items, openNote) {
    const box = node("section", "cm-box");
    box.setAttribute("aria-label", "我给自己定的对策");
    box.append(node("h3", "cm-title", "我给自己定的对策"));
    for (const item of items) {
      const block = node("div", "cm-block");
      const head = node("p", "cm-label");
      head.append(document.createTextNode(`「${item.label}」`));
      if (typeof openNote === "function") {
        const open = node("button", "cm-open", "打开笔记");
        open.type = "button";
        open.addEventListener("click", () => openNote(item.note_id));
        head.append(" ", open);
      }
      const list = node("ul", "cm-lines");
      for (const line of item.lines || []) list.append(node("li", "", line));
      block.append(head, list);
      box.append(block);
    }
    return box;
  }

  async function mount(host, item, hooks = {}) {
    if (!host || !item || item.id === undefined || typeof hooks.api !== "function") return;
    host.replaceChildren();
    let data = null;
    try {
      data = await hooks.api(`/api/mistakes/${encodeURIComponent(item.id)}/countermeasures`);
    } catch {
      return; // 取不到就不显示，不打扰复习
    }
    if (typeof hooks.isCurrent === "function" && !hooks.isCurrent()) return;
    const items = Array.isArray(data?.items) ? data.items.filter((entry) => entry.lines?.length) : [];
    if (!items.length) return;
    host.append(renderList(items, hooks.openNote));
  }

  window.Countermeasures = Object.freeze({ mount, renderList });
})();
