"use strict";

/* 记笔记页（static/notes.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   纯函数直接调用；控制器挂在自动补齐的最小 DOM 上（harness 按 id 现造节点），请求由测试决定何时、怎样回应。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

const plain = (value) => JSON.parse(JSON.stringify(value));
const iso = (msAgo) => new Date(Date.now() - msAgo).toISOString();

const note = (id, extra = {}) => ({
  id, title: "", content: `笔记 ${id} 正文`, tags: [], problem_id: null, problem_title: "",
  pinned: false, created_at: iso(3600 * 1000), updated_at: iso(60 * 1000), ...extra,
});

/** 配好假 api 的 Notes 环境：请求全部记下来，由测试逐个回应。 */
function setup(envNotes = {}) {
  const env = load(["notes.js"]);
  const state = {
    user: { id: 7, username: "陈默" }, epoch: 1, view: "notes",
    confirms: [], confirmed: true, notified: [],
  };
  env.window.Notes.configure({
    async api(path, options = {}) {
      const response = await env.window.fetch(path, options);
      const body = await response.json();
      if (!response.ok) { const error = new Error(body.detail); error.status = response.status; throw error; }
      return body;
    },
    getUser: () => state.user,
    getEpoch: () => state.epoch,
    getView: () => state.view,
    confirm: (text) => { state.confirms.push(text); return state.confirmed; },
    notify: (text) => state.notified.push(text),
    ...envNotes,
  });
  return { env, state, Notes: env.window.Notes, document: env.document };
}

const lastCall = (env) => env.calls[env.calls.length - 1];
async function respondLast(env, status, body) {
  env.respond(lastCall(env), status, body);
  await tick(); await tick();
}

function markdown(Notes, source) {
  const escape = Notes.escapeHtml;
  function serialize(node) {
    if (node.nodeType === 3) return escape(node.textContent);
    if (node.tagName === "#FRAGMENT") return node.children.map(serialize).join("");
    const tag = node.tagName.toLowerCase();
    const attrs = Object.entries(node.attributes).map(([name, value]) => ` ${name}="${escape(value)}"`).join("");
    const content = node._text !== null ? escape(node.textContent) : node.children.map(serialize).join("");
    return `<${tag}${attrs}>${content}</${tag}>`;
  }
  return serialize(Notes.renderNoteMarkdown(source));
}

/* ───────────── 纯函数：renderNoteMarkdown 子集 ───────────── */

test("renderNoteMarkdown: 标题 / 粗体 / 斜体 / 行内代码 / 列表 / 链接", () => {
  const { Notes } = load(["notes.js"]).window;
  assert.equal(markdown(Notes, "# 一级"), "<h1>一级</h1>");
  assert.equal(markdown(Notes, "## 二级"), "<h2>二级</h2>");
  assert.equal(markdown(Notes, "### 三级"), "<h3>三级</h3>");
  assert.equal(markdown(Notes, "**粗体**"), "<p><strong>粗体</strong></p>");
  assert.equal(markdown(Notes, "*斜体*"), "<p><em>斜体</em></p>");
  assert.equal(markdown(Notes, "`code`"), "<p><code>code</code></p>");
  assert.equal(
    markdown(Notes, "- 第一\n- 第二\n\n正文"),
    "<ul><li>第一</li><li>第二</li></ul><p>正文</p>",
  );
  assert.equal(
    markdown(Notes, "[欧叶](https://example.com/x)"),
    '<p><a href="https://example.com/x" target="_blank" rel="noopener">欧叶</a></p>',
  );
  assert.equal(
    markdown(Notes, "```\nconst a = 1;\n```"),
    "<pre><code>const a = 1;</code></pre>",
  );
});

test("renderNoteMarkdown: 四级标题不渲染，行内代码里的星号不触发斜体", () => {
  const { Notes } = load(["notes.js"]).window;
  assert.equal(markdown(Notes, "#### 四级"), "<p>#### 四级</p>");
  assert.equal(markdown(Notes, "#无空格"), "<p>#无空格</p>");
  const codeStar = markdown(Notes, "`a*b`");
  assert.match(codeStar, /<code>a\*b<\/code>/);
  assert.doesNotMatch(codeStar, /<em>/);
});

