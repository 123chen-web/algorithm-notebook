"use strict";

/* 复仇成功卡：假浏览器里验证挂载渲染、展开列表、月份计数。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick } = require("./js_harness.cjs");

const revengeSource = fs.readFileSync(
  path.join(__dirname, "..", "static", "app", "overview-revenge.js"), "utf8"
);

function setup(items) {
  const env = load([]);
  const { document, context } = env;
  vm.runInContext(revengeSource, context);
  const run = (code) => vm.runInContext(code, context);
  run(`globalThis.__testItems = ${JSON.stringify(items)}`);
  run(`window.RevengeCard.configure({
    api: async () => ({ items: globalThis.__testItems, count: globalThis.__testItems.length }),
    getUser: () => ({ id: 1 }),
    getEpoch: () => 1,
  })`);
  const host = document.createElement("div");
  document.body.append(host);
  return { document, context, host, run };
}

test("revenge card: 本月计数 + 展开列出", async () => {
  const now = new Date();
  const prefix = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const items = [
    { mistake_id: 1, revenge_at: `${prefix}-05T10:00:00`, problem_title: "两数之和",
      first_fail_date: `${prefix}-01`, verdict: "WA", verdict_label: "答案错误",
      mode: "rewrite", seconds: 300 },
    { mistake_id: 2, revenge_at: "2020-01-01T10:00:00", problem_title: "旧题",
      first_fail_date: "2019-12-20", verdict: "TLE", verdict_label: "超时",
      mode: "redo", seconds: null },
  ];
  const { host, run } = setup(items);
  run("window.RevengeCard.mount(document.body.firstChild)");
  await tick(20);
  assert.match(host.textContent, /本月复仇成功/);
  assert.match(host.textContent, /1 次/);
  const head = host.querySelector(".revenge-card-head");
  assert.ok(head, "has head button");
  head.click();
  await tick(20);
  assert.match(host.textContent, /两数之和/);
  assert.match(host.textContent, /答案错误/);
  assert.ok(host.querySelector(".revenge-share"), "has share button");
});

test("revenge helpers: monthCount 只算本月", async () => {
  const { run: run2 } = setup([]);
  const count = run2(`(() => {
    const now = new Date();
    const prefix = now.getFullYear() + "-" + String(now.getMonth() + 1).padStart(2, "0");
    return window.RevengeCard.helpers.monthCount([
      { revenge_at: prefix + "-05T10:00:00" },
      { revenge_at: prefix + "-06T10:00:00" },
      { revenge_at: "2020-01-01T00:00:00" },
    ]);
  })()`);
  assert.equal(count, 2);
});

test("revenge helpers: modeLabel 映射", async () => {
  const { run: run3 } = setup([]);
  const labels = run3(`[
    window.RevengeCard.helpers.modeLabel("rewrite"),
    window.RevengeCard.helpers.modeLabel("redo"),
    window.RevengeCard.helpers.modeLabel("recall"),
  ]`);
  assert.deepEqual([...labels], ["默写", "限时重做", "只回忆"]);
});
