/*
 * offline-queue.js
 *
 * 离线评分队列：浏览器端挂 window.OfflineQueue，Node 端走 module.exports。
 * 所有函数都是纯函数：不修改传入的 state，返回新状态。
 *
 * State: { userId: number, ops: Op[] }
 * Op:    { opId: string, mistakeId: number, grade: 1|2|3|4|5, at: string,
 *          status: "pending"|"failed", reason: string|null, attempts: number }
 */
(function (global) {
  "use strict";

  var MAX_QUEUE = 200;   // 队列容量上限
  var MAX_REASON = 80;   // reason 最大长度
  var MAX_ATTEMPTS = 3;  // 达到此次数后 status 变为 "failed"

  // ---------- 校验辅助 ----------

  function isNonEmptyString(value) {
    return typeof value === "string" && value.length > 0;
  }

  function isPositiveInteger(value) {
    return typeof value === "number" && isFinite(value) &&
      Math.floor(value) === value && value > 0;
  }

  function isValidGrade(value) {
    return typeof value === "number" && Math.floor(value) === value &&
      value >= 1 && value <= 5;
  }

  function isParseableDateString(value) {
    return typeof value === "string" && !isNaN(Date.parse(value));
  }

  // ---------- 内部工具 ----------

  function copyOp(op) {
    return {
      opId: op.opId,
      mistakeId: op.mistakeId,
      grade: op.grade,
      at: op.at,
      status: op.status,
      reason: op.reason,
      attempts: op.attempts
    };
  }

  function withOps(state, ops) {
    return { userId: state.userId, ops: ops };
  }

  function indexOfOp(state, opId) {
    for (var i = 0; i < state.ops.length; i++) {
      if (state.ops[i].opId === opId) return i;
    }
    return -1;
  }

  // ---------- 公开 API ----------

  function create(userId) {
    return { userId: userId, ops: [] };
  }

  function enqueue(state, input) {
    var fields = input || {};
    if (!isNonEmptyString(fields.opId)) {
      throw new TypeError("enqueue: opId 必须是非空字符串");
    }
    if (indexOfOp(state, fields.opId) !== -1) {
      return state; // 队列里已存在同 opId：原样返回
    }
    if (!isPositiveInteger(fields.mistakeId)) {
      throw new TypeError("enqueue: mistakeId 必须是正整数");
    }
    if (!isValidGrade(fields.grade)) {
      throw new TypeError("enqueue: grade 必须是 1-5 的整数");
    }
    if (!isParseableDateString(fields.at)) {
      throw new TypeError("enqueue: at 必须是可被 Date 解析的字符串");
    }
    if (state.ops.length >= MAX_QUEUE) {
      throw new RangeError("enqueue: 队列已满（最多 " + MAX_QUEUE + " 条）");
    }
    var op = {
      opId: fields.opId,
      mistakeId: fields.mistakeId,
      grade: fields.grade,
      at: fields.at,
      status: "pending",
      reason: null,
      attempts: 0
    };
    return withOps(state, state.ops.concat([op]));
  }

  function nextBatch(state, n) {
    var limit = Math.floor(n);
    if (!(limit > 0)) return [];
    var batch = [];
    var blocked = Object.create(null); // mistakeId -> 前面还有未完成的 op
    for (var i = 0; i < state.ops.length && batch.length < limit; i++) {
      var op = state.ops[i];
      var key = "#" + op.mistakeId;
      if (blocked[key]) continue; // 同一道题前面还有未完成的 op，跳过
      blocked[key] = true;        // 本 op 未完成，阻塞后面的同题 op
      if (op.status !== "pending") continue;
      batch.push(copyOp(op));
    }
    return batch;
  }

  function markSynced(state, opId) {
    var idx = indexOfOp(state, opId);
    if (idx === -1) return state;
    return withOps(state, state.ops.slice(0, idx).concat(state.ops.slice(idx + 1)));
  }

  function markFailed(state, opId, reason) {
    var idx = indexOfOp(state, opId);
    if (idx === -1) return state;
    var attempts = state.ops[idx].attempts + 1;
    var next = copyOp(state.ops[idx]);
    next.attempts = attempts;
    next.reason = reason === null || reason === undefined
      ? null
      : String(reason).slice(0, MAX_REASON);
    next.status = attempts >= MAX_ATTEMPTS ? "failed" : "pending";
    var ops = state.ops.slice();
    ops[idx] = next;
    return withOps(state, ops);
  }

  function retryFailed(state) {
    var changed = false;
    var ops = [];
    for (var i = 0; i < state.ops.length; i++) {
      var op = state.ops[i];
      if (op.status !== "failed") {
        ops.push(op);
        continue;
      }
      changed = true;
      var next = copyOp(op);
      next.status = "pending";
      next.attempts = 0;
      ops.push(next);
    }
    return changed ? withOps(state, ops) : state;
  }

  function forUser(state, userId) {
    if (state.userId === userId) {
      return { state: state, dropped: 0 };
    }
    // 换号：丢弃整个旧队列，绝不把别人的评分交给新用户
    return { state: create(userId), dropped: state.ops.length };
  }

  function summary(state) {
    var pending = 0;
    var failed = 0;
    for (var i = 0; i < state.ops.length; i++) {
      if (state.ops[i].status === "failed") failed++; else pending++;
    }
    return { pending: pending, failed: failed, total: state.ops.length };
  }

  function serialize(state) {
    return JSON.stringify(state);
  }

  // ---------- validate ----------

  function normalizeOp(raw) {
    if (raw === null || typeof raw !== "object" || Array.isArray(raw)) return null;
    if (!isNonEmptyString(raw.opId)) return null;
    if (!isPositiveInteger(raw.mistakeId)) return null;
    if (!isValidGrade(raw.grade)) return null;
    if (!isParseableDateString(raw.at)) return null;
    if (raw.status !== "pending" && raw.status !== "failed") return null;
    var attempts = (typeof raw.attempts === "number" && isFinite(raw.attempts) && raw.attempts >= 0)
      ? Math.floor(raw.attempts)
      : 0;
    var reason = typeof raw.reason === "string" ? raw.reason.slice(0, MAX_REASON) : null;
    return {
      opId: raw.opId,
      mistakeId: raw.mistakeId,
      grade: raw.grade,
      at: raw.at,
      status: raw.status,
      reason: reason,
      attempts: attempts
    };
  }

  function validate(value) {
    if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
    if (typeof value.userId !== "number" || !isFinite(value.userId)) return null;
    var rawOps = Array.isArray(value.ops) ? value.ops : [];
    var seen = Object.create(null);
    var ops = [];
    for (var i = 0; i < rawOps.length && ops.length < MAX_QUEUE; i++) {
      var op = normalizeOp(rawOps[i]);
      if (op === null) continue;   // 结构非法：丢弃
      if (seen[op.opId]) continue; // 重复 opId：保留第一条
      seen[op.opId] = true;
      ops.push(op);
    }
    return { userId: value.userId, ops: ops };
  }

  var OfflineQueue = {
    create: create,
    enqueue: enqueue,
    nextBatch: nextBatch,
    markSynced: markSynced,
    markFailed: markFailed,
    retryFailed: retryFailed,
    forUser: forUser,
    summary: summary,
    serialize: serialize,
    validate: validate,
    MAX_QUEUE: MAX_QUEUE,
    MAX_REASON: MAX_REASON,
    MAX_ATTEMPTS: MAX_ATTEMPTS
  };

  global.OfflineQueue = OfflineQueue;
  if (typeof module !== "undefined") {
    module.exports = OfflineQueue;
  }
})(typeof window !== "undefined" ? window
  : typeof globalThis !== "undefined" ? globalThis
  : this);
