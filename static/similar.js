"use strict";

/* 举一反三：复习完成页的"趁热打铁"卡片。
   对外契约：window.Similar = { configure({ api }), mount(container, mistakeId), unmount(), quickAdd(item) }。
   - 请求只走宿主传入的 api()（默认 window.api）：GET /api/review/similar?mistake_id=；
   - 一键加入：取 item.add_payload，剥掉 POST /api/problems 不认的字段（extra="forbid"），
     quick 模式建题；建题成功后给新建的易错点打"举一反三"标签（NewProblem 没有 source 字段，
     用标签标记来源）。
   - 渲染只用 textContent；迟到响应按 generation 丢弃；失败显示可重试的错误行。 */
(() => {
  const SOURCE_TAG = "举一反三";
  // POST /api/problems 允许的字段（InputModel extra="forbid"，其余一律剥离）。
  const PROBLEM_FIELDS = ["title", "zone", "language", "code", "thinking", "mistakes", "quick"];

  let hooks = { api: null };
  let generation = 0;
  let mounted = null;
  const ticket = () => ({ generation, epoch: hooks?.getEpoch?.(), userId: hooks?.getUser?.()?.id });
  const alive = (t) => t.generation === generation && t.epoch === hooks?.getEpoch?.() && t.userId === hooks?.getUser?.()?.id;

  function api() {
    if (typeof hooks.api === "function") return hooks.api;
    if (typeof window.api === "function") return window.api;
    throw new Error("Similar 需要宿主提供 api()");
  }

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

  function sanitizePayload(payload) {
    // add_payload 里 source_tag / source_url 等只是给前端用的提示字段，POST 前剥掉。
    const body = {};
    for (const key of PROBLEM_FIELDS) {
      if (payload[key] !== undefined) body[key] = payload[key];
    }
    if (body.quick !== true) body.quick = true;
    return body;
  }

  async function quickAdd(item, requestApi) {
    const t = ticket();
    const request = requestApi || api();
    const body = sanitizePayload(item.add_payload || {});
    const created = await request("/api/problems", {
      method: "POST",
      body: JSON.stringify(body),
    });
    if (!alive(t)) return null;
    // quick 模式自动建 1 条易错点：打上"举一反三"标签，便于以后按标签筛选。
    const mistakeId = created && created.mistake_ids && created.mistake_ids[0];
    if (mistakeId != null) {
      await request(`/api/mistakes/${mistakeId}/tags`, {
        method: "PUT",
        body: JSON.stringify({ tags: [SOURCE_TAG] }),
      });
    }
    return alive(t) ? created : null;
  }

  function buildCard(items, mistakeId) {
    const card = node("section", "similar-card");
    card.setAttribute("aria-label", "趁热打铁");
    card.append(node("h3", "similar-card-title", "趁热打铁：再来 3 道同类题"));
    const list = node("ul", "similar-card-list");
    for (const item of items) {
      const li = node("li", "similar-card-item");
      const head = node("div", "similar-card-head");
      head.append(node("strong", "", item.title || ""));
      const meta = [];
      if (item.source) meta.push(item.source);
      if (item.difficulty != null) meta.push(String(item.difficulty));
      if (item.tags && item.tags.length) meta.push(item.tags.join("、"));
      head.append(node("small", "muted", meta.join(" · ")));
      li.append(head);
      if (item.reason) li.append(node("p", "similar-card-reason", item.reason));
      const actions = node("div", "similar-card-actions");
      if (item.url) {
        const link = node("a", "similar-card-link", "原题链接");
        link.href = item.url;
        link.target = "_blank";
        link.rel = "noopener";
        actions.append(link);
      }
      const add = button("一键加入", "similar-card-add");
      const status = node("span", "similar-card-status");
      add.addEventListener("click", async () => {
        add.disabled = true;
        status.textContent = "加入中…";
        try {
          await quickAdd(item);
          status.textContent = "已加入题库";
          add.textContent = "已加入";
        } catch (error) {
          status.textContent = `加入失败：${error.message || "请稍后重试"}`;
          add.disabled = false;
        }
      });
      actions.append(add, status);
      li.append(actions);
      list.append(li);
    }
    card.append(list);
    card.dataset.mistakeId = String(mistakeId);
    return card;
  }

  function renderError(container, gen, message, retry) {
    if (gen !== generation || !alive(t)) return;
    container.replaceChildren();
    const box = node("p", "similar-card-error", message);
    if (retry) {
      const btn = button("重试", "similar-card-retry");
      btn.addEventListener("click", retry);
      box.append(document.createTextNode(" "));
      box.append(btn);
    }
    container.append(box);
  }

  async function load(container, mistakeId, gen) {
    const t = ticket();
    try {
      const data = await api()(`/api/review/similar?mistake_id=${encodeURIComponent(mistakeId)}`);
      if (gen !== generation || !alive(t)) return;
      const items = (data && data.items) || [];
      if (!items.length) {
        renderError(container, gen, "暂时没有找到同类题，稍后再来。", null);
        return;
      }
      container.replaceChildren();
      container.append(buildCard(items, mistakeId));
    } catch (error) {
      if (!alive(t)) return;
      renderError(container, gen, `加载失败：${error.message || "请稍后重试"}`, () =>
        mount(container, mistakeId),
      );
    }
  }

  function mount(container, mistakeId) {
    if (!container || mistakeId == null) return;
    const gen = ++generation;
    mounted = { container, mistakeId };
    container.replaceChildren();
    container.append(node("p", "similar-card-loading", "正在找同类题…"));
    load(container, mistakeId, gen);
  }

  function unmount() {
    generation += 1;
    mounted = null;
  }

  function configure(options) {
    hooks = { ...hooks, ...(options || {}) };
  }

  window.Similar = {
    configure,
    mount,
    unmount,
    reset: unmount,
    quickAdd,
    SOURCE_TAG,
  };
})();
