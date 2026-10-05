"use strict";

/* 草稿演算区（static/scratch.js）的行为测试：假浏览器 + 可控的假 api / 假 TraceTable。
   覆盖：挂载卸载、三个标签的切换与键盘、行号与上限、Tab/Esc、对比视图、
   演算表编辑增删粘贴上限、自动保存全部状态转换、保存中合并、失败重试、
   409 两种处理、切换标签 / 页面隐藏立即保存、大小上限、迟到响应守卫、reset、XSS。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/* ---------------- 假 TraceTable：纯函数，不修改入参，上限 20 列 × 60 行 ---------------- */
function makeTrace() {
  const clone = (model) => ({ cols: model.cols.slice(), rows: model.rows.map((row) => row.slice()) });
  const validate = (value) => {
    if (!value || !Array.isArray(value.cols) || !Array.isArray(value.rows)) return null;
    if (value.cols.length < 1 || value.cols.length > 20) return null;
    if (value.rows.length < 1 || value.rows.length > 60) return null;
    if (!value.rows.every((row) => Array.isArray(row))) return null;
    const cols = value.cols.map(String);
    return { cols, rows: value.rows.map((row) => cols.map((_h, c) => String(row[c] ?? ""))) };
  };
  return {
    create: (cols, rows) => validate({ cols, rows }),
    validate,
    toJSON: (model) => JSON.stringify(model),
    setCell(model, r, c, text) { const next = clone(model); next.rows[r][c] = String(text); return next; },
    setHeader(model, c, text) { const next = clone(model); next.cols[c] = String(text); return next; },
    addRow(model, index) {
      const next = clone(model);
      if (next.rows.length >= 60) return next;
      next.rows.splice(index ?? next.rows.length, 0, next.cols.map(() => ""));
      return next;
    },
    removeRow(model, index) {
      const next = clone(model);
      if (next.rows.length <= 1) return next;
      next.rows.splice(index, 1);
      return next;
    },
    addCol(model, index) {
      const next = clone(model);
      if (next.cols.length >= 20) return next;
      const at = index ?? next.cols.length;
      next.cols.splice(at, 0, "");
      for (const row of next.rows) row.splice(at, 0, "");
      return next;
    },
    removeCol(model, index) {
      const next = clone(model);
      if (next.cols.length <= 1) return next;
      next.cols.splice(index, 1);
      for (const row of next.rows) row.splice(index, 1);
      return next;
    },
    pasteGrid(model, r, c, text) {
      const next = clone(model);
      const grid = String(text).replace(/\r\n?/g, "\n").split("\n").map((line) => line.split("\t"));
      let truncated = false;
      const needRows = r + grid.length;
      const needCols = c + Math.max(...grid.map((line) => line.length));
      while (next.rows.length < Math.min(needRows, 60)) next.rows.push(next.cols.map(() => ""));
      while (next.cols.length < Math.min(needCols, 20)) {
        next.cols.push("");
        for (const row of next.rows) row.push("");
      }
      grid.forEach((line, dr) => {
        line.forEach((cell, dc) => {
          if (r + dr >= 60 || c + dc >= 20) { truncated = true; return; }
          next.rows[r + dr][c + dc] = cell;
        });
      });
      if (needRows > 60 || needCols > 20) truncated = true;
      return { model: next, truncated };
    },
  };
}

/* ---------------- 假 api：记录每次调用，由测试决定何时、怎样回应 ---------------- */
function makeApi() {
  const calls = [];
  const api = (url, options = {}) => {
    const call = { url: String(url), options, ...deferred() };
    calls.push(call);
    return call.promise;
  };
  return { api, calls };
}
const ok = (call, body) => call.resolve(body);
const fail = (call, status, message, extra = {}) =>
  call.reject(Object.assign(new Error(message || `HTTP ${status}`), { status, ...extra }));

const EMPTY = { version: 0, code: "", fixed: "", table: null, updated_at: null };

function setup({ trace = true } = {}) {
  const env = load(["diff-lines.js", "scratch.js"], trace ? { extra: { TraceTable: makeTrace() } } : {});
  const { api, calls } = makeApi();
  const session = { user: { id: "u1" }, epoch: 1 };
  const Scratch = env.window.Scratch;
  Scratch.debounceMs = 5;
  Scratch.configure({ api, getUser: () => session.user, getEpoch: () => session.epoch });
  const container = env.document.createElement("div");
  env.document.body.append(container);
  return { env, calls, session, container, Scratch };
}

