"use strict";

/* sw.js —— 欧叶OY 的 Service Worker（作用域 /）。
 *
 * 静态外壳：pwa-register.js 用 /sw.js?v=<版本> 注册，安装后立刻 postMessage
 * {type:"SHELL_LIST", urls:[...]} 把带 ?v= 版本号的资源清单传进来；安装事件等这份清单，
 * 超时（SHELL_LIST_WAIT_MS）没等到就只缓存首页兜底。
 * fetch 策略：
 *   - /api/ 下的请求、非 GET、跨域请求：一律直连网络，绝不进缓存；
 *   - 带 Set-Cookie 的响应：绝不写入缓存；
 *   - 静态资源：cache-first，按带 ?v= 的完整 URL 做键（没有 ?v= 的静态资源不缓存）；
 *   - HTML 导航：network-first，失败时回退到缓存的首页 "/"。
 * 激活时清掉旧版本的外壳缓存；收到 {type:"SKIP_WAITING"} 时 skipWaiting()。 */

const VERSION = new URL(self.location.href).searchParams.get("v") || "1";
const CACHE_PREFIX = "ouye-shell-v";
const SHELL_CACHE = CACHE_PREFIX + VERSION;
const FALLBACK_SHELL = ["/"];
const SHELL_LIST_WAIT_MS = 8000;

/* ---------- 安装期的外壳清单：等 pwa-register.js postMessage 送来 ---------- */
let shellListResolve = null;
const shellList = new Promise((resolve) => {
  shellListResolve = resolve;
  self.setTimeout(() => resolve(FALLBACK_SHELL), SHELL_LIST_WAIT_MS);
});

/** 只接受同源、以 / 开头、不带 // 的路径，避免清单被注入外部地址。 */
function sanitizeShellUrls(value) {
  if (!Array.isArray(value)) return null;
  const urls = [];
  for (const item of value) {
    if (typeof item !== "string") return null;
    if (!item.startsWith("/") || item.startsWith("//")) return null;
    if (item.length > 300) return null;
    urls.push(item);
  }
  return urls.length ? urls : null;
}

self.addEventListener("message", (event) => {
  const data = event.data;
  if (!data || typeof data !== "object") return;
  if (data.type === "SKIP_WAITING") {
    self.skipWaiting();
    return;
  }
  if (data.type === "SHELL_LIST") {
    const urls = sanitizeShellUrls(data.urls);
    if (urls && shellListResolve) shellListResolve(urls);
    return;
  }
  // 画板资源预缓存：宿主在笔记页空闲时 / 打开画板前请求。
  // waitUntil 延长 worker 寿命，保证几百个字体分片能拉完。
  if (data.type === "DRAW_PRECACHE") {
    event.waitUntil(precacheDrawAssets());
    return;
  }
  if (data.type === "DRAW_CACHE_STATUS") {
    event.waitUntil((async () => {
      if (!drawPrecachePromise) precacheDrawAssets();
      await drawPrecachePromise.catch(() => {});
      await announceDrawStatus(event.source);
    })());
  }
});

/* ---------- 画板资源预缓存（/static/draw/，约 8MB） ----------
 * 自托管 Excalidraw 的每个手绘字体面在构造时 src 带两个候选：本地同源 URL
 * 在前、跨源 CDN 兜底在后。浏览器对多源 src 会并发派发候选，冷启动一次性
 * 注册数百个字体面时，跨源候选被 CSP 瞬时否决、本地候选也被整体挤掉
 * （实测冷启动 230 个字体面全部只发跨源、本地零请求）。根治靠 draw-font-shim.js
 * 在 FontFace 构造前剔除跨源候选（见 drawNavigation 注入）；预缓存则保证：
 * 打开画板前在网络空闲时把清单内资源全部缓存，挂载时本地候选瞬时命中，
 * 首屏不卡在 8MB 资源，并满足“断网后画板仍可打开”。幂等，可重复触发。 */
