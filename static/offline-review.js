"use strict";

/* offline-review.js —— 「今日复习」页的离线编排（window.OfflineReview）。
 *
 * 自己不存数据、不发请求：队列与预取全部复用 window.OfflineSync（offline-sync.js），
 * 本文件只负责把它接到复习页的界面流程上：
 *   - isOffline() / isNetworkError(error)：navigator.onLine===false，或 fetch 直接 reject
 *     （app.js 的 api() 在网络失败时抛不带 .status 的错误，HTTP 4xx/5xx 带 .status）；
 *   - todayFallback()：联网拉列表失败时，用 OfflineSync.readTodayQueue() 的预取队列
 *     渲染今天要复习的题；没预取到就返回 null，绝不编造题目；
 *   - cachedItem(id)：预取条目补成详情页需要的形状（reviews/variants 给空数组）；
 *   - grade(id, quality)：离线评分入队，提示「已记下，联网后同步」；
 *   - 联网补交由 pwa-register 派发 pwa:online → OfflineSync 自动 flush 完成，
 *     本文件监听 pwa:flush-result 播报「成功 N 条、跳过 M 条、失败可重试」；
 *   - restrictDetail(root)：离线时禁用撤销之外的联网功能入口（推迟/暂停菜单、AI 出题、
 *     变体结果保存、删除），并插入文字说明；离线评分没有撤销入口（不调用 rememberReview）；
 *   - 换号丢弃（pwa:queue-dropped）、会话过期（pwa:auth-expired）、登出保留都有提示。
 * 迟到守卫：configure  generation / reset 之后回来的事件一律不再提示。 */
