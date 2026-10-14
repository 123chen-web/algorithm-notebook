"use strict";
/* 复习=重做：app.js 复习详情里的三种复习方式，在仓库的 fake browser 里跑生产函数，
   只替换 API I/O。覆盖：模式选择器、默认选择、默写对照、限时重做、建议分入库。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");
const SOURCE = (() => {
  const order = ["core-api.js","core-session.js","core-ui.js","overview-growth.js","overview-home.js","overview-insights.js","review-list.js","review-detail.js","review-grade.js","problem-form.js","forum-board.js","forum-post.js","forum-comments.js","forum-map.js","groups.js","rank.js","account.js","admin.js","payments.js","ai.js"];
  return order.map((f) => fs.readFileSync(path.join(__dirname, "../static/app", f), "utf8")).join("\n")
    + "\n" + fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
})();

function hasTopFunction(name, source = SOURCE) {
  const head = "function " + name + "(";
  let index = source.indexOf(head);
  while (index >= 0) {
    const lineStart = source.lastIndexOf("\n", index - 1) + 1;
    const prefix = source.slice(lineStart, index);
    if (prefix === "" || prefix === "async ") return true;
    index = source.indexOf(head, index + 1);
  }
  return false;
}

function topFunction(name, source = SOURCE) {
  // 不用正则找函数头，避免模板字符串里反斜杠层数出错。
  const head = "function " + name + "(";
  let index = source.indexOf(head);
  while (index >= 0) {
    const lineStart = source.lastIndexOf("\n", index - 1) + 1;
    const prefix = source.slice(lineStart, index);
    if (prefix === "" || prefix === "async ") { index = lineStart; break; }
    index = source.indexOf(head, index + 1);
  }
  const match = index >= 0 ? { index } : null;
  assert.ok(match, `production function ${name} must exist`);
  const end = source.indexOf("\n}", match.index);
  assert.ok(end > match.index, `production function ${name} must end`);
  return source.slice(match.index, end + 2);
}

const CARD = (id = 1, changes = {}) => ({
  id, problem_id: id, title: `题目${id}`, zone: "算法", language: "Python",
  description: "边界容易漏掉", thinking: "原始思路", code: "pass", tags: ["边界"],
  correct_code: "def f():\n    x = 1\n    return x",
  source_url: "https://example.com/problem/1",
  counterexample: "输入 n=1 时输出 0，期望 1",
  interval_days: 2, repetitions: 1, ease_factor: 2.5, version: 3,
  today: "2026-10-04", due_date: "2026-10-04", last_reviewed_at: null,
  reviews: [], variants: [], suspended_at: null, ...changes,
});

function environment() {
  const env = load(["redo-flow.js", "problem-cards.js", "review-extras.js"]);
  assert.ok(env.window.RedoFlow, "redo-flow.js 应当在页面上下文里导出 window.RedoFlow");
  // harness 的页面 vm 上下文没有 timers，startTimer 需要它们（生产浏览器自带）。
  // 注意：要挂到 context 全局上，挂 window 对象上在 vm 里读不到裸的 setInterval。
  env.context.setInterval = setInterval;
  env.context.clearInterval = clearInterval;
  const { document, context } = env;
  const calls = [], messages = [], notifications = [];
  const $ = (selector) => document.querySelector(selector);
  for (const id of ["detail", "cards", "list-page", "list-focus", "list-title", "list-summary",
    "list-filter", "list-filter-text", "zone-filter", "tag-filter", "review-cap-controls",
    "review-daily-cap", "review-done-today", "review-cap-note", "review-queue-status"]) $("#" + id);
  const cap = document.createElement("select");
  cap.id = "review-daily-cap";
  for (const value of ["", "10", "20", "30", "50", "100"]) {
    const option = document.createElement("option"); option.value = value; cap.append(option);
  }
  const label = document.createElement("label"); label.append(cap);
  $("#review-daily-cap").replaceWith(label);
  Object.assign(context, {
    $, user: { id: 1, ai_enabled: false, ai_daily_limit: 10 }, view: "today", sessionEpoch: 1,
    busy: false, codeZones: new Set(["算法"]), listCreatedOn: "",
    rvfDetailState: null, rvfDetailGeneration: 0, rvfSelectionGeneration: 0, rvfListGeneration: 0, rvfPageGeneration: 0,
    rvfHeaderGeneration: 0, rvfListItems: new Map(), rvfQueue: null, rvfShowAllOn: "",
    redoPreferenceCache: null, // app.js 模块级 let，提取的函数在 vm 上下文里需要它
    localStorage: env.window.localStorage,
    timestamp: (value) => value || "尚未复习", confirm: () => false,
    message: (text, error) => messages.push({ text, error }),
    notifyDataChanged: (reason) => notifications.push(reason), updateUserInfo() {},
    sealAnchorPoint() { return {}; }, stampSeal() {},
    run: (action) => Promise.resolve().then(action),
    api(url, options = {}) { const call = { url, options, ...deferred() }; calls.push(call); return call.promise; },
    renderMistakeText: (item) => context.element("pre", item.description, "prose"),
    renderProblemEditor: (item) => context.element("pre", item.thinking + item.code, "prose"),
  });
  vm.runInContext(SOURCE.slice(SOURCE.indexOf("const grades = ["), SOURCE.indexOf("\n];", SOURCE.indexOf("const grades = [")) + 3), context);
  for (const name of ["element", "field"]) vm.runInContext(topFunction(name, SOURCE), context);
  const names = ["clearDetail", "renderDetail", "loadList", "openMistake", "rvfRestoreItem"];
  for (const match of SOURCE.matchAll(/^(?:async )?function (rvf\w+)\(/gm)) names.push(match[1]);
  for (const name of [...new Set(names)]) {
    if (hasTopFunction(name, SOURCE)) vm.runInContext(topFunction(name, SOURCE), context);
  }
  return { ...env, $, calls, messages, notifications,
    render(item = CARD()) { context.user.today = item.today; context.renderDetail(item); return $("#detail"); },
    // 回答所有待处理的 redo-hint / review-settings 请求；api 调用是异步产生的，需要轮询。
    async settleRedo(options = {}) {
      for (let i = 0; i < 8; i++) {
        for (const call of calls.filter((c) => !c.answered)) {
          if (call.url.endsWith("/redo-hint")) { call.answered = true; call.resolve({ hint: Boolean(options.hint) }); }
          else if (call.url === "/api/me/review-settings" && !call.options.method) {
            call.answered = true;
            call.resolve({ daily_review_cap: null, redo_preference: options.preference || "recall_only" });
          }
        }
        await tick();
      }
    },
    key(key, target = document.body, props = {}) {
      const event = new FakeEvent("keydown", { props: { key, target, ...props } });
      context.rvfDetailKeydown(event); return event;
    },
  };
}

function modeButtons(env) {
  return [...env.$("#detail").querySelectorAll(".redo-mode")];
}

test("算法分区显示四种复习方式；非算法分区不显示", async () => {
  const env = environment();
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo();
  const buttons = modeButtons(env);
  assert.equal(buttons.length, 4);
  assert.deepEqual(buttons.map((b) => b.dataset.mode), ["redo", "similar", "rewrite", "recall"]);

  const other = environment();
  other.render(CARD(2, { zone: "数学" }));
  other.$("#review-reveal").click();
  await other.settleRedo();
  assert.equal(other.$("#detail").querySelectorAll(".redo-mode").length, 0);
});

test("缺正确代码时默写按钮禁用；缺来源链接时重做按钮禁用", async () => {
  const env = environment();
  env.render(CARD(1, { correct_code: "", source_url: "" }));
  env.$("#review-reveal").click();
  await env.settleRedo();
  const buttons = modeButtons(env);
  assert.equal(buttons.find((b) => b.dataset.mode === "rewrite").disabled, true);
  assert.equal(buttons.find((b) => b.dataset.mode === "redo").disabled, true);
  assert.equal(buttons.find((b) => b.dataset.mode === "recall").disabled, false);
});

test("防虚高提示时默认选中重做并显示提示", async () => {
  const env = environment();
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo({ hint: true });
  const pressed = modeButtons(env).find((b) => b.getAttribute("aria-pressed") === "true");
  assert.ok(pressed, "应当有默认选中的方式");
  assert.equal(pressed.dataset.mode, "rewrite");
  const hint = env.$("#detail").querySelector(".redo-hint");
  assert.ok(hint && !hint.hidden, "应当显示“这条用重做更靠谱”");
});

test("只回忆偏好时默认选只回忆并标自评", async () => {
  const env = environment();
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo({ preference: "recall_only" });
  const pressed = modeButtons(env).find((b) => b.getAttribute("aria-pressed") === "true");
  assert.equal(pressed.dataset.mode, "recall");
  const badge = env.$("#detail").querySelector(".redo-badge");
  assert.ok(badge && !badge.hidden, "只回忆时应显示“自评”标记");
});

test("默写：对照 diff 后评分带 mode 与 evidence 入库", async () => {
  const env = environment();
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo();
  modeButtons(env).find((b) => b.dataset.mode === "rewrite").click();
  const panel = env.$("#detail").querySelector(".redo-panel");
  assert.ok(panel, "默写面板应当出现");
  const area = panel.querySelector("textarea");
  area.value = "def f():\n    x = 2\n    return x";
  panel.querySelector("button").click(); // 对照
  await tick();
  const missing = [...panel.querySelectorAll(".redo-missing")].map((n) => n.textContent);
  const extra = [...panel.querySelectorAll(".redo-extra")].map((n) => n.textContent);
  assert.ok(missing.some((t) => t.includes("x = 1")), `缺失行应含 x = 1，实际 ${missing}`);
  assert.ok(extra.some((t) => t.includes("x = 2")), `多余行应含 x = 2，实际 ${extra}`);
  assert.match(panel.textContent, /相似度/);
  // 成绩由相似度算出（0.6–0.85 → 很吃力 3），只有一个提交按钮，没有二次选分。
  assert.equal(panel.querySelectorAll(".review-grade").length, 0);
  const gradeButton = panel.querySelector(".redo-submit");
  assert.ok(gradeButton && gradeButton.dataset.quality === "3", "应当有按相似度计分的提交按钮");
  gradeButton.click();
  await tick(); await tick();
  const review = env.calls.find((call) => call.url === "/api/mistakes/1/review");
  assert.ok(review, "应当发起评分请求");
  const body = JSON.parse(review.options.body);
  assert.equal(body.quality, 3);
  assert.equal(body.mode, "rewrite");
  const evidence = JSON.parse(body.evidence);
  assert.equal(evidence.suggested, 3);
  assert.equal(evidence.final, 3);
  assert.equal(evidence.auto, true);
  assert.ok(evidence.similarity > 0.6 && evidence.similarity < 0.7, JSON.stringify(evidence));
});

test("限时重做：选择时限、开始、做对后建议分入库", async () => {
  const env = environment();
  const opened = [];
  env.window.open = (url) => { opened.push(url); return null; };
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo();
  modeButtons(env).find((b) => b.dataset.mode === "redo").click();
  const panel = env.$("#detail").querySelector(".redo-panel");
  assert.ok(panel, "重做面板应当出现");
  const select = panel.querySelector("select");
  select.value = "10";
  const start = [...panel.querySelectorAll("button")].find((b) => /开始/.test(b.textContent));
  assert.ok(start);
  start.click();
  await tick();
  assert.deepEqual(opened, ["https://example.com/problem/1"]);
  const results = [...panel.querySelectorAll(".redo-result")].map((b) => b.dataset.result);
  assert.deepEqual(results, ["clean", "retry", "hinted", "failed"]);
  assert.equal(panel.querySelectorAll(".review-grade").length, 0, "不再有二次选分的按钮");
  panel.querySelector('[data-result="clean"]').click();
  await tick(); await tick();
  const review = env.calls.find((call) => call.url === "/api/mistakes/1/review");
  const body = JSON.parse(review.options.body);
  assert.equal(body.mode, "redo");
  assert.equal(body.quality, 5, "刚开始就一次通过（用时不到时限 1/3）算很熟");
  const evidence = JSON.parse(body.evidence);
  assert.equal(evidence.suggested, 5);
  assert.equal(evidence.final, 5);
  assert.equal(evidence.result, "clean");
  assert.equal(evidence.auto, true);
  assert.equal(evidence.limit_seconds, 600);
  assert.ok(Number.isInteger(evidence.seconds) && evidence.seconds >= 0);
});

test("重做：看了提示算很吃力，没做出来算答错，客户端和服务端规则一致", async () => {
  for (const [result, quality] of [["hinted", 3], ["retry", 4], ["failed", 0]]) {
    const env = environment();
    env.window.open = () => null;
    env.render();
    env.$("#review-reveal").click();
    await env.settleRedo();
    modeButtons(env).find((b) => b.dataset.mode === "redo").click();
    const panel = env.$("#detail").querySelector(".redo-panel");
    [...panel.querySelectorAll("button")].find((b) => /开始/.test(b.textContent)).click();
    panel.querySelector(`[data-result="${result}"]`).click();
    await tick(); await tick();
    const review = env.calls.find((call) => call.url === "/api/mistakes/1/review");
    assert.equal(JSON.parse(review.options.body).quality, quality, result);
  }
});

test("做类似题：找题、限时、做错时顺手记成新错题，成绩由结果算出", async () => {
  const env = environment();
  const opened = [], added = [];
  env.window.open = (url) => { opened.push(url); return null; };
  env.window.Similar = { configure() {}, quickAdd: async (item) => { added.push(item.title); } };
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo();
  modeButtons(env).find((b) => b.dataset.mode === "similar").click();
  await tick();
  const lookup = env.calls.find((call) => call.url === "/api/review/similar?mistake_id=1");
  assert.ok(lookup, "应当去取一道类似题");
  lookup.resolve({ items: [
    { title: "没有链接的站内题", source: "站内", url: null },
    { title: "Two Sum 变体", source: "codeforces", difficulty: 1200, reason: "同一个套路", url: "https://example.com/p/2",
      add_payload: { title: "Two Sum 变体", zone: "算法", language: "Python", quick: true } },
  ] });
  await tick(); await tick();
  const panel = env.$("#detail").querySelector(".redo-panel");
  assert.match(panel.textContent, /Two Sum 变体/);
  assert.doesNotMatch(panel.textContent, /没有链接的站内题/, "没有题目链接的题没法去做，不推荐");
  [...panel.querySelectorAll("button")].find((b) => /开始/.test(b.textContent)).click();
  assert.deepEqual(opened, ["https://example.com/p/2"]);
  panel.querySelector('[data-result="failed"]').click();
  await tick(); await tick(); await tick();
  assert.deepEqual(added, ["Two Sum 变体"], "没做出来时默认把它记进错题本");
  const review = env.calls.find((call) => call.url === "/api/mistakes/1/review");
  const body = JSON.parse(review.options.body);
  assert.equal(body.mode, "similar");
  assert.equal(body.quality, 0);
  const evidence = JSON.parse(body.evidence);
  assert.equal(evidence.result, "failed");
  assert.equal(evidence.ref, "Two Sum 变体");
  assert.equal(evidence.auto, true);
});

test("类似题：一次通过最高算记得，不给很熟", async () => {
  const env = environment();
  env.window.open = () => null;
  env.window.Similar = { configure() {}, quickAdd: async () => {} };
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo();
  modeButtons(env).find((b) => b.dataset.mode === "similar").click();
  await tick();
  env.calls.find((call) => call.url === "/api/review/similar?mistake_id=1")
    .resolve({ items: [{ title: "T", source: "x", url: "https://example.com/p/3" }] });
  await tick(); await tick();
  const panel = env.$("#detail").querySelector(".redo-panel");
  [...panel.querySelectorAll("button")].find((b) => /开始/.test(b.textContent)).click();
  panel.querySelector('[data-result="clean"]').click();
  await tick(); await tick();
  const review = env.calls.find((call) => call.url === "/api/mistakes/1/review");
  assert.equal(JSON.parse(review.options.body).quality, 4);
});

test("没有带链接的类似题时给出说明，不卡住", async () => {
  const env = environment();
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo();
  modeButtons(env).find((b) => b.dataset.mode === "similar").click();
  await tick();
  env.calls.find((call) => call.url === "/api/review/similar?mistake_id=1").resolve({ items: [] });
  await tick(); await tick();
  assert.match(env.$("#detail").querySelector(".redo-panel").textContent, /暂时没有合适的类似题/);
});

test("复习方式在显示错因之前就能选；做题验证不需要先看答案", async () => {
  const env = environment();
  env.window.open = () => null;
  env.render();
  await env.settleRedo();
  const section = env.$("#detail").querySelector(".redo-section");
  assert.ok(section && !section.hidden, "没点“显示错因”也应当能选复习方式");
  assert.equal(env.$("#detail").querySelector(".review-actions").parentNode.hidden, true, "自评按钮在显示错因之前不可见");
  modeButtons(env).find((b) => b.dataset.mode === "redo").click();
  const panel = env.$("#detail").querySelector(".redo-panel");
  [...panel.querySelectorAll("button")].find((b) => /开始/.test(b.textContent)).click();
  panel.querySelector('[data-result="clean"]').click();
  await tick(); await tick();
  assert.ok(env.calls.find((call) => call.url === "/api/mistakes/1/review"), "没看错因也能交卷");
});

test("上一次只是自评：默认改用做题验证，并提醒两周上限", async () => {
  const env = environment();
  env.render(CARD(1, { reviews: [{ reviewed_at: "2026-10-01T04:00:00+00:00", quality: 4, next_due_date: "2026-10-05", mode: "recall" }] }));
  await env.settleRedo({ preference: "recall_only" });
  const pressed = modeButtons(env).find((b) => b.getAttribute("aria-pressed") === "true");
  assert.equal(pressed.dataset.mode, "redo");
  const nudge = env.$("#detail").querySelector(".redo-nudge");
  assert.ok(nudge && !nudge.hidden);
  assert.match(nudge.textContent, /14 天/);

  const verified = environment();
  verified.render(CARD(1, { reviews: [{ reviewed_at: "2026-10-01T04:00:00+00:00", quality: 4, next_due_date: "2026-10-05", mode: "redo" }] }));
  await verified.settleRedo({ preference: "recall_only" });
  const choice = modeButtons(verified).find((b) => b.getAttribute("aria-pressed") === "true");
  assert.equal(choice.dataset.mode, "recall", "上次已经验证过，按偏好走");
  assert.equal(verified.$("#detail").querySelector(".redo-nudge").hidden, true);
});

test("算法题的自评没有“很熟”，其他分区还有", async () => {
  const algorithm = environment();
  algorithm.render();
  const qualities = [...algorithm.$("#detail").querySelectorAll(".review-grade")].map((b) => b.dataset.quality);
  assert.deepEqual(qualities, ["0", "2", "3", "4"]);
  const math = environment();
  math.render(CARD(2, { zone: "数学" }));
  assert.deepEqual([...math.$("#detail").querySelectorAll(".review-grade")].map((b) => b.dataset.quality), ["0", "2", "3", "4", "5"]);
});

test("重做进行中按数字键不触发普通评分", async () => {
  const env = environment();
  env.render();
  env.$("#review-reveal").click();
  await env.settleRedo();
  modeButtons(env).find((b) => b.dataset.mode === "rewrite").click();
  const before = env.calls.filter((c) => c.url === "/api/mistakes/1/review").length;
  env.key("4", env.$("#detail"));
  await tick();
  const after = env.calls.filter((c) => c.url === "/api/mistakes/1/review").length;
  assert.equal(after, before, "默写面板激活时数字键不应评分");
});

test("复习历史显示模式标签", async () => {
  const env = environment();
  env.render(CARD(1, { reviews: [
    { reviewed_at: "2026-10-01T04:00:00+00:00", quality: 4, next_due_date: "2026-10-05", mode: "rewrite" },
    { reviewed_at: "2026-10-02T04:00:00+00:00", quality: 3, next_due_date: "2026-10-06", mode: "recall" },
  ] }));
  const history = env.$("#detail").querySelector(".review-history");
  assert.ok(history);
  assert.match(history.textContent, /默写/);
  assert.match(history.textContent, /自评/);
});
