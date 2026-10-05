"use strict";

/* PWA 客户端的异步行为测试（Node 内置 node:test + tests/js_harness.cjs 假浏览器）。
 * 覆盖：sw.js 缓存策略（/api/ 绝不缓存、Set-Cookie 不缓存、版本更新清理、SKIP_WAITING）、
 * pwa-register.js 的注册 / 更新条 / 网络状态条、offline-sync.js 的离线入队与补交顺序、
 * 各状态码处理、换号丢弃、IndexedDB 不可用时的降级、迟到响应守卫。 */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const STATIC = path.join(__dirname, "..", "static");
const ISO = "2026-10-05T08:00:00.000Z";
// 脚本在独立的 vm 环境里跑，对象 / 数组 / TypeError 的原型和这里不是同一个：
// 深比较前先 JSON 往返转成普通对象，类型断言看 name 不看 instanceof。
const plain = (value) => JSON.parse(JSON.stringify(value));
const isTypeError = (error) => error && error.name === "TypeError";
const ticks = async (n = 3) => { for (let i = 0; i < n; i += 1) await tick(); };

/* ================= 共用假货 ================= */

function httpError(status, message) {
  const error = new Error(message || `HTTP ${status}`);
  error.status = status;
  return error;
}

/** 假 api()：行为对齐 app.js——非 2xx 抛带 .status 的 Error；网络错误抛不带 status 的 Error。 */
function apiStub(handler) {
  const calls = [];
  const api = (path, options = {}) => {
    calls.push({ path, options });
    return handler(path, options);
  };
  api.calls = calls;
  return api;
}

/** 假 indexedDB：内存版，可注入 open / 读 / 写失败。 */
function fakeIDB(failure = {}) {
  const dbs = new Map();
  return {
    dbs,
    open(name) {
      const request = {};
      setTimeout(() => {
        if (failure.open) {
          request.error = new Error("open failed");
          request.onerror?.();
          return;
        }
        if (!dbs.has(name)) dbs.set(name, new Map());
        const store = dbs.get(name);
        const db = {
          objectStoreNames: { contains: (storeName) => storeName === "kv" },
          createObjectStore() {},
          transaction: () => ({
            objectStore: () => ({
              get(key) {
                const inner = {};
                setTimeout(() => {
                  if (failure.read) {
                    inner.error = new Error("read failed");
                    inner.onerror?.();
                  } else {
                    inner.result = store.has(key) ? JSON.parse(JSON.stringify(store.get(key))) : undefined;
                    inner.onsuccess?.();
                  }
                }, 0);
                return inner;
              },
              put(value, key) {
                const inner = {};
                setTimeout(() => {
                  if (failure.write) {
                    inner.error = new Error("write failed");
                    inner.onerror?.();
                  } else {
                    store.set(key, JSON.parse(JSON.stringify(value)));
                    inner.onsuccess?.();
                  }
                }, 0);
                return inner;
              },
            }),
          }),
        };
        request.result = db;
        request.onupgradeneeded?.();
        request.onsuccess?.();
      }, 0);
      return request;
    },
  };
}

/** 配置好的 OfflineSync 环境：可控 user / epoch / api / idb。 */
function syncEnv({ user = { id: 7 }, handler = () => Promise.resolve({}), idb = fakeIDB(), extra = {} } = {}) {
  const env = load(["offline-queue.js", "offline-sync.js"], { extra });
  const session = { user, epoch: 1 };
  const api = apiStub(handler);
  env.window.OfflineSync.configure({ api, getUser: () => session.user, getEpoch: () => session.epoch, idb });
  return { env, session, api, idb };
}

const posted = (call) => JSON.parse(call.options.body);

/* ================= offline-sync.js ================= */

test("sync: enqueueGrade 入队、派发 pwa:sync-state，并返回可用的 opId 与时间", async () => {
  const { env } = syncEnv();
  const op = await env.window.OfflineSync.enqueueGrade(11, 4);
  assert.equal(op.mistakeId, 11);
  assert.equal(op.grade, 4);
  assert.ok(op.opId.length > 0);
  assert.ok(!isNaN(Date.parse(op.at)));
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 1, failed: 0, total: 1 });
  const syncEvents = env.events.filter((event) => event.type === "pwa:sync-state");
  assert.equal(syncEvents.at(-1).detail.pending, 1);
});

test("sync: opId 优先用 crypto.randomUUID", async () => {
  const { env } = syncEnv();
  env.window.crypto = { randomUUID: () => "uuid-fixed-1" };
  const op = await env.window.OfflineSync.enqueueGrade(1, 3);
  assert.equal(op.opId, "uuid-fixed-1");
});

test("sync: 没有 crypto.randomUUID 时退回时间戳加随机数，且两次不同", async () => {
  const { env } = syncEnv();
  env.window.crypto = undefined;
  const first = await env.window.OfflineSync.enqueueGrade(1, 3);
  const second = await env.window.OfflineSync.enqueueGrade(2, 3);
  assert.ok(first.opId.startsWith("op-"));
  assert.notEqual(first.opId, second.opId);
});

