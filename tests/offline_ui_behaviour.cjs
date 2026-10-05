"use strict";

/* 离线「今日复习」界面编排的行为测试（Node 内置 node:test + tests/js_harness.cjs 假浏览器）。
 * 覆盖：离线评分入队与提示、预取队列兜底渲染所需数据、联网补交顺序与结果提示、
 * 401/404/409/网络错误的处理、换号丢弃提示、离线禁用项、顶部细条文案、登出保留、迟到守卫。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const ticks = async (n = 5) => { for (let i = 0; i < n; i += 1) await tick(); };
const plain = (value) => JSON.parse(JSON.stringify(value));

function httpError(status, message) {
  const error = new Error(message || `HTTP ${status}`);
  error.status = status;
  return error;
}

function apiStub(handler) {
  const calls = [];
  const api = (path, options = {}) => { calls.push({ path, options }); return handler(path, options); };
  api.calls = calls;
  return api;
}

function fakeIDB() {
  const dbs = new Map();
  return {
    open(name) {
      const request = {};
      setTimeout(() => {
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
                  inner.result = store.has(key) ? JSON.parse(JSON.stringify(store.get(key))) : undefined;
                  inner.onsuccess?.();
                }, 0);
                return inner;
              },
              put(value, key) {
                const inner = {};
                setTimeout(() => {
                  store.set(key, JSON.parse(JSON.stringify(value)));
                  inner.onsuccess?.();
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

const QUEUE_ITEM = (id) => ({
  id, problem_id: id + 1000, title: `题 ${id}`, zone: "二分查找", language: "Python",
  code: "print(1)", thinking: "题目链接：https://example.com/p/1\n思路",
  description: "边界没判空", repetitions: 2, interval_days: 6, ease_factor: 2.4,
  due_date: "2026-10-05", version: 3, tags: ["数组", "边界"],
  suspended_at: null, last_reviewed_at: "2026-10-01T00:00:00",
});

function uiEnv({ user = { id: 7 }, onLine = true, handler = () => Promise.resolve({}) } = {}) {
  const session = { user, epoch: 1 };
  const navigator = { onLine };
  const idb = fakeIDB();
  const env = load(["offline-queue.js", "offline-sync.js", "offline-review.js"], { extra: { navigator } });
  const api = apiStub(handler);
  const notifications = [];
  env.window.OfflineSync.configure({
    api, idb, getUser: () => session.user, getEpoch: () => session.epoch,
  });
  env.window.OfflineReview.configure({
    notify: (text) => notifications.push(String(text)),
    getUser: () => session.user,
    getEpoch: () => session.epoch,
  });
  env.navigator = navigator;
  env.session = session;
  env.idb = idb;
  return { env, api, session, navigator, idb, notifications };
}

/* ================= 离线判定与网络错误 ================= */

test("ui: navigator.onLine=false 即离线；没有 status 的错误算网络错误，409/500 不算", () => {
  const { env } = uiEnv();
  env.navigator.onLine = false;
  assert.equal(env.window.OfflineReview.isOffline(), true);
  assert.equal(env.window.OfflineReview.isNetworkError(new Error("Failed to fetch")), true);
  assert.equal(env.window.OfflineReview.isNetworkError(httpError(500)), false);
  assert.equal(env.window.OfflineReview.isNetworkError(httpError(409)), false);
  env.navigator.onLine = true;
  assert.equal(env.window.OfflineReview.isOffline(), false);
});

/* ================= 离线评分入队 ================= */

test("ui: 离线评分走 enqueueGrade，提示「已记下，联网后同步」，summary 计数增加", async () => {
  const { env, notifications } = uiEnv({ onLine: false });
  const op = await env.window.OfflineReview.grade(11, 4);
  assert.ok(op && op.opId, "返回 opId");
  assert.equal(op.mistakeId, 11);
  assert.equal(op.grade, 4);
  assert.equal(env.window.OfflineReview.summary().pending, 1);
  assert.ok(notifications.some((text) => text.includes("已记下") && text.includes("联网后同步")));
  // grade 只接受 1-5（quality 0 不入队）。
  await assert.rejects(() => env.window.OfflineReview.grade(11, 0), { name: "TypeError" });
});

