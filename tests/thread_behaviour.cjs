"use strict";

/* 真正执行 Thread 的 DOM 渲染器和异步控制器；不连接服务或数据库。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const plain = (value) => JSON.parse(JSON.stringify(value));
const setup = (extra = {}) => load(["thread.js"], { extra });
const row = (id, floor, fields = {}) => ({
  id, floor, user_id: id, username: `作者${id}`, is_op: false,
  body: `正文${floor}`, created_at: `2026-10-03T0${floor}:00:00+00:00`,
  helpful_count: 0, viewer_helpful: false, ...fields,
});
const post = (id = 42) => ({ id, user_id: 10, body: "主帖", created_at: "2026-10-03T00:00:00Z", comments: [row(1, 2), row(2, 5)] });
const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled rejection"));

test("body: returns a fragment, preserves plain line breaks and treats HTML as text", () => {
  const { window } = setup();
  const body = window.Thread.renderBody("第一行\n第二行 <img src=x onerror=alert(1)>\n<script>bad()</script>");
  assert.match(body.textContent, /第一行\n第二行 <img src=x onerror=alert\(1\)>/);
  assert.match(body.textContent, /<script>bad\(\)<\/script>/);
  assert.equal(body.querySelectorAll("img, script").length, 0);
  assert.equal(body.tagName, "#FRAGMENT");
});

test("body: inline code consumes paired single backticks only within its line", () => {
  const { window } = setup();
  const body = window.Thread.renderBody("更新 `lo = mid + 1` 后\n孤立 `符号\n下行`");
  assert.equal(body.querySelectorAll("code").length, 1);
  assert.equal(body.querySelector("code").textContent, "lo = mid + 1");
  assert.match(body.textContent, /孤立 `符号\n下行`/);
});

test("body: fenced language, numbered lines and copy source exclude visual line numbers", () => {
  const { window } = setup();
  const body = window.Thread.renderBody("```c++\nint lo = 0;\nreturn lo;\n```");
  const figure = body.querySelector("figure.thread-code");
  assert.ok(figure);
  assert.match(figure.querySelector("figcaption").textContent, /^C\+\+/);
  assert.deepEqual(figure.querySelectorAll("span.l").map((line) => line.textContent), ["int lo = 0;", "return lo;"]);
  assert.equal(figure.querySelector("button").type, "button");
});

test("body: unclosed and empty fences render safely; unsupported language falls back to CODE", () => {
  const { window } = setup();
  const unclosed = window.Thread.renderBody("```py\nprint(1)\n尾行");
  assert.equal(unclosed.querySelectorAll("figure").length, 1);
  assert.deepEqual(unclosed.querySelectorAll("span.l").map((line) => line.textContent), ["print(1)", "尾行"]);
  for (const source of ["```\n```", "```bad language\n```"]) {
    const body = window.Thread.renderBody(source);
    assert.equal(body.querySelectorAll("figure.thread-code").length, 1);
    assert.match(body.querySelector("figcaption").textContent, /^CODE/);
    assert.equal(body.querySelector("pre code").textContent, "");
  }
});

test("body: mention matching normalizes case and NFKC but code never becomes a mention", () => {
  const { window } = setup();
  const body = window.Thread.renderBody("@ALICE @李同学 `@alice`\n```js\n@alice\n```", { mentionMe: "ａｌｉｃｅ" });
  assert.deepEqual(body.querySelectorAll(".thread-mention").map((item) => item.textContent), ["@ALICE", "@李同学"]);
  assert.equal(body.querySelectorAll(".thread-mention.is-me").length, 1);
});

test("body: contiguous numbered and bullet lists become native lists with inline code", () => {
  const { window } = setup();
  const body = window.Thread.renderBody("1. 第一个\n2) 第二个 `mid`\n3、第三个\n\n- 左边\n* 右边\n• 边界\n普通行");
  assert.equal(body.querySelectorAll("ol").length, 1);
  assert.equal(body.querySelectorAll("ul").length, 1);
  assert.equal(body.querySelectorAll("ol li").length, 3);
  assert.equal(body.querySelectorAll("ul li").length, 3);
  assert.equal(body.querySelector("ol code").textContent, "mid");
  assert.match(body.textContent, /普通行/);
});

test("body: CRLF normalizes, huge lines and blank input do not fail", () => {
  const { window } = setup();
  assert.equal(window.Thread.renderBody("a\r\nb").textContent, "a\nb");
  assert.equal(window.Thread.renderBody("x".repeat(2000)).textContent, "x".repeat(2000));
  assert.equal(window.Thread.renderBody("").textContent, "");
  assert.equal(window.Thread.renderBody(" \n\t ").textContent.trim(), "");
});

test("code counting: multiple and unclosed fences count, inline backticks and indented text do not", () => {
  const { Thread } = setup().window;
  assert.equal(Thread.countCode("`inline`\n普通"), 0);
  assert.equal(Thread.countCode("```py\nx\n```\n```\ny"), 2);
  assert.equal(Thread.hasCode("```\n```"), true);
  assert.equal(Thread.hasCode("文本里 ``` 只是文字"), false);
});

test("arrange: sort/filter intersections preserve source and stable floors, with helpful ties ascending", () => {
  const { Thread } = setup().window;
  const comments = [row(1, 1, { is_op: true, body: "@ME\n```py\nx\n```", helpful_count: 2 }),
    row(2, 3, { body: "@me", helpful_count: 8 }), row(3, 5, { is_op: true, body: "```\ny\n```", helpful_count: 2 }),
    row(4, 9, { body: "@me\n```js\nz\n```", helpful_count: 2 })];
  const before = plain(comments);
  const me = { id: 99, username: "Ｍｅ" };
  assert.deepEqual(plain(Thread.arrange(comments, { order: "earliest" }).map((item) => item.floor)), [1, 3, 5, 9]);
  assert.deepEqual(plain(Thread.arrange(comments, { order: "latest" }).map((item) => item.floor)), [9, 5, 3, 1]);
  assert.deepEqual(plain(Thread.arrange(comments, { order: "helpful" }).map((item) => item.floor)), [3, 1, 5, 9]);
  assert.deepEqual(plain(Thread.arrange(comments, { onlyOp: true, codeOnly: true, mentionOnly: true, me }).map((item) => item.floor)), [1]);
  assert.deepEqual(plain(Thread.arrange(comments, { order: "latest", codeOnly: true, mentionOnly: true, me }).map((item) => item.floor)), [9, 1]);
  assert.deepEqual(plain(comments), before);
});

test("mentionsMe: replies to my floor or normalized body mention match, unrelated names do not", () => {
  const { Thread } = setup().window;
  const comments = [row(1, 1, { user_id: 99 }), row(2, 3, { reply_to_id: 1 })];
  const me = { id: 99, username: "ＭＥ" };
  assert.equal(Thread.mentionsMe(comments[1], comments, me), true);
  assert.equal(Thread.mentionsMe(row(3, 5, { body: "谢谢 @me" }), comments, me), true);
  assert.equal(Thread.mentionsMe(row(3, 5, { body: "谢谢 @metoo" }), comments, me), false);
  assert.equal(Thread.mentionsMe(row(3, 5, { reply_to_id: 88 }), comments, me), false);
});

test("stats: counts visible authors uniquely, code in post/comments, and newest comment activity", () => {
  const { Thread } = setup().window;
  const value = post();
  value.body = "```cpp\nx\n```";
  value.comments = [row(1, 2, { user_id: 10, body: "```py\ny\n```" }), row(2, 5, { user_id: 20 }), row(3, 7, { user_id: 20 })];
  assert.deepEqual(plain(Thread.stats(value)), { replies: 3, participants: 2, codeBlocks: 2, lastActivity: value.comments[2].created_at });
  assert.equal(Thread.stats({ ...value, comments: [] }).lastActivity, value.created_at);
});

test("excerpt: collapses whitespace, replaces fenced code, retains inline code and truncates emojis safely", () => {
  const { Thread } = setup().window;
  assert.equal(Thread.excerpt("先\r\n  定义 `lo`\n```cpp\nsecret();\n```\n 再检查"), "先 定义 `lo` 〈代码〉 再检查");
  assert.doesNotMatch(Thread.excerpt("```py\nprivate()"), /private/);
  const excerpt = Thread.excerpt("a👩‍💻b", 2);
  assert.ok(excerpt.endsWith("…"));
  assert.doesNotMatch(excerpt, /[\u200d\ud800-\udfff]$/);
  assert.equal(Thread.excerpt(""), "");
});

test("map: evenly spaced markers retain classification and floor gaps, selects last top at 40%", () => {
  const { Thread } = setup().window;
  const items = [row(1, 1, { is_op: true }), row(2, 3, { body: "```\nx\n```" }), row(3, 8, { is_accepted: true }), row(4, 9)];
  const model = Thread.mapModel(items, [{ top: -200, bottom: 100 }, { top: 110, bottom: 260 }, { top: 350, bottom: 600 }, { top: 650, bottom: 900 }], 800);
  assert.equal(model.hidden, false);
  assert.equal(model.total, 4);
  assert.deepEqual(plain(model.ticks.map((item) => item.position)), [0, 1 / 3, 2 / 3, 1]);
  assert.equal(model.ticks[0].isOp, true);
  assert.equal(model.ticks[1].hasCode, true);
  assert.equal(model.ticks[2].isAccepted, true);
  assert.equal(model.currentIndex, 1);
  assert.equal(model.currentFloor, 3);
  assert.equal(model.firstIndex, 0);
  assert.equal(model.lastIndex, 3);
  assert.ok(model.thumb.top >= 0 && model.thumb.height > 0 && model.thumb.top + model.thumb.height <= 1);
});

test("map: thumb tracks viewport subset and fewer than three items hide enhancement", () => {
  const { Thread } = setup().window;
  const items = [row(1, 1), row(2, 4), row(3, 9), row(4, 10), row(5, 12)];
  const middle = Thread.mapModel(items, [{ top: -800, bottom: -600 }, { top: -300, bottom: -50 }, { top: 20, bottom: 240 }, { top: 800, bottom: 1000 }, { top: 1500, bottom: 1700 }], 600);
  assert.equal(middle.currentFloor, 9);
  assert.equal(middle.firstIndex, 2);
  assert.equal(middle.lastIndex, 2);
  assert.ok(middle.thumb.top > 0 && middle.thumb.height < 1);
  assert.equal(Thread.mapModel(items.slice(0, 2), [], 600).hidden, true);
  assert.equal(Thread.mapModel([], [], 600).hidden, true);
});

test("keyboard: editable targets, modifiers, dialogs and hidden details all block navigation", () => {
  const { window: { Thread }, document } = setup();
  const options = { detailVisible: true, dialogOpen: false };
  for (const tagName of ["INPUT", "TEXTAREA", "SELECT"]) {
    assert.equal(Thread.keyboardAllowed({ key: "j", target: document.createElement(tagName) }, options), false);
  }
  assert.equal(Thread.keyboardAllowed({ key: "j", target: { tagName: "DIV", isContentEditable: true } }, options), false);
  for (const flag of ["ctrlKey", "metaKey", "altKey"]) assert.equal(Thread.keyboardAllowed({ key: "j", [flag]: true }, options), false);
  assert.equal(Thread.keyboardAllowed({ key: "j" }, { ...options, dialogOpen: true }), false);
  assert.equal(Thread.keyboardAllowed({ key: "j" }, { ...options, detailVisible: false }), false);
  assert.equal(Thread.keyboardAllowed({ key: "j", target: { tagName: "ARTICLE" } }, options), true);
});

function summaryEnv() {
  const env = setup();
  const identity = { postId: 42, sessionEpoch: 1, userId: 10, visible: true };
  const calls = [], states = [];
  const controller = env.window.Thread.createSummaryController({
    api(url, options = {}) { const call = { url, options, ...deferred() }; calls.push(call); return call.promise; },
    getIdentity: () => ({ ...identity }), onState: (state) => states.push(plain(state)),
  });
  return { ...env, identity, calls, states, controller };
}
const summary = (extra = {}) => ({ tldr: "结论", points: [{ text: "边界约定", floors: [2, 5, 999, "2"] }], open_questions: ["边界还有什么？"], generated_at: "2026-10-03T04:00:00Z", comment_count: 2, stale: false, ...extra });

test("summary: cache GET stays free, idle -> generating -> result, valid floor whitelist and cached flag", async () => {
  const env = summaryEnv();
  const value = post();
  const initial = env.controller.load(value);
  assert.equal(env.calls[0].url, "/api/posts/42/summary");
  assert.equal(env.calls[0].options.method || "GET", "GET");
  env.calls[0].resolve({ summary: null }); await initial;
  assert.equal(env.controller.getState().phase, "idle");
  const generated = env.controller.generate(value);
  assert.equal(env.controller.getState().phase, "generating");
  assert.equal(env.calls[1].options.method, "POST");
  env.calls[1].resolve({ summary: summary(), cached: true }); await generated;
  assert.equal(env.controller.getState().phase, "result");
  assert.equal(env.controller.getState().cached, true);
  assert.deepEqual(plain(env.controller.getState().summary.points[0].floors), [2, 5]);
  assert.equal(env.controller.getState().summary.tldr, "结论");
});

test("summary: 429 preserves Chinese detail and can retry; stale results remain visible", async () => {
  const env = summaryEnv();
  const generated = env.controller.generate(post());
  env.calls[0].reject(Object.assign(new Error("HTTP 429"), { status: 429, detail: "本月 AI 额度已用完" })); await generated;
  assert.equal(env.controller.getState().phase, "error");
  assert.equal(env.controller.getState().error, "本月 AI 额度已用完");
  const retry = env.controller.generate(post());
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve({ summary: summary({ stale: true }), cached: false }); await retry;
  assert.equal(env.controller.getState().phase, "result");
  assert.equal(env.controller.getState().summary.stale, true);
});

test("summary: duplicate clicks issue one request while pending", async () => {
  const env = summaryEnv();
  const first = env.controller.generate(post());
  const duplicate = env.controller.generate(post());
  assert.equal(env.calls.length, 1);
  env.calls[0].resolve({ summary: summary() });
  await Promise.all([first, duplicate]);
  assert.equal(env.controller.getState().phase, "result");
});

for (const [label, change] of [
  ["switching threads", (identity) => { identity.postId = 43; }],
  ["returning to list", (identity) => { identity.visible = false; }],
  ["sign-out and re-login as same account", (identity) => { identity.sessionEpoch += 2; }],
  ["another account", (identity) => { identity.userId = 20; }],
]) {
  test(`summary: response after ${label} is discarded`, async () => {
    const env = summaryEnv();
    const pending = env.controller.load(post());
    const before = env.states.length;
    change(env.identity);
    env.calls[0].resolve({ summary: summary({ tldr: "旧私密要点" }) }); await pending;
    assert.equal(env.states.length, before);
    assert.notEqual(env.controller.getState().summary?.tldr, "旧私密要点");
  });
}

test("summary: reset invalidates old sequence so its answer cannot overwrite a newer result", async () => {
  const env = summaryEnv();
  const slow = env.controller.load(post());
  env.controller.reset();
  const fast = env.controller.load(post());
  env.calls[1].resolve({ summary: summary({ tldr: "新" }) }); await fast;
  env.calls[0].resolve({ summary: summary({ tldr: "旧" }) }); await slow;
  assert.equal(env.controller.getState().summary.tldr, "新");
});

test("summary: loading a different identity immediately clears the old cached result", async () => {
  const env = summaryEnv();
  const first = env.controller.load(post());
  env.calls[0].resolve({ summary: summary({ tldr: "A 的私密讨论" }) }); await first;
  env.identity.postId = 43;
  const next = env.controller.load(post(43));
  assert.equal(env.controller.getState().phase, "loading");
  assert.equal(env.controller.getState().summary, null);
  env.calls[1].resolve({ summary: null }); await next;
  assert.equal(env.controller.getState().phase, "idle");
});

for (const [label, response] of [["accepted", { accepted_comment_id: 5 }], ["helpful", { helpful_count: 12, viewer_helpful: true }]]) {
  test(`mutation ${label}: disables pending button, ignores double-click, forwards server values and recovers`, async () => {
    const { window, document } = setup();
    const button = document.createElement("button");
    const pending = deferred(); let requests = 0; let value = null;
    const options = { button, request: () => { requests++; return pending.promise; }, onSuccess: (body) => { value = body; }, onError: () => assert.fail("unexpected failure"), isCurrent: () => true };
    const first = window.Thread.performMutation(options);
    assert.equal(button.disabled, true);
    assert.equal(await window.Thread.performMutation(options), false);
    assert.equal(requests, 1);
    pending.resolve(response); assert.equal(await first, true);
    assert.deepEqual(value, response);
    assert.equal(button.disabled, false);
  });
}

test("mutation: detail errors restore controls; outdated success never mutates current post", async () => {
  const { window, document } = setup();
  const button = document.createElement("button"); let error = null;
  assert.equal(await window.Thread.performMutation({ button, request: async () => { throw new Error("不能采纳自己的评论"); }, onError: (message) => { error = message; }, isCurrent: () => true }), false);
  assert.equal(error, "不能采纳自己的评论");
  assert.equal(button.disabled, false);
  const pending = deferred(); let current = true; let changes = 0;
  const action = window.Thread.performMutation({ button, request: () => pending.promise, onSuccess: () => changes++, onError: () => assert.fail("unexpected"), isCurrent: () => current });
  current = false; pending.resolve({ helpful_count: 30 }); await action;
  assert.equal(changes, 0);
  assert.equal(button.disabled, false);
});

function clipboardEnv(writeText, fallback = true) {
  const selection = { ranges: [], removeAllRanges() { this.ranges = []; }, addRange(range) { this.ranges.push(range); } };
  const env = setup({ navigator: { clipboard: { writeText } }, getSelection: () => selection });
  const copies = [];
  env.document.createRange = () => ({ selectNodeContents(node) { this.node = node; } });
  env.document.execCommand = (command) => { copies.push(command); return fallback; };
  const rendered = env.window.Thread.renderBody("```\nx\n```");
  env.document.body.append(rendered);
  const button = env.document.querySelector(".thread-code button");
  const messages = [];
  return { ...env, button, copies, messages, selection };
}

test("copy: clipboard success receives raw code and announces copied status", async () => {
  const written = [];
  const env = clipboardEnv(async (text) => { written.push(text); });
  assert.equal(await env.window.Thread.copyCode("x\n\ny", env.button, (text) => env.messages.push(text)), true);
  assert.deepEqual(written, ["x\n\ny"]);
  assert.equal(env.copies.length, 0);
  assert.equal(env.button.textContent, "已复制 ✓");
  assert.match(env.messages.join(" "), /已复制/);
});

test("copy: clipboard denial uses text selection + execCommand, and both failures report manual fallback", async () => {
  const fail = async () => { throw new Error("denied"); };
  const env = clipboardEnv(fail);
  assert.equal(await env.window.Thread.copyCode("秘密代码", env.button, (text) => env.messages.push(text)), true);
  assert.deepEqual(env.copies, ["copy"]);
  assert.equal(env.document.querySelectorAll("textarea").length, 0, "temporary copy node is cleaned up");
  const denied = clipboardEnv(fail, false);
  assert.equal(await denied.window.Thread.copyCode("x", denied.button, (text) => denied.messages.push(text)), false);
  assert.equal(denied.messages.at(-1), "复制失败，请手动选择");
  assert.equal(denied.button.textContent, "复制");
});

test("body copy button: click sends original blank lines and raw code without line numbers", async () => {
  const written = [];
  const env = setup({ navigator: { clipboard: { writeText: async (value) => written.push(value) } } });
  const body = env.window.Thread.renderBody("```js\nconst x = 1;\n\nreturn x;\n```");
  body.querySelector("button").click(); await tick();
  assert.deepEqual(written, ["const x = 1;\n\nreturn x;"]);
});

test("mutation checks: removing helpful tie-break and keyboard input gate kills the regression probes", () => {
  const original = fs.readFileSync(path.join(__dirname, "../static/thread.js"), "utf8");
  const ties = setup();
  const changed = original.replace("countHelpful(b) - countHelpful(a) || floorNumber(a) - floorNumber(b)", "countHelpful(b) - countHelpful(a)");
  assert.notEqual(changed, original, "mutation must actually change the implementation");
  vm.runInContext(changed, ties.context);
  const tied = [row(1, 5, { helpful_count: 2 }), row(2, 2, { helpful_count: 2 })];
  assert.throws(() => assert.deepEqual(plain(ties.window.Thread.arrange(tied, { order: "helpful" }).map((item) => item.floor)), [2, 5]), assert.AssertionError);
  const keys = setup();
  const gate = original.replace('target?.closest?.("input, textarea, select, [contenteditable]")', "false");
  assert.notEqual(gate, original, "input mutation must be applied");
  vm.runInContext(gate, keys.context);
  assert.throws(() => assert.equal(keys.window.Thread.keyboardAllowed({ target: keys.document.createElement("textarea") }, { detailVisible: true }), false), assert.AssertionError);
});

/* 直接提取 app.js 中完整论坛区域及其事件绑定，在真实 index.html 的论坛 DOM 上运行。
   只替换 I/O（api/头像/系统时间/动画帧），排序、表单、导航和状态播报都用正式函数。 */
