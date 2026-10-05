"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const TT = require("../static/trace-table.js");

const deepCopy = (v) => JSON.parse(JSON.stringify(v));

// ---------- create ----------

test("create() 默认 3 列 3 行，列名 v1..v3，单元格为空串", () => {
  const m = TT.create();
  assert.deepEqual(m.cols, ["v1", "v2", "v3"]);
  assert.equal(m.rows.length, 3);
  for (const row of m.rows) assert.deepEqual(row, ["", "", ""]);
});

test("create(1, 1) 得到最小模型", () => {
  const m = TT.create(1, 1);
  assert.deepEqual(m, { cols: ["v1"], rows: [[""]] });
});

test("create 参数超过上限时按上限处理", () => {
  const m = TT.create(99, 999);
  assert.equal(m.cols.length, 20);
  assert.equal(m.rows.length, 60);
  assert.equal(m.cols[19], "v20");
  assert.ok(m.rows.every((r) => r.length === 20));
});

test("create 参数小于 1 时按 1 处理", () => {
  const m = TT.create(0, -5);
  assert.equal(m.cols.length, 1);
  assert.equal(m.rows.length, 1);
});

// ---------- addRow ----------

test("addRow 缺省 index 时追加到末尾", () => {
  const m = TT.addRow(TT.create(2, 2));
  assert.equal(m.rows.length, 3);
  assert.deepEqual(m.rows[2], ["", ""]);
});

test("addRow 在 index 之前插入", () => {
  let m = TT.create(2, 2);
  m = TT.setCell(m, 0, 0, "a");
  m = TT.setCell(m, 1, 0, "b");
  m = TT.addRow(m, 1);
  assert.equal(m.rows[0][0], "a");
  assert.deepEqual(m.rows[1], ["", ""]);
  assert.equal(m.rows[2][0], "b");
});

test("addRow index 越界（负数或过大）时追加到末尾", () => {
  const a = TT.addRow(TT.create(2, 2), -1);
  const b = TT.addRow(TT.create(2, 2), 99);
  assert.deepEqual(a.rows[2], ["", ""]);
  assert.deepEqual(b.rows[2], ["", ""]);
});

test("addRow 已达 60 行上限时原样返回（同一引用）", () => {
  const m = TT.create(3, 60);
  assert.equal(TT.addRow(m), m);
  assert.equal(TT.addRow(m, 0), m);
});

test("addRow 不修改原模型", () => {
  const m = TT.create(2, 2);
  const snap = deepCopy(m);
  TT.addRow(m, 0);
  assert.deepEqual(m, snap);
});

// ---------- removeRow ----------

test("removeRow 删除指定行", () => {
  let m = TT.create(1, 3);
  m = TT.setCell(m, 0, 0, "a");
  m = TT.setCell(m, 1, 0, "b");
  m = TT.setCell(m, 2, 0, "c");
  m = TT.removeRow(m, 1);
  assert.deepEqual(m.rows, [["a"], ["c"]]);
});

test("removeRow 越界时原样返回", () => {
  const m = TT.create(2, 2);
  assert.equal(TT.removeRow(m, -1), m);
  assert.equal(TT.removeRow(m, 2), m);
  assert.equal(TT.removeRow(m), m);
});

test("removeRow 至少保留 1 行", () => {
  const m = TT.create(2, 1);
  assert.equal(TT.removeRow(m, 0), m);
});

// ---------- addCol ----------

test("addCol 缺省 index 时追加到末尾，列名自动取 v<N>", () => {
  const m = TT.addCol(TT.create(3, 2));
  assert.deepEqual(m.cols, ["v1", "v2", "v3", "v4"]);
  assert.ok(m.rows.every((r) => r.length === 4 && r[3] === ""));
});

test("addCol 列名取最小的不重复 v<N>（删除后可复用）", () => {
  let m = TT.create(3, 1); // v1 v2 v3
  m = TT.removeCol(m, 1); // v1 v3
  m = TT.addCol(m);
  assert.deepEqual(m.cols, ["v1", "v3", "v2"]);
});

test("addCol 在 index 处插入", () => {
  const m = TT.addCol(TT.create(2, 2), 0);
  assert.deepEqual(m.cols, ["v3", "v1", "v2"]);
  assert.deepEqual(m.rows[0], ["", "", ""]);
});

test("addCol index 越界时追加到末尾", () => {
  const m = TT.addCol(TT.create(2, 1), 99);
  assert.deepEqual(m.cols, ["v1", "v2", "v3"]);
});

