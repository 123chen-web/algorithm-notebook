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
  }
});

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
  if (request.mode === "navigate") {
    event.respondWith(networkFirst(request));
    return;
  }
  event.respondWith(cacheFirst(request, url));
});
