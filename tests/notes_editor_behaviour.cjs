"use strict";

/* N5 所见即所得编辑器（static/notes-editor.js + notes.js 内容层）行为测试。
   用一个 OYEditor 桩对象验证：
   - OYEditor.create 的挂载参数（元素、maxLength、各回调）；
   - 保存 / 预填 / 模板插入走 getMarkdown / setMarkdown；
   - 图片上传、[[ 联想、互链跳转、画板插入、题目选择回调；
   - 两条降级路径（OYEditor 缺失、create 抛错）回退 textarea；
   - 编辑态 mount/destroy；字数上限回调；
   - 列表静态渲染：待办清单（只读勾选框）、GFM 表格、分割线、删除线、嵌套列表。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick } = require("./js_harness.cjs");

const STATIC = path.join(__dirname, "..", "static");
const DEPS = ["notes.js", "notes-links.js", "notes-rich.js", "draw-host.js"];
const RICH_HINT = "输入 / 试试，选中文字可以加粗或加链接";

// vm 上下文里创建的对象与测试主 realm 原型不同，deepEqual 前用 JSON 归一化为普通对象。
const plain = (value) => JSON.parse(JSON.stringify(value));

function runEditor(env) {
  vm.runInContext(
    fs.readFileSync(path.join(STATIC, "notes-editor.js"), "utf8"),
    env.context,
    { filename: "notes-editor.js" }
  );
}

function makeOYEditor() {
  const created = [];
  const OYEditor = {
    create(element, options) {
      const inst = {
        el: element,
        opts: options,
        md: options.markdown || "",
        focused: false,
        destroyed: false,
        getMarkdown() { return this.md; },
        setMarkdown(value) { this.md = String(value); },
        focus() { this.focused = true; },
        blur() {},
        destroy() { this.destroyed = true; },
        insertDrawing(payload) { this.drawing = payload; },
        insertImage(payload) { this.image = payload; },
        insertWikilink(payload) { this.wiki = payload; },
        uploadImageFile() {},
        isEmpty() { return !this.md; }
      };
      created.push(inst);
      return inst;
    }
  };
  return { OYEditor, created };
}

function seedComposer(document) {
  const host = document.createElement("div");
  host.id = "notes-editor-host";
  host.className = "notes-editor-host";
  const textarea = document.createElement("textarea");
  textarea.id = "notes-content";
  textarea.hidden = true;
  const hint = document.createElement("p");
  hint.id = "notes-editor-hint";
  const notice = document.createElement("p");
  notice.id = "notes-editor-fallback";
  notice.hidden = true;
  document.body.append(host, textarea, hint, notice);
  return { host, textarea, hint, notice };
}

function envWithEditor(OYEditor) {
  const env = load(DEPS);
  const draw = { created: 0, inserter: null, owned: new Set([9]), thumb: 0 };
  env.window.DrawHost.createDrawing = () => { draw.created += 1; };
  env.window.DrawHost.setNoteInserter = (fn) => { draw.inserter = fn; };
  env.window.DrawHost.ownedIds = () => draw.owned;
  env.window.DrawHost.requestThumbUpload = () => { draw.thumb += 1; return Promise.resolve(true); };
  if (OYEditor) env.window.OYEditor = OYEditor;
  runEditor(env);
  return { env, draw };
}

/* ───────────── 挂载与 create 参数 ───────────── */

test("挂载：OYEditor.create 收到 host 与完整回调，textarea 隐藏、引导语就位", () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  const nodes = seedComposer(env.document);
  const ok = env.window.NotesEditor.attachComposer();
  assert.equal(ok, true);
  assert.equal(env.window.NotesEditor.editorActive(), true);
  assert.equal(env.window.NotesEditor.isFallback(), false);
  assert.equal(created.length, 1);
  assert.equal(created[0].el, nodes.host);
  const opts = created[0].opts;
  assert.equal(opts.maxLength, 20000, "maxLength 取后端 NOTE_CONTENT_MAX");
  for (const name of [
    "uploadImage", "resolveImageSrc", "suggestLinks", "onLinkClick",
    "onRequestDrawing", "onRequestProblemPicker", "onOverLimit"
  ]) {
    assert.equal(typeof opts[name], "function", `options.${name} 是函数`);
  }
  assert.equal(nodes.textarea.hidden, true);
  assert.equal(nodes.host.hidden, false);
  assert.equal(nodes.hint.textContent, RICH_HINT);
  assert.equal(nodes.notice.hidden, true);
});