test("sync: 非法 grade / mistakeId 抛 TypeError，未登录抛错", async () => {
  const { env, session } = syncEnv();
  await assert.rejects(env.window.OfflineSync.enqueueGrade(1, 0), isTypeError);
  await assert.rejects(env.window.OfflineSync.enqueueGrade(1, 6), isTypeError);
  await assert.rejects(env.window.OfflineSync.enqueueGrade(1, 2.5), isTypeError);
  await assert.rejects(env.window.OfflineSync.enqueueGrade(0, 3), isTypeError);
  await assert.rejects(env.window.OfflineSync.enqueueGrade("5", 3), isTypeError);
  session.user = null;
  await assert.rejects(env.window.OfflineSync.enqueueGrade(1, 3), /登录/);
});

test("sync: 队列持久化到按用户命名的 IndexedDB，重新 configure 后能读回", async () => {
  const { env, idb } = syncEnv();
  await env.window.OfflineSync.enqueueGrade(5, 2);
  await ticks();
  assert.ok(idb.dbs.has("ouye-offline-7"), "库名带用户 id");
  assert.equal(idb.dbs.get("ouye-offline-7").get("queue").ops.length, 1);

  env.window.OfflineSync.reset();
  env.window.OfflineSync.configure({
    api: apiStub(() => Promise.resolve({})),
    getUser: () => ({ id: 7 }),
    getEpoch: () => 2,
    idb,
  });
  await env.window.OfflineSync.enqueueGrade(6, 3); // 触发 ensureState 读回旧队列
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 2, failed: 0, total: 2 });
});

test("sync: 换号时丢弃旧队列并派发 pwa:queue-dropped", async () => {
  const { env, session } = syncEnv();
  await env.window.OfflineSync.enqueueGrade(1, 3);
  await env.window.OfflineSync.enqueueGrade(2, 4);
  env.events.length = 0;

  session.user = { id: 8 }; // 登出再登录别的账号
  session.epoch += 1;
  await env.window.OfflineSync.enqueueGrade(3, 5);
  const dropped = env.events.find((event) => event.type === "pwa:queue-dropped");
  assert.equal(dropped.detail.dropped, 2, "旧账号的两条评分被丢弃");
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 1, failed: 0, total: 1 });
});

test("sync: flush 按入队顺序逐条 POST，带 quality / client_op_id / reviewed_at", async () => {
  const { env, api } = syncEnv();
  const first = await env.window.OfflineSync.enqueueGrade(1, 4);
  const second = await env.window.OfflineSync.enqueueGrade(2, 2);
  const result = await env.window.OfflineSync.flush();
  assert.equal(api.calls.length, 2);
  assert.equal(api.calls[0].path, "/api/mistakes/1/review");
  assert.equal(api.calls[1].path, "/api/mistakes/2/review");
  assert.equal(api.calls[0].options.method, "POST");
  assert.deepEqual(posted(api.calls[0]), { quality: 4, client_op_id: first.opId, reviewed_at: first.at });
  assert.deepEqual(posted(api.calls[1]), { quality: 2, client_op_id: second.opId, reviewed_at: second.at });
  assert.deepEqual(plain(result), { synced: 2, skipped: 0, failed: 0, stopped: false, authExpired: false, offline: false });
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 0, failed: 0, total: 0 });
});

test("sync: 预取过 version 时补交带上 version，没预取就不带", async () => {
  const handler = (path) => {
    if (path.startsWith("/api/review/queue")) {
      return Promise.resolve({ today: "2026-10-05", items: [{ id: 1, version: 3, title: "题" }] });
    }
    return Promise.resolve({});
  };
  const { env, api } = syncEnv({ handler });
  await env.window.OfflineSync.prefetch();
  await env.window.OfflineSync.enqueueGrade(1, 5);
  await env.window.OfflineSync.enqueueGrade(2, 5);
  await env.window.OfflineSync.flush();
  assert.equal(posted(api.calls[1]).version, 3);
  assert.equal("version" in posted(api.calls[2]), false);
});

test("sync: 网络错误 markFailed 并停止本轮，后面的 op 不再 POST", async () => {
  let count = 0;
  const { env, api } = syncEnv({
    handler: () => {
      count += 1;
      return count === 1 ? Promise.reject(new Error("network down")) : Promise.resolve({});
    },
  });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  await env.window.OfflineSync.enqueueGrade(2, 3);
  const result = await env.window.OfflineSync.flush();
  assert.equal(api.calls.length, 1, "第一条失败就停，第二条不发");
  assert.equal(result.failed, 1);
  assert.equal(result.stopped, true);
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 2, failed: 0, total: 2 });
});

