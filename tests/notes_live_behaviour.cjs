"use strict";

/* 编辑时的积木效果（static/notes-live.js）：识别规则与“挂不上就什么也不做”。真实编辑器里的效果在浏览器里验过。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

const plain = (value) => JSON.parse(JSON.stringify(value));
const live = () => load(["notes-live.js"]).window.NotesLive;

test("highlightRanges：找出每个 ==x==（含两头），空的、行内代码里的不算", () => {
  const api = live();
  assert.deepEqual(plain(api.highlightRanges("先看 ==边界== 再写 ==x==")), [[3, 9], [13, 18]]);
  assert.deepEqual(plain(api.highlightRanges("a ==== b == c")), [], "空的不算");
  assert.deepEqual(plain(api.highlightRanges("x == y == z")), [], "两边有空格的比较式不算");
  const text = "代码 ==a== 和 ==b==";
  const mask = [...text].map((_, i) => i >= 3 && i < 8);
  assert.deepEqual(plain(api.highlightRanges(text, mask)), [[11, 16]], "行内代码里的不算");
});

test("calloutHead：认 [!类型] 和编辑器转义后的 \[!类型\]，类型和阅读视图一致", () => {
  const api = live();
  assert.deepEqual(plain(api.calloutHead("[!易错] 下标")), { kind: "danger", length: 5 });
  assert.deepEqual(plain(api.calloutHead("\\[!答案\\]- 想好再看")), { kind: "answer", length: 8 });
  assert.equal(api.calloutHead("[!Tip]").kind, "tip");
  assert.equal(api.calloutHead("[!没见过的类型]").kind, "tip", "不认识的类型按提示框");
  assert.equal(api.calloutHead("普通引用"), null);
});

test("attach：编辑器缺这缺那时不挂、不报错，编辑器照常用", () => {
  const api = live();
  assert.equal(api.attach(null), false);
  assert.equal(api.attach({ state: { plugins: [] }, registerPlugin() {} }), false);
  assert.equal(api.attach({ isDestroyed: true }), false);
});