/* ───────────── 保存 / 预填 / 模板：走 getMarkdown / setMarkdown ───────────── */

test("内容层：保存取 getMarkdown，预填/聚焦/模板插入作用于编辑器实例", () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  const inst = created[0];

  inst.md = "# 标题\n正文内容";
  assert.equal(env.window.Notes.getComposerContent(), "# 标题\n正文内容");

  env.window.Notes.setComposerContent("新内容");
  assert.equal(inst.md, "新内容");

  env.window.Notes.focusComposer();
  assert.equal(inst.focused, true);

  env.window.Notes.insertComposerTemplate("## 解法对比\n- 思路 A");
  assert.match(inst.md, /新内容\n\n## 解法对比/);
});

/* ───────────── 图片上传回调 ───────────── */

test("uploadImage：复用 NotesRich.uploadFile，返回 attachment:ID 形式", async () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  env.window.NotesRich.uploadFile = async () => ({ id: 42 });
  const result = await created[0].opts.uploadImage({ name: "a.png" });
  assert.deepEqual(plain(result), { src: "attachment:42", alt: "a.png" });

  env.window.NotesRich.uploadFile = async () => ({});
  await assert.rejects(created[0].opts.uploadImage({ name: "b.png" }), /附件 ID/);
});

/* ───────────── 图片地址解析：附件 / 画板归属 ───────────── */

test("resolveImageSrc：attachment 直连；drawing 仅对当前用户拥有的 id 给缩略图", () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  const resolve = created[0].opts.resolveImageSrc;
  assert.equal(resolve("attachment:7"), "/api/notes/attachments/7");
  assert.equal(resolve("drawing:9"), "/api/drawings/9/thumb");
  assert.equal(resolve("drawing:5"), "", "非本人画板不返回地址");
  assert.equal(resolve("other-src"), "other-src");
});

/* ───────────── [[ 联想 ───────────── */

test("suggestLinks：把 /api/notes/suggest 结果映射成 {label,kind}", async () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  let gotUrl = "";
  env.window.NotesEditor.configure({
    api: async (url) => {
      gotUrl = url;
      return { results: [
        { title: "笔记甲", kind: "note" },
        { title: "题目乙", kind: "problem" },
        { title: "其它", kind: "unknown" }
      ] };
    }
  });
  const list = await created[0].opts.suggestLinks("甲乙");
  assert.deepEqual(plain(list), [
    { label: "笔记甲", kind: "note" },
    { label: "题目乙", kind: "problem" },
    { label: "其它", kind: "note" }
  ]);
  assert.match(gotUrl, /\/api\/notes\/suggest\?q=%E7%94%B2%E4%B9%99/);
});

/* ───────────── 互链跳转 ───────────── */

test("onLinkClick：复用 NotesLinks.openLinkTarget（题目分支走其跳转提示）", () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  const notified = [];
  env.window.NotesLinks.configure({ notify: (message) => notified.push(message) });
  created[0].opts.onLinkClick({ label: "两数之和", kind: "problem" });
  assert.equal(notified.length, 1);
  assert.match(notified[0], /两数之和/);
});

/* ───────────── 画板：打开面板，保存后插入 drawing:ID ───────────── */

test("onRequestDrawing：打开 DrawHost，并把新画板插进当前编辑器", async () => {
  const { OYEditor, created } = makeOYEditor();
  const { env, draw } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  created[0].opts.onRequestDrawing();
  assert.equal(draw.created, 1, "调用 DrawHost.createDrawing");
  assert.equal(typeof draw.inserter, "function");
  await draw.inserter({ id: 9, title: "示意图" });
  assert.equal(draw.thumb, 1, "插入前先主动上传缩略图");
  assert.deepEqual(plain(created[0].drawing), { id: 9, title: "示意图" });
});

/* ───────────── 题目选择弹层 ───────────── */

