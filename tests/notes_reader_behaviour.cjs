"use strict";

/* 笔记阅读视图与“串联”的行为测试：
   - renderNoteMarkdown 的内置积木：荧光笔、提示框（含编辑器转义后的写法）、折叠答案、下划线标题、中文章节标题、转义的 * 列表；
   - NotesPane：整理格式 tidyMarkdown、列表预览 previewText、目录 headings；
   - 右边打开一篇先看阅读视图，点“编辑”进编辑区，“整理格式”带 snapshot 保存；
   - NotesConnect：正文里提到的笔记 / 套路加下划线（每个词只标一次、代码里不标），串联面板按组列出。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

function render(source) {
  const env = load(["notes-pane.js", "notes.js"]);
  const root = env.document.createElement("div");
  root.append(env.window.Notes.renderNoteMarkdown(source));
  return { root, env };
}

test("荧光笔 ==文字== 渲染成 mark，里面的粗体照常", () => {
  const { root } = render("这是 ==要记住的 **重点**== 一句");
  const mark = root.querySelector("mark");
  assert.ok(mark && mark.classList.contains("nm-hl"));
  assert.equal(mark.textContent, "要记住的 重点");
  assert.equal(mark.querySelector("strong").textContent, "重点");
});

test("提示框：中英文类型、折叠、编辑器转义后的 \\[!类型\\] 都认", () => {
  const { root } = render([
    "> [!易错] 优先级",
    "> `x & 1 == 0` 要加括号",
    "",
    "> \\[!答案\\]- lowbit 是什么？  ",
    "> `x & -x`",
    "",
    "> [!tip]",
    "> 英文类型也行",
    "",
    "> 普通引用不变",
  ].join("\n"));
  const boxes = root.querySelectorAll(".nm-callout");
  assert.equal(boxes.length, 3);
  assert.ok(boxes[0].classList.contains("nm-callout-danger"));
  assert.equal(boxes[0].tagName, "DIV");
  assert.match(boxes[0].querySelector(".nm-callout-title").textContent, /优先级/);
  assert.equal(boxes[0].querySelector("code").textContent, "x & 1 == 0");
  assert.equal(boxes[1].tagName, "DETAILS", "[!答案]- 默认折起");
  assert.ok(boxes[1].classList.contains("nm-callout-answer"));
  assert.match(boxes[1].querySelector("summary").textContent, /lowbit 是什么？/);
  assert.ok(boxes[2].classList.contains("nm-callout-tip"));
  assert.match(boxes[2].querySelector(".nm-callout-title").textContent, /提示/, "没写标题时用类型名");
  assert.equal(root.querySelectorAll("blockquote").length, 1);
});

test("粘贴来的纯文本：下划线标题、一、章节、转义的 * 列表、答案：折叠", () => {
  const { root } = render([
    "位运算学习笔记",
    "==============",
    "",
    "一、二进制基础",
    "",
    "\\* 二进制转十进制",
    "\\* 十进制转二进制",
    "",
    "自测：手算 6 的二进制。",
    "",
    "答案：6 = 110",
    "",
    "一、这一行太长了不像标题而像一句完整的话，带句号。",
  ].join("\n"));
  const headings = root.querySelectorAll("h2");
  assert.equal(headings[0].textContent, "位运算学习笔记");
  assert.equal(headings[1].textContent, "一、二进制基础");
  assert.ok(headings[1].classList.contains("nm-cn-heading"));
  assert.equal(headings.length, 2, "带句号的长句不当标题");
  assert.deepEqual(root.querySelectorAll("li").map((li) => li.textContent), ["二进制转十进制", "十进制转二进制"]);
  assert.ok(root.querySelector("p.nm-selftest"));
  const answer = root.querySelector("details.nm-answer");
  assert.ok(answer, "答案默认折起");
  assert.match(answer.querySelector("summary").textContent, /想好了再点开/);
  assert.equal(answer.querySelector("p").textContent, "6 = 110");
});

test("NotesPane.tidyMarkdown：整理成正经格式，代码块里不动", () => {
  const { window } = load(["notes-pane.js"]);
  const source = "位运算学习笔记\n=====\n\n一、二进制基础\n\n\\* 一项\n\n答案：6 = 110\n\n```\n一、代码里\n```";
  assert.equal(window.NotesPane.tidyMarkdown(source),
    "# 位运算学习笔记\n\n## 一、二进制基础\n\n- 一项\n\n> [!答案]- 答案\n> 6 = 110\n\n```\n一、代码里\n```");
  assert.equal(window.NotesPane.tidyMarkdown("# 已经很整齐\n\n- 列表"), "# 已经很整齐\n\n- 列表");
});

test("NotesPane.previewText / headings：跳过标题行、下划线、代码块", () => {
  const { window } = load(["notes-pane.js"]);
  const note = { title: "", content: "位运算\n=====\n\n## 一、基础\n```\ncode\n```\n**权重** 从右往左翻倍" };
  assert.equal(window.NotesPane.previewText(note), "一、基础 权重 从右往左翻倍");
  assert.deepEqual([...window.NotesPane.headings("# A\n```\n# 不是\n```\n## B\n### C").map((item) => `${item.level}${item.text}`)], ["1A", "2B", "3C"]);
});

/* ---------- 右边：阅读视图 → 编辑 / 整理格式 ---------- */
function setup() {
  const env = load(["notes-pane.js", "notes.js"]);
  const state = { notified: [] };
  env.window.Notes.configure({
    async api(path, options = {}) {
      const response = await env.window.fetch(path, options);
      const body = await response.json();
      if (!response.ok) { const error = new Error(body.detail); error.status = response.status; throw error; }
      return body;
    },
    getUser: () => ({ id: 7 }), getEpoch: () => 1, getView: () => "notes",
    confirm: () => true, notify: (text) => state.notified.push(text),
  });
  return { env, state, document: env.document, Notes: env.window.Notes };
}

