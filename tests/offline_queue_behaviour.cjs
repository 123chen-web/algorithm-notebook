"use strict";

var test = require("node:test");
var assert = require("node:assert/strict");
var OfflineQueue = require("../static/offline-queue.js");

var ISO = "2026-10-05T08:00:00.000Z";

// ---------- 测试辅助 ----------

function makeOp(opId, mistakeId, overrides) {
  var op = {
    opId: opId,
    mistakeId: mistakeId,
    grade: 3,
    at: ISO,
    status: "pending",
    reason: null,
    attempts: 0
  };
  if (overrides) { for (var k in overrides) op[k] = overrides[k]; }
  return op;
}

function stateWith(ops, userId) {
  return { userId: userId === undefined ? 1 : userId, ops: ops };
}

function fullState(count) {
  var ops = [];
  for (var i = 0; i < count; i++) ops.push(makeOp("op-" + i, i + 1));
  return stateWith(ops);
}

function inputFor(opId, mistakeId, overrides) {
  var input = { opId: opId, mistakeId: mistakeId === undefined ? 1 : mistakeId, grade: 3, at: ISO };
  if (overrides) { for (var k in overrides) input[k] = overrides[k]; }
  return input;
}

function enqueueOp(state, opId, mistakeId, overrides) {
  return OfflineQueue.enqueue(state, inputFor(opId, mistakeId, overrides));
}

function opIds(ops) {
  return ops.map(function (op) { return op.opId; });
}

function failTimes(state, opId, times) {
  for (var i = 0; i < times; i++) state = OfflineQueue.markFailed(state, opId, "err");
  return state;
}

// ---------- create ----------

test("create 返回带 userId 的空队列", function () {
  assert.deepEqual(OfflineQueue.create(1), { userId: 1, ops: [] });
  assert.deepEqual(OfflineQueue.create(0), { userId: 0, ops: [] });
});

test("create 每次返回独立的 ops 数组", function () {
  var a = OfflineQueue.create(1);
  var b = OfflineQueue.create(1);
  assert.notEqual(a.ops, b.ops);
});

// ---------- enqueue ----------

test("enqueue 追加到末尾并写入默认字段", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1, { grade: 5 });
  s = enqueueOp(s, "b", 2);
  assert.equal(s.ops.length, 2);
  assert.deepEqual(s.ops[0], {
    opId: "a", mistakeId: 1, grade: 5, at: ISO,
    status: "pending", reason: null, attempts: 0
  });
  assert.equal(s.ops[1].opId, "b");
});

test("enqueue 不修改传入的原状态", function () {
  var s = OfflineQueue.create(1);
  var s2 = enqueueOp(s, "a", 1);
  assert.equal(s.ops.length, 0);
  assert.equal(s2.ops.length, 1);
  assert.notEqual(s2, s);
  assert.notEqual(s2.ops, s.ops);
});

test("enqueue 重复 opId 时原样返回（同一引用）", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  var s2 = enqueueOp(s, "a", 2, { grade: 1 });
  assert.equal(s2, s);
  assert.equal(s2.ops.length, 1);
});

test("enqueue 非法 opId 抛 TypeError", function () {
  var s = OfflineQueue.create(1);
  assert.throws(function () { enqueueOp(s, "", 1); }, TypeError);
  assert.throws(function () { enqueueOp(s, 7, 1); }, TypeError);
  assert.throws(function () { OfflineQueue.enqueue(s, { mistakeId: 1, grade: 3, at: ISO }); }, TypeError);
  assert.throws(function () { OfflineQueue.enqueue(s); }, TypeError);
});

test("enqueue 非法 mistakeId 抛 TypeError", function () {
  var s = OfflineQueue.create(1);
  assert.throws(function () { enqueueOp(s, "a", 0); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", -3); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", 1.5); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", "5"); }, TypeError);
});

test("enqueue 非法 grade 抛 TypeError", function () {
  var s = OfflineQueue.create(1);
  assert.throws(function () { enqueueOp(s, "a", 1, { grade: 0 }); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", 1, { grade: 6 }); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", 1, { grade: 2.5 }); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", 1, { grade: "3" }); }, TypeError);
});

test("enqueue 非法 at 抛 TypeError", function () {
  var s = OfflineQueue.create(1);
  assert.throws(function () { enqueueOp(s, "a", 1, { at: "" }); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", 1, { at: "not-a-date" }); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", 1, { at: 123 }); }, TypeError);
  assert.throws(function () { enqueueOp(s, "a", 1, { at: null }); }, TypeError);
});

test("enqueue 队列满 200 条抛 RangeError", function () {
  var s = fullState(200);
  assert.throws(function () { enqueueOp(s, "op-x", 999); }, RangeError);
});

test("enqueue 199 条时仍可入队", function () {
  var s = enqueueOp(fullState(199), "op-x", 999);
  assert.equal(s.ops.length, 200);
});

// ---------- nextBatch ----------

test("nextBatch 按入队顺序返回最多 n 条 pending", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  s = enqueueOp(s, "c", 3);
  assert.deepEqual(opIds(OfflineQueue.nextBatch(s, 2)), ["a", "b"]);
  assert.deepEqual(opIds(OfflineQueue.nextBatch(s, 3)), ["a", "b", "c"]);
});