(() => {
  let hooks = null;
  let generation = 0;
  let cachedItems = new Map();

  const sync = () => window.OfflineSync;
  const nav = () => (typeof window !== "undefined" ? window.navigator : undefined);

  function isOffline() {
    return nav()?.onLine === false;
  }

  function isNetworkError(error) {
    // api() 的 HTTP 错误带数字 .status；fetch 自身 reject（断网/DNS/TLS）没有 .status。
    return Boolean(error) && !error.status;
  }

  function notify(text) {
    if (hooks && typeof hooks.notify === "function") hooks.notify(text);
  }

  /* ---------- 预取队列兜底 ---------- */
  function detailShape(item, today) {
    return {
      ...item,
      today: today || item.today || null,
      reviews: Array.isArray(item.reviews) ? item.reviews : [],
      variants: Array.isArray(item.variants) ? item.variants : [],
    };
  }

  async function todayFallback() {
    if (!hooks || !hooks.getUser() || !sync()) return null;
    const gen = generation;
    const queue = await sync().readTodayQueue();
    if (gen !== generation || !queue || !Array.isArray(queue.items)) return null;
    cachedItems = new Map(queue.items.map((item) => [item.id, detailShape(item, queue.today)]));
    return { today: queue.today, items: queue.items.map((item) => ({ ...item })) };
  }

  function cachedItem(id) {
    const item = cachedItems.get(id);
    return item ? { ...item } : null;
  }

  /* ---------- 离线评分 ---------- */
  async function grade(mistakeId, quality) {
    if (!sync()) throw new Error("OfflineSync 未就绪");
    const op = await sync().enqueueGrade(mistakeId, quality);
    notify("已记下，联网后同步。");
    return op;
  }

  function summary() {
    return sync() ? sync().summary() : { pending: 0, failed: 0, total: 0 };
  }

  async function flushNow() {
    if (!sync()) return { synced: 0, skipped: 0, failed: 0, stopped: false, authExpired: false, offline: false };
    return sync().retryFailed();
  }

  /* ---------- 补交结果 / 安全事件提示 ---------- */
  function flushResultText(result) {
    const parts = [];
    if (result.synced > 0) parts.push(`成功同步 ${result.synced} 条评分`);
    if (result.skipped > 0) parts.push(`跳过 ${result.skipped} 条（题目已删除或已处理）`);
    if (result.failed > 0) parts.push(`${result.failed} 条失败，恢复联网后会自动重试`);
    if (result.authExpired) return "登录已过期，离线评分已保留在本机，请重新登录后同步。";
    return parts.length ? `离线评分补交完成：${parts.join("，")}。` : "";
  }

  function onFlushResult(event) {
    // 监听器在 reset() 时已卸掉；能收到事件就说明仍在当前会话。
    const result = event?.detail || {};
    const text = flushResultText(result);
    if (text) notify(text);
  }

  function onQueueDropped(event) {
    const dropped = event?.detail?.dropped || 0;
    if (dropped > 0) {
      notify(`已切换账号：上一账号的 ${dropped} 条离线评分不属于当前账号，仍保留在原账号的本机数据里，不会替别人提交。`);
    }
  }

  function onAuthExpired(event) {
    const pending = event?.detail?.pending ?? summary().total;
    notify(`登录已过期，${pending} 条离线评分已保留在本机，请重新登录后同步。`);
  }

  /* ---------- 离线详情：禁用联网功能并说明 ---------- */
  function restrictDetail(root) {
    if (!root) return;
    // 推迟 / 暂停 / 恢复菜单（review-extras.js 的 .review-more）。
    root.querySelectorAll(".review-more").forEach((menu) => { menu.hidden = true; });
    // AI 出题按钮。
    root.querySelectorAll(".ai-section button").forEach((button) => {
      button.disabled = true;
      button.dataset.blocked = "1";
      const aiSection = button.closest(".ai-section");
      if (aiSection && !aiSection.querySelector(".review-offline-subnote")) {
        const note = root.ownerDocument.createElement("p");
        note.className = "muted review-offline-subnote";
        note.textContent = "离线时不能让 AI 出题，联网后再试。";
        aiSection.append(note);
      }
    });
    // 变体练习结果保存。
    root.querySelectorAll(".variant form button").forEach((button) => { button.disabled = true; });
    // 删除易错点 / 删除整道题。
    root.querySelectorAll(".danger-zone button").forEach((button) => { button.disabled = true; });
    if (!root.querySelector(".review-offline-note")) {
      const note = root.ownerDocument.createElement("p");
      note.className = "muted review-offline-note";
      note.setAttribute("role", "status");
      note.textContent = "离线模式：评分会先存在本机、联网后按顺序自动同步；撤销、推迟、暂停和 AI 出题暂不可用。";
      root.prepend(note);
    }
  }

  /* ---------- 登出 ---------- */
  function onSignedOut() {
    const total = summary().total;
    if (total > 0) {
      notify(`${total} 条离线评分已保留在本机，重新登录后会自动同步。`);
    }
  }

  /* ---------- 配置 / 复位 ---------- */
  function bind() {
    if (typeof document === "undefined") return;
    document.addEventListener("pwa:flush-result", onFlushResult);
    document.addEventListener("pwa:queue-dropped", onQueueDropped);
    document.addEventListener("pwa:auth-expired", onAuthExpired);
  }

  function unbind() {
    if (typeof document === "undefined") return;
    document.removeEventListener("pwa:flush-result", onFlushResult);
    document.removeEventListener("pwa:queue-dropped", onQueueDropped);
    document.removeEventListener("pwa:auth-expired", onAuthExpired);
  }

  function configure(options) {
    if (generation > 0) unbind();
    hooks = options || null;
    cachedItems = new Map();
    bind();
  }

  function reset() {
    generation += 1;
    unbind();
    hooks = null;
    cachedItems = new Map();
  }

  window.OfflineReview = {
    configure,
    reset,
    isOffline,
    isNetworkError,
    todayFallback,
    cachedItem,
    grade,
    flushNow,
    summary,
    restrictDetail,
    onSignedOut,
    helpers: { flushResultText },
  };
})();
