"use strict";

/* 总览页的「今日一签」：像撕日历一样的今日宜忌。
   对外契约：window.SignCard = { configure({ api, getUser, getEpoch }), mount(container), reset() }。
   - 内容来自 /api/today-sign（用户自己的数据，不联网、不用 AI）；
   - 第一次点“开签”有盖章 + 展开动画，当天再打开直接是展开状态（记在 localStorage，读写失败也能用）；
   - 请求失败时静默隐藏卡片；晚到响应按登录代次、账号、请求序号丢弃；
   - 一切来自服务器的文字都用 textContent。 */
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

  function storageKey(request, date) {
    return `oy-sign-open:${request.userId}:${date}`;
  }

  function wasOpened(key) {
    try { return localStorage.getItem(key) === "1"; } catch { return false; }
  }

  function rememberOpened(key) {
    try { localStorage.setItem(key, "1"); } catch { /* 存储不可用也不影响使用 */ }
  }

  function column(kind, label, items) {
    const section = node("section", `sign-col sign-${kind}`);
    const mark = node("span", "sign-mark", label);
    mark.setAttribute("aria-hidden", "true");
    const list = node("ul", "sign-list");
    list.setAttribute("aria-label", label === "宜" ? "今日宜" : "今日忌");
    for (const item of items) {
      const row = node("li", "sign-item");
      const details = node("details", "sign-details");
      const summary = node("summary", "sign-text", item.text);
      details.append(summary, node("p", "sign-why", item.detail));
      row.append(details);
      list.append(row);
    }
    section.append(mark, list);
    return section;
  }

  function render(data, request) {
    const key = storageKey(request, data.date);
    const card = node("section", "sign-card");
    card.setAttribute("aria-labelledby", "sign-title");
    card.setAttribute("data-tilt", ""); // 手感层：卡片倾斜 + 反光（feel.js 接管）

    const head = node("div", "sign-head");
    const date = node("div", "sign-date");
    date.append(
      node("span", "sign-day", String(data.day)),
      node("span", "sign-meta", `${data.month} 月 · ${data.weekday}`),
    );
    const title = node("h3", "sign-title", "今日一签");
    title.id = "sign-title";
    const hint = node("p", "sign-hint", "点一下印章，看看今天宜什么、忌什么");
    const textWrap = node("div", "sign-headtext");
    textWrap.append(title, hint);

    const seal = node("button", "sign-seal");
    seal.type = "button";
    seal.setAttribute("aria-expanded", "false");
    seal.append(node("span", "sign-seal-char", "签"));
    seal.setAttribute("aria-label", "开签：查看今日宜忌");
    head.append(date, textWrap, seal);

    const body = node("div", "sign-body");
    const inner = node("div", "sign-inner");
    inner.setAttribute("role", "region");
    inner.setAttribute("aria-label", "今日宜忌内容");
    const cols = node("div", "sign-cols");
    cols.append(column("yi", "宜", data.yi || []), column("ji", "忌", data.ji || []));
    inner.append(
      cols,
      node("p", "sign-quote", `“${data.quote}”`),
      node("p", "sign-note", data.note),
    );
    body.append(inner);
    card.append(head, body);

    function setOpen(open, { animate }) {
      card.dataset.open = open ? "true" : "false";
      card.dataset.animate = animate ? "true" : "false";
      seal.setAttribute("aria-expanded", String(open));
      hint.textContent = open ? "再点一下印章可以收起" : "点一下印章，看看今天宜什么、忌什么";
      inner.inert = !open;
    }

    setOpen(wasOpened(key), { animate: false });
    seal.addEventListener("click", () => {
      const next = card.dataset.open !== "true";
      setOpen(next, { animate: true });
      if (next) rememberOpened(key);
    });
    return card;
  }

  async function mount(target) {
    container = target;
    if (!container || !hooks) return;
    const mine = ++generation;
    const request = ticket();
    container.replaceChildren();
    window.Feel?.skeletonShow?.(container, 2); // 手感层：骨架屏代替空白等待
    try {
      const data = await hooks.api("/api/today-sign");
      if (mine !== generation || !current(request)) return;
      window.Feel?.skeletonHide?.(container);
      if (!data || !Array.isArray(data.yi) || !Array.isArray(data.ji) || typeof data.date !== "string") {
        container.replaceChildren();
        return;
      }
      container.replaceChildren(render(data, request));
    } catch {
      window.Feel?.skeletonHide?.(container);
      if (mine === generation && container) container.replaceChildren();
    }
  }

  function reset() {
    generation += 1;
    if (container) container.replaceChildren();
  }

  window.SignCard = {
    configure(next) { hooks = next; },
    mount,
    reset,
  };
})();
