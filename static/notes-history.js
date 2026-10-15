/* notes-history.js —— 笔记的“不丢字”层：自动保存、本机草稿、历史版本、最近删除。
 *
 * 对外：window.NotesHistory = { configure, drafts, autosave, openHistory, openTrash, hasUnsaved }。
 * - autosave：编辑已有笔记时，停手 1.5 秒自动保存；每次都带上 base_updated_at，别处改过就停下来问。
 * - drafts：新建区和编辑中的内容随手存进本机（localStorage，按用户 id 分开），存不了就算了，绝不抛错。
 * - openHistory / openTrash：用同一个 <dialog id="notes-history-dialog"> 展示历史版本 / 最近删除。
 * 用户文字一律 textContent；样式全在 notes-history.css。
 */
(function () {
  "use strict";

  const SAVE_DELAY = 1500;
  const RETRY_DELAY = 10000;
  const NOTE_CONTENT_MAX = 20000;
  let hooks = null;
  const active = new Set();

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

  function clock(iso) {
    const date = new Date(iso || Date.now());
    if (Number.isNaN(date.getTime())) return "";
    return `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  }

  function when(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "";
    const now = new Date();
    const sameDay = date.toDateString() === now.toDateString();
    const day = sameDay ? "今天" : `${date.getMonth() + 1} 月 ${date.getDate()} 日`;
    const year = date.getFullYear() === now.getFullYear() ? "" : `${date.getFullYear()} 年 `;
    return `${year}${day} ${clock(iso)}`;
  }

  /* ---------- 本机草稿 ---------- */
  function draftKey(suffix) {
    const id = hooks?.getUser?.()?.id;
    return id == null ? null : `oy:notes:${id}:${suffix}`;
  }

  const drafts = {
    read(suffix) {
      const key = draftKey(suffix);
      if (!key) return null;
      try {
        const raw = window.localStorage.getItem(key);
        const value = raw ? JSON.parse(raw) : null;
        return value && typeof value === "object" ? value : null;
      } catch (_error) {
        return null;
      }
    },
    write(suffix, value) {
      const key = draftKey(suffix);
      if (!key) return;
      try {
        window.localStorage.setItem(key, JSON.stringify({ ...value, at: new Date().toISOString() }));
      } catch (_error) {
        /* 隐私模式 / 配额满：这台设备上不留草稿，服务器保存不受影响 */
      }
    },
    clear(suffix) {
      const key = draftKey(suffix);
      if (!key) return;
      try {
        window.localStorage.removeItem(key);
      } catch (_error) {
        /* 同上 */
      }
    },
  };

  /* ---------- 自动保存 ---------- */
  /**
   * note：正在编辑的笔记（要有 id、updated_at）；read() 返回 { title, content, tags }；
   * bar：放保存状态和冲突按钮的容器；onSaved(updated)：每次保存成功；onTheirs(latest)：冲突时选了“看最新的”。
   */
  function autosave({ note, read, bar, onSaved, onTheirs }) {
    const key = `edit:${note.id}`;
    const serialize = (value) => JSON.stringify([value.title || "", value.content || "", value.tags || []]);
    const original = { title: note.title || "", content: note.content || "", tags: [...(note.tags || [])] };
    let base = note.updated_at;
    let lastSaved = serialize(original);
    let timer = 0;
    let saving = null;
    let again = false;
    let conflict = false;
    let everSaved = false;
    let stopped = false;

    const text = h("span", "改动会自动保存", "notes-save-text");
    text.setAttribute("role", "status");
    text.setAttribute("aria-live", "polite");
    const extra = h("span", null, "notes-save-actions");
    bar.replaceChildren(text, extra);

    function status(message, state, actions = []) {
      text.textContent = message;
      bar.dataset.state = state;
      extra.replaceChildren(...actions);
    }

    function dirty() {
      return serialize(read()) !== lastSaved;
    }

    function schedule() {
      if (stopped || conflict || !dirty()) return;
      drafts.write(key, { ...read(), base });
      status("有改动，停手后自动保存…", "pending");
      window.clearTimeout(timer);
      timer = window.setTimeout(() => void flush(), SAVE_DELAY);
    }

    async function send(value, extraBody = {}) {
      return hooks.api(`/api/notes/${note.id}`, {
        method: "PUT",
        body: JSON.stringify({
          title: (value.title || "").trim(), content: value.content, tags: value.tags,
          base_updated_at: base, ...extraBody,
        }),
      });
    }

    async function flush(extraBody = {}) {
      window.clearTimeout(timer);
      timer = 0;
      if (conflict) return false;
      if (saving) {
        again = true;
        return saving;
      }
      const value = read();
      const serialized = serialize(value);
      if (serialized === lastSaved && !extraBody.snapshot) return true;
      if (!String(value.content || "").trim()) {
        status("正文是空的，先不保存（至少写点什么）", "error");
        return false;
      }
      if (String(value.content).length > NOTE_CONTENT_MAX) {
        status(`正文超过 ${NOTE_CONTENT_MAX} 字，没法保存，请精简一些`, "error");
        return false;
      }
      status("正在保存…", "saving");
      saving = (async () => {
        try {
          const updated = await send(value, extraBody);
          base = updated.updated_at;
          lastSaved = serialized;
          everSaved = true;
          if (dirty()) drafts.write(key, { ...read(), base });
          else drafts.clear(key);
          status(`已自动保存 ${clock(updated.updated_at)}`, "saved");
          onSaved?.(updated);
          return true;
        } catch (error) {
          if (error?.status === 409) {
            conflict = true;
            showConflict();
            return false;
          }
          status(`没保存上：${error?.message || "网络有问题"}。改动先存在这台设备上，稍后自动重试`, "error",
            [button("现在重试", "notes-save-retry", () => void flush())]);
          window.clearTimeout(timer);
          timer = window.setTimeout(() => void flush(), RETRY_DELAY);
          return false;
        } finally {
          saving = null;
          if (again && !conflict) {
            again = false;
            void flush();
          }
        }
      })();
      return saving;
    }

    async function latest() {
      return hooks.api(`/api/notes/${note.id}`);
    }

    function showConflict() {
      status("这条笔记在别处（另一个窗口或设备）改过了，你这边的改动还没保存。", "conflict", [
        button("保留我的（对方版本存进历史）", "notes-save-mine", async () => {
          try {
            const fresh = await latest();
            base = fresh.updated_at;
            conflict = false;
            await flush({ snapshot: true });
          } catch (error) {
            status(`没能读取最新内容：${error?.message || "网络有问题"}`, "conflict", extra.childNodes.length ? [...extra.childNodes] : []);
          }
        }),
        button("看最新的（放弃我这次的改动）", "notes-save-theirs", async () => {
          try {
            const fresh = await latest();
            drafts.clear(key);
            stop();
            onTheirs?.(fresh);
          } catch (error) {
            status(`没能读取最新内容：${error?.message || "网络有问题"}`, "conflict", [...extra.childNodes]);
          }
        }),
      ]);
    }

    /** 放弃本次修改：已经自动保存过就把打开时的内容写回去（当前内容先进历史）。 */
    async function revert() {
      window.clearTimeout(timer);
      if (!everSaved) {
        drafts.clear(key);
        return null;
      }
      const updated = await hooks.api(`/api/notes/${note.id}`, {
        method: "PUT",
        body: JSON.stringify({ ...original, snapshot: true }),
      });
      drafts.clear(key);
      return updated;
    }

    function stop() {
      stopped = true;
      window.clearTimeout(timer);
      active.delete(controller);
    }

    const controller = {
      schedule, flush, revert, stop, dirty,
      get conflict() { return conflict; },
      get everSaved() { return everSaved; },
      /** 别的操作（如置顶）改了服务器上的 updated_at 但没动正文：跟上新版本号，免得下次保存误报冲突。 */
      rebase(updatedAt) {
        if (updatedAt) base = updatedAt;
      },
      pendingDraft() {
        const saved = drafts.read(key);
        if (!saved || typeof saved.content !== "string") return null;
        if (saved.base !== note.updated_at) return null; // 之后服务器上又改过：旧草稿不再适用
        if (serialize(saved) === lastSaved) return null;
        return saved;
      },
      discardDraft() { drafts.clear(key); },
    };
    active.add(controller);
    return controller;
  }

  function hasUnsaved() {
    for (const controller of active) if (controller.dirty()) return true;
    return false;
  }

  window.addEventListener("beforeunload", (event) => {
    if (!hasUnsaved()) return;
    for (const controller of active) void controller.flush();
    event.preventDefault();
    event.returnValue = "";
  });

  /* ---------- 对话框：历史版本 / 最近删除 ---------- */
  function dialog() {
    return document.getElementById("notes-history-dialog");
  }

  function openDialog(title) {
    const node = dialog();
    if (!node) return null;
    const heading = h("h3", title, "nh-title");
    heading.id = "notes-history-title";
    const close = button("×", "nh-close", () => node.close());
    close.setAttribute("aria-label", "关闭");
    const body = h("div", null, "nh-body");
    const head = h("div", null, "nh-head");
    head.append(heading, close);
    node.replaceChildren(head, body);
    if (!node.open) {
      if (typeof node.showModal === "function") node.showModal();
      else node.setAttribute("open", "");
    }
    return body;
  }

  function errorText(error) {
    return error?.message || "网络有问题，请稍后重试";
  }

  async function openHistory(note, { onRestored } = {}) {
    const body = openDialog(`历史版本 · ${hooks?.noteTitle ? hooks.noteTitle(note) : note.title || "笔记"}`);
    if (!body) return;
    const list = h("ol", null, "nh-list");
    const preview = h("div", null, "nh-preview");
    preview.append(h("p", "选一个版本，在这里预览。", "nh-muted"));
    const status = h("p", "正在读取…", "nh-muted");
    status.setAttribute("role", "status");
    body.append(status, h("div", null, "nh-split"));
    body.lastChild.append(list, preview);
    let data;
    try {
      data = await hooks.api(`/api/notes/${note.id}/revisions`);
    } catch (error) {
      status.textContent = errorText(error);
      return;
    }
    if (!data.items.length) {
      status.textContent = "还没有历史版本。改过一次正文、标题或标签后，这里会留下改之前的样子；连续编辑时每 10 分钟留一份，每条笔记最多 50 份。";
      body.lastChild.hidden = true;
      return;
    }
    status.textContent = `共 ${data.items.length} 个版本，时间是那一版最后保存的时间。`;
    for (const item of data.items) {
      const entry = h("li");
      const pick = button("", "nh-item", () => void show(item, pick));
      pick.append(h("span", when(item.saved_at), "nh-item-time"), h("span", item.preview || "（空）", "nh-item-preview"));
      entry.append(pick);
      list.append(entry);
    }
    async function show(item, pick) {
      for (const other of list.querySelectorAll(".nh-item")) other.removeAttribute("aria-current");
      pick.setAttribute("aria-current", "true");
      preview.replaceChildren(h("p", "正在读取这个版本…", "nh-muted"));
      let detail;
      try {
        detail = await hooks.api(`/api/notes/${note.id}/revisions/${item.id}`);
      } catch (error) {
        preview.replaceChildren(h("p", errorText(error), "nh-error"));
        return;
      }
      const restore = button("恢复这个版本", "primary nh-restore", async () => {
        restore.disabled = true;
        try {
          const updated = await hooks.api(`/api/notes/${note.id}/revisions/${item.id}/restore`, { method: "POST" });
          dialog()?.close();
          onRestored?.(updated);
          hooks?.notify?.(`已恢复到 ${when(item.saved_at)} 的版本，恢复前的内容也存进了历史。`);
        } catch (error) {
          restore.disabled = false;
          preview.append(h("p", errorText(error), "nh-error"));
        }
      });
      const rendered = h("div", null, "nh-rendered notes-rich");
      rendered.append(hooks?.renderMarkdown ? hooks.renderMarkdown(detail.content) : h("pre", detail.content));
      const meta = h("p", `${when(detail.saved_at)}${detail.title ? ` · 标题：${detail.title}` : ""}${detail.tags.length ? ` · 标签：${detail.tags.join("、")}` : ""}`, "nh-muted");
      preview.replaceChildren(meta, rendered, h("div", null, "nh-actions"));
      preview.lastChild.append(restore);
    }
  }

  async function openTrash({ onRestored } = {}) {
    const body = openDialog("最近删除");
    if (!body) return;
    const status = h("p", "正在读取…", "nh-muted");
    status.setAttribute("role", "status");
    const list = h("ul", null, "nh-trash");
    body.append(status, list);
    let data;
    try {
      data = await hooks.api("/api/notes/trash");
    } catch (error) {
      status.textContent = errorText(error);
      return;
    }
    function render() {
      status.textContent = data.items.length
        ? "删掉的笔记都在这里，可以恢复；彻底删除后就找不回来了。"
        : "回收站是空的。";
      list.replaceChildren(...data.items.map((item) => {
        const row = h("li", null, "nh-trash-item");
        const text = h("div", null, "nh-trash-text");
        text.append(h("strong", item.title || "（无标题）"), h("span", item.preview, "nh-item-preview"),
          h("span", `删除于 ${when(item.deleted_at)}`, "nh-muted"));
        const actions = h("div", null, "nh-trash-actions");
        const restore = button("恢复", "nh-trash-restore", async () => {
          restore.disabled = true;
          try {
            const note = await hooks.api(`/api/notes/${item.id}/restore`, { method: "POST" });
            data.items = data.items.filter((other) => other.id !== item.id);
            render();
            onRestored?.(note);
            hooks?.notify?.("笔记已恢复。");
          } catch (error) {
            restore.disabled = false;
            status.textContent = errorText(error);
          }
        });
        const purge = button("彻底删除", "nh-trash-purge", async () => {
          const ok = hooks?.confirm ? hooks.confirm(`彻底删除「${item.title || "这条笔记"}」？它的历史版本也会一起删除，之后无法找回。`) : false;
          if (!ok) return;
          purge.disabled = true;
          try {
            await hooks.api(`/api/notes/${item.id}/purge`, { method: "DELETE" });
            data.items = data.items.filter((other) => other.id !== item.id);
            render();
          } catch (error) {
            purge.disabled = false;
            status.textContent = errorText(error);
          }
        });
        actions.append(restore, purge);
        row.append(text, actions);
        return row;
      }));
    }
    render();
  }

  window.NotesHistory = Object.freeze({ configure, drafts, autosave, openHistory, openTrash, hasUnsaved, when });
})();
