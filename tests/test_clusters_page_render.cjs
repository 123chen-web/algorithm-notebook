"use strict";

// A deliberately small DOM executes the unmodified page script. No browser,
// network, database or temporary files are involved; layout is not tested here.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));

class Node {
  constructor(tag, attrs = {}) {
    this.tagName = tag;
    this.attributes = { ...attrs };
    this.hidden = Object.hasOwn(attrs, "hidden");
    this.disabled = Object.hasOwn(attrs, "disabled");
    this.children = [];
    this.dataset = {};
    this._text = "";
    this.listeners = {};
    this.style = { setProperty() {} };
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      toggle: (name, enabled) => {
        const names = new Set(this.className.split(/\s+/).filter(Boolean));
        if (enabled) names.add(name); else names.delete(name);
        this.className = [...names].join(" ");
      },
    };
  }
  get className() { return this.attributes.class || ""; }
  set className(value) { this.attributes.class = value; }
  get textContent() { return this._text + this.children.map((child) => child.textContent).join(""); }
  set textContent(value) { this._text = value == null ? "" : String(value); this.children = []; }
  set innerHTML(_) { throw new Error("Report text must never become HTML"); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this._text = ""; this.children = [...children]; }
  addEventListener(name, callback) { (this.listeners[name] ??= []).push(callback); }
  async click() {
    if (this.disabled) return;
    for (const callback of this.listeners.click || []) await callback({ target: this });
  }
}

function descendants(node) { return node.children.flatMap((child) => [child, ...descendants(child)]); }
function byClass(node, name) { return descendants(node).filter((child) => child.classList.contains(name)); }
function deferred() {
  let resolve, reject;
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
function report(overrides = {}) {
  return { status: "not_generated", message: null, minimum_mistakes: 6, mistake_count: 8, today: "2026-10-02", insight: null, new_since: 0, ...overrides };
}
function ready(overrides = {}) {
  const members = [
    { mistake_id: 11, title: "<img src=x onerror=alert(1)>", zone: "算法", problem_id: 101, description: "<script>不要执行</script>", due_date: "2026-10-01" },
    { mistake_id: 12, title: "闭区间终点", zone: "高等数学", problem_id: 102, description: "最后一点没有单独验证", due_date: "2026-10-02" },
    { mistake_id: 13, title: "循环停止条件", zone: "后端", problem_id: 103, description: "退出时多走了一步", due_date: "2026-10-08" },
  ];
  return report({ status: "ready", new_since: 3, insight: {
    created_at: "2026-10-01T12:00:00+00:00", content: {
      summary: "<b>区间的端点需要先说明</b>", sample: { mistake_count: 8 }, clusters: [
        { title: "区间边界没想清", explanation: "<svg/onload=alert(1)>", tip: "先写出端点是否包含，再做一个最小用例。", members },
        { title: "概念混淆", explanation: "条件与结论没有分清。", tip: "各写一个定义和反例。", members: [
          { ...members[0], mistake_id: 14, problem_id: 104, due_date: "2026-10-20" },
          { ...members[1], mistake_id: 15, problem_id: 105, due_date: null },
        ] },
      ],
    },
  }, ...overrides });
}

function harness(options = {}) {
  const nodes = Object.fromEntries(Object.entries(payload.ids).map(([id, attrs]) => [id, new Node(payload.tags[id], attrs)]));
  const get = (id) => { assert.ok(nodes[id], `Unknown DOM id ${id}`); return nodes[id]; };
  get("clusters-page").hidden = false;
  const events = [];
  const listeners = {};
  const calls = [];
  const trendCalls = []; // 徽标读取每个成员的复习记录（GET /api/mistakes/{id}）；单独记，不混进 calls
  const practices = [];
  let saved = options.report || ready();
  const profile = { id: 1, ai_daily_remaining: 5, ai_daily_limit: 10 };
  const context = {
    user: { ...profile }, window: { scrollTo() {} }, view: "clusters", busy: false,
    finishHomeOpening: null, achievementsGeneration: 0, groupsGeneration: 0,
    stopOrderPolling() {}, cancelAchievementStamps() {}, renderUserInfo() {}, message() {},
    document: {
      body: new Node("body"),
      querySelector: (selector) => get(selector.slice(1)),
      querySelectorAll: (selector) => {
        assert.equal(selector, "button");
        return Object.values(nodes).flatMap((node) => [node, ...descendants(node)]).filter((node) => node.tagName === "button");
      },
      createElement: (tag) => new Node(tag),
      addEventListener: (name, callback) => (listeners[name] ??= []).push(callback),
      dispatchEvent: (event) => { events.push(event); for (const callback of listeners[event.type] || []) callback(event); return true; },
    },
    CustomEvent: class CustomEvent { constructor(type, options = {}) { this.type = type; this.detail = options.detail; } },
    updateUserInfo: () => {
      get("home-quota").hidden = context.user.ai_daily_limit <= 0;
      if (!get("home-quota").hidden) get("home-quota-text").textContent = `剩余 ${context.user.ai_daily_remaining} / ${context.user.ai_daily_limit} 次`;
    },
    timestamp: (text) => text,
  };
  // 徽标按成员读取复习记录（GET /api/mistakes/{id}）：任何场景里（含各测试自己换掉的 api）都由这里先接走，
  // 不计入 calls / reads，这样原有"读了几次、写了几次"的断言只数专题与额度请求。
  const baseApi = async (path, args = {}) => {
    calls.push({ path, args });
    if (path === "/api/me") return { ...profile };
    if (path === "/api/insights/clusters") return saved;
    throw new Error(`Unknown API ${path}`);
  };
  let currentApi = baseApi;
  Object.defineProperty(context, "api", {
    enumerable: true,
    get: () => async (path, args) => {
      const member = /^\/api\/mistakes\/(\d+)$/.exec(path);
      if (member) {
        trendCalls.push(Number(member[1]));
        if (options.reviews === null || (options.failIds || []).includes(Number(member[1]))) throw new Error("reviews unavailable");
        if (options.trendGate) await options.trendGate.promise;
        return { id: Number(member[1]), reviews: (options.reviews || {})[member[1]] || [] };
      }
      return currentApi(path, args);
    },
    set: (replacement) => { currentApi = replacement; },
  });
  context.$ = context.document.querySelector;
  context.renderHomeQuota = context.updateUserInfo;
  if (options.focus !== false) context.window.FocusReview = { start: (args) => practices.push(JSON.parse(JSON.stringify(args))) };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(require("node:path").join(__dirname, "../static/problem-cards.js"), "utf8"), context, { timeout: 1000 });
  vm.runInContext(payload.practice, context, { timeout: 1000 });
  vm.runInContext(payload.source, context, { timeout: 1000 });
  vm.runInContext(payload.appBehavior, context, { timeout: 1000 });
  const setReport = (value) => { saved = value; };
  return { context, get, calls, trendCalls, events, practices, profile, setReport, page: context.window.Clusters };
}