test("onRequestProblemPicker：拉 /api/problems，点选后插入 [[题:标题]]", async () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  env.window.NotesEditor.configure({
    api: async (url) => url.includes("/api/problems")
      ? { problems: [{ id: 1, title: "两数之和" }, { id: 2, title: "两数相加" }] }
      : {}
  });
  created[0].opts.onRequestProblemPicker();
  await tick(); await tick(); await tick();
  const dialog = env.document.getElementById("notes-problem-picker");
  assert.ok(dialog, "弹出题目选择层");
  const items = dialog.querySelectorAll(".notes-picker-item");
  assert.equal(items.length, 2);
  assert.equal(items[0].textContent, "两数之和");
  items[0].click();
  assert.deepEqual(plain(created[0].wiki), { label: "两数之和", kind: "problem" });
  assert.equal(env.document.getElementById("notes-problem-picker"), null, "选完关闭弹层");
  assert.equal(env.document.querySelectorAll(".notes-picker-backdrop").length, 0,
    "选完连带移除全屏背景，不残留遮挡层");
  assert.equal(env.document.querySelectorAll(".notes-picker").length, 0, "对话框一并移除");
});

test("题目弹层：点关闭按钮或按 Esc 也会移除背景层", async () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  env.window.NotesEditor.configure({
    api: async () => ({ problems: [{ id: 1, title: "两数之和" }] })
  });
  const { FakeEvent } = require("./js_harness.cjs");
  const open = async () => {
    created[0].opts.onRequestProblemPicker();
    await tick(); await tick(); await tick();
    assert.ok(env.document.querySelector(".notes-picker-backdrop"), "背景层出现");
  };
  await open();
  env.document.querySelector(".notes-picker-close").click();
  assert.equal(env.document.querySelectorAll(".notes-picker-backdrop").length, 0, "关闭按钮移除背景");
  await open();
  env.document.dispatchEvent(new FakeEvent("keydown", { props: { key: "Escape" } }));
  assert.equal(env.document.querySelectorAll(".notes-picker-backdrop").length, 0, "Esc 移除背景");
});

test("图片重试：画板/附件图片加载失败时退避重试并加缓存击穿参数", async () => {
  const { OYEditor } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  env.window.NotesEditor.configure({});
  const { FakeEvent } = require("./js_harness.cjs");
  const fireError = (node) => env.document.listeners.error.forEach((fn) => fn({ target: node }));
  const img = env.document.createElement("img");
  img.setAttribute("src", "/api/drawings/9/thumb");
  env.document.body.append(img);
  fireError(img);
  assert.equal(img.getAttribute("data-oy-retry"), "1", "失败后立即登记重试次数");
  await new Promise((resolve) => setTimeout(resolve, 700));
  const src = img.getAttribute("src");
  assert.match(src, /^\/api\/drawings\/9\/thumb\?retry=1&t=\d+$/, "退避后重设 src 并带缓存击穿参数");
  // 无关图片不重试
  const other = env.document.createElement("img");
  other.setAttribute("src", "/static/x.png");
  env.document.body.append(other);
  fireError(other);
  assert.equal(other.getAttribute("data-oy-retry"), null, "非画板/附件图片不重试");
});

/* ───────────── 字数上限回调 ───────────── */

test("onOverLimit：超长时用中文提示", () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  const notified = [];
  env.window.NotesEditor.configure({ notify: (message, isError) => notified.push([message, isError]) });
  created[0].opts.onOverLimit(20001, 20000);
  assert.equal(notified.length, 1);
  assert.match(notified[0][0], /20000/);
  assert.equal(notified[0][1], true);
});

/* ───────────── 编辑已有笔记：mountEdit / destroy ───────────── */

test("mountEdit：在容器内挂编辑器，控制器 getMarkdown/focus/destroy 可用", () => {
  const { OYEditor, created } = makeOYEditor();
  const { env } = envWithEditor(OYEditor);
  seedComposer(env.document);
  env.window.NotesEditor.attachComposer();
  const container = env.document.createElement("div");
  env.document.body.append(container);
  const ctl = env.window.NotesEditor.mountEdit(container, "# 原笔记");
  assert.ok(ctl, "mountEdit 返回控制器");
  assert.equal(created.length, 2);
  assert.equal(ctl.getMarkdown(), "# 原笔记");
  ctl.focus();
  assert.equal(created[1].focused, true);
  ctl.destroy();
  assert.equal(created[1].destroyed, true);
  assert.equal(container.children.length, 0, "destroy 移除挂载节点");
});

