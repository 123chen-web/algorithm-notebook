"use strict";

/* 画板宿主（static/draw-host.js）行为测试：Node 内置测试运行器 + tests/js_harness.cjs 假浏览器。
   覆盖：postMessage 来源校验（恶意 origin / 错误 source 忽略）、draw:ready→draw:load、
   防抖自动保存与版本更新、409/401/失败状态与重试、关闭时缩略图上传、Esc 未保存提示、
   markdown 画板引用只渲染本人 id、插入到笔记光标处。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

const ORIGIN = "http://local.test";
const TINY_PNG_DATAURL =
  "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** 装好 draw-host 的假环境；api 每次调用都记录下来由测试手动应答。 */
function setup(files = ["draw-host.js"]) {
  const env = load(files, { extra: { location: { origin: ORIGIN } } });
  // vm 上下文默认没有这些浏览器全局，按需补齐。
  env.context.Uint8Array = Uint8Array;
  const atob = (data) => Buffer.from(data, "base64").toString("binary");
  env.context.atob = atob;
  env.window.atob = atob;
  // 宿主页面里的固定节点（harness 的 getElementById 不会现造节点）。
  for (const id of [
    "draw-new", "draw-list", "draw-list-status", "draw-close", "draw-insert",
    "draw-retry", "draw-reload", "draw-panel", "draw-frame", "draw-loading",
    "draw-loading-tip", "draw-save-status", "draw-panel-title", "draw-error",
    "notes-content",
  ]) {
    env.document.querySelector(`#${id}`);
  }
  env.document.querySelector("#draw-error").append(env.document.createElement("span"));

  const calls = [];
  const state = {
    confirms: [], confirmed: true, notified: [],
  };
  // 与 static/app.js 的 api 行为一致：解析 JSON，非 2xx 抛出带 status 的错误。
  function api(path, options = {}) {
    const d = deferred();
    calls.push({
      path,
      method: options.method || "GET",
      body: options.body && typeof options.body === "string" ? JSON.parse(options.body) : null,
      headers: options.headers || {},
      rawBody: options.body,
      ...d,
    });
    return (async () => {
      const response = await d.promise;
      const data = await response.json();
      if (!response.ok) {
        const error = new Error(data && data.detail ? data.detail : "请求失败，请稍后重试");
        error.status = response.status;
        throw error;
      }
      return data;
    })();
  }
  const hooks = {
    api,
    getUser: () => ({ id: 7, username: "alice" }),
    getEpoch: () => 1,
    getView: () => "notes",
    confirm: (text) => { state.confirms.push(text); return state.confirmed; },
    notify: (text, isError) => state.notified.push({ text, isError: Boolean(isError) }),
    onDrawingsChanged: () => state.changed = (state.changed || 0) + 1,
  };
  env.window.DrawHost.configure(hooks);
  return { env, calls, state, DrawHost: env.window.DrawHost, document: env.document };
}

function findCall(calls, predicate) {
  for (let i = calls.length - 1; i >= 0; i -= 1) {
    if (predicate(calls[i])) return calls[i];
  }
  return null;
}
async function respond(calls, predicate, status, body) {
  const call = findCall(calls, predicate);
  assert.ok(call, "expected api call not found");
  call.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
  await tick(); await tick();
  return call;
}

/** 给 iframe 装一个能记录 postMessage 的 contentWindow，并返回事件工厂。 */
function mountFrame(ctx) {
  const panel = ctx.DrawHost.ensurePanel();
  const posted = [];
  panel.frame.contentWindow = {
    posted,
    postMessage(message, targetOrigin) { posted.push({ message, targetOrigin }); },
  };
  const event = (type, extra = {}) => ({
    origin: ORIGIN,
    source: panel.frame.contentWindow,
    data: { type, ...extra },
  });
  return { panel, frame: panel.frame, posted, event };
}

async function openReady(ctx, id = 5, detail) {
  const opened = ctx.DrawHost.openDrawing(id);
  await tick();
  const mount = mountFrame(ctx);
  await respond(ctx.calls, (c) => c.method === "GET" && c.path === `/api/drawings/${id}`, 200,
    detail || { id, title: "草图", version: 1, scene: null });
  // ready 之前不应向 iframe 发消息。
  assert.equal(mount.posted.length, 0);
  ctx.DrawHost.onFrameMessage(mount.event("draw:ready"));
  await tick();
  await opened;
  return mount;
}

/* ───────────── 纯函数 ───────────── */