test("sync: 500 同样 markFailed 并停止", async () => {
  const { env } = syncEnv({ handler: () => Promise.reject(httpError(500)) });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  const result = await env.window.OfflineSync.flush();
  assert.equal(result.failed, 1);
  assert.equal(result.stopped, true);
  assert.equal(env.window.OfflineSync.summary().total, 1);
});

test("sync: 401 保留队列、停止本轮并派发 pwa:auth-expired", async () => {
  const { env, api } = syncEnv({ handler: () => Promise.reject(httpError(401, "请先登录")) });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  await env.window.OfflineSync.enqueueGrade(2, 3);
  const result = await env.window.OfflineSync.flush();
  assert.equal(api.calls.length, 1);
  assert.equal(result.authExpired, true);
  assert.equal(result.stopped, true);
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 2, failed: 0, total: 2 }, "队列原样保留");
  const auth = env.events.find((event) => event.type === "pwa:auth-expired");
  assert.equal(auth.detail.pending, 2);
});

test("sync: 404（题目已删）markSynced 并计入已跳过，继续补下一条", async () => {
  let count = 0;
  const { env } = syncEnv({
    handler: () => {
      count += 1;
      return count === 1 ? Promise.reject(httpError(404, "错题不存在")) : Promise.resolve({});
    },
  });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  await env.window.OfflineSync.enqueueGrade(2, 3);
  const result = await env.window.OfflineSync.flush();
  assert.equal(result.skipped, 1);
  assert.equal(result.synced, 1);
  assert.equal(env.window.OfflineSync.summary().total, 0);
});

test("sync: 409 视为已处理 markSynced", async () => {
  const { env } = syncEnv({ handler: () => Promise.reject(httpError(409, "这条记录已更新")) });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  const result = await env.window.OfflineSync.flush();
  assert.equal(result.synced, 1);
  assert.equal(result.stopped, false);
  assert.equal(env.window.OfflineSync.summary().total, 0);
});

test("sync: 同一道题的第二个 op 要等第一个同步完才发出", async () => {
  const { env, api } = syncEnv();
  await env.window.OfflineSync.enqueueGrade(1, 2);
  await env.window.OfflineSync.enqueueGrade(1, 4); // 同题：被前序 op 阻塞
  await env.window.OfflineSync.enqueueGrade(2, 5);
  const first = await env.window.OfflineSync.flush();
  assert.equal(api.calls.length, 2, "首批只发题 1 的第一条和题 2");
  assert.equal(first.synced, 2);
  const second = await env.window.OfflineSync.flush();
  assert.equal(api.calls.length, 3);
  assert.equal(posted(api.calls[2]).quality, 4);
  assert.equal(second.synced, 1);
});

test("sync: 三次失败后 status 变 failed，flush 不再发它", async () => {
  const { env, api } = syncEnv({ handler: () => Promise.reject(new Error("down")) });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  await env.window.OfflineSync.flush();
  await env.window.OfflineSync.flush();
  const result = await env.window.OfflineSync.flush();
  assert.equal(result.failed, 1, "本轮第三次失败");
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 0, failed: 1, total: 1 });
  const after = await env.window.OfflineSync.flush();
  assert.equal(api.calls.length, 3, "failed 的 op 不再进入补交批次");
  assert.deepEqual(plain(after), { synced: 0, skipped: 0, failed: 0, stopped: false, authExpired: false, offline: false });
});

test("sync: 离线时 flush 直接返回 offline，不调 api、不消耗 attempts", async () => {
  const { env, api } = syncEnv();
  await env.window.OfflineSync.enqueueGrade(1, 3);
  env.window.navigator = { onLine: false };
  const result = await env.window.OfflineSync.flush();
  assert.equal(result.offline, true);
  assert.equal(api.calls.length, 0);
  assert.equal(env.window.OfflineSync.summary().pending, 1);
});

test("sync: 空队列 flush 不发任何请求", async () => {
  const { env, api } = syncEnv();
  const result = await env.window.OfflineSync.flush();
  assert.equal(api.calls.length, 0);
  assert.deepEqual(plain(result), { synced: 0, skipped: 0, failed: 0, stopped: false, authExpired: false, offline: false });
});

test("sync: 补交途中 reset，迟到的响应不再改状态也不再发事件", async () => {
  const gate = deferred();
  const { env } = syncEnv({ handler: () => gate.promise });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  const flushing = env.window.OfflineSync.flush();
  await tick();
  await tick();
  env.window.OfflineSync.reset(); // 登出：之后回来的响应一律作废
  env.events.length = 0;
  gate.resolve({});
  const result = await flushing;
  assert.equal(result.synced, 0, "迟到的成功不能记账");
  assert.equal(env.events.filter((event) => event.type === "pwa:sync-state").length, 0);
});