test("addCol 已达 20 列上限时原样返回", () => {
  const m = TT.create(20, 2);
  assert.equal(TT.addCol(m), m);
});

test("addCol 不修改原模型", () => {
  const m = TT.create(2, 2);
  const snap = deepCopy(m);
  TT.addCol(m, 0);
  assert.deepEqual(m, snap);
});

// ---------- removeCol ----------

test("removeCol 删除指定列及其所有单元格", () => {
  let m = TT.create(3, 2);
  m = TT.setCell(m, 0, 1, "x");
  m = TT.removeCol(m, 1);
  assert.deepEqual(m.cols, ["v1", "v3"]);
  assert.ok(m.rows.every((r) => r.length === 2));
});

test("removeCol 越界时原样返回", () => {
  const m = TT.create(2, 2);
  assert.equal(TT.removeCol(m, -1), m);
  assert.equal(TT.removeCol(m, 2), m);
});

test("removeCol 至少保留 1 列", () => {
  const m = TT.create(1, 2);
  assert.equal(TT.removeCol(m, 0), m);
});

// ---------- setCell / setHeader ----------

test("setCell 设置单元格文本", () => {
  const m = TT.setCell(TT.create(2, 2), 1, 0, "hello");
  assert.equal(m.rows[1][0], "hello");
});

test("setCell 将换行、制表符等控制字符替换为单个普通空格", () => {
  const m = TT.setCell(TT.create(1, 1), 0, 0, "a\nb\tc\x07d\x7Fe");
  assert.equal(m.rows[0][0], "a b c d e");
});

test("setCell 连续控制字符逐个替换为空格", () => {
  const m = TT.setCell(TT.create(1, 1), 0, 0, "a\r\nb");
  assert.equal(m.rows[0][0], "a  b");
});

test("setCell 文本截断到 60 字符", () => {
  const m = TT.setCell(TT.create(1, 1), 0, 0, "x".repeat(100));
  assert.equal(m.rows[0][0].length, 60);
});

test("setCell 越界时原样返回", () => {
  const m = TT.create(2, 2);
  assert.equal(TT.setCell(m, -1, 0, "a"), m);
  assert.equal(TT.setCell(m, 0, 2, "a"), m);
  assert.equal(TT.setCell(m, 2, 0, "a"), m);
});

test("setCell 不修改原模型", () => {
  const m = TT.create(2, 2);
  const snap = deepCopy(m);
  TT.setCell(m, 0, 0, "changed");
  assert.deepEqual(m, snap);
});

test("setHeader 设置表头并截断到 20 字符", () => {
  const m = TT.setHeader(TT.create(2, 1), 1, "h".repeat(30));
  assert.equal(m.cols[1].length, 20);
});

test("setHeader 清理控制字符", () => {
  const m = TT.setHeader(TT.create(1, 1), 0, "a\tb\nc");
  assert.equal(m.cols[0], "a b c");
});

test("setHeader 越界时原样返回", () => {
  const m = TT.create(2, 1);
  assert.equal(TT.setHeader(m, 5, "x"), m);
  assert.equal(TT.setHeader(m, -1, "x"), m);
});

// ---------- pasteGrid ----------

test("pasteGrid 基本粘贴 2x2", () => {
  const { model, truncated } = TT.pasteGrid(TT.create(2, 2), 0, 0, "a\tb\nc\td");
  assert.equal(truncated, false);
  assert.deepEqual(model.rows, [["a", "b"], ["c", "d"]]);
});

test("pasteGrid 在偏移位置粘贴，不触碰其它单元格", () => {
  let m = TT.create(3, 3);
  m = TT.setCell(m, 0, 0, "keep");
  const { model } = TT.pasteGrid(m, 1, 1, "a\tb\nc\td");
  assert.equal(model.rows[0][0], "keep");
  assert.equal(model.rows[1][1], "a");
  assert.equal(model.rows[1][2], "b");
  assert.equal(model.rows[2][1], "c");
  assert.equal(model.rows[2][2], "d");
});

test("pasteGrid 超出当前行列时自动扩展（含新列名）", () => {
  const { model, truncated } = TT.pasteGrid(TT.create(2, 2), 1, 1, "a\tb\nc\td");
  assert.equal(truncated, false);
  assert.equal(model.rows.length, 3);
  assert.deepEqual(model.cols, ["v1", "v2", "v3"]);
  assert.equal(model.rows[2][2], "d");
});