async function loadNotes(env, notes) {
  const loading = env.window.Notes.load();
  await tick();
  env.respond(env.calls.at(-1), 200, { problems: [] });
  await tick(); await tick();
  env.respond(env.calls.at(-1), 200, { notes, total: notes.length });
  await loading;
}

const NOTE = {
  id: 5, title: "位运算", content: "位运算学习笔记\n=====\n\n答案：6 = 110", tags: ["位运算"], problem_id: null,
  pinned: false, created_at: "2026-10-14T08:00:00+00:00", updated_at: "2026-10-14T08:00:00+00:00",
};

test("打开一篇先是阅读视图；“编辑”进编辑区；“整理格式”带 snapshot 保存后回到阅读视图", async () => {
  const { env, document, state, Notes } = setup();
  await loadNotes(env, [{ ...NOTE }]);
  document.querySelector("#notes-list").querySelector(".notes-card-open").click();
  await tick(); await tick();
  const pane = document.querySelector("#notes-pane-note");
  assert.ok(pane.querySelector(".notes-reader"), "先看到阅读视图");
  assert.equal(pane.querySelector(".notes-editor"), null);
  const actions = () => [...pane.querySelectorAll(".notes-action")];
  const tidy = actions().find((button) => button.textContent === "整理格式");
  assert.ok(tidy && !tidy.hidden, "有能整理的格式时出现按钮");
  tidy.click();
  await tick();
  const call = env.calls.at(-1);
  assert.equal(call.url, "/api/notes/5");
  const body = JSON.parse(call.init.body);
  assert.equal(body.snapshot, true, "整理前的样子一定存进历史");
  assert.equal(body.base_updated_at, NOTE.updated_at);
  assert.equal(body.content, "# 位运算学习笔记\n\n> [!答案]- 答案\n> 6 = 110");
  env.respond(call, 200, { ...NOTE, content: body.content, updated_at: "2026-10-14T08:05:00+00:00" });
  await tick(); await tick(); await tick();
  assert.ok(state.notified.some((text) => /整理好了/.test(text)));
  const tidyAgain = actions().find((button) => button.textContent === "整理格式");
  assert.equal(tidyAgain.hidden, true, "整理过就不再出现");
  actions().find((button) => button.textContent === "编辑").click();
  await tick(); await tick();
  assert.ok(pane.querySelector(".notes-editor"), "点编辑进入编辑区");
  assert.ok(pane.querySelector(".notes-block-row"), "编辑时有“插入积木”一行");
  assert.equal(Notes.currentNoteId(), 5);
});