test("renderNoteMarkdown: XSS 用例必须被中和", () => {
  const { Notes } = load(["notes.js"]).window;
  const script = markdown(Notes, "<script>alert(1)</script>");
  assert.doesNotMatch(script, /<script/);
  assert.match(script, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/);

  const img = markdown(Notes, '<img src=x onerror="alert(1)">');
  assert.doesNotMatch(img, /<img/);
  assert.match(img, /&lt;img/);

  for (const evil of [
    "[x](javascript:alert(1))",
    "[x](JaVaScRiPt:alert(1))",
    "[x](data:text/html,<script>alert(1)</script>)",
    "[x](vbscript:msgbox(1))",
  ]) {
    const html = markdown(Notes, evil);
    assert.doesNotMatch(html, /<a\s/, evil);
  }

  const fenced = markdown(Notes, "```\n<script>alert(1)</script>\n```");
  assert.doesNotMatch(fenced, /<script/);
  assert.match(fenced, /<pre><code>&lt;script&gt;/);

  // 大写协议的 http(s) 仍然是合法链接
  assert.match(markdown(Notes, "[x](HTTP://example.com)"), /<a href="HTTP:\/\/example\.com"/);
});

test("Markdown DOM: 属性逃逸、危险链接、伪造代码占位符保持安全", () => {
  const { Notes } = load(["notes.js"]).window;
  const dom = Notes.renderNoteMarkdown('[x](https://example.com/"onmouseover="alert(1))\n[x](javascript:alert(1))\n\u00000\u0000\n\u00010\u0001\n<img onerror=alert(1)>');
  const link = dom.querySelector("a");
  assert.equal(link.getAttribute("rel"), "noopener");
  assert.equal(link.getAttribute("target"), "_blank");
  assert.equal(link.getAttribute("onmouseover"), null);
  assert.equal(dom.querySelectorAll("a").length, 1);
  assert.equal(dom.querySelector("img"), null);
  assert.equal(dom.querySelector("script"), null);
  assert.ok(dom.textContent.includes("\u00000\u0000"));
  assert.ok(dom.textContent.includes("\u00010\u0001"));
});

/* ───────────── 纯函数：其他 ───────────── */

test("parseTags: 逗号 / 空格 / 全角逗号 / 顿号分隔，去重保序", () => {
  const { Notes } = load(["notes.js"]).window;
  assert.deepEqual(plain(Notes.parseTags("算法, 二分 动态规划，贪心、算法")), ["算法", "二分", "动态规划", "贪心"]);
  assert.deepEqual(plain(Notes.parseTags("  ")), []);
  assert.deepEqual(plain(Notes.parseTags(null)), []);
});

test("noteTitle: 有标题用标题，否则取正文首行前 30 字", () => {
  const { Notes } = load(["notes.js"]).window;
  assert.equal(Notes.noteTitle({ title: "  我的标题  ", content: "正文" }), "我的标题");
  const longLine = "第一行" + "很长".repeat(20);
  assert.equal(Notes.noteTitle({ title: "", content: `\n${longLine}\n第二行` }), longLine.slice(0, 30));
  assert.equal(Notes.noteTitle({ title: "", content: "" }), "");
});

test("buildNotesQuery: 只带非默认值，limit 钳在 1..100", () => {
  const { Notes } = load(["notes.js"]).window;
  assert.equal(Notes.buildNotesQuery({}), "/api/notes");
  assert.equal(Notes.buildNotesQuery({ q: " 二分 " }), "/api/notes?q=%E4%BA%8C%E5%88%86");
  assert.equal(Notes.buildNotesQuery({ tag: "算法", problemId: 7, offset: 20 }), "/api/notes?tag=%E7%AE%97%E6%B3%95&problem_id=7&offset=20");
  assert.equal(Notes.buildNotesQuery({ limit: 999 }), "/api/notes?limit=100");
});

test("collectTags: 按出现次数降序，次数相同按标签名升序", () => {
  const { Notes } = load(["notes.js"]).window;
  const tags = Notes.collectTags([
    { tags: ["二分", "算法"] }, { tags: ["算法"] }, { tags: ["贪心", "二分"] }, { tags: [] },
  ]);
  assert.deepEqual(plain(tags), [
    { tag: "二分", count: 2 }, { tag: "算法", count: 2 }, { tag: "贪心", count: 1 },
  ]);
});