test("pasteGrid 扩展受上限约束，超出部分丢弃并置 truncated", () => {
  const text = Array.from({ length: 5 }, () => "p\tp\tp\tp\tp").join("\n");
  const { model, truncated } = TT.pasteGrid(TT.create(2, 2), 58, 18, text);
  assert.equal(truncated, true);
  assert.equal(model.rows.length, 60);
  assert.equal(model.cols.length, 20);
  assert.equal(model.rows[58][18], "p");
  assert.equal(model.rows[59][19], "p");
});

test("pasteGrid 起点超出上限时模型不变且 truncated 为 true", () => {
  const m = TT.create(2, 2);
  assert.deepEqual(TT.pasteGrid(m, 60, 0, "a"), { model: m, truncated: true });
  assert.deepEqual(TT.pasteGrid(m, 0, 20, "a"), { model: m, truncated: true });
});

test("pasteGrid 支持 CRLF 与 CR 行分隔", () => {
  const lf = TT.pasteGrid(TT.create(2, 2), 0, 0, "a\tb\nc\td").model;
  const crlf = TT.pasteGrid(TT.create(2, 2), 0, 0, "a\tb\r\nc\td").model;
  const cr = TT.pasteGrid(TT.create(2, 2), 0, 0, "a\tb\rc\td").model;
  assert.deepEqual(crlf, lf);
  assert.deepEqual(cr, lf);
});

test("pasteGrid 忽略文本末尾换行产生的空尾行", () => {
  const { model, truncated } = TT.pasteGrid(TT.create(2, 2), 0, 0, "a\tb\n");
  assert.equal(truncated, false);
  assert.equal(model.rows.length, 2);
  assert.deepEqual(model.rows[0], ["a", "b"]);
  assert.deepEqual(model.rows[1], ["", ""]);
});

test("pasteGrid 空文本或非字符串时原样返回", () => {
  const m = TT.create(2, 2);
  assert.deepEqual(TT.pasteGrid(m, 0, 0, ""), { model: m, truncated: false });
  assert.deepEqual(TT.pasteGrid(m, 0, 0, null), { model: m, truncated: false });
});

test("pasteGrid 起点为负数或非法时原样返回", () => {
  const m = TT.create(2, 2);
  assert.deepEqual(TT.pasteGrid(m, -1, 0, "a"), { model: m, truncated: false });
  assert.deepEqual(TT.pasteGrid(m, 0, 0.5, "a"), { model: m, truncated: false });
});

test("pasteGrid 不修改原模型", () => {
  const m = TT.create(2, 2);
  const snap = deepCopy(m);
  TT.pasteGrid(m, 0, 0, "a\tb\nc\td");
  assert.deepEqual(m, snap);
});

// ---------- validate ----------

test("validate(null) 返回 null", () => {
  assert.equal(TT.validate(null), null);
});

test("validate 数组、字符串、数字返回 null", () => {
  assert.equal(TT.validate([1, 2]), null);
  assert.equal(TT.validate("x"), null);
  assert.equal(TT.validate(42), null);
});

test("validate 缺少 cols 或 rows 字段返回 null", () => {
  assert.equal(TT.validate({}), null);
  assert.equal(TT.validate({ cols: ["a"] }), null);
  assert.equal(TT.validate({ rows: [[""]] }), null);
});

test("validate cols/rows 类型不对返回 null", () => {
  assert.equal(TT.validate({ cols: "v1", rows: [[""]] }), null);
  assert.equal(TT.validate({ cols: ["v1"], rows: "x" }), null);
});

test("validate 元素类型不对返回 null（非字符串列名/非数组行/非字符串单元格）", () => {
  assert.equal(TT.validate({ cols: [1], rows: [[""]] }), null);
  assert.equal(TT.validate({ cols: ["a"], rows: ["x"] }), null);
  assert.equal(TT.validate({ cols: ["a"], rows: [[5]] }), null);
});

test("validate 空 cols 或空 rows 返回 null", () => {
  assert.equal(TT.validate({ cols: [], rows: [] }), null);
  assert.equal(TT.validate({ cols: ["a"], rows: [] }), null);
});

test("validate 补齐长度不足的行", () => {
  const m = TT.validate({ cols: ["a", "b"], rows: [["1"]] });
  assert.deepEqual(m.rows, [["1", ""]]);
});

test("validate 截断长度超出列数的行", () => {
  const m = TT.validate({ cols: ["a"], rows: [["1", "2", "3"]] });
  assert.deepEqual(m.rows, [["1"]]);
});

