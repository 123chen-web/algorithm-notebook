"use strict";

/* pwa-register.js —— Service Worker 注册与两条系统状态条（window.PwaRegister）。
 *
 * 职责（只做事件与状态，样式全部在 static/pwa.css）：
 *   - 检测支持：没有 navigator.serviceWorker 就整体静默（不报错、不动 DOM）；
 *   - 用 /sw.js?v=<版本> 注册，并把带 ?v= 的静态外壳清单 postMessage 给 SW；
 *   - 发现新版本 waiting 时显示「有新版本，点此刷新」条（textContent，按钮可键盘操作），
 *     点击后给 waiting 的 worker 发 SKIP_WAITING，controllerchange 后刷新页面；
 *   - 监听 online / offline 更新顶部细条：「离线中，评分会在联网后同步」；
 *     联网时派发 pwa:online（offline-sync.js 收到后自动补交评分）；
 *   - 监听 pwa:sync-state（offline-sync.js 派发）显示「N 条待同步」。
 * 迟到守卫：register 的异步结果回来前若已 reset / 重新 configure，一律丢弃。 */
(() => {
  const DEFAULT_SHELL_BASE = "/";
  const OFFLINE_TEXT = "离线中，评分会在联网后同步";

  let config = null;        // { version, swUrl, shellUrls }
  let registration = null;
  let generation = 0;       // reset / 重新 configure 时 +1
  let netBar = null;
  let updateBar = null;
  let queueState = { pending: 0, failed: 0 };
  let wantReload = false;
  let reloading = false;
  let bound = false;

  /* ---------- 环境 ---------- */
  const nav = () => (typeof window !== "undefined" ? window.navigator : undefined);
  const worker = () => nav()?.serviceWorker;
  const supported = () => Boolean(worker() && typeof worker().register === "function");
  const online = () => nav()?.onLine !== false; // 读不到 onLine 就按在线处理

  /* ---------- 静态外壳清单 ---------- */
  function sameOriginPath(value) {
    if (typeof value !== "string" || !value) return null;
    if (value.startsWith("/") && !value.startsWith("//")) return value;
    const origin = window.location?.origin;
    if (origin && value.startsWith(origin + "/")) return value.slice(origin.length);
    return null;
  }

  /** 从页面现有的 stylesheet / script 标签收集同源资源（保留 ?v= 版本号），首页永远在内。 */
  function collectShellUrls() {
    const urls = [DEFAULT_SHELL_BASE];
    const seen = new Set(urls);
    const nodes = document.querySelectorAll('link[rel="stylesheet"][href], script[src]');
    for (const node of nodes) {
      const raw = node.getAttribute("href") || node.getAttribute("src");
      const path = sameOriginPath(raw);
      if (path && !seen.has(path)) {
        seen.add(path);
        urls.push(path);
      }
    }
    return urls;
  }

  function shellUrls() {
    if (Array.isArray(config?.shellUrls) && config.shellUrls.length) return config.shellUrls;
    return collectShellUrls();
  }

  /* ---------- 顶部网络 / 同步状态细条 ---------- */
  function ensureNetBar() {
    if (netBar) return netBar;
    netBar = document.createElement("div");
    netBar.className = "pwa-net";
    netBar.setAttribute("role", "status");
    netBar.hidden = true;
    document.body.prepend(netBar);
    return netBar;
  }

  function updateNetBar() {
    if (!supported()) return;
    const bar = ensureNetBar();
    if (!online()) {
      bar.dataset.state = "offline";
      bar.textContent = queueState.pending > 0
        ? `离线中，${queueState.pending} 条评分待同步`
        : OFFLINE_TEXT;
      bar.hidden = false;
      return;
    }
    if (queueState.pending > 0) {
      bar.dataset.state = "pending";
      bar.textContent = `${queueState.pending} 条待同步`;
      bar.hidden = false;
      return;
    }
    if (queueState.failed > 0) {
      bar.dataset.state = "pending";
      bar.textContent = `${queueState.failed} 条同步失败，联网后会自动重试`;
      bar.hidden = false;
      return;
    }
    bar.hidden = true;
    bar.textContent = "";
    delete bar.dataset.state;
  }

  /* ---------- 「有新版本」条 ---------- */
  function applyUpdate() {
    if (reloading) return;
    const waiting = registration?.waiting;
    if (waiting) {
      wantReload = true;
      waiting.postMessage({ type: "SKIP_WAITING" });
      return;
    }
    // 没有 waiting 就直接刷新（比如新 worker 已经接管、只是条还没消失）。
    reloading = true;
    window.location?.reload?.();
  }

  function showUpdateBar() {
    if (updateBar) {
      updateBar.hidden = false;
      return;
    }
    updateBar = document.createElement("div");
    updateBar.className = "pwa-update";
    updateBar.setAttribute("role", "status");
    const message = document.createElement("p");
    message.textContent = "有新版本，点此刷新";
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "primary";
    refresh.textContent = "刷新";
    refresh.setAttribute("aria-label", "刷新以使用新版本");
    refresh.addEventListener("click", applyUpdate);
    updateBar.append(message, refresh);
    document.body.append(updateBar);
  }

  function dismissUpdate() {
    if (updateBar) updateBar.hidden = true;
  }

  function watchWorker(candidate, gen) {
    if (!candidate || typeof candidate.addEventListener !== "function") return;
    candidate.addEventListener("statechange", () => {
      if (gen !== generation) return; // 迟到守卫：已经 reset / 换配置
      if (candidate.state === "installed" && worker()?.controller) showUpdateBar();
    });
  }

  function sendShellList(target) {
    if (!target || typeof target.postMessage !== "function") return;
    target.postMessage({ type: "SHELL_LIST", urls: shellUrls() });
  }

  /* ---------- 事件绑定（可全部卸掉） ---------- */
  function onOnline() {
    updateNetBar();
    document.dispatchEvent(new CustomEvent("pwa:online"));
  }
  function onOffline() {
    updateNetBar();
  }
  function onSyncState(event) {
    const detail = event?.detail || {};
    queueState = {
      pending: Number.isInteger(detail.pending) ? detail.pending : 0,
      failed: Number.isInteger(detail.failed) ? detail.failed : 0,
    };
    updateNetBar();
  }
  function onControllerChange() {
    if (wantReload && !reloading) {
      reloading = true;
      window.location?.reload?.();
    }
  }

  function bindEvents() {
    if (bound) return;
    bound = true;
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    document.addEventListener("pwa:sync-state", onSyncState);
    worker()?.addEventListener?.("controllerchange", onControllerChange);
  }

  function unbindEvents() {
    if (!bound) return;
    bound = false;
    window.removeEventListener("online", onOnline);
    window.removeEventListener("offline", onOffline);
    document.removeEventListener("pwa:sync-state", onSyncState);
    worker()?.removeEventListener?.("controllerchange", onControllerChange);
  }

  /* ---------- 对外 ---------- */
  async function register() {
    if (!config || !supported()) return false; // 不支持就静默
    const gen = generation;
    let result;
    try {
      result = await worker().register(`${config.swUrl}?v=${encodeURIComponent(config.version)}`, { scope: "/" });
    } catch {
      return false; // 注册失败同样静默：站点没有 PWA 也能用
    }
    if (gen !== generation) return false; // 迟到守卫
    registration = result;
    bindEvents();
    sendShellList(result.installing || result.waiting || result.active);
    if (result.waiting && worker()?.controller) showUpdateBar();
    if (typeof result.addEventListener === "function") {
      result.addEventListener("updatefound", () => {
        if (gen !== generation) return;
        sendShellList(result.installing);
        watchWorker(result.installing, gen);
      });
    }
    watchWorker(result.installing, gen);
    updateNetBar();
    return true;
  }

  function configure(options) {
    generation += 1; // 旧配置发出的异步请求全部作废
    registration = null;
    config = {
      version: String(options?.version || "1"),
      swUrl: typeof options?.swUrl === "string" ? options.swUrl : "/sw.js",
      shellUrls: Array.isArray(options?.shellUrls) ? options.shellUrls.slice() : null,
    };
  }

  function reset() {
    generation += 1;
    registration = null;
    queueState = { pending: 0, failed: 0 };
    wantReload = false;
    reloading = false;
    unbindEvents();
    netBar?.remove();
    netBar = null;
    updateBar?.remove();
    updateBar = null;
  }

  function state() {
    return {
      supported: supported(),
      registered: Boolean(registration),
      updateVisible: Boolean(updateBar && !updateBar.hidden),
      netVisible: Boolean(netBar && !netBar.hidden),
      online: online(),
      pending: queueState.pending,
      failed: queueState.failed,
    };
  }

  window.PwaRegister = {
    configure,
    register,
    reset,
    applyUpdate,
    dismissUpdate,
    updateNetBar,
    state,
    helpers: { collectShellUrls, sameOriginPath },
  };
})();
