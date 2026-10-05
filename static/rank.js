"use strict";

/* 榜单页的三个新区块：今日一条、昨日之星、本周热门题目，账号菜单里的“参与公开榜单”开关，
   以及总览页趋势区下面的“昨日之星”小卡片（Rank.mountCard）。
   请求走宿主传入的 api()；每个响应都按登录代次 / 页面 / 请求序号校验，晚到的响应直接丢弃；
   一切来自服务器的文字都用 textContent，链接只允许 http(s) 且带 rel="noopener noreferrer" target="_blank"。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const yesterdayRoot = $("#rank-yesterday");
  const hotRoot = $("#rank-hot");
  const noticeRoot = $("#rank-notice");
  if (!yesterdayRoot || !hotRoot || !noticeRoot) return;

  // 与服务端 hot_problems.ALLOWED_LINK_HOSTS 一致：热门题目只允许打开这些站点。
  const HOT_HOSTS = ["leetcode.cn", "www.luogu.com.cn", "www.nowcoder.com", "codeforces.com", "atcoder.jp"];
  const SECTIONS = ["notice", "yesterday", "hot"];

  let hooks = null;
  let generation = 0;
  const sequence = { notice: 0, yesterday: 0, hot: 0, setting: 0 };
  let settingPending = false;

  /* ---------- 纯函数（也挂在 window.Rank.helpers 上供测试） ---------- */

  /** 只接受 http / https、有主机名、不带账号密码的链接；否则返回 null。 */
  function safeLink(value, allowedHosts = null) {
    if (typeof value !== "string" || !value || value.length > 600 || /\s/.test(value)) return null;
    let url;
    try {
      url = new window.URL(value);
    } catch {
      return null;
    }
    if ((url.protocol !== "http:" && url.protocol !== "https:") || url.username || url.password || !url.hostname) return null;
    if (allowedHosts && !allowedHosts.includes(url.hostname)) return null;
    return url.href;
  }

  /** “你昨天……”那句话；按账号状态选不同的说法。 */
  function meText(me) {
    const count = Number(me?.count) || 0;
    if (me?.is_trial) return `体验账号不参与榜单。你昨天复习了 ${count} 次。`;
    if (me?.opted_out) return `你昨天复习了 ${count} 次。你已设置不参与公开榜单，所以不会出现在榜单上。`;
    if (me?.in_top) return `你昨天复习了 ${count} 次，排第 ${me.rank} 名。`;
    const gap = Number(me?.gap) || 1;
    return `你昨天复习了 ${count} 次，距离前十还差 ${gap} 次。`;
  }

  function rangeText(from, to) {
    return from === to ? from : `${from} 至 ${to}`;
  }

  /* ---------- 加载与隔离 ---------- */

  function ticket(name) {
    sequence[name] += 1;
    return { name, sequence: sequence[name], generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }

  function current(request, view = "leaderboard") {
    const user = hooks?.getUser();
    if (!hooks || !user) return false;
    const common = request.epoch === hooks.getEpoch() && request.userId === user.id;
    if (view !== "leaderboard") {
      // 总览小卡片有自己的代次/序号，与榜单页三块互不串扰；视图守卫同样必过。
      return common && request.generation === cardGeneration && request.sequence === cardSequence[request.name]
        && hooks.getView() === view;
    }
    return common && request.generation === generation && request.sequence === sequence[request.name]
      && hooks.getView() === "leaderboard";
  }

  function setBusy(root, busy) {
    root.setAttribute("aria-busy", busy ? "true" : "false");
  }

  function failure(error) {
    return error?.message || "加载失败，请检查网络后重试。";
  }

  function fallbackAvatar(name) {
    const node = document.createElement("span");
    node.className = "avatar avatar-sm";
    node.setAttribute("aria-hidden", "true");
    node.textContent = (name || "?").slice(0, 1).toUpperCase();
    return node;
  }

  function avatarFor(entry) {
    if (typeof hooks?.avatar === "function") {
      const node = hooks.avatar(entry.user_id, entry.username, entry.avatar_version, { hasAvatar: entry.avatar_version > 0 });
      node.classList.add("rank-avatar");
      return node;
    }
    return fallbackAvatar(entry.username);
  }

  function text(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    return node;
  }

  function renderYesterday(data) {
    $("#rank-yesterday-date").textContent = data.day;
    const list = $("#rank-yesterday-list");
    list.replaceChildren();
    for (const entry of data.entries) {
      const row = document.createElement("li");
      row.className = "rank-row";
      if (entry.rank <= 3) row.classList.add("is-top");
      if (entry.rank === 1) row.classList.add("is-first");
      if (entry.is_me) row.classList.add("is-me");
      const number = text("span", "rank-no", String(entry.rank));
      number.setAttribute("aria-label", `第 ${entry.rank} 名`);
      const who = document.createElement("span");
      who.className = "rank-who";
      who.append(text("span", "rank-name", entry.username), text("span", "rank-praise", entry.praise));
      const stats = document.createElement("span");
      stats.className = "rank-stats";
      const count = text("span", "rank-count", "");
      count.append(text("strong", "", String(entry.count)), " 次");
      stats.append(count, text("span", "rank-streak", `连续 ${entry.streak_days} 天`));
      row.append(number, avatarFor(entry), who, stats);
      list.append(row);
    }
    $("#rank-yesterday-status").textContent = data.entries.length ? "" : "昨天还没有人上榜。今天复习一次，明天榜上就有你。";
    $("#rank-yesterday-me").textContent = meText(data.me);
  }

  function renderHot(data) {
    $("#rank-hot-range").textContent = rangeText(data.from, data.to);
    $("#rank-hot-min").textContent = String(data.min_users);
    const list = $("#rank-hot-list");
    list.replaceChildren();
    for (const entry of data.entries) {
      const row = document.createElement("li");
      row.className = "rank-hot-row";
      const href = safeLink(entry.url, HOT_HOSTS);
      const body = href ? document.createElement("a") : document.createElement("span");
      body.className = "rank-hot-link";
      if (href) {
        body.setAttribute("href", href);
        body.setAttribute("rel", "noopener noreferrer");
        body.setAttribute("target", "_blank");
      }
      body.append(
        text("span", "rank-source", entry.source_label),
        text("span", "rank-hot-name", entry.name),
        text("span", "rank-hot-users", `${entry.users} 人`),
      );
      row.append(body);
      list.append(row);
    }
    $("#rank-hot-status").textContent = data.entries.length ? ""
      : `这一周还没有题目达到 ${data.min_users} 人的门槛。`;
  }

  function renderNotice(data) {
    const notice = data?.notice;
    const link = $("#rank-notice-link");
    if (!notice || typeof notice.text !== "string" || !notice.text) {
      noticeRoot.hidden = true;
      $("#rank-notice-text").textContent = "";
      link.hidden = true;
      link.removeAttribute("href");
      return;
    }
    $("#rank-notice-text").textContent = notice.text;
    const href = notice.link ? safeLink(notice.link) : null;
    if (href) {
      link.setAttribute("href", href);
      link.setAttribute("rel", "noopener noreferrer");
      link.setAttribute("target", "_blank");
      link.hidden = false;
    } else {
      link.hidden = true;
      link.removeAttribute("href");
    }
    noticeRoot.hidden = false;
  }

  async function loadSection(name, path, render, root, statusId, retryId, loadingText) {
    const request = ticket(name);
    const status = $(statusId);
    const retry = $(retryId);
    if (root) setBusy(root, true);
    if (status) status.textContent = loadingText;
    if (retry) retry.hidden = true;
    try {
      const data = await hooks.api(path);
      if (!current(request)) return false;
      render(data);
      return true;
    } catch (error) {
      if (!current(request)) return false;
      if (status) status.textContent = failure(error);
      if (retry) retry.hidden = false;
      return false;
    } finally {
      if (root && current(request)) setBusy(root, false);
    }
  }

  function loadYesterday() {
    return loadSection("yesterday", "/api/rank/yesterday", renderYesterday, yesterdayRoot,
      "#rank-yesterday-status", "#rank-yesterday-retry", "正在加载昨日之星…");
  }

  function loadHot() {
    return loadSection("hot", "/api/rank/hot-problems", renderHot, hotRoot,
      "#rank-hot-status", "#rank-hot-retry", "正在加载本周热门题目…");
  }

  async function loadNotice() {
    const request = ticket("notice");
    try {
      const data = await hooks.api("/api/rank/notice");
      if (current(request)) renderNotice(data);
    } catch {
      // “今日一条”只是点缀：失败就不显示，不打扰榜单本身。
      if (current(request)) renderNotice(null);
    }
  }

  function load() {
    if (!hooks || !hooks.getUser()) return Promise.resolve(false);
    return Promise.all([loadNotice(), loadYesterday(), loadHot()]).then(() => true);
  }

  function clear() {
    renderNotice(null);
    $("#rank-yesterday-list").replaceChildren();
    $("#rank-yesterday-me").textContent = "";
    $("#rank-yesterday-status").textContent = "";
    $("#rank-yesterday-date").textContent = "—";
    $("#rank-hot-list").replaceChildren();
    $("#rank-hot-status").textContent = "";
    $("#rank-hot-range").textContent = "—";
    $("#rank-yesterday-retry").hidden = true;
    $("#rank-hot-retry").hidden = true;
    setBusy(yesterdayRoot, false);
    setBusy(hotRoot, false);
  }

  /* ---------- 账号菜单里的“参与公开榜单”开关 ---------- */

  const checkbox = $("#account-public-rank");
  const settingLabel = $("#account-public-rank-label");
  const settingNote = $("#account-public-rank-status");
  const NOTE = "关闭后，你不会出现在“昨日之星”“本周热门题目”和榜单里。";

  function syncSetting(user) {
    if (!checkbox || !settingLabel) return;
    if (settingPending && user) return; // 正在保存时，别让一次资料刷新把开关改回旧值
    checkbox.disabled = false;
    settingLabel.hidden = !user || Boolean(user.is_trial);
    checkbox.checked = !user?.public_rank_opt_out;
    if (settingNote) settingNote.textContent = NOTE;
  }

  async function changeSetting() {
    const wanted = checkbox.checked;
    if (!hooks || !hooks.getUser() || settingPending) {
      checkbox.checked = !wanted;
      return;
    }
    const user = hooks.getUser();
    const request = { sequence: (sequence.setting += 1), epoch: hooks.getEpoch(), userId: user.id };
    const stillCurrent = () => request.sequence === sequence.setting && request.epoch === hooks.getEpoch()
      && hooks.getUser()?.id === request.userId;
    settingPending = true;
    checkbox.disabled = true;
    settingNote.textContent = "正在保存…";
    try {
      await hooks.api("/api/me/public-rank", { method: "PUT", body: JSON.stringify({ participate: wanted }) });
      if (!stillCurrent()) return;
      hooks.getUser().public_rank_opt_out = !wanted;
      settingNote.textContent = wanted ? "已加入公开榜单。" : "已退出公开榜单，你不会再出现在榜单上。";
      if (hooks.getView() === "leaderboard") {
        // 宿主能刷新整页（含原来的连续打卡榜）就交给宿主；否则只刷新这里的区块。
        if (typeof hooks.refreshPage === "function") hooks.refreshPage();
        else load();
      }
    } catch (error) {
      if (!stillCurrent()) return;
      checkbox.checked = !wanted;
      settingNote.textContent = `没有保存成功：${failure(error)}`;
    } finally {
      if (stillCurrent()) {
        settingPending = false;
        checkbox.disabled = false;
      }
    }
  }

  /* ---------- 总览页趋势区下面的“昨日之星”小卡片 ---------- */

  const CARD_GUIDE = "昨天还没有榜单数据。今天复习一次，明天榜上就有你。";
  let cardGeneration = 0;
  const cardSequence = { card: 0 };

  function cardText(me) {
    const count = Number(me?.count) || 0;
    if (me?.opted_out) return "你已选择不参与公开榜单";
    if (me?.is_trial) return `体验账号不参与榜单，昨天复习 ${count} 次。`;
    if (me?.in_top) return `你昨天排第 ${Number(me.rank)}，复习 ${count} 次`;
    const gap = Number(me?.gap) || 1;
    return `昨天你复习了 ${count} 次，距离前十还差 ${gap} 次`;
  }

  function buildCard(container) {
    if (container.querySelector("#ov-yesterday-card-title")) return;
    const title = text("h3", "rank-mini-title", "昨日之星");
    title.id = "ov-yesterday-card-title";
    const sentence = text("p", "rank-mini-text", CARD_GUIDE);
    sentence.id = "ov-yesterday-card-text";
    const link = document.createElement("button");
    link.type = "button";
    link.className = "rank-mini-link";
    link.id = "ov-yesterday-card-link";
    link.dataset.view = "leaderboard";
    link.setAttribute("data-view", "leaderboard");
    link.textContent = "查看榜单";
    container.append(title, sentence, link);
  }

  async function mountCard(container) {
    if (!container) return;
    if (!hooks || !hooks.getUser()) { container.hidden = true; return; }
    buildCard(container);
    container.hidden = false;
    const sentence = container.querySelector("#ov-yesterday-card-text");
    if (sentence) sentence.textContent = CARD_GUIDE;
    const user = hooks.getUser();
    const request = {
      name: "card",
      sequence: (cardSequence.card += 1),
      generation: cardGeneration,
      epoch: hooks.getEpoch(),
      userId: user?.id,
    };
    let data = null;
    try {
      data = await hooks.api("/api/rank/yesterday");
    } catch {
      if (!current(request, "home")) return;
      if (sentence) sentence.textContent = CARD_GUIDE;
      return;
    }
    if (!current(request, "home")) return;
    if (sentence) sentence.textContent = cardText(data?.me);
  }

  function reset() {
    generation += 1;
    cardGeneration += 1;
    cardSequence.card += 1;
    for (const name of [...SECTIONS, "setting"]) sequence[name] += 1;
    settingPending = false;
    clear();
    const cardRoot = document.querySelector("#ov-yesterday-card");
    if (cardRoot) { cardRoot.hidden = true; cardRoot.replaceChildren(); }
    syncSetting(null);
  }

  checkbox?.addEventListener("change", changeSetting);
  $("#rank-yesterday-retry").addEventListener("click", () => { if (hooks?.getUser()) loadYesterday(); });
  $("#rank-hot-retry").addEventListener("click", () => { if (hooks?.getUser()) loadHot(); });
  document.addEventListener("app:view-changed", () => { generation += 1; });

  window.Rank = {
    configure: (value) => { hooks = value; },
    load,
    reset,
    syncSetting,
    mountCard,
    helpers: { safeLink, meText, rangeText, cardText },
  };
})();