async function states() {
  for (const count of [0, 3, 5]) {
    const h = harness({ report: report({ status: "insufficient_data", mistake_count: count, message: "再积累几条具体错因，本次不会调用 AI，也不消耗额度。" }) });
    await h.page.load();
    assert.equal(h.get("clusters-generate").getAttribute("aria-disabled"), "true");
    assert.equal(h.get("clusters-page").getAttribute("aria-busy"), "false");
    assert.ok(h.get("clusters-empty-title").textContent.includes(String(6 - count)));
    assert.ok(h.get("clusters-empty-text").textContent.includes("不消耗额度"));
    await h.get("clusters-generate").click();
    assert.ok(h.calls.every((call) => call.args.method !== "POST"));
  }
  const h = harness({ report: report() });
  await h.page.load();
  assert.equal(h.get("clusters-result").hidden, true);
  assert.equal(h.get("clusters-empty").hidden, false);
  assert.equal(h.get("clusters-generate").getAttribute("aria-disabled"), "false");
  assert.equal(h.get("clusters-generate").textContent, "归并相似错因 · 消耗 1 次 AI 额度");
  assert.ok(h.get("clusters-quota").textContent.includes("剩余 5 / 10 次"));
  h.profile.ai_daily_remaining = 0;
  await h.page.load();
  assert.equal(h.get("clusters-generate").getAttribute("aria-disabled"), "true");
  assert.ok(h.get("clusters-quota").textContent.includes("明天"));
  h.profile.ai_daily_limit = 0;
  h.get("home-quota-text").textContent = "剩余 2 / 2 次";
  await h.page.load();
  assert.equal(h.get("clusters-generate").getAttribute("aria-disabled"), "true");
  assert.ok(h.get("clusters-quota").textContent.includes("0 / 0"));
  assert.ok(!h.get("clusters-quota").textContent.includes("2 / 2"));
  assert.ok(!h.get("clusters-quota").textContent.includes("明天"));
}