test("validate 丢弃超出上限的行列", () => {
  const cols = Array.from({ length: 25 }, (_, i) => "c" + i);
  const rows = Array.from({ length: 70 }, () => Array.from({ length: 25 }, () => "x"));
  const m = TT.validate({ cols, rows });
  assert.equal(m.cols.length, 20);
  assert.equal(m.rows.length, 60);
  assert.ok(m.rows.every((r) => r.length === 20));
});

test("validate 截断过长的表头与单元格文本并清理控制字符", () => {
  const m = TT.validate({ cols: ["h".repeat(30)], rows: [["x".repeat(59) + "\n" + "y".repeat(10)]] });
  assert.equal(m.cols[0].length, 20);
  // 换行被替换为单个空格，再整体截断到 60 字符
  assert.equal(m.rows[0][0], "x".repeat(59) + " ");
});

test("validate 序列化超过 20000 字符返回 null", () => {
  const cols = Array.from({ length: 20 }, (_, i) => "v" + i);
  const rows = Array.from({ length: 60 }, () => Array.from({ length: 20 }, () => "x".repeat(60)));
  const value = { cols, rows };
  assert.ok(JSON.stringify(value).length > 20000);
  assert.equal(TT.validate(value), null);
});

test("validate 返回规范化的新对象且不修改输入", () => {
  const input = { cols: ["a"], rows: [["1", "extra"]] };
  const snap = deepCopy(input);
  const m = TT.validate(input);
  assert.notEqual(m, input);
  assert.deepEqual(input, snap);
  assert.deepEqual(m, { cols: ["a"], rows: [["1"]] });
});

// ---------- toJSON 往返 ----------

test("toJSON 与 validate 往返一致", () => {
  let m = TT.create(4, 5);
  m = TT.setHeader(m, 0, "名称");
  m = TT.setCell(m, 2, 3, "值");
  m = TT.pasteGrid(m, 4, 2, "p\tq").model;
  const restored = TT.validate(JSON.parse(TT.toJSON(m)));
  assert.deepEqual(restored, m);
});

test("toJSON 返回字符串", () => {
  const s = TT.toJSON(TT.create());
  assert.equal(typeof s, "string");
  assert.doesNotThrow(() => JSON.parse(s));
});

// ---------- 固定种子随机测试 ----------

test("固定种子随机 200 组操作后模型始终满足不变量", () => {
  function makeRng(seed) {
    let s = seed >>> 0;
    return () => {
      s = (s * 1664525 + 1013904223) >>> 0;
      return s / 4294967296;
    };
  }
  const rand = makeRng(123456789);
  const pick = (n) => Math.floor(rand() * n);

  for (let g = 0; g < 200; g++) {
    let model = TT.create(1 + pick(20), 1 + pick(60));
    const ops = 5 + pick(15);
    for (let i = 0; i < ops; i++) {
      const r = pick(model.rows.length + 2) - 1;
      const c = pick(model.cols.length + 2) - 1;
      switch (pick(8)) {
        case 0:
          model = TT.addRow(model, r);
          break;
        case 1:
          model = TT.removeRow(model, r);
          break;
        case 2:
          model = TT.addCol(model, c);
          break;
        case 3:
          model = TT.removeCol(model, c);
          break;
        case 4:
          model = TT.setCell(model, r, c, "x".repeat(pick(80)) + "\n\t");
          break;
        case 5:
          model = TT.setHeader(model, c, "h".repeat(pick(30)));
          break;
        case 6: {
          const lines = [];
          const nr = pick(4);
          for (let a = 0; a < nr; a++) {
            const cells = [];
            const nc = pick(4);
            for (let b = 0; b < nc; b++) cells.push("p" + pick(100));
            lines.push(cells.join("\t"));
          }
          model = TT.pasteGrid(model, Math.max(0, r), Math.max(0, c), lines.join("\r\n")).model;
          break;
        }
        case 7:
          model = TT.validate(JSON.parse(TT.toJSON(model))) || model;
          break;
      }
    }
    assert.ok(model.cols.length >= 1 && model.cols.length <= 20, "列数在 1..20");
    assert.ok(model.rows.length >= 1 && model.rows.length <= 60, "行数在 1..60");
    for (const h of model.cols) assert.ok(h.length <= 20, "表头不超过 20 字符");
    for (const row of model.rows) {
      assert.equal(row.length, model.cols.length, "行长度等于列数");
      for (const cell of row) assert.ok(cell.length <= 60, "单元格不超过 60 字符");
    }
  }
});