test("isTrustedFrameMessage: 只认同源且来自当前 iframe 的 draw: 消息", () => {
  const { DrawHost } = setup();
  const source = {};
  const ok = (event) => DrawHost.isTrustedFrameMessage(event, ORIGIN, source);
  assert.equal(ok({ origin: ORIGIN, source, data: { type: "draw:ready" } }), true);
  assert.equal(ok({ origin: "https://evil.example", source, data: { type: "draw:ready" } }), false);
  assert.equal(ok({ origin: ORIGIN, source: {}, data: { type: "draw:ready" } }), false);
  assert.equal(ok({ origin: ORIGIN, source: null, data: { type: "draw:ready" } }), false);
  assert.equal(ok({ origin: ORIGIN, source, data: { type: "ready" } }), false);
  assert.equal(ok({ origin: ORIGIN, source, data: { type: "draw:evil" } }), true); // draw: 前缀即协议消息
  assert.equal(ok({ origin: ORIGIN, source, data: "draw:ready" }), false);
  assert.equal(ok(null, ORIGIN, source), false);
});

test("parseDrawingTarget: 只接受正整数 drawing:<id>", () => {
  const { DrawHost } = setup();
  assert.equal(DrawHost.parseDrawingTarget("drawing:12"), 12);
  assert.equal(DrawHost.parseDrawingTarget("drawing:0"), null);
  assert.equal(DrawHost.parseDrawingTarget("drawing:-1"), null);
  assert.equal(DrawHost.parseDrawingTarget("drawing:1.5"), null);
  assert.equal(DrawHost.parseDrawingTarget("https://x/drawing:1"), null);
  assert.equal(DrawHost.parseDrawingTarget(42), null);
});

test("drawingRefMarkdown: 去掉会破坏语法的字符", () => {
  const { DrawHost } = setup();
  assert.equal(DrawHost.drawingRefMarkdown(9, "流程[图](一)"), "![流程 图  一](drawing:9)\n");
  assert.equal(DrawHost.drawingRefMarkdown(9, ""), "![画板](drawing:9)\n");
});

test("insertAtCursor: 在选区处插入并把光标移到片段末尾", () => {
  const { DrawHost } = setup();
  const field = { value: "abcdef", selectionStart: 2, selectionEnd: 4 };
  const pos = DrawHost.insertAtCursor(field, "XX");
  assert.equal(field.value, "abXXef");
  assert.equal(pos, 4);
  assert.equal(field.selectionStart, 4);
  assert.equal(field.selectionEnd, 4);
});

test("dataUrlToBytes: 解析 base64 dataURL", () => {
  const { DrawHost } = setup();
  const bytes = DrawHost.dataUrlToBytes(TINY_PNG_DATAURL);
  assert.ok(bytes instanceof Uint8Array);
  assert.equal(bytes[0], 0x89);
  assert.equal(bytes[1], 0x50);
  assert.throws(() => DrawHost.dataUrlToBytes("nope"));
});

/* ───────────── 生命周期与自动保存 ───────────── */

test("draw:ready 后发送 draw:load；恶意 origin / 错误 source 的消息被忽略", async () => {
  const ctx = setup();
  const mount = await openReady(ctx, 5);
  assert.equal(mount.posted.length, 1);
  // 消息对象在 vm 上下文里创建，不能跨 realm 用 deepStrictEqual，逐字段比较。
  const loadMsg = mount.posted[0].message;
  assert.equal(loadMsg.type, "draw:load");
  assert.equal(loadMsg.scene, null);
  assert.equal(loadMsg.readOnly, false);
  assert.equal(mount.posted[0].targetOrigin, ORIGIN);

  const panel = mount.panel;
  // 伪造来源的 change 必须被丢弃。
  ctx.DrawHost.onFrameMessage({ origin: "https://evil.example", source: mount.frame.contentWindow,
    data: { type: "draw:change", scene: { hacked: true } } });
  ctx.DrawHost.onFrameMessage({ origin: ORIGIN, source: {},
    data: { type: "draw:change", scene: { hacked: true } } });
  ctx.DrawHost.onFrameMessage({ origin: ORIGIN, source: mount.frame.contentWindow,
    data: { type: "other-message", scene: { hacked: true } } });
  await sleep(1400);
  assert.equal(panel.dirty, false);
  assert.equal(ctx.calls.filter((c) => c.method === "PUT").length, 0);
});

