"use strict";

/* Public profile dialog. Requests use the host's CSRF api and remain tied to
   their account, login epoch, page and dialog generation. */
(() => {
  const dialog = document.querySelector("#profile-dialog");
  const content = document.querySelector("#profile-content");
  let hooks = null, sequence = 0, returnFocus = null;

  function node(tag, text = "", className = "") {
    const result = document.createElement(tag);
    result.textContent = text;
    if (className) result.className = className;
    return result;
  }
  function button(text, className = "") {
    const result = node("button", text, className);
    result.type = "button";
    return result;
  }
  function ticket() {
    return { epoch: hooks.getEpoch(), owner: hooks.getUser()?.id, view: hooks.getView(), sequence };
  }
  function current(value) {
    return Boolean(hooks?.getUser()) && dialog.open && value.sequence === sequence
      && value.epoch === hooks.getEpoch() && value.owner === hooks.getUser().id
      && value.view === hooks.getView();
  }
  function clear(restore = true) {
    sequence += 1;
    content.replaceChildren();
    if (restore && returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
    returnFocus = null;
  }
  function reset() {
    clear(false);
    if (dialog.open) dialog.close();
  }
  function author(userId, username) {
    const link = button(username, "profile-author");
    link.setAttribute("aria-label", `查看 ${username} 的个人资料`);
    link.addEventListener("click", () => open(userId));
    return link;
  }
  function render(data, identity) {
    const head = node("div", "", "profile-heading");
    const title = node("h3", data.display_name);
    title.id = "profile-title";
    const close = button("关闭", "profile-close");
    close.addEventListener("click", () => dialog.close());
    head.append(hooks.avatar(data.user_id, data.display_name, data.avatar_version, { hasAvatar: data.has_avatar }), title, close);
    const bio = node("p", data.bio || "还没有填写简介。", "profile-bio");
    const stats = node("dl", "", "profile-stats");
    for (const [label, field] of [["累计录入题目", "lifetime_problem_count"], ["加入时间", "joined_at"]]) {
      if (!(field in data)) continue;
      const cell = node("div");
      cell.append(node("dt", label), node("dd", field === "joined_at" ? String(data[field]).slice(0, 10) : String(data[field])));
      stats.append(cell);
    }
    content.replaceChildren(head);
    if ("bio" in data) content.append(bio);
    content.append(stats, node("p", "只显示对方选择公开的资料。累计以功能启用时已有题目为起点，以后删除不减少。", "profile-note"));
    if (data.user_id !== identity.owner) return;
    const form = node("form", "", "profile-edit");
    const label = node("label", "公开简介（最多 200 字，可留空）");
    const input = node("textarea");
    input.rows = 3; input.maxLength = 200; input.value = data.bio ?? data.settings?.bio ?? "";
    label.append(input);
    const status = node("p", "", "profile-status");
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const save = button("保存简介", "primary"); save.type = "submit";
    let busy = false;
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!current(identity) || busy) return;
      if ([...input.value].length > 200) { status.textContent = "简介最多 200 字。"; return; }
      busy = true; input.readOnly = true; save.disabled = true; status.textContent = "正在保存…";
      form.setAttribute("aria-busy", "true");
      try {
        const updated = await hooks.api("/api/me/bio", { method: "PUT", body: JSON.stringify({ bio: input.value }) });
        if (!current(identity)) return;
        bio.textContent = updated.bio || "还没有填写简介。";
        input.value = updated.bio;
        status.textContent = "简介已保存。";
      } catch (error) {
        if (!current(identity)) return;
        status.textContent = error.message || "保存失败，请重试。";
      } finally {
        if (current(identity)) {
          busy = false; input.readOnly = false; save.disabled = false;
          form.setAttribute("aria-busy", "false");
        }
      }
    });
    form.append(label, save, status); content.append(form);
    if (!data.settings) return;
    const settingsForm = node("form", "", "profile-edit");
    const nameLabel = node("label", "榜单显示名（选填，建议真名，1~12 个字）");
    const name = node("input"); name.type = "text"; name.maxLength = 12;
    name.value = data.settings.rank_display_name;
    name.className = "profile-rank-name"; nameLabel.append(name);
    settingsForm.append(nameLabel, node("p", "仅在登录后的榜单与资料卡显示；留空继续使用昵称。不想公开可在账号菜单选择匿名。", "profile-note"));
    const flags = {};
    for (const [key, text] of [["bio", "公开简介"], ["count", "公开累计题目数"], ["joined", "公开加入时间"]]) {
      const label = node("label", text);
      const check = node("input"); check.type = "checkbox";
      check.checked = Boolean(data.settings[`profile_public_${key}`]);
      flags[key] = check; label.prepend(check); settingsForm.append(label);
    }
    const settingsSave = button("保存资料设置", "primary"); settingsSave.type = "submit";
    const settingsStatus = node("p", "", "profile-status"); settingsStatus.setAttribute("role", "status");
    let settingsBusy = false;
    settingsForm.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!current(identity) || settingsBusy) return;
      settingsBusy = true; settingsSave.disabled = true;
      try {
        const payload = { rank_display_name: name.value };
        for (const [key, check] of Object.entries(flags)) payload[`profile_public_${key}`] = check.checked;
        await hooks.api("/api/me/profile-settings", { method: "PUT", body: JSON.stringify(payload) });
        if (current(identity)) await open(data.user_id);
      } catch (error) {
        if (current(identity)) settingsStatus.textContent = error.message || "保存失败，请重试。";
      } finally {
        if (current(identity)) { settingsBusy = false; settingsSave.disabled = false; }
      }
    });
    settingsForm.append(settingsSave, settingsStatus); content.append(settingsForm);
  }
  async function open(userId) {
    if (!hooks?.getUser() || !Number.isSafeInteger(userId) || userId < 1) return;
    sequence += 1;
    if (!dialog.open) returnFocus = document.activeElement;
    const title = node("h3", "个人资料"); title.id = "profile-title";
    const close = button("关闭", "profile-close"); close.addEventListener("click", () => dialog.close());
    const head = node("div", "", "profile-heading"); head.append(title, close);
    const loading = node("p", "正在读取资料…", "profile-status"); loading.setAttribute("role", "status");
    content.replaceChildren(head, loading);
    if (!dialog.open) dialog.showModal();
    const identity = ticket();
    try {
      const data = await hooks.api(`/api/users/${userId}/public`);
      if (current(identity)) render(data, identity);
    } catch (error) {
      if (current(identity)) loading.textContent = error.message || "读取失败，请重试。";
    }
  }
  dialog.addEventListener("close", () => { if (!dialog.open) clear(); });
  document.addEventListener("app:view-changed", reset);
  window.Profile = { configure(next) { hooks = next; }, author, open, reset };
})();