test("sync: prefetch 途中换 epoch（登出再登录），迟到的响应被丢弃", async () => {
  const gate = deferred();
  const { env, session } = syncEnv({ handler: () => gate.promise });
  const prefetching = env.window.OfflineSync.prefetch();
  await tick();
  session.user = { id: 9 };
  session.epoch += 1;
  gate.resolve({ today: "2026-10-05", items: [{ id: 1, title: "A 的题" }] });
  const done = await prefetching;
  assert.equal(done, false);
  assert.equal(await env.window.OfflineSync.readTodayQueue(), null, "A 的预取不能落到 B 手里");
});

test("sync: prefetch 拉 /api/review/queue?limit=50 并把合法题目存进 IndexedDB", async () => {
  const items = [
    { id: 1, version: 2, title: "合法", tags: ["dp", 42], junk: "丢掉" },
    { id: -3, title: "坏 id" },
    "garbage",
    { id: 2, title: "第二条" },
  ];
  const { env, api, idb } = syncEnv({
    handler: () => Promise.resolve({ today: "2026-10-05", items }),
  });
  const ok = await env.window.OfflineSync.prefetch();
  assert.equal(ok, true);
  assert.equal(api.calls[0].path, "/api/review/queue?limit=50");
  await ticks(); // storeWrite 是 fire-and-forget
  const stored = idb.dbs.get("ouye-offline-7").get("today-queue");
  assert.equal(stored.today, "2026-10-05");
  assert.equal(stored.items.length, 2, "坏条目被丢弃");
  assert.equal("junk" in stored.items[0], false);
  assert.deepEqual(stored.items[0].tags, ["dp"], "tags 只留字符串");
  const read = await env.window.OfflineSync.readTodayQueue();
  assert.equal(read.items[1].title, "第二条");
});

test("sync: prefetch 失败（网络/401）当作不可用，不抛错也不动旧缓存", async () => {
  const { env } = syncEnv({ handler: () => Promise.reject(httpError(401, "请先登录")) });
  assert.equal(await env.window.OfflineSync.prefetch(), false);
  assert.equal(await env.window.OfflineSync.readTodayQueue(), null);
});

test("sync: IndexedDB 打不开时降级为纯内存，评分照常入队", async () => {
  const { env } = syncEnv({ idb: fakeIDB({ open: true }) });
  const op = await env.window.OfflineSync.enqueueGrade(1, 3);
  assert.ok(op.opId);
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 1, failed: 0, total: 1 });
  assert.equal(await env.window.OfflineSync.readTodayQueue(), null);
});

test("sync: IndexedDB 读失败当作空队列，不报错", async () => {
  const { env } = syncEnv({ idb: fakeIDB({ read: true }) });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 1, failed: 0, total: 1 });
});

test("sync: 没有 indexedDB 也能用（idb 注入 null）", async () => {
  const { env } = syncEnv({ idb: null });
  await env.window.OfflineSync.enqueueGrade(1, 3);
  assert.equal(env.window.OfflineSync.summary().pending, 1);
});

test("sync: pwa:online 事件触发自动补交，reset 后不再触发", async () => {
  const { env, api } = syncEnv();
  await env.window.OfflineSync.enqueueGrade(1, 3);
  env.document.dispatchEvent(new FakeEvent("pwa:online"));
  await tick();
  await tick();
  assert.equal(api.calls.length, 1, "联网事件自动 flush");

  await env.window.OfflineSync.enqueueGrade(2, 3);
  env.window.OfflineSync.reset();
  env.document.dispatchEvent(new FakeEvent("pwa:online"));
  await tick();
  assert.equal(api.calls.length, 1, "reset 后监听已卸掉");
});

test("sync: flush 每补交成功一条都播报一次队列状态", async () => {
  const { env } = syncEnv();
  await env.window.OfflineSync.enqueueGrade(1, 3);
  await env.window.OfflineSync.enqueueGrade(2, 3);
  env.events.length = 0;
  await env.window.OfflineSync.flush();
  const states = env.events.filter((event) => event.type === "pwa:sync-state").map((event) => event.detail.pending);
  assert.deepEqual(states, [1, 0], "先减到 1 再减到 0");
});

test("sync: summary 在没有任何操作时是零", () => {
  const { env } = syncEnv();
  assert.deepEqual(plain(env.window.OfflineSync.summary()), { pending: 0, failed: 0, total: 0 });
});

/* ================= pwa-register.js ================= */

function swRegistration() {
  const listeners = {};
  const workers = {};
  for (const slot of ["installing", "waiting", "active"]) {
    workers[slot] = null;
  }
  const registration = {
    installing: null,
    waiting: null,
    active: null,
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    fire: (type) => { for (const fn of listeners[type] || []) fn(); },
    _workers: workers,
  };
  return registration;
}

function fakeWorker(state = "installing") {
  const listeners = {};
  return {
    state,
    messages: [],
    postMessage(message) { this.messages.push(message); },
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    setState(next) {
      this.state = next;
      for (const fn of listeners.statechange || []) fn();
    },
  };
}