async function mountLoaded(ctx, data = EMPTY, mistake = { id: 7 }) {
  ctx.Scratch.mount(ctx.container, mistake);
  await tick();
  assert.equal(ctx.calls.length, 1, "mount issues exactly one GET");
  assert.equal(ctx.calls[0].url, `/api/mistakes/${mistake.id}/scratch`);
  ok(ctx.calls[0], data);
  await tick();
  await tick();
}

const codeArea = (ctx) => ctx.container.querySelector("#sc-panel-code .sc-text");
const fixedArea = (ctx) => ctx.container.querySelector("#sc-panel-diff .sc-text");
const type = (area, value) => {
  area.value = value;
  area.dispatchEvent(new FakeEvent("input"));
};
const key = (node, name) => {
  const event = new FakeEvent("keydown", { bubbles: true, props: { key: name } });
  node.dispatchEvent(event);
  return event;
};
const cells = (ctx) => ctx.container.querySelectorAll(".sc-grid-cell");
const cellAt = (ctx, r, c, cols) => cells(ctx)[r * cols + c];
const flushPut = async (ctx) => { await sleep(30); await tick(); };

/* ---------------- 挂载与卸载 ---------------- */
test("mount: builds a tablist with three tabs and matching panels", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const tablist = ctx.container.querySelector('[role="tablist"]');
  assert.ok(tablist);
  const tabs = ctx.container.querySelectorAll('[role="tab"]');
  assert.equal(tabs.length, 3);
  assert.deepEqual(tabs.map((tab) => tab.textContent), ["代码草稿", "对比", "演算表"]);
  const panels = ctx.container.querySelectorAll('[role="tabpanel"]');
  assert.equal(panels.length, 3);
  assert.equal(tabs[0].getAttribute("aria-selected"), "true");
  assert.equal(tabs[1].getAttribute("aria-selected"), "false");
  assert.equal(panels[1].hidden, true);
  assert.equal(panels[0].hidden, false);
});

test("mount: fills editors and table from the saved draft", async () => {
  const ctx = setup();
  await mountLoaded(ctx, {
    version: 3, code: "line1\nline2", fixed: "line1\nline3",
    table: { cols: ["步骤"], rows: [["a"], ["b"]] }, updated_at: "2026-10-01 10:00",
  });
  assert.equal(codeArea(ctx).value, "line1\nline2");
  assert.equal(fixedArea(ctx).value, "line1\nline3");
  assert.equal(ctx.container.querySelector(".sc-status").textContent.includes("已保存 2026-10-01 10:00"), true);
  assert.equal(cells(ctx).length, 2, "one column, two rows");
  assert.equal(cells(ctx)[1].value, "b");
});

test("mount: a never-saved draft starts empty and idle", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  assert.equal(codeArea(ctx).value, "");
  assert.equal(fixedArea(ctx).value, "");
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "idle");
  assert.equal(cells(ctx).length, 3, "default table: 3 columns x 1 row");
});

test("unmount: removes the panel; a second mount loads again", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  ctx.Scratch.unmount();
  assert.equal(ctx.container.childElementCount, 0);
  ctx.Scratch.mount(ctx.container, { id: 7 });
  await tick();
  assert.equal(ctx.calls.length, 2, "remount issues a fresh GET");
  ok(ctx.calls[1], EMPTY);
  await tick();
  assert.ok(ctx.container.querySelector(".sc-panel"));
});

test("unmount: with unsaved edits still sends the pending save", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "unsaved work");
  ctx.Scratch.unmount();
  assert.equal(ctx.calls.length, 2, "PUT goes out even though the panel is gone");
  assert.equal(ctx.calls[1].options.method, "PUT");
  assert.equal(JSON.parse(ctx.calls[1].options.body).code, "unsaved work");
  ok(ctx.calls[1], { version: 1, updated_at: "t" }); // 迟到响应被丢弃，不报错
  await tick();
});

/* ---------------- 标签切换与键盘 ---------------- */
test("tabs: clicking switches the visible panel", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const tabs = ctx.container.querySelectorAll('[role="tab"]');
  tabs[1].click();
  assert.equal(tabs[1].getAttribute("aria-selected"), "true");
  assert.equal(ctx.container.querySelector("#sc-panel-diff").hidden, false);
  assert.equal(ctx.container.querySelector("#sc-panel-code").hidden, true);
});

test("tabs: arrow keys, Home and End move selection and focus", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const tabs = ctx.container.querySelectorAll('[role="tab"]');
  key(tabs[0], "ArrowRight");
  assert.equal(tabs[1].getAttribute("aria-selected"), "true");
  assert.equal(ctx.env.document.activeElement, tabs[1]);
  key(tabs[1], "ArrowRight");
  assert.equal(tabs[2].getAttribute("aria-selected"), "true");
  key(tabs[2], "ArrowRight"); // 环绕回第一个
  assert.equal(tabs[0].getAttribute("aria-selected"), "true");
  key(tabs[0], "ArrowLeft"); // 反向环绕到最后一个
  assert.equal(tabs[2].getAttribute("aria-selected"), "true");
  key(tabs[2], "Home");
  assert.equal(tabs[0].getAttribute("aria-selected"), "true");
  key(tabs[0], "End");
  assert.equal(tabs[2].getAttribute("aria-selected"), "true");
});

