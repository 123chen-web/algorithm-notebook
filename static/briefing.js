"use strict";

/* 总览页的「今日一读」：把论文和技术新闻做成可以横向翻看的小卡片，每张一句话中文摘要。
   对外契约：window.BriefingCard = { configure({ api, getUser, getEpoch }), mount(container), reset() }。
   - 内容来自 /api/briefing/today（服务器每天抓好的缓存，请求时不联网）；没有内容或请求失败就不显示；
   - “已读”只记在本机 localStorage（读写失败也能用），读完不催促、不惩罚；
   - 一切来自服务器的文字都用 textContent；链接只放 http(s)，新标签页打开并带 noopener noreferrer；
   - 晚到响应按登录代次、账号、请求序号丢弃。 */
(() => {
  let hooks = null;
  let container = null;
  let generation = 0;
  let sequence = 0;

  function ticket() {
    sequence += 1;
    return { sequence, generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }

  function current(request) {
    const user = hooks?.getUser();
    return Boolean(hooks && user && request.generation === generation
      && request.sequence === sequence
      && request.epoch === hooks.getEpoch() && request.userId === user.id);
  }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function readKey(request, date) {
    return `oy-briefing-read:${request.userId}:${date}`;
  }

  function loadRead(key) {
    try {
      const raw = JSON.parse(localStorage.getItem(key) || "[]");
      return new Set(Array.isArray(raw) ? raw.filter((value) => Number.isInteger(value)) : []);
    } catch {
      return new Set();
    }
  }

  function saveRead(key, set) {
    try { localStorage.setItem(key, JSON.stringify([...set])); } catch { /* 存储不可用也不影响使用 */ }
  }

  function safeUrl(value) {
    try {
      const url = new URL(String(value));
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : "";
    } catch {
      return "";
    }
  }

  function formatDate(iso) {
    const match = /^\d{4}-(\d{2})-(\d{2})$/.exec(String(iso || ""));
    return match ? `${Number(match[1])} 月 ${Number(match[2])} 日` : "";
  }

  function render(data, request) {
    const key = readKey(request, data.date);
    const read = loadRead(key);
    const items = data.items.filter((item) => item && typeof item.title === "string" && safeUrl(item.url));
    if (!items.length) return null;

    const card = node("section", "bf-card");
    card.setAttribute("aria-labelledby", "bf-title");

    const head = node("div", "bf-head");
    const heading = node("div", "bf-headtext");
    heading.append(node("p", "bf-eyebrow", "TODAY'S READ"));
    const title = node("h3", "bf-title", `今日一读 · ${formatDate(data.date)}`);
    title.id = "bf-title";
    heading.append(title);
    const progress = node("p", "bf-progress");
    progress.setAttribute("role", "status");
    progress.setAttribute("aria-live", "polite");

    const nav = node("div", "bf-nav");
    const prev = node("button", "bf-arrow", "‹");
    const next = node("button", "bf-arrow", "›");
    for (const [button, label] of [[prev, "上一张"], [next, "下一张"]]) {
      button.type = "button";
      button.setAttribute("aria-label", label);
    }
    nav.append(prev, next);
    head.append(heading, nav);

    const track = node("ul", "bf-track");
    track.tabIndex = 0;
    track.setAttribute("aria-label", "今日简报，可左右滑动或用左右方向键翻看");
    const cards = [];

    function paint() {
      const done = items.filter((item) => read.has(item.id)).length;
      progress.textContent = done === items.length
        ? `今天的 ${items.length} 张都看完了`
        : `已看 ${done} / ${items.length}`;
      for (const entry of cards) entry.li.classList.toggle("is-read", read.has(entry.id));
    }

    for (const item of items) {
      const li = node("li", "bf-item");
      li.dataset.kind = item.kind === "paper" ? "paper" : "news";
      const source = node("span", "bf-source", item.source || (item.kind === "paper" ? "arXiv" : "Hacker News"));
      const summary = node("p", "bf-summary", item.summary || item.title);
      const original = node("p", "bf-original", item.title);
      const actions = node("div", "bf-actions");
      const link = node("a", "bf-link", "阅读原文 ↗");
      link.href = safeUrl(item.url);
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      const done = node("button", "bf-read", "标为已看");
      done.type = "button";
      done.addEventListener("click", () => {
        if (read.has(item.id)) read.delete(item.id); else read.add(item.id);
        done.textContent = read.has(item.id) ? "取消已看" : "标为已看";
        saveRead(key, read);
        paint();
      });
      link.addEventListener("click", () => {
        read.add(item.id);
        done.textContent = "取消已看";
        saveRead(key, read);
        paint();
      });
      if (read.has(item.id)) done.textContent = "取消已看";
      actions.append(link, done);
      li.append(source, summary, original, actions);
      track.append(li);
      cards.push({ id: item.id, li });
    }

    const step = () => Math.max(track.clientWidth * 0.8, 240);
    prev.addEventListener("click", () => track.scrollBy({ left: -step(), behavior: "smooth" }));
    next.addEventListener("click", () => track.scrollBy({ left: step(), behavior: "smooth" }));
    track.addEventListener("keydown", (event) => {
      if (event.key === "ArrowRight") { event.preventDefault(); next.click(); }
      if (event.key === "ArrowLeft") { event.preventDefault(); prev.click(); }
    });

    const foot = node("p", "bf-foot", "来自 arXiv 与 Hacker News，一句话摘要由 AI 概括，仅供开阔眼界。");
    card.append(head, track, progress, foot);
    paint();
    return card;
  }

  async function mount(target) {
    container = target;
    if (!container || !hooks) return;
    const mine = ++generation;
    const request = ticket();
    container.replaceChildren();
    try {
      const data = await hooks.api("/api/briefing/today");
      if (mine !== generation || !current(request)) return;
      if (!data || typeof data.date !== "string" || !Array.isArray(data.items)) return;
      const card = render(data, request);
      if (card) container.replaceChildren(card);
    } catch {
      if (mine === generation && container) container.replaceChildren();
    }
  }

  function reset() {
    generation += 1;
    if (container) container.replaceChildren();
  }

  window.BriefingCard = {
    configure(next) { hooks = next; },
    mount,
    reset,
  };
})();