test("noteTime: 刚刚 / 分钟 / 小时 / 天 / 日期边界", () => {
  const { Notes } = load(["notes.js"]).window;
  const now = Date.now();
  const at = (msAgo) => Notes.noteTime(new Date(now - msAgo).toISOString(), now);
  assert.equal(at(0), "刚刚");
  assert.equal(at(59 * 1000), "刚刚");
  assert.equal(at(60 * 1000), "1 分钟前");
  assert.equal(at(3 * 3600 * 1000), "3 小时前");
  assert.equal(at(2 * 86400 * 1000), "2 天前");
  assert.match(at(400 * 86400 * 1000), /^\d{4}-\d{2}-\d{2}$/);
  assert.equal(Notes.noteTime("不是时间", now), "");
});

/* ───────────── 控制器：composer 保存流程 ───────────── */

test("composer: 空内容点保存 → 行内提示，不发请求", async () => {
  const { env, document } = setup();
  await loadEmpty(env);
  const callsBefore = env.calls.length;
  document.querySelector("#notes-content").value = "   ";
  document.querySelector("#notes-save").click();
  await tick();
  assert.equal(env.calls.length, callsBefore);
  assert.equal(document.querySelector("#notes-composer-notice").textContent, "至少写点什么");
});

test("composer: 保存成功 → 断言 POST body，插到列表顶部并清空表单", async () => {
  const { env, document, Notes } = setup();
  await loadEmpty(env);
  document.querySelector("#notes-content").value = "  hello **world**  ";
  document.querySelector("#notes-tags-input").value = "算法, 二分";
  document.querySelector("#notes-save").click();
  await tick();
  const call = lastCall(env);
  assert.equal(call.url, "/api/notes");
  assert.equal(call.init.method, "POST");
  assert.deepEqual(JSON.parse(call.init.body), { content: "hello **world**", tags: ["算法", "二分"] });
  const created = note(101, { content: "hello **world**", tags: ["算法", "二分"] });
  await respondLast(env, 201, created);
  assert.equal(Notes.snapshot().count, 1);
  assert.equal(document.querySelector("#notes-content").value, "");
  assert.equal(document.querySelector("#notes-tags-input").value, "");
  // 正文由 DOM 节点构造；检查实际节点内容与样式结构。
  const card = document.querySelector("#notes-list").querySelector(".notes-card");
  // N5：无显式标题、且正文首行不是标题块时，卡片头部不再重复显示“标题=正文第一句”。
  assert.equal(card.querySelector(".notes-card-title strong"), null);
  const body = card.querySelector(".notes-body");
  assert.equal(body.textContent, "hello world");
  assert.equal(body.querySelector("strong").textContent, "world");
  assert.equal(body.querySelector("script"), null);
});

test("composer: 关联题目一起提交；后端 422 时行内显示中文错误", async () => {
  const { env, document, Notes } = setup();
  await loadWith(env, [{ id: 7, title: "二分查找" }], { notes: [], total: 0 });
  document.querySelector("#notes-content").value = "记一道题";
  document.querySelector("#notes-problem").value = "7";
  document.querySelector("#notes-save").click();
  await tick();
  assert.deepEqual(JSON.parse(lastCall(env).init.body), { content: "记一道题", problem_id: 7 });
  await respondLast(env, 422, { detail: "内容不能为空" });
  assert.equal(Notes.snapshot().count, 0);
  assert.equal(document.querySelector("#notes-composer-notice").textContent, "内容不能为空");
});

/* ───────────── 控制器：列表 / 筛选 / 更多 ───────────── */

/** 真实流程：先进笔记页（load 绑定 DOM）再操作 composer。 */
async function loadEmpty(env) {
  await loadWith(env, [], { notes: [], total: 0 });
}

async function loadWith(env, problems, notesPage) {
  const loading = env.window.Notes.load();
  await tick();
  env.respond(lastCall(env), 200, { problems });
  await tick(); await tick();
  env.respond(lastCall(env), 200, notesPage);
  await loading;
}