test("tabs: switching tabs saves pending edits immediately", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "draft");
  const tabs = ctx.container.querySelectorAll('[role="tab"]');
  tabs[1].click(); // 不等防抖
  assert.equal(ctx.calls.length, 2);
  assert.equal(ctx.calls[1].options.method, "PUT");
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
});

/* ---------------- 代码草稿：行号、上限、Tab/Esc ---------------- */
test("editor: gutter line numbers track the content", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const gutter = ctx.container.querySelector("#sc-panel-code .sc-gutter");
  assert.equal(gutter.textContent, "1");
  type(codeArea(ctx), "a\nb\nc");
  assert.equal(gutter.textContent, "1\n2\n3");
  type(codeArea(ctx), "a");
  assert.equal(gutter.textContent, "1");
});

test("editor: gutter scrolls with the textarea", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const area = codeArea(ctx);
  const gutter = ctx.container.querySelector("#sc-panel-code .sc-gutter");
  area.scrollTop = 42;
  area.dispatchEvent(new FakeEvent("scroll"));
  assert.equal(gutter.scrollTop, 42);
});

test("editor: Tab inserts four spaces and is prevented", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const area = codeArea(ctx);
  const event = key(area, "Tab");
  assert.equal(event.defaultPrevented, true);
  assert.equal(area.value, "    ");
});

test("editor: Esc frees Tab to leave the field, with a visible hint; Esc again restores", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const area = codeArea(ctx);
  const hint = ctx.container.querySelector("#sc-panel-code .sc-note:not(.is-error)");
  key(area, "Escape");
  assert.equal(hint.hidden, false, "hint becomes visible after Esc");
  assert.match(hint.textContent, /Tab 移出编辑框/);
  const tabEvent = key(area, "Tab");
  assert.equal(tabEvent.defaultPrevented, false, "Tab keeps its default behaviour (leave the field)");
  assert.equal(area.value, "");
  key(area, "Escape");
  assert.equal(hint.hidden, true);
  const again = key(area, "Tab");
  assert.equal(again.defaultPrevented, true, "Tab indents again");
});

test("editor: blur resets the Esc mode", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const area = codeArea(ctx);
  key(area, "Escape");
  area.dispatchEvent(new FakeEvent("blur"));
  const tabEvent = key(area, "Tab");
  assert.equal(tabEvent.defaultPrevented, true);
});

test("editor: more than 2000 lines is rejected with a hint; exactly 2000 is fine", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const area = codeArea(ctx);
  const note = ctx.container.querySelector("#sc-panel-code .sc-note.is-error");
  type(area, Array(2001).fill("x").join("\n"));
  assert.equal(area.value, "", "the over-limit input is rolled back");
  assert.equal(note.hidden, false);
  assert.match(note.textContent, /2000/);
  type(area, Array(2000).fill("x").join("\n"));
  assert.equal(note.hidden, true);
  assert.equal(area.value.split("\n").length, 2000);
  assert.equal(ctx.container.querySelector("#sc-panel-code .sc-gutter").textContent.split("\n").length, 2000);
});

/* ---------------- 对比标签 ---------------- */
test("diff: rows carry old/new line numbers, +/−/space signs and text", async () => {
  const ctx = setup();
  await mountLoaded(ctx, { version: 1, code: "a\nb\nc", fixed: "a\nx\nc", table: null, updated_at: "t" });
  const rows = ctx.container.querySelectorAll(".sc-diff-row");
  assert.equal(rows.length, 4);
  assert.equal(rows.filter((row) => row.classList.contains("is-same")).length, 2);
  const removed = rows.find((row) => row.classList.contains("is-removed"));
  const added = rows.find((row) => row.classList.contains("is-added"));
  assert.ok(removed && added);
  assert.equal(removed.querySelector(".sc-diff-sign").textContent, "−");
  assert.equal(added.querySelector(".sc-diff-sign").textContent, "+");
  assert.equal(removed.querySelector(".sc-diff-old").textContent, "2");
  assert.equal(removed.querySelector(".sc-diff-new").textContent, "");
  assert.equal(added.querySelector(".sc-diff-old").textContent, "");
  assert.equal(added.querySelector(".sc-diff-new").textContent, "2");
  assert.equal(removed.querySelector(".sc-diff-text").textContent, "b");
  assert.equal(added.querySelector(".sc-diff-text").textContent, "x");
});