/* ================= 预取队列兜底 ================= */

test("ui: todayFallback 用预取队列返回今日列表数据并缓存题目明细", async () => {
  const { env } = uiEnv({});
  // 在线时预取。
  const queueApi = (path) => path.startsWith("/api/review/queue")
    ? Promise.resolve({ today: "2026-10-05", items: [QUEUE_ITEM(11), QUEUE_ITEM(12)] })
    : Promise.resolve({});
  env.window.OfflineSync.configure({
    api: queueApi, idb: env.idb, getUser: () => env.session.user, getEpoch: () => env.session.epoch,
  });
  assert.equal(await env.window.OfflineSync.prefetch(), true);
  await ticks();
  // 离线后从 IndexedDB 读回。
  env.navigator.onLine = false;
  const fallback = await env.window.OfflineReview.todayFallback();
  assert.ok(fallback, "有预取就能兜底");
  assert.equal(fallback.today, "2026-10-05");
  assert.deepEqual(plain(fallback.items.map((item) => item.id)), [11, 12]);
  const cached = env.window.OfflineReview.cachedItem(11);
  assert.equal(cached.id, 11);
  assert.ok(Array.isArray(cached.reviews) && Array.isArray(cached.variants), "详情需要的数组字段给默认值");
  assert.equal(cached.today, "2026-10-05");
  assert.equal(env.window.OfflineReview.cachedItem(999), null, "没预取到的题不给假数据");
});

test("ui: 没有预取缓存时 todayFallback 返回 null，不编造题目", async () => {
  const { env } = uiEnv({ onLine: false });
  assert.equal(await env.window.OfflineReview.todayFallback(), null);
  assert.equal(env.window.OfflineReview.cachedItem(11), null);
});

/* ================= 联网补交与结果提示 ================= */

test("ui: pwa:online 触发按入队顺序补交，flush-result 提示成功 N 条", async () => {
  const posted = [];
  const handler = (path, options) => {
    posted.push({ path, body: JSON.parse(options.body) });
    return Promise.resolve({ repetitions: 3, interval_days: 8, ease_factor: 2.4, due_date: "2026-10-13", version: 4 });
  };
  const { env, notifications } = uiEnv({ onLine: false, handler });
  await env.window.OfflineReview.grade(11, 4);
  await env.window.OfflineReview.grade(12, 2);
  notifications.length = 0;
  env.navigator.onLine = true;
  env.document.dispatchEvent(new FakeEvent("pwa:online"));
  await ticks(8);
  assert.deepEqual(posted.map((call) => call.path), [
    "/api/mistakes/11/review", "/api/mistakes/12/review",
  ]);
  assert.deepEqual(posted.map((call) => call.body.quality), [4, 2]);
  for (const call of posted) {
    assert.match(call.body.client_op_id, /^[\w-]{8,}$/);
    assert.ok(!isNaN(Date.parse(call.body.reviewed_at)));
  }
  assert.equal(env.window.OfflineReview.summary().total, 0);
  assert.ok(notifications.some((text) => text.includes("成功") && text.includes("2")));
});

test("ui: 404 计入跳过、409 视为已同步，结果提示含跳过数", async () => {
  const handler = (path) => {
    if (path.endsWith("/11/review")) return Promise.reject(httpError(404, "题目已删除"));
    if (path.endsWith("/12/review")) return Promise.reject(httpError(409, "已经有更新的评分"));
    return Promise.resolve({ version: 4 });
  };
  const { env, notifications } = uiEnv({ handler });
  await env.window.OfflineReview.grade(11, 2);
  await env.window.OfflineReview.grade(12, 3);
  notifications.length = 0;
  const result = await env.window.OfflineReview.flushNow();
  assert.equal(result.skipped, 1);
  assert.equal(result.synced, 1);
  assert.ok(notifications.some((text) => text.includes("跳过") && text.includes("1")));
});