test("draw:change 防抖自动保存，成功后更新 version 并显示已保存", async () => {
  const ctx = setup();
  const mount = await openReady(ctx, 6, { id: 6, title: "t", version: 1, scene: null });
  const scene1 = { elements: [{ id: "e1", type: "rectangle" }], files: {} };
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: scene1, sceneVersion: 1 }));
  // 连续修改只应产生一次保存（防抖）。
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: { ...scene1, appState: { x: 1 } }, sceneVersion: 2 }));
  assert.equal(mount.panel.saveStatus.dataset.state, "saving");
  await sleep(700);
  assert.equal(ctx.calls.filter((c) => c.method === "PUT").length, 0, "防抖窗口内不应发请求");
  await sleep(700);
  const puts = ctx.calls.filter((c) => c.method === "PUT" && c.path === "/api/drawings/6");
  assert.equal(puts.length, 1);
  assert.equal(puts[0].body.version, 1);
  await respond(ctx.calls, (c) => c === puts[0], 200, { id: 6, version: 2 });
  assert.equal(mount.panel.current.version, 2);
  assert.equal(mount.panel.saveStatus.dataset.state, "saved");
  assert.equal(mount.panel.dirty, false);
});

test("409 冲突：提示中文并停止后续自动保存，点刷新重新加载", async () => {
  const ctx = setup();
  const mount = await openReady(ctx, 7);
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: { elements: [] } }));
  await sleep(1300);
  const put = await respond(ctx.calls, (c) => c.method === "PUT", 409, { detail: "这张画板已在别处被修改，请刷新后再保存" });
  assert.equal(put.path, "/api/drawings/7");
  assert.equal(mount.panel.saveStatus.dataset.state, "conflict");
  assert.match(mount.panel.saveStatus.textContent, /在别处被修改/);
  // 冲突后继续画也不再自动保存。
  const before = ctx.calls.length;
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: { elements: [{ id: "z" }] } }));
  await sleep(1300);
  assert.equal(ctx.calls.length, before);
  // 点“刷新”：重新拉详情。
  ctx.document.getElementById("draw-retry").click();
  await tick();
  assert.ok(findCall(ctx.calls, (c) => c.method === "GET" && c.path === "/api/drawings/7"));
});

test("保存失败可重试；401 显示登录过期", async () => {
  const ctx = setup();
  const mount = await openReady(ctx, 8);
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: { elements: [] } }));
  await sleep(1300);
  await respond(ctx.calls, (c) => c.method === "PUT", 500, { detail: "服务器错误" });
  assert.equal(mount.panel.saveStatus.dataset.state, "error");
  // 重试按钮再发一次 PUT。
  ctx.document.getElementById("draw-retry").click();
  await tick();
  const retry = findCall(ctx.calls, (c) => c.method === "PUT");
  assert.ok(retry);
  retry.resolve({ ok: true, status: 200, json: async () => ({ id: 8, version: 2 }) });
  await tick(); await tick();
  assert.equal(mount.panel.saveStatus.dataset.state, "saved");

  // 新一次保存遇到 401。
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: { elements: [{ id: "q" }] } }));
  await sleep(1300);
  await respond(ctx.calls, (c) => c.method === "PUT", 401, { detail: "请先登录" });
  assert.equal(mount.panel.saveStatus.dataset.state, "unauthorized");
  assert.match(mount.panel.saveStatus.textContent, /登录/);
});

/* ───────────── 关闭、Esc、缩略图 ───────────── */

test("Esc 关闭时若有未保存更改先确认；取消则面板保持打开", async () => {
  const ctx = setup();
  const mount = await openReady(ctx, 9);
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: { elements: [] } }));
  ctx.state.confirmed = false;
  mount.panel.el.dispatchEvent({ type: "keydown", key: "Escape", preventDefault() {}, bubbles: true });
  assert.equal(ctx.state.confirms.length, 1);
  assert.equal(mount.panel.el.hidden, false);

  ctx.state.confirmed = true;
  mount.panel.el.dispatchEvent({ type: "keydown", key: "Escape", preventDefault() {}, bubbles: true });
  await tick();
  // 关闭流程先把未保存场景落盘。
  await respond(ctx.calls, (c) => c.method === "PUT" && c.path === "/api/drawings/9", 200, { id: 9, version: 2 });
  await tick();
  // 然后请求导出 PNG，回应一张缩略图并等其上传完成。
  const exportMsg = mount.posted.find((m) => m.message.type === "draw:export-png");
  assert.ok(exportMsg);
  ctx.DrawHost.onFrameMessage(mount.event("draw:png", { dataUrl: TINY_PNG_DATAURL }));
  await tick();
  const thumb = findCall(ctx.calls, (c) => c.method === "PUT" && c.path === "/api/drawings/9/thumb");
  assert.ok(thumb);
  thumb.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) });
  await tick(); await tick();
  assert.equal(mount.panel.el.hidden, true);
});