test("load: 先拉题目下拉再拉列表；标签 chip 点击 → 按该标签重新请求", async () => {
  const { env, document, Notes } = setup();
  const problems = [{ id: 7, title: "二分查找", zone: "算法" }];
  const first = { notes: [note(1, { tags: ["算法", "二分"] }), note(2, { tags: ["贪心"] })], total: 2 };
  await loadWith(env, problems, first);
  assert.equal(env.calls[0].url, "/api/problems?limit=30");
  assert.equal(env.calls[1].url, "/api/notes");
  assert.equal(Notes.snapshot().count, 2);
  assert.equal(document.querySelector("#notes-problem").querySelectorAll("option").length, 2); // 不关联 + 1 道题

  const chip = document.querySelector("#notes-list").querySelector('[data-tag="算法"]');
  assert.ok(chip, "列表里有算法标签 chip");
  chip.click();
  await tick();
  assert.match(lastCall(env).url, /tag=%E7%AE%97%E6%B3%95/);
  await respondLast(env, 200, { notes: [note(1, { tags: ["算法", "二分"] })], total: 1 });
  assert.equal(Notes.snapshot().count, 1);
  assert.equal(Notes.snapshot().activeTag, "算法");
});

test("标签筛选行：从已加载笔记收集标签，点击切换筛选", async () => {
  const { env, document, Notes } = setup();
  await loadWith(env, [], { notes: [note(1, { tags: ["算法"] }), note(2, { tags: ["算法", "贪心"] })], total: 2 });
  const rowChips = [...document.querySelector("#notes-tag-row").querySelectorAll(".notes-chip")];
  assert.deepEqual(rowChips.map((chip) => chip.textContent), ["全部", "算法 2", "贪心 1"]);
  rowChips[2].click(); // 贪心 1
  await tick();
  assert.match(lastCall(env).url, /tag=%E8%B4%AA%E5%BF%83/);
  await respondLast(env, 200, { notes: [note(2, { tags: ["算法", "贪心"] })], total: 1 });
  assert.equal(Notes.snapshot().activeTag, "贪心");
});

test("openWithProblem: load 之前调用不丢预填，load 后消费并选中题目", async () => {
  const { env, document, Notes } = setup();
  Notes.openWithProblem(42, "最长递增子序列");
  assert.deepEqual(plain(Notes.snapshot().pendingPrefill), { id: 42, title: "最长递增子序列" });
  await loadWith(env, [{ id: 7, title: "二分查找" }], { notes: [], total: 0 });
  assert.equal(Notes.snapshot().pendingPrefill, null);
  const select = document.querySelector("#notes-problem");
  assert.equal(select.value, "42"); // 不在 30 条内也补了选项
  assert.ok([...select.querySelectorAll("option")].some((option) => option.value === "42"));
});

test("置顶：PUT pinned 并按置顶排序；删除：confirm 后调 DELETE 并从列表移除", async () => {
  const { env, document, state, Notes } = setup();
  // 显式固定 updated_at：note() 默认用 Date.now() 生成秒内毫秒时间戳，高并发跑全套件时
  // 两次构造可能跨毫秒，导致按 updated_at 降序排序后卡片顺序非确定、置顶请求落到 note 2。
  await loadWith(env, [], { notes: [
    note(1, { pinned: false, updated_at: iso(30 * 1000) }),
    note(2, { pinned: false, updated_at: iso(60 * 1000) }),
  ], total: 2 });

  const cards = document.querySelector("#notes-list").querySelectorAll(".notes-card");
  const pinButton = [...cards[0].querySelectorAll(".notes-action")].find((button) => button.textContent === "置顶");
  pinButton.click();
  await tick();
  assert.equal(lastCall(env).url, "/api/notes/1");
  assert.equal(lastCall(env).init.method, "PUT");
  assert.deepEqual(JSON.parse(lastCall(env).init.body), { pinned: true });
  await respondLast(env, 200, note(1, { pinned: true }));
  const firstCard = document.querySelector("#notes-list").querySelector(".notes-card");
  assert.ok(firstCard.classList.contains("is-pinned"));

  const deleteButton = [...firstCard.querySelectorAll(".notes-action")].find((button) => button.textContent === "删除");
  deleteButton.click();
  await tick();
  assert.deepEqual(state.confirms, ["确定删除这条笔记吗？删除后无法恢复。"]);
  assert.equal(lastCall(env).url, "/api/notes/1");
  assert.equal(lastCall(env).init.method, "DELETE");
  await respondLast(env, 200, { ok: true });
  assert.equal(Notes.snapshot().count, 1);
  assert.ok(state.notified.includes("笔记已删除。"));
});

