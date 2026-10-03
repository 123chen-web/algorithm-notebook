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
    api: async (path, args = {}) => {
      calls.push({ path, args });
      if (path === "/api/me") return { ...profile };
      if (path === "/api/insights/clusters") return saved;
      throw new Error(`Unknown API ${path}`);
    },
  };
  context.$ = context.document.querySelector;
  context.renderHomeQuota = context.updateUserInfo;
  if (options.focus !== false) context.window.FocusReview = { start: (args) => practices.push(JSON.parse(JSON.stringify(args))) };
  vm.createContext(context);
  vm.runInContext(payload.source, context, { timeout: 1000 });
  vm.runInContext(payload.appBehavior, context, { timeout: 1000 });
  const setReport = (value) => { saved = value; };
  return { context, get, calls, events, practices, profile, setReport, page: context.window.Clusters };
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
  assert.equal(byClass(cards[0], "clusters-member-title")[0].textContent, "<img src=x onerror=alert(1)>");
  assert.equal(descendants(result).filter((item) => ["img", "script", "svg", "b"].includes(item.tagName)).length, 0);
  assert.equal(byClass(cards[0], "clusters-due").map((item) => item.textContent).join("|"), "已逾期|今天到期|2026-10-08 到期");
  assert.equal(h.get("clusters-new").textContent, "有 3 条新错题还没归并");
  assert.equal(h.get("clusters-new").hidden, false);
  assert.equal(h.get("clusters-generate").textContent, "重新归并 · 消耗 1 次 AI 额度");
  await byClass(cards[0], "clusters-member-open")[0].click();
  const navigation = h.events.find((event) => event.type === "app:navigate");
  assert.deepEqual(JSON.parse(JSON.stringify(navigation.detail)), { view: "all", recordId: 11 });
  const practices = byClass(result, "clusters-practise");
  assert.equal(practices.length, 1);
  assert.equal(practices[0].textContent, "一起复习这 2 条到期的");
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
  assert.equal(byClass(h.get("clusters-result"), "clusters-practise").length, 0);
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
  post.reject(new Error("服务端尚未配置 OpenAI API Key"));
  await generating;
  assert.ok(h.get("clusters-status").textContent.includes("服务端尚未配置 OpenAI API Key"));
  assert.equal(h.context.user.ai_daily_remaining, 5);
  assert.equal(h.get("clusters-retry").hidden, false);
  assert.ok(h.get("clusters-retry").textContent.includes("消耗 1 次 AI 额度"));
  assert.equal(byClass(h.get("clusters-result"), "clusters-card").length, 2);
  await byClass(h.get("clusters-result"), "clusters-member-open")[0].click();
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
  await byClass(h.get("clusters-result"), "clusters-member-open")[0].click();
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
  assert.equal(byClass(h.get("clusters-result"), "clusters-due")[0].textContent, "2026-10-20 到期");
  assert.equal(byClass(h.get("clusters-result"), "clusters-practise")[0].textContent, "一起复习这 1 条到期的");
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
  assert.equal(byClass(h.get("clusters-result"), "clusters-due")[0].textContent, "2026-10-20 到期");
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
};
assert.ok(Object.hasOwn(scenarios, payload.scenario), "Unknown rendering scenario");
Promise.resolve(scenarios[payload.scenario]()).then(() => {
  process.stdout.write(`${payload.scenario}: page behavior passed\n`);
}).catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
