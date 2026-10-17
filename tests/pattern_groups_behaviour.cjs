"use strict";

/* 学习路线的大类：28 个细分类收成 13 个左右的大类；模板代码默认收起。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const { load } = require("./js_harness.cjs");

const PATTERNS = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "static", "data", "patterns.json"), "utf8"));

test("每个细分类都归进了登记过的大类，大类不超过 13 个", () => {
  const R = load(["pattern-render.js"]).window.PatternRender;
  const known = new Set(R.GROUPS.map(([group]) => group));
  for (const pattern of PATTERNS) {
    assert.ok(known.has(R.groupOf(pattern.category)), `${pattern.category} 没有归到大类`);
  }
  const groups = [...R.groupsOf(PATTERNS)];
  assert.ok(groups.length <= 13 && groups.length >= 10, String(groups.length));
  assert.ok(groups.includes("图") && !groups.includes("最短路"), "图论 / 图遍历 / 最短路 并成“图”");
});

test("按大类筛选能筛到它下面的细分类，按细分类筛也照样可以", () => {
  const R = load(["pattern-render.js"]).window.PatternRender;
  const shortest = PATTERNS.find((pattern) => pattern.category === "最短路");
  assert.equal(R.matches(shortest, { category: "图" }), true);
  assert.equal(R.matches(shortest, { category: "最短路" }), true);
  assert.equal(R.matches(shortest, { category: "树" }), false);
});

test("模板代码默认收起在 details 里，卡片上显示“大类 · 细分类”", () => {
  const env = load(["pattern-render.js"]);
  const R = env.window.PatternRender;
  const shortest = PATTERNS.find((pattern) => pattern.category === "最短路");
  const card = R.renderCard(shortest, {});
  const fold = card.querySelector(".pattern-template-fold");
  assert.ok(fold, "模板在折叠里");
  assert.equal(Boolean(fold.open), false);
  assert.match(card.querySelector(".pattern-chip").textContent, /^图 · 最短路$/);
});


test("套路卡：有错因统计时写“你在这个套路上常错在：…”，点“看这些题”走错题入口；没有就不出现", () => {
  const env = load(["pattern-render.js"]);
  const R = env.window.PatternRender;
  const pattern = PATTERNS.find((entry) => entry.id === "binary-search-basic");
  const clicked = [];
  const card = R.renderCard(pattern, {
    causes: [{ tag: "boundary", count: 3 }, { tag: "misread", count: 1 }],
    causeLabel: (code) => ({ boundary: "边界/特例", misread: "审题/漏条件" })[code],
    onMistakeClick: (target) => clicked.push(target.id),
  });
  const line = card.querySelector(".pattern-causes");
  assert.match(line.textContent, /你在这个套路上常错在：边界\/特例 ×3、审题\/漏条件 ×1/);
  line.querySelector(".pattern-causes-open").click();
  assert.deepEqual(clicked, ["binary-search-basic"]);
  assert.equal(R.renderCard(pattern, {}).querySelector(".pattern-causes"), null, "套路库静态页没有统计：不出现");
});
