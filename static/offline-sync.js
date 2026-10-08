"use strict";

/* offline-sync.js —— 离线「今日复习」与评分补交（window.OfflineSync）。
 *
 * 数据模型完全复用 static/offline-queue.js（window.OfflineQueue，纯函数队列）：
 *   - prefetch()：联网时拉 GET /api/review/queue?limit=50，把今天待复习的题存进 IndexedDB
 *     （库名带用户 id：ouye-offline-<userId>，单 kv 仓；读写全部 try/catch，失败当作不可用，不报错）；
 *   - enqueueGrade(mistakeId, grade)：离线时入队（opId 用 crypto.randomUUID，
 *     没有就退回时间戳加随机数），并持久化；
 *   - flush()：联网后按 OfflineQueue.nextBatch 顺序逐条 POST /api/mistakes/<id>/review，
 *     请求体带 quality、client_op_id（幂等）、reviewed_at（入队时间），预取过 version 时一并带上。
 *     成功 markSynced；网络错误 markFailed 并停止本轮；401 保留队列并派发 pwa:auth-expired；
 *     404（题已删）markSynced 计入 skipped；409 视为已处理 markSynced；
 *   - 队列绑定用户 id：换号时经 OfflineQueue.forUser 丢弃并派发 pwa:queue-dropped；
 *   - summary() 给顶部条显示「N 条待同步」；队列变化时派发 pwa:sync-state。
 * 迟到响应守卫：登出 / 换号 / reset 之后才回来的响应一律丢弃（getEpoch() + getUser().id + 请求序号）。 */