test("删除：confirm 点取消则不发请求", async () => {
  const { env, document, state, Notes } = setup({ confirm: () => { state.confirms.push("x"); return false; } });
  await loadWith(env, [], { notes: [note(1)], total: 1 });
  const callsBefore = env.calls.length;
  const deleteButton = [...document.querySelector("#notes-list").querySelector(".notes-card").querySelectorAll(".notes-action")]
    .find((button) => button.textContent === "删除");
  deleteButton.click();
  await tick();
  assert.equal(env.calls.length, callsBefore);
  assert.equal(Notes.snapshot().count, 1);
});

test("加载更多：offset 累加追加；登出代次变化后迟到响应被丢弃", async () => {
  const { env, document, state, Notes } = setup();
  const three = [note(1), note(2), note(3)];
  await loadWith(env, [], { notes: three.slice(0, 2), total: 3 });
  assert.equal(document.querySelector("#notes-more").hidden, false);
  document.querySelector("#notes-more").click();
  await tick();
  assert.match(lastCall(env).url, /offset=2/);
  await respondLast(env, 200, { notes: three.slice(2), total: 3 });
  assert.equal(Notes.snapshot().count, 3);
  assert.equal(document.querySelector("#notes-more").hidden, true);

  // 换用户后迟到的响应不污染列表
  const stale = Notes.load();
  state.user = { id: 9, username: "李四" };
  await tick();
  env.respond(lastCall(env), 200, { problems: [] });
  await tick(); await tick();
  env.respond(lastCall(env), 200, { notes: [note(999)], total: 1 });
  await stale;
  assert.equal(Notes.snapshot().count, 3);
});

/* 异步回归：请求的归属、筛选顺序和提交时的草稿都必须保持明确。 */
const actionNamed = (card, label) => [...card.querySelectorAll(".notes-action")]
  .find((button) => button.textContent === label);
const cardTitles = (document) => document.querySelector("#notes-list")
  .querySelectorAll(".notes-card-title strong").map((title) => title.textContent);

