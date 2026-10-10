"use strict";

/* draw-host.js —— 笔记页里的画板宿主（自托管 Excalidraw）。
 *
 * 职责：笔记页的“新建画板/我的画板”列表、全屏 iframe 面板、postMessage
 * 协议（见 static/draw 的 README_DRAW.md）、防抖自动保存、乐观锁版本、
 * 缩略图上传、markdown 引用 `![标题](drawing:ID)` 的打开委托。
 *
 * 安全：只接受 origin === location.origin 且 source === 当前画板 iframe
 * 的消息；所有用户文本走 textContent，一律用 DOM API 构建节点、不拼 HTML；
 * 网络请求一律走宿主注入的 hooks.api，本文件不直接发起网络请求。
 */
(function () {
  const DRAW_PATH = "/static/draw/draw.html";
  const SAVE_DEBOUNCE_MS = 1200;
  const READY_TIMEOUT_MS = 15000;
  const PNG_TIMEOUT_MS = 8000;
  const PAGE_SIZE = 100;
  // 首次打开画板前等待 SW 预缓存（约 8MB）的最长时间；超时则降级直接打开。
  const CACHE_GATE_MS = 40000;

  // ---------------------------------------------------------------- 纯函数

  /** 消息来源校验：必须是同源、且来自当前画板 iframe，且类型是 draw:* 协议。 */
  function isTrustedFrameMessage(event, origin, frameSource) {
    if (!event || typeof event !== "object") return false;
    if (typeof origin !== "string" || !origin || event.origin !== origin) return false;
    if (!frameSource || event.source !== frameSource) return false;
    const data = event.data;
    if (!data || typeof data !== "object") return false;
    return typeof data.type === "string" && data.type.indexOf("draw:") === 0;
  }

  /** 解析 drawing:<id> 目标，返回正整数 id 或 null。 */
  function parseDrawingTarget(target) {
    if (typeof target !== "string") return null;
    const match = /^drawing:(\d+)$/.exec(target);
    if (!match) return null;
    const id = Number(match[1]);
    return Number.isInteger(id) && id > 0 ? id : null;
  }

  /** 标题里的 markdown/换行特殊字符去掉，避免破坏引用语法。 */
  function sanitizeTitleForMarkdown(title) {
    return String(title || "画板")
      .replace(/[\[\]()\r\n]/g, " ")
      .trim()
      .slice(0, 100) || "画板";
  }

  /** 生成笔记正文里的画板引用行。 */
  function drawingRefMarkdown(id, title) {
    return `![${sanitizeTitleForMarkdown(title)}](drawing:${id})\n`;
  }

  function thumbUrl(id) {
    return `/api/drawings/${id}/thumb`;
  }

  /** dataURL → Uint8Array（缩略图直传用，不依赖 Blob/FormData）。 */
  function dataUrlToBytes(dataUrl) {
    const comma = String(dataUrl || "").indexOf(",");
    if (comma < 0) throw new Error("缩略图数据格式不正确");
    const binary = window.atob(dataUrl.slice(comma + 1));
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
    return bytes;
  }

  /**
   * 在 textarea 光标处插入文本；返回插入后的光标位置。纯 DOM 操作，
   * 便于在假 DOM 里单测。
   */
  function insertAtCursor(field, snippet) {
    const start = Number.isInteger(field.selectionStart) ? field.selectionStart : field.value.length;
    const end = Number.isInteger(field.selectionEnd) ? field.selectionEnd : field.value.length;
    field.value = field.value.slice(0, start) + snippet + field.value.slice(end);
    const next = start + snippet.length;
    field.selectionStart = next;
    field.selectionEnd = next;
    return next;
  }

  function formatTime(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} `
      + `${pad(date.getHours())}:${pad(date.getMinutes())}`;
  }

  function currentOrigin() {
    return (window.location && window.location.origin) || "";
  }

  /** Excalidraw 主题：应用只有浅色纸感主题，场景暗色仅用于背景，故固定 light。 */
  function currentDrawTheme() {
    return document.documentElement.dataset.sceneTone === "dark" ? "dark" : "light";
  }

  // ---------------------------------------------------------------- 画板资源缓存闸门
  //
  // 跨源字体兜底由 sw.js 注入的 draw-font-shim.js 在 FontFace 构造前剔除
  // （零外链的根治手段，详见 sw.js / draw-font-shim.js 注释）。这里的闸门解决
  // 另一个问题：画板首屏约 8MB，sw.js 在空闲时把整个 /static/draw/ 预缓存，
  // 宿主首次开板前等待预缓存完成，挂载时本地资源全部缓存瞬时命中、首屏更快，
  // 且断网后仍可打开。不支持 Service Worker / 未受控 / 超时时降级为直接打开。

  /** 纯函数：给定 SW 与缓存状态，决定开板前是否需要等待。 */
  function drawGateDecision(state) {
    if (!state || state.supported !== true) return "proceed";
    if (state.controlled !== true) return "proceed";
    if (state.ready === true) return "proceed";
    return "wait";
  }

  const drawCache = {
    supported: false,
    controlled: false,
    ready: false,
    done: false,
    total: 0,
    cached: 0,
    bound: false,
    waiters: [],
    gateSeq: 0,
  };

  function serviceWorkerApi() {
    return (typeof navigator !== "undefined" && navigator.serviceWorker) || null;
  }

  function flushDrawCacheWaiters() {
    const waiters = drawCache.waiters.splice(0);
    for (const waiter of waiters) waiter();
  }

  function applyDrawCacheStatus(data) {
    if (!data || typeof data !== "object") return;
    drawCache.total = Number.isInteger(data.total) ? data.total : drawCache.total;
    drawCache.cached = Number.isInteger(data.cached) ? data.cached : drawCache.cached;
    if (data.done === true) {
      drawCache.done = true;
      drawCache.ready = drawCache.cached > 0;
      flushDrawCacheWaiters();
    }
  }

  function primeDrawCache() {
    const sw = serviceWorkerApi();
    if (!sw || typeof sw.controller === "undefined") {
      drawCache.supported = false;
      return;
    }
    drawCache.supported = true;
    drawCache.controlled = Boolean(sw.controller);
    if (!drawCache.bound) {
      drawCache.bound = true;
      sw.addEventListener("message", (event) => {
        // 消息只能来自同源受控 SW（ServiceWorker 消息天然同源）。
        if (event.source && event.source.scriptURL
          && event.source.scriptURL.indexOf(location.origin) !== 0) return;
        applyDrawCacheStatus(event.data);
      });
      sw.addEventListener("controllerchange", () => {
        drawCache.controlled = Boolean(sw.controller);
        primeDrawCache();
      });
    }
    if (sw.controller) {
      try {
        sw.controller.postMessage({ type: "DRAW_PRECACHE" });
        sw.controller.postMessage({ type: "DRAW_CACHE_STATUS" });
      } catch (_error) {
        /* SW 状态竞态时忽略，开板闸门会超时降级 */
      }
    }
  }

  /** 等待画板资源预缓存就绪；不支持 SW / 未受控 / 超时则解析为降级结果。 */
  function waitDrawCacheReady(timeoutMs) {
    primeDrawCache();
    if (drawGateDecision(drawCache) === "proceed") {
      return Promise.resolve(drawCache.supported ? "ready" : "unsupported");
    }
    const seq = drawCache.gateSeq;
    return new Promise((resolve) => {
      let settled = false;
      const finish = (result) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(timer);
        const index = drawCache.waiters.indexOf(onReady);
        if (index >= 0) drawCache.waiters.splice(index, 1);
        resolve(result);
      };
      const onReady = () => {
        if (seq !== drawCache.gateSeq) return;
        finish(drawCache.ready ? "ready" : "partial");
      };
      const timer = window.setTimeout(() => {
        finish(drawCache.cached > 0 ? "partial" : "timeout");
      }, timeoutMs);
      drawCache.waiters.push(onReady);
    });
  }

  // ---------------------------------------------------------------- 控制器

  const controller = {
    hooks: {},
    bound: false,
    els: {},
    items: [],
    listLoaded: false,
    listLoading: false,
    listBusy: false,
    panel: null,
    lastActivator: null,
    lastNoteField: null,
    // 所见即所得编辑器注册的插入回调：保存画板后把 ![标题](drawing:ID) 插进编辑器。
    noteInserter: null,
  };

  // 由 notes-editor.js 注册/清理。fn 接收 { id, title }。
  function setNoteInserter(fn) {
    controller.noteInserter = typeof fn === "function" ? fn : null;
  }

  function clearNoteInserter() {
    controller.noteInserter = null;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function configure(value) {
    controller.hooks = value || {};
    bindDom();
    primeDrawCache();
  }

  function reset() {
    // 登出/换号：关闭面板但不要触发任何网络请求（会话已失效）。
    closePanel(true, { reload: false });
    controller.items = [];
    controller.listLoaded = false;
    controller.listLoading = false;
    renderList();
    const status = $("draw-list-status");
    if (status) status.hidden = true;
  }

  function ownedIds() {
    return new Set(controller.items.map((item) => item.id));
  }

  function bindDom() {
    if (controller.bound) return;
    if (!document.getElementById("draw-new")) return; // DOM 尚未就绪
    controller.bound = true;
    document.getElementById("draw-new").addEventListener("click", () => {
      controller.lastActivator = document.activeElement;
      createDrawing();
    });
    document.getElementById("draw-list").addEventListener("click", onListClick);
    document.getElementById("draw-close").addEventListener("click", () => requestClose(false));
    document.getElementById("draw-insert").addEventListener("click", insertIntoNote);
    document.getElementById("draw-retry").addEventListener("click", () => {
      const panel = controller.panel;
      if (!panel) return;
      if (panel.conflict) {
        reopenCurrent();
      } else {
        panel.pendingScene = panel.pendingScene || panel.lastScene;
        panel.dirty = true;
        flushSave();
      }
    });
    document.getElementById("draw-reload").addEventListener("click", () => {
      const panel = controller.panel;
      if (panel && panel.current) openDrawing(panel.current.id);
    });
    document.getElementById("draw-panel").addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        requestClose(false);
      }
    });
    if (typeof window.addEventListener === "function") {
      window.addEventListener("popstate", () => {
        const panel = controller.panel;
        if (!panel || panel.closed || !panel.historyPushed) return;
        panel.historyPushed = false; // 浏览器已经退回一步，关闭面板时不要再退一次
        void requestClose(false).then(() => {
          if (panel.closed) return;
          // 在“有未保存更改”的确认里选了取消：补回一格历史，画板继续打开。
          try {
            window.history.pushState({ oyDraw: true }, "", window.location.href);
            panel.historyPushed = true;
          } catch (_error) {
            /* 忽略 */
          }
        });
      });
    }
    // 笔记正文里的画板缩略图：事件委托打开；error 不冒泡，用捕获阶段兜底占位。
    document.addEventListener("click", (event) => {
      const ref = event.target.closest && event.target.closest(".notes-drawing-ref");
      if (!ref) return;
      const id = Number(ref.dataset.drawingId);
      if (Number.isInteger(id) && id > 0) {
        controller.lastActivator = ref;
        openDrawing(id);
      }
    });
    document.addEventListener("error", (event) => {
      const img = event.target;
      if (img && img.classList && img.classList.contains("notes-drawing-thumb")) {
        const ref = img.closest && img.closest(".notes-drawing-ref");
        if (ref) ref.classList.add("is-missing");
      }
    }, true);
    // 记录笔记编辑区最后聚焦的输入框，供“插入到笔记”定位光标。
    document.addEventListener("focusin", (event) => {
      const el = event.target;
      if (el && el.id === "notes-content") controller.lastNoteField = el;
      if (el && el.classList && el.classList.contains("notes-edit-content")) controller.lastNoteField = el;
    });
    window.addEventListener("message", onFrameMessage);
    window.addEventListener("beforeunload", (event) => {
      const panel = controller.panel;
      if (panel && !panel.closed && panel.dirty && !panel.conflict) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  }

  // ---------------------------------------------------------------- 列表

  async function load(options = {}) {
    if (controller.listLoading) return;
    const hooks = controller.hooks;
    controller.listLoading = true;
    setListStatus("");
    try {
      const data = await hooks.api(
        `/api/drawings?limit=${PAGE_SIZE}&offset=0`
      );
      controller.items = Array.isArray(data.items) ? data.items : [];
      controller.listLoaded = true;
      renderList();
      if (options.notify !== false && typeof hooks.onDrawingsChanged === "function") {
        hooks.onDrawingsChanged();
      }
    } catch (error) {
      if (error && error.status === 401) return; // 宿主会统一跳转登录
      setListStatus("画板列表加载失败，请刷新页面重试");
    } finally {
      controller.listLoading = false;
    }
  }

  function setListStatus(text) {
    const el = $("draw-list-status");
    if (!el) return;
    el.textContent = text;
    el.hidden = !text;
  }

  function renderList() {
    const list = $("draw-list");
    if (!list) return;
    list.replaceChildren();
    if (!controller.items.length) {
      const empty = document.createElement("p");
      empty.className = "draw-empty";
      empty.textContent = controller.listLoaded ? "还没有画板，点击上方按钮新建。" : "画板加载中…";
      list.appendChild(empty);
      return;
    }
    for (const item of controller.items) list.appendChild(renderCard(item));
  }

  function renderCard(item) {
    const card = document.createElement("div");
    card.className = "draw-card";
    card.dataset.drawingId = String(item.id);

    const open = document.createElement("button");
    open.type = "button";
    open.className = "draw-card-open";
    open.title = "打开画板";

    const thumb = document.createElement("span");
    thumb.className = "draw-thumb";
    if (item.has_thumb) {
      const img = document.createElement("img");
      img.className = "draw-thumb-img";
      img.src = thumbUrl(item.id);
      img.alt = "";
      img.loading = "lazy";
      thumb.appendChild(img);
    } else {
      const placeholder = document.createElement("span");
      placeholder.className = "draw-thumb-placeholder";
      placeholder.textContent = "画板";
      thumb.appendChild(placeholder);
    }

    const meta = document.createElement("span");
    meta.className = "draw-card-meta";
    const title = document.createElement("strong");
    title.className = "draw-card-title";
    title.textContent = item.title || "未命名画板";
    const time = document.createElement("span");
    time.className = "draw-card-time";
    time.textContent = formatTime(item.updated_at);
    meta.append(title, time);
    open.append(thumb, meta);

    const actions = document.createElement("span");
    actions.className = "draw-card-actions";
    const rename = document.createElement("button");
    rename.type = "button";
    rename.className = "draw-card-btn draw-rename";
    rename.textContent = "重命名";
    const del = document.createElement("button");
    del.type = "button";
    del.className = "draw-card-btn draw-delete";
    del.textContent = "删除";
    actions.append(rename, del);

    card.append(open, actions);
    return card;
  }

  async function onListClick(event) {
    const card = event.target.closest && event.target.closest(".draw-card");
    if (!card) return;
    const id = Number(card.dataset.drawingId);
    if (!Number.isInteger(id) || id <= 0) return;
    if (event.target.closest(".draw-delete")) {
      await onDelete(id, event.target);
      return;
    }
    if (event.target.closest(".draw-rename")) {
      onRename(id, card);
      return;
    }
    if (event.target.closest(".draw-card-open")) {
      controller.lastActivator = event.target;
      openDrawing(id);
    }
  }

  async function onDelete(id, button) {
    const item = controller.items.find((entry) => entry.id === id);
    const name = (item && item.title) || "该画板";
    if (!controller.hooks.confirm(`确定删除画板「${name}」吗？此操作可在数据导出中找回场景数据。`)) return;
    if (button) button.disabled = true;
    try {
      await controller.hooks.api(`/api/drawings/${id}`, { method: "DELETE" });
      await load();
    } catch (error) {
      if (button) button.disabled = false;
      controller.hooks.notify("删除失败，请稍后重试", true);
    }
  }

  function onRename(id, card) {
    if (card.querySelector(".draw-rename-input")) return;
    const item = controller.items.find((entry) => entry.id === id);
    const actions = card.querySelector(".draw-card-actions");
    const titleEl = card.querySelector(".draw-card-title");
    const wrap = document.createElement("span");
    wrap.className = "draw-rename-row";
    const input = document.createElement("input");
    input.className = "draw-rename-input";
    input.maxLength = 100;
    input.value = (item && item.title) || "";
    input.setAttribute("aria-label", "新的画板标题");
    const save = document.createElement("button");
    save.type = "button";
    save.className = "draw-card-btn";
    save.textContent = "保存";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "draw-card-btn";
    cancel.textContent = "取消";
    wrap.append(input, save, cancel);
    actions.hidden = true;
    titleEl.replaceWith(wrap);
    input.focus();
    if (typeof input.select === "function") input.select();

    const restore = () => {
      wrap.replaceWith(titleEl);
      actions.hidden = false;
    };
    const submit = async () => {
      const title = input.value.trim();
      if (!title) {
        controller.hooks.notify("标题不能为空", true);
        return;
      }
      save.disabled = true;
      try {
        await controller.hooks.api(`/api/drawings/${id}`, {
          method: "PATCH",
          body: JSON.stringify({ title }),
        });
        await load();
      } catch (error) {
        save.disabled = false;
        controller.hooks.notify("重命名失败，请稍后重试", true);
      }
    };
    save.addEventListener("click", submit);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        submit();
      }
      if (event.key === "Escape") restore();
    });
    cancel.addEventListener("click", restore);
  }

  async function createDrawing() {
    try {
      const data = await controller.hooks.api("/api/drawings", {
        method: "POST",
        body: JSON.stringify({ title: "" }),
      });
      await load();
      openDrawing(data.id);
    } catch (error) {
      const hint = error && error.status === 422 && error.message
        ? error.message
        : "新建画板失败，请稍后重试";
      controller.hooks.notify(hint, true);
    }
  }

  // ---------------------------------------------------------------- 面板

  function ensurePanel() {
    if (controller.panel) return controller.panel;
    const el = $("draw-panel");
    const panel = {
      el,
      frame: $("draw-frame"),
      loading: $("draw-loading"),
      loadingTip: $("draw-loading-tip"),
      reloadBtn: $("draw-reload"),
      saveStatus: $("draw-save-status"),
      errorBar: $("draw-error"),
      errorText: $("draw-error").querySelector("span"),
      titleEl: $("draw-panel-title"),
      insertBtn: $("draw-insert"),
      current: null,
      detail: null,
      ready: false,
      detailLoaded: false,
      readyTimer: 0,
      slowTimer: 0,
      saveTimer: 0,
      saveSeq: 0,
      saving: false,
      dirty: false,
      conflict: false,
      saveState: "idle",
      pendingScene: null,
      lastScene: null,
      pngQueue: null,
      closing: false,
      closed: true,
    };
    controller.panel = panel;
    return panel;
  }

  function setPanelLoading(panel, loading) {
    panel.loading.hidden = !loading;
    // 用 class 控制显隐，不写行内 style（静态契约）。
    panel.frame.classList.toggle("is-loading", loading);
    if (!loading) {
      panel.loadingTip.hidden = true;
      panel.reloadBtn.hidden = true;
    }
  }

  function setSaveState(panel, state) {
    panel.saveState = state;
    const map = {
      idle: "",
      saving: "保存中…",
      saved: "已保存",
      error: "保存失败",
      conflict: "这张画板在别处被修改，请刷新",
      unauthorized: "登录已过期，请重新登录",
    };
    panel.saveStatus.textContent = map[state] || "";
    panel.saveStatus.dataset.state = state;
    panel.errorBar.hidden = state !== "error" && state !== "conflict" && state !== "unauthorized";
    if (state === "conflict") {
      panel.errorText.textContent = "这张画板在别处被修改，请刷新";
      $("draw-retry").textContent = "刷新";
    } else if (state === "unauthorized") {
      panel.errorText.textContent = "登录已过期，请重新登录后再试";
      $("draw-retry").textContent = "重试";
    } else if (state === "error") {
      panel.errorText.textContent = "自动保存失败，可以继续编辑后点重试";
      $("draw-retry").textContent = "重试";
    }
  }

  async function openDrawing(id) {
    const panel = ensurePanel();
    // 重开/切换：清掉上一个面板的所有定时器与状态。
    teardownPanelSession(panel);
    drawCache.gateSeq += 1;
    panel.current = { id };
    panel.closed = false;
    // 浏览器的“后退”应当先关掉画板、留在笔记页，而不是直接离开本站：
    // 打开时压入一格历史，popstate 时关闭面板；用按钮或 Esc 关闭时再退回这一格。
    if (!panel.historyPushed) {
      try {
        window.history.pushState({ oyDraw: true }, "", window.location.href);
        panel.historyPushed = true;
      } catch (_error) {
        /* 没有 history 能力时忽略，仍可用按钮关闭 */
      }
    }
    panel.closing = false;
    panel.ready = false;
    panel.detailLoaded = false;
    panel.detail = null;
    panel.dirty = false;
    panel.conflict = false;
    panel.pendingScene = null;
    panel.lastScene = null;
    panel.el.hidden = false;
    panel.el.setAttribute("tabindex", "-1");
    setPanelLoading(panel, true);
    setSaveState(panel, "idle");
    panel.titleEl.textContent = "画板加载中…";
    panel.insertBtn.disabled = true;
    panel.loadingTip.textContent = "首次打开画板需要准备本地资源（约 8MB），请稍候…";
    panel.loadingTip.hidden = false;
    panel.reloadBtn.hidden = true;

    // 先取详情（轻量 API），再等 SW 把画板静态资源（含数百个字体分片）缓存好；
    // 冷启动字体本地候选只有在缓存瞬时命中时才能跑赢被 CSP 否决的外链兜底。
    let detail = null;
    let detailError = null;
    try {
      detail = await controller.hooks.api(`/api/drawings/${id}`);
    } catch (error) {
      detailError = error;
    }
    if (panel.closed) return; // 等待期间面板已被关闭/切换
    const gate = await waitDrawCacheReady(CACHE_GATE_MS);
    if (panel.closed) return;
    if (detailError) {
      panel.loadingTip.textContent = detailError && detailError.status === 404
        ? "画板不存在或已被删除"
        : "画板内容加载失败，请关闭后重试";
      panel.loadingTip.hidden = false;
      panel.reloadBtn.hidden = true;
      return;
    }
    panel.detail = detail;
    panel.current = { id: detail.id, title: detail.title, version: detail.version };
    panel.detailLoaded = true;
    panel.titleEl.textContent = detail.title || "未命名画板";
    panel.insertBtn.disabled = false;
    if (gate === "ready" || gate === "partial") panel.loadingTip.hidden = true;
    // 每次打开都重新挂载 iframe，确保 draw:ready 生命周期干净。
    panel.frame.src = `${DRAW_PATH}?theme=${encodeURIComponent(currentDrawTheme())}&lang=zh-CN`;
    panel.slowTimer = window.setTimeout(() => {
      panel.loadingTip.textContent = "加载较慢，请稍候或刷新";
      panel.loadingTip.hidden = false;
      panel.reloadBtn.hidden = false;
    }, READY_TIMEOUT_MS);
    trySendLoad(panel);
  }

  function reopenCurrent() {
    const panel = controller.panel;
    if (panel && panel.current) openDrawing(panel.current.id);
  }

  function trySendLoad(panel) {
    if (!panel.ready || !panel.detailLoaded) return;
    postToFrame(panel, { type: "draw:load", scene: panel.detail.scene || null, readOnly: false });
    setPanelLoading(panel, false);
    try {
      panel.frame.focus();
    } catch (_error) {
      /* 焦点失败不影响使用 */
    }
  }

  function teardownPanelSession(panel) {
    if (panel.readyTimer) window.clearTimeout(panel.readyTimer);
    if (panel.slowTimer) window.clearTimeout(panel.slowTimer);
    if (panel.saveTimer) window.clearTimeout(panel.saveTimer);
    panel.readyTimer = 0;
    panel.slowTimer = 0;
    panel.saveTimer = 0;
    if (panel.pngQueue) {
      panel.pngQueue.reject(new Error("panel closed"));
      panel.pngQueue = null;
    }
  }

  function postToFrame(panel, message) {
    if (panel.frame && panel.frame.contentWindow) {
      panel.frame.contentWindow.postMessage(message, currentOrigin());
    }
  }

  function onFrameMessage(event) {
    const panel = controller.panel;
    if (!panel || panel.closed || !panel.frame) return;
    if (!isTrustedFrameMessage(event, currentOrigin(), panel.frame.contentWindow)) return;
    const data = event.data;
    if (data.type === "draw:ready") {
      if (panel.ready) return; // 生命周期内只认第一次
      panel.ready = true;
      if (panel.readyTimer) window.clearTimeout(panel.readyTimer);
      if (panel.slowTimer) window.clearTimeout(panel.slowTimer);
      panel.readyTimer = 0;
      panel.slowTimer = 0;
      trySendLoad(panel);
      return;
    }
    if (!panel.ready || !panel.detailLoaded) return;
    if (data.type === "draw:change") {
      if (!data.scene || typeof data.scene !== "object") return;
      panel.lastScene = data.scene;
      panel.pendingScene = data.scene;
      panel.dirty = true;
      if (panel.conflict) return; // 冲突后停止自动保存，等用户刷新
      setSaveState(panel, "saving");
      if (panel.saveTimer) window.clearTimeout(panel.saveTimer);
      panel.saveTimer = window.setTimeout(() => flushSave(), SAVE_DEBOUNCE_MS);
      return;
    }
    if (data.type === "draw:png") {
      if (panel.pngQueue) {
        panel.pngQueue.resolve(data.dataUrl || "");
        panel.pngQueue = null;
      }
      return;
    }
    if (data.type === "draw:error") {
      panel.saveStatus.textContent = "画板内部报错，可继续编辑，更改会在恢复后保存";
      panel.saveStatus.dataset.state = "error";
    }
  }

  async function flushSave() {
    const panel = controller.panel;
    if (!panel || panel.closed || !panel.current) return;
    if (panel.conflict || !panel.pendingScene) return;
    if (panel.saving) return; // 在途请求回来后发现 pendingScene 变化会再存一次
    const scene = panel.pendingScene;
    const version = panel.current.version;
    const seq = panel.saveSeq + 1;
    panel.saveSeq = seq;
    panel.saving = true;
    setSaveState(panel, "saving");
    try {
      const data = await controller.hooks.api(
        `/api/drawings/${panel.current.id}`,
        { method: "PUT", body: JSON.stringify({ scene, version }) }
      );
      if (panel.closed || seq !== panel.saveSeq) return;
      panel.current.version = data.version;
      if (panel.pendingScene !== scene) {
        panel.saving = false;
        flushSave();
        return;
      }
      panel.dirty = false;
      panel.pendingScene = null;
      setSaveState(panel, "saved");
    } catch (error) {
      if (panel.closed || seq !== panel.saveSeq) return;
      if (error && error.status === 409) {
        panel.conflict = true;
        panel.dirty = true;
        setSaveState(panel, "conflict");
      } else if (error && error.status === 401) {
        setSaveState(panel, "unauthorized");
      } else {
        setSaveState(panel, "error");
      }
    } finally {
      if (panel && !panel.closed && panel.saveSeq === seq) panel.saving = false;
    }
  }

  async function requestClose(force) {
    const panel = controller.panel;
    if (!panel || panel.closed || panel.closing) return;
    if (!force && (panel.dirty || panel.saving) && !panel.conflict) {
      const ok = controller.hooks.confirm("画板还有未保存的更改，确定关闭吗？");
      if (!ok) return;
    }
    panel.closing = true;
    // 关闭前：先尽力落盘最后一次编辑，再导出缩略图上传；任何一步失败都不阻塞关闭。
    try {
      if (panel.pendingScene && !panel.conflict) {
        if (panel.saveTimer) window.clearTimeout(panel.saveTimer);
        await flushSave();
      }
      if (panel.ready && panel.detailLoaded && !panel.conflict) {
        await exportAndUploadThumb(panel);
      }
    } catch (_error) {
      controller.hooks.notify("画板缩略图保存失败，不影响场景内容", true);
    }
    hidePanel(panel);
  }

  function closePanel(force, options = {}) {
    const panel = controller.panel;
    if (!panel || panel.closed) return;
    panel.closing = true;
    hidePanel(panel, options);
  }

  function hidePanel(panel, options = {}) {
    if (panel.historyPushed) {
      panel.historyPushed = false;
      try {
        window.history.back(); // 退回打开时压入的那一格，保持浏览器历史干净
      } catch (_error) {
        /* 忽略 */
      }
    }
    teardownPanelSession(panel);
    panel.ready = false;
    panel.closed = true;
    panel.el.hidden = true;
    panel.frame.src = "about:blank";
    setPanelLoading(panel, true);
    setSaveState(panel, "idle");
    const activator = controller.lastActivator;
    if (activator && typeof activator.focus === "function") {
      try {
        activator.focus();
      } catch (_error) {
        /* 原激活元素已消失时忽略 */
      }
    }
    // 关闭后刷新列表（新缩略图）；登出 reset 时不发请求。
    if (options.reload !== false) load();
  }

  function requestPng(panel) {
    postToFrame(panel, {
      type: "draw:export-png",
      mimeType: "image/png",
      quality: 0.85,
      autoFit: true,
      background: "#ffffff",
      padding: 20,
      maxWidthOrHeight: 1200,
    });
    return new Promise((resolve, reject) => {
      const timer = window.setTimeout(() => {
        if (panel.pngQueue) {
          panel.pngQueue = null;
          reject(new Error("export timeout"));
        }
      }, PNG_TIMEOUT_MS);
      panel.pngQueue = {
        resolve: (dataUrl) => {
          window.clearTimeout(timer);
          resolve(dataUrl);
        },
        reject: (error) => {
          window.clearTimeout(timer);
          reject(error);
        },
      };
    });
  }

  async function exportAndUploadThumb(panel) {
    const dataUrl = await requestPng(panel);
    if (!dataUrl || dataUrl.indexOf("data:image/png") !== 0) return;
    const bytes = dataUrlToBytes(dataUrl);
    await controller.hooks.api(
      `/api/drawings/${panel.current.id}/thumb`,
      { method: "PUT", headers: { "Content-Type": "image/png" }, body: bytes }
    );
  }

  // 插入到笔记的瞬间主动导出并上传一次缩略图（关闭面板时还会再传一次）。
  // 面板未就绪时返回 false；任何失败都由调用方忽略，不影响场景内容。
  function requestThumbUpload() {
    const panel = controller.panel;
    if (!panel || panel.closed || !panel.ready || !panel.detailLoaded) {
      return Promise.resolve(false);
    }
    return exportAndUploadThumb(panel).then(() => true).catch(() => false);
  }

  function chooseNoteField() {
    const last = controller.lastNoteField;
    if (last && (!document.contains || document.contains(last)) && isVisible(last)) return last;
    const edit = document.querySelector("#notes-page .notes-edit-content:not([hidden])");
    if (edit) return edit;
    return $("notes-content");
  }

  function isVisible(el) {
    return !el.hidden && el.offsetParent !== null;
  }

  function insertIntoNote() {
    const panel = controller.panel;
    if (!panel || !panel.current) return;
    const title = panel.current.title || "未命名画板";
    const payload = { id: panel.current.id, title };
    // 所见即所得编辑器优先：由编辑器把 drawing:ID 以图片节点形式插入当前实例。
    if (typeof controller.noteInserter === "function") {
      try {
        controller.noteInserter(payload);
        controller.hooks.notify?.(`已把画板「${title}」插入到笔记`);
        return;
      } catch (error) {
        // 插入器异常时回退到下方 textarea 逻辑。
      }
    }
    const field = chooseNoteField();
    if (!field || field.hidden) {
      controller.hooks.notify("请先打开一篇笔记再插入画板", true);
      return;
    }
    insertAtCursor(field, drawingRefMarkdown(panel.current.id, title));
    field.focus();
    controller.hooks.notify(`已把画板「${title}」插入到笔记光标处`);
  }

  // ---------------------------------------------------------------- 导出

  window.DrawHost = {
    configure,
    load,
    reset,
    openDrawing,
    closePanel,
    requestClose,
    createDrawing,
    requestThumbUpload,
    setNoteInserter,
    clearNoteInserter,
    ownedIds,
    // 纯函数与内部句柄暴露给 node 行为测试。
    isTrustedFrameMessage,
    parseDrawingTarget,
    drawingRefMarkdown,
    sanitizeTitleForMarkdown,
    thumbUrl,
    dataUrlToBytes,
    insertAtCursor,
    onFrameMessage,
    flushSave,
    ensurePanel,
    drawGateDecision,
    waitDrawCacheReady,
    primeDrawCache,
    _state: controller,
    _cache: drawCache,
  };
})();
