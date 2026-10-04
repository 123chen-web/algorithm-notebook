"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const DiffLines = require("../static/diff-lines.js");
const { diffLines, summarize } = DiffLines;

const types = (result) => result.map((item) => item.type);

// 固定种子的伪随机数生成器（LCG），保证随机测试可复现。
function makeRng(seed) {
  let state = seed >>> 0;
  return function next() {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function reconstruct(oldText, newText) {
  const result = diffLines(oldText, newText);
  const oldRec = result
    .filter((item) => item.type === "same" || item.type === "removed")
    .map((item) => item.text)
    .join("\n");
  const newRec = result
    .filter((item) => item.type === "same" || item.type === "added")
    .map((item) => item.text)
    .join("\n");
  return { result, oldRec, newRec };
}

test("模块导出 diffLines 和 summarize 两个函数", () => {
  assert.equal(typeof diffLines, "function");
  assert.equal(typeof summarize, "function");
  assert.strictEqual(DiffLines.diffLines, diffLines);
  assert.strictEqual(DiffLines.summarize, summarize);
});

test("两边都为空字符串时返回空数组（0 行，不是 1 个空行）", () => {
  assert.deepEqual(diffLines("", ""), []);
});

test("旧文本为空时全部是 added，oldLine 为 null，newLine 从 1 开始", () => {
  const result = diffLines("", "a\nb");
  assert.deepEqual(result, [
    { type: "added", oldLine: null, newLine: 1, text: "a" },
    { type: "added", oldLine: null, newLine: 2, text: "b" }
  ]);
});

test("新文本为空时全部是 removed，newLine 为 null，oldLine 从 1 开始", () => {
  const result = diffLines("a\nb", "");
  assert.deepEqual(result, [
    { type: "removed", oldLine: 1, newLine: null, text: "a" },
    { type: "removed", oldLine: 2, newLine: null, text: "b" }
  ]);
});

test("单行完全相同", () => {
  assert.deepEqual(diffLines("hello", "hello"), [
    { type: "same", oldLine: 1, newLine: 1, text: "hello" }
  ]);
});

test("多行完全相同，行号两边一一对应", () => {
  const text = "a\nb\nc";
  const result = diffLines(text, text);
  assert.deepEqual(types(result), ["same", "same", "same"]);
  assert.deepEqual(result, [
    { type: "same", oldLine: 1, newLine: 1, text: "a" },
    { type: "same", oldLine: 2, newLine: 2, text: "b" },
    { type: "same", oldLine: 3, newLine: 3, text: "c" }
  ]);
});

test("完全不同：2 removed + 2 added，无 same", () => {
  const result = diffLines("x\ny", "p\nq");
  assert.deepEqual(summarize(result), { added: 2, removed: 2, same: 0 });
  assert.deepEqual(types(result).sort(), ["added", "added", "removed", "removed"]);
  assert.deepEqual(
    result.map((item) => item.text).sort(),
    ["p", "q", "x", "y"]
  );
});

test("末尾差一行：前两行 same，末尾一对 removed/added", () => {
  const result = diffLines("a\nb\nc", "a\nb\nd");
  const same = result.filter((item) => item.type === "same");
  assert.deepEqual(same.map((item) => item.text), ["a", "b"]);
  assert.ok(result.some(
    (item) => item.type === "removed" && item.oldLine === 3 && item.newLine === null && item.text === "c"
  ));
  assert.ok(result.some(
    (item) => item.type === "added" && item.oldLine === null && item.newLine === 3 && item.text === "d"
  ));
});

test("开头差一行：第二行 same 的行号仍为 2/2", () => {
  const result = diffLines("x\nb", "y\nb");
  assert.deepEqual(summarize(result), { added: 1, removed: 1, same: 1 });
  assert.deepEqual(
    result.find((item) => item.type === "same"),
    { type: "same", oldLine: 2, newLine: 2, text: "b" }
  );
});

test("中间插入一行", () => {
  const result = diffLines("a\nc", "a\nb\nc");
  assert.deepEqual(result, [
    { type: "same", oldLine: 1, newLine: 1, text: "a" },
    { type: "added", oldLine: null, newLine: 2, text: "b" },
    { type: "same", oldLine: 2, newLine: 3, text: "c" }
  ]);
});

test("中间删除一行", () => {
  const result = diffLines("a\nb\nc", "a\nc");
  assert.deepEqual(result, [
    { type: "same", oldLine: 1, newLine: 1, text: "a" },
    { type: "removed", oldLine: 2, newLine: null, text: "b" },
    { type: "same", oldLine: 3, newLine: 2, text: "c" }
  ]);
});

test("中间替换一行：1 added + 1 removed + 2 same", () => {
  const result = diffLines("a\nb\nc", "a\nX\nc");
  assert.deepEqual(summarize(result), { added: 1, removed: 1, same: 2 });
  assert.deepEqual(
    result.filter((item) => item.type === "same").map((item) => item.text),
    ["a", "c"]
  );
});

test("重复行也能得到正确的 LCS 计数", () => {
  const result = diffLines("a\na\nb", "a\nb\nb");
  assert.deepEqual(summarize(result), { added: 1, removed: 1, same: 2 });
  const { oldRec, newRec } = reconstruct("a\na\nb", "a\nb\nb");
  assert.equal(oldRec, "a\na\nb");
  assert.equal(newRec, "a\nb\nb");
});

test("\\r\\n 与 \\n 混合视为相同", () => {
  const result = diffLines("a\r\nb\r\nc", "a\nb\nc");
  assert.deepEqual(types(result), ["same", "same", "same"]);
  assert.deepEqual(result.map((item) => item.text), ["a", "b", "c"]);
});

test("只去掉行尾的 \\r：'a\\r' 与 'a' 相同", () => {
  const result = diffLines("a\r", "a");
  assert.deepEqual(result, [
    { type: "same", oldLine: 1, newLine: 1, text: "a" }
  ]);
});

test("末尾多一个换行等于多一个空行（空行也是行）", () => {
  const result = diffLines("a\n", "a");
  assert.deepEqual(result, [
    { type: "same", oldLine: 1, newLine: 1, text: "a" },
    { type: "removed", oldLine: 2, newLine: null, text: "" }
  ]);
});

test("行内空格不做修剪，' a ' 与 'a' 不同", () => {
  const result = diffLines(" a ", "a");
  assert.deepEqual(summarize(result), { added: 1, removed: 1, same: 0 });
});

test("旧文本超过 2000 行返回 {tooLarge: true}", () => {
  const oldText = new Array(2001).fill("x").join("\n");
  assert.deepEqual(diffLines(oldText, "x"), { tooLarge: true });
});

test("新文本超过 2000 行返回 {tooLarge: true}", () => {
  const newText = new Array(2001).fill("x").join("\n");
  assert.deepEqual(diffLines("x", newText), { tooLarge: true });
});

test("恰好 2000 行时正常计算，不触发 tooLarge", () => {
  const text = new Array(2000).fill("L").join("\n");
  const result = diffLines(text, text);
  assert.ok(Array.isArray(result));
  assert.equal(result.length, 2000);
  assert.deepEqual(summarize(result), { added: 0, removed: 0, same: 2000 });
});

test("options.maxLines 调小后超限返回 {tooLarge: true}", () => {
  const text = new Array(6).fill("x").join("\n");
  assert.deepEqual(diffLines(text, text, { maxLines: 5 }), { tooLarge: true });
});

test("options.maxLines 调大后 2001 行也能正常计算", () => {
  const text = new Array(2001).fill("L").join("\n");
  const result = diffLines(text, text, { maxLines: 3000 });
  assert.ok(Array.isArray(result));
  assert.equal(result.length, 2001);
  assert.equal(result[2000].oldLine, 2001);
  assert.equal(result[2000].newLine, 2001);
});

test("混合 diff 中行号连续且正确", () => {
  const result = diffLines("a\nb\nc\nd", "a\nX\nc\nd\ne");
  let oldNo = 0;
  let newNo = 0;
  for (const item of result) {
    if (item.type === "same") {
      oldNo++;
      newNo++;
      assert.equal(item.oldLine, oldNo);
      assert.equal(item.newLine, newNo);
    } else if (item.type === "removed") {
      oldNo++;
      assert.equal(item.oldLine, oldNo);
      assert.equal(item.newLine, null);
    } else {
      newNo++;
      assert.equal(item.oldLine, null);
      assert.equal(item.newLine, newNo);
    }
  }
  assert.equal(oldNo, 4);
  assert.equal(newNo, 5);
});

test("added 的 oldLine 与 removed 的 newLine 一律为 null", () => {
  const result = diffLines("a\nb\nc", "a\nX\nc");
  for (const item of result) {
    if (item.type === "added") assert.equal(item.oldLine, null);
    if (item.type === "removed") assert.equal(item.newLine, null);
    if (item.type === "same") {
      assert.equal(typeof item.oldLine, "number");
      assert.equal(typeof item.newLine, "number");
    }
  }
});

test("summarize 统计 added/removed/same 数值", () => {
  const result = diffLines("a\nb\nc", "a\nX\nc");
  assert.deepEqual(summarize(result), { added: 1, removed: 1, same: 2 });
});

test("summarize 对空 diff 返回三个 0", () => {
  assert.deepEqual(summarize([]), { added: 0, removed: 0, same: 0 });
});

test("每项的 type 只允许是 same/removed/added", () => {
  const result = diffLines("l1\nl2\nl3\nl4", "l1\nX\nl3\nY");
  for (const item of result) {
    assert.ok(["same", "removed", "added"].includes(item.type));
    assert.equal(typeof item.text, "string");
  }
});

test("小规模手工用例：same+removed 拼回旧文本，same+added 拼回新文本", () => {
  const oldText = "a\nb\nc\nd";
  const newText = "a\nX\nc\nd\ne";
  const { oldRec, newRec } = reconstruct(oldText, newText);
  assert.equal(oldRec, oldText);
  assert.equal(newRec, newText);
});

test("固定种子随机 200 组：拼回不变量、计数与行号全部成立", () => {
  const rng = makeRng(20261004);
  for (let group = 0; group < 200; group++) {
    const n = Math.floor(rng() * 21);
    const m = Math.floor(rng() * 21);
    const alphabet = 1 + Math.floor(rng() * 4);
    const makeLines = (count) => {
      const lines = [];
      for (let k = 0; k < count; k++) {
        lines.push("L" + Math.floor(rng() * alphabet));
      }
      return lines;
    };
    const oldLines = makeLines(n);
    const newLines = makeLines(m);
    const oldText = oldLines.join("\n");
    const newText = newLines.join("\n");

    const result = diffLines(oldText, newText);
    assert.ok(Array.isArray(result), "第 " + group + " 组应返回数组");

    const counts = summarize(result);
    assert.equal(counts.same + counts.removed, n, "第 " + group + " 组旧行数");
    assert.equal(counts.same + counts.added, m, "第 " + group + " 组新行数");

    const oldRec = result
      .filter((item) => item.type === "same" || item.type === "removed")
      .map((item) => item.text)
      .join("\n");
    const newRec = result
      .filter((item) => item.type === "same" || item.type === "added")
      .map((item) => item.text)
      .join("\n");
    assert.equal(oldRec, oldText, "第 " + group + " 组旧文本拼回");
    assert.equal(newRec, newText, "第 " + group + " 组新文本拼回");

    let oldNo = 0;
    let newNo = 0;
    for (const item of result) {
      if (item.type === "same") {
        oldNo++;
        newNo++;
        assert.equal(item.oldLine, oldNo, "第 " + group + " 组 same oldLine");
        assert.equal(item.newLine, newNo, "第 " + group + " 组 same newLine");
      } else if (item.type === "removed") {
        oldNo++;
        assert.equal(item.oldLine, oldNo, "第 " + group + " 组 removed oldLine");
        assert.equal(item.newLine, null);
      } else {
        newNo++;
        assert.equal(item.oldLine, null);
        assert.equal(item.newLine, newNo, "第 " + group + " 组 added newLine");
      }
    }
    assert.equal(oldNo, n);
    assert.equal(newNo, m);
  }
});
