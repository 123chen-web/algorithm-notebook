/* N2 笔记富渲染行为测试（假 DOM）。仿 notes_links_behaviour.cjs：
   prepareSource 纯函数分段、attachment 图片渲染与越权回退、XSS 转义、
   工具栏插入、懒加载静态契约。 */
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { load } = require("./js_harness.cjs");

const STATIC = path.join(__dirname, "..", "static");

function envLoad() {
  return load(["notes.js", "notes-links.js", "notes-rich.js"]);
}

const BLOCK_OPEN = String.fromCharCode(0xE010);
const BLOCK_CLOSE = String.fromCharCode(0xE011);
const INLINE_OPEN = String.fromCharCode(0xE012);
const INLINE_CLOSE = String.fromCharCode(0xE013);

// ───────────── prepareSource 分段 ─────────────

test("prepareSource: 行内 $...$ 识别为 inline-math", () => {
  const { window } = envLoad();
  window.NotesRich.prepareSource("公式 $E=mc^2$ 结束");
  const reg = window.NotesRich.knownRegistry();
  assert.equal(reg.length, 1);
  assert.equal(reg[0].kind, "inline-math");
  assert.equal(reg[0].code, "E=mc^2");
});

test("prepareSource: 块级 $$...$$ 识别为 block-math", () => {
  const { window } = envLoad();
  window.NotesRich.prepareSource("开头\n$$\n\\int_0^1 x\\,dx\n$$\n结尾");
  const reg = window.NotesRich.knownRegistry();
  assert.ok(reg.some((r) => r.kind === "block-math" && r.code.includes("int_0^1")));
});

test("prepareSource: mermaid 围栏识别，其余围栏保持代码块", () => {
  const { window } = envLoad();
  const out = window.NotesRich.prepareSource("```mermaid\nflowchart TD\n A-->B\n```\n```python\nprint(1)\n```");
  const reg = window.NotesRich.knownRegistry();
  assert.ok(reg.some((r) => r.kind === "block-mermaid" && r.code.includes("flowchart TD")));
  assert.ok(!reg.some((r) => r.kind === "block-mermaid" && r.code.includes("print")));
  assert.ok(out.includes("print(1)"), "普通代码围栏内容应原样保留");
});

test("prepareSource: 代码围栏与行内代码里的 $ 与 [[ ]] 不解析", () => {
  const { window } = envLoad();
  window.NotesRich.prepareSource("```\n$code$\n[[链接]]\n```\n行内 `a[[0]]` 真$y$\n普通 [[真实链接]]");
  const reg = window.NotesRich.knownRegistry();
  // 只有"真$y$"是行内公式；代码里的 $code$ 不解析；[[ ]] 交给 NotesLinks。
  assert.equal(reg.length, 1);
  assert.equal(reg[0].kind, "inline-math");
  assert.equal(reg[0].code, "y");
});

// ───────────── attachment 图片渲染 ─────────────

function walkText(node, acc) {
  const kids = node.children || [];
  for (const child of Array.from(kids)) {
    if (child.nodeType === 1) {
      if (child.tagName === "IMG") acc.push({ img: child.getAttribute("src"), alt: child.getAttribute("alt") });
      walkText(child, acc);
    } else if (child.nodeType === 3) {
      acc.push({ text: child.textContent });
    }
  }
  return acc;
}

test("attachment: 本人 id 渲染 img，伪造/他人 id 回退纯文本", () => {
  const { window } = envLoad();
  window.NotesRich.configure({ ownedAttachmentIds: new Set([5]) });
  const root = window.Notes.renderNoteMarkdown("图 ![我的图](attachment:5) 与 ![未知](attachment:999)");
  const acc = walkText(root, []);
  assert.ok(acc.some((x) => x.img === "/api/notes/attachments/5"), "本人 id 应渲染 img");
  assert.ok(acc.some((x) => x.text && x.text.includes("attachment:999")), "伪造 id 应回退纯文本");
});

test("外链 http(s)/data:/javascript: 图片一律纯文本、不发请求", () => {
  const { window } = envLoad();
  window.NotesRich.configure({ ownedAttachmentIds: new Set([5]) });
  const root = window.Notes.renderNoteMarkdown(
    "![a](https://evil.com/x.png) ![b](data:text/html,<script>) ![c](javascript:alert(1))"
  );
  const acc = walkText(root, []);
  assert.ok(!acc.some((x) => x.img), "任何外链都不得渲染 img");
  assert.ok(acc.some((x) => x.text && x.text.includes("evil.com")));
});