/* ---------- 串联 ---------- */
test("NotesConnect.decorate：提到的笔记 / 套路各标第一次，长词优先，代码里不标", () => {
  const env = load(["notes-connect.js"]);
  const doc = env.document;
  const root = doc.createElement("div");
  const p = doc.createElement("p");
  p.append(doc.createTextNode("单调栈和单调栈不一样，二分查找很常用；"));
  const code = doc.createElement("code");
  code.textContent = "单调栈";
  p.append(code, doc.createTextNode(" 再说一次二分。"));
  root.append(p);
  const count = env.window.NotesConnect.decorate(root, {
    note_mentions: [{ id: 3, title: "单调栈" }],
    patterns: [{ id: "binary-search-basic", title: "二分查找（左闭右闭）", term: "二分查找" }, { id: "x", title: "二分", term: "二分" }],
  });
  const marks = root.querySelectorAll(".nc-mention");
  assert.equal(count, 3);
  assert.deepEqual(marks.map((node) => `${node.dataset.kind}:${node.textContent}`), ["note:单调栈", "pattern:二分查找", "pattern:二分"]);
  assert.equal(code.textContent, "单调栈", "代码里的不动");
  assert.equal(root.textContent, "单调栈和单调栈不一样，二分查找很常用；单调栈 再说一次二分。", "文字一个字都没少");
});

test("NotesConnect.renderPanel：分组列出，点相关错题走打开错题的钩子", () => {
  const env = load(["notes-connect.js"]);
  const opened = [];
  env.window.NotesConnect.configure({ openMistake: (id) => opened.push(["mistake", id]), openNote: (id) => opened.push(["note", id]), today: () => "2026-10-15" });
  const box = env.document.createElement("div");
  env.window.NotesConnect.renderPanel(box, {
    mistakes: [{ id: 9, title: "二分查找", tags: ["边界"], due_date: "2026-10-15" }],
    patterns: [],
    note_mentions: [{ id: 3, title: "单调栈" }],
    mentioned_by: [],
    same_tag: [{ id: 4, title: "树状数组", shared: ["位运算"] }],
  });
  assert.equal(box.hidden, false);
  const titles = box.querySelectorAll(".nc-section-title").map((node) => node.textContent);
  assert.deepEqual(titles, ["相关错题（1）", "这篇提到的笔记（1）", "同标签的笔记（1）"]);
  assert.match(box.querySelector(".nc-mistake").textContent, /今天该复习/);
  box.querySelector(".nc-mistake").click();
  box.querySelectorAll(".nc-item")[1].click();
  assert.deepEqual(opened, [["mistake", 9], ["note", 3]]);
  env.window.NotesConnect.renderPanel(box, { mistakes: [], patterns: [], note_mentions: [], mentioned_by: [], same_tag: [] });
  assert.equal(box.hidden, true, "什么都没有就不显示");
});

test("RelatedNotes：复习时折叠显示相关笔记，点一下打开；一条都没有就不显示", async () => {
  const env = load(["related-notes.js"]);
  const host = env.document.createElement("div");
  const opened = [];
  let reply;
  const api = (path) => new Promise((resolve) => { reply = { path, resolve }; });
  const pending = env.window.RelatedNotes.mount(host, { problem_id: 12 }, { api, openNote: (id) => opened.push(id), isCurrent: () => true });
  assert.equal(reply.path, "/api/problems/12/related-notes");
  reply.resolve({ items: [{ id: 4, title: "二分套路", reason: "同一个套路：二分查找", preview: "先想区间开闭" }] });
  await pending;
  assert.equal(host.hidden, false);
  assert.match(host.querySelector("summary").textContent, /我写过的相关笔记（1）/);
  assert.match(host.querySelector(".related-note-reason").textContent, /同一个套路/);
  host.querySelector(".related-note").click();
  assert.deepEqual(opened, [4]);

  const empty = env.document.createElement("div");
  const second = env.window.RelatedNotes.mount(empty, { problem_id: 13 }, { api, isCurrent: () => true });
  reply.resolve({ items: [] });
  await second;
  assert.equal(empty.hidden, true);
});

