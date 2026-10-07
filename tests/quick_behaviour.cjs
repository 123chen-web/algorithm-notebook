"use strict";

/* 速记模式前端（E2 事项一）：开关显隐逻辑、quick=true/false 请求体字段、
   提交成功后恢复普通模式。
   复用 static/app.js 里的真实代码（速记函数块 + 建题 submit 监听），只用假浏览器
   （tests/js_harness.cjs）驱动；api/run/提示等外围依赖用桩代替（桩语义贴近真实 run：
   action 抛错时吞掉并记录，不让它变成未处理拒绝）。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const appSource = fs.readFileSync(path.join(__dirname, "..", "static", "app.js"), "utf8");
function between(start, end) {
  const from = appSource.indexOf(start);
  const to = appSource.indexOf(end, from);
  assert.ok(from >= 0 && to > from, `extract the real app.js section: ${start}`);
  return appSource.slice(from, to);
}
// 速记函数块：isQuickMode / applyQuickMode / buildProblemPayload / resetQuickMode / 开关监听。
const quickSource = between("/* ---- 速记模式（E2 事项一）", '$("#problem-form").addEventListener("submit", (event) => {');
// 建题 submit 监听（含成功后 resetQuickMode）。
const submitSource = between('$("#problem-form").addEventListener("submit", (event) => {', "async function showForumList() {");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

const HINT_ORIGINAL = "可留空，生成练习时 AI 会自动诊断；最多添加 10 条。";
const QUICK_HINT = "速记模式下将自动创建一条待补原因的易错点";

/* 按 static/index.html 的真实结构搭一个最小表单：basics 区（题名/分区/语言/代码）、
   思路折叠区、易错点区（一空一有字，覆盖"留空过滤"逻辑）、速记开关（默认关闭）。 */
function buildForm(document) {
  const el = (tag, attrs = {}) => {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "text") node.textContent = value;
      else if (key === "checked") node.checked = value;
      else if (key === "value") node.value = value;
      else node.setAttribute(key, value);
    }
    return node;
  };
  const form = el("form");
  form.id = "problem-form";

  const toggleWrap = el("label");
  const toggle = el("select", { id: "problem-quick", value: "full" });
  toggleWrap.append(toggle, "速记模式");

  const basics = el("section");
  basics.setAttribute("data-form-section", "basics");
  const title = el("input", { name: "title", value: "两数之和" });
  title.setAttribute("required", "");
  const zone = el("select", { name: "zone", value: "算法" });
  const language = el("input", { name: "language", value: "Python" });
  const codeLabel = el("label");
  const code = el("textarea", { name: "code", value: "" });
  code.setAttribute("required", "");
  codeLabel.append(code);
  basics.append(title, zone, language, codeLabel);

  const thinkingSection = el("section");
  thinkingSection.setAttribute("data-form-section", "thinking");
  const thinking = el("textarea", { name: "thinking", value: "" });
  thinking.setAttribute("required", "");
  thinkingSection.append(thinking);

  const mistakesSection = el("section");
  mistakesSection.setAttribute("data-form-section", "mistakes");
  const hint = el("p", { id: "mistake-hint", text: HINT_ORIGINAL });
  const mistakeInputs = el("div", { id: "mistake-inputs" });
  const blank = el("textarea", { name: "mistake", value: "" });
  const written = el("textarea", { name: "mistake", value: "边界条件没考虑" });
  mistakeInputs.append(blank, written);
  mistakesSection.append(hint, mistakeInputs);

  const save = el("button", { type: "submit", text: "保存这条记录" });
  const sentenceLabel = el("label", { id: "problem-sentence-field" });
  sentenceLabel.hidden = true;
  const sentence = el("textarea", { id: "problem-sentence", name: "quick_reason", value: "" });
  sentenceLabel.append(sentence);
  form.append(toggleWrap, basics, sentenceLabel, thinkingSection, mistakesSection, save);
  // 真实 DOM 里 form.zone 能拿到具名控件；假 DOM 里手动补上（提交成功分支会读它）。
  form.zone = zone;
  // 模拟浏览器原生 reset：开关回到默认（关闭），各字段值清空。
  form.reset = () => {
    toggle.value = "full";
    for (const field of form.querySelectorAll("[name]")) field.value = "";
  };
  document.body.append(form);
  return { form, toggle, title, code, thinking, codeLabel, thinkingSection, mistakesSection, sentenceLabel, sentence, hint };
}