test("XSS：alt 含 <script> 经 setAttribute 落位，不执行", () => {
  const { window } = envLoad();
  window.NotesRich.configure({ ownedAttachmentIds: new Set([5]) });
  const root = window.Notes.renderNoteMarkdown('![<script>alert(1)</script>](attachment:5)');
  const acc = walkText(root, []);
  const img = acc.find((x) => x.img);
  assert.ok(img, "本人附件应渲染 img");
  assert.equal(img.alt, "<script>alert(1)</script>", "alt 作为属性值原样保留，不当 HTML 解析");
  assert.ok(!root.textContent.includes("<script>alert(1)</script>"), "alt 不应出现在正文文本里");
});

// ───────────── 工具栏插入 ─────────────

test("工具栏插入：setRangeText + dispatch input，光标落点正确", () => {
  const { window } = envLoad();
  window.NotesRich.prepareSource("x"); // 暴露模块已加载
  const ta = {
    value: "开头",
    selectionStart: 2,
    selectionEnd: 2,
    dispatched: null,
    setRangeText(text, start, end, mode) {
      this.value = this.value.slice(0, start) + text + this.value.slice(end);
      this.selectionStart = this.selectionEnd = start + text.length;
    },
    dispatchEvent(event) { this.dispatched = event; },
  };
  window.NotesRich.insertAtCursor(ta, "**", "**", "加粗文字");
  assert.equal(ta.value, "开头**加粗文字**", "插入结果正确: " + ta.value);
  assert.ok(ta.dispatched, "应 dispatch input 事件");
});

// ───────────── 懒加载静态契约 ─────────────

const nrJs = fs.readFileSync(path.join(STATIC, "notes-rich.js"), "utf8");
const nrCss = fs.readFileSync(path.join(STATIC, "notes-rich.css"), "utf8");
const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");

test("静态契约: notes-rich.js 不用 innerHTML/eval/存储 API/外链", () => {
  for (const forbidden of ["innerHTML", "insertAdjacentHTML", "document.write",
    "localStorage", "sessionStorage", "eval(", "https://", "http://"]) {
    assert.ok(!nrJs.includes(forbidden), `notes-rich.js 不得出现 ${forbidden}`);
  }
  assert.ok(!/\.style\s*\.[a-zA-Z]+\s*=/.test(nrJs), "不得写行内 style");
});

test("静态契约: index.html 无 vendor 静态脚本/样式（运行时才懒加载）", () => {
  assert.ok(!html.includes("vendor/katex"), "index.html 不得静态引入 katex");
  assert.ok(!html.includes("vendor/mermaid"), "index.html 不得静态引入 mermaid");
});

test("静态契约: vendor 只在 notes-rich.js 内动态注入", () => {
  assert.ok(nrJs.includes("/static/vendor/katex/katex.min.js"), "katex 应按需注入");
  assert.ok(nrJs.includes("/static/vendor/mermaid/mermaid.min.js"), "mermaid 应按需注入");
  assert.ok(nrJs.includes("securityLevel"), "mermaid 须 strict");
});

test("静态契约: 加载顺序与版本号", () => {
  const notesJs = html.indexOf("notes.js?v=4");
  const linksJs = html.indexOf("notes-links.js?v=6");
  const richJs = html.indexOf("notes-rich.js?v=5");
  const appJs = html.indexOf("app.js?v=92");
  assert.ok(notesJs > -1 && linksJs > notesJs, "notes-links.js 在 notes.js 后");
  assert.ok(richJs > linksJs, "notes-rich.js 在 notes-links.js 后");
  assert.ok(appJs > richJs, "app.js 在 notes-rich.js 后");
  assert.ok(html.includes("notes-rich.css?v=4"), "引入 notes-rich.css");
});

test("静态契约: CSS 只用主题令牌、动效/hover/手机断点包裹", () => {
  for (const forbidden of ["!important", "rgba(", "rgb(", "hsl(", "#"]) {
    assert.ok(!nrCss.includes(forbidden), `notes-rich.css 不得出现 ${forbidden}`);
  }
  assert.ok(nrCss.includes("prefers-reduced-motion: no-preference"), "动效需包 reduced-motion");
  assert.ok(nrCss.includes("hover: hover"), "hover 需包 hover:hover");
  assert.ok(nrCss.includes("max-width: 520px"), "需手机断点");
});
