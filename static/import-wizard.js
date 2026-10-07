"use strict";

/* File → preview → confirm. Every async result belongs to one account, view and
   dialog opening. All requests use the host api(), including multipart uploads. */
(() => {
  let hooks = null, dialog = null, content = null, status = null, entry = null;
  let sequence = 0, busy = false, storedPreview = null;
  function node(tag, text, className = "") {
    const element = document.createElement(tag);
    element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function button(text, id, action) {
    const element = node("button", text);
    element.type = "button"; element.id = id;
    element.addEventListener("click", action);
    return element;
  }
  function identity() {
    return { user: hooks.getUser()?.id, epoch: hooks.getEpoch(), view: hooks.getView(), sequence };
  }
  function current(ticket) {
    return dialog?.open && ticket.user === hooks.getUser()?.id && ticket.user !== undefined
      && ticket.epoch === hooks.getEpoch() && ticket.view === hooks.getView()
      && ticket.view === "all" && ticket.sequence === sequence;
  }
  function setBusy(value) {
    busy = value;
    content?.querySelectorAll("button,input,select").forEach((element) => { element.disabled = value; });
    dialog?.setAttribute("aria-busy", String(value));
  }
  function reset() {
    sequence += 1; busy = false; storedPreview = null;
    dialog?.close(); content?.replaceChildren();
    if (status) status.textContent = "";
    if (dialog) dialog.setAttribute("aria-busy", "false");
    if (entry) entry.hidden = !hooks?.getUser() || hooks.getView() !== "all";
  }
  function uploadStep() {
    storedPreview = null; setBusy(false); content.replaceChildren(); status.textContent = "";
    content.append(node("h3", "1. 选择文件"), node("p",
      "支持 day/cuoti Markdown 或本站导出的 JSON；每份文件最多 2 MiB、200 道题。只导入题目和错因，重新安排复习；JSON 的旧评分、AI 变体和标签不会恢复。", "muted"));
    const form = node("form"); form.id = "import-upload";
    const label = node("label", "笔记文件"); label.setAttribute("for", "import-file");
    const file = node("input"); file.id = "import-file"; file.type = "file"; file.required = true;
    file.setAttribute("accept", ".md,.markdown,.json");
    const zoneLabel = node("label", "导入到分区"); zoneLabel.setAttribute("for", "import-zone");
    const zone = node("select"); zone.id = "import-zone";
    for (const name of hooks.getZones()) { const option = node("option", name); option.value = name; zone.append(option); }
    zone.value = "算法";
    const next = node("button", "预览文件"); next.id = "import-preview"; next.type = "submit"; next.className = "primary";
    form.append(label, file, zoneLabel, zone, next);
    form.addEventListener("submit", async (event) => {
      event.preventDefault(); if (busy) return;
      const chosen = file.files?.[0];
      if (!chosen) { status.textContent = "请先选择文件。"; return; }
      if (chosen.size > 2 * 1024 * 1024) { status.textContent = "文件太大，最多 2 MiB。"; return; }
      const ticket = identity(); setBusy(true); status.textContent = "正在读取预览…";
      const body = new window.FormData(); body.append("file", chosen); body.append("zone", zone.value);
      try {
        const preview = await hooks.api("/api/import/preview", { method: "POST", body });
        if (!current(ticket)) return;
        storedPreview = preview; previewStep();
      } catch (error) { if (current(ticket)) status.textContent = error.message; }
      finally { if (current(ticket)) setBusy(false); }
    });
    const instructions = node("details"), summary = node("summary", "文件需要是什么格式？");
    instructions.append(summary, node("p", "Markdown 需包含题目、带语言标签的代码围栏、思路和易错点列表。cuoti 的一级题名、错误原因分析、关键收获格式也支持。JSON 请使用账号菜单中的「导出我的数据」。合法的原记录日期会保留；文中的网址和代码不会被执行。"));
    content.append(form, instructions);
  }
  function previewStep() {
    content.replaceChildren(); status.textContent = "预览 30 分钟内有效。勾选需要导入的题目。";
    content.append(node("h3", "2. 预览并勾选"));
    const list = node("ul", undefined, "import-items");
    for (const item of storedPreview.items) {
      const row = node("li"), label = node("label", undefined, "import-choice");
      const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.checked = true;
      checkbox.value = String(item.index); checkbox.id = `import-item-${item.index}`;
      label.setAttribute("for", checkbox.id);
      label.append(checkbox, node("span", `${item.title} · ${item.mistakes_count} 条易错点 · ${item.zone}`));
      row.append(label);
      for (const warning of item.warnings) row.append(node("p", warning, "muted"));
      list.append(row);
    }
    content.append(list);
    for (const skipped of storedPreview.skipped) content.append(node("p", `跳过知识点：${skipped}`, "muted"));
    const actions = node("div", undefined, "import-actions");
    actions.append(button("重新选文件", "import-back", () => { if (!busy) uploadStep(); }));
    const confirm = button("确认导入", "import-confirm", async () => {
      if (busy) return;
      const indices = [...list.querySelectorAll("input")].filter((box) => box.checked).map((box) => Number(box.value));
      if (!indices.length) { status.textContent = "请至少勾选一道题。"; return; }
      const ticket = identity(); setBusy(true);
      status.textContent = "正在导入…关闭窗口不会取消已经发出的导入，请稍后在记录页查看。";
      try {
        const result = await hooks.api("/api/import/confirm", { method: "POST",
          body: JSON.stringify({ preview_id: storedPreview.preview_id, indices }) });
        if (!current(ticket)) return;
        content.replaceChildren(node("h3", "3. 导入结果"), node("p", `新增 ${result.imported} 道题；跳过 ${result.duplicates.length} 道重复题。`));
        for (const duplicate of result.duplicates) content.append(node("p", `第 ${duplicate.index + 1} 项：${duplicate.reason}`, "muted"));
        for (const failure of result.failed) content.append(node("p", `第 ${failure.index + 1} 项：${failure.error}`));
        content.append(button("继续导入", "import-again", () => { if (!busy) uploadStep(); }));
        status.textContent = "导入完成。新易错点安排在今天复习。";
        try { await hooks.onImported(); }
        catch { if (current(ticket)) status.textContent = "导入已完成，但列表暂时无法刷新；关闭窗口后手动刷新即可。"; }
      } catch (error) { if (current(ticket)) status.textContent = error.message; }
      finally { if (current(ticket)) setBusy(false); }
    });
    confirm.className = "primary"; actions.append(confirm); content.append(actions);
  }
  function open() {
    if (!hooks?.getUser() || hooks.getView() !== "all") return;
    reset();
    if (!dialog) {
      dialog = node("dialog"); dialog.id = "import-wizard"; dialog.setAttribute("aria-labelledby", "import-title");
      const header = node("div", undefined, "import-actions"), title = node("h2", "导入错题"); title.id = "import-title";
      header.append(title, button("关闭", "import-close", reset));
      content = node("div", undefined, "import-content");
      status = node("p"); status.id = "import-status"; status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
      dialog.append(header, content, status); dialog.addEventListener("cancel", (event) => { event.preventDefault(); reset(); });
      dialog.addEventListener("close", () => { if (!dialog.open && (storedPreview || busy)) reset(); });
      document.body.append(dialog);
    }
    uploadStep(); dialog.showModal(); dialog.querySelector("#import-file").focus();
  }
  document.addEventListener("app:view-changed", reset);
  window.ImportWizard = { reset, configure(options) {
    hooks = options;
    if (!entry) { entry = document.getElementById("list-import"); entry?.addEventListener("click", open); }
    if (entry) entry.hidden = !hooks.getUser() || hooks.getView() !== "all";
  } };
})();