for (const operation of ["保存", "置顶", "删除", "编辑"]) {
  for (const outcome of ["success", "failure"]) {
    test(`异步归属：${operation} ${outcome} 在 reset 换账号后不修改新用户状态`, async () => {
      const { env, document, state, Notes } = setup();
      await loadWith(env, [], { notes: [note(1, { title: "旧用户笔记" })], total: 1 });
      let oldEditor = null;
      let oldEditSave = null;
      if (operation === "保存") {
        document.querySelector("#notes-content").value = "旧用户提交";
        document.querySelector("#notes-save").click();
      } else {
        const card = document.querySelector("#notes-list").querySelector(".notes-card");
        if (operation === "编辑") {
          actionNamed(card, "编辑").click();
          oldEditor = card.querySelector(".notes-editor");
          oldEditor.querySelector(".notes-edit-content").value = "旧用户修改";
          oldEditSave = actionNamed(oldEditor, "保存");
          oldEditSave.click();
        } else actionNamed(card, operation).click();
      }
      await tick();
      const oldRequest = lastCall(env);
      assert.equal(oldRequest.init.method, operation === "保存" ? "POST" : operation === "删除" ? "DELETE" : "PUT");

      Notes.reset();
      state.epoch += 1;
      state.user = { id: 9, username: "新用户" };
      await loadWith(env, [], { notes: [note(90, { title: "新用户笔记" })], total: 1 });
      document.querySelector("#notes-content").value = "新用户在途草稿";
      document.querySelector("#notes-save").click();
      await tick();
      const newRequest = lastCall(env);
      assert.equal(newRequest.init.method, "POST");
      assert.equal(document.querySelector("#notes-save").disabled, true);
      const newCard = document.querySelector("#notes-list").querySelector(".notes-card");
      actionNamed(newCard, "编辑").click();
      const newEditor = newCard.querySelector(".notes-editor");
      newEditor.querySelector(".notes-edit-content").value = "新用户正在编辑的草稿";
      const before = plain(Notes.snapshot());
      const notificationCount = state.notified.length;
      const noticeBefore = document.querySelector("#notes-composer-notice").textContent;
      const oldErrorBefore = oldEditor?.querySelector(".notes-notice").textContent;
      env.respond(oldRequest, outcome === "success" ? 200 : 422,
        outcome === "failure" ? { detail: "旧用户错误不应出现" }
          : operation === "删除" ? { ok: true } : note(1, { title: "迟到的旧用户内容", pinned: true }));
      await tick(); await tick();

      assert.deepEqual(plain(Notes.snapshot()), before);
      assert.deepEqual(cardTitles(document), ["新用户笔记"]);
      assert.ok(document.querySelector("#notes-list").querySelector(".notes-editor") === newEditor,
        "迟到响应不得重新渲染列表，丢弃新用户编辑草稿");
      assert.equal(newEditor.querySelector(".notes-edit-content").value, "新用户正在编辑的草稿");
      assert.equal(document.querySelector("#notes-content").value, "新用户在途草稿");
      assert.equal(document.querySelector("#notes-composer-notice").textContent, noticeBefore);
      assert.equal(document.querySelector("#notes-save").disabled, true, "旧请求 finally 不得启用新请求的按钮");
      assert.equal(state.notified.length, notificationCount);
      if (oldEditor) {
        assert.equal(oldEditor.querySelector(".notes-notice").textContent, oldErrorBefore);
        assert.equal(oldEditSave.disabled, true, "迟到错误不得修改已作废的编辑器");
      }
      assert.equal(lastCall(env), newRequest, "旧请求不得为新用户额外发起列表刷新");
      // 作废本测试剩余的新请求，避免悬挂操作参与其他测试。
      Notes.reset();
      env.respond(newRequest, 201, note(91));
      await tick(); await tick();
    });
  }
}

test("筛选乱序：后选标签的结果不被先选标签的迟到响应覆盖", async () => {
  const { env, document, Notes } = setup();
  await loadWith(env, [], { notes: [note(1, { tags: ["A", "B"] })], total: 1 });
  const chip = (name) => document.querySelector("#notes-tag-row").querySelector(`[data-tag="${name}"]`);
  chip("A").click();
  await tick();
  const older = lastCall(env);
  chip("B").click();
  await tick();
  const newer = lastCall(env);
  assert.match(older.url, /tag=A/);
  assert.match(newer.url, /tag=B/);
  env.respond(newer, 200, { notes: [note(20, { title: "B 的结果", tags: ["B"] })], total: 1 });
  await tick(); await tick();
  env.respond(older, 200, { notes: [note(10, { title: "A 的旧结果", tags: ["A"] })], total: 5 });
  await tick(); await tick();
  assert.equal(Notes.snapshot().activeTag, "B");
  assert.equal(Notes.snapshot().total, 1);
  assert.deepEqual(cardTitles(document), ["B 的结果"]);
});

test("筛选乱序：旧筛选的加载更多不追加到新筛选，也不提前释放 loading", async () => {
  const { env, document, Notes } = setup();
  await loadWith(env, [], { notes: [note(1, { tags: ["B"] })], total: 3 });
  const pendingMore = Notes.loadMore();
  await tick();
  const oldPage = lastCall(env);
  document.querySelector("#notes-tag-row").querySelector('[data-tag="B"]').click();
  await tick();
  const freshFilter = lastCall(env);
  env.respond(oldPage, 200, { notes: [note(2, { title: "旧分页" })], total: 3 });
  await pendingMore;
  assert.equal(Notes.snapshot().loading, true, "旧分页完成不能释放新筛选的 loading");
  env.respond(freshFilter, 200, { notes: [note(30, { title: "新筛选", tags: ["B"] })], total: 1 });
  await tick(); await tick();
  assert.deepEqual(cardTitles(document), ["新筛选"]);
  assert.equal(Notes.snapshot().total, 1);
});

