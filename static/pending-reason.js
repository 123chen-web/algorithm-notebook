"use strict";

/* 待补原因：普通记录沿用原有编辑器；所有请求经过宿主 api()。
   表单草稿只留在当前页面，每个异步结果校验账号、登录代次、视图、页面代次与请求序号。 */
(() => {
  let hooks = null;
  let pageGeneration = 0;
  let homeSequence = 0;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function button(text, className) {
    const element = node("button", className, text);
    element.type = "button";
    return element;
  }
  function identity() {
    return { epoch: hooks.getEpoch(), userId: hooks.getUser()?.id, view: hooks.getView(), page: pageGeneration };
  }
  function sameSession(ticket) {
    return Boolean(hooks?.getUser()) && ticket.epoch === hooks.getEpoch()
      && ticket.userId === hooks.getUser().id;
  }
  function current(ticket) {
    return sameSession(ticket) && ticket.view === hooks.getView() && ticket.page === pageGeneration;
  }
  function cleanTag(value) {
    return typeof value === "string" ? value.trim().replace(/\s+/g, " ") : "";
  }

  function render(item) {
    if (!hooks || !hooks.getUser() || !item.pending_reason) return null;
    const ticket = identity();
    const form = node("form", "pending-reason-form");
    const live = () => current(ticket) && form.isConnected;
    let busy = false, completed = false, completedElsewhere = false, sequence = 0;
    let tagsSequence = 0;
    const choices = new Map();
    const selected = new Map();
    for (const value of item.tags || []) {
      const tag = cleanTag(value);
      if (tag && tag.length <= 20) { choices.set(tag.toLowerCase(), tag); selected.set(tag.toLowerCase(), tag); }
    }
    const title = node("h4", "pending-reason-title", "待补 · 现在补一句原因");
    const help = node("p", "pending-reason-help", "回想这次为什么错，写一句就好。补充原因不会改变复习安排。");
    const label = node("label", "pending-reason-label", "为什么错？");
    const input = node("textarea", "pending-reason-input");
    input.name = "description";
    input.maxLength = 2000;
    input.rows = 3;
    input.required = true;
    input.placeholder = "例如：循环结束时漏掉了等号。";
    label.append(input);
    const tagTitle = node("p", "pending-reason-help", "标签建议（可选，最多 3 个）");
    const tagHost = node("div", "pending-reason-tags");
    tagHost.setAttribute("role", "group");
    tagHost.setAttribute("aria-label", "选择错因标签");
    const count = node("p", "pending-reason-count");
    const customLabel = node("label", "pending-reason-label", "自定义标签");
    const custom = node("input", "pending-reason-custom");
    custom.type = "text";
    custom.maxLength = 20;
    custom.placeholder = "最多 20 个字";
    customLabel.append(custom);
    const add = button("添加标签", "pending-reason-add");
    const customRow = node("div", "pending-reason-custom-row");
    customRow.append(customLabel, add);
    const tagStatus = node("p", "pending-reason-tag-status", "正在读取标签建议…");
    tagStatus.setAttribute("role", "status");
    const status = node("p", "pending-reason-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const serverReason = node("p", "pending-reason-server");
    serverReason.hidden = true;
    const save = button("保存这句原因", "pending-reason-save");
    save.type = "submit";
    const refresh = button("刷新版本，保留草稿", "pending-reason-refresh");
    refresh.hidden = true;
    const reopen = button("重新查看记录", "pending-reason-reopen");
    reopen.hidden = true;
    const actions = node("div", "pending-reason-actions");
    actions.append(save, refresh, reopen);
    form.append(title, help, label, tagTitle, tagHost, count, customRow, tagStatus, status, serverReason, actions);

    function notice(text, error = false) {
      status.textContent = text;
      status.dataset.error = String(error);
    }
    function paintTags() {
      tagHost.replaceChildren();
      for (const [key, tag] of choices) {
        const choice = button(tag, "pending-reason-tag");
        choice.setAttribute("aria-pressed", String(selected.has(key)));
        choice.disabled = busy || completed || completedElsewhere || (!selected.has(key) && selected.size >= 3);
        choice.addEventListener("click", () => {
          if (!live() || choice.disabled) return;
          if (selected.has(key)) selected.delete(key);
          else if (selected.size < 3) selected.set(key, tag);
          paintTags();
        });
        tagHost.append(choice);
      }
      count.textContent = `已选 ${selected.size} / 3 个标签`;
    }
    function controls() {
      input.readOnly = busy || completed || completedElsewhere;
      custom.disabled = add.disabled = busy || completed || completedElsewhere;
      save.disabled = busy || completed || completedElsewhere;
      refresh.disabled = reopen.disabled = busy;
      form.setAttribute("aria-busy", String(busy));
      paintTags();
    }
    add.addEventListener("click", () => {
      if (!live() || busy || completed || completedElsewhere) return;
      const tag = cleanTag(custom.value);
      if (!tag || tag.length > 20) { notice("标签请填写 1–20 个字。", true); return; }
      const key = tag.toLowerCase();
      if (!selected.has(key) && selected.size >= 3) { notice("最多选择 3 个标签，先取消一个。", true); return; }
      choices.set(key, tag); selected.set(key, tag); custom.value = ""; paintTags();
    });
    form.addEventListener("submit", (event) => { event.preventDefault(); void submit(); });
    async function submit() {
      if (!live() || busy || completed || completedElsewhere) return;
      const description = input.value.trim();
      if (!description || description.length > 2000) { notice("请补一句原因，最多 2000 个字。", true); return; }
      if (selected.size > 3) { notice("最多选择 3 个标签，先取消多余标签。", true); return; }
      const request = ++sequence;
      busy = true; notice("正在保存…"); controls();
      try {
        const updated = await hooks.api(`/api/mistakes/${item.id}/reason`, {
          method: "POST", body: JSON.stringify({ description, tags: [...selected.values()], version: item.version }),
        });
        if (!live() || request !== sequence) return;
        Object.assign(item, { description: updated.description, tags: updated.tags || [], version: updated.version, pending_reason: false });
        completed = true;
        tagsSequence += 1;
        notice("原因已保存，复习安排保持不变。");
        try { await hooks.onSaved?.(item.id); }
        catch { if (live()) notice("原因已保存，重新查看记录可读取最新内容。"); }
      } catch (error) {
        if (!live() || request !== sequence) return;
        notice(error.status === 409 ? "这条记录已更新；草稿已保留，请刷新版本后再提交。" : error.message, true);
        refresh.hidden = error.status !== 409;
      } finally {
        if (live() && request === sequence) { busy = false; controls(); }
      }
    }
    refresh.addEventListener("click", () => { void refreshVersion(); });
    async function refreshVersion() {
      if (!live() || busy || completed || completedElsewhere) return;
      const request = ++sequence;
      busy = true; controls();
      try {
        const fresh = await hooks.api(`/api/mistakes/${item.id}`);
        if (!live() || request !== sequence) return;
        item.version = fresh.version;
        refresh.hidden = true;
        if (!fresh.pending_reason) {
          completedElsewhere = true;
          serverReason.textContent = fresh.description || "（原因未填写）";
          serverReason.hidden = false;
          reopen.hidden = false;
          notice("其他设备已补充原因。你的草稿仍保留在上方，可复制后重新查看记录。");
        } else {
          notice("已读取最新版本，草稿和标签仍保留；确认后再点保存。");
        }
      } catch (error) {
        if (live() && request === sequence) notice(error.message, true);
      } finally {
        if (live() && request === sequence) { busy = false; controls(); }
      }
    }
    reopen.addEventListener("click", () => {
      if (!live() || busy) return;
      Promise.resolve(hooks.openMistake(item.id)).catch((error) => { if (live()) notice(error.message, true); });
    });
    async function suggestions() {
      const request = ++tagsSequence;
      try {
        const data = await hooks.api("/api/tags/suggest");
        if (!live() || request !== tagsSequence) return;
        const tags = [...(Array.isArray(data.mine) ? data.mine.map((entry) => entry?.tag) : []),
          ...(Array.isArray(data.builtin) ? data.builtin : [])];
        for (const value of tags) {
          const tag = cleanTag(value), key = tag.toLowerCase();
          if (tag && tag.length <= 20 && !choices.has(key)) choices.set(key, tag);
        }
        tagStatus.textContent = "也可以不选标签，先把原因记下来。";
        paintTags();
      } catch {
        if (live() && request === tagsSequence) tagStatus.textContent = "标签建议暂时不可用；可以先保存原因，或添加自定义标签。";
      }
    }
    controls();
    void suggestions();
    return form;
  }

  function homeRendered(detail) {
    const sequence = ++homeSequence;
    document.getElementById("pending-reason-reminder")?.remove();
    if (!hooks?.getUser() || hooks.getView() !== "home" || detail?.user?.id !== hooks.getUser().id) return;
    const count = detail.overview?.pending_reason_count;
    if (!Number.isInteger(count) || count <= 0) return;
    const ticket = identity();
    const reminder = node("section", "pending-reason-reminder");
    reminder.id = "pending-reason-reminder";
    const text = node("p", "pending-reason-reminder-text", `还有 ${count} 条记录待补原因，补一句就能把这次发现留下来。`);
    const action = button("去补一句", "pending-reason-open");
    const status = node("p", "pending-reason-status");
    status.setAttribute("role", "status");
    reminder.append(text, action, status);
    document.getElementById("home-page")?.prepend(reminder);
    const live = () => current(ticket) && sequence === homeSequence && reminder.isConnected;
    action.addEventListener("click", () => { void openPending(); });
    async function openPending() {
      if (!live() || action.disabled) return;
      action.disabled = true;
      try {
        const data = await hooks.api("/api/mistakes?due_only=false&pending_reason=1");
        if (!live()) return;
        const first = data.items?.[0];
        if (!first || !Number.isInteger(first.id)) { status.textContent = "当前没有待补原因的记录，刷新总览可更新数量。"; return; }
        const transition = pageGeneration;
        await hooks.showView("all");
        if (!sameSession(ticket) || hooks.getView() !== "all" || pageGeneration !== transition + 1 || sequence !== homeSequence) return;
        await hooks.openMistake(first.id);
      } catch (error) {
        if (live()) status.textContent = error.message;
      } finally {
        if (live()) action.disabled = false;
      }
    }
  }

  document.addEventListener("app:view-changed", () => { pageGeneration += 1; });
  document.addEventListener("app:home-rendered", (event) => homeRendered(event.detail));
  window.PendingReason = { configure(options) { hooks = options; pageGeneration += 1; }, render };
})();