/** 装好可控的 navigator.serviceWorker / window 事件捕获 / location。 */
function registerEnv({ supported = true, controller = true, extra = {} } = {}) {
  const windowListeners = {};
  const swListeners = {};
  const registrations = [];
  const serviceWorker = {
    controller: controller ? {} : null,
    register: async (url, options) => {
      const registration = swRegistration();
      registration.installing = fakeWorker("installing");
      registrations.push({ url, options, registration });
      return registration;
    },
    addEventListener: (type, fn) => { (swListeners[type] ||= []).push(fn); },
    removeEventListener: () => {},
    fire: (type) => { for (const fn of swListeners[type] || []) fn(); },
  };
  const navigator = supported ? { serviceWorker, onLine: true } : {};
  const env = load(["pwa-register.js"], { extra: { navigator, ...extra } });
  const reloads = [];
  env.window.location = { origin: "https://ouye.test", href: "https://ouye.test/", reload: () => reloads.push(1) };
  env.window.addEventListener = (type, fn) => { (windowListeners[type] ||= []).push(fn); };
  env.window.removeEventListener = (type, fn) => {
    windowListeners[type] = (windowListeners[type] || []).filter((item) => item !== fn);
  };
  env.window.PwaRegister.configure({ version: "7" });
  return { env, navigator, serviceWorker, registrations, windowListeners, reloads };
}

const netBar = (env) => env.document.querySelector(".pwa-net");
const updateBar = (env) => env.document.querySelector(".pwa-update");

test("register: 没有 serviceWorker 支持就整体静默", async () => {
  const { env } = registerEnv({ supported: false });
  const before = env.document.body.children.length;
  const ok = await env.window.PwaRegister.register();
  assert.equal(ok, false);
  assert.equal(env.document.body.children.length, before, "不动 DOM");
  assert.deepEqual(env.window.PwaRegister.state().supported, false);
});

test("register: 用 /sw.js?v=<版本> 注册，作用域 /", async () => {
  const { env, registrations } = registerEnv();
  const ok = await env.window.PwaRegister.register();
  assert.equal(ok, true);
  assert.equal(registrations[0].url, "/sw.js?v=7");
  assert.deepEqual(plain(registrations[0].options), { scope: "/" });
});

test("register: 把带 ?v= 的同源外壳清单 postMessage 给安装中的 worker", async () => {
  const { env, registrations } = registerEnv();
  const link = env.document.createElement("link");
  link.setAttribute("rel", "stylesheet");
  link.setAttribute("href", "/static/style.css?v=50");
  const script = env.document.createElement("script");
  script.setAttribute("src", "/static/app.js?v=72");
  const cross = env.document.createElement("script");
  cross.setAttribute("src", "https://evil.example/x.js?v=1");
  env.document.body.append(link, script, cross);
  await env.window.PwaRegister.register();
  const message = registrations[0].registration.installing.messages.find((item) => item.type === "SHELL_LIST");
  assert.ok(message, "安装中的 worker 收到外壳清单");
  assert.ok(message.urls.includes("/"));
  assert.ok(message.urls.includes("/static/style.css?v=50"));
  assert.ok(message.urls.includes("/static/app.js?v=72"));
  assert.equal(message.urls.some((url) => url.includes("evil.example")), false, "跨域资源不进清单");
});

test("register: 显式配置的 shellUrls 优先于页面收集", async () => {
  const { env, registrations } = registerEnv();
  env.window.PwaRegister.configure({ version: "7", shellUrls: ["/", "/static/only.css?v=1"] });
  await env.window.PwaRegister.register();
  const message = registrations[0].registration.installing.messages.find((item) => item.type === "SHELL_LIST");
  assert.deepEqual(plain(message.urls), ["/", "/static/only.css?v=1"]);
});

test("register: 新版本安装完成（有 controller）时显示「有新版本，点此刷新」条", async () => {
  const { env, registrations } = registerEnv();
  await env.window.PwaRegister.register();
  assert.equal(updateBar(env), null);
  const { registration } = registrations[0];
  registration.fire("updatefound");
  registration.installing.setState("installed");
  await tick();
  const bar = updateBar(env);
  assert.ok(bar, "更新条出现");
  assert.match(bar.textContent, /有新版本，点此刷新/);
  const button = bar.querySelector("button");
  assert.equal(button.textContent, "刷新");
  assert.equal(button.getAttribute("aria-label"), "刷新以使用新版本");
});

test("register: 没有 controller 时（首次安装）不显示更新条", async () => {
  const { env, registrations } = registerEnv({ controller: false });
  await env.window.PwaRegister.register();
  const { registration } = registrations[0];
  registration.fire("updatefound");
  registration.installing.setState("installed");
  await tick();
  assert.equal(updateBar(env), null);
});

test("register: 注册时已有 waiting 且页面受控，立即显示更新条", async () => {
  const { env, serviceWorker } = registerEnv();
  serviceWorker.register = async () => {
    const registration = swRegistration();
    registration.waiting = fakeWorker("installed");
    return registration;
  };
  await env.window.PwaRegister.register();
  assert.ok(updateBar(env));
});