async function readyPage() {
  const h = harness();
  await h.page.load();
  const result = h.get("clusters-result");
  const cards = byClass(result, "clusters-card");
  assert.equal(cards.length, 2);
  assert.equal(byClass(cards[0], "clusters-member").length, 3);
  assert.equal(byClass(result, "clusters-summary")[0].textContent, "<b>区间的端点需要先说明</b>");
  assert.equal(byClass(cards[0], "problem-card-open")[0].children[0].textContent, "<img src=x onerror=alert(1)>");
  assert.equal(descendants(result).filter((item) => ["img", "script", "svg", "b"].includes(item.tagName)).length, 0);
  assert.equal(byClass(cards[0], "problem-card-summary").map((item) => item.textContent).join("|"), "待复习 1 条 · 共 1 条易错点|待复习 1 条 · 共 1 条易错点|待复习 0 条 · 共 1 条易错点");
  assert.equal(h.get("clusters-new").textContent, "有 3 条新错题还没归并");
  assert.equal(h.get("clusters-new").hidden, false);
  assert.equal(h.get("clusters-generate").textContent, "重新归并 · 消耗 1 次 AI 额度");
  await byClass(cards[0], "problem-card-open")[0].click();
  const navigation = h.events.find((event) => event.type === "app:navigate");
  assert.deepEqual(JSON.parse(JSON.stringify(navigation.detail)), { view: "all", recordId: 11 });
  // 每个专题卡片底部都有"现在就练这个专题"；第二个专题没有到期的，按钮禁用并写明原因。
  const practices = byClass(result, "clusters-practise");
  assert.equal(practices.length, 2);
  assert.equal(practices[0].textContent, "现在就练这个专题（2 条）");
  assert.equal(practices[0].disabled, false);
  assert.equal(practices[1].disabled, true);
  assert.equal(byClass(cards[1], "practice-now-note")[0].textContent, "这个专题今天没有要复习的");
  await practices[1].click();
  assert.deepEqual(h.practices, [], "a disabled button starts nothing");
  await practices[0].click();
  assert.deepEqual(h.practices, [{ ids: [11, 12] }]);
}

async function noFocus() {
  const h = harness({ focus: false });
  await h.page.load();
  assert.equal(byClass(h.get("clusters-result"), "clusters-practise").length, 0);
}

async function futureOnly() {
  const data = ready();
  for (const cluster of data.insight.content.clusters) for (const member of cluster.members) member.due_date = "2026-10-10";
  const h = harness({ report: data });
  await h.page.load();
  const buttons = byClass(h.get("clusters-result"), "clusters-practise");
  assert.equal(buttons.length, 2, "no due member: the button stays but is disabled");
  assert.ok(buttons.every((button) => button.disabled));
  await buttons[0].click();
  assert.deepEqual(h.practices, []);
}

async function failureQuota() {
  const h = harness();
  await h.page.load();
  const post = deferred();
  let posts = 0;
  h.context.api = async (path, args = {}) => {
    if (path === "/api/me") return { ...h.profile };
    assert.equal(args.method, "POST");
    posts += 1;
    return post.promise;
  };
  const generating = h.get("clusters-generate").click();
  assert.equal(h.get("clusters-page").getAttribute("aria-busy"), "true");
  assert.equal(h.get("clusters-generate").getAttribute("aria-disabled"), "true");
  await h.get("clusters-generate").click();
  assert.equal(posts, 1, "Duplicate click must not launch another request");
  post.reject(new Error("服务端尚未配置 AI 服务密钥"));
  await generating;
  assert.ok(h.get("clusters-status").textContent.includes("服务端尚未配置 AI 服务密钥"));
  assert.equal(h.context.user.ai_daily_remaining, 5);
  assert.equal(h.get("clusters-retry").hidden, false);
  assert.ok(h.get("clusters-retry").textContent.includes("消耗 1 次 AI 额度"));
  assert.equal(byClass(h.get("clusters-result"), "clusters-card").length, 2);
  await byClass(h.get("clusters-result"), "problem-card-open")[0].click();
  await byClass(h.get("clusters-result"), "clusters-practise")[0].click();
  assert.equal(h.events.filter((event) => event.type === "app:navigate").length, 1);
  assert.deepEqual(h.practices, [{ ids: [11, 12] }], "Retained cards must remain usable after an error");
  h.context.api = async (path) => path === "/api/me" ? { ...h.profile, ai_daily_remaining: 4 } : ready();
  await h.get("clusters-retry").click();
  assert.equal(h.context.user.ai_daily_remaining, 4);
  assert.ok(h.get("clusters-quota").textContent.includes("剩余 4 / 10 次"));
  assert.equal(h.get("clusters-retry").hidden, true);
}