test("筛选后保存：清空筛选时重新读取全部列表和总数", async () => {
  const { env, document, Notes } = setup();
  const matching = note(1, { title: "A 笔记", tags: ["A"] });
  const other = note(2, { title: "其他笔记", tags: ["B"] });
  await loadWith(env, [], { notes: [matching, other], total: 2 });
  document.querySelector("#notes-tag-row").querySelector('[data-tag="A"]').click();
  await tick();
  await respondLast(env, 200, { notes: [matching], total: 1 });
  document.querySelector("#notes-content").value = "新笔记";
  document.querySelector("#notes-save").click();
  await tick();
  const saveRequest = lastCall(env);
  const created = note(3, { title: "新笔记" });
  env.respond(saveRequest, 201, created);
  await tick(); await tick();
  const reload = lastCall(env);
  assert.notEqual(reload, saveRequest, "已筛选数据不能直接冒充全部列表");
  assert.equal(reload.url, "/api/notes");
  assert.notEqual(reload.init.method, "POST");
  env.respond(reload, 200, { notes: [created, matching, other], total: 3 });
  await tick(); await tick();
  assert.equal(Notes.snapshot().activeTag, "");
  assert.equal(Notes.snapshot().count, 3);
  assert.equal(Notes.snapshot().total, 3);
  assert.deepEqual(new Set(cardTitles(document)), new Set(["新笔记", "A 笔记", "其他笔记"]));
});

test("保存期间新草稿：提交响应不会清空用户随后输入的正文、标签和关联", async () => {
  const { env, document } = setup();
  await loadWith(env, [{ id: 7, title: "题目" }], { notes: [], total: 0 });
  document.querySelector("#notes-content").value = "这次提交";
  document.querySelector("#notes-tags-input").value = "已提交标签";
  document.querySelector("#notes-save").click();
  await tick();
  const saveRequest = lastCall(env);
  assert.equal(JSON.parse(saveRequest.init.body).content, "这次提交");
  document.querySelector("#notes-content").value = "用户随后输入的新草稿";
  document.querySelector("#notes-tags-input").value = "新草稿标签";
  document.querySelector("#notes-problem").value = "7";
  env.respond(saveRequest, 201, note(1, { content: "这次提交" }));
  await tick(); await tick();
  if (lastCall(env) !== saveRequest) {
    assert.equal(lastCall(env).url, "/api/notes");
    await respondLast(env, 200, { notes: [note(1, { content: "这次提交" })], total: 1 });
  }
  assert.equal(document.querySelector("#notes-content").value, "用户随后输入的新草稿");
  assert.equal(document.querySelector("#notes-tags-input").value, "新草稿标签");
  assert.equal(document.querySelector("#notes-problem").value, "7");
});

test("noteTitle: 首行的 # 标题、列表、引用符号不会出现在标题里", () => {
  const { Notes } = load(["notes.js"]).window;
  assert.equal(Notes.noteTitle({ title: "", content: "## 二分查找小结\n- 要点" }), "二分查找小结");
  assert.equal(Notes.noteTitle({ title: "", content: "- 第一条\n- 第二条" }), "第一条");
  assert.equal(Notes.noteTitle({ title: "", content: "1. 先排序" }), "先排序");
  assert.equal(Notes.noteTitle({ title: "", content: "> 重点 **提醒**" }), "重点 提醒");
});

test("noteTitle/cardHeading: 标题里的链接、互链、图片语法只保留可读文字", () => {
  const { Notes } = load(["notes.js"]).window;
  assert.equal(
    Notes.noteTitle({ title: "", content: "# [**手测标题**](https://example.com/note)\n正文" }),
    "手测标题");
  assert.equal(
    Notes.cardHeading({ title: "", content: "# 见 [[题:求极限]] 的推导" }),
    "见 求极限 的推导");
  assert.equal(
    Notes.cardHeading({ title: "", content: "# ![图](attachment:2) 配图笔记" }),
    "图 配图笔记");
});

test("renderNoteMarkdown: 编号列表和引用都能渲染", () => {
  const { Notes } = load(["notes.js"]).window;
  const root = Notes.renderNoteMarkdown("1. 第一步\n2. 第二步\n\n> 记住这句");
  const tags = root.children.map((node) => node.tagName.toLowerCase());
  assert.deepEqual(tags, ["ol", "blockquote"]);
  assert.equal(root.children[0].children.length, 2);
});
