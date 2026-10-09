"use strict";

/* 账号设置里的「每日复习提醒」卡片：开关 + 说明。对外契约：
   window.Reminder = { configure({api, getUser, getEpoch}), mount(container), reset() }。
   - 初始状态取 getUser().reminder_opt_in（后端 /api/me 需返回该字段；缺省按开启显示，
     与迁移默认值 1 一致）；用户文字一律 textContent；
   - 迟到响应守卫：getEpoch()、getUser().id、内部代次一起校验，登出 / 换号 /
     reset 之后才返回的响应一律丢弃；请求走宿主注入的 api()（自带 CSRF 头）。 */
(() => {
  const PATH = "/api/me/reminder";

  let hooks = null;
  let generation = 0;
  let container = null;
  let root = null;
  let saving = false;
  let refs = null;

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function currentUser() {
    return hooks?.getUser ? hooks.getUser() : null;
  }

  function alive(ticket) {
    return Boolean(
      root && ticket.generation === generation
      && (hooks?.getEpoch ? hooks.getEpoch() : null) === ticket.epoch
      && (currentUser()?.id ?? null) === ticket.userId
    );
  }

  function setStatus(text, isError) {
    if (!refs) return;
    refs.status.textContent = text;
    refs.status.dataset.kind = isError ? "error" : "info";
  }

  function setBusy() {
    if (!refs) return;
    refs.toggle.disabled = saving;
  }

  function readInitialOptIn() {
    const user = currentUser();
    // 后端尚未返回该字段时按开启显示（迁移默认值 1）。
    if (user && typeof user.reminder_opt_in !== "undefined" && user.reminder_opt_in !== null) {
      return Boolean(user.reminder_opt_in);
    }
    return true;
  }

  async function save(optIn, ticket) {
    if (!alive(ticket) || saving) return;
    saving = true;
    setBusy();
    setStatus("保存中…", false);
    try {
      const updated = await hooks.api(PATH, {
        method: "PUT",
        body: JSON.stringify({ opt_in: optIn }),
      });
      if (!alive(ticket)) return;
      currentUser().reminder_opt_in = Boolean(updated.opt_in);
      setStatus(updated.opt_in ? "已开启每日复习提醒。" : "已关闭每日复习提醒。", false);
    } catch (error) {
      if (!alive(ticket)) return;
      setStatus(error.message || "保存失败，请稍后重试。", true);
      // 回滚开关显示。
      if (refs) refs.toggle.checked = !optIn;
    } finally {
      if (alive(ticket)) {
        saving = false;
        setBusy();
      }
    }
  }

  function build() {
    const section = el("section", "reminder-settings");
    const heading = el("h3", "reminder-settings-title", "每日复习提醒");
    const label = el("label", "reminder-settings-row");
    const toggle = el("input", "reminder-toggle");
    toggle.type = "checkbox";
    toggle.checked = readInitialOptIn();
    toggle.setAttribute("aria-label", "每日复习提醒开关");
    const caption = el("span", null, "每天有一封邮件，告诉你今天有几道题待复习。");
    const status = el("p", "reminder-settings-status");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    const ticket = {
      generation,
      epoch: hooks?.getEpoch ? hooks.getEpoch() : null,
      userId: currentUser()?.id ?? null,
    };

    toggle.addEventListener("change", () => {
      save(toggle.checked, ticket);
    });

    label.append(toggle, caption);
    section.append(heading, label, status);
    refs = { toggle, status };
    setBusy();
    return section;
  }

  const api = {
    configure(options) {
      hooks = options || null;
    },
    mount(node) {
      generation += 1;
      saving = false;
      container = node;
      root = build();
      container.replaceChildren(root);
    },
    reset() {
      generation += 1;
      container = null;
      root = null;
      refs = null;
      saving = false;
    },
  };

  window.Reminder = api;
})();