test("diff: summary shows the +N −M counts", async () => {
  const ctx = setup();
  await mountLoaded(ctx, { version: 1, code: "a\nb\nc", fixed: "a\nx\nc\nd", table: null, updated_at: "t" });
  assert.equal(ctx.container.querySelector(".sc-diff-summary").textContent, "+2 −1");
});

test("diff: editing the fixed code refreshes the diff", async () => {
  const ctx = setup();
  await mountLoaded(ctx, { version: 1, code: "a", fixed: "a", table: null, updated_at: "t" });
  assert.equal(ctx.container.querySelector(".sc-diff-summary").textContent, "+0 −0");
  type(fixedArea(ctx), "a\nb");
  assert.equal(ctx.container.querySelector(".sc-diff-summary").textContent, "+1 −0");
});

test("diff: editing the scratch code refreshes the diff too", async () => {
  const ctx = setup();
  await mountLoaded(ctx, { version: 1, code: "a", fixed: "a", table: null, updated_at: "t" });
  type(codeArea(ctx), "a\nold");
  assert.equal(ctx.container.querySelector(".sc-diff-summary").textContent, "+0 −1");
});

test("diff: too-large input shows the tooLarge notice instead of rows", async () => {
  const ctx = setup();
  await mountLoaded(ctx, {
    version: 1, code: Array(2001).fill("x").join("\n"), fixed: "", table: null, updated_at: "t",
  });
  const notes = ctx.container.querySelectorAll("#sc-panel-diff .sc-note");
  const tooLarge = [...notes].find((note) => note.textContent.includes("暂时没有显示差异"));
  assert.equal(tooLarge.hidden, false);
  assert.equal(ctx.container.querySelectorAll(".sc-diff-row").length, 0);
  assert.equal(ctx.container.querySelector(".sc-diff-summary").textContent, "");
});

test("diff: user text with markup stays text, never becomes elements", async () => {
  const ctx = setup();
  await mountLoaded(ctx, {
    version: 1, code: '<img src=x onerror="alert(1)">\nplain', fixed: "<b>bold</b>", table: null, updated_at: "t",
  });
  assert.equal(ctx.container.querySelectorAll("img").length, 0);
  assert.equal(ctx.container.querySelectorAll("b").length, 0);
  const texts = [...ctx.container.querySelectorAll(".sc-diff-text")].map((node) => node.textContent);
  assert.ok(texts.includes('<img src=x onerror="alert(1)">'));
  assert.ok(texts.includes("<b>bold</b>"));
});

/* ---------------- 演算表 ---------------- */
test("table: renders editable headers and labelled cells", async () => {
  const ctx = setup();
  await mountLoaded(ctx, { version: 1, code: "", fixed: "", table: { cols: ["i", "n"], rows: [["0", "1"]] }, updated_at: "t" });
  const heads = ctx.container.querySelectorAll(".sc-grid-head");
  assert.equal(heads.length, 2);
  assert.equal(heads[0].value, "i");
  assert.equal(heads[1].getAttribute("aria-label"), "第 2 列列名");
  assert.equal(cells(ctx)[0].getAttribute("aria-label"), "第 1 行第 1 列");
  assert.equal(cells(ctx)[1].value, "1");
});

test("table: editing a cell goes into the next save", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const cell = cells(ctx)[1];
  cell.value = "42";
  cell.dispatchEvent(new FakeEvent("input"));
  await flushPut(ctx);
  assert.equal(ctx.calls.length, 2);
  const body = JSON.parse(ctx.calls[1].options.body);
  assert.equal(body.table.rows[0][1], "42");
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
});

test("table: editing a header goes into the next save", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const head = ctx.container.querySelectorAll(".sc-grid-head")[0];
  head.value = "轮次";
  head.dispatchEvent(new FakeEvent("input"));
  await flushPut(ctx);
  const body = JSON.parse(ctx.calls[1].options.body);
  assert.equal(body.table.cols[0], "轮次");
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
});

test("table: arrow keys move between cells, Enter moves down", async () => {
  const ctx = setup();
  await mountLoaded(ctx, { version: 1, code: "", fixed: "", table: { cols: ["a", "b"], rows: [["", ""], ["", ""]] }, updated_at: "t" });
  const at = (r, c) => cellAt(ctx, r, c, 2);
  at(0, 0).focus();
  key(at(0, 0), "ArrowRight");
  assert.equal(ctx.env.document.activeElement, at(0, 1));
  key(at(0, 1), "Enter");
  assert.equal(ctx.env.document.activeElement, at(1, 1));
  key(at(1, 1), "ArrowLeft");
  assert.equal(ctx.env.document.activeElement, at(1, 0));
  key(at(1, 0), "ArrowUp");
  assert.equal(ctx.env.document.activeElement, at(0, 0));
  key(at(0, 0), "ArrowDown");
  assert.equal(ctx.env.document.activeElement, at(1, 0));
});

