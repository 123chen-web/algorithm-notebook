"use strict";

/* 画板“算法图形”（static/draw-shapes.js）：每个图形都生成合法的 Excalidraw 元素，插进任何场景都不留悬空引用。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

const plain = (value) => JSON.parse(JSON.stringify(value));
const REQUIRED = ["id", "type", "x", "y", "width", "height", "angle", "strokeColor", "backgroundColor", "fillStyle",
  "strokeWidth", "strokeStyle", "roughness", "opacity", "groupIds", "frameId", "roundness", "seed", "version",
  "versionNonce", "isDeleted", "boundElements", "updated", "link", "locked"];

function shapes() {
  return load(["draw-shapes.js"]).window.DrawShapes;
}

test("所有图形都能生成，元素字段齐全、数值有限、整组共用一个 groupId", () => {
  const api = shapes();
  assert.equal(api.SHAPES.length, 11);
  for (const { kind } of api.SHAPES) {
    const elements = plain(api.build(kind, { x: 300, y: 200 }));
    assert.ok(elements.length >= 3, kind);
    const ids = new Set(elements.map((el) => el.id));
    assert.equal(ids.size, elements.length, `${kind} 的 id 不重复`);
    const groups = new Set(elements.map((el) => el.groupIds.join(",")));
    assert.equal(groups.size, 1, `${kind} 整体一组`);
    for (const el of elements) {
      for (const key of REQUIRED) assert.ok(key in el, `${kind} ${el.type} 缺 ${key}`);
      for (const key of ["x", "y", "width", "height"]) assert.ok(Number.isFinite(el[key]), `${kind} ${key}`);
      assert.ok(["rectangle", "ellipse", "text", "arrow"].includes(el.type));
      if (el.type === "text") {
        assert.equal(el.containerId, null);
        assert.equal(el.text, el.originalText);
        assert.ok(el.text.length > 0 && el.fontSize > 0 && el.lineHeight > 0);
      }
      if (el.type === "arrow") {
        assert.equal(el.startBinding, null);
        assert.equal(el.endBinding, null);
        assert.deepEqual(el.points[0], [0, 0]);
        assert.equal(el.points.length, 2);
      }
      assert.ok(el.x >= 200 && el.y >= 60, `${kind} 画在原点附近（${el.x}, ${el.y}）`);
    }
  }
});

test("每次生成都是新的 id；不认识的图形返回空数组", () => {
  const api = shapes();
  const a = plain(api.build("array")).map((el) => el.id);
  const b = plain(api.build("array")).map((el) => el.id);
  assert.equal(a.filter((id) => b.includes(id)).length, 0);
  assert.deepEqual(plain(api.build("nope")), []);
});

test("originBeside：空画板放在 (100,100)，有内容时放在最右边再往右 80、跳过已删除的", () => {
  const api = shapes();
  assert.deepEqual(plain(api.originBeside([])), { x: 100, y: 100 });
  const origin = plain(api.originBeside([
    { x: 0, y: 50, width: 200, isDeleted: false },
    { x: 1000, y: 0, width: 500, isDeleted: true },
    { x: 150, y: 10, width: 300 },
  ]));
  assert.deepEqual(origin, { x: 530, y: 50 });
});