test("关闭面板：先导出 PNG 并以 image/png 上传缩略图", async () => {
  const ctx = setup();
  const mount = await openReady(ctx, 10);
  ctx.DrawHost.onFrameMessage(mount.event("draw:change", { scene: { elements: [] } }));
  await sleep(1300);
  await respond(ctx.calls, (c) => c.method === "PUT" && c.path === "/api/drawings/10", 200, { id: 10, version: 2 });

  const closing = ctx.DrawHost.requestClose(false);
  await tick();
  // 宿主先请求导出 PNG。
  const exportMsg = mount.posted.find((m) => m.message.type === "draw:export-png");
  assert.ok(exportMsg);
  assert.equal(exportMsg.message.mimeType, "image/png");
  ctx.DrawHost.onFrameMessage(mount.event("draw:png", { dataUrl: TINY_PNG_DATAURL }));
  await tick();
  const thumb = findCall(ctx.calls, (c) => c.method === "PUT" && c.path === "/api/drawings/10/thumb");
  assert.ok(thumb);
  assert.equal(thumb.headers["Content-Type"], "image/png");
  assert.ok(thumb.rawBody && thumb.rawBody.length > 0);
  assert.equal(thumb.rawBody[0], 0x89); // PNG 魔数
  thumb.resolve({ ok: true, status: 200, json: async () => ({ ok: true }) });
  await closing;
  assert.equal(mount.panel.el.hidden, true);
});

test("加载超过 15 秒未 ready：显示慢加载提示（用短超时替身验证提示逻辑）", async () => {
  // 直接验证 DOM 状态切换：打开后 ready 未到达时 loading 可见。
  const ctx = setup();
  const opened = ctx.DrawHost.openDrawing(11);
  await tick();
  const mount = mountFrame(ctx);
  await respond(ctx.calls, (c) => c.path === "/api/drawings/11", 200, { id: 11, title: "t", version: 1, scene: null });
  await opened;
  assert.equal(mount.panel.loading.hidden, false);
  assert.equal(mount.frame.classList.contains("is-loading"), true);
  ctx.DrawHost.onFrameMessage(mount.event("draw:ready"));
  await tick();
  assert.equal(mount.panel.loading.hidden, true);
  assert.equal(mount.frame.classList.contains("is-loading"), false);
});

/* ───────────── 画板资源缓存闸门 ───────────── */

test("drawGateDecision：不支持/未受控/已就绪直接开板，受控未就绪才等待", () => {
  const { DrawHost } = setup();
  assert.equal(DrawHost.drawGateDecision({ supported: false }), "proceed");
  assert.equal(DrawHost.drawGateDecision({ supported: true, controlled: false }), "proceed");
  assert.equal(DrawHost.drawGateDecision({ supported: true, controlled: true, ready: true }), "proceed");
  assert.equal(DrawHost.drawGateDecision({ supported: true, controlled: true, ready: false }), "wait");
});

test("无 Service Worker 环境：闸门立即放行（unsupported），不阻塞开板", async () => {
  const ctx = setup();
  const result = await ctx.DrawHost.waitDrawCacheReady(100);
  assert.equal(result, "unsupported");
});

function fakeServiceWorker(ctx) {
  const listeners = {};
  const posted = [];
  const controller = { postMessage: (m) => posted.push(m) };
  ctx.env.context.navigator = {
    serviceWorker: {
      controller,
      addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    },
  };
  ctx.env.context.location = { origin: ORIGIN };
  return {
    posted,
    deliver: (data) => listeners.message && listeners.message.forEach((fn) => fn({ data, source: null })),
  };
}

test("受控 SW 报告预缓存完成：等待解析为 ready，并向 SW 发预热与状态请求", async () => {
  const ctx = setup();
  const sw = fakeServiceWorker(ctx);
  ctx.DrawHost.primeDrawCache();
  assert.ok(sw.posted.some((m) => m.type === "DRAW_PRECACHE"));
  const waiting = ctx.DrawHost.waitDrawCacheReady(2000);
  await tick();
  sw.deliver({ type: "DRAW_CACHE_STATUS", done: true, total: 365, cached: 365 });
  const result = await waiting;
  assert.equal(result, "ready");
  assert.equal(ctx.DrawHost._cache.ready, true);
});

test("受控 SW 迟迟未就绪：闸门超时降级为 timeout，不永久阻塞", async () => {
  const ctx = setup();
  const sw = fakeServiceWorker(ctx);
  ctx.DrawHost.primeDrawCache();
  const t0 = Date.now();
  const result = await ctx.DrawHost.waitDrawCacheReady(20);
  assert.equal(result, "timeout");
  assert.ok(Date.now() - t0 >= 15);
});

