"use strict";

/* 普通详情与专注复习共用：预览、最近一次撤销、推迟/暂停及本地遮挡偏好。
   写请求由 app.js 注入的 api() 发送，保留 CSRF 与登录态处理。 */
(() => {
  const GRADES = [
    { key: "1", quality: 0, label: "完全忘了", tone: "low", stamp: "再练" },
    { key: "2", quality: 2, label: "答错了", tone: "low", stamp: "再练" },
    { key: "3", quality: 3, label: "很吃力", tone: "mid", stamp: "过关" },
    { key: "4", quality: 4, label: "记得", tone: "good", stamp: "记住" },
    { key: "5", quality: 5, label: "很熟", tone: "good", stamp: "掌握" },
  ];
  let options = { getEpoch: () => 0, getUser: () => null, getView: () => null };
  let generation = 0;
  let sessionKey = null;
  const previews = new Map();
  let decorations = new WeakMap();
  const capabilities = new Map();
  const menus = new Set();
  let activeMenu = null;
  let toast = null;
  let latestUndo = null;
  let menuId = 0;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function configure(next) { options = { ...options, ...next }; }
  function clearNotice() { toast?.dismiss(); latestUndo = null; }
  function userId() {
    const user = options.getUser();
    return user && typeof user === "object" ? user.id : user;
  }
  function reset() {
    generation += 1;
    previews.clear();
    decorations = new WeakMap();
    capabilities.clear();
    for (const menu of [...menus]) menu.destroy();
    toast?.dismiss();
    latestUndo = null;
    sessionKey = null;
  }
  function syncSession() {
    const key = [options.getEpoch(), userId()];
    if (sessionKey && (key[0] !== sessionKey[0] || key[1] !== sessionKey[1])) reset();
    sessionKey = key;
  }
  function capture() {
    syncSession();
    const stamp = generation;
    const epoch = options.getEpoch();
    const user = userId();
    const view = options.getView();
    return () => stamp === generation && epoch === options.getEpoch()
      && user === userId() && view === options.getView();
  }
  async function request(path, init = {}) {
    if (typeof options.api === "function") return options.api(path, init);
    if (typeof window.api === "function") return window.api(path, init);
    // 没有应用的 CSRF 请求封装时只允许读取；独立加载仍可显示预览。
    if (init.method && init.method !== "GET") throw new Error("复习操作暂时不可用");
    const response = await fetch(path, { credentials: "same-origin", ...init });
    let body = {};
    try { body = await response.json(); } catch (_) { /* 405 等可能没有 JSON */ }
    if (!response.ok) throw Object.assign(new Error(body.detail || "请求失败，请稍后重试"), { status: response.status });
    return body;
  }
  function errorText(error) { return String(error?.detail || error?.message || "请求失败，请稍后重试"); }
  function intervalText(days) {
    const count = Number(days);
    if (!Number.isFinite(count) || count < 1) return "";
    return count === 1 ? "明天" : `${count} 天后`;
  }
  function hideReason() {
    try { return window.localStorage.getItem("review-hide-reason") !== "false"; }
    catch (_) { return true; }
  }
  function setHideReason(value) {
    try { window.localStorage.setItem("review-hide-reason", String(Boolean(value))); }
    catch (_) { /* 隐私模式/存储受限时仍允许当前详情切换 */ }
  }
  function editable(target) {
    if (!target?.closest) return false;
    return Boolean(target.closest("input,textarea,select") || target.isContentEditable
      || target.closest('[contenteditable=""],[contenteditable="true"],[role="textbox"]'));
  }
  function dialogOpen() {
    return [...document.querySelectorAll('dialog[open],[role="dialog"][aria-modal="true"]:not(.focus-shell)')]
      .some((dialog) => !dialog.closest('[hidden],[inert],[aria-hidden="true"]'));
  }
  function blockedKey(event) {
    return Boolean(event.ctrlKey || event.metaKey || event.altKey || event.shiftKey
      || event.isComposing || editable(event.target) || dialogOpen() || activeMenu);
  }

  async function preview(item, isCurrent = () => true) {
    if (!item || item.id === undefined || item.version === undefined) return null;
    const guard = capture();
    if (!isCurrent()) return null;
    const key = `${item.id}:${item.version}`;
    if (!previews.has(key)) {
      const promise = request(`/api/mistakes/${encodeURIComponent(item.id)}/preview`)
        .then((data) => guard() && data?.version === item.version && data.previews ? data.previews : null)
        .catch(() => null);
      previews.set(key, promise);
      // 失败和页面离开不缓存，下一次打开可以重新尝试。
      promise.then((data) => { if (!data && previews.get(key) === promise) previews.delete(key); });
    }
    const data = await previews.get(key);
    return guard() && isCurrent() ? data : null;
  }
  async function decorateGrades(buttons, item, isCurrent = () => true) {
    const guard = capture();
    const token = {};
    for (const button of buttons) {
      decorations.set(button, token);
      button.querySelector(".review-interval")?.remove();
    }
    const data = await preview(item, isCurrent);
    if (!data || !guard() || !isCurrent()) return;
    for (const button of buttons) {
      if (decorations.get(button) !== token) continue;
      const text = intervalText(data[String(button.dataset.quality)]?.interval_days);
      if (text && button.isConnected) button.append(node("small", "review-interval", text));
    }
  }
  async function available(item, action, isCurrent) {
    const guard = capture();
    const path = `/api/mistakes/${encodeURIComponent(item.id)}/${action}`;
    if (!capabilities.has(path)) {
      // GET 探测已存在的写路由：405 表示路由存在，404 表示此功能未发布。
      const promise = request(path, { method: "GET" }).then(() => true)
        .catch((error) => {
          if (error.status === 405) return true;
          if (error.status !== 404 && capabilities.get(path) === promise) capabilities.delete(path);
          return false;
        });
      capabilities.set(path, promise);
    }
    const supported = await capabilities.get(path);
    return guard() && isCurrent() && supported;
  }

  function notify(text, { actionLabel, onAction, isCurrent = () => true } = {}) {
    toast?.dismiss();
    const guard = capture();
    const root = node("aside", "review-toast");
    root.id = "review-toast-status";
    root.setAttribute("role", "status");
    root.setAttribute("aria-live", "polite");
    root.setAttribute("aria-atomic", "true");
    const message = node("span", "review-toast-text", text);
    root.append(message);
    let button = null;
    let timer = null;
    let remaining = 8000;
    let started = 0;
    let hovered = false;
    let focused = false;
    let dismissed = false;
    let actionBusy = false;
    const current = () => !dismissed && toast === handle && guard() && isCurrent();
    const pause = () => {
      if (!timer) return;
      remaining = Math.max(0, remaining - (Date.now() - started));
      clearTimeout(timer);
      timer = null;
    };
    const resume = () => {
      if (!current() || hovered || focused || timer) return;
      started = Date.now();
      timer = setTimeout(() => handle.dismiss(), remaining);
      timer?.unref?.();
    };
    const handle = {
      root, current,
      dismiss() {
        dismissed = true;
        clearTimeout(timer);
        timer = null;
        root.remove();
        if (latestUndo?.handle === handle) latestUndo = null;
        if (toast === handle) toast = null;
      },
      setText(value) { if (current()) message.textContent = String(value); },
      setBusy(value) { if (button) button.disabled = Boolean(value); },
      setAction(label, action) {
        if (!current()) return;
        button?.remove();
        button = null;
        if (!label || typeof action !== "function") return;
        button = node("button", "review-toast-action", label);
        button.type = "button";
        button.addEventListener("click", async () => {
          if (!current() || button.disabled || actionBusy) return;
          actionBusy = true;
          handle.setBusy(true);
          try { await action(); }
          catch (error) { handle.setText(errorText(error)); }
          finally { actionBusy = false; if (current()) handle.setBusy(false); }
        });
        root.append(button);
      },
    };
    root.addEventListener("mouseenter", () => { hovered = true; pause(); });
    root.addEventListener("mouseleave", () => { hovered = false; resume(); });
    root.addEventListener("focusin", () => { focused = true; pause(); });
    root.addEventListener("focusout", (event) => {
      if (event.relatedTarget && root.contains(event.relatedTarget)) return;
      focused = false;
      resume();
    });
    toast = handle;
    if (guard() && isCurrent()) {
      // 专注模式是模态对话框，撤销/恢复也应在其读屏范围和 Tab 循环里。
      const host = document.querySelector('#focus:not([hidden]) .focus-shell') || document.body;
      host.append(root);
    }
    else handle.dismiss();
    handle.setAction(actionLabel, onAction);
    resume();
    return handle;
  }

  function rememberReview({ item, result, quality, isCurrent = () => true, onUndo = () => {} }) {
    const guard = capture();
    const label = GRADES.find((grade) => grade.quality === Number(quality))?.label || "已记录";
    const interval = intervalText(result.interval_days);
    const handle = notify(`已评分「${label}」${interval ? ` · 下次 ${interval}` : ""}`, { isCurrent });
    const record = { item, result, handle, guard, isCurrent, onUndo, busy: false, supported: false };
    latestUndo = record;
    const current = () => latestUndo === record && handle.current() && guard() && isCurrent();
    async function undo() {
      if (!current() || !record.supported || record.busy) return;
      record.busy = true;
      handle.setBusy(true);
      try {
        const restored = await request(`/api/mistakes/${encodeURIComponent(item.id)}/review/undo`, {
          method: "POST", body: JSON.stringify({ version: result.version }),
        });
        if (!current()) return;
        await onUndo(restored);
        if (!current()) return;
        latestUndo = null;
        handle.setAction(null);
        handle.setText("已撤销");
      } catch (error) {
        if (current()) {
          handle.setText(errorText(error));
          if (error.status === 404) { record.supported = false; handle.setAction(null); }
        }
      } finally {
        record.busy = false;
        if (current()) handle.setBusy(false);
      }
    }
    record.undo = undo;
    available(item, "review/undo", current).then((supported) => {
      if (!current() || !supported) return;
      record.supported = true;
      handle.setAction("撤销", undo);
    });
    return handle;
  }

  function menu({ item, isCurrent = () => true, onAction = () => {}, status } = {}) {
    const guard = capture();
    let destroyed = false;
    let busy = false;
    let version = item.version;
    const root = node("div", "review-more");
    root.hidden = true;
    const popup = node("div", "review-more-menu");
    popup.id = `review-more-menu-${++menuId}`;
    popup.hidden = true;
    popup.setAttribute("role", "menu");
    popup.setAttribute("aria-label", "这条复习的更多操作");
    const trigger = node("button", "review-more-button", "更多");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "menu");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", popup.id);
    root.append(trigger, popup);
    const current = () => !destroyed && guard() && isCurrent();
    const buttons = () => [...popup.querySelectorAll("button")].filter((button) => !button.hidden && !button.disabled);
    function close(restoreFocus = false) {
      popup.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
      if (activeMenu === root) activeMenu = null;
      if (restoreFocus && trigger.isConnected) trigger.focus({ preventScroll: true });
    }
    function open(last = false) {
      if (!current() || root.hidden || busy || item.suspended_at) return;
      activeMenu?.close();
      popup.hidden = false;
      activeMenu = root;
      trigger.setAttribute("aria-expanded", "true");
      const entries = buttons();
      (last ? entries.at(-1) : entries[0])?.focus({ preventScroll: true });
    }
    root.close = close;
    root.open = open;
    root.destroy = () => {
      close(); destroyed = true;
      document.removeEventListener("keydown", keydown);
      document.removeEventListener("click", outside);
      menus.delete(root);
    };
    menus.add(root);
    function announce(text) {
      if (status && current()) status.textContent = text;
    }
    async function perform(action, days) {
      if (!current() || busy) return;
      busy = true;
      close();
      for (const button of root.querySelectorAll("button")) button.disabled = true;
      announce("正在更新复习安排…");
      try {
        const payload = { version };
        if (action === "snooze") payload.days = days;
        const result = await request(`/api/mistakes/${encodeURIComponent(item.id)}/${action}`, {
          method: "POST", body: JSON.stringify(payload),
        });
        if (!current()) return;
        version = result.version;
        await onAction(action, days, result);
        // 成功回调通常会移走这张卡并 destroy 菜单；已提交动作的提示仍属本轮。
        if (!guard() || !isCurrent()) return;
        if (action === "suspend") {
          announce("已暂停这条复习");
          const notification = notify("已暂停这条复习", { isCurrent: () => guard() && isCurrent() });
          available(item, "unsuspend", () => guard() && isCurrent()).then((supported) => {
            if (!supported || !notification.current()) return;
            notification.setAction("恢复", async () => {
              const restoreGuard = capture();
              const restored = await request(`/api/mistakes/${encodeURIComponent(item.id)}/unsuspend`, {
                method: "POST", body: JSON.stringify({ version: result.version }),
              });
              if (!restoreGuard() || !guard() || !isCurrent()) return;
              await onAction("unsuspend", undefined, restored);
              if (restoreGuard() && guard() && isCurrent()) notify("已恢复复习", { isCurrent });
            });
          });
        } else {
          const message = action === "snooze" ? `已推迟到${days === 1 ? "明天" : `${days} 天后`}` : "已恢复复习";
          announce(message);
          notify(message, { isCurrent: () => guard() && isCurrent() });
        }
      } catch (error) {
        if (current()) {
          announce(errorText(error));
          notify(errorText(error), { isCurrent });
          if (error.status === 404) root.hidden = true;
        }
      } finally {
        busy = false;
        if (current()) for (const button of root.querySelectorAll("button")) button.disabled = false;
      }
    }
    function actionButton(action, days, text) {
      const button = node("button", "review-more-action", text);
      button.type = "button";
      button.dataset.reviewAction = action;
      if (days !== undefined) button.dataset.days = String(days);
      button.setAttribute("role", "menuitem");
      button.tabIndex = -1;
      button.addEventListener("click", () => perform(action, days));
      return button;
    }
    function keydown(event) {
      if (activeMenu !== root) return;
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); close(true); return; }
      if (event.key === "Tab") { close(); return; }
      if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation();
      const entries = buttons();
      const index = entries.indexOf(document.activeElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? entries.length - 1
        : (index + (event.key === "ArrowDown" ? 1 : -1) + entries.length) % entries.length;
      entries[next]?.focus({ preventScroll: true });
    }
    function outside(event) { if (activeMenu === root && !root.contains(event.target)) close(); }
    trigger.addEventListener("click", () => popup.hidden ? open() : close(true));
    trigger.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown" || event.key === "ArrowUp") {
        event.preventDefault(); open(event.key === "ArrowUp");
      }
    });
    document.addEventListener("keydown", keydown);
    document.addEventListener("click", outside);
    root.ready = (async () => {
      if (item.suspended_at) {
        if (!await available(item, "unsuspend", current) || !current()) return;
        trigger.remove(); popup.remove();
        const restore = actionButton("unsuspend", undefined, "恢复复习");
        restore.className = "review-restore";
        restore.removeAttribute("role");
        restore.tabIndex = 0;
        root.append(restore);
        root.hidden = false;
        return;
      }
      const supported = await Promise.all([
        available(item, "snooze", current), available(item, "suspend", current),
      ]);
      if (!current()) return;
      if (supported[0]) {
        popup.append(actionButton("snooze", 1, "推迟到明天 (T)"),
          actionButton("snooze", 3, "推迟 3 天"), actionButton("snooze", 7, "推迟 7 天"));
      }
      if (supported[1]) popup.append(actionButton("suspend", undefined, "暂停这条（不再出现在待复习里）(P)"));
      root.hidden = !supported.some(Boolean);
    })();
    return root;
  }

  document.addEventListener("keydown", (event) => {
    if (!(event.ctrlKey || event.metaKey) || event.altKey || event.shiftKey || event.isComposing
      || String(event.key).toLowerCase() !== "z" || editable(event.target) || dialogOpen() || activeMenu) return;
    if (!latestUndo?.supported || !latestUndo.handle.current()) return;
    event.preventDefault();
    latestUndo.undo();
  });
  document.addEventListener("focus:closed", () => {
    if (toast?.root.closest("#focus")) clearNotice();
  });
  window.ReviewExtras = { GRADES, configure, capture, preview, decorateGrades, intervalText,
    hideReason, setHideReason, editable, blockedKey, menu, notify, rememberReview, clearNotice, reset };
})();