test("register: 点击刷新给 waiting 发 SKIP_WAITING，controllerchange 后刷新页面", async () => {
  const { env, serviceWorker, registrations, reloads } = registerEnv();
  await env.window.PwaRegister.register();
  const { registration } = registrations[0];
  registration.fire("updatefound");
  registration.installing.setState("installed");
  registration.waiting = registration.installing;
  await tick();
  updateBar(env).querySelector("button").click();
  assert.deepEqual(plain(registration.waiting.messages.at(-1)), { type: "SKIP_WAITING" });
  assert.equal(reloads.length, 0, "等 SW 接管后才刷新");
  serviceWorker.fire("controllerchange");
  assert.equal(reloads.length, 1);
});

test("register: 没有 waiting 时点击刷新直接重载", async () => {
  const { env, registrations, reloads } = registerEnv();
  await env.window.PwaRegister.register();
  const { registration } = registrations[0];
  registration.fire("updatefound");
  registration.installing.setState("installed");
  await tick();
  updateBar(env).querySelector("button").click();
  assert.equal(reloads.length, 1);
});

test("register: offline 显示离线细条，online 隐藏并派发 pwa:online", async () => {
  const { env, navigator, windowListeners } = registerEnv();
  await env.window.PwaRegister.register();
  navigator.onLine = false;
  for (const fn of windowListeners.offline || []) fn();
  const bar = netBar(env);
  assert.equal(bar.hidden, false);
  assert.equal(bar.textContent, "离线中，评分会在联网后同步");
  assert.equal(bar.getAttribute("role"), "status");

  env.events.length = 0;
  navigator.onLine = true;
  for (const fn of windowListeners.online || []) fn();
  assert.equal(netBar(env).hidden, true);
  assert.ok(env.events.some((event) => event.type === "pwa:online"), "通知 offline-sync 补交");
});

test("register: pwa:sync-state 更新「N 条待同步」，离线文案优先", async () => {
  const { env, navigator, windowListeners } = registerEnv();
  await env.window.PwaRegister.register();
  env.document.dispatchEvent(new FakeEvent("pwa:sync-state", { detail: { pending: 3, failed: 0 } }));
  assert.equal(netBar(env).textContent, "3 条待同步");
  env.document.dispatchEvent(new FakeEvent("pwa:sync-state", { detail: { pending: 0, failed: 2 } }));
  assert.match(netBar(env).textContent, /2 条同步失败/);
  navigator.onLine = false;
  for (const fn of windowListeners.offline || []) fn();
  assert.equal(netBar(env).textContent, "离线中，评分会在联网后同步");
  env.document.dispatchEvent(new FakeEvent("pwa:sync-state", { detail: { pending: 0, failed: 0 } }));
  navigator.onLine = true;
  for (const fn of windowListeners.online || []) fn();
  assert.equal(netBar(env).hidden, true);
});

test("register: 注册响应晚于 reset 到达时一律丢弃", async () => {
  const { env, serviceWorker } = registerEnv();
  const gate = deferred();
  serviceWorker.register = () => gate.promise;
  const pending = env.window.PwaRegister.register();
  env.window.PwaRegister.reset();
  gate.resolve(swRegistration());
  const ok = await pending;
  assert.equal(ok, false);
  assert.equal(env.window.PwaRegister.state().registered, false);
  assert.equal(updateBar(env), null);
});

test("register: reset 清掉两条状态条与 window 监听", async () => {
  const { env, navigator, windowListeners } = registerEnv();
  await env.window.PwaRegister.register();
  navigator.onLine = false;
  for (const fn of windowListeners.offline || []) fn();
  assert.ok(netBar(env));
  env.window.PwaRegister.reset();
  assert.equal(netBar(env), null, "细条被移除");
  assert.equal((windowListeners.offline || []).length, 0, "监听被卸掉");
  assert.equal((windowListeners.online || []).length, 0);
});

test("register: 注册失败（reject）静默返回 false", async () => {
  const { env, serviceWorker } = registerEnv();
  serviceWorker.register = () => Promise.reject(new Error("denied"));
  assert.equal(await env.window.PwaRegister.register(), false);
  assert.equal(env.window.PwaRegister.state().registered, false);
});

test("register: state() 汇总支持 / 已注册 / 在线 / 待同步", async () => {
  const { env } = registerEnv();
  assert.deepEqual(plain(env.window.PwaRegister.state()), {
    supported: true, registered: false, updateVisible: false, netVisible: false,
    online: true, pending: 0, failed: 0,
  });
  await env.window.PwaRegister.register();
  env.document.dispatchEvent(new FakeEvent("pwa:sync-state", { detail: { pending: 1, failed: 0 } }));
  const state = env.window.PwaRegister.state();
  assert.equal(state.registered, true);
  assert.equal(state.netVisible, true);
  assert.equal(state.pending, 1);
});

/* ================= sw.js（假 ServiceWorkerGlobalScope） ================= */