test("nextBatch 跳过 failed 的 op", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  s = failTimes(s, "a", 3);
  assert.deepEqual(opIds(OfflineQueue.nextBatch(s, 10)), ["b"]);
});

test("nextBatch 同一道题顺序保证：前序 op 未完成时后续不进批", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 1);
  s = enqueueOp(s, "c", 2);
  assert.deepEqual(opIds(OfflineQueue.nextBatch(s, 10)), ["a", "c"]);
});

test("nextBatch 前序 op 是 failed 也阻塞同题后续 op", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 1);
  s = failTimes(s, "a", 3);
  assert.deepEqual(OfflineQueue.nextBatch(s, 10), []);
});

test("nextBatch 前序 op markSynced 后同题下一 op 可进批", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 1);
  s = OfflineQueue.markSynced(s, "a");
  assert.deepEqual(opIds(OfflineQueue.nextBatch(s, 10)), ["b"]);
});

test("nextBatch n 为 0、负数或 NaN 时返回空数组", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  assert.deepEqual(OfflineQueue.nextBatch(s, 0), []);
  assert.deepEqual(OfflineQueue.nextBatch(s, -3), []);
  assert.deepEqual(OfflineQueue.nextBatch(s, NaN), []);
});

test("nextBatch 返回副本：改返回值不影响原状态", function () {
  var s = stateWith([makeOp("a", 1)]);
  var batch = OfflineQueue.nextBatch(s, 1);
  batch[0].attempts = 99;
  batch[0].status = "failed";
  assert.deepEqual(s, stateWith([makeOp("a", 1)]));
});

test("nextBatch n 大于可用数量时返回全部符合条件的 op", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  var batch = OfflineQueue.nextBatch(s, 100);
  assert.equal(batch.length, 2);
});

// ---------- markSynced ----------

test("markSynced 删除指定 op，其余保持顺序", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  s = enqueueOp(s, "c", 3);
  var s2 = OfflineQueue.markSynced(s, "b");
  assert.deepEqual(opIds(s2.ops), ["a", "c"]);
});

test("markSynced 不存在的 opId 原样返回", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  assert.equal(OfflineQueue.markSynced(s, "nope"), s);
});

test("markSynced 不修改传入的原状态", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  var snapshot = JSON.parse(OfflineQueue.serialize(s));
  OfflineQueue.markSynced(s, "a");
  assert.deepEqual(s, snapshot);
});

// ---------- markFailed ----------

test("markFailed attempts 加 1，未到 3 次仍为 pending", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  s = OfflineQueue.markFailed(s, "a", "e1");
  assert.equal(s.ops[0].attempts, 1);
  assert.equal(s.ops[0].status, "pending");
  assert.equal(s.ops[0].reason, "e1");
  s = OfflineQueue.markFailed(s, "a", "e2");
  assert.equal(s.ops[0].attempts, 2);
  assert.equal(s.ops[0].status, "pending");
});

test("markFailed 第 3 次变为 failed 且不再被 nextBatch 选中", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  s = failTimes(s, "a", 3);
  assert.equal(s.ops[0].attempts, 3);
  assert.equal(s.ops[0].status, "failed");
  assert.deepEqual(OfflineQueue.nextBatch(s, 10), []);
});

test("markFailed reason 截断到 80 字符，null 保持 null", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  s = OfflineQueue.markFailed(s, "a", "r".repeat(200));
  assert.equal(s.ops[0].reason.length, 80);
  s = OfflineQueue.markFailed(s, "b", null);
  assert.equal(s.ops[1].reason, null);
});

test("markFailed 不存在的 opId 原样返回", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  assert.equal(OfflineQueue.markFailed(s, "nope", "x"), s);
});

