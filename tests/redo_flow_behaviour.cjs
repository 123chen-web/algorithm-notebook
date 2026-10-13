"use strict";
/* 复习=重做：redo-flow.js 纯逻辑的 Node 行为检查（normalize/diff/建议分/evidence/计时）。 */
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function loadRedoFlow() {
  const src = fs.readFileSync(path.join(__dirname, "..", "static", "redo-flow.js"), "utf-8");
  const window = {};
  // startTimer 用到 timers 和 Date，一并放进沙箱。
  vm.runInNewContext(src, { window, setInterval, clearInterval, Date });
  assert.ok(window.RedoFlow, "window.RedoFlow 应当导出");
  return window.RedoFlow;
}
const RedoFlow = loadRedoFlow();

test("normalize 去掉注释、空行并压缩空白", () => {
  const code = [
    "// 行注释",
    "def f():  # python 注释",
    "    x = 1   ",
    "",
    "    /* 块注释 */ y = 2",
    's = "a // b"  # 字符串里的 // 要保留',
  ].join("\n");
  // 跨 vm realm 的数组不能 deepStrictEqual，先转成原生数组。
  assert.deepEqual([...RedoFlow.normalize(code)], ["def f():", "x = 1", "y = 2", 's = "a // b"']);
});

test("diffLines 找出缺失和多余的行并给出相似度", () => {
  const expected = "def f():\n    x = 1\n    return x";
  const actual = "def f():\n    x = 2\n    return x\n    extra = 1";
  const diff = RedoFlow.diffLines(expected, actual);
  assert.deepEqual([...diff.missing], ["x = 1"]);
  assert.deepEqual([...diff.extra], ["x = 2", "extra = 1"]);
  // matched=2（def f(): 和 return x），similarity=2*2/(3+4)=0.57
  assert.equal(diff.similarity, 0.57);
});

test("diffLines 全对时相似度为 1，注释和空白差异不计", () => {
  const diff = RedoFlow.diffLines(
    "def f():\n  # 注释\n  return 1",
    "def f():\n    return 1  // 尾注",
  );
  assert.equal(diff.similarity, 1);
  assert.deepEqual([...diff.missing], []);
  assert.deepEqual([...diff.extra], []);
});

test("diffLines 两边都空时相似度为 1", () => {
  assert.equal(RedoFlow.diffLines("", "   \n # c").similarity, 1);
});

test("similarityToGrade 按 0.85/0.6 分界", () => {
  assert.equal(RedoFlow.similarityToGrade(0.85), 4);
  assert.equal(RedoFlow.similarityToGrade(0.9), 4);
  assert.equal(RedoFlow.similarityToGrade(0.6), 3);
  assert.equal(RedoFlow.similarityToGrade(0.7), 3);
  assert.equal(RedoFlow.similarityToGrade(0.59), 0);
  assert.equal(RedoFlow.similarityToGrade(0), 0);
});

test("redoGradeSuggest 用时不到一半给 5，否则给 4", () => {
  assert.equal(RedoFlow.redoGradeSuggest(100, 900), 5);
  assert.equal(RedoFlow.redoGradeSuggest(449, 900), 5);
  assert.equal(RedoFlow.redoGradeSuggest(450, 900), 4);
  assert.equal(RedoFlow.redoGradeSuggest(900, 900), 4);
});

test("buildEvidence 组装并校验类型", () => {
  const text = RedoFlow.buildEvidence({ suggested: 3, final: 4, similarity: 0.82, seconds: 412 });
  assert.equal(text, JSON.stringify({ suggested: 3, final: 4, similarity: 0.82, seconds: 412 }));
  assert.equal(RedoFlow.buildEvidence({}), "{}");
  assert.throws(() => RedoFlow.buildEvidence({ suggested: 9 }), /0 到 5/);
  assert.throws(() => RedoFlow.buildEvidence({ final: 2.5 }), /0 到 5/);
  assert.throws(() => RedoFlow.buildEvidence({ similarity: 2 }), /0 到 1/);
  assert.throws(() => RedoFlow.buildEvidence({ seconds: -1 }), /非负整数/);
  assert.throws(() => RedoFlow.buildEvidence({ suggested: 1, final: 2, extra: "x" }), /500|字段/);
});

test("startTimer 倒计时并支持取消", async () => {
  const ticks = [];
  let done = false;
  const timer = RedoFlow.startTimer(3, (r) => ticks.push(r), () => { done = true; });
  assert.deepEqual(ticks, [3]);
  await new Promise((resolve) => setTimeout(resolve, 3500));
  assert.ok(done, "3 秒后应当结束");
  assert.ok(ticks.includes(0), "应当 tick 到 0");
  timer.cancel(); // 结束后 cancel 不报错
});

test("startTimer 取消后不再回调", async () => {
  let done = false;
  const timer = RedoFlow.startTimer(5, () => {}, () => { done = true; });
  timer.cancel();
  await new Promise((resolve) => setTimeout(resolve, 1200));
  assert.equal(done, false);
  assert.equal(timer.elapsed(), 0);
});

test("MODE_LABEL 覆盖三种模式", () => {
  // 跨 vm realm 的对象不能 deepStrictEqual，逐个键断言。
  assert.equal(RedoFlow.MODE_LABEL.recall, "自评");
  assert.equal(RedoFlow.MODE_LABEL.rewrite, "默写");
  assert.equal(RedoFlow.MODE_LABEL.redo, "限时重做");
  assert.deepEqual([...RedoFlow.REDO_LIMITS], [10, 15, 30]);
  assert.equal(RedoFlow.DEFAULT_REDO_LIMIT, 15);
});