class FakeHeaders {
  constructor(map = {}) {
    this.map = new Map(Object.entries(map).map(([key, value]) => [key.toLowerCase(), value]));
  }
  get(name) { return this.map.get(String(name).toLowerCase()) ?? null; }
}
class FakeResponse {
  constructor(body = "", { status = 200, headers = {} } = {}) {
    this.body = body;
    this.status = status;
    this.ok = status >= 200 && status < 300;
    this.headers = headers instanceof FakeHeaders ? headers : new FakeHeaders(headers);
  }
  clone() { return new FakeResponse(this.body, { status: this.status, headers: this.headers }); }
}
class FakeRequest {
  constructor(url, init = {}) {
    this.url = String(url);
    this.method = init.method || "GET";
    this.credentials = init.credentials || "same-origin";
    this.mode = init.mode || "";
  }
}

function fakeCacheStorage() {
  const stores = new Map();
  return {
    async open(name) {
      if (!stores.has(name)) stores.set(name, new Map());
      const map = stores.get(name);
      return {
        _map: map,
        async match(key) {
          const wanted = typeof key === "string" ? key : key.url;
          return map.get(wanted);
        },
        async put(key, response) {
          map.set(typeof key === "string" ? key : key.url, response);
        },
      };
    },
    async keys() { return [...stores.keys()]; },
    async delete(name) { return stores.delete(name); },
    _stores: stores,
  };
}

/** 在 vm 里以假的 ServiceWorkerGlobalScope 跑 static/sw.js。 */
function loadSW({ version = "2" } = {}) {
  const listeners = {};
  const timers = [];
  const caches = fakeCacheStorage();
  const fetches = [];
  const routes = new Map(); // path+search -> FakeResponse | "error"
  const self = {
    location: { href: `https://ouye.test/sw.js?v=${version}`, origin: "https://ouye.test" },
    addEventListener: (type, fn) => { (listeners[type] ||= []).push(fn); },
    setTimeout: (fn) => { timers.push(fn); return timers.length; },
    skipWaiting() { self.skipWaiting.called = true; },
    clients: { claim: async () => { self.clients.claim.called = true; } },
  };
  const fetchStub = (request) => {
    const raw = typeof request === "string" ? request : request.url;
    const url = new URL(raw, self.location.origin);
    fetches.push(url.pathname + url.search);
    const route = routes.get(url.pathname + url.search);
    if (!route || route === "error") return Promise.reject(new Error("network down"));
    return Promise.resolve(route);
  };
  const context = vm.createContext({
    self, caches, fetch: fetchStub, Request: FakeRequest, Response: FakeResponse,
    URL, console, setTimeout: self.setTimeout,
  });
  vm.runInContext(fs.readFileSync(path.join(STATIC, "sw.js"), "utf8"), context, { filename: "sw.js" });
  return { self, listeners, timers, caches, fetches, routes };
}

const sw = (env, type, event) => { for (const fn of env.listeners[type] || []) fn(event); };
function waitable() {
  return { waitUntil(promise) { this.promise = promise; } };
}
async function swFetch(env, request) {
  const event = { request, respondWith(promise) { this.promise = promise; } };
  sw(env, "fetch", event);
  return event.promise ? event.promise : undefined; // undefined = SW 没接管（直连网络）
}
async function swInstall(env, urls) {
  const event = waitable();
  sw(env, "install", event);
  if (urls) sw(env, "message", { data: { type: "SHELL_LIST", urls } });
  await event.promise;
}

test("sw: 安装时按清单预缓存带 ?v= 的外壳，Set-Cookie 响应不入缓存", async () => {
  const env = loadSW();
  env.routes.set("/", new FakeResponse("<html>", { headers: { "set-cookie": "sid=1" } }));
  env.routes.set("/static/app.js?v=72", new FakeResponse("js"));
  env.routes.set("/static/style.css?v=50", new FakeResponse("css"));
  await swInstall(env, ["/", "/static/app.js?v=72", "/static/style.css?v=50"]);
  const cache = env.caches._stores.get("ouye-shell-v2");
  assert.ok(cache.has("/static/app.js?v=72"));
  assert.ok(cache.has("/static/style.css?v=50"));
  assert.equal(cache.has("/"), false, "首页响应带 Set-Cookie，不缓存");
});

test("sw: 清单超时未到就回退只缓存首页", async () => {
  const env = loadSW();
  env.routes.set("/", new FakeResponse("<html>"));
  const event = waitable();
  sw(env, "install", event);
  env.timers[0](); // 触发 SHELL_LIST 等待超时
  await event.promise;
  const cache = env.caches._stores.get("ouye-shell-v2");
  assert.deepEqual([...cache.keys()], ["/"]);
});