test("markFailed 不修改传入的原状态", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  var s2 = OfflineQueue.markFailed(s, "a", "boom");
  assert.equal(s.ops[0].attempts, 0);
  assert.equal(s.ops[0].status, "pending");
  assert.equal(s2.ops[0].attempts, 1);
});

// ---------- retryFailed ----------

test("retryFailed 把 failed 重置为 pending 且 attempts 归零，pending 不动", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  s = failTimes(s, "a", 3);
  var r = OfflineQueue.retryFailed(s);
  assert.equal(r.ops[0].status, "pending");
  assert.equal(r.ops[0].attempts, 0);
  assert.equal(r.ops[1], s.ops[1]); // 未受影响的 op 保持原引用
  assert.deepEqual(opIds(OfflineQueue.nextBatch(r, 10)), ["a", "b"]);
});

test("retryFailed 没有 failed 时原样返回", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  assert.equal(OfflineQueue.retryFailed(s), s);
});

// ---------- forUser ----------

test("forUser 同 userId 返回原 state 且 dropped 为 0", function () {
  var s = enqueueOp(OfflineQueue.create(1), "a", 1);
  var r = OfflineQueue.forUser(s, 1);
  assert.equal(r.state, s);
  assert.equal(r.dropped, 0);
});

test("forUser 换号丢弃整个队列并报告丢弃数量", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  s = enqueueOp(s, "c", 3);
  var r = OfflineQueue.forUser(s, 2);
  assert.deepEqual(r.state, { userId: 2, ops: [] });
  assert.equal(r.dropped, 3);
});

// ---------- summary ----------

test("summary 统计 pending / failed / total", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2);
  s = enqueueOp(s, "c", 3);
  s = failTimes(s, "a", 3);
  assert.deepEqual(OfflineQueue.summary(s), { pending: 2, failed: 1, total: 3 });
});

test("summary 空队列全部为 0", function () {
  assert.deepEqual(OfflineQueue.summary(OfflineQueue.create(9)), { pending: 0, failed: 0, total: 0 });
});

// ---------- serialize / validate ----------

test("serialize 与 validate 往返一致", function () {
  var s = OfflineQueue.create(7);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 2, { grade: 5 });
  s = OfflineQueue.markFailed(s, "a", "network down");
  var roundTripped = OfflineQueue.validate(JSON.parse(OfflineQueue.serialize(s)));
  assert.deepEqual(roundTripped, s);
});

test("validate 拒绝非对象、数组与缺失 / 非法 userId", function () {
  assert.equal(OfflineQueue.validate(null), null);
  assert.equal(OfflineQueue.validate(undefined), null);
  assert.equal(OfflineQueue.validate([]), null);
  assert.equal(OfflineQueue.validate("nope"), null);
  assert.equal(OfflineQueue.validate(42), null);
  assert.equal(OfflineQueue.validate({ ops: [] }), null);
  assert.equal(OfflineQueue.validate({ userId: "1", ops: [] }), null);
  assert.equal(OfflineQueue.validate({ userId: NaN, ops: [] }), null);
});

test("validate 丢弃结构非法的 op，保留合法的", function () {
  var value = {
    userId: 3,
    ops: [
      makeOp("ok", 1),
      makeOp("badGrade", 2, { grade: 9 }),
      makeOp("badGrade2", 2, { grade: "3" }),
      makeOp("", 3),
      makeOp("badStatus", 4, { status: "done" }),
      makeOp("badMistake", 0),
      makeOp("badAt", 5, { at: "not a date" }),
      "garbage",
      null,
      [1, 2, 3]
    ]
  };
  var v = OfflineQueue.validate(value);
  assert.deepEqual(v, { userId: 3, ops: [makeOp("ok", 1)] });
});

test("validate 去掉重复 opId，保留第一条", function () {
  var v = OfflineQueue.validate({
    userId: 1,
    ops: [makeOp("x", 1, { grade: 1 }), makeOp("x", 2, { grade: 2 }), makeOp("y", 3)]
  });
  assert.equal(v.ops.length, 2);
  assert.equal(v.ops[0].grade, 1);
  assert.equal(v.ops[1].opId, "y");
});

test("validate 截断到 200 条", function () {
  var ops = [];
  for (var i = 0; i < 250; i++) ops.push(makeOp("op-" + i, i + 1));
  var v = OfflineQueue.validate({ userId: 1, ops: ops });
  assert.equal(v.ops.length, 200);
  assert.equal(v.ops[199].opId, "op-199");
});

