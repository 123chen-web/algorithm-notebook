"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

test("groups by problem id, keeps independent ids, and never merges equal titles", () => {
  const { window } = load(["problem-cards.js"]);
  const groups = window.ProblemCards.group([
    { id: 1, problem_id: 8, title: "同名", tags: ["边界", "SQL"] },
    { id: 2, problem_id: 9, title: "同名" },
    { id: 3, problem_id: 8, tags: ["边界", "sql", "复杂度"] },
  ]);
  assert.deepEqual(Array.from(groups, g => Array.from(g.members, m => m.id)), [[1, 3], [2]]);
  assert.deepEqual(Array.from(groups[0].tags), ["边界", "SQL", "复杂度"]);
});

test("uses whole-problem server metadata even when list has one filtered mistake", () => {
  const { window } = load(["problem-cards.js"]);
  const group = window.ProblemCards.group([{ id: 1, problem_id: 8, progress: 60,
    problem_tags: ["一", "二"], tags: ["一"], problem_due_count: 3 }])[0];
  assert.equal(group.progress, 60);
  assert.equal(group.dueCount, 3);
  assert.deepEqual(group.tags, ["一", "二"]);
});

test("five visible chips, expandable +N is a sibling button, accessible progress", () => {
  const { window } = load(["problem-cards.js"]);
  const group = window.ProblemCards.group([{ id: 1, problem_id: 8, title: "题目", zone: "算法",
    progress: 50, problem_tags: Array.from({length: 8}, (_, i) => `标签${i}`) }])[0];
  let opens = 0;
  const card = window.ProblemCards.card(group, { onOpen: () => opens++ });
  assert.equal(card.dataset.progress, "50");
  assert.equal(card.querySelectorAll(".problem-chip").filter(c => !c.hidden).length, 5);
  const more = card.querySelector(".problem-tags-more");
  assert.equal(more.textContent, "+3");
  more.click();
  assert.equal(more.getAttribute("aria-expanded"), "true");
  assert.equal(card.querySelectorAll(".problem-chip").filter(c => !c.hidden).length, 8);
  assert.equal(opens, 0);
  card.querySelector(".record-button").click();
  assert.equal(opens, 1);
  assert.match(card.querySelector(".problem-progress-label").getAttribute("aria-label"), /复习完成度 50%/);
  assert.equal(card.querySelector(".problem-progress-label").getAttribute("role"), "img");
});

test("accepts only server progress buckets; missing or invalid metadata stays unknown", () => {
  const { window } = load(["problem-cards.js"]);
  for (let p = 0; p <= 100; p += 5) assert.equal(window.ProblemCards.progress(p), p);
  for (const p of [undefined, null, -5, 101, 53, "50"]) assert.equal(window.ProblemCards.progress(p), null);
});

test("question position uses the original queue, without reordering or mutating it", () => {
  const { window } = load(["problem-cards.js"]);
  const queue = [{id: 1, problem_id: 8}, {id: 2, problem_id: 9}, {id: 3, problem_id: 8}];
  assert.equal(window.ProblemCards.position(queue[2], queue), "这道题 第 2/2 条");
  assert.deepEqual(queue.map(i => i.id), [1, 2, 3]);
});