function environment({ failApi = false } = {}) {
  const env = load([]);
  const { document, context } = env;
  const ui = buildForm(document);
  env.window.probe = { requests: [], error: null };
  vm.runInContext(`
    const $ = (selector) => document.querySelector(selector);
    let photoRecognition = null; let sessionEpoch = 1; let user = { id: 1 };
    function sealAnchorPoint() { return {}; }
    function stampSeal() {}
    function notifyDataChanged() {}
    function applyZoneFieldMode() {}
    function resetPhotoForm() {}
    async function showView() {
      if (window.probe.waitForView) await new Promise((resolve) => { window.probe.resolveView = resolve; });
      if (window.probe.failView) throw new Error("view failed");
    }
    async function openMistake(id) {
      (window.probe.opened ||= []).push(id);
      if (window.probe.waitForDetail) await new Promise((resolve) => { window.probe.resolveDetail = resolve; });
    }
    function message(text) { (window.probe.messages ||= []).push(text); }
    // 贴近真实 run：action 抛错时吞掉并记录，resetQuickMode 只在成功路径执行。
    async function run(action) {
      try { await action(); } catch (error) { window.probe.error = error; }
    }
    async function api(path, options = {}) {
      window.probe.requests.push({ path, options });
      if (window.probe.waitForApi) await new Promise((resolve) => { window.probe.resolveApi = resolve; });
      if (${failApi}) throw new Error("network down");
      return { mistake_ids: [7] };
    }
    function addMistakeInput() {}
    // 够建题提交用的最小 FormData：按 name 收集控件值。
    class FormData {
      constructor(form) { this.form = form; }
      _all(name) { return this.form.querySelectorAll("[name]").filter((item) => item.getAttribute("name") === name); }
      get(name) { const found = this._all(name); return found.length ? found[0].value : null; }
      getAll(name) { return this._all(name).map((item) => item.value); }
    }
  `, context);
  vm.runInContext(quickSource, context, { filename: "app.js:quick" });
  vm.runInContext(submitSource, context, { filename: "app.js:problem-submit" });
  const setQuick = (on) => {
    ui.toggle.value = on ? "quick" : "full";
    ui.toggle.dispatchEvent(new FakeEvent("change", { bubbles: true }));
  };
  const submit = () => ui.form.dispatchEvent(new FakeEvent("submit", { bubbles: true, props: { submitter: null } }));
  return {
    ...env, ...ui, setQuick, submit,
    setMode: (mode) => { ui.toggle.value = mode; ui.toggle.dispatchEvent(new FakeEvent("change")); },
    requests: env.window.probe.requests,
    probe: env.window.probe,
    isQuick: () => vm.runInContext("isQuickMode()", context),
    payload: (quick) => vm.runInContext(`buildProblemPayload(document.querySelector("#problem-form"), ${quick})`, context),
  };
}
async function settle() { await tick(); await tick(); }
const lastBody = (env) => JSON.parse(env.requests[env.requests.length - 1].options.body);

/* ---------------- 开关显隐逻辑 ---------------- */

test("quick: 开关默认关闭，普通模式下 required 与显隐保持原样", () => {
  const env = environment();
  assert.equal(env.toggle.value, "full");
  assert.equal(env.isQuick(), false);
  assert.ok(env.title.hasAttribute("required"), "题名 required 不动");
  assert.ok(env.code.hasAttribute("required"));
  assert.ok(env.thinking.hasAttribute("required"));
  assert.equal(env.codeLabel.hidden, false);
  assert.equal(env.thinkingSection.hidden, false);
  assert.equal(env.hint.textContent, HINT_ORIGINAL);
});