test("validate 规范化 attempts 与 reason", function () {
  var v = OfflineQueue.validate({
    userId: 1,
    ops: [
      makeOp("a", 1, { attempts: -2 }),
      makeOp("b", 2, { attempts: "3" }),
      makeOp("c", 3, { attempts: 2.9, reason: "x".repeat(100) }),
      makeOp("d", 4, { reason: 42 })
    ]
  });
  assert.equal(v.ops[0].attempts, 0);
  assert.equal(v.ops[1].attempts, 0);
  assert.equal(v.ops[2].attempts, 2);
  assert.equal(v.ops[2].reason.length, 80);
  assert.equal(v.ops[3].reason, null);
});

test("validate 缺失 ops 字段时视为空队列", function () {
  assert.deepEqual(OfflineQueue.validate({ userId: 5 }), { userId: 5, ops: [] });
});

test("validate 返回全新的规范化状态，不引用入参", function () {
  var input = { userId: 1, ops: [makeOp("a", 1)] };
  var v = OfflineQueue.validate(input);
  assert.notEqual(v, input);
  assert.notEqual(v.ops, input.ops);
  assert.notEqual(v.ops[0], input.ops[0]);
  assert.deepEqual(v.ops[0], makeOp("a", 1));
});

// ---------- 全局不变量 ----------

test("所有 API 都不修改传入的状态", function () {
  var s = OfflineQueue.create(1);
  s = enqueueOp(s, "a", 1);
  s = enqueueOp(s, "b", 1);
  s = enqueueOp(s, "c", 2);
  var snapshot = JSON.parse(OfflineQueue.serialize(s));
  OfflineQueue.enqueue(s, inputFor("d", 3, { grade: 4 }));
  OfflineQueue.markSynced(s, "a");
  OfflineQueue.markFailed(s, "b", "boom");
  OfflineQueue.retryFailed(s);
  OfflineQueue.nextBatch(s, 2);
  OfflineQueue.summary(s);
  OfflineQueue.serialize(s);
  OfflineQueue.forUser(s, 2);
  assert.deepEqual(s, snapshot);
});

test("固定种子随机 300 次操作后不变量成立", function () {
  var seed = 20261005;
  function rand() { // mulberry32，固定种子 -> 固定序列
    seed |= 0; seed = (seed + 0x6D2B79F5) | 0;
    var t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  }
  function pick(arr) { return arr[Math.floor(rand() * arr.length)]; }

  var opIdPool = [];
  for (var i = 0; i < 40; i++) opIdPool.push("op-" + i);
  var mistakePool = [1, 2, 3, 4, 5];

  var state = OfflineQueue.create(1);
  for (var step = 0; step < 300; step++) {
    var action = Math.floor(rand() * 5);
    var opId = pick(opIdPool);
    try {
      if (action === 0) {
        state = OfflineQueue.enqueue(state, {
          opId: opId,
          mistakeId: pick(mistakePool),
          grade: 1 + Math.floor(rand() * 5),
          at: ISO
        });
      } else if (action === 1) {
        state = OfflineQueue.markSynced(state, opId);
      } else if (action === 2) {
        state = OfflineQueue.markFailed(state, opId, "err-" + step);
      } else if (action === 3) {
        state = OfflineQueue.retryFailed(state);
      } else {
        OfflineQueue.nextBatch(state, 1 + Math.floor(rand() * 10));
      }
    } catch (err) {
      assert.ok(err instanceof RangeError, "随机操作只可能因队列满抛 RangeError，实际: " + err);
    }

    // 不变量：长度 <= 200、opId 唯一、attempts 为非负整数、status 合法、reason 不超长
    assert.ok(state.ops.length <= 200, "队列长度 <= 200");
    var seen = {};
    for (var j = 0; j < state.ops.length; j++) {
      var op = state.ops[j];
      assert.ok(!seen[op.opId], "opId 唯一: " + op.opId);
      seen[op.opId] = true;
      assert.ok(Number.isInteger(op.attempts) && op.attempts >= 0, "attempts 是非负整数");
      assert.ok(op.status === "pending" || op.status === "failed", "status 合法");
      if (op.status === "failed") assert.ok(op.attempts >= 3, "failed 时 attempts >= 3");
      if (op.reason !== null) assert.ok(op.reason.length <= 80, "reason <= 80 字符");
    }
    var s = OfflineQueue.summary(state);
    assert.equal(s.total, state.ops.length);
    assert.equal(s.pending + s.failed, s.total, "summary 自洽");

    // serialize -> validate 往返在随机状态下也保持一致
    assert.deepEqual(OfflineQueue.validate(JSON.parse(OfflineQueue.serialize(state))), state);
  }
});
