/* related-notes.js —— 复习某道错题时，顺带看到自己写过的相关笔记（只读，不新增任务）。
 *
 * 对外：window.RelatedNotes = { mount(host, item, hooks) }。
 * hooks：{ api, openNote(id), isCurrent() }。
 * 取 /api/problems/{problem_id}/related-notes：关联这道题的、[[题:…]] 链接到它的、正文提到题名的、写同一个套路的。
 * 折叠成一行“我写过的相关笔记（N）”，点开才看；一条都没有就整块不显示。用户文字一律 textContent。
 */
(function () {
  "use strict";

  function h(tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  async function mount(host, item, hooks) {
    if (!host || !item || !item.problem_id || !hooks?.api) return;
    host.hidden = true;
    let data;
    try {
      data = await hooks.api(`/api/problems/${item.problem_id}/related-notes`);
    } catch (_error) {
      return; // 锦上添花：取不到就不显示
    }
    if (hooks.isCurrent && !hooks.isCurrent()) return;
    const items = (data && data.items) || [];
    if (!items.length) return;
    const fold = h("details", null, "related-notes");
    fold.append(h("summary", `我写过的相关笔记（${items.length}）`));
    const list = h("ul", null, "related-notes-list");
    for (const note of items) {
      const li = h("li");
      const open = h("button", null, "related-note");
      open.type = "button";
      open.append(h("span", note.title || "（无标题）", "related-note-title"), h("span", note.reason, "related-note-reason"));
      if (note.preview) open.append(h("span", note.preview, "related-note-preview"));
      open.addEventListener("click", () => hooks.openNote?.(note.id));
      li.append(open);
      list.append(li);
    }
    fold.append(list);
    host.replaceChildren(fold);
    host.hidden = false;
  }

  window.RelatedNotes = Object.freeze({ mount });
})();
