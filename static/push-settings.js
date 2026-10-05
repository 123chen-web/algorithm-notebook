"use strict";

/* 账号设置里的「微信提醒」卡片：渠道选择、SendKey/token 输入、开关、
   保存与发送测试消息。对外契约：
   window.PushSettings = { configure({api, getUser, getEpoch}), mount(container), reset() }。
   - 密钥永远不回填到输入框，已配置时只显示尾号 4 位；用户文字一律 textContent；
   - 迟到响应守卫：getEpoch()、getUser().id、内部代次一起校验，登出 / 换号 /
     reset 之后才返回的响应一律丢弃；
   - 连续失败被服务端自动关闭时显示检查提示；请求走宿主注入的 api()（自带 CSRF 头）。 */
(() => {
  const PATH = "/api/me/push";
  const TEST_PATH = "/api/me/push/test";
  const CHANNELS = [
    { value: "serverchan", label: "Server酱（微信推送）" },
    { value: "pushplus", label: "PushPlus（微信推送）" },
  ];
  const AUTO_DISABLE_FAILS = 5;

  let hooks = null;
  let generation = 0;
  let container = null;
  let root = null;
  let saving = false;
  let testing = false;
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
      && (currentUser()?.id ?? null) === ticket.userId,
    );
  }

  function setStatus(text, isError) {
    if (!refs) return;
    refs.status.textContent = text;
    refs.status.dataset.kind = isError ? "error" : "info";
  }

  function setBusy() {
    if (!refs) return;
    refs.save.disabled = saving;
    refs.test.disabled = testing || !refs.configured;
    refs.channel.disabled = saving || testing;
    refs.secret.disabled = saving || testing;
    refs.enabledInput.disabled = saving || testing;
  }

  function paint(state) {
    if (!refs) return;
    refs.configured = Boolean(state.configured);
    refs.channel.value = state.channel || "serverchan";
    refs.enabledInput.checked = Boolean(state.enabled);
    refs.secret.value = "";
    refs.secret.setAttribute(
      "placeholder",
      state.configured
        ? `已保存（尾号 ${state.tail}），留空保存表示不修改`
        : "填写 SendKey 或 PushPlus token",
    );
    refs.tail.textContent = state.configured
      ? `当前已保存，只显示尾号 ${state.tail}，密钥不会显示在页面上。`
      : "";
    const autoOff = state.configured && !state.enabled
      && state.fail_count >= AUTO_DISABLE_FAILS;
    refs.note.hidden = !autoOff;
    refs.test.disabled = testing || !state.configured;
    refs.state = state;
  }

  async function load() {
    const ticket = {
      generation, epoch: hooks.getEpoch(), userId: currentUser()?.id ?? null,
    };
    try {
      const state = await hooks.api(PATH);
      if (!alive(ticket)) return;
      paint(state);
    } catch (error) {
      if (!alive(ticket)) return;
      setStatus(error?.message || "加载失败，请刷新后重试。", true);
    }
  }

  async function save() {
    if (saving || !root) return;
    const ticket = {
      generation, epoch: hooks.getEpoch(), userId: currentUser()?.id ?? null,
    };
    const body = {
      channel: refs.channel.value,
      secret: refs.secret.value.trim() ? refs.secret.value : null,
      enabled: refs.enabledInput.checked,
    };
    saving = true;
    setBusy();
    setStatus("正在保存…", false);
    try {
      const state = await hooks.api(PATH, { method: "PUT", body: JSON.stringify(body) });
      if (!alive(ticket)) return;
      paint(state);
      setStatus("已保存。", false);
    } catch (error) {
      if (!alive(ticket)) return;
      setStatus(error?.message || "保存失败，请检查网络后重试。", true);
    } finally {
      if (alive(ticket)) {
        saving = false;
        setBusy();
      } else {
        saving = false;
      }
    }
  }

  async function sendTest() {
    if (testing || !root || !refs.configured) return;
    const ticket = {
      generation, epoch: hooks.getEpoch(), userId: currentUser()?.id ?? null,
    };
    testing = true;
    setBusy();
    setStatus("正在发送测试消息…", false);
    try {
      const result = await hooks.api(TEST_PATH, { method: "POST" });
      if (!alive(ticket)) return;
      setStatus(result?.message || "测试消息已发送。", !result?.ok);
    } catch (error) {
      if (!alive(ticket)) return;
      setStatus(error?.message || "发送失败，请检查网络后重试。", true);
    } finally {
      if (alive(ticket)) {
        testing = false;
        setBusy();
      } else {
        testing = false;
      }
    }
  }

  function buildCard() {
    const card = el("section", "ps-card");
    card.append(el("h3", "ps-title", "微信提醒"));
    card.append(el(
      "p", "ps-desc",
      "每日复习提醒除了邮件，还可以通过 Server酱 / PushPlus 推送到微信；密钥只保存在服务器，页面只显示尾号 4 位。",
    ));

    const channelLabel = el("label", "ps-field");
    const channel = el("select", "ps-channel");
    channel.setAttribute("aria-label", "推送渠道");
    for (const item of CHANNELS) {
      const option = el("option", "", item.label);
      option.value = item.value;
      channel.append(option);
    }
    channelLabel.append(el("span", "ps-field-name", "渠道"), channel);

    const secretLabel = el("label", "ps-field");
    const secret = el("input", "ps-secret");
    secret.type = "password";
    secret.name = "push-secret";
    secret.autocomplete = "new-password";
    secret.maxLength = 128;
    secret.setAttribute("aria-label", "SendKey 或 token");
    secretLabel.append(el("span", "ps-field-name", "SendKey / token"), secret);

    const tail = el("p", "ps-tail", "");
    const switchLabel = el("label", "ps-switch");
    const enabledInput = el("input", "ps-enabled");
    enabledInput.type = "checkbox";
    enabledInput.name = "push-enabled";
    switchLabel.append(enabledInput, el("span", "", "开启微信提醒"));

    const note = el(
      "p", "ps-note",
      "微信提醒因连续 5 次发送失败已自动关闭，请检查 Key 后重新填写并开启。",
    );
    note.hidden = true;
    note.setAttribute("role", "status");

    const actions = el("div", "ps-actions");
    const saveButton = el("button", "ps-save", "保存");
    saveButton.type = "button";
    saveButton.addEventListener("click", () => { save(); });
    const testButton = el("button", "ps-test", "发送测试消息");
    testButton.type = "button";
    testButton.disabled = true;
    testButton.addEventListener("click", () => { sendTest(); });
    actions.append(saveButton, testButton);

    const status = el("p", "ps-status", "");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    card.append(channelLabel, secretLabel, tail, switchLabel, note, actions, status);
    root = card;
    refs = {
      card, channel, secret, tail, enabledInput, note,
      save: saveButton, test: testButton, status,
      configured: false, state: null,
    };
    return card;
  }

  function mount(target) {
    generation += 1;
    saving = false;
    testing = false;
    container = target;
    const user = currentUser();
    const card = buildCard();
    container.replaceChildren(card);
    if (!user || user.is_trial) {
      card.hidden = true;
      return Promise.resolve();
    }
    return load();
  }

  function reset() {
    generation += 1;
    saving = false;
    testing = false;
    root = null;
    refs = null;
    container?.replaceChildren();
    container = null;
  }

  window.PushSettings = { configure, mount, reset };

  function configure(next) {
    hooks = next;
  }
})();