const DRAW_MANIFEST_URL = "/static/draw-manifest.json";
const DRAW_PRECURRENCY = 6;
let drawPrecachePromise = null;
const drawPrecacheState = { done: false, total: 0, cached: 0, failed: 0 };

function sanitizeDrawUrls(value) {
  if (!Array.isArray(value)) return [];
  const urls = [];
  for (const item of value) {
    if (typeof item !== "string") continue;
    if (item.startsWith("//")) continue;
    if (!item.startsWith("/static/draw/") && item !== DRAW_SHIM_URL) continue;
    if (item.length > 300 || /[*"'<>\\^`]/.test(item)) continue;
    urls.push(item);
  }
  return urls;
}

async function announceDrawStatus(source) {
  const payload = { type: "DRAW_CACHE_STATUS", ...drawPrecacheState };
  if (source && typeof source.postMessage === "function") {
    source.postMessage(payload);
    return;
  }
  const clients = await self.clients.matchAll({ includeUncontrolled: true });
  for (const client of clients) client.postMessage(payload);
}

function precacheDrawAssets() {
  if (drawPrecachePromise) return drawPrecachePromise;
  drawPrecachePromise = (async () => {
    let urls = [];
    try {
      const manifestResp = await fetch(DRAW_MANIFEST_URL, { cache: "no-store" });
      if (manifestResp.ok) {
        const manifest = await manifestResp.json();
        urls = sanitizeDrawUrls(manifest && manifest.urls);
      }
    } catch {
      urls = [];
    }
    // shim 在 dist 之外，清单不包含它，但它必须随画板一起离线可用。
    if (!urls.includes(DRAW_SHIM_URL)) urls.unshift(DRAW_SHIM_URL);
    drawPrecacheState.total = urls.length;
    drawPrecacheState.cached = 0;
    drawPrecacheState.failed = 0;
    const cache = await caches.open(SHELL_CACHE);
    let index = 0;
    const runOne = async () => {
      while (index < urls.length) {
        const url = urls[index];
        index += 1;
        try {
          const hit = await cache.match(url);
          if (hit) {
            drawPrecacheState.cached += 1;
            continue;
          }
          const response = await fetch(url, { cache: "no-store" });
          if (response.ok && !response.headers.get("set-cookie")) {
            await cache.put(url, response.clone());
          } else {
            drawPrecacheState.failed += 1;
          }
          drawPrecacheState.cached += 1;
        } catch {
          drawPrecacheState.failed += 1;
        }
        if (index % 25 === 0) await announceDrawStatus().catch(() => {});
      }
    };
    const workers = [];
    for (let i = 0; i < DRAW_PRECURRENCY; i += 1) workers.push(runOne());
    await Promise.all(workers);
    drawPrecacheState.done = true;
    await announceDrawStatus().catch(() => {});
  })().catch((error) => {
    // 失败后允许下次消息重新触发。
    drawPrecachePromise = null;
    drawPrecacheState.done = false;
    console.error("draw assets precache failed", error);
  });
  return drawPrecachePromise;
}

/** 预缓存：逐个抓取，单个失败不阻塞安装；带 Set-Cookie 的响应不写缓存。 */
async function precache() {
  const urls = await shellList;
  const cache = await caches.open(SHELL_CACHE);
  await Promise.all(urls.map(async (url) => {
    try {
      const response = await fetch(new Request(url, { credentials: "omit" }));
      if (response && response.ok && !response.headers.get("set-cookie")) {
        await cache.put(url, response);
      }
    } catch {
      // 某个资源拉不到不阻塞整个安装；运行时还有 cache-first 兜底。
    }
  }));
}

self.addEventListener("install", (event) => {
  event.waitUntil(precache());
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    const names = await caches.keys();
    await Promise.all(names
      .filter((name) => name.startsWith(CACHE_PREFIX) && name !== SHELL_CACHE)
      .map((name) => caches.delete(name)));
    await self.clients.claim();
  })());
  // 激活后后台预热画板资源；不阻塞接管页面（寿命由宿主的消息 waitUntil 兜底）。
  precacheDrawAssets();
});