test("table: arrow keys do not leave the grid", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const first = cells(ctx)[0];
  first.focus();
  const event = key(first, "ArrowUp");
  assert.equal(event.defaultPrevented, false, "top edge: no move, no preventDefault");
  assert.equal(ctx.env.document.activeElement, first);
});

test("table: pasting a grid fills cells through pasteGrid", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const target = cellAt(ctx, 0, 1, 3);
  const event = new FakeEvent("paste", { props: { clipboardData: { getData: () => "a\tb\nc\td" } } });
  target.dispatchEvent(event);
  assert.equal(event.defaultPrevented, true);
  assert.equal(cellAt(ctx, 0, 1, 3).value, "a");
  assert.equal(cellAt(ctx, 0, 2, 3).value, "b");
  assert.equal(cellAt(ctx, 1, 1, 3).value, "c");
  assert.equal(cellAt(ctx, 1, 2, 3).value, "d");
});

test("table: a single-line paste is left to the default behaviour", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const event = new FakeEvent("paste", { props: { clipboardData: { getData: () => "word" } } });
  cells(ctx)[0].dispatchEvent(event);
  assert.equal(event.defaultPrevented, false);
});

test("table: an oversized paste is truncated with a visible note", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const big = Array.from({ length: 65 }, (_v, i) => `r${i}`).join("\n");
  const event = new FakeEvent("paste", { props: { clipboardData: { getData: () => big } } });
  cells(ctx)[0].dispatchEvent(event);
  const note = [...ctx.container.querySelectorAll("#sc-panel-table .sc-note")]
    .find((item) => item.textContent.includes("只放进了能放得下的部分"));
  assert.equal(note.hidden, false);
  assert.equal(ctx.container.querySelectorAll(".sc-grid tbody tr").length, 60);
});

test("table: add/remove row and column buttons work and carry labels", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const [addRow, removeRow, addCol, removeCol] = ctx.container.querySelectorAll(".sc-tool");
  for (const [node, label] of [[addRow, "加行"], [removeRow, "删行"], [addCol, "加列"], [removeCol, "删列"]]) {
    assert.equal(node.textContent, label);
    assert.ok(node.getAttribute("aria-label"), `${label} has an aria-label`);
  }
  addRow.click();
  assert.equal(ctx.container.querySelectorAll(".sc-grid tbody tr").length, 2);
  addCol.click();
  assert.equal(cells(ctx).length, 2 * 4);
  removeRow.click();
  assert.equal(ctx.container.querySelectorAll(".sc-grid tbody tr").length, 1);
  assert.equal(removeRow.disabled, true, "cannot remove the last row");
  removeCol.click();
  assert.equal(ctx.container.querySelectorAll(".sc-grid-head").length, 3);
});

test("table: structural changes go into the next save", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  ctx.container.querySelectorAll(".sc-tool")[0].click(); // 加行
  await flushPut(ctx);
  const body = JSON.parse(ctx.calls[1].options.body);
  assert.equal(body.table.rows.length, 2);
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
});

test("table: at 60 rows the add-row button is disabled with an explanation", async () => {
  const ctx = setup();
  const rows = Array.from({ length: 60 }, (_v, i) => [String(i)]);
  await mountLoaded(ctx, { version: 1, code: "", fixed: "", table: { cols: ["x"], rows }, updated_at: "t" });
  const [addRow] = ctx.container.querySelectorAll(".sc-tool");
  assert.equal(addRow.disabled, true);
  assert.match(ctx.container.querySelector("#sc-panel-table .sc-note").textContent, /最多 60 行/);
});

test("table: at 20 columns the add-column button is disabled with an explanation", async () => {
  const ctx = setup();
  const cols = Array.from({ length: 20 }, (_v, i) => `c${i}`);
  await mountLoaded(ctx, { version: 1, code: "", fixed: "", table: { cols, rows: [cols.map(() => "")] }, updated_at: "t" });
  const [, , addCol] = ctx.container.querySelectorAll(".sc-tool");
  assert.equal(addCol.disabled, true);
  assert.match(ctx.container.querySelector("#sc-panel-table .sc-note").textContent, /最多 20 列/);
});