function topFunction(source, name) {
  const match = source.match(new RegExp(`^function ${name}\\(`, "m"));
  assert.ok(match, `missing ${name}`);
  const rest = source.slice(match.index + match[0].length);
  const next = rest.search(/^(?:async )?function \w+\(/m);
  return source.slice(match.index, next < 0 ? source.length : match.index + match[0].length + next);
}

function mountForumMarkup(document) {
  const html = fs.readFileSync(path.join(__dirname, "../static/index.html"), "utf8");
  const section = html.slice(html.indexOf('<section id="forum-page"'), html.indexOf('<section id="admin-page"'));
  assert.ok(section.includes('id="forum-comment-form"'));
  const stack = [document.body];
  const voids = new Set(["input", "img", "br", "hr"]);
  for (const match of section.matchAll(/<\/?([a-z][\w-]*)([^>]*)>|([^<]+)/gi)) {
    if (match[3]) { stack.at(-1).append(match[3].replace(/&gt;/g, ">")); continue; }
    const tag = match[1].toLowerCase();
    if (match[0].startsWith("</")) { stack.pop(); continue; }
    const element = document.createElement(tag);
    for (const attribute of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      const [, name, value = ""] = attribute;
      element.setAttribute(name, value);
      if (name.startsWith("data-")) element.dataset[name.slice(5).replace(/-([a-z])/g, (_all, char) => char.toUpperCase())] = value;
      if (["type", "name", "value"].includes(name)) element[name] = value;
      if (name === "tabindex") element.tabIndex = Number(value);
      if (name === "maxlength") element.maxLength = Number(value);
      if (name === "required") element.required = true;
    }
    element.reset = () => element.querySelectorAll("textarea, input").forEach((field) => { field.value = ""; });
    element.checkValidity = () => element.querySelectorAll("textarea, input").every((field) => (!field.required || Boolean(field.value)) && (!field.maxLength || field.value.length <= field.maxLength));
    element.reportValidity = () => element.checkValidity();
    element.setRangeText = (text, start, end) => { element.value = element.value.slice(0, start) + text + element.value.slice(end); };
    stack.at(-1).append(element);
    if (!voids.has(tag)) stack.push(element);
  }
}

function appEnv() {
  const env = setup({ requestAnimationFrame: () => 1, cancelAnimationFrame() {},
    matchMedia: () => ({ matches: true }), getComputedStyle: () => ({ fontSize: "14px" }) });
  mountForumMarkup(env.document);
  const $ = (selector) => env.document.documentElement.querySelector(selector);
  const calls = [], messages = [];
  let pending = Promise.resolve();
  Object.assign(env.context, {
    $, user: { id: 1, username: "我", avatar_version: 0, has_avatar: false, is_trial: false, ai_enabled: true },
    sessionEpoch: 1, view: "forum", busy: false,
    forumPost: post(), forumCommentOrder: "earliest", forumOnlyOp: false, forumCodeOnly: false,
    forumMentionOnly: false, forumPreview: false, forumDetailGeneration: 0, forumCurrentComment: null,
    forumSummaryController: null, forumMapFrame: null, forumMapModel: null, forumMapDrag: null,
    forumMapDragFrame: null, forumMutations: new Map(), forumReplyTarget: null,
    forumSearchQuery: "", forumListGeneration: 0, sealMotion: { matches: true },
    confirm: () => true, message: (text) => messages.push(text),
    timestamp: (value) => `完整时间 ${value}`,
    avatarElement: (_id, name) => { const node = env.document.createElement("span"); node.className = "avatar"; node.textContent = name; return node; },
    run(action) { pending = Promise.resolve().then(action); return pending; },
    api(url, options = {}) { const call = { url, options, ...deferred() }; calls.push(call); return call.promise; },
    FormData: class {
      constructor(form) { this.form = form; }
      get(name) { return this.form.querySelector(`[name="${name}"]`)?.value || null; }
    },
  });
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const functions = source.slice(source.indexOf("async function showForumList()"), source.indexOf("function resetAdminDashboard()"));
  const bindings = source.slice(source.indexOf('document.querySelectorAll("#forum-back, #forum-back-bottom")'), source.indexOf('$("#timezone").value ='));
  assert.ok(functions.includes("renderForumComment"));
  assert.ok(bindings.includes("submitForumComment"));
  for (const name of ["element", "field", "textarea", "avatarReportForm"]) vm.runInContext(topFunction(source, name), env.context);
  vm.runInContext(functions + "\n" + bindings, env.context, { filename: "app.js:forum" });
  $("#forum-page").hidden = false;
  $("#forum-detail").hidden = false;
  return { ...env, $, calls, messages, pending: () => pending,
    render(value = env.context.forumPost) { env.context.forumPost = value; env.context.renderForumPost(value); env.context.renderForumComments(value.comments); },
    floor: (id) => $(`#forum-comment-${id}`),
    actions: (node) => node.querySelectorAll(".forum-actions button"),
    visible: () => $("#forum-comments").querySelectorAll("article.forum-comment").filter((node) => !node.hidden).map((node) => node.id),
  };
}

test("app sort/filter: real controls announce stable floors, filters intersect and quote navigation clears them", () => {
  const env = appEnv();
  const value = { ...post(), user_id: 1, comments: [row(1, 1, { user_id: 1, is_op: true }), row(3, 3), row(5, 5, { user_id: 1, is_op: true, body: "```py\nx\n```" })] };
  env.render(value);
  assert.deepEqual(env.visible(), ["forum-comment-1", "forum-comment-3", "forum-comment-5"]);
  env.$('[data-forum-order="latest"]').click();
  assert.deepEqual(env.visible(), ["forum-comment-5", "forum-comment-3", "forum-comment-1"]);
  assert.match(env.$("#forum-comment-status").textContent, /最新.*楼层号保持不变/);
  env.$("#forum-only-op").click();
  assert.deepEqual(env.visible(), ["forum-comment-5", "forum-comment-1"]);
  env.$("#forum-code-only").click();
  assert.deepEqual(env.visible(), ["forum-comment-5"]);
  assert.deepEqual(value.comments.map((item) => item.floor), [1, 3, 5]);
  assert.equal(env.$("#forum-comments-title").textContent, "回复 3");
  assert.equal(env.$("#forum-only-op").getAttribute("aria-pressed"), "true");
  env.context.scrollToForumComment(3);
  assert.deepEqual([env.context.forumOnlyOp, env.context.forumCodeOnly, env.context.forumMentionOnly], [false, false, false]);
  assert.equal(env.document.activeElement, env.floor(3));
  assert.match(env.$("#forum-comment-status").textContent, /已显示全部评论，跳转到被引用的楼层/);
});

test("app ownership/quotes: native accessible actions preserve permissions and deleted quote privacy", () => {
  const env = appEnv();
  const comments = [row(1, 3, { user_id: 1, is_op: true }), row(2, 4, { has_avatar: true })];
  for (const item of comments) { delete item.helpful_count; delete item.viewer_helpful; }
  env.render({ ...post(), comments });
  for (const [id, labels] of [[1, ["回复", "编辑", "删除"]], [2, ["回复", "举报", "举报头像"]]]) {
    const article = env.floor(id);
    assert.equal(article.tagName, "ARTICLE");
    assert.equal(article.getAttribute("aria-labelledby"), `forum-comment-heading-${id}`);
    assert.deepEqual(env.actions(article).map((item) => item.textContent), labels);
    for (const button of env.actions(article)) {
      assert.equal(button.type, "button");
      assert.match(button.getAttribute("aria-label"), new RegExp(`${id === 1 ? 3 : 4} 楼`));
    }
  }
  const quoted = row(8, 8, { reply_to: { id: 3, floor: 3, deleted: true, username: "已删秘密作者", excerpt: "已删秘密正文" } });
  const deleted = env.context.renderForumComment(quoted);
  assert.match(deleted.textContent, /回复的楼层已删除/);
  assert.doesNotMatch(deleted.textContent, /已删秘密作者|已删秘密正文/);
  assert.equal(deleted.querySelector("button.forum-quote").disabled, true);
  quoted.reply_to = { id: 3, floor: 3, deleted: false, username: "苏晚", excerpt: "边界检查" };
  const quote = env.context.renderForumComment(quoted).querySelector("button.forum-quote");
  assert.equal(quote.type, "button");
  assert.match(quote.getAttribute("aria-label"), /3 楼/);
  assert.match(quote.textContent, /↳ #03 苏晚 边界检查/);
  assert.equal(quote.listeners.click.length, 1);
  env.context.user.is_trial = true;
  for (const item of comments) assert.equal(env.actions(env.context.renderForumComment(item)).length, 0);
});

test("app empty/trial: seal empty state, intersection feedback and trial browsing notice remain explicit", () => {
  const env = appEnv();
  env.context.forumOnlyOp = true;
  env.render({ ...post(), comments: [row(3, 3)] });
  assert.match(env.$("#forum-comments").textContent, /没有符合条件的回复/);
  assert.equal(env.$("#forum-comments .forum-empty-seal").textContent, "候");
  env.render({ ...post(), comments: [] });
  assert.match(env.$("#forum-comments").textContent, /还没有评论，来抢沙发吧/);
  assert.equal(env.$("#forum-comments .forum-empty-seal").textContent, "首");
  env.context.user.is_trial = true; env.render();
  assert.equal(env.$("#forum-comment-form").hidden, true);
  assert.equal(env.$("#forum-comment-trial-note").hidden, false);
  assert.equal(env.$("#forum-comment-trial-note").textContent, "体验账号可以浏览，不能评论");
  env.context.user.is_trial = false; env.render();
  assert.equal(env.$("#forum-comment-form").hidden, false);
  assert.equal(env.$("#forum-comment-trial-note").hidden, true);
});

test("app reply/reduced-motion: reply focuses textarea, Esc keeps draft, jumps expand with auto scrolling", () => {
  const env = appEnv();
  env.render({ ...post(), comments: [row(3, 3, { user_id: 1, is_op: true })] });
  const field = env.$("#forum-comment-body"); field.value = "未提交的评论草稿";
  env.context.selectForumReply(env.context.forumPost.comments[0]);
  assert.equal(env.context.forumReplyTarget.id, 3);
  assert.equal(env.$("#forum-reply-target").hidden, false);
  assert.match(env.$("#forum-reply-label").textContent, /回复 3 楼 @作者3/);
  assert.match(env.$("#forum-composer-title").textContent, /> 回复 #03 作者3/);
  assert.equal(env.document.activeElement, field);
  env.document.dispatchEvent(new FakeEvent("keydown", { props: { target: field, key: "Escape" } }));
  assert.equal(env.context.forumReplyTarget, null);
  assert.equal(env.$("#forum-reply-target").hidden, true);
  assert.equal(field.value, "未提交的评论草稿");
  let options;
  env.floor(3).scrollIntoView = (value) => { options = value; };
  env.context.scrollToForumComment(3);
  assert.equal(options.behavior, "auto");
  assert.equal(env.document.activeElement, env.floor(3));
  assert.equal(env.floor(3).querySelector(".forum-comment-body").dataset.collapsed, "false");
  env.context.updateForumCommentCount();
  assert.equal(env.$("#forum-comment-count").textContent, `${field.value.length} / 2000`);
});

test("app drafts: sorting/filtering, edit save, quote updates and delete preserve unrelated inline drafts", async () => {
  const env = appEnv();
  const comments = [row(1, 1, { user_id: 1, is_op: true }), row(2, 2, { user_id: 1, is_op: true }), row(3, 3), row(4, 4)];
  for (const item of [comments[0], comments[2], comments[3]]) item.reply_to = { id: 2, floor: 2, deleted: false, username: "作者2", excerpt: "正文2" };
  env.render({ ...post(), comments });
  const original = comments.map((item) => env.floor(item.id));
  const open = (id, label, value) => {
    env.actions(env.floor(id)).find((button) => button.textContent === label).click();
    const form = env.floor(id).querySelector("form"), field = form.querySelector("textarea"); field.value = value;
    return { form, field };
  };
  const edit = open(1, "编辑", "未保存的编辑草稿");
  const report = open(3, "举报", "未提交的举报草稿");
  const avatar = open(4, "举报头像", "未提交的头像举报草稿");
  edit.field.focus(); edit.field.setSelectionRange(2, 6, "backward");
  const preserved = () => {
    for (const [id, item, value] of [[1, edit, "未保存的编辑草稿"], [3, report, "未提交的举报草稿"], [4, avatar, "未提交的头像举报草稿"]]) {
      assert.equal(env.floor(id), original[id - 1]);
      assert.equal(env.floor(id).querySelector("form"), item.form);
      assert.equal(item.field.value, value);
    }
    assert.deepEqual([edit.field.selectionStart, edit.field.selectionEnd, edit.field.selectionDirection], [2, 6, "backward"]);
  };
  env.context.user = { ...env.context.user, avatar_version: 1 }; env.context.renderForumComments(comments); preserved();
  assert.equal(env.document.activeElement, edit.field);
  env.context.forumCommentOrder = "latest"; env.context.renderForumComments(comments); preserved();
  assert.equal(env.document.activeElement, edit.field);
  assert.deepEqual(env.visible(), ["forum-comment-4", "forum-comment-3", "forum-comment-2", "forum-comment-1"]);
  env.context.forumOnlyOp = true; env.context.renderForumComments(comments); preserved();
  for (const id of [3, 4]) { assert.equal(env.floor(id).hidden, true); assert.equal(env.floor(id).inert, true); }
  env.context.scrollToForumComment(3); preserved();
  assert.equal(env.context.forumOnlyOp, false);
  assert.equal(env.floor(3).inert, false);
  assert.equal(env.document.activeElement, env.floor(3));
  const other = open(2, "编辑", "保存另一条评论后的新正文");
  other.form.dispatchEvent(new FakeEvent("submit")); await tick();
  const saveCall = env.calls.at(-1); assert.equal(saveCall.options.method, "PUT");
  saveCall.resolve({ ...comments[1], body: "保存另一条评论后的新正文", updated_at: "2026-10-03T09:00:00Z" }); await env.pending(); preserved();
  assert.equal(env.floor(2).querySelector("form"), null);
  for (const id of [3, 4]) assert.match(env.floor(id).textContent, /保存另一条评论后的新正文/);
  const unchanged = open(2, "编辑", comments[1].body);
  unchanged.form.dispatchEvent(new FakeEvent("submit")); await tick(); env.calls.at(-1).resolve({ ...comments[1] }); await env.pending(); preserved();
  assert.equal(env.floor(2).querySelector("form"), null);
  env.actions(env.floor(2)).find((button) => button.textContent === "删除").click(); await tick();
  assert.equal(env.calls.at(-1).options.method, "DELETE"); env.calls.at(-1).resolve({}); await env.pending(); preserved();
  assert.equal(env.floor(2), null);
  for (const id of [3, 4]) {
    assert.match(env.floor(id).textContent, /回复的楼层已删除/);
    assert.doesNotMatch(env.floor(id).textContent, /保存另一条评论后的新正文/);
  }
  edit.form.querySelectorAll("button").find((button) => button.textContent === "取消").click();
  assert.equal(env.floor(1).querySelector("form"), null);
  assert.match(env.floor(1).textContent, /回复的楼层已删除/);
  env.context.user = { ...env.context.user, id: 99, username: "另一账号" }; env.context.renderForumComments(env.context.forumPost.comments);
  for (const id of [1, 3, 4]) { assert.notEqual(env.floor(id), original[id - 1]); assert.equal(env.floor(id).querySelector("form"), null); }
});

test("app AI panel: busy disables generation, text stays inert, stale footer and exact errors restore action", () => {
  const env = appEnv(); env.render();
  env.context.renderForumSummary({ phase: "idle", summary: null });
  assert.match(env.$("#forum-summary").textContent, /AI 提炼要点（用 1 次 AI 额度）/);
  env.context.renderForumSummary({ phase: "generating", summary: null });
  assert.equal(env.$("#forum-summary").getAttribute("aria-busy"), "true");
  assert.equal(env.$("#forum-summary button").disabled, true);
  assert.equal(env.$("#forum-summary").querySelectorAll(".thread-summary-skeleton").length, 3);
  const safe = summary({ tldr: "<img src=x onerror=bad()>", points: [{ text: "<script>bad()</script>", floors: [2, 5] }], stale: true });
  env.context.renderForumSummary({ phase: "result", summary: safe });
  assert.equal(env.$("#forum-summary").querySelectorAll("img, script").length, 0);
  assert.match(env.$("#forum-summary").textContent, /之后又有新回复，要点可能过时/);
  assert.match(env.$("#forum-summary").textContent, /基于 2 条回复 · 生成于/);
  assert.deepEqual(env.$("#forum-summary").querySelectorAll(".thread-floor").map((chip) => chip.textContent), ["#02", "#05"]);
  env.context.renderForumSummary({ phase: "error", summary: null, error: "本月 AI 额度已用完" });
  assert.equal(env.$("#forum-summary .thread-summary-error").textContent, "本月 AI 额度已用完");
  assert.equal(env.$("#forum-summary button").disabled, false);
  env.context.user.is_trial = true; env.context.renderForumSummary({ phase: "idle", summary: null });
  assert.equal(env.$("#forum-summary").querySelectorAll("button").length, 0);
  env.context.user.ai_enabled = false; env.context.renderForumSummary({ phase: "idle", summary: null });
  assert.equal(env.$("#forum-summary").hidden, true);
  assert.equal(env.$("#forum-summary").childElementCount, 0);
});

test("app accepted/helpful: missing contracts leave no shell, pending guards and server response update real cards", async () => {
  const env = appEnv();
  const value = { ...post(), user_id: 1, accepted_comment_id: null, comments: [row(2, 2), row(5, 5)] };
  env.render(value);
  const helpful = env.floor(2).querySelector('[data-thread-helpful]');
  const helpfulRequest = env.context.updateForumHelpful(value.comments[0], helpful);
  assert.equal(helpful.disabled, true);
  await env.context.updateForumHelpful(value.comments[0], helpful);
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, "/api/comments/2/helpful");
  assert.equal(env.calls[0].options.method, "PUT");
  env.calls[0].resolve({ helpful_count: 19, viewer_helpful: true }); await helpfulRequest;
  assert.equal(value.comments[0].helpful_count, 19);
  assert.equal(env.floor(2).querySelector('[data-thread-helpful]').getAttribute("aria-pressed"), "true");
  assert.match(env.floor(2).querySelector('[data-thread-helpful]').textContent, /19/);
  const accept = env.floor(2).querySelector('[data-thread-accept]');
  const accepted = env.context.updateForumAccepted(value.comments[0], accept);
  assert.equal(accept.disabled, true);
  assert.equal(env.floor(5).querySelector('[data-thread-accept]').disabled, true);
  assert.equal(env.calls[1].options.method, "PUT");
  assert.deepEqual(JSON.parse(env.calls[1].options.body), { comment_id: 2 });
  env.calls[1].resolve({ accepted_comment_id: 2 }); await accepted;
  assert.equal(value.accepted_comment_id, 2);
  assert.equal(env.floor(2).classList.contains("is-accepted"), true);
  assert.equal(env.$("#forum-accepted").hidden, false);
  assert.match(env.$("#forum-post-signal").textContent, /已解决/);
  const unaccept = env.floor(2).querySelector('[data-thread-accept]');
  const failed = env.context.updateForumAccepted(value.comments[0], unaccept);
  assert.equal(env.calls[2].options.method, "DELETE");
  env.calls[2].reject(Object.assign(new Error("HTTP 403"), { detail: "仅楼主可以取消采纳" })); await failed;
  assert.equal(env.$("#forum-comment-status").textContent, "仅楼主可以取消采纳");
  assert.equal(unaccept.disabled, false);
  assert.equal(value.accepted_comment_id, 2);
  delete value.accepted_comment_id;
  for (const item of value.comments) { delete item.helpful_count; delete item.viewer_helpful; }
  env.render();
  assert.equal(env.$("#forum-post-signal").hidden, true);
  assert.equal(env.$("#forum-accepted").hidden, true);
  assert.equal(env.$('[data-forum-order="helpful"]').hidden, true);
  assert.equal(env.$("#forum-comments").querySelectorAll('[data-thread-accept], [data-thread-helpful]').length, 0);
});

test("app composer: preview shares renderer, code insertion wraps selection and successful submission reveals/focuses new floor", async () => {
  const env = appEnv(); env.render();
  const field = env.$("#forum-comment-body"); field.value = "测试 `mid`";
  env.$("#forum-preview-tab").click();
  assert.equal(field.hidden, true);
  assert.equal(env.$("#forum-preview-tab").getAttribute("aria-pressed"), "true");
  assert.equal(env.$("#forum-comment-preview code").textContent, "mid");
  env.$("#forum-editor-tab").click(); field.value = "代码"; field.setSelectionRange(0, 2);
  env.$("#forum-insert-code").click();
  assert.equal(field.value, "```\n代码\n```");
  assert.deepEqual([field.selectionStart, field.selectionEnd], [4, 6]);
  field.value = "新的普通回复";
  env.context.forumOnlyOp = true; env.context.forumCodeOnly = true; env.context.forumMentionOnly = true;
  env.context.submitForumComment(); await tick();
  const created = row(8, 8, { user_id: 1, username: "我", body: field.value });
  assert.equal(env.calls.at(-1).url, "/api/posts/42/comments");
  env.calls.at(-1).resolve(created); await env.pending();
  assert.deepEqual([env.context.forumOnlyOp, env.context.forumCodeOnly, env.context.forumMentionOnly], [false, false, false]);
  assert.equal(env.floor(8).hidden, false);
  assert.equal(env.document.activeElement, env.floor(8));
  assert.equal(field.value, "");
  assert.match(env.$("#forum-comment-status").textContent, /评论已发表，已清除会隐藏新评论的筛选/);
});

test("app keyboard: J/K focus visible articles, R replies; textarea/modifiers/dialog and hidden detail do not navigate", () => {
  const env = appEnv(); env.render();
  const sendKey = (key, target = env.document.body, props = {}) => env.document.dispatchEvent(new FakeEvent("keydown", { props: { key, target, ...props } }));
  sendKey("j"); assert.equal(env.document.activeElement, env.floor(1));
  sendKey("j"); assert.equal(env.document.activeElement, env.floor(2));
  sendKey("k"); assert.equal(env.document.activeElement, env.floor(1));
  sendKey("r", env.floor(1)); assert.equal(env.context.forumReplyTarget.id, 1);
  const before = env.context.forumCurrentComment;
  sendKey("j", env.$("#forum-comment-body")); assert.equal(env.context.forumCurrentComment, before);
  sendKey("j", env.document.body, { ctrlKey: true }); assert.equal(env.context.forumCurrentComment, before);
  const dialog = env.document.createElement("dialog"); env.document.body.append(dialog); dialog.showModal();
  sendKey("j"); assert.equal(env.context.forumCurrentComment, before); dialog.close();
  env.$("#forum-detail").hidden = true;
  sendKey("j"); assert.equal(env.context.forumCurrentComment, before);
});

test("app blocked controls: global busy reset cannot enable zero-count filters or pending requests", async () => {
  const env = appEnv();
  for (const id of ["app", "auth", "notice"]) { const node = env.document.createElement("div"); node.id = id; env.document.body.append(node); }
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  vm.runInContext(topFunction(source, "setBusy"), env.context);
  // Helpful is available only on someone else's comment; keep both filters empty.
  const value = { ...post(), user_id: 1, accepted_comment_id: null,
    comments: [row(1, 2, { user_id: 2 }), row(2, 5)] };
  env.render(value);
  env.context.setBusy(true); env.context.setBusy(false);
  assert.equal(env.$("#forum-code-only").disabled, true);
  assert.equal(env.$("#forum-mention-only").disabled, true);
  env.context.renderForumSummary({ phase: "generating", summary: null });
  env.context.setBusy(true); env.context.setBusy(false);
  assert.equal(env.$("#forum-summary button").disabled, true);
  const button = env.floor(1).querySelector("[data-thread-helpful]");
  assert.ok(button, "another author's helpful action is present");
  const pending = env.context.updateForumHelpful(value.comments[0], button);
  env.context.setBusy(true); env.context.setBusy(false);
  assert.equal(button.disabled, true);
  env.calls[0].resolve({ helpful_count: 1, viewer_helpful: true }); await pending;
  assert.equal(env.floor(1).querySelector("[data-thread-helpful]").disabled, false);
});

test("app mutation identity: an older account's finally cannot unlock a newer request for the same comment", async () => {
  const env = appEnv(); env.render();
  const comment = env.context.forumPost.comments[0];
  const previous = env.context.updateForumHelpful(comment, env.floor(1).querySelector("[data-thread-helpful]"));
  env.context.sessionEpoch += 2; env.context.forumMutations.clear();
  env.context.user = { ...env.context.user, id: 99 }; env.context.renderForumComments(env.context.forumPost.comments);
  const nextButton = env.floor(1).querySelector("[data-thread-helpful]");
  const next = env.context.updateForumHelpful(comment, nextButton);
  assert.equal(env.calls.length, 2);
  env.calls[0].resolve({ helpful_count: 88, viewer_helpful: true }); await previous;
  assert.equal(nextButton.disabled, true);
  assert.equal(env.context.forumMutations.has("helpful:1"), true);
  assert.equal(comment.helpful_count, 0);
  await env.context.updateForumHelpful(comment, nextButton);
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve({ helpful_count: 3, viewer_helpful: true }); await next;
  assert.equal(comment.helpful_count, 3);
  assert.equal(env.context.forumMutations.has("helpful:1"), false);
});

test("app helpful errors: DELETE keeps response state until success, exact 429 detail restores button", async () => {
  const env = appEnv();
  const comment = row(1, 2, { user_id: 2, helpful_count: 7, viewer_helpful: true });
  env.render({ ...post(), comments: [comment, row(3, 5)] });
  const button = env.floor(1).querySelector("[data-thread-helpful]");
  const pending = env.context.updateForumHelpful(comment, button);
  assert.equal(env.calls[0].options.method, "DELETE");
  env.calls[0].reject(Object.assign(new Error("HTTP 429"), { detail: "操作太频繁，请稍后再试" })); await pending;
  assert.equal(env.$("#forum-comment-status").textContent, "操作太频繁，请稍后再试");
  assert.equal(button.disabled, false);
  assert.equal(comment.helpful_count, 7);
  assert.equal(comment.viewer_helpful, true);
});

test("app submit keyboard: Ctrl/Cmd Enter submit valid edit/preview drafts once; invalid and trial drafts stay local", async () => {
  const env = appEnv(); env.render();
  const field = env.$("#forum-comment-body");
  const send = (target, modifier) => env.document.dispatchEvent(new FakeEvent("keydown", { props: { target, key: "Enter", [modifier]: true } }));
  field.value = ""; send(field, "ctrlKey"); await tick();
  assert.equal(env.calls.length, 0);
  assert.equal(env.$("#forum-comment-status").textContent, "请输入回复内容。");
  field.value = "预览中的回复"; env.$("#forum-preview-tab").click();
  send(env.$("#forum-preview-tab"), "metaKey"); await tick();
  assert.equal(env.calls.length, 1);
  assert.deepEqual(JSON.parse(env.calls[0].options.body), { body: "预览中的回复" });
  env.calls[0].resolve(row(8, 8, { body: field.value, user_id: 1 })); await env.pending();
  assert.equal(env.document.activeElement, env.floor(8));
  env.context.user.is_trial = true; field.value = "体验账号草稿";
  send(field, "ctrlKey"); await tick();
  assert.equal(env.calls.length, 1);
});

test("app submit: late success cannot mutate a previous account's post", async () => {
  const env = appEnv(); env.render();
  const old = env.context.forumPost;
  const count = old.comments.length;
  env.$("#forum-comment-body").value = "旧账号草稿";
  env.context.submitForumComment(); await tick();
  env.context.sessionEpoch++;
  env.context.user = { ...env.context.user, id: 99 };
  env.calls[0].resolve(row(8, 8)); await env.pending();
  assert.equal(old.comments.length, count);
  assert.equal(env.messages.length, 0);
  assert.equal(env.$("#forum-comment-body").value, "旧账号草稿");
});

test("app submit: failure explains retry without discarding the draft or reply target", async () => {
  const env = appEnv(); env.render();
  env.context.selectForumReply(env.context.forumPost.comments[0]);
  const field = env.$("#forum-comment-body"); field.value = "保留回复草稿";
  env.context.submitForumComment(); await tick();
  env.calls[0].reject(Object.assign(new Error("HTTP 429"), { detail: "操作太频繁，请稍后再试" }));
  await env.pending();
  assert.equal(field.value, "保留回复草稿");
  assert.equal(env.context.forumReplyTarget.id, 1);
  assert.equal(env.$("#forum-comment-status").textContent, "操作太频繁，请稍后再试");
  assert.equal(env.messages.at(-1), "操作太频繁，请稍后再试");
  env.context.submitForumComment(); await tick();
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve(row(8, 8)); await env.pending();
});

test("feedback: reply network failure explains retry in Chinese and allows another send", async () => {
  const env = appEnv(); env.render();
  const field = env.$("#forum-comment-body"); field.value = "网络中断时保留草稿";
  env.context.selectForumReply(env.context.forumPost.comments[0]);
  env.context.submitForumComment(); await tick();
  env.calls[0].reject(new TypeError("Failed to fetch")); await env.pending();
  assert.match(env.$("#forum-comment-status").textContent, /网络.*重试|发送失败.*重试/);
  assert.equal(field.value, "网络中断时保留草稿");
  assert.equal(env.context.forumReplyTarget.id, 1);
  env.context.submitForumComment(); await tick();
  assert.equal(env.calls.length, 2);
  env.calls[1].resolve(row(8, 8)); await env.pending();
});

test("feedback: reply 401 explains login expiry even after api invalidates the session", async () => {
  const env = appEnv(); env.render();
  env.$("#forum-comment-body").value = "过期登录的草稿";
  env.context.submitForumComment(); await tick();
  // The production api() calls signedOut() before rejecting a 401 response.
  env.context.sessionEpoch++;
  env.context.user = null;
  env.context.view = "home";
  env.calls[0].reject(Object.assign(new Error("请先登录"), { status: 401 })); await env.pending();
  assert.match(env.messages.join(" "), /登录.*过期|重新登录|请先登录/);
});

test("app submit: a late failure cannot display errors after leaving the discussion", async () => {
  const env = appEnv(); env.render();
  env.$("#forum-comment-body").value = "离开前草稿";
  env.context.submitForumComment(); await tick();
  env.context.view = "home";
  env.calls[0].reject(new Error("旧请求失败")); await env.pending();
  assert.equal(env.messages.length, 0);
  assert.equal(env.$("#forum-comment-status").textContent, "");
});

test("app submit: successful send preserves text typed while its response was pending", async () => {
  const env = appEnv(); env.render();
  const field = env.$("#forum-comment-body"); field.value = "第一条回复";
  env.context.submitForumComment(); await tick();
  field.value = "下一条还没发的回复";
  env.calls[0].resolve(row(8, 8, { body: "第一条回复", user_id: 1 })); await env.pending();
  assert.equal(field.value, "下一条还没发的回复");
  assert.equal(env.floor(8).textContent.includes("第一条回复"), true);
});

test("feedback: opening an unanswered post reveals its composer and accepts its first reply", async () => {
  const env = appEnv();
  env.context.user.ai_enabled = false;
  env.$("#forum-detail").hidden = true;
  env.$("#forum-list").hidden = false;
  const opening = env.context.openForumPost(72);
  env.calls[0].resolve({ ...post(72), comments: [], comment_count: 0 });
  await opening;
  assert.equal(env.$("#forum-detail").hidden, false);
  assert.equal(env.$("#forum-list").hidden, true);
  assert.equal(env.$("#forum-comment-form").hidden, false);
  env.$("#forum-comment-body").value = "这是第一条回复";
  env.$("#forum-comment-form").dispatchEvent(new FakeEvent("submit"));
  await tick();
  assert.equal(env.calls[1].url, "/api/posts/72/comments");
  env.calls[1].resolve(row(80, 1, { body: "这是第一条回复", user_id: 1 }));
  await env.pending();
  assert.equal(env.floor(80).textContent.includes("这是第一条回复"), true);
  assert.equal(env.document.activeElement, env.floor(80));
});

test("feedback: a collapsed or accepted comment remains replyable", () => {
  const env = appEnv();
  const comments = [row(3, 3, { body: "很长的正文\n".repeat(30) }), row(4, 4)];
  env.render({ ...post(), comments, accepted_comment_id: 4 });
  for (const id of [3, 4]) {
    const reply = env.actions(env.floor(id)).find((button) => button.textContent === "回复");
    assert.ok(reply, `floor ${id} has a reply action`);
    reply.click();
    assert.equal(env.context.forumReplyTarget.id, id);
    assert.equal(env.$("#forum-comment-body").hidden, false);
    assert.equal(env.document.activeElement, env.$("#forum-comment-body"));
  }
});

test("feedback: out-of-order detail results cannot replace the newer unanswered post", async () => {
  const env = appEnv(); env.context.user.ai_enabled = false;
  const older = env.context.openForumPost(71);
  const newer = env.context.openForumPost(72);
  env.calls[1].resolve({ ...post(72), comments: [] }); await newer;
  env.calls[0].resolve({ ...post(71), comments: [] }); await older;
  assert.equal(env.context.forumPost.id, 72);
  assert.equal(env.$("#forum-detail").hidden, false);
  assert.equal(env.$("#forum-comment-form").hidden, false);
});

test("feedback: opening failure gives a visible Chinese retry without losing the list", async () => {
  const env = appEnv(); env.context.user.ai_enabled = false;
  env.$("#forum-detail").hidden = true;
  env.$("#forum-list").hidden = false;
  const opening = env.context.openForumPost(72);
  // A failed GET must remain reviewable beside the list, even when global toast scrolls away.
  const settled = Promise.resolve(opening).catch(() => {});
  env.calls[0].reject(new TypeError("Failed to fetch")); await settled;
  const notice = env.$("#forum-open-status");
  assert.ok(notice, "opening status is present in the forum markup");
  assert.match(notice.textContent, /打不开|无法打开|打开.*失败/);
  const retry = env.$("#forum-open-retry");
  assert.ok(retry, "an opening failure offers an actionable retry");
  assert.equal(retry.hidden, false);
  retry.click(); await tick();
  assert.equal(env.calls[1].url, "/api/posts/72");
  env.calls[1].resolve({ ...post(72), comments: [] }); await tick();
  assert.equal(env.$("#forum-detail").hidden, false);
  assert.equal(env.$("#forum-comment-form").hidden, false);
});

test("feedback: a slow detail GET explains loading while keeping the list visible", async () => {
  const env = appEnv(); env.context.user.ai_enabled = false;
  env.$("#forum-detail").hidden = true;
  env.$("#forum-list").hidden = false;
  const opening = env.context.openForumPost(72);
  const status = env.$("#forum-open-status");
  assert.ok(status, "loading a post exposes a status area");
  assert.match(status.textContent, /正在.*打开|正在.*加载/);
  assert.equal(env.$("#forum-list").hidden, false);
  env.calls[0].resolve({ ...post(72), comments: [] }); await opening;
  assert.equal(env.$("#forum-detail").hidden, false);
});

test("feedback: an older detail failure cannot cover a newly opened post with retry", async () => {
  const env = appEnv(); env.context.user.ai_enabled = false;
  const older = Promise.resolve(env.context.openForumPost(71)).catch(() => {});
  const newer = env.context.openForumPost(72);
  env.calls[1].resolve({ ...post(72), comments: [] }); await newer;
  env.calls[0].reject(new TypeError("Failed to fetch")); await older;
  assert.equal(env.context.forumPost.id, 72);
  assert.equal(env.$("#forum-detail").hidden, false);
  const retry = env.$("#forum-open-retry");
  assert.ok(retry);
  assert.equal(retry.hidden, true, "late errors do not ask to reopen an old post");
});

test("feedback: a stale 401 reply failure does not interrupt a newly signed-in account", async () => {
  const env = appEnv(); env.render();
  env.$("#forum-comment-body").value = "旧账号草稿";
  env.context.submitForumComment(); await tick();
  env.context.sessionEpoch++;
  env.context.user = { ...env.context.user, id: 99 };
  env.calls[0].reject(Object.assign(new Error("请先登录"), { status: 401 })); await env.pending();
  assert.equal(env.messages.length, 0);
});