async function quotaUnavailable() {
  const h = harness();
  await h.page.load();
  h.context.api = async (path) => {
    if (path === "/api/me") throw new Error("profile offline");
    throw new Error("AI 请求失败");
  };
  await h.get("clusters-generate").click();
  assert.ok(h.get("clusters-quota").textContent.includes("暂时无法读取"));
  assert.ok(!h.get("clusters-quota").textContent.includes("剩余 5"));
  assert.equal(h.get("clusters-page").getAttribute("aria-busy"), "false");
}

async function staleLoad() {
  const h = harness();
  const first = deferred(), second = deferred();
  let count = 0;
  h.context.api = async (path) => path === "/api/me" ? { ...h.profile } : ++count === 1 ? first.promise : second.promise;
  const one = h.page.load();
  const two = h.page.load();
  const newest = ready();
  newest.insight.content.summary = "最新一份";
  second.resolve(newest);
  await two;
  const old = ready();
  old.insight.content.summary = "旧一份";
  first.resolve(old);
  await one;
  assert.equal(byClass(h.get("clusters-result"), "clusters-summary")[0].textContent, "最新一份");
}

async function accountSwitch() {
  const h = harness();
  const old = deferred();
  h.context.api = async (path) => path === "/api/me" ? old.promise : ready();
  const loading = h.page.load();
  h.page.reset();
  h.context.user = { id: 2, ai_daily_remaining: 9, ai_daily_limit: 10 };
  old.resolve({ ...h.profile });
  await loading;
  assert.equal(h.context.user.id, 2);
  assert.equal(h.get("clusters-result").children.length, 0);
  assert.equal(h.get("clusters-status").textContent, "");
  h.context.api = async (path) => path === "/api/me" ? { ...h.profile } : ready();
  await h.page.load();
  assert.equal(h.context.user.id, 2, "An unexpected profile id must not change the active account");
  assert.ok(h.get("clusters-quota").textContent.includes("暂时无法读取"));
}

async function resetGeneration() {
  const h = harness();
  await h.page.load();
  const old = deferred();
  h.context.api = async () => old.promise;
  const generating = h.get("clusters-generate").click();
  h.page.reset();
  old.resolve(ready());
  await generating;
  assert.equal(h.get("clusters-result").children.length, 0);
  assert.equal(h.get("clusters-new").textContent, "");
  assert.equal(h.get("clusters-quota").textContent, "");
  assert.equal(h.get("clusters-status").textContent, "");
  assert.equal(h.get("clusters-page").getAttribute("aria-busy"), "false");
}

async function loadError() {
  const h = harness();
  await h.page.load();
  h.context.api = async (path) => {
    if (path === "/api/me") return { ...h.profile };
    throw new Error("读取失败文案");
  };
  await h.page.load();
  assert.equal(h.get("clusters-retry").hidden, false);
  assert.ok(h.get("clusters-status").textContent.includes("读取失败文案"));
  assert.equal(byClass(h.get("clusters-result"), "clusters-card").length, 2);
  await byClass(h.get("clusters-result"), "problem-card-open")[0].click();
  await byClass(h.get("clusters-result"), "clusters-practise")[0].click();
  assert.equal(h.practices.length, 1);
  assert.equal(h.events.filter((event) => event.type === "app:navigate").length, 1);
  h.context.api = async (path) => path === "/api/me" ? { ...h.profile } : report();
  await h.get("clusters-retry").click();
  assert.equal(h.get("clusters-result").hidden, true);
  assert.equal(h.get("clusters-retry").hidden, true);
}

async function dataChanged() {
  const h = harness();
  await h.page.load();
  const prior = h.calls.length;
  h.context.document.dispatchEvent(new h.context.CustomEvent("app:data-changed"));
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(h.calls.length, prior + 2);
  h.get("clusters-page").hidden = true;
  h.context.document.dispatchEvent(new h.context.CustomEvent("app:data-changed"));
  assert.equal(h.calls.length, prior + 2);
}

async function settle() {
  for (let i = 0; i < 12; i += 1) await Promise.resolve();
}

async function generationGlobalBusy() {
  const h = harness();
  await h.page.load();
  const post = deferred();
  let posts = 0;
  h.context.api = async (path, args = {}) => {
    if (path === "/api/me") return { ...h.profile };
    if (args.method !== "POST") return ready();
    posts += 1;
    return post.promise;
  };
  const generating = h.get("clusters-generate").click();
  await h.context.run(() => h.page.load());
  assert.equal(h.get("app").getAttribute("aria-busy"), "false");
  assert.equal(h.get("clusters-generate").disabled, true);
  assert.equal(posts, 1);
  post.resolve(ready());
  await generating;
  assert.equal(h.get("clusters-generate").dataset.blocked, "0");
  assert.equal(h.get("clusters-generate").getAttribute("aria-disabled"), "false");
  assert.equal(h.get("clusters-generate").disabled, false, "Global refresh must not leave generation disabled");
  await h.get("clusters-generate").click();
  assert.equal(posts, 2, "The restored button must accept another click");

  h.context.setBusy(true);
  await h.page.load();
  assert.equal(h.get("clusters-generate").dataset.blocked, "0");
  assert.equal(h.get("clusters-generate").disabled, true, "Page rendering must respect active global busy state");
  h.context.setBusy(false);
  assert.equal(h.get("clusters-generate").disabled, false);
}