test("table: without window.TraceTable the tab degrades gracefully", async () => {
  const ctx = setup({ trace: false });
  await mountLoaded(ctx);
  const panel = ctx.container.querySelector("#sc-panel-table");
  assert.match(panel.textContent, /演算表暂不可用/);
  assert.equal(panel.querySelectorAll("input").length, 0);
  const tabs = ctx.container.querySelectorAll('[role="tab"]');
  tabs[2].click(); // 切过去也不报错
  assert.equal(panel.hidden, false);
});

test("table: cell text with markup stays text", async () => {
  const ctx = setup();
  await mountLoaded(ctx, {
    version: 1, code: "", fixed: "",
    table: { cols: ["<img src=x>"], rows: [["<svg onload=alert(1)>"]] }, updated_at: "t",
  });
  assert.equal(ctx.container.querySelectorAll("img").length, 0);
  assert.equal(ctx.container.querySelectorAll("svg").length, 0);
  assert.equal(ctx.container.querySelectorAll(".sc-grid-head")[0].value, "<img src=x>");
  assert.equal(cells(ctx)[0].value, "<svg onload=alert(1)>");
});

/* ---------------- 自动保存状态机 ---------------- */
test("autosave: edit -> dirty -> saving -> saved with the server timestamp", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "idle");
  type(codeArea(ctx), "hello");
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "dirty");
  await sleep(30);
  assert.equal(ctx.calls.length, 2);
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "saving");
  const body = JSON.parse(ctx.calls[1].options.body);
  assert.deepEqual([body.version, body.code, body.fixed], [0, "hello", ""]);
  ok(ctx.calls[1], { version: 1, updated_at: "2026-10-05 11:11" });
  await tick();
  await tick();
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "saved");
  assert.match(ctx.container.querySelector(".sc-status").textContent, /已保存 2026-10-05 11:11/);
});

test("autosave: the next save uses the version returned by the previous one", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "one");
  await flushPut(ctx);
  ok(ctx.calls[1], { version: 7, updated_at: "t" });
  await tick();
  type(codeArea(ctx), "two");
  await flushPut(ctx);
  assert.equal(JSON.parse(ctx.calls[2].options.body).version, 7);
  ok(ctx.calls[2], { version: 8, updated_at: "t" });
  await tick();
});

test("autosave: an edit during saving is saved again right after (merged)", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "first");
  await flushPut(ctx);
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "saving");
  type(codeArea(ctx), "first and more"); // 保存中又编辑
  await sleep(30); // 防抖到了也只能挂起
  assert.equal(ctx.calls.length, 2, "no second request while the first is in flight");
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
  await tick();
  assert.equal(ctx.calls.length, 3, "the follow-up save fires immediately");
  assert.equal(JSON.parse(ctx.calls[2].options.body).code, "first and more", "merged: latest content wins");
  assert.equal(JSON.parse(ctx.calls[2].options.body).version, 1);
  ok(ctx.calls[2], { version: 2, updated_at: "t2" });
  await tick();
  await tick();
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "saved");
});

test("autosave: a failed save shows a retry that resends the same content", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "keep me");
  await flushPut(ctx);
  fail(ctx.calls[1], 500, "服务器开小差");
  await tick();
  await tick();
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "error");
  const retry = ctx.container.querySelector(".sc-retry");
  assert.ok(retry);
  assert.match(retry.textContent, /保存失败，点此重试/);
  retry.click();
  await tick();
  const puts = ctx.calls.filter((call) => call.options.method === "PUT");
  assert.equal(puts.length, 2, "retry issues a new PUT");
  assert.equal(JSON.parse(puts[1].options.body).code, "keep me", "same content, nothing lost");
  ok(puts[1], { version: 1, updated_at: "t" });
  await tick();
  await tick();
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "saved");
});

test("autosave: content is not lost after a failure", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "still here");
  await flushPut(ctx);
  fail(ctx.calls[1], 500, "x");
  await tick();
  await tick();
  assert.equal(codeArea(ctx).value, "still here");
});

test("autosave: 413 is treated as a normal failure with retry", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "x");
  await flushPut(ctx);
  fail(ctx.calls[1], 413, "太大了");
  await tick();
  await tick();
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "error");
  assert.ok(ctx.container.querySelector(".sc-retry"));
});

/* ---------------- 409 冲突 ---------------- */
test("conflict: 409 shows both choices and never silently overwrites", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "mine");
  await flushPut(ctx);
  fail(ctx.calls[1], 409, "版本冲突");
  await tick();
  assert.equal(ctx.calls.length, 3, "api() drops the 409 body, so the widget re-fetches the server copy");
  ok(ctx.calls[2], { version: 5, code: "theirs", fixed: "", table: null, updated_at: "srv" });
  await tick();
  await tick();
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "conflict");
  assert.match(ctx.container.querySelector(".sc-conflict").textContent, /另一个窗口修改过这份草稿/);
  assert.ok(ctx.container.querySelector(".sc-conflict-use-server"));
  assert.ok(ctx.container.querySelector(".sc-conflict-keep-mine"));
  assert.equal(codeArea(ctx).value, "mine", "my text is untouched");
});

