"use strict";

/* 认证变更未决时保持会话入口锁定；强制失效后的迟到响应不能改动界面。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const dialog = $("#account-dialog");
  const content = $("#account-dialog-content");
  if (!dialog || !content) return;

  let generation = 0;
  let pending = false;
  let mode = "";
  let ownerId = null;
  let secOwnerEpoch = null;
  let returnFocus = null;
  let restoreOnClose = true;
  let form = null;
  let submitButton = null;
  let errorBox = null;
  let fields = {};
  let submitText = "";

  function node(tag, className, text, id) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    if (id) item.id = id;
    return item;
  }

  function passwordField(key, id, labelText, autocomplete) {
    const label = node("label", "account-dialog-field");
    const input = node("input", "", undefined, id);
    input.type = "password";
    input.name = key;
    input.autocomplete = autocomplete;
    input.required = true;
    input.maxLength = 128;
    if (autocomplete === "new-password") input.minLength = 8;
    label.setAttribute("for", id);
    label.append(node("span", "", labelText), input);
    fields[key] = input;
    form.append(label);
    return input;
  }

  function current(ticket, userId) {
    return Boolean(user) && user.id === userId && secOwnerEpoch === sessionEpoch
      && generation === ticket && dialog.open && Boolean(mode);
  }

  function restoreFocus(target) {
    const summary = $(".account-menu summary");
    if (target?.isConnected && !target.disabled && !target.closest("[hidden]")) {
      // 关闭的 details 内无法聚焦；重新展开后还原到原来的菜单控件。
      const menu = target.closest(".account-menu");
      if (menu) menu.open = true;
      if (target.offsetParent !== null) {
        target.focus({ preventScroll: true });
        return;
      }
    }
    if (summary?.isConnected && !summary.closest("[hidden]") && summary.offsetParent !== null) {
      summary.focus({ preventScroll: true });
    }
  }

  function finishClose(restore) {
    if (!mode) return;
    generation += 1;
    const target = returnFocus;
    returnFocus = null;
    for (const input of Object.values(fields)) {
      input.value = "";
      if (input.type === "checkbox") input.checked = false;
    }
    fields = {};
    mode = "";
    ownerId = null;
    secOwnerEpoch = null;
    form = null;
    submitButton = null;
    errorBox = null;
    content.replaceChildren();
    dialog.removeAttribute("aria-describedby");
    if (restore) restoreFocus(target);
  }

  function close({ restore = true, force = false } = {}) {
    if (pending && !force) return;
    restoreOnClose = restore;
    if (force) finishClose(restore);
    if (dialog.open) dialog.close();
    finishClose(restore);
  }

  function reset() {
    close({ restore: false, force: true });
    generation += 1;
  }

  function secRenderSessionLock() {
    const secLogout = $("#logout");
    if (secLogout) {
      secLogout.dataset.blocked = pending ? "1" : "0";
      secLogout.disabled = pending;
    }
    for (const id of ["account-dialog-close", "account-dialog-cancel"]) {
      const button = $(`#${id}`);
      if (button) button.disabled = pending;
    }
  }

  function renderControls() {
    secRenderSessionLock();
    if (!form) return;
    const incomplete = mode === "delete" && (!fields.confirm_delete.checked || !fields.password.value);
    submitButton.disabled = pending || incomplete;
    submitButton.dataset.blocked = submitButton.disabled ? "1" : "0";
    submitButton.setAttribute("aria-disabled", String(submitButton.disabled));
    submitButton.textContent = pending ? "处理中…" : submitText;
    form.setAttribute("aria-busy", String(pending));
    for (const input of Object.values(fields)) input.disabled = pending;
  }

  function fail(text, input) {
    errorBox.textContent = text;
    input?.focus();
  }

  async function submit(event) {
    event.preventDefault();
    if (pending || !current(generation, ownerId)) return;
    errorBox.textContent = "";
    let path;
    let body;
    const action = mode;
    if (action === "password") {
      const { current_password: old, new_password: next, confirm_password: confirm } = fields;
      const missing = [old, next, confirm].find((input) => !input.value);
      if (missing) return fail("请填写当前密码、新密码和确认新密码。", missing);
      if (next.value.length < 8) return fail("新密码至少需要 8 个字符。", next);
      if (next.value !== confirm.value) return fail("两次输入的新密码不一致。", confirm);
      if (next.value === old.value) return fail("新密码不能与当前密码相同。", next);
      path = "/api/me/password";
      body = { current_password: old.value, new_password: next.value };
    } else if (action === "revoke") {
      path = "/api/me/sessions/revoke-others";
      body = {};
    } else {
      if (!fields.password.value) return fail("请输入当前密码。", fields.password);
      if (!fields.confirm_delete.checked) return fail("请先确认你已了解注销后无法恢复。", fields.confirm_delete);
      path = "/api/me/delete-account";
      body = { password: fields.password.value };
    }
    const ticket = generation;
    const userId = ownerId;
    pending = true;
    renderControls();
    try {
      const result = await api(path, { method: "POST", body: JSON.stringify(body) });
      if (!current(ticket, userId)) return;
      pending = false;
      renderControls();
      if (action === "delete") {
        close({ restore: false });
        history.replaceState(null, "", `${location.pathname}#/welcome`);
        signedOut();
        message("账号已注销。");
      } else {
        close();
        message(action === "password"
          ? result.revoked_sessions > 0 ? "密码已更新，其他设备已退出登录。" : "密码已更新。"
          : result.revoked > 0 ? `已退出其他设备（共 ${result.revoked} 处）。` : "没有其他已登录的设备。");
      }
    } catch (error) {
      if (current(ticket, userId)) fail(error.message || "操作失败，请检查网络后重试。");
    } finally {
      pending = false;
      if (current(ticket, userId)) renderControls();
      else secRenderSessionLock();
    }
  }

  function deletionDetails() {
    const details = node("div", "account-delete-consequences", undefined, "account-dialog-description");
    const deleted = node("p");
    deleted.append(node("strong", "", "将删除且无法恢复："), node("span", "", "你的全部题目、易错点、复习记录、变体题、错因标签、AI 分析结果、头像；你在小组里的成员身份。"));
    const retained = node("p");
    retained.append(node("strong", "", "将保留："), node("span", "", "你在讨论区发过的帖子和评论（作者显示为“已注销用户”）；订单记录（按财务要求保留，不含个人资料）。"));
    const exportHint = node("p");
    const exportLink = node("a", "", "先导出我的数据");
    exportLink.href = "/api/export";
    exportHint.append(node("span", "", "建议"), exportLink, node("span", "", "，再注销账号。"));
    details.append(deleted, retained, exportHint);
    form.append(details);
    passwordField("password", "account-delete-password", "当前密码", "current-password");
    const label = node("label", "account-delete-confirm");
    const checkbox = node("input", "", undefined, "account-delete-confirm");
    checkbox.type = "checkbox";
    checkbox.name = "confirm_delete";
    checkbox.required = true;
    label.setAttribute("for", checkbox.id);
    label.append(checkbox, node("span", "", "我已了解，注销后无法恢复"));
    fields.confirm_delete = checkbox;
    form.append(label);
    fields.password.addEventListener("input", renderControls);
    checkbox.addEventListener("change", renderControls);
  }

  function open(nextMode, trigger) {
    if (pending || !user || user.is_trial) return;
    close({ restore: false });
    closeAccountMenu();
    generation += 1;
    mode = nextMode;
    ownerId = user.id;
    secOwnerEpoch = sessionEpoch;
    returnFocus = trigger;
    restoreOnClose = true;
    const title = nextMode === "password" ? "修改密码" : nextMode === "revoke" ? "退出其他设备" : "注销账号";
    const heading = node("div", "account-dialog-heading");
    const closeButton = node("button", "account-dialog-close", "×", "account-dialog-close");
    closeButton.type = "button";
    closeButton.setAttribute("aria-label", "关闭账号对话框");
    closeButton.addEventListener("click", () => close());
    heading.append(node("h2", "", title, "account-dialog-title"), closeButton);
    form = node("form", "account-dialog-form", undefined, "account-dialog-form");
    form.noValidate = true;
    form.addEventListener("submit", submit);
    if (nextMode === "password") {
      form.append(node("p", "account-dialog-hint", "新密码至少 8 个字符，修改后其他设备会退出登录。", "account-dialog-description"));
      passwordField("current_password", "account-current-password", "当前密码", "current-password");
      passwordField("new_password", "account-new-password", "新密码", "new-password");
      passwordField("confirm_password", "account-confirm-password", "确认新密码", "new-password");
    } else if (nextMode === "revoke") {
      form.append(node("p", "account-dialog-hint", "将退出你在其他浏览器和设备上的登录，当前设备不受影响。", "account-dialog-description"));
    } else {
      deletionDetails();
    }
    errorBox = node("p", "account-dialog-error", "", "account-dialog-error");
    errorBox.setAttribute("role", "alert");
    const actions = node("div", "account-dialog-actions");
    const cancelButton = node("button", "account-dialog-cancel", "取消", "account-dialog-cancel");
    cancelButton.type = "button";
    cancelButton.addEventListener("click", () => close());
    submitText = nextMode === "password" ? "更新密码" : nextMode === "revoke" ? "确认退出其他设备" : "永久注销账号";
    submitButton = node("button", `account-dialog-submit${nextMode === "delete" ? " account-danger" : ""}`, submitText, "account-dialog-submit");
    submitButton.type = "submit";
    actions.append(cancelButton, submitButton);
    form.append(errorBox, actions);
    content.replaceChildren(heading, form);
    dialog.setAttribute("aria-describedby", "account-dialog-description");
    renderControls();
    dialog.showModal();
    (form.querySelector("input") || cancelButton).focus({ preventScroll: true });
  }

  const openPassword = (trigger = $("#account-password")) => open("password", trigger);
  const openRevokeOthers = (trigger = $("#account-revoke-others")) => open("revoke", trigger);
  const openDelete = (trigger = $("#account-delete")) => open("delete", trigger);
  $("#account-password")?.addEventListener("click", (event) => openPassword(event.currentTarget));
  $("#account-revoke-others")?.addEventListener("click", (event) => openRevokeOthers(event.currentTarget));
  $("#account-delete")?.addEventListener("click", (event) => openDelete(event.currentTarget));
  dialog.addEventListener("cancel", (event) => { event.preventDefault(); close(); });
  dialog.addEventListener("close", () => {
    if (dialog.open) return;
    if (pending && mode) dialog.showModal();
    else finishClose(restoreOnClose);
  });
  dialog.addEventListener("click", (event) => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) close();
  });
  dialog.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      close();
      return;
    }
    if (event.key !== "Tab" || !dialog.open) return;
    const items = [...dialog.querySelectorAll("input:not(:disabled), button:not(:disabled), a[href]")]
      .filter((item) => !item.closest("[hidden]") && item.offsetParent !== null);
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  });
  window.Account = { openPassword, openRevokeOthers, openDelete, close, reset, isPending: () => pending };
})();
