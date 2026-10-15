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

test("做题结果算成绩：与服务端共用同一张对照表", () => {
  const cases = JSON.parse(fs.readFileSync(path.join(__dirname, "fixtures", "verified_grading.json"), "utf-8"));
  for (const entry of cases) {
    assert.equal(RedoFlow.qualityFromEvidence(entry.mode, entry.evidence), entry.quality, JSON.stringify(entry));
  }
});

test("做题结果缺失或模式不对时抛错，不悄悄给分", () => {
  assert.throws(() => RedoFlow.qualityFromEvidence("redo", {}));
  assert.throws(() => RedoFlow.qualityFromEvidence("redo", { result: "maybe" }));
  assert.throws(() => RedoFlow.qualityFromEvidence("rewrite", {}));
  assert.throws(() => RedoFlow.qualityFromEvidence("recall", { result: "clean" }));
});

test("evidence 接受新字段并拒绝不合法的值", () => {
  const text = RedoFlow.buildEvidence({ result: "clean", seconds: 12, limit_seconds: 900, auto: true, ref: "两数之和", suggested: 4, final: 4 });
  assert.deepEqual(JSON.parse(text), { suggested: 4, final: 4, seconds: 12, limit_seconds: 900, result: "clean", auto: true, ref: "两数之和" });
  assert.throws(() => RedoFlow.buildEvidence({ result: "maybe" }));
  assert.throws(() => RedoFlow.buildEvidence({ auto: false }));
  assert.throws(() => RedoFlow.buildEvidence({ ref: "x".repeat(61) }));
  assert.throws(() => RedoFlow.buildEvidence({ limit_seconds: -1 }));
});

test("复习方式有四种，类似题有中文标签", () => {
  assert.deepEqual([...RedoFlow.MODES], ["recall", "rewrite", "redo", "similar"]);
  assert.equal(RedoFlow.MODE_LABEL.similar, "类似题");
});

test("startTimer 按真实时间算：后台变慢也不少算，暂停的时间不算进用时", () => {
  let clock = 1_000_000;
  const ticks = [];
  let done = false;
  const timer = RedoFlow.startTimer(600, (left, state) => ticks.push([left, Boolean(state && state.paused)]), () => { done = true; }, { now: () => clock });
  clock += 125_000; // 切到原题标签页做了两分钟多，定时器可能根本没跑
  assert.equal(timer.elapsed(), 125);
  timer.pause();
  assert.equal(timer.isPaused(), true);
  assert.deepEqual(ticks.at(-1), [475, true]);
  clock += 300_000; // 暂停 5 分钟
  assert.equal(timer.elapsed(), 125, "暂停期间不计时");
  timer.resume();
  assert.deepEqual(ticks.at(-1), [475, false]);
  clock += 5_000;
  assert.equal(timer.elapsed(), 130);
  assert.equal(done, false);
  timer.cancel();
  assert.equal(timer.isDone(), true);
});

test("formatCountdown 标出暂停状态", () => {
  assert.equal(RedoFlow.formatCountdown(125), "剩余 2:05");
  assert.equal(RedoFlow.formatCountdown(125, { paused: true }), "已暂停 · 剩余 2:05");
});

test("problemUrl 认来源链接、补 https、老题思路里的判题网站链接", () => {
  assert.equal(RedoFlow.problemUrl({ source_url: " https://leetcode.cn/problems/two-sum/ " }), "https://leetcode.cn/problems/two-sum/");
  assert.equal(RedoFlow.problemUrl({ source_url: "codeforces.com/problemset/problem/1/A" }), "https://codeforces.com/problemset/problem/1/A");
  assert.equal(RedoFlow.problemUrl({ source_url: "", thinking: "题目链接：https://www.luogu.com.cn/problem/P1001。\n思路……" }), "https://www.luogu.com.cn/problem/P1001");
  assert.equal(RedoFlow.problemUrl({ source_url: "", thinking: "参考 https://oi-wiki.org/ds/stack/ 这篇" }), "", "不是判题网站的链接不当原题");
  assert.equal(RedoFlow.problemUrl({}), "");
});