test("conflict: 'use the server version' adopts the server copy", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "mine");
  await flushPut(ctx);
  fail(ctx.calls[1], 409, "版本冲突", { current: { version: 5, code: "theirs", fixed: "f", table: null, updated_at: "srv" } });
  await tick();
  await tick();
  assert.equal(ctx.calls.length, 2, "a host-provided error.current is used directly, no extra GET");
  ctx.container.querySelector(".sc-conflict-use-server").click();
  assert.equal(codeArea(ctx).value, "theirs");
  assert.equal(fixedArea(ctx).value, "f");
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "saved");
  type(codeArea(ctx), "theirs plus");
  await flushPut(ctx); // 后续保存基于服务器版本
  assert.equal(JSON.parse(ctx.calls.at(-1).options.body).version, 5);
  ok(ctx.calls.at(-1), { version: 6, updated_at: "t" });
  await tick();
});

test("conflict: 'keep mine and overwrite' resubmits with the server version", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "mine");
  await flushPut(ctx);
  fail(ctx.calls[1], 409, "版本冲突", { current: { version: 5, code: "theirs", fixed: "", table: null, updated_at: "srv" } });
  await tick();
  await tick();
  ctx.container.querySelector(".sc-conflict-keep-mine").click();
  await tick();
  const puts = ctx.calls.filter((call) => call.options.method === "PUT");
  assert.equal(puts.length, 2);
  const body = JSON.parse(puts[1].options.body);
  assert.equal(body.version, 5, "resubmitted with the conflict response's version");
  assert.equal(body.code, "mine");
  ok(puts[1], { version: 6, updated_at: "t" });
  await tick();
  await tick();
  assert.equal(ctx.container.querySelector(".sc-panel").dataset.state, "saved");
});

test("conflict: edits made while conflicted do not auto-save silently", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "mine");
  await flushPut(ctx);
  fail(ctx.calls[1], 409, "版本冲突", { current: { version: 5, code: "theirs", fixed: "", table: null, updated_at: "srv" } });
  await tick();
  await tick();
  type(codeArea(ctx), "mine extended");
  await sleep(30);
  const puts = ctx.calls.filter((call) => call.options.method === "PUT");
  assert.equal(puts.length, 1, "no automatic save while the conflict is unresolved");
});

/* ---------------- 立即保存的触发点 ---------------- */
test("autosave: hiding the page saves immediately", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "page hidden");
  ctx.env.document.hidden = true;
  ctx.env.document.dispatchEvent(new FakeEvent("visibilitychange"));
  assert.equal(ctx.calls.length, 2, "no waiting for the debounce");
  assert.equal(JSON.parse(ctx.calls[1].options.body).code, "page hidden");
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
});

test("autosave: visibilitychange while clean does nothing", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  ctx.env.document.hidden = true;
  ctx.env.document.dispatchEvent(new FakeEvent("visibilitychange"));
  assert.equal(ctx.calls.length, 1);
});

/* ---------------- 大小上限 ---------------- */
test("size limit: input beyond 40000 serialized chars is rolled back with a hint", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const area = codeArea(ctx);
  type(area, "x".repeat(40000));
  assert.equal(area.value, "", "over-limit input is reverted");
  const note = [...ctx.container.querySelectorAll(".sc-note.is-error")]
    .find((item) => item.textContent.includes("40000"));
  assert.equal(note.hidden, false);
  await sleep(30);
  assert.equal(ctx.calls.length, 1, "blocked input never becomes a save");
});

test("size limit: the combined size of all three parts is what counts", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  const area = codeArea(ctx);
  const tableJson = JSON.stringify({ cols: ["步骤", "变量", "值"], rows: [["1", "", ""]] }).length;
  type(area, "x".repeat(40000 - tableJson)); // 刚好达标
  assert.equal(area.value.length, 40000 - tableJson);
  const fixed = fixedArea(ctx);
  fixed.value = "y"; // 再多 1 个字符就超了
  fixed.dispatchEvent(new FakeEvent("input"));
  assert.equal(fixed.value, "", "the extra character is rejected");
  await sleep(30);
  assert.equal(ctx.calls.filter((call) => call.options.method === "PUT").length, 1, "only the fitting edit was saved");
  ok(ctx.calls.at(-1), { version: 1, updated_at: "t" });
  await tick();
});