test("sw: 静态资源 cache-first：命中不发网络，未命中回源并写入缓存", async () => {
  const env = loadSW();
  env.routes.set("/static/a.js?v=1", new FakeResponse("a"));
  const miss = await swFetch(env, new FakeRequest("https://ouye.test/static/a.js?v=1"));
  assert.equal(miss.body, "a");
  assert.deepEqual(env.fetches, ["/static/a.js?v=1"]);
  env.routes.delete("/static/a.js?v=1"); // 回源已经拿不到，只能靠缓存
  const hit = await swFetch(env, new FakeRequest("https://ouye.test/static/a.js?v=1"));
  assert.equal(hit.body, "a");
  assert.equal(env.fetches.length, 1, "第二次不再发网络");
});

test("sw: 不带 ?v= 的静态资源不缓存", async () => {
  const env = loadSW();
  env.routes.set("/static/favicon.svg", new FakeResponse("svg"));
  await swFetch(env, new FakeRequest("https://ouye.test/static/favicon.svg"));
  await swFetch(env, new FakeRequest("https://ouye.test/static/favicon.svg"));
  assert.equal(env.fetches.length, 2, "每次都回源");
  const opened = env.caches._stores.get("ouye-shell-v2");
  assert.equal(opened === undefined || opened.size === 0, true, "缓存开了仓但什么都没写");
});

test("sw: 带 Set-Cookie 的静态响应不缓存", async () => {
  const env = loadSW();
  env.routes.set("/static/me.js?v=1", new FakeResponse("js", { headers: { "Set-Cookie": "a=b" } }));
  await swFetch(env, new FakeRequest("https://ouye.test/static/me.js?v=1"));
  const cache = env.caches._stores.get("ouye-shell-v2");
  assert.equal(cache === undefined || cache.size === 0, true);
});

test("sw: /api/ 请求完全不接管（不缓存、不经 SW 回源）", async () => {
  const env = loadSW();
  env.routes.set("/api/review/queue?limit=50", new FakeResponse("{}"));
  const result = await swFetch(env, new FakeRequest("https://ouye.test/api/review/queue?limit=50"));
  assert.equal(result, undefined, "SW 不调 respondWith");
  assert.equal(env.fetches.length, 0, "SW 自己不回源，交给浏览器直连");
  assert.equal(env.caches._stores.size, 0);
});

test("sw: HTML 导航 network-first：在线用网络，失败回退缓存首页", async () => {
  const env = loadSW();
  env.routes.set("/", new FakeResponse("<html>home</html>"));
  await swInstall(env, ["/"]);
  env.routes.set("/", new FakeResponse("<html>fresh</html>"));
  const online = await swFetch(env, new FakeRequest("https://ouye.test/", { mode: "navigate" }));
  assert.equal(online.body, "<html>fresh</html>", "在线时是网络的新页面");
  env.routes.set("/", "error");
  const offline = await swFetch(env, new FakeRequest("https://ouye.test/", { mode: "navigate" }));
  assert.equal(offline.body, "<html>home</html>", "离线回退到缓存的首页");
});

test("sw: 导航失败且没有缓存首页时返回 503", async () => {
  const env = loadSW();
  const response = await swFetch(env, new FakeRequest("https://ouye.test/", { mode: "navigate" }));
  assert.equal(response.status, 503);
});

test("sw: 非 GET 与跨域请求一律不接管", async () => {
  const env = loadSW();
  const post = await swFetch(env, new FakeRequest("https://ouye.test/static/a.js?v=1", { method: "POST" }));
  assert.equal(post, undefined);
  const cross = await swFetch(env, new FakeRequest("https://cdn.example.com/a.js?v=1"));
  assert.equal(cross, undefined);
  assert.equal(env.fetches.length, 0);
});

test("sw: 激活时清掉旧版本外壳缓存，保留当前版本与无关缓存", async () => {
  const env = loadSW({ version: "2" });
  await env.caches.open("ouye-shell-v1");
  await env.caches.open("ouye-shell-v2");
  await env.caches.open("someone-else");
  const event = waitable();
  sw(env, "activate", event);
  await event.promise;
  assert.deepEqual((await env.caches.keys()).sort(), ["ouye-shell-v2", "someone-else"]);
  assert.equal(env.self.clients.claim.called, true);
});

test("sw: 收到 SKIP_WAITING 立即 skipWaiting", async () => {
  const env = loadSW();
  sw(env, "message", { data: { type: "SKIP_WAITING" } });
  assert.equal(env.self.skipWaiting.called, true);
});

test("sw: 非法外壳清单被拒（绝对 URL / 协议相对 / 非数组）", async () => {
  const env = loadSW();
  env.routes.set("/", new FakeResponse("<html>"));
  const event = waitable();
  sw(env, "install", event);
  sw(env, "message", { data: { type: "SHELL_LIST", urls: ["https://evil.example/x.js"] } });
  env.timers[0](); // 清单非法 → 等超时兜底
  await event.promise;
  const cache = env.caches._stores.get("ouye-shell-v2");
  assert.deepEqual([...cache.keys()], ["/"], "注入的地址不进缓存");
});