async function deferredRefresh(events = ["data", "data", "focus"]) {
  const h = harness();
  await h.page.load();
  const post = deferred(), quota = deferred();
  let profiles = 0, reads = 0, posts = 0;
  const refreshed = ready();
  refreshed.insight.content.clusters[0].members[0].due_date = "2026-10-20";
  h.context.api = async (path, args = {}) => {
    if (path === "/api/me") return ++profiles === 1 ? quota.promise : { ...h.profile };
    if (args.method === "POST") { posts += 1; return post.promise; }
    reads += 1;
    return refreshed;
  };
  const generating = h.get("clusters-generate").click();
  post.resolve(ready());
  await settle();
  assert.equal(profiles, 1, "The quota read must still be pending after the result renders");
  assert.equal(h.get("clusters-page").getAttribute("aria-busy"), "true");
  for (const kind of events) {
    if (kind === "data") h.context.document.dispatchEvent(new h.context.CustomEvent("app:data-changed"));
    else h.context.document.dispatchEvent(new h.context.CustomEvent("focus:closed", { detail: { graded: true, view: "clusters" } }));
  }
  await settle();
  assert.equal(reads, 0, "Changes during the quota read must wait for generation to finish");
  quota.resolve({ ...h.profile, ai_daily_remaining: 4 });
  await generating;
  await settle();
  assert.equal(posts, 1);
  assert.equal(reads, 1, "Data changes and focus completion must coalesce into one follow-up read");
  assert.equal(profiles, 2);
  assert.equal(byClass(h.get("clusters-result"), "problem-card-summary")[0].textContent, "待复习 0 条 · 共 1 条易错点");
  assert.equal(byClass(h.get("clusters-result"), "clusters-practise")[0].textContent, "现在就练这个专题（1 条）");
  assert.equal(h.get("clusters-generate").disabled, false);
}

async function focusClosed() {
  const h = harness();
  await h.page.load();
  const prior = h.calls.length;
  const changed = ready();
  changed.insight.content.clusters[0].members[0].due_date = "2026-10-20";
  h.setReport(changed);
  h.context.view = "all";
  h.get("clusters-page").hidden = true;
  h.context.document.dispatchEvent(new h.context.CustomEvent("focus:closed", { detail: { graded: true, view: "clusters" } }));
  await settle();
  assert.equal(h.context.view, "clusters");
  assert.equal(h.get("clusters-page").hidden, false);
  assert.equal(h.calls.length, prior + 2, "The real focus listener must reread saved clusters and quota");
  assert.equal(byClass(h.get("clusters-result"), "problem-card-summary")[0].textContent, "待复习 0 条 · 共 1 条易错点");
}

async function resetRefresh() {
  const h = harness();
  await h.page.load();
  const post = deferred();
  let reads = 0;
  h.context.api = async (path, args = {}) => {
    if (path === "/api/me") return { ...h.profile };
    if (args.method === "POST") return post.promise;
    reads += 1;
    return ready();
  };
  const generating = h.get("clusters-generate").click();
  h.context.document.dispatchEvent(new h.context.CustomEvent("app:data-changed"));
  h.page.reset();
  post.resolve(ready());
  await generating;
  assert.equal(reads, 0);
  h.get("clusters-page").hidden = false;
  await h.page.load();
  assert.equal(reads, 1, "Reset must clear refresh requests from the prior page generation");
}

async function zeroClusters() {
  const data = ready();
  data.insight.content.clusters = [];
  data.insight.content.summary = "暂时看不出可以归并的共性。";
  const h = harness({ report: data });
  await h.page.load();
  assert.equal(h.get("clusters-result").hidden, false);
  assert.equal(byClass(h.get("clusters-result"), "clusters-card").length, 0);
  assert.ok(byClass(h.get("clusters-result"), "clusters-no-common")[0].textContent.includes("暂时看不出"));
}