/* ---------------- 迟到响应守卫 ---------------- */
test("guard: a GET that returns after reset (sign-out) never reaches the next account", async () => {
  const ctx = setup();
  ctx.Scratch.mount(ctx.container, { id: 7 });
  await tick();
  ctx.Scratch.reset(); // 登出
  ok(ctx.calls[0], { version: 9, code: "A 的草稿", fixed: "", table: null, updated_at: "t" });
  await tick();
  await tick();
  ctx.session.user = { id: "u2" }; // 换一个账号登录
  ctx.session.epoch = 2;
  ctx.Scratch.mount(ctx.container, { id: 7 });
  await tick();
  assert.equal(ctx.calls.length, 2);
  ok(ctx.calls[1], EMPTY);
  await tick();
  await tick();
  assert.equal(codeArea(ctx).value, "", "A's draft never shows up for B");
  assert.equal(ctx.container.textContent.includes("A 的草稿"), false);
});

test("guard: switching mistakes discards the previous one's late GET", async () => {
  const ctx = setup();
  ctx.Scratch.mount(ctx.container, { id: 7 });
  await tick();
  ctx.Scratch.mount(ctx.container, { id: 9 }); // 切到另一道题
  await tick();
  assert.equal(ctx.calls.length, 2);
  assert.equal(ctx.calls[1].url, "/api/mistakes/9/scratch");
  ok(ctx.calls[0], { version: 1, code: "第七题的草稿", fixed: "", table: null, updated_at: "t" });
  await tick();
  ok(ctx.calls[1], { version: 1, code: "第九题的草稿", fixed: "", table: null, updated_at: "t" });
  await tick();
  await tick();
  assert.equal(codeArea(ctx).value, "第九题的草稿");
});

test("guard: a save that returns after unmount changes nothing", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "x");
  await flushPut(ctx);
  ctx.Scratch.unmount();
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
  await tick();
  assert.equal(ctx.Scratch.state(), "idle");
  assert.equal(ctx.container.childElementCount, 0);
});

test("guard: a save that returns after sign-out is discarded (epoch + user changed)", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "x");
  await flushPut(ctx);
  ctx.session.user = { id: "u2" };
  ctx.session.epoch = 2;
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await tick();
  await tick();
  assert.notEqual(ctx.container.querySelector(".sc-panel").dataset.state, "saved",
    "the stale success never marks the panel saved");
});

test("guard: a late 409 after unmount does not resurrect the panel", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "x");
  await flushPut(ctx);
  ctx.Scratch.unmount();
  fail(ctx.calls[1], 409, "版本冲突");
  await tick();
  await tick();
  assert.equal(ctx.container.childElementCount, 0);
  assert.equal(ctx.Scratch.state(), "idle");
});

/* ---------------- reset ---------------- */
test("reset: flushes once, then the pending debounce never fires a second save", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  type(codeArea(ctx), "about to be dropped");
  ctx.Scratch.reset(); // 卸载时按约定立即保存一次；防抖计时器必须清掉
  assert.equal(ctx.calls.length, 2, "the pending edit is flushed synchronously");
  ok(ctx.calls[1], { version: 1, updated_at: "t" });
  await sleep(40);
  assert.equal(ctx.calls.length, 2, "the debounce timer was cleared: no second save");
});

test("reset: removes the visibilitychange listener", async () => {
  const ctx = setup();
  await mountLoaded(ctx);
  ctx.Scratch.reset();
  ctx.env.document.hidden = true;
  ctx.env.document.dispatchEvent(new FakeEvent("visibilitychange"));
  assert.equal(ctx.calls.length, 1, "no listener survives reset");
});

test("reset: is safe to call before any mount and twice in a row", async () => {
  const ctx = setup();
  ctx.Scratch.reset();
  ctx.Scratch.reset();
  await mountLoaded(ctx);
  assert.ok(ctx.container.querySelector(".sc-panel"));
});

/* ---------------- 加载失败 ---------------- */
test("load: a failed GET shows an error with a working retry", async () => {
  const ctx = setup();
  ctx.Scratch.mount(ctx.container, { id: 7 });
  await tick();
  fail(ctx.calls[0], 500, "服务器开小差");
  await tick();
  await tick();
  assert.match(ctx.container.querySelector(".sc-status").textContent, /草稿加载失败/);
  ctx.container.querySelector(".sc-retry").click();
  await tick();
  assert.equal(ctx.calls.length, 2);
  ok(ctx.calls[1], { version: 1, code: "loaded on retry", fixed: "", table: null, updated_at: "t" });
  await tick();
  await tick();
  assert.equal(codeArea(ctx).value, "loaded on retry");
});
