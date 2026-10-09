"use strict";

/* N1 笔记双向链接（static/notes-links.js）行为测试：Node 内置测试运行器 + 假浏览器。
   覆盖：链接解析（代码保护/别名/题目）、联想键盘流程、正文链接渲染（resolved/dangling/XSS）、
   图谱纯函数（BFS/度数截断/中心保留/布局确定性）、静态契约（真实文件扫描）。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const { load, tick } = require("./js_harness.cjs");

const STATIC = path.join(__dirname, "..", "static");
const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

function envLoad() {
  return load(["notes.js", "notes-links.js"]);
}

function anchorsOf(fragment) {
  const out = [];
  (function walk(node) {
    if (node.nodeType === 3) return;
    if (node.tagName === "A") out.push({
      target: node.getAttribute("data-link-target"),
      kind: node.getAttribute("data-note-link"),
      resolved: node.getAttribute("data-link-resolved"),
      text: node.textContent,
    });
    (node.children || []).forEach(walk);
  })(fragment);
  return out;
}

// ───────────── 解析规则 ─────────────

test("parseLinkTokens: 笔记/别名/题目/代码保护", () => {
  const { window } = envLoad();
  const NL = window.NotesLinks;
  const tokens = NL.parseLinkTokens("见 [[二分]] 与 [[二分|折半]] 与 [[题:两数之和]] 与 `[[代码]]`");
  assert.equal(tokens.length, 3);
  assert.equal(tokens[0].targetText, "二分");
  assert.equal(tokens[1].targetText, "二分");
  assert.equal(tokens[1].alias, "折半");
  assert.equal(tokens[2].kind, "problem");
  assert.equal(tokens[2].targetText, "两数之和");
});

test("parseLinkTokens: 围栏代码块与行内代码不解析", () => {
  const { window } = envLoad();
  const NL = window.NotesLinks;
  const src = "```\n[[围栏]]\n```\n[[真]]\n`a[[0]]`";
  const tokens = NL.parseLinkTokens(src);
  assert.deepEqual(JSON.parse(JSON.stringify(tokens.map((t) => t.targetText))), ["真"]);
});

// ───────────── 联想键盘流程 ─────────────

test("detectLinkQuery / buildLinkInsertion: 补全与光标", () => {
  const { window } = envLoad();
  const NL = window.NotesLinks;
  const q = NL.detectLinkQuery("正文 [[二", 6);
  assert.equal(q.openStart, 3);
  assert.equal(q.query, "二");
  const next = NL.buildLinkInsertion("正文 [[二", 6, "二分查找");
  assert.equal(next.text, "正文 [[二分查找]]");
  assert.equal(next.caret, next.text.length);
  // 无未闭合 [[ 时不动。
  assert.equal(NL.detectLinkQuery("没有链接", 4), null);
});

// ───────────── 正文链接渲染 ─────────────

test("attachInlineLinks: resolved / dangling / 别名 / 代码保护", () => {
  const { window } = envLoad();
  window.NotesLinks.setKnownTargets({ notes: [{ title: "二分" }], problems: [{ title: "两数之和" }] });
  const frag = window.Notes.renderNoteMarkdown("见 [[二分]] 与 [[不存在]] 与 [[两数之和|两数]] 与 `[[x]]`");
  const anchors = anchorsOf(frag);
  assert.equal(anchors.length, 3);
  assert.equal(anchors[0].resolved, "1");
  assert.equal(anchors[1].resolved, "0");
  assert.equal(anchors[2].resolved, "0"); // [[两数之和|两数]] 无题: 前缀 → 视为笔记链接
  assert.equal(anchors[2].text, "两数"); // 别名显示
});

test("attachInlineLinks: XSS 标题被 textContent 中和，不产生脚本节点", () => {
  const { window } = envLoad();
  window.NotesLinks.setKnownTargets({ notes: [], problems: [] });
  const evil = "<script>alert(1)</script>";
  const frag = window.Notes.renderNoteMarkdown(`引用 [[${evil}]]`);
  function tagNames(node, acc) {
    if (node.nodeType === 1) acc.push(node.tagName);
    (node.children || []).forEach((c) => tagNames(c, acc));
    return acc;
  }
  const tags = tagNames(frag, []);
  assert.ok(!tags.includes("SCRIPT"), "不得生成 SCRIPT 元素");
  const anchor = anchorsOf(frag)[0];
  assert.equal(anchor.text, evil); // 仅作为纯文本
});

// ───────────── 图谱纯函数 ─────────────

test("selectGraphNodes: 中心 BFS depth1/depth2", () => {
  const { window } = envLoad();
  const nodes = [
    { id: "note:1", type: "note", title: "A" },
    { id: "note:2", type: "note", title: "B" },
    { id: "note:3", type: "note", title: "C" },
  ];
  const edges = [{ source: "note:1", target: "note:2" }, { source: "note:2", target: "note:3" }];
  const d1 = window.NotesLinks.selectGraphNodes(nodes, edges, { center: "note:1", depth: 1 });
  assert.ok(d1.nodes.find((n) => n.id === "note:2"));
  assert.ok(!d1.nodes.find((n) => n.id === "note:3"));
  const d2 = window.NotesLinks.selectGraphNodes(nodes, edges, { center: "note:1", depth: 2 });
  assert.ok(d2.nodes.find((n) => n.id === "note:3"));
});

test("selectGraphNodes: 超上限按度数截断且中心保留", () => {
  const { window } = envLoad();
  const nodes = [];
  for (let i = 1; i <= 8; i += 1) nodes.push({ id: `note:${i}`, type: "note", title: `N${i}` });
  // 中心 note:1 直接连出 2/3/4/5（depth1 即覆盖 5 个节点）；note:3 度数最高。
  const edges = [
    { source: "note:1", target: "note:2" }, { source: "note:1", target: "note:3" },
    { source: "note:1", target: "note:4" }, { source: "note:1", target: "note:5" },
    { source: "note:3", target: "note:6" }, { source: "note:3", target: "note:7" },
  ];
  const sel = window.NotesLinks.selectGraphNodes(nodes, edges, { center: "note:1", depth: 1, limit: 4 });
  assert.equal(sel.truncated, true);
  assert.ok(sel.nodes.find((n) => n.id === "note:1"), "中心节点必保留");
  assert.ok(sel.nodes.find((n) => n.id === "note:3"), "度数高的节点优先保留");
});

test("computeLayout: 同输入必同输出（确定性）", () => {
  const { window } = envLoad();
  const nodes = [{ id: "note:1" }, { id: "note:2" }, { id: "note:3" }];
  const edges = [{ source: "note:1", target: "note:2" }];
  const a = window.NotesLinks.computeLayout(nodes, edges, { iterations: 5 });
  const b = window.NotesLinks.computeLayout(nodes, edges, { iterations: 5 });
  assert.deepEqual(a, b);
});

// ───────────── 静态契约（真实文件） ─────────────

const js = fs.readFileSync(path.join(STATIC, "notes-links.js"), "utf8");
const css = fs.readFileSync(path.join(STATIC, "notes-links.css"), "utf8");
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");

test("静态契约: 不使用 innerHTML / 行内 style / eval / 存储 API / 外链", () => {
  for (const forbidden of ["innerHTML", "insertAdjacentHTML", "document.write",
    "localStorage", "sessionStorage", "eval(", "href=\"javascript:", "javascript:"]) {
    assert.ok(!js.includes(forbidden), `notes-links.js 不得出现 ${forbidden}`);
  }
  // 不通过 .style.x = 赋值（允许空的 setProperty 调用形式，但这里禁止任何 .style. 写）。
  assert.ok(!/\.style\s*\.[a-zA-Z]+\s*=/ .test(js), "不得写行内 style");
});

test("静态契约: index.html 版本号与加载顺序", () => {
  const notesLinks = html.indexOf("notes-links.js?v=3");
  const app = html.indexOf("app.js?v=92");
  const notesJs = html.indexOf("notes.js?v=3");
  assert.ok(notesLinks > notesJs, "notes-links.js 在 notes.js 之后");
  assert.ok(app > notesLinks, "notes-links.js 在 app.js 之前");
  assert.ok(html.includes("notes-links.css?v=3"), "引入 notes-links.css");
});

test("静态契约: CSS 只用主题令牌、动效/hover/手机断点包裹", () => {
  for (const forbidden of ["!important", "rgba(", "rgb(", "hsl(", "#"]) {
    assert.ok(!css.includes(forbidden), `notes-links.css 不得出现 ${forbidden}`);
  }
  assert.ok(css.includes("prefers-reduced-motion: no-preference"), "动效需包 reduced-motion");
  assert.ok(css.includes("hover: hover"), "hover 需包 hover:hover");
  assert.ok(css.includes("max-width: 520px"), "需手机断点");
});