/* ───────────── 降级路径 A：window.OYEditor 不存在 ───────────── */

test("降级 A：OYEditor 缺失时回退 textarea，给中文提示，仍可读写内容", () => {
  const env = load(DEPS);
  runEditor(env); // 不注入 window.OYEditor
  const nodes = seedComposer(env.document);
  const ok = env.window.NotesEditor.attachComposer();
  assert.equal(ok, false);
  assert.equal(env.window.NotesEditor.isFallback(), true);
  assert.equal(env.window.NotesEditor.editorActive(), false);
  assert.equal(nodes.textarea.hidden, false);
  assert.equal(nodes.host.hidden, true);
  assert.equal(nodes.notice.hidden, false);
  assert.match(nodes.notice.textContent, /纯文本/);
  assert.match(nodes.hint.textContent, /纯文本输入/);
  nodes.textarea.value = "# 降级笔记";
  assert.equal(env.window.Notes.getComposerContent(), "# 降级笔记");
});

/* ───────────── 降级路径 B：create 抛错 ───────────── */

test("降级 B：OYEditor.create 抛错时回退 textarea，mountEdit 返回 null", () => {
  const throwing = { create() { throw new Error("boom"); } };
  const { env } = envWithEditor(throwing);
  const nodes = seedComposer(env.document);
  const ok = env.window.NotesEditor.attachComposer();
  assert.equal(ok, false);
  assert.equal(env.window.NotesEditor.isFallback(), true);
  assert.equal(nodes.textarea.hidden, false);
  const container = env.document.createElement("div");
  env.document.body.append(container);
  assert.equal(env.window.NotesEditor.mountEdit(container, "x"), null);
});

/* ───────────── 列表静态渲染：待办清单 / 表格 / 分割线 / 删除线 / 嵌套列表 ───────────── */

test("渲染：- [ ] / - [x] 输出只读勾选清单", () => {
  const { window } = load(["notes.js"]);
  const frag = window.Notes.renderNoteMarkdown("- [ ] 未完成\n- [x] 已完成");
  const list = frag.querySelector("ul.notes-task-list");
  assert.ok(list);
  const items = list.querySelectorAll("li.notes-task-item");
  assert.equal(items.length, 2);
  const boxes = list.querySelectorAll("input");
  assert.equal(boxes.length, 2);
  assert.equal(boxes[0].disabled, true);
  assert.equal(boxes[0].checked, false);
  assert.equal(boxes[1].disabled, true);
  assert.equal(boxes[1].checked, true);
  assert.equal(items[0].querySelector(".notes-task-text").textContent, "未完成");
  assert.equal(items[1].querySelector(".notes-task-text").textContent, "已完成");
});

test("渲染：GFM 表格输出 thead/tbody，单元格文字走 textContent", () => {
  const { window } = load(["notes.js"]);
  const src = "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |";
  const frag = window.Notes.renderNoteMarkdown(src);
  const table = frag.querySelector("table.notes-rich-table");
  assert.ok(table);
  const heads = table.querySelectorAll("thead th");
  assert.deepEqual([...heads].map((cell) => cell.textContent), ["列A", "列B"]);
  const rows = table.querySelectorAll("tbody tr");
  assert.equal(rows.length, 2);
  assert.deepEqual([...rows[0].querySelectorAll("td")].map((cell) => cell.textContent), ["1", "2"]);
  assert.equal(frag.textContent.includes("|"), false, "表格线不进文本");
});

test("渲染：分割线、删除线、嵌套列表", () => {
  const { window } = load(["notes.js"]);
  assert.ok(window.Notes.renderNoteMarkdown("---").querySelector("hr"));
  const striked = window.Notes.renderNoteMarkdown("这是 ~~删掉~~ 的");
  assert.equal(striked.querySelector("del").textContent, "删掉");
  const nested = window.Notes.renderNoteMarkdown("- 外层\n  - 内层");
  assert.equal(nested.querySelector("ul ul li").textContent, "内层");
});
