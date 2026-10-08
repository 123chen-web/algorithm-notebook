"use strict";

/* Boss 战：失败 3+ 次的错题变成 Boss，来一场限时挑战。
   对外契约：window.Boss = { configure, load(), mountEntrance(card), reset() }。
   - load(): 渲染 #boss-page 挑战视图（开场 → 逐题挑战 → 结算）。
   - mountEntrance(card): 在首页渲染"本周 Boss 战"入口卡片。
   - reset(): 登出/换账号时清掉进行中的状态。
   数据来自 POST /api/boss/session、/round、/finish；题目详情复用 GET /api/mistakes/{id}。
   计时：每题 60 秒倒计时显示；超时自动按"没能打败"提交（correct=false），
   后端再按提交时的 seconds 做最终判定（>600 秒同样判负）。
   所有用户文本一律 textContent。文案基调是"挑战"不是"审判"。 */
(() => {
  const ROUND_SECONDS = 60; // 每题倒计时（显示用）


  const $ = (selector, root = document) => root.querySelector(selector);
  let generation = 0;
  let hooks = null;
  const ticket = () => ({ generation, epoch: hooks?.getEpoch?.(), userId: hooks?.getUser?.()?.id });
  const alive = (t) => t.generation === generation && t.epoch === hooks?.getEpoch?.() && t.userId === hooks?.getUser?.()?.id;
  function configure(options) { hooks = options; }

  let state = null; // { sessionId, rounds, index, detail, timer, startedAt, wins, losses }

  function nowMs() {
    // 真实浏览器用高精度时钟；node 冒烟环境回退到 Date.now()。
    return typeof performance !== "undefined" && performance.now
      ? performance.now()
      : Date.now();
  }
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function api(path, options = {}) { return hooks.api(path, options); }
  function gotoBossView() {
    // 导航按钮由接线时加上（data-view="boss"）；找不到就什么都不做。
    document.querySelector('[data-view="boss"]')?.click();
  }

  function page() {
    return $("#boss-page");
  }

  /* ---------- 入口卡片（首页"本周 Boss 战"） ---------- */

  function mountEntrance(card) {
    if (!card) return;
    card.replaceChildren();
    card.append(
      node("h3", "boss-entrance-title", "本周 Boss 战"),
      node(
        "p",
        "boss-entrance-desc",
        "失败 3 次以上的错题会化身 Boss。来一场限时挑战，打赢了它就「毕业」啦。"
      ),
    );
    const button = node("button", "boss-entrance-btn primary", "进入挑战");
    button.type = "button";
    button.addEventListener("click", gotoBossView);
    card.append(button);
    card.hidden = false;
  }

  /* ---------- 挑战视图 ---------- */

  function setStatus(text) {
    const box = page();
    if (!box) return;
    let status = $("#boss-status", box);
    if (!status) {
      status = node("p", "", text);
      status.id = "boss-status";
      status.setAttribute("role", "status");
      box.prepend(status);
    } else {
      status.textContent = text;
    }
  }

  function renderShell() {
    const box = page();
    box.replaceChildren();
    const head = node("div", "page-heading");
    const headText = node("div", "");
    headText.append(
      node("h2", "", "Boss 战"),
      node("p", "muted", "曾经绊倒你的题，回来再战一场。赢了就毕业，输了也别怕——它还会等你。"),
    );
    head.append(headText);
    const body = node("div", "boss-body");
    body.id = "boss-body";
    box.append(head, body);
    return body;
  }

  async function startSession(t) {
    const body = $("#boss-body", page());
    body.replaceChildren(node("p", "muted", "正在集结 Boss…"));
    try {
      const data = await api("/api/boss/session", { method: "POST", body: "{}" });
      if (!alive(t)) return;
      if (data.empty) { renderEmpty(body); return; }
      state = { ticket: t, sessionId: data.session_id, rounds: data.rounds, index: 0, detailCache: new Map(), timer: null, wins: 0, losses: 0 };
      renderIntro(body);
    } catch (error) {
      if (!alive(t)) return;
      body.replaceChildren(node("p", "muted", error.message || "挑战场次创建失败，稍后再试。"));
    }
  }

  function renderEmpty(body) {
    body.replaceChildren();
    const card = node("section", "panel boss-card");
    card.append(
      node("h3", "", "本周没有 Boss 在等你"),
      node(
        "p",
        "muted",
        "只有累计失败 3 次以上的错题才会化身 Boss。继续好好复习，保持这个状态！"
      ),
    );
    body.append(card);
  }

  function renderIntro(body) {
    body.replaceChildren();
    const card = node("section", "panel boss-card");
    card.append(
      node("h3", "", `本场挑战：${state.rounds.length} 位 Boss`),
      node("p", "muted", "每题 60 秒倒计时。答对即获胜、该题毕业；超时或答错算惜败，它会留在池里等你下次再战。"),
    );
    const list = node("ol", "boss-list");
    for (const round of state.rounds) {
      list.append(node("li", "", round.title));
    }
    card.append(list);
    const start = node("button", "boss-start-btn primary", "开始挑战");
    start.type = "button";
    start.addEventListener("click", () => {
      if (state) renderRound($("#boss-body", page()));
    });
    card.append(start);
    body.append(card);
  }

  async function fetchDetail(mistakeId) {
    const active = state;
    if (active.detailCache.has(mistakeId)) return active.detailCache.get(mistakeId);
    const detail = await api(`/api/mistakes/${mistakeId}`);
    if (!alive(active.ticket) || state !== active) return null;
    active.detailCache.set(mistakeId, detail);
    return detail;
  }

  function stopTimer() {
    if (state?.timer) {
      window.clearInterval(state.timer);
      state.timer = null;
    }
  }

  function renderRound(body) {
    stopTimer();
    const active = state;
    const round = state.rounds[state.index];
    body.replaceChildren(node("p", "muted", `第 ${state.index + 1} / ${state.rounds.length} 战，正在请出 Boss…`));
    fetchDetail(round.mistake_id).then(
      (detail) => {
        if (!state || state !== active || !alive(active.ticket) || !detail || state.rounds[state.index]?.mistake_id !== round.mistake_id) return;
        paintRound(body, round, detail);
      },
      () => {
        if (state !== active || !alive(active.ticket)) return;
        body.replaceChildren(node("p", "muted", "题目详情加载失败，稍后再试。"));
      },
    );
  }

  function paintRound(body, round, detail) {
    body.replaceChildren();
    const card = node("section", "panel boss-card boss-arena");
    const progress = node("p", "boss-progress", `第 ${state.index + 1} / ${state.rounds.length} 战`);
    const title = node("h3", "boss-title", round.title);
    const code = node("pre", "boss-code", detail.code || "");
    const hint = node(
      "p",
      "boss-hint muted",
      "这是你曾经做错过的题。遮住旧思路，凭现在的自己再做一次——然后诚实地告诉我结果。"
    );
    const timerWrap = node("div", "boss-timer-wrap");
    const timerBar = node("div", "boss-timer-bar");
    const timerText = node("span", "boss-timer-text", `${ROUND_SECONDS}s`);
    timerWrap.append(timerBar, timerText);
    const actions = node("div", "boss-actions");
    const winBtn = node("button", "boss-win-btn primary", "我做对了");
    const loseBtn = node("button", "boss-lose-btn", "这次没做对");
    winBtn.type = "button";
    loseBtn.type = "button";
    winBtn.addEventListener("click", () => submitRound(true));
    loseBtn.addEventListener("click", () => submitRound(false));
    actions.append(winBtn, loseBtn);
    card.append(progress, title, code, hint, timerWrap, actions);
    body.append(card);

    const startedAt = nowMs();
    const deadline = startedAt + ROUND_SECONDS * 1000;
    const tick = () => {
      if (!state) return;
      const remainMs = deadline - nowMs();
      const remain = Math.max(0, Math.ceil(remainMs / 1000));
      timerText.textContent = remain > 0 ? `${remain}s` : "时间到！";
      timerBar.style.width = `${(Math.max(0, remainMs) / (ROUND_SECONDS * 1000)) * 100}%`;
      if (remainMs <= 0) {
        // 60 秒显示倒计时走完：自动按"没能打败"提交，后端按 seconds 最终判定。
        submitRound(false);
      }
    };
    state.timer = window.setInterval(tick, 250);
    state.roundStartedAt = startedAt;
    tick();
  }

  function elapsedSeconds() {
    return Math.max(0, Math.round((nowMs() - state.roundStartedAt) / 1000));
  }

  async function submitRound(correct) {
    if (!state || state.submitting || !alive(state.ticket)) return;
    const active = state;
    active.submitting = true;
    stopTimer();
    const round = active.rounds[active.index];
    const seconds = elapsedSeconds();
    const body = $("#boss-body", page());
    body.replaceChildren(node("p", "muted", "正在判定这一战的结果…"));
    try {
      const data = await api(`/api/boss/session/${active.sessionId}/round`, { method: "POST", body: JSON.stringify({ mistake_id: round.mistake_id, correct, seconds }) });
      if (state !== active || !alive(active.ticket)) return;
      active.wins = data.wins; active.losses = data.losses; active.submitting = false; active.index += 1;
      renderRoundResult(body, data);
    } catch (error) {
      if (state !== active || !alive(active.ticket)) return;
      active.submitting = false;
      body.replaceChildren(node("p", "muted", error.message || "提交失败，请返回重试。"));
    }
  }

  function renderRoundResult(body, data) {
    body.replaceChildren();
    const card = node("section", "panel boss-card");
    if (data.result === "win") {
      card.append(
        node("h3", "boss-result-win", "挑战成功！"),
        node("p", "muted", "这道 Boss 毕业了，它不会再出现在挑战池里。漂亮的一战！"),
      );
    } else {
      card.append(
        node("h3", "boss-result-loss", "这次没能打败它"),
        node(
          "p",
          "muted",
          "别灰心，它还会留在 Boss 池里等你。下次准备更充分一点，再来战过。"
        ),
      );
    }
    card.append(
      node("p", "boss-score", `当前战绩：${data.wins} 胜 ${data.losses} 负`),
    );
    const next = node(
      "button",
      "boss-next-btn primary",
      state.index < state.rounds.length ? "迎战下一位 Boss" : "查看结算"
    );
    next.type = "button";
    next.addEventListener("click", () => {
      if (state.index < state.rounds.length) renderRound($("#boss-body", page()));
      else settle($("#boss-body", page()));
    });
    card.append(next);
    body.append(card);
  }

  async function settle(body) {
    const active = state;
    if (!active || !alive(active.ticket)) return;
    body.replaceChildren(node("p", "muted", "正在结算本场挑战…"));
    try {
      const data = await api(`/api/boss/session/${active.sessionId}/finish`, { method: "POST", body: "{}" });
      if (state !== active || !alive(active.ticket)) return;
      state = null; renderSettlement(body, data, active);
    } catch (error) {
      if (state !== active || !alive(active.ticket)) return;
      body.replaceChildren(node("p", "muted", error.message || "结算失败，稍后再试。"));
    }
  }

  function renderSettlement(body, data, done) {
    body.replaceChildren();
    const card = node("section", "panel boss-card");
    card.append(node("h3", "", "本场挑战结束"));
    const total = data.wins + data.losses;
    const winRate = total > 0 ? Math.round((data.wins / total) * 100) : 0;
    card.append(
      node("p", "boss-final-score", `${data.wins} 胜 ${data.losses} 负`),
      node(
        "p",
        "muted",
        data.wins === total && total > 0
          ? `全胜！胜率 ${winRate}%，${done.rounds.length} 位 Boss 全部毕业，太强了。`
          : `胜率 ${winRate}%。赢下的 Boss 已经毕业，剩下的还会等你——每周都来挑战，越战越强。`
      ),
    );
    const again = node("button", "boss-again-btn", "再来一场");
    again.type = "button";
    again.addEventListener("click", () => startSession(ticket()));
    const back = node("button", "boss-back-btn", "返回首页");
    back.type = "button";
    back.addEventListener("click", () => {
      document.querySelector('[data-view="home"]')?.click();
    });
    card.append(again, back);
    body.append(card);
  }

  /* ---------- 入口 ---------- */

  async function load() {
    generation += 1;
    const t = ticket();
    stopTimer();
    state = null;
    const box = page();
    if (!box) return;
    const body = renderShell();
    setStatus("");
    await startSession(t);
  }

  function reset() {
    generation += 1;
    stopTimer();
    state = null;
  }

  window.Boss = { configure, load, mountEntrance, reset, ROUND_SECONDS };
})();