test("从复习页“记笔记”：带上复盘草稿时新建区直接放草稿，并关联这道题", async () => {
  const { env, document, Notes } = setup();
  Notes.openWithProblem(42, "二分查找", { content: "# 复盘：二分查找\n\n## 错在哪\n漏了 left == right" });
  const loading = Notes.load();
  await tick();
  env.respond(env.calls.at(-1), 200, { problems: [] });
  await tick(); await tick();
  env.respond(env.calls.at(-1), 200, { notes: [], total: 0 });
  await loading;
  assert.match(document.querySelector("#notes-content").value, /^# 复盘：二分查找/);
  assert.equal(document.querySelector("#notes-problem").value, "42");
});

test("NotesConnect：单独一行的 [[题:…]] 变成活的错题卡，句子里的变成能点开错题的链接", () => {
  const env = load(["notes-connect.js"]);
  const doc = env.document;
  const opened = [];
  env.window.NotesConnect.configure({ openMistake: (id) => opened.push(id), today: () => "2026-10-15", tagLabel: (tag) => ({ boundary: "边界/特例" })[tag] || tag });
  const root = doc.createElement("div");
  const link = (title) => {
    const a = doc.createElement("a");
    a.setAttribute("data-note-link", "problem");
    a.setAttribute("data-link-target", title);
    a.textContent = title;
    return a;
  };
  const alone = doc.createElement("p");
  alone.append(link("二分查找"));
  const inSentence = doc.createElement("p");
  inSentence.append(doc.createTextNode("和 "), link("二分查找"), doc.createTextNode(" 一样"));
  const unknown = doc.createElement("p");
  unknown.append(link("没记过的题"));
  root.append(alone, inSentence, unknown);
  env.window.NotesConnect.decorate(root, { mistakes: [{ id: 9, title: "二分查找", zone: "算法", tags: ["boundary"], due_date: "2026-10-15" }] });
  const card = root.querySelector(".nc-mistake-card");
  assert.ok(card, "单独一行变成错题卡");
  assert.match(card.textContent, /二分查找/);
  assert.match(card.textContent, /边界\/特例/, "错因显示中文名");
  assert.match(card.textContent, /今天该复习/);
  const inline = inSentence.querySelector(".nc-mistake-link");
  assert.ok(inline && inline.textContent === "二分查找");
  assert.ok(unknown.querySelector('a[data-note-link="problem"]'), "找不到的题保持原样");
  card.click();
  inline.click();
  assert.deepEqual(opened, [9, 9]);
});

test("NotesPane：代码块加语言名和复制；遮住看点一块显示一块，标题始终可见", () => {
  const env = load(["notes-pane.js"]);
  const doc = env.document;
  const root = doc.createElement("div");
  const heading = doc.createElement("h2");
  heading.textContent = "标题";
  const pre = doc.createElement("pre");
  const code = doc.createElement("code");
  code.className = "language-python";
  code.textContent = "print(1)";
  pre.append(code);
  const para = doc.createElement("p");
  para.textContent = "要回想的内容";
  root.append(heading, pre, para);
  assert.equal(env.window.NotesPane.enhanceCode(root), 1);
  assert.equal(root.querySelector(".nm-code-lang").textContent, "python");
  assert.ok(root.querySelector(".nm-code-copy"));
  assert.equal(env.window.NotesPane.enhanceCode(root), 0, "重复调用不会再包一层");
  env.window.NotesPane.setCovered(root, true);
  assert.ok(root.classList.contains("is-covered"));
  root.dispatchEvent(new (require("./js_harness.cjs").FakeEvent)("click", { props: { target: para } }));
  assert.ok(para.classList.contains("nm-uncovered"), "点哪块显示哪块");
  env.window.NotesPane.setCovered(root, false);
  assert.equal(root.classList.contains("is-covered"), false);
  assert.equal(para.classList.contains("nm-uncovered"), false, "关掉后清掉“已显示”标记");
});

test("模板里没有空代码块（所见即所得编辑器读不进空代码块，整篇会变空白）", () => {
  const { window } = load(["notes-pane.js"]);
  for (const template of window.NotesPane.TEMPLATES) {
    const text = window.NotesPane.templateText(template.key);
    assert.doesNotMatch(text, /```[\w-]*\n\s*```/, template.key);
  }
});

/* ---------------- 搜索命中片段 ---------------- */
test("searchSnippet：在去掉 Markdown 的正文里找第一个命中，前后留一点并加省略号", () => {
  const env = load(["notes-pane.js"]);
  const pane = env.window.NotesPane;
  const note = { title: "二分", content: "# 二分\n" + "前面很长的铺垫".repeat(10) + "\n- **左闭右开** 时 while lo < hi\n后面" };
  const snip = pane.searchSnippet(note, "左闭右开", 6);
  assert.equal(snip.hit, "左闭右开");
  assert.ok(snip.before.startsWith("…") && snip.before.length === 7, snip.before);
  assert.ok(!snip.before.includes("*") && !snip.after.includes("*"), "Markdown 符号不进片段");
  assert.equal(pane.searchSnippet(note, "LO < HI", 4).hit, "lo < hi", "不分大小写，片段保留原文大小写");
  assert.equal(pane.searchSnippet(note, "不存在"), null);
  assert.equal(pane.searchSnippet(note, "  "), null);
});

test("markInto：每处命中都包进 mark，用户写的 <b> 只是文字", () => {
  const env = load(["notes-pane.js"]);
  const el = env.document.createElement("span");
  const count = env.window.NotesPane.markInto(el, "<b>栈</b> 和 单调栈", "栈");
  assert.equal(count, 2);
  assert.equal(el.querySelectorAll("mark").length, 2);
  assert.equal(el.querySelector("b"), null);
  assert.equal(el.textContent, "<b>栈</b> 和 单调栈");
});

test("revealMatch：正文里第一个命中被标成当前命中，折叠里的会先展开", () => {
  const { root, env } = render("开头\n\n> [!答案]- 点开看\n> 关键是 lowbit\n\n结尾 lowbit");
  const mark = env.window.NotesPane.revealMatch(root, "LOWBIT");
  assert.ok(mark && mark.classList.contains("is-current"));
  assert.equal(mark.textContent, "lowbit");
  assert.equal(root.querySelectorAll("mark.notes-search-hit").length, 1, "只标第一个");
  const details = root.querySelector("details");
  if (details) assert.equal(details.open, true);
  assert.ok(root.textContent.includes("关键是 lowbit"), "拆开文字节点后原文不变");
  assert.equal(env.window.NotesPane.revealMatch(root, "没有的词"), null);
});

test("编辑时“看效果”：在正文下面显示排好版的样子，改了正文会跟着变，收起后不再刷新", async () => {
  const { env, document } = setup();
  await loadNotes(env, [{ ...NOTE, content: "要点 ==边界== 别忘" }]);
  document.querySelector("#notes-list").querySelector(".notes-card-open").click();
  await tick(); await tick();
  const pane = document.querySelector("#notes-pane-note");
  [...pane.querySelectorAll(".notes-action")].find((button) => button.textContent === "编辑").click();
  await tick(); await tick();
  const toggle = pane.querySelector(".notes-preview-toggle");
  const preview = pane.querySelector(".notes-edit-preview");
  assert.ok(toggle && preview);
  assert.equal(preview.hidden, true, "默认收着");
  toggle.click();
  assert.equal(preview.hidden, false);
  assert.equal(toggle.getAttribute("aria-pressed"), "true");
  assert.equal(preview.querySelector("mark").textContent, "边界", "荧光笔显示成真的荧光笔");
  pane.querySelector(".notes-edit-content").value = "> [!易错] 小心\n> 下标从 0 开始";
  await new Promise((resolve) => setTimeout(resolve, 800));
  assert.equal(preview.querySelector("mark"), null);
  assert.ok(preview.textContent.includes("下标从 0 开始"), "改了正文，预览跟着变");
  toggle.click();
  assert.equal(preview.hidden, true);
  assert.equal(toggle.textContent, "看效果");
});