/* ---------- fetch ---------- */

function cacheKey(url) {
  return url.pathname + url.search;
}

async function cacheFirst(request, url) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(cacheKey(url));
  if (hit) return hit;
  const response = await fetch(request);
  // 只缓存带 ?v= 版本号的成功响应；Set-Cookie 响应绝不进缓存。
  if (response.ok && /[?&]v=/.test(url.search) && !response.headers.get("set-cookie")) {
    await cache.put(cacheKey(url), response.clone());
  }
  return response;
}

/* 画板静态页（/static/draw/）：首屏约 8MB，要求断网后仍可打开。
 * draw.html 走 network-first 并缓存固定键；assets 文件名带内容哈希，
 * 天然不可变，cache-first 且不要求 ?v=。仅作用于该路径。 */

/* 同源化前置脚本（dist 之外的新文件）：注入到 draw.html 的 <head>，
 * 先于 bundle 执行，把 FontFace 的跨源兜底候选剔除。 */
const DRAW_SHIM_URL = "/static/draw-font-shim.js";
const DRAW_SHIM_TAG = '<script src="' + DRAW_SHIM_URL + '"></script>';

function injectDrawShim(html) {
  if (typeof html !== "string" || html.indexOf(DRAW_SHIM_TAG) !== -1) return html;
  if (html.indexOf("<head>") !== -1) {
    return html.replace("<head>", "<head>\n  " + DRAW_SHIM_TAG);
  }
  return html;
}

function htmlResponse(original, text) {
  const headers = new Headers(original.headers);
  headers.set("Content-Type", "text/html; charset=utf-8");
  return new Response(text, { status: original.status, statusText: original.statusText, headers });
}

async function drawNavigation(request) {
  const cache = await caches.open(SHELL_CACHE);
  const htmlKey = "/static/draw/draw.html";
  try {
    // no-store：文档响应自带安全头（CSP），不能让 HTTP 缓存的 304 复用旧头，
    // 否则升级后的 CSP（如内联块 hash 白名单）无法到达 iframe。
    const response = await fetch(request, { cache: "no-store" });
    if (response.ok && !response.headers.get("set-cookie")) {
      const text = injectDrawShim(await response.text());
      const injected = htmlResponse(response, text);
      cache.put(htmlKey, injected.clone());
      return injected;
    }
    return response;
  } catch {
    const hit = await cache.match(htmlKey);
    if (hit) return hit; // 离线缓存的 draw.html 已在写入前注入过 shim
    return networkFirst(request);
  }
}

async function drawAsset(request, url) {
  const cache = await caches.open(SHELL_CACHE);
  // 预缓存以同源绝对路径为键；字体/哈希资源不带查询串，同时尝试 pathname 键。
  const hit = (await cache.match(request.url))
    || (url.search ? cache.match(url.pathname) : null);
  if (hit) return hit;
  const response = await fetch(request);
  if (response.ok && !response.headers.get("set-cookie")) {
    cache.put(url.pathname, response.clone());
  }
  return response;
}

async function networkFirst(request) {
  try {
    return await fetch(request);
  } catch {
    const cache = await caches.open(SHELL_CACHE);
    const fallback = await cache.match("/");
    if (fallback) return fallback;
    return new Response("离线且没有可用的缓存页面。", {
      status: 503,
      headers: { "Content-Type": "text/plain; charset=utf-8" },
    });
  }
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  let url;
  try {
    url = new URL(request.url);
  } catch {
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/")) return; // API 响应绝不缓存
  if (url.pathname.startsWith("/static/draw/") || url.pathname === DRAW_SHIM_URL) {
    // 画板：文档导航 network-first（离线回缓存），哈希资源 cache-first。
    if (request.mode === "navigate") {
      event.respondWith(drawNavigation(request));
    } else {
      event.respondWith(drawAsset(request, url));
    }
    return;
  }
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }
  event.respondWith(cacheFirst(request, url));
});