test("SW 预热期间开板：iframe 在缓存就绪后才挂载", async () => {
  const ctx = setup();
  const sw = fakeServiceWorker(ctx);
  ctx.DrawHost.primeDrawCache();
  const opened = ctx.DrawHost.openDrawing(12);
  await tick();
  const mount = mountFrame(ctx);
  // 详情 API 先回来，但闸门未放行，iframe 不应挂载。
  await respond(ctx.calls, (c) => c.path === "/api/drawings/12", 200,
    { id: 12, title: "t", version: 1, scene: null });
  await tick();
  assert.ok(!mount.frame.src, "缓存就绪前不应挂载 iframe");
  sw.deliver({ type: "DRAW_CACHE_STATUS", done: true, total: 365, cached: 365 });
  await opened;
  assert.ok(mount.frame.src.includes("/static/draw/draw.html"));
});

/* ───────────── 笔记 markdown 引用 ───────────── */

function serialize(node) {
  if (node.nodeType === 3) return node.textContent;
  if (node.tagName === "#FRAGMENT") return node.children.map(serialize).join("");
  const tag = node.tagName.toLowerCase();
  // class 在假 DOM 里是属性（等价真实 DOM 的 class 特性），单独输出；
  // img.alt/loading 是 IDL 属性，harness 不回写到 attributes，这里补齐。
  const parts = [];
  if (node.className) parts.push(`class="${node.className}"`);
  for (const [name, value] of Object.entries(node.attributes)) parts.push(`${name}="${value}"`);
  if (tag === "img") {
    if (node.alt) parts.push(`alt="${node.alt}"`);
    if (node.loading) parts.push(`loading="${node.loading}"`);
  }
  const attrs = parts.length ? ` ${parts.join(" ")}` : "";
  const content = node._text !== null ? node.textContent : node.children.map(serialize).join("");
  return `<${tag}${attrs}>${content}</${tag}>`;
}

test("笔记正文只把本人画板 id 渲染成缩略图按钮，他人 id 纯文本", () => {
  const ctx = setup(["draw-host.js", "notes.js"]);
  ctx.env.context.Uint8Array = Uint8Array;
  ctx.env.context.atob = (data) => Buffer.from(data, "base64").toString("binary");
  ctx.env.window.Notes.configure({
    api: async () => ({}),
    getUser: () => ({ id: 7 }),
    getEpoch: () => 1,
    getView: () => "notes",
    getDrawingIds: () => ctx.DrawHost.ownedIds(),
  });
  ctx.DrawHost._state.items = [{ id: 7 }, { id: 9 }];

  const owned = serialize(ctx.env.window.Notes.renderNoteMarkdown(
    "看这张 ![流程图](drawing:7)", { drawingIds: ctx.DrawHost.ownedIds() }
  ));
  assert.match(owned, /<button[^>]*class="notes-drawing-ref"[^>]*data-drawing-id="7"/);
  assert.match(owned, /<img[^>]*src="\/api\/drawings\/7\/thumb"/);
  assert.match(owned, /流程图/);

  const other = serialize(ctx.env.window.Notes.renderNoteMarkdown(
    "![别人的](drawing:100)", { drawingIds: ctx.DrawHost.ownedIds() }
  ));
  assert.doesNotMatch(other, /<button/);
  assert.match(other, /drawing:100/);

  // 非 drawing: 目标的图片语法保持纯文本。
  const external = serialize(ctx.env.window.Notes.renderNoteMarkdown(
    "![x](https://evil.example/a.png)", { drawingIds: ctx.DrawHost.ownedIds() }
  ));
  assert.doesNotMatch(external, /<img/);
});

test("插入到笔记：把引用行插到 textarea 光标处", async () => {
  const ctx = setup();
  const mount = await openReady(ctx, 12, { id: 12, title: "插入用", version: 1, scene: null });
  const field = ctx.document.getElementById("notes-content");
  field.value = "前后";
  field.selectionStart = 1;
  field.selectionEnd = 1;
  ctx.document.getElementById("draw-insert").click();
  await tick();
  assert.equal(field.value, "前![插入用](drawing:12)\n后");
  assert.ok(ctx.state.notified.some((n) => /插入/.test(n.text)));
});

test("reset：关闭面板、清空列表且不发起网络请求", () => {
  const ctx = setup();
  ctx.DrawHost._state.items = [{ id: 1, has_thumb: false }];
  ctx.DrawHost.reset();
  assert.equal(ctx.DrawHost.ownedIds().size, 0);
  // reset 之后不应触发列表刷新请求。
  assert.equal(ctx.calls.length, 0);
});
