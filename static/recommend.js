"use strict";

/* 总览页的「今日推荐题」卡：按用户未掌握的错题标签，从 Codeforces 题库缓存里
   推荐 3 道难度合适的题（只链原站，不保存题面）。
   对外契约：window.RecommendCard = { configure({ api, getUser, getEpoch }), mount(container), reset() }。
   - 请求走宿主传入的 api()；每个响应都按登录代次（getEpoch()）、账号（getUser().id）和请求序号校验，
     登出、换号、reset() 之后晚到的响应一律丢弃；
   - 一切来自服务器的文字都用 textContent；请求失败时静默隐藏卡片，不打扰总览页。 */
(() => {
  let hooks = null;
  let container = null;
  let mode = "idle"; // idle | loading | ready | empty（hidden 时直接清空容器）
  let items = [];
  let hint = "";
  let notice = ""; // 标记"做完了 / 不感兴趣"失败时的提示
  let busyKey = ""; // 正在提交状态的题目 key，防重复点击
  let generation = 0;
  let sequence = 0;

  /* ---------- 迟到响应守卫 ---------- */

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

  /* ---------- DOM ---------- */

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function button(label, className, onClick) {
    const item = node("button", className, label);
    item.type = "button";
    item.addEventListener("click", onClick);
    return item;
  }

  function itemKey(item) {
    return `${item.contest_id}${item.idx}`;
  }

  function head() {
    const wrap = node("div", "rc-head");
    const eyebrow = node("p", "rc-eyebrow", "RECOMMEND");
    eyebrow.setAttribute("aria-hidden", "true");
    const title = node("h3", "rc-title", "今日推荐题");
    title.id = "rc-title";
    wrap.append(eyebrow, title);
    return wrap;
  }

  function itemRow(item) {
    const key = itemKey(item);
    const row = node("li", "rc-item");

    const headLine = node("div", "rc-item-head");
    headLine.append(node("span", "rc-item-name", String(item.name ?? "")));
    if (item.rating !== null && item.rating !== undefined) {
      headLine.append(node("span", "rc-item-rating", `难度 ${item.rating}`));
    }
    row.append(headLine);

    const tags = Array.isArray(item.tags) ? item.tags.join(" · ") : "";
    if (tags) row.append(node("p", "rc-item-tags", `标签：${tags}`));
    if (item.reason) row.append(node("p", "rc-item-reason", String(item.reason)));

    const actions = node("div", "rc-item-actions");
    const link = node("a", "rc-item-link", "去做题");
    link.setAttribute("href", String(item.url ?? ""));
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
    actions.append(link);

    const busy = busyKey === key;
    if (item.state === "done") {
      actions.append(node("span", "rc-item-state", "已完成"));
    } else if (item.state === "dismissed") {
      actions.append(node("span", "rc-item-state", "已忽略"));
    } else {
      const done = button("做完了", "rc-done primary", () => mark(item, "done"));
      const dismiss = button("不感兴趣", "rc-dismiss", () => mark(item, "dismissed"));
      if (busy) {
        done.disabled = true;
        dismiss.disabled = true;
      }
      actions.append(done, dismiss);
    }
    row.append(actions);
    return row;
  }

  function render() {
    if (!container) return;
    const card = node("section", "rc-card");
    card.setAttribute("aria-labelledby", "rc-title");
    card.append(head());
    if (mode === "loading") {
      card.append(node("p", "rc-note", "正在挑选题目…"));
    } else if (mode === "ready") {
      const list = node("ul", "rc-list");
      for (const item of items) list.append(itemRow(item));
      card.append(list);
      if (notice) card.append(node("p", "rc-error", notice));
      card.append(node("p", "rc-footnote", "题目来自 Codeforces，点击跳转原站；欧叶OY 不保存题面。"));
    } else if (mode === "empty") {
      card.append(node("p", "rc-note", hint || "暂时没有推荐。"));
    }
    container.replaceChildren(card);
  }

  /* ---------- 请求 ---------- */

  async function load() {
    if (!hooks || !hooks.getUser() || !container) return false;
    const request = ticket();
    mode = "loading";
    notice = "";
    render();
    try {
      const data = await hooks.api("/api/recommend");
      if (!current(request)) return false;
      const list = Array.isArray(data?.items) ? data.items : [];
      hint = typeof data?.hint === "string" ? data.hint : "";
      if (list.length > 0) {
        items = list;
        mode = "ready";
      } else {
        items = [];
        mode = "empty";
      }
      render();
      return true;
    } catch (error) {
      if (!current(request)) return false;
      // 失败静默隐藏卡片，不打扰总览页。
      container.replaceChildren();
      return false;
    }
  }

  async function mark(item, state) {
    const key = itemKey(item);
    if (busyKey) return; // 防重复提交：先占位再发请求
    busyKey = key;
    notice = "";
    render();
    const request = ticket();
    try {
      await hooks.api(`/api/recommend/${item.contest_id}/${item.idx}`, {
        method: "POST",
        body: JSON.stringify({ state }),
      });
      if (!current(request)) return;
      const target = items.find((entry) => itemKey(entry) === key);
      if (target) target.state = state;
    } catch (error) {
      if (!current(request)) return;
      notice = "更新失败，请稍后重试。";
    } finally {
      if (current(request)) {
        busyKey = "";
        render();
      }
    }
  }

  /* ---------- 对外 ---------- */

  function mount(next) {
    container = next || null;
    if (!container || !hooks?.getUser()) {
      container?.replaceChildren();
      return Promise.resolve(false);
    }
    if (mode === "ready" || mode === "empty") {
      render(); // 有缓存直接画，不重复请求
      return Promise.resolve(true);
    }
    return load();
  }

  function reset() {
    generation += 1;
    sequence += 1;
    items = [];
    hint = "";
    notice = "";
    busyKey = "";
    mode = "idle";
    container?.replaceChildren();
  }

  window.RecommendCard = {
    configure(options) { hooks = options || null; },
    mount,
    reset,
  };
})();
