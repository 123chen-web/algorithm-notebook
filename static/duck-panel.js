"use strict";

/* 讲给小黄鸭听：错题详情页里的橡皮鸭对话面板。
   对外契约：window.DuckPanel = { configure({ api, getUser, getEpoch }), mount(container, mistake), unmount(), reset() }。
   - 请求只走宿主传入的 api()：POST /api/mistakes/{id}/duck，
     请求体 { turns: [{ role: "user" | "duck", text }], finish: boolean }，
     成功 200 { reply, turns_used, ai_remaining }；429 今日额度用完，503 AI 未配置，502 这次没答好（可重试）；
   - 每个响应都按 登录代次(epoch) + 账号 id + 面板代次(generation) + 错题 id + 请求序号 校验，
     登出换号、关闭面板、切换错题之后才回来的响应一律丢弃；
   - 对话只存在前端内存，刷新或离开页面即丢（界面上写明）；
   - 一切来自用户或服务器的文字都用 textContent；组件自己不直接发请求、不写本地存储。 */
(() => {
  const MAX_CHARS = 600;   // 单条发言上限，与输入框 maxlength 一致
  const MAX_TURNS = 12;    // 与 duck_prompt.MAX_USER_TURNS 一致；每日额度仍单独限制
  const THINKING = "小黄鸭在想…";
  const SUMMING = "小黄鸭在整理总结…";
  const RETRYABLE_FALLBACK = "小黄鸭这次没答好，可以再试一次。";
  const QUOTA_TEXT = "今天的 AI 额度用完了，明天再来找小黄鸭讲吧。";

  let hooks = null;
  let generation = 0; // mount / unmount / reset 都会加一：旧面板的迟到响应全部作废
  let sendSeq = 0;    // 请求序号：只允许最后一次发出的请求写界面
  let panel = null;   // 当前面板状态；null 表示未挂载（组件不用计时器和缓存，reset 只需作废在飞请求）

  /* ---------- 小工具 ---------- */
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function button(label, className) {
    const item = node("button", className, label);
    item.type = "button";
    return item;
  }

  /* ---------- 纯函数（挂在 DuckPanel.helpers 上供测试） ---------- */
  const countUserTurns = (turns) => turns.reduce((total, turn) => total + (turn.role === "user" ? 1 : 0), 0);

  /** N 是正在进行的这一轮；讲满上限后定格并提示去总结。 */
  function roundText(turns) {
    const used = countUserTurns(turns);
    if (used >= MAX_TURNS) return `第 ${MAX_TURNS} / ${MAX_TURNS} 轮 · 已讲完，点“结束并总结”收尾`;
    return `第 ${used + 1} / ${MAX_TURNS} 轮`;
  }

  function remainingText(length) {
    return `还可输入 ${Math.max(0, MAX_CHARS - length)} 字`;
  }

  /** 发往服务端的 turns：已确认的历史加上（可选的）正在发送的用户发言；user 与 duck 严格交替、以 user 开头。 */
  function payloadTurns(turns, draft) {
    const list = turns.map((turn) => ({ role: turn.role, text: turn.text }));
    if (draft) list.push({ role: "user", text: draft });
    return list;
  }

  /** 把 api() 抛出的错误（带 .status / .message）翻译成界面说明；quota 为真时锁定面板到本次挂载结束。 */
  function failureInfo(error) {
    if (error?.status === 429) return { text: QUOTA_TEXT, quota: true, retry: false };
    if (error?.status === 502) return { text: RETRYABLE_FALLBACK, retry: true };
    if (error?.status === 503) return { text: error?.message || "小黄鸭还没有配置好，请稍后再来。", retry: true };
    return { text: error?.message || "请求失败，请检查网络后重试。", retry: true };
  }

  /* ---------- 迟到响应守卫 ---------- */
  function takeTicket() {
    sendSeq += 1;
    return {
      generation,
      sequence: sendSeq,
      epoch: hooks.getEpoch(),
      userId: hooks.getUser()?.id,
      mistakeId: panel?.mistakeId,
    };
  }
  function current(request) {
    return Boolean(panel && hooks && panel.root.isConnected
      && request.generation === generation && request.sequence === sendSeq
      && request.epoch === hooks.getEpoch() && request.userId === hooks.getUser()?.id
      && request.mistakeId === panel.mistakeId);
  }

  /* ---------- 渲染 ---------- */
  function appendBubble(role, text) {
    const item = node("li", `duck-bubble duck-${role}`);
    item.append(
      node("span", "duck-speaker", role === "user" ? "我" : "小黄鸭"),
      node("p", "duck-text", text),
    );
    panel.nodes.log.append(item);
  }

  function showError(info) {
    panel.nodes.errorText.textContent = info.text;
    panel.nodes.retry.hidden = !info.retry;
    panel.nodes.error.hidden = false;
  }
  function hideError() {
    panel.nodes.error.hidden = true;
    panel.nodes.errorText.textContent = "";
    panel.nodes.retry.hidden = true;
  }

  /** 按当前状态同步各控件的可用性与提示文字。 */
  function update() {
    if (!panel) return;
    const { input, send, finish, restart, round, count } = panel.nodes;
    const used = countUserTurns(panel.turns);
    const full = used >= MAX_TURNS;
    input.disabled = panel.sending || panel.finished || panel.quotaOut || full;
    send.disabled = input.disabled || !input.value.trim();
    finish.disabled = panel.sending || panel.finished || panel.quotaOut || used === 0;
    restart.disabled = false;
    round.textContent = roundText(panel.turns);
    count.textContent = remainingText(input.value.slice(0, MAX_CHARS).length);
  }

  /* ---------- 发送 ---------- */
  async function send(kind) { // kind: "chat" | "finish"
    if (!panel || panel.sending || panel.finished || panel.quotaOut) return;
    if (!hooks?.api || !hooks.getUser()) {
      showError({ text: "请先登录，再讲给小黄鸭听。", retry: false });
      return;
    }
    let draft = "";
    if (kind === "chat") {
      draft = panel.nodes.input.value.slice(0, MAX_CHARS);
      if (!draft.trim()) {
        panel.nodes.status.textContent = "先写点什么再发送。";
        return;
      }
      if (countUserTurns(panel.turns) >= MAX_TURNS) return;
    }
    const request = takeTicket();
    panel.sending = true;
    panel.pending = { kind, draft };
    hideError();
    panel.nodes.status.textContent = kind === "finish" ? SUMMING : THINKING;
    update();
    try {
      const data = await hooks.api(`/api/mistakes/${panel.mistakeId}/duck`, {
        method: "POST",
        body: JSON.stringify({
          turns: payloadTurns(panel.turns, kind === "chat" ? draft : ""),
          finish: kind === "finish",
        }),
      });
      if (!current(request)) return; // 登出换号 / 关闭面板 / 切换错题之后才回来：丢弃
      const reply = typeof data?.reply === "string" ? data.reply.trim() : "";
      if (!reply) throw Object.assign(new Error(RETRYABLE_FALLBACK), { status: 502 });
      panel.pending = null;
      if (kind === "chat") {
        panel.turns.push({ role: "user", text: draft }, { role: "duck", text: reply });
        panel.nodes.input.value = "";
        appendBubble("user", draft);
        appendBubble("duck", reply);
        panel.nodes.status.textContent = `小黄鸭：${reply}`; // aria-live 播报新回复
      } else {
        panel.finished = true;
        panel.nodes.summary.hidden = false;
        panel.nodes.summaryText.textContent = reply;
        panel.nodes.status.textContent = `小黄鸭的总结：${reply}`;
      }
    } catch (error) {
      if (!current(request)) return;
      const info = failureInfo(error);
      if (info.quota) panel.quotaOut = true; // 429：今天不用等了，锁定到下次挂载
      showError(info);
      panel.nodes.status.textContent = "";
    } finally {
      if (current(request)) {
        panel.sending = false;
        update();
      }
    }
  }

  /** 清空对话重来；429 的额度锁定保留（额度是服务端按天算的，清空对话不会恢复）。 */
  function restart() {
    if (!panel) return;
    sendSeq += 1; // 在飞的请求作废，迟到响应不再写入
    panel.turns = [];
    panel.sending = false;
    panel.finished = false;
    panel.pending = null;
    panel.nodes.log.replaceChildren();
    panel.nodes.summary.hidden = true;
    panel.nodes.summaryText.textContent = "";
    panel.nodes.input.value = "";
    hideError();
    panel.nodes.status.textContent = "已清空，重新开始。";
    update();
  }

  /* ---------- 挂载 / 卸载 ---------- */
  function mount(container, mistake) {
    unmount(); // 切换错题或重复挂载：旧面板连同它的迟到响应一起作废
    if (!container || !mistake || mistake.id === undefined || mistake.id === null) return false;
    generation += 1;

    const root = node("section", "duck-panel");
    root.setAttribute("aria-labelledby", "duck-panel-title");

    const head = node("div", "duck-head");
    head.append(
      node("h3", "duck-title", "讲给小黄鸭听"),
      node("p", "duck-round", ""),
    );
    head.querySelector(".duck-title").id = "duck-panel-title";
    const note = node("p", "duck-note",
      `把《${String(mistake.title ?? "").slice(0, 80)}》讲给小黄鸭听，讲不清楚的地方它会追问。对话只存在这个页面里，刷新或离开就没了。`);

    const log = node("ol", "duck-log");
    log.setAttribute("aria-label", "与小黄鸭的对话");

    const status = node("p", "duck-status", "");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");

    const error = node("div", "duck-error");
    error.setAttribute("role", "alert");
    error.hidden = true;
    const errorText = node("p", "duck-error-text", "");
    const retry = button("重试", "duck-retry");
    retry.hidden = true;
    error.append(errorText, retry);

    const summary = node("div", "duck-summary");
    summary.hidden = true;
    const summaryText = node("p", "duck-summary-text", "");
    summary.append(node("h4", "duck-summary-title", "小黄鸭的总结"), summaryText);

    const composer = node("div", "duck-composer");
    const input = node("textarea", "duck-input");
    input.id = "duck-panel-input";
    input.maxLength = MAX_CHARS;
    input.rows = 3;
    input.setAttribute("aria-label", "讲给小黄鸭听的内容");
    input.setAttribute("placeholder", "用自己的话讲：这题错在哪、正确的思路是什么…");
    const row = node("div", "duck-row");
    const count = node("span", "duck-count", remainingText(0));
    const actions = node("div", "duck-actions");
    const sendBtn = button("发送", "primary duck-send");
    const finishBtn = button("结束并总结", "duck-finish");
    const restartBtn = button("重新开始", "duck-restart");
    actions.append(sendBtn, finishBtn, restartBtn);
    row.append(count, actions);
    const hint = node("p", "duck-hint", `Enter 发送，Shift+Enter 换行。最多 ${MAX_TURNS} 轮；额度规则见 `);
    const billing = node("a", "", "AI 额度与计费");
    billing.href = "/static/ai-billing.html";
    hint.append(billing);
    composer.append(input, row, hint);

    root.append(head, note, log, status, error, summary, composer);
    container.replaceChildren(root);

    panel = {
      root,
      mistakeId: mistake.id,
      turns: [],
      sending: false,
      finished: false,
      quotaOut: false,
      pending: null,
      nodes: {
        input, send: sendBtn, finish: finishBtn, restart: restartBtn,
        round: head.querySelector(".duck-round"), count, log, status,
        error, errorText, retry, summary, summaryText,
      },
    };

    let composing = false; // 输入法组合输入期间 Enter 不发送
    input.addEventListener("compositionstart", () => { composing = true; });
    input.addEventListener("compositionend", () => { composing = false; });
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing || composing) return;
      event.preventDefault();
      send("chat");
    });
    input.addEventListener("input", update);
    sendBtn.addEventListener("click", () => send("chat"));
    finishBtn.addEventListener("click", () => send("finish"));
    retry.addEventListener("click", () => { if (panel?.pending) send(panel.pending.kind); });
    restartBtn.addEventListener("click", restart);

    update();
    return true;
  }

  function unmount() {
    if (!panel) return;
    generation += 1; // 在飞请求的响应回来之后也找不到活着的面板
    sendSeq += 1;
    panel.root.remove();
    panel = null;
  }

  function reset() {
    unmount(); // 组件没有计时器和缓存；作废在飞请求并摘掉面板即可
  }

  window.DuckPanel = {
    configure(options) { hooks = options || null; },
    mount,
    unmount,
    reset,
    helpers: { countUserTurns, roundText, remainingText, payloadTurns, failureInfo },
  };
})();