test("quick: 打开速记，code/thinking 去 required 并隐藏，易错点换提示", () => {
  const env = environment();
  env.setQuick(true);
  assert.equal(env.isQuick(), true);
  assert.equal(env.code.hasAttribute("required"), false);
  assert.equal(env.thinking.hasAttribute("required"), false);
  assert.ok(env.title.hasAttribute("required"), "题名仍是必填");
  assert.equal(env.codeLabel.hidden, true, "代码整块隐藏");
  assert.equal(env.thinkingSection.hidden, true, "思路整节隐藏");
  assert.equal(env.hint.textContent, QUICK_HINT);
});

test("quick: 关闭速记，一切恢复（required/显隐/提示文案）", () => {
  const env = environment();
  env.setQuick(true);
  env.setQuick(false);
  assert.equal(env.isQuick(), false);
  assert.ok(env.code.hasAttribute("required"));
  assert.ok(env.thinking.hasAttribute("required"));
  assert.equal(env.codeLabel.hidden, false);
  assert.equal(env.thinkingSection.hidden, false);
  assert.equal(env.hint.textContent, HINT_ORIGINAL);
});

/* ---------------- 请求体字段 ---------------- */

test("quick: 速记请求体带 quick:true，留空的易错点被过滤", () => {
  const env = environment();
  const body = env.payload(true);
  assert.equal(body.quick, true);
  assert.deepEqual(Array.from(body.mistakes), [], "隐藏的易错点不提交，速记交给后端自动建待补易错点");
  assert.equal(body.title, "两数之和");
  assert.equal(body.zone, "算法");
  assert.equal(body.language, "Python");
  assert.equal(body.code, "");
  assert.equal(body.thinking, "");
});

test("quick: 普通请求体带 quick:false，易错点原样提交（含空串）", () => {
  const env = environment();
  const body = env.payload(false);
  assert.equal(body.quick, false);
  assert.deepEqual(body.mistakes, ["", "边界条件没考虑"], "普通模式字段与原来一字不差");
});

/* ---------------- 提交与复位 ---------------- */

test("quick: 速记提交发 quick:true，成功后开关复位、界面恢复普通模式", async () => {
  const env = environment();
  env.setQuick(true);
  env.submit();
  await settle();

  assert.equal(env.requests.length, 1);
  assert.equal(env.requests[0].path, "/api/problems");
  const body = lastBody(env);
  assert.equal(body.quick, true);
  assert.deepEqual(body.mistakes, []);

  assert.equal(env.toggle.value, "full", "开关复位");
  assert.equal(env.isQuick(), false);
  assert.ok(env.code.hasAttribute("required"), "required 恢复");
  assert.ok(env.thinking.hasAttribute("required"));
  assert.equal(env.codeLabel.hidden, false);
  assert.equal(env.thinkingSection.hidden, false);
  assert.equal(env.hint.textContent, HINT_ORIGINAL);
  assert.equal(env.probe.error, null);
});

test("quick: 普通提交发 quick:false，校验与原来一致，不碰界面", async () => {
  const env = environment();
  env.submit();
  await settle();

  assert.equal(env.requests.length, 1);
  const body = lastBody(env);
  assert.equal(body.quick, false);
  assert.deepEqual(body.mistakes, ["", "边界条件没考虑"]);

  assert.equal(env.toggle.value, "full");
  assert.ok(env.code.hasAttribute("required"));
  assert.equal(env.codeLabel.hidden, false);
  assert.equal(env.thinkingSection.hidden, false);
  assert.equal(env.hint.textContent, HINT_ORIGINAL);
});

test("quick: 提交失败不复位，速记状态保留给用户重试", async () => {
  const env = environment({ failApi: true });
  env.setQuick(true);
  env.submit();
  await settle();

  assert.equal(env.requests.length, 1);
  assert.ok(env.probe.error, "失败被 run 吞掉并记录");
  assert.equal(env.toggle.value, "quick", "开关保持打开");
  assert.equal(env.code.hasAttribute("required"), false, "required 保持去掉");
  assert.equal(env.codeLabel.hidden, true);
  assert.equal(env.hint.textContent, QUICK_HINT);
});