async function navigationQuota() {
  for (const hide of [true, false]) {
    const h = harness();
    const delayedReport = deferred();
    h.context.api = async (path) => path === "/api/me" ? { ...h.profile } : delayedReport.promise;
    const loading = h.page.load();
    await Promise.resolve();
    h.get("clusters-page").hidden = hide;
    h.context.user = { ...h.profile, ai_daily_remaining: 3 };
    delayedReport.resolve(ready());
    await loading;
    assert.equal(h.context.user.ai_daily_remaining, 3, "Old profile must not overwrite a newer shared quota");
  }
  const h = harness();
  await h.page.load();
  const post = deferred();
  h.context.api = async (path) => path === "/api/me" ? { ...h.profile } : post.promise;
  const generating = h.get("clusters-generate").click();
  h.get("clusters-page").hidden = true;
  h.context.user = { ...h.profile, ai_daily_remaining: 2 };
  post.resolve(ready());
  await generating;
  assert.equal(h.context.user.ai_daily_remaining, 2, "A generation completed off-page must not replace global user information");
}

/* ---------------- 专题徽标与"现在就练这个专题" ---------------- */
const TODAY = "2026-10-02";
function dayBefore(days) {
  const date = new Date(Date.UTC(2026, 9, 2 - days, 8, 0, 0));
  return `${date.toISOString().slice(0, 19)}+00:00`;
}
// 例：review(0, 2) = 今天打了 2 分；review(14, 4) = 14 天前打了 4 分。
const review = (daysAgo, quality) => ({ quality, reviewed_at: dayBefore(daysAgo) });
const repeat = (count, make) => Array.from({ length: count }, (_, index) => make(index));
const trend = (h, reviews) => {
  const result = h.page.trendOf(reviews, TODAY);
  return result && JSON.parse(JSON.stringify(result));
};

async function trendRules() {
  const h = harness();
  const T = h.page.TREND;
  assert.deepEqual(JSON.parse(JSON.stringify(T)), { windowDays: 14, minRecent: 3, failBelow: 3, improvePoints: 20, repeatPercent: 40, concurrency: 4 });

  // 样本不足：最近 14 天少于 3 次（没有任何复习也算）。
  assert.equal(trend(h, []).kind, "sparse");
  assert.equal(trend(h, [review(0, 0), review(1, 0)]).kind, "sparse");
  assert.ok(trend(h, [review(0, 0), review(1, 0)]).text.includes("2 次"));
  assert.equal(trend(h, [review(0, 5), review(1, 5), review(2, 5), review(30, 0)]).kind !== "sparse", true, "3 次刚好够");
  // 最近 14 天 = 距今 0–13 天；14 天前算"更早"。
  assert.equal(trend(h, [review(13, 0), review(13, 0), review(13, 0)]), null, "13 天前仍是最近，且没有更早的可比");
  assert.equal(trend(h, [review(14, 0), review(14, 0), review(14, 0)]).kind, "sparse", "14 天前不算最近");
  // 没有更早的复习：没法比较，不显示。
  assert.equal(trend(h, repeat(6, () => review(1, 0))), null);

  // 已改善：失败占比下降 ≥ 20 个百分点；恰好 20 算，19.4 不算。
  const improved = trend(h, [...repeat(4, () => review(1, 5)), review(2, 1), ...repeat(3, () => review(20, 5)), ...repeat(2, () => review(21, 0))]);
  assert.equal(improved.kind, "improved", "20% vs 40% 恰好下降 20 个百分点");
  assert.equal(improved.text, "最近两周答错的比例 20%，之前是 40%");
  const notEnough = trend(h, [...repeat(3, () => review(1, 5)), review(2, 1), ...repeat(5, () => review(20, 5)), ...repeat(4, () => review(21, 0))]);
  assert.equal(notEnough, null, "25% vs 44% 只下降 19.4 个百分点，且已经低于 40%：不显示");
  // 页面示例文案：最近 18%，之前 47%。
  const sample = trend(h, [...repeat(2, () => review(3, 0)), ...repeat(9, () => review(4, 5)), ...repeat(8, () => review(30, 1)), ...repeat(9, () => review(31, 4))]);
  assert.equal(sample.kind, "improved");
  assert.equal(sample.text, "最近两周答错的比例 18%，之前是 47%");
  // 分数 3 不算答错，2 才算。
  assert.equal(trend(h, [...repeat(5, () => review(1, 3)), ...repeat(5, () => review(20, 2))]).kind, "improved");
  assert.equal(trend(h, [...repeat(5, () => review(1, 2)), ...repeat(5, () => review(20, 3))]).kind, "repeating");

  // 仍在反复：占比持平或上升，且最近仍 ≥ 40%。
  assert.equal(trend(h, [...repeat(3, () => review(1, 5)), ...repeat(2, () => review(2, 0)), ...repeat(3, () => review(20, 5)), ...repeat(2, () => review(21, 0))]).kind, "repeating", "40% 持平且恰好 40%");
  assert.equal(trend(h, [...repeat(2, () => review(1, 0)), ...repeat(3, () => review(2, 5)), ...repeat(8, () => review(20, 5)), ...repeat(2, () => review(21, 0))]).kind, "repeating", "40% 对 20%：上升");
  // 持平但低于 40%、或下降不足 20 且仍高 → 不显示 / 不算改善。
  assert.equal(trend(h, [...repeat(18, () => review(1, 5)), ...repeat(7, () => review(2, 0)), ...repeat(18, () => review(20, 5)), ...repeat(7, () => review(21, 0))]), null, "28% 持平但不到 40%");
  assert.equal(trend(h, [...repeat(3, () => review(1, 5)), ...repeat(2, () => review(2, 0)), review(20, 5), review(21, 0)]), null, "40% 对 50%：下降不足 20 个百分点，也不是持平或上升");
}