test("ui: 401 保留队列并提示重新登录", async () => {
  const handler = () => Promise.reject(httpError(401, "未登录"));
  const { env, notifications } = uiEnv({ handler });
  await env.window.OfflineReview.grade(11, 4);
  notifications.length = 0;
  const result = await env.window.OfflineReview.flushNow();
  assert.equal(result.authExpired, true);
  assert.equal(env.window.OfflineReview.summary().pending, 1, "队列原样保留");
  assert.ok(notifications.some((text) => text.includes("重新登录")));
});

test("ui: 网络错误记为失败；三次停摆后再次联网会重新排队并补交成功（失败可重试）", async () => {
  let online = false;
  const handler = () => {
    if (!online) return Promise.reject(new Error("network down"));
    return Promise.resolve({ version: 4 });
  };
  const { env, notifications } = uiEnv({ handler });
  await env.window.OfflineReview.grade(11, 4);
  // 连续三轮补交都失败：第三次后 op 进入 failed 停摆，普通 flush 不再碰它。
  let result;
  for (let i = 0; i < 3; i += 1) {
    result = await env.window.OfflineReview.flushNow();
    await ticks(2);
  }
  assert.equal(result.failed, 1);
  assert.equal(env.window.OfflineReview.summary().failed, 1);
  // 网络恢复后 pwa:online 自动 rearm 并补交。
  notifications.length = 0;
  online = true;
  env.navigator.onLine = true;
  env.document.dispatchEvent(new FakeEvent("pwa:online"));
  await ticks(10);
  assert.equal(env.window.OfflineReview.summary().total, 0);
  assert.ok(notifications.some((text) => text.includes("成功")));
});

/* ================= 换号丢弃 ================= */

test("ui: 换号后入队触发丢弃旧队列并提示，绝不把旧评分交给新账号", async () => {
  const { env, api, notifications } = uiEnv({ user: { id: 7 } });
  await env.window.OfflineReview.grade(11, 4);
  assert.equal(api.calls.length, 0);
  notifications.length = 0;
  env.session.user = { id: 8 };
  await env.window.OfflineReview.grade(12, 5);
  await ticks();
  assert.ok(notifications.some((text) => text.includes("切换账号")), notifications.join(" | "));
  // 新账号队列里只有自己的新评分。
  env.navigator.onLine = true;
  env.document.dispatchEvent(new FakeEvent("pwa:online"));
  await ticks(8);
  const paths = api.calls.map((call) => call.path);
  assert.deepEqual(paths, ["/api/mistakes/12/review"]);
});

/* ================= 离线禁用项 ================= */

function fakeDetailRoot(document) {
  const root = document.createElement("div");
  const toolbar = document.createElement("div");
  toolbar.className = "review-toolbar";
  const more = document.createElement("div");
  more.className = "review-more";
  const moreButton = document.createElement("button");
  moreButton.className = "review-more-button";
  more.append(moreButton);
  toolbar.append(more);
  root.append(toolbar);
  const variant = document.createElement("div");
  variant.className = "variant";
  const form = document.createElement("form");
  const save = document.createElement("button");
  save.type = "submit";
  save.textContent = "保存练习结果";
  form.append(save);
  variant.append(form);
  root.append(variant);
  const ai = document.createElement("section");
  ai.className = "ai-section";
  const generate = document.createElement("button");
  generate.className = "primary";
  generate.textContent = "诊断错因并出两道新题";
  ai.append(generate);
  root.append(ai);
  const danger = document.createElement("div");
  danger.className = "danger-zone";
  const del = document.createElement("button");
  del.className = "danger";
  danger.append(del);
  root.append(danger);
  const reviewSection = document.createElement("section");
  reviewSection.className = "review-section";
  root.append(reviewSection);
  return { root, more, moreButton, save, generate, del, reviewSection };
}