test("three modes: one sentence submits only the written reason", () => {
  const env = environment();
  env.setMode("sentence");
  env.sentence.value = "忘记检查空列表";
  const body = env.payload(true);
  assert.equal(body.quick, true);
  assert.deepEqual(Array.from(body.mistakes), ["忘记检查空列表"]);
  assert.equal(env.sentenceLabel.hidden, false);
  assert.equal(env.sentence.required, true);
  assert.equal(env.mistakesSection.hidden, true);
});

test("three modes: quick never submits hidden old mistake inputs", () => {
  const env = environment();
  env.setMode("quick");
  assert.deepEqual(Array.from(env.payload(true).mistakes), []);
  assert.equal(env.sentenceLabel.hidden, true);
  assert.equal(env.mistakesSection.hidden, true);
});

test("three modes: full restores original fields and retains written data", () => {
  const env = environment();
  env.code.value = "print(1)";
  env.setMode("sentence");
  env.setMode("full");
  assert.equal(env.code.value, "print(1)");
  assert.equal(env.code.hasAttribute("required"), true);
  assert.equal(env.thinking.hasAttribute("required"), true);
  assert.equal(env.mistakesSection.hidden, false);
  assert.equal(env.sentence.required, false);
  assert.equal(env.sentenceLabel.hidden, true);
});


test("creation: a late response cannot reset a different session's form", async () => {
  const env = environment();
  env.probe.waitForApi = true;
  env.setMode("quick");
  env.submit();
  await tick();
  vm.runInContext("sessionEpoch += 1; user = { id: 2 };", env.context);
  env.code.value = "new session draft";
  env.probe.resolveApi();
  await settle();
  assert.equal(env.toggle.value, "quick");
  assert.equal(env.code.value, "new session draft");
  assert.equal(env.requests.length, 1);
});

test("one sentence: whitespace cannot create a pending reason by accident", async () => {
  const env = environment();
  env.setMode("sentence");
  env.sentence.value = "   ";
  env.submit();
  await settle();
  assert.equal(env.requests.length, 0);
  assert.equal(env.toggle.value, "sentence");
});


test("creation: a late failure cannot notify a different session", async () => {
  const env = environment({ failApi: true });
  env.probe.waitForApi = true;
  env.setMode("quick");
  env.submit();
  await tick();
  vm.runInContext("sessionEpoch += 1; user = { id: 2 };", env.context);
  env.probe.resolveApi();
  await settle();
  assert.equal(env.probe.error, null);
  assert.equal(env.toggle.value, "quick");
});


test("creation: a session change during navigation cannot open the old record", async () => {
  const env = environment();
  env.probe.waitForView = true;
  env.setMode("quick");
  env.submit();
  await tick();
  vm.runInContext("sessionEpoch += 1; user = { id: 2 };", env.context);
  env.probe.resolveView();
  await settle();
  assert.deepEqual(env.probe.opened || [], []);
  assert.deepEqual(env.probe.messages || [], []);
});

test("creation: a session change during detail loading cannot show the old notice", async () => {
  const env = environment();
  env.probe.waitForDetail = true;
  env.setMode("quick");
  env.submit();
  await tick();
  vm.runInContext("sessionEpoch += 1; user = { id: 2 };", env.context);
  env.probe.resolveDetail();
  await settle();
  assert.deepEqual(env.probe.messages || [], []);
});

test("creation: a late navigation failure cannot notify a new session", async () => {
  const env = environment();
  env.probe.waitForView = true;
  env.probe.failView = true;
  env.setMode("quick");
  env.submit();
  await tick();
  vm.runInContext("sessionEpoch += 1; user = { id: 2 };", env.context);
  env.probe.resolveView();
  await settle();
  assert.equal(env.probe.error, null);
});