(() => {
  const STORE = "kv";
  const QUEUE_KEY = "queue";
  const PREFETCH_KEY = "today-queue";
  const FLUSH_BATCH = 10;
  const MAX_PREFETCH_ITEMS = 50;
  const ITEM_FIELDS = [
    "id", "problem_id", "title", "zone", "language", "code", "thinking", "description",
    "repetitions", "interval_days", "ease_factor", "due_date", "version", "tags",
    // 仅保存服务端题级展示快照；评分操作和逐条队列的结构保持不变。
    "progress", "problem_tags", "problem_due_count",
  ];

  let hooks = null; // { api, getUser, getEpoch, idb }
  let generation = 0;
  const sequence = { prefetch: 0, flush: 0 };
  let state = null;        // OfflineQueue state
  let stateLoaded = false;
  let prefetched = null;   // { at, today, items }
  let dbCache = null;      // { userId, promise }
  let flushing = false;
  let listening = false;

  const queue = () => window.OfflineQueue;

  /* ---------- 迟到响应守卫 ---------- */
  function ticket(name) {
    sequence[name] += 1;
    return { name, sequence: sequence[name], generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }
  function current(request) {
    return Boolean(hooks && hooks.getUser()
      && request.generation === generation
      && request.sequence === sequence[request.name]
      && request.epoch === hooks.getEpoch()
      && request.userId === hooks.getUser().id);
  }

  /* ---------- IndexedDB（全部 try/catch，失败当作不可用） ---------- */
  function idbFactory() {
    return hooks?.idb ?? (typeof window !== "undefined" ? window.indexedDB : undefined) ?? null;
  }

  function requestToPromise(request) {
    return new Promise((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error || new Error("IndexedDB 操作失败"));
    });
  }

  function openDb(userId) {
    if (dbCache && dbCache.userId === userId) return dbCache.promise;
    const idb = idbFactory();
    const promise = !idb ? Promise.resolve(null) : new Promise((resolve) => {
      let open;
      try {
        open = idb.open(`ouye-offline-${userId}`, 1);
      } catch {
        resolve(null);
        return;
      }
      open.onupgradeneeded = () => {
        try {
          if (!open.result.objectStoreNames.contains(STORE)) open.result.createObjectStore(STORE);
        } catch {
          // 建仓失败按不可用处理，onerror / 后续 try/catch 兜底
        }
      };
      open.onsuccess = () => resolve(open.result);
      open.onerror = () => resolve(null);
      open.onblocked = () => resolve(null);
    });
    dbCache = { userId, promise };
    return promise;
  }

  async function storeRead(userId, key) {
    try {
      const db = await openDb(userId);
      if (!db) return { ok: false, value: undefined };
      const tx = db.transaction(STORE, "readonly");
      return { ok: true, value: await requestToPromise(tx.objectStore(STORE).get(key)) };
    } catch {
      return { ok: false, value: undefined };
    }
  }

  async function storeWrite(userId, key, value) {
    try {
      const db = await openDb(userId);
      if (!db) return false;
      const tx = db.transaction(STORE, "readwrite");
      tx.objectStore(STORE).put(value, key);
      return true;
    } catch {
      return false;
    }
  }

  /* ---------- 事件 ---------- */
  function announce(name, detail) {
    document.dispatchEvent(new CustomEvent(name, { detail }));
  }
  function announceSyncState() {
    if (!state) return;
    announce("pwa:sync-state", queue().summary(state));
  }

  /* ---------- 队列状态（绑定当前用户） ---------- */
  async function ensureState() {
    const user = hooks?.getUser();
    if (!hooks || !user) return null;
    if (stateLoaded && state && state.userId === user.id) return state;
    const loadTicket = { generation, epoch: hooks.getEpoch(), userId: user.id };
    const stored = await storeRead(user.id, QUEUE_KEY);
    if (loadTicket.generation !== generation || loadTicket.epoch !== hooks.getEpoch()) return null;
    const now = hooks.getUser();
    if (!now || now.id !== loadTicket.userId) return null; // 加载期间换了账号：丢弃
    const parsed = stored.ok ? queue().validate(stored.value) : null;
    let next = parsed || state || queue().create(now.id);
    const bound = queue().forUser(next, now.id);
    next = bound.state;
    state = next;
    stateLoaded = true;
    if (bound.dropped > 0) {
      announce("pwa:queue-dropped", { dropped: bound.dropped });
      storeWrite(now.id, QUEUE_KEY, JSON.parse(queue().serialize(next)));
      announceSyncState();
    }
    return state;
  }

  function persist() {
    const user = hooks?.getUser();
    if (!user || !state) return;
    storeWrite(user.id, QUEUE_KEY, JSON.parse(queue().serialize(state)));
  }

  function makeOpId() {
    const crypto = window.crypto;
    if (crypto && typeof crypto.randomUUID === "function") return crypto.randomUUID();
    return `op-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
  }

  /* ---------- 预取今日复习 ---------- */
  function sanitizeItems(value) {
    if (!Array.isArray(value)) return [];
    const items = [];
    for (const raw of value) {
      if (items.length >= MAX_PREFETCH_ITEMS) break;
      if (!raw || typeof raw !== "object" || Array.isArray(raw)) continue;
      if (!Number.isInteger(raw.id) || raw.id <= 0) continue;
      const item = {};
      for (const field of ITEM_FIELDS) {
        if (field === "tags") item.tags = Array.isArray(raw.tags) ? raw.tags.filter((tag) => typeof tag === "string") : [];
        else if (raw[field] !== undefined) item[field] = raw[field];
      }
      items.push(item);
    }
    return items;
  }

  async function prefetch() {
    if (!hooks || !hooks.getUser()) return false;
    const request = ticket("prefetch");
    let data;
    try {
      data = await hooks.api("/api/review/queue?limit=50");
    } catch {
      return false; // 拉不到就当离线不可用，不报错、不动旧缓存
    }
    if (!current(request)) return false; // 迟到响应：丢弃
    prefetched = {
      at: new Date().toISOString(),
      today: typeof data?.today === "string" ? data.today : null,
      items: sanitizeItems(data?.items),
    };
    storeWrite(request.userId, PREFETCH_KEY, prefetched);
    return true;
  }

  /** 离线复习界面读这里：预取到的今日队列；没有（或不可用）返回 null。 */
  async function readTodayQueue() {
    const user = hooks?.getUser();
    if (!hooks || !user) return null;
    if (prefetched) return JSON.parse(JSON.stringify(prefetched));
    const stored = await storeRead(user.id, PREFETCH_KEY);
    const value = stored.ok ? stored.value : null;
    if (!value || typeof value !== "object" || !Array.isArray(value.items)) return null;
    prefetched = {
      at: typeof value.at === "string" ? value.at : null,
      today: typeof value.today === "string" ? value.today : null,
      items: sanitizeItems(value.items),
    };
    return JSON.parse(JSON.stringify(prefetched));
  }

  /* ---------- 离线评分入队 ---------- */
  async function enqueueGrade(mistakeId, grade) {
    if (!hooks) throw new Error("OfflineSync 尚未 configure");
    if (!hooks.getUser()) throw new Error("需要登录后再评分");
    const ready = await ensureState();
    if (!ready) throw new Error("需要登录后再评分");
    const opId = makeOpId();
    const at = new Date().toISOString();
    state = queue().enqueue(state, { opId, mistakeId, grade, at }); // TypeError / RangeError 原样上抛
    persist();
    announceSyncState();
    return { opId, mistakeId, grade, at };
  }

  /* ---------- 联网补交 ---------- */
  function emptyResult() {
    return { synced: 0, skipped: 0, failed: 0, stopped: false, authExpired: false, offline: false };
  }

  function bodyFor(op) {
    const body = { quality: op.grade, client_op_id: op.opId, reviewed_at: op.at };
    const known = prefetched?.items?.find((item) => item.id === op.mistakeId);
    if (known && Number.isInteger(known.version)) body.version = known.version;
    return body;
  }

  async function flush() {
    if (!hooks || !hooks.getUser() || flushing) return emptyResult();
    if (window.navigator && window.navigator.onLine === false) {
      return { ...emptyResult(), offline: true }; // 离线时不空耗 attempts
    }
    const request = ticket("flush");
    flushing = true;
    const result = emptyResult();
    try {
      const ready = await ensureState();
      if (!ready || !current(request)) return result;
      const batch = queue().nextBatch(state, FLUSH_BATCH);
      for (const op of batch) {
        if (!current(request)) break; // 迟到守卫：换号 / reset 后不再发也不再改
        try {
          await hooks.api(`/api/mistakes/${op.mistakeId}/review`, {
            method: "POST",
            body: JSON.stringify(bodyFor(op)),
          });
          if (!current(request)) break;
          state = queue().markSynced(state, op.opId);
          result.synced += 1;
        } catch (error) {
          if (!current(request)) break;
          const status = error?.status;
          if (status === 401) {
            result.authExpired = true; // 队列原样保留，重新登录后再 flush
            result.stopped = true;
            announce("pwa:auth-expired", { pending: queue().summary(state).total });
            break;
          }
          if (status === 404) {
            state = queue().markSynced(state, op.opId); // 题已删：算跳过
            result.skipped += 1;
          } else if (status === 409) {
            state = queue().markSynced(state, op.opId); // 已处理过（幂等命中）：算同步
            result.synced += 1;
          } else {
            state = queue().markFailed(state, op.opId, error?.message || "网络错误");
            result.failed += 1;
            result.stopped = true; // 网络/服务器错误：停本轮，保持顺序
          }
        }
        persist();
        announceSyncState();
        if (result.stopped) break;
      }
      announce("pwa:flush-result", result); // 界面据此提示成功 N 条 / 跳过 M 条 / 失败可重试
      return result;
    } finally {
      flushing = false;
    }
  }

  /* ---------- 失败重排（恢复联网后可重试） ---------- */
  async function rearmFailed() {
    const ready = await ensureState();
    if (!ready) return false;
    const next = queue().retryFailed(state);
    if (next === state) return false;
    state = next;
    persist();
    announceSyncState();
    return true;
  }

  /** 手动/自动重试：先把 failed 的评分重新排队，再立即补交一轮。 */
  async function retryFailed() {
    if (!hooks || !hooks.getUser()) return emptyResult();
    await rearmFailed();
    return flush();
  }

  /* ---------- 对外 ---------- */
  function summary() {
    return state ? queue().summary(state) : { pending: 0, failed: 0, total: 0 };
  }

  async function onOnline() {
    await rearmFailed(); // 三次失败停摆的评分，恢复联网后给一次新机会
    void flush(); // 内部自带守卫与结果汇总；fire-and-forget
  }

  function configure(options) {
    hooks = options || null;
    if (!listening && typeof document !== "undefined") {
      listening = true;
      document.addEventListener("pwa:online", onOnline);
    }
  }

  function reset() {
    generation += 1;
    sequence.prefetch += 1;
    sequence.flush += 1;
    state = null;
    stateLoaded = false;
    prefetched = null;
    dbCache = null;
    flushing = false;
    if (listening && typeof document !== "undefined") {
      listening = false;
      document.removeEventListener("pwa:online", onOnline);
    }
  }

  window.OfflineSync = {
    configure,
    prefetch,
    readTodayQueue,
    enqueueGrade,
    flush,
    retryFailed,
    summary,
    reset,
    helpers: { sanitizeItems, makeOpId },
  };
})();
