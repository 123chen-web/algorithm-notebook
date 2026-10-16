"use strict";

/* 错因词表点选（static/tags.js）：印章 chips、选/取消、至少一个的提示、verdict 下拉显隐、最小反例显示。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const TAXONOMY = {
  causes: [
    { code: "wrong_idea", label: "思路错误", group: "reasoning", group_label: "想错了", hint: "方向想错了" },
    { code: "boundary", label: "边界/特例", group: "implementation", group_label: "写错了", hint: "边界" },
    { code: "careless", label: "粗心/笔误", group: "implementation", group_label: "写错了", hint: "手滑" },
    { code: "other", label: "其他", group: "environment", group_label: "环境与其他", hint: "其他" },
  ],
  groups: [
    { code: "reasoning", label: "想错了" },
    { code: "implementation", label: "写错了" },
    { code: "environment", label: "环境与其他" },
  ],
  verdicts: [
    { code: "WA", label: "答案错误" },
    { code: "TLE", label: "超时" },
  ],
};

const TAGS = { tags: [], suggestions: [], limits: { per_mistake: 8, length: 20 } };

function setup() {
  const env = load(["tags.js"]);
  return env;
}

async function renderEditor(env, item) {
  const editor = env.window.TagEditor.render(item);
  env.document.body.append(editor);
  // tags.js 会请求 /api/tags 和 /api/error-taxonomy。
  for (const call of env.calls) {
    if (call.url === "/api/tags") env.respond(call, 200, TAGS);
    if (call.url === "/api/error-taxonomy") env.respond(call, 200, TAXONOMY);
  }
  await tick(); await tick();
  return editor;
}

const settle = async () => { await tick(); await tick(); };

test("taxonomy: chips are grouped and show labels not codes", async () => {
  const env = setup();
  const editor = await renderEditor(env, { id: 1, tags: [], pending_reason: false });
  const seals = editor.querySelectorAll(".tag-seal");
  assert.equal(seals.length, 4);
  assert.ok(seals[0].textContent.includes("思路错误"));
  assert.ok(!seals[0].textContent.includes("wrong_idea"));
  // 按大类分组。
  const titles = [...editor.querySelectorAll(".tag-group-title")].map((el) => el.textContent);
  assert.deepEqual(titles, ["想错了", "写错了", "环境与其他"]);
  // 「其他」保留在词表里。
  assert.ok([...seals].some((seal) => seal.textContent.includes("其他")));
});

test("taxonomy: clicking a seal selects it and PUTs codes", async () => {
  const env = setup();
  const item = { id: 2, tags: [], pending_reason: false };
  const editor = await renderEditor(env, item);
  const seal = [...editor.querySelectorAll(".tag-seal")].find((el) => el.textContent.includes("边界"));
  seal.click();
  await settle();
  const put = env.calls.find((call) => call.url === "/api/mistakes/2/tags" && call.init.method === "PUT");
  assert.ok(put);
  assert.deepEqual(JSON.parse(put.init.body), { tags: ["boundary"] });
  env.respond(put, 200, { tags: ["boundary"] });
  await settle();
  assert.deepEqual(item.tags, ["boundary"]);
  // 选中态 aria-pressed。
  assert.equal(editor.querySelectorAll('.tag-seal[aria-pressed="true"]').length, 1);
});

test("taxonomy: removing the last tag on a complete record shows a prompt", async () => {
  const env = setup();
  const item = { id: 3, tags: ["boundary"], pending_reason: false };
  const editor = await renderEditor(env, item);
  const seal = [...editor.querySelectorAll(".tag-seal")].find((el) => el.textContent.includes("边界"));
  assert.equal(seal.getAttribute("aria-pressed"), "true");
  seal.click(); // 取消最后一个
  await settle();
  // 没有发 PUT，状态栏给提示。
  assert.equal(env.calls.filter((call) => call.url === "/api/mistakes/3/tags").length, 0);
  assert.match(editor.querySelector(".tag-status").textContent, /至少保留 1 个/);
});

test("taxonomy: pending record allows removing the last tag", async () => {
  const env = setup();
  const item = { id: 4, tags: ["boundary"], pending_reason: true };
  const editor = await renderEditor(env, item);
  const seal = [...editor.querySelectorAll(".tag-seal")].find((el) => el.textContent.includes("边界"));
  seal.click();
  await settle();
  const put = env.calls.find((call) => call.url === "/api/mistakes/4/tags" && call.init.method === "PUT");
  assert.ok(put);
  assert.deepEqual(JSON.parse(put.init.body), { tags: [] });
});

test("taxonomy: chips() renders labels for list cards", async () => {
  const env = setup();
  await renderEditor(env, { id: 5, tags: [], pending_reason: false });
  const chips = env.window.TagEditor.chips(["boundary", "wrong_idea"]);
  assert.ok(chips.textContent.includes("边界/特例"));
  assert.ok(chips.textContent.includes("思路错误"));
  assert.ok(!chips.textContent.includes("boundary"));
});

test("taxonomy: label() maps code to Chinese name", async () => {
  const env = setup();
  await renderEditor(env, { id: 6, tags: [], pending_reason: false });
  assert.equal(env.window.TagEditor.label("boundary"), "边界/特例");
  assert.equal(env.window.TagEditor.label("unknown-code"), "unknown-code");
});

test("老毛病：有同错因的旧题时，标签下面列出题名，点一下打开那条；题名是纯文字", async () => {
  const env = setup();
  const opened = [];
  env.window.openMistake = (id) => { opened.push(id); return Promise.resolve(); };
  const editor = await renderEditor(env, { id: 9, tags: ["boundary"], pending_reason: false });
  const call = env.calls.find((entry) => entry.url === "/api/mistakes/9/same-cause");
  assert.ok(call, "有标签时去取老毛病");
  env.respond(call, 200, { groups: [{ tag: "boundary", total: 5, items: [
    { id: 3, problem_id: 2, title: "<b>二分</b>", description: "漏了 left == right" },
    { id: 4, problem_id: 3, title: "搜索插入位置", description: "" },
  ] }] });
  await settle();
  const box = editor.querySelector(".tag-recurrence");
  assert.equal(box.hidden, false);
  assert.match(box.textContent, /老毛病「边界\/特例」：你在 《<b>二分<\/b>》、《搜索插入位置》 等 5 道题里也是这样错的。/);
  assert.equal(editor.querySelector(".tag-recurrence b"), null, "题名不会变成 HTML");
  editor.querySelector(".tag-recurrence-link").click();
  assert.deepEqual(opened, [3]);
});

test("老毛病：没有同错因的旧题或请求失败时不显示；没选标签时不去请求", async () => {
  const env = setup();
  const editor = await renderEditor(env, { id: 9, tags: ["boundary"], pending_reason: false });
  env.respond(env.calls.find((entry) => entry.url === "/api/mistakes/9/same-cause"), 500, {});
  await settle();
  assert.equal(editor.querySelector(".tag-recurrence").hidden, true);
  const env2 = setup();
  await renderEditor(env2, { id: 10, tags: [], pending_reason: true });
  assert.equal(env2.calls.some((entry) => entry.url.includes("same-cause")), false);
});


test("老毛病“汇成笔记”：取最多 20 道同错因的题，拼成草稿放进笔记新建区；题名里的 [ ] | 不会弄坏链接", async () => {
  const env = setup();
  const views = [];
  const drafts = [];
  env.window.showView = (view) => { views.push(view); return Promise.resolve(); };
  env.window.Notes = { newFromText: (text) => { drafts.push(text); return Promise.resolve(true); } };
  const editor = await renderEditor(env, { id: 9, title: "x 的平方根", description: "右边界写成 n", tags: ["boundary"], pending_reason: false });
  env.respond(env.calls.find((entry) => entry.url === "/api/mistakes/9/same-cause"), 200, { groups: [{ tag: "boundary", total: 1, items: [
    { id: 3, problem_id: 2, title: "二分[左闭]|右开", description: "漏了\nleft == right" },
  ] }] });
  await settle();
  editor.querySelector(".tag-recurrence-gather").click();
  await tick();
  const full = env.calls.find((entry) => entry.url === "/api/mistakes/9/same-cause?limit=20");
  assert.ok(full, "汇成笔记时多取一些");
  env.respond(full, 200, { groups: [{ tag: "boundary", total: 2, items: [
    { id: 3, problem_id: 2, title: "二分[左闭]|右开", description: "漏了\nleft == right" },
    { id: 4, problem_id: 3, title: "搜索插入位置", description: "" },
  ] }] });
  await settle(); await settle();
  assert.deepEqual(views, ["notes"]);
  assert.equal(drafts.length, 1);
  assert.equal(drafts[0], [
    "# 我的老毛病：边界/特例",
    "",
    "「边界/特例」这类错，我在 3 道题里都犯过：",
    "",
    "- [[题:x 的平方根]]：右边界写成 n",
    "- [[题:二分 左闭 右开]]：漏了 left == right",
    "- [[题:搜索插入位置]]",
    "",
    "## 我的对策",
    "",
    "- 下次看到……，先……",
    "",
  ].join("\n"));
});