async function trendRulesBad() {
  const h = harness();
  assert.equal(h.page.trendOf("x", TODAY), null);
  assert.equal(h.page.trendOf([review(1, 0)], "not-a-date"), null);
  assert.equal(h.page.trendOf([review(1, 0)], undefined), null);
  const good = [...repeat(3, () => review(1, 0)), ...repeat(3, () => review(20, 5))];
  assert.equal(h.page.trendOf(good, TODAY).kind, "repeating");
  for (const bad of [{ quality: 0, reviewed_at: "昨天" }, { quality: 9, reviewed_at: dayBefore(1) }, { quality: "3", reviewed_at: dayBefore(1) }, { quality: 1.5, reviewed_at: dayBefore(1) }, null, { reviewed_at: dayBefore(1) }]) {
    assert.equal(h.page.trendOf([...good, bad], TODAY), null, JSON.stringify(bad));
  }
}

function trendReport() {
  return ready();
}
const badges = (h) => byClass(h.get("clusters-result"), "clusters-trend");
const visibleBadges = (h) => badges(h).filter((badge) => !badge.hidden);

async function trendBadges() {
  // 专题 1（成员 11、12、13）：近两周 20%，之前 40% → 已改善；专题 2（成员 14、15）：近两周只有 1 次 → 样本不足。
  const reviews = {
    11: [review(1, 5), review(2, 5), review(3, 1), review(20, 5), review(21, 0)],
    12: [review(1, 5), review(2, 5), review(22, 5)],
    13: [review(4, 5), review(5, 5), review(23, 0)],
    14: [review(2, 0)],
    15: [],
  };
  const h = harness({ reviews });
  await h.page.load();
  await new Promise((resolve) => setImmediate(resolve));
  const [first, second] = badges(h);
  assert.equal(first.hidden, false);
  assert.equal(first.textContent, "已改善");
  assert.ok(first.classList.contains("is-improved"));
  assert.equal(first.title, first.dataset.tip);
  assert.equal(first.dataset.tip, "最近两周答错的比例 14%，之前是 50%");
  assert.equal(first.getAttribute("aria-label"), "已改善：最近两周答错的比例 14%，之前是 50%");
  assert.equal(first.tabIndex, 0, "badge can take keyboard focus so the explanation is not hover-only");
  assert.equal(first.getAttribute("role"), "note");
  assert.equal(second.textContent, "样本不足");
  assert.ok(second.classList.contains("is-sparse"));
  assert.ok(second.dataset.tip.includes("1 次"));
  assert.deepEqual([...new Set(h.trendCalls)].sort(), [11, 12, 13, 14, 15]);
  assert.equal(h.trendCalls.length, 5, "each member is read once");
}

async function trendConcurrency() {
  const gate = deferred();
  const h = harness({ trendGate: gate, reviews: {} });
  await h.page.load();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.trendCalls.length, 4, "at most 4 reads in flight at the same time");
  assert.equal(visibleBadges(h).length, 0);
  gate.resolve();
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.trendCalls.length, 5);
  assert.equal(visibleBadges(h).length, 2, "both clusters get a (sparse) badge once all reads are in");
}

async function trendUnavailable() {
  for (const options of [{ reviews: null }, { reviews: {}, failIds: [13] }]) {
    const h = harness(options);
    await h.page.load();
    await new Promise((resolve) => setImmediate(resolve));
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(badges(h).length, 2);
    assert.equal(visibleBadges(h).length, 0, "if any member's reviews cannot be read, no badge at all");
    assert.equal(byClass(h.get("clusters-result"), "clusters-card").length, 2, "the page itself is unaffected");
  }
}