test("ui: restrictDetail 禁用推迟/暂停、AI、变体保存、删除，并给出离线说明", () => {
  const { env } = uiEnv({ onLine: false });
  const parts = fakeDetailRoot(env.document);
  env.window.OfflineReview.restrictDetail(parts.root);
  assert.equal(parts.more.hidden, true, "推迟/暂停/恢复菜单离线隐藏");
  assert.equal(parts.generate.disabled, true);
  assert.equal(parts.generate.dataset.blocked, "1");
  assert.equal(parts.save.disabled, true, "变体练习结果保存需要联网");
  assert.equal(parts.del.disabled, true, "删除需要联网");
  const note = parts.root.querySelector(".review-offline-note");
  assert.ok(note, "插入离线说明");
  assert.match(note.textContent, /离线/);
  assert.ok(/撤销|推迟|暂停|AI/.test(note.textContent), "说明要点名不可用功能");
});

/* ================= 顶部细条文案 ================= */

test("ui: 顶部细条离线且有待同步时显示「离线中，N 条评分待同步」", async () => {
  const windowListeners = {};
  const serviceWorker = {
    controller: {},
    register: async () => ({
      installing: null, waiting: null, active: { postMessage() {} },
      addEventListener() {},
    }),
    addEventListener() {},
  };
  const env = load(["pwa-register.js"], {
    extra: { navigator: { serviceWorker, onLine: true } },
  });
  const navigator = env.window.navigator;
  env.window.addEventListener = (type, fn) => { (windowListeners[type] ||= []).push(fn); };
  env.window.PwaRegister.configure({ version: "1" });
  await env.window.PwaRegister.register();
  // 先有 2 条待同步，再断网：离线文案要带上条数。
  env.document.dispatchEvent(new FakeEvent("pwa:sync-state", { detail: { pending: 2, failed: 0 } }));
  navigator.onLine = false;
  for (const fn of windowListeners.offline || []) fn();
  const bar = env.document.querySelector(".pwa-net");
  assert.equal(bar.hidden, false);
  assert.equal(bar.textContent, "离线中，2 条评分待同步");
  // 没有待同步时保留通用离线文案。
  env.document.dispatchEvent(new FakeEvent("pwa:sync-state", { detail: { pending: 0, failed: 0 } }));
  for (const fn of windowListeners.offline || []) fn();
  assert.equal(bar.textContent, "离线中，评分会在联网后同步");
});

/* ================= 登出保留与会话过期 ================= */

test("ui: 登出时若有待同步评分，提示已保留；reset 后迟到事件不再提示", async () => {
  const { env, notifications } = uiEnv({ onLine: false });
  await env.window.OfflineReview.grade(11, 4);
  notifications.length = 0;
  env.window.OfflineReview.onSignedOut();
  assert.ok(notifications.some((text) => text.includes("保留") && text.includes("重新登录")));
  // reset 之后再派发补交结果 / 换号事件，全部丢弃。
  notifications.length = 0;
  env.window.OfflineReview.reset();
  env.document.dispatchEvent(new FakeEvent("pwa:flush-result", { detail: { synced: 1, skipped: 0, failed: 0 } }));
  env.document.dispatchEvent(new FakeEvent("pwa:queue-dropped", { detail: { dropped: 1 } }));
  env.document.dispatchEvent(new FakeEvent("pwa:auth-expired", { detail: { pending: 1 } }));
  assert.deepEqual(notifications, []);
});

test("ui: 没有待同步评分时登出不打扰用户", () => {
  const { env, notifications } = uiEnv();
  env.window.OfflineReview.onSignedOut();
  assert.deepEqual(notifications, []);
});