async function trendStale() {
  const gate = deferred();
  const h = harness({ trendGate: gate, reviews: {} });
  await h.page.load();
  await new Promise((resolve) => setImmediate(resolve));
  const oldBadges = badges(h);
  // 数据变化触发重读重画：旧一轮的读取回来时不能再画到旧卡片上。
  h.context.document.dispatchEvent(new h.context.CustomEvent("app:data-changed"));
  await new Promise((resolve) => setImmediate(resolve));
  const newBadges = badges(h);
  assert.notEqual(oldBadges[0], newBadges[0]);
  gate.resolve();
  for (let turn = 0; turn < 6; turn += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.ok(oldBadges.every((badge) => badge.hidden), "the superseded run never paints");
  assert.equal(visibleBadges(h).length, 2);

  // 登出（reset）之后才回来的读取：什么都不画、不抛错。
  const gate2 = deferred();
  const h2 = harness({ trendGate: gate2, reviews: {} });
  await h2.page.load();
  await new Promise((resolve) => setImmediate(resolve));
  const before = badges(h2);
  h2.page.reset();
  gate2.resolve();
  for (let turn = 0; turn < 6; turn += 1) await new Promise((resolve) => setImmediate(resolve));
  assert.ok(before.every((badge) => badge.hidden));
  assert.equal(byClass(h2.get("clusters-result"), "clusters-trend").length, 0);
}

async function practiseCap() {
  const data = ready();
  const member = (id, due, zone = "算法") => ({ mistake_id: id, title: `题${id}`, zone, problem_id: 200 + id, description: "d", due_date: due });
  data.insight.content.clusters = [{
    title: "很多到期", explanation: "e", tip: "t", members: [
      member(21, "2026-09-30"), member(22, "2026-09-25"), member(23, "2026-10-02"), member(24, "2026-09-25"),
      member(25, "2026-10-01"), member(26, "2026-10-02"), member(27, "2026-10-03"), member(28, "2026-09-20"),
    ],
  }, { title: "刚好三条", explanation: "e", tip: "t", members: [member(31, "2026-10-02"), member(32, null), member(33, "2026-10-02")] }];
  const h = harness({ report: data });
  await h.page.load();
  const buttons = byClass(h.get("clusters-result"), "clusters-practise");
  assert.equal(buttons[0].textContent, "现在就练这个专题（5 条）");
  await buttons[0].click();
  // 到期日升序（同一天按 id）、最多 5 条；未到期（27）与到期但排在后面的（23、26）不在其中。
  assert.deepEqual(h.practices, [{ ids: [28, 22, 24, 21, 25] }]);
  assert.equal(buttons[1].textContent, "现在就练这个专题（2 条）", "没有到期日的（null）不算到期");
  await buttons[1].click();
  assert.deepEqual(h.practices[1], { ids: [31, 33] });
}

async function practiseStale() {
  const h = harness();
  await h.page.load();
  const button = byClass(h.get("clusters-result"), "clusters-practise")[0];
  h.context.user = { ...h.profile, id: 2 }; // 换了账号
  await button.click();
  assert.deepEqual(h.practices, [], "a button from the previous account does nothing");
  h.context.user = { ...h.profile, id: 1 };
  await button.click();
  assert.equal(h.practices.length, 1);
  h.page.reset(); // 登出
  await button.click();
  assert.equal(h.practices.length, 1, "after reset a leftover button does nothing");
}

const scenarios = {
  states, ready: readyPage, "no-focus": noFocus, "future-only": futureOnly,
  "failure-quota": failureQuota, "quota-unavailable": quotaUnavailable,
  "stale-load": staleLoad, "account-switch": accountSwitch, "reset-generation": resetGeneration,
  "load-error": loadError, "data-changed": dataChanged, "zero-clusters": zeroClusters,
  "navigation-quota": navigationQuota,
  "generation-global-busy": generationGlobalBusy, "deferred-refresh": () => deferredRefresh(),
  "deferred-data-changed": () => deferredRefresh(["data"]),
  "deferred-focus-closed": () => deferredRefresh(["focus"]),
  "focus-closed": focusClosed, "reset-refresh": resetRefresh,
  "trend-rules": trendRules, "trend-rules-bad": trendRulesBad, "trend-badges": trendBadges,
  "trend-concurrency": trendConcurrency, "trend-unavailable": trendUnavailable, "trend-stale": trendStale,
  "practise-cap": practiseCap, "practise-stale": practiseStale,
};
assert.ok(Object.hasOwn(scenarios, payload.scenario), "Unknown rendering scenario");
Promise.resolve(scenarios[payload.scenario]()).then(() => {
  process.stdout.write(`${payload.scenario}: page behavior passed\n`);
}).catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
