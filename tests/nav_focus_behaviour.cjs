/**
 * Node 假浏览器行为测试：专注模式（nav-focus.js）。
 * 覆盖：两个开关同步、默认关闭、侧栏/更多抽屉/总览卡片收起、手机底栏“小组”换“分析”、
 * localStorage 按用户 id 持久化、换号不串、localStorage 抛异常按关闭处理、
 * 键盘可达（真实 button）、被收起页面顶部轻提示与关闭按钮、reset 还原、data-admin-only 不被误展开。
 * 运行：node --test tests/nav_focus_behaviour.cjs
 */
"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const { load, FakeEvent } = require("./js_harness.cjs");

const STATIC = path.join(__dirname, "..", "static");
const HIDDEN_VIEWS = ["groups", "forum", "leaderboard", "achievements", "weekly-recap", "plan"];
const KEPT_VIEWS = ["home", "today", "all", "new", "insights", "mastery", "clusters"];

/** 把真实 index.html 的 <body> 解析进假文档（做法同 thread_behaviour.cjs）。 */
function mountBody(document) {
  const html = fs.readFileSync(path.join(STATIC, "index.html"), "utf8");
  const body = html.slice(html.indexOf("<body"), html.indexOf("</body>"));
  const stack = [document.body];
  const voids = new Set(["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"]);
  for (const match of body.matchAll(/<\/?([a-z][\w-]*)([^>]*)>|([^<]+)/gi)) {
    if (match[3]) {
      const text = match[3].trim();
      if (text) stack.at(-1).append(text);
      continue;
    }
    const tag = match[1].toLowerCase();
    if (match[0].startsWith("</")) {
      if (stack.length > 1) stack.pop();
      continue;
    }
    const element = document.createElement(tag);
    for (const attribute of match[2].matchAll(/([\w-]+)(?:="([^"]*)")?/g)) {
      const [, name, value = ""] = attribute;
      if (name === "hidden") { element.hidden = true; continue; }
      element.setAttribute(name, value);
      if (name.startsWith("data-")) {
        element.dataset[name.slice(5).replace(/-([a-z])/g, (_all, char) => char.toUpperCase())] = value;
      }
      if (["type", "role"].includes(name)) element[name] = value;
    }
    stack.at(-1).append(element);
    if (!voids.has(tag) && !match[0].endsWith("/>")) stack.push(element);
  }
}

// 假浏览器里 document 不参与冒泡：document 级委托监听直接在 document 上派发，target 指向被点元素。
function activate(document, element) {
  document.dispatchEvent(new FakeEvent("click", { bubbles: true, props: { target: element } }));
}

function makeStorage(backing = new Map()) {
  return {
    backing,
    localStorage: {
      getItem: (key) => (backing.has(key) ? backing.get(key) : null),
      setItem: (key, value) => backing.set(key, String(value)),
      removeItem: (key) => backing.delete(key),
    },
  };
}

function loadFocus(options = {}) {
  const storage = options.storage || makeStorage();
  const env = load(["nav-focus.js"], {
    extra: { URL: "http://localhost:8000/static/index.html", ...storage },
  });
  mountBody(env.document);
  let view = options.view || "home";
  env.window.NavFocus.configure({
    getUser: () => (options.signedOut ? null : (options.user || { id: 1, username: "alice" })),
    getView: () => view,
  });
  const document = env.document;
  return {
    env,
    document,
    window: env.window,
    storage: storage.backing,
    setView(next) { view = next; document.dispatchEvent(new FakeEvent("app:view-changed", { bubbles: true })); },
    sidebar(name) { return document.querySelector(`.app-sidebar .nav-item[data-view="${name}"]`); },
  };
}

test("focus mode: two real buttons, off by default, aria-pressed and text stay in sync", () => {
  const { document } = loadFocus();
  for (const id of ["nav-focus-toggle-account", "nav-focus-toggle-sidebar"]) {
    const button = document.getElementById(id);
    assert.ok(button, id);
    assert.equal(button.tagName, "BUTTON");
    assert.equal(button.getAttribute("type"), "button", "native keyboard operable button");
    assert.equal(button.classList.contains("nav-focus-toggle"), true);
    assert.equal(button.getAttribute("aria-pressed"), "false");
    assert.equal(button.textContent.trim(), "专注模式：关");
    assert.equal(button.disabled, false);
  }
  assert.equal(document.getElementById("nav-focus-notice").hidden, true);
});

test("focus mode: enabling hides only the collapsed sidebar entries and the whole community group", () => {
  const { document } = loadFocus();
  activate(document, document.getElementById("nav-focus-toggle-sidebar"));
  for (const name of KEPT_VIEWS) {
    assert.equal(sidebarVisible(document, name), true, `${name} stays visible`);
  }
  for (const name of HIDDEN_VIEWS) {
    assert.equal(sidebarVisible(document, name), false, `${name} is collapsed`);
  }
  assert.equal(sidebarVisible(document, "print"), true, "考前打印版不在点名清单，保持可见");
  const groups = Array.from(document.querySelectorAll(".app-sidebar .sidebar-group"));
  const community = groups.find((group) => group.querySelector('[data-view="forum"]'));
  const insight = groups.find((group) => group.querySelector('[data-view="insights"]'));
  assert.equal(community.hidden, true, "社区分组整组（含标题）收起");
  assert.equal(insight.hidden, false, "分析分组仍有条目，标题保留");
  for (const id of ["nav-focus-toggle-account", "nav-focus-toggle-sidebar"]) {
    const button = document.getElementById(id);
    assert.equal(button.getAttribute("aria-pressed"), "true");
    assert.equal(button.textContent.trim(), "专注模式：开");
  }
});

function sidebarVisible(document, name) {
  const item = document.querySelector(`.app-sidebar .nav-item[data-view="${name}"]`);
  return Boolean(item) && item.hidden === false;
}

test("focus mode: more drawer collapses the same entries; account toggle mirrors state", () => {
  const { document } = loadFocus();
  activate(document, document.getElementById("nav-focus-toggle-account"));
  for (const name of ["achievements", "weekly-recap", "forum", "leaderboard", "plan"]) {
    assert.equal(document.querySelector(`.more-item[data-view="${name}"]`).hidden, true, `${name} hidden in more drawer`);
  }
  for (const name of ["home", "insights", "print", "mastery", "clusters"]) {
    assert.equal(document.querySelector(`.more-item[data-view="${name}"]`).hidden, false, `${name} stays in more drawer`);
  }
});

test("focus mode: phone tab bar swaps 小组 for 分析 and restores when turned off", () => {
  const { document } = loadFocus();
  const tab = document.getElementById("tab-groups");
  assert.equal(tab.dataset.view, "groups");
  assert.equal(tab.querySelector(".tab-label").textContent.trim(), "小组");
  activate(document, document.getElementById("nav-focus-toggle-sidebar"));
  assert.equal(tab.dataset.view, "insights");
  assert.equal(tab.querySelector(".tab-label").textContent.trim(), "分析");
  assert.equal(tab.querySelector('[data-nav-focus-icon="groups"]').hidden, true);
  assert.equal(tab.querySelector('[data-nav-focus-icon="insights"]').hidden, false);
  activate(document, document.getElementById("nav-focus-toggle-sidebar"));
  assert.equal(tab.dataset.view, "groups");
  assert.equal(tab.querySelector(".tab-label").textContent.trim(), "小组");
  assert.equal(tab.querySelector('[data-nav-focus-icon="groups"]').hidden, false);
  assert.equal(tab.querySelector('[data-nav-focus-icon="insights"]').hidden, true);
});

test("focus mode: overview community cards collapse; trend section stays", () => {
  const { document } = loadFocus();
  // 这两张卡在没有小组 / 热帖时本来就是 hidden；先记下初始状态，关闭后必须原样还原。
  const initial = {
    groups: document.getElementById("ov-groups-card").hidden,
    hot: document.getElementById("ov-hot-card").hidden,
  };
  activate(document, document.getElementById("nav-focus-toggle-sidebar"));
  assert.equal(document.getElementById("ov-groups-card").hidden, true);
  assert.equal(document.getElementById("ov-hot-card").hidden, true);
  assert.equal(document.getElementById("home-trend").hidden, false);
  activate(document, document.getElementById("nav-focus-toggle-sidebar"));
  assert.equal(document.getElementById("ov-groups-card").hidden, initial.groups);
  assert.equal(document.getElementById("ov-hot-card").hidden, initial.hot);
});

test("focus mode: direct visit to a collapsed view shows the top notice; closing it leaves focus mode", () => {
  const ctx = loadFocus({ view: "home" });
  activate(ctx.document, ctx.document.getElementById("nav-focus-toggle-sidebar"));
  ctx.setView("groups");
  const notice = ctx.document.getElementById("nav-focus-notice");
  assert.equal(notice.hidden, false);
  assert.equal(notice.getAttribute("role"), "status");
  assert.match(notice.textContent, /专注模式已开启，这个入口已收起/);
  const close = notice.querySelector("#nav-focus-notice-close");
  assert.equal(close.tagName, "BUTTON");
  assert.equal(close.textContent.trim(), "关闭专注模式");
  activate(ctx.document, close);
  assert.equal(notice.hidden, true);
  assert.equal(ctx.document.getElementById("nav-focus-toggle-sidebar").getAttribute("aria-pressed"), "false");
  assert.equal(ctx.sidebar("groups").hidden, false);
});

test("focus mode: notice never shows for kept views", () => {
  const ctx = loadFocus({ view: "home" });
  activate(ctx.document, ctx.document.getElementById("nav-focus-toggle-sidebar"));
  ctx.setView("today");
  assert.equal(ctx.document.getElementById("nav-focus-notice").hidden, true);
  ctx.setView("leaderboard");
  assert.equal(ctx.document.getElementById("nav-focus-notice").hidden, false);
  ctx.setView("insights");
  assert.equal(ctx.document.getElementById("nav-focus-notice").hidden, true);
});

test("focus mode: choice persists per user id and does not leak across accounts", () => {
  const shared = makeStorage();
  const first = loadFocus({ user: { id: 1, username: "alice" }, storage: shared });
  activate(first.document, first.document.getElementById("nav-focus-toggle-sidebar"));
  assert.equal(first.storage.get("nav-focus:1"), "1");
  first.window.NavFocus.reset();

  const second = loadFocus({ user: { id: 2, username: "bob" }, storage: shared });
  assert.equal(second.window.NavFocus.isEnabled(), false, "user 2 starts off");
  assert.equal(second.sidebar("forum").hidden, false);
  activate(second.document, second.document.getElementById("nav-focus-toggle-sidebar"));
  assert.equal(second.storage.get("nav-focus:2"), "1");
  assert.equal(first.storage.get("nav-focus:1"), "1", "alice's choice untouched");

  second.window.NavFocus.reset();
  second.window.NavFocus.configure({ getUser: () => ({ id: 1, username: "alice" }), getView: () => "home" });
  assert.equal(second.window.NavFocus.isEnabled(), true, "alice's choice restored");
  assert.equal(second.sidebar("forum").hidden, true);
});

test("focus mode: stored choice is reapplied on a fresh page load for that user", () => {
  const first = loadFocus({ user: { id: 7 } });
  activate(first.document, first.document.getElementById("nav-focus-toggle-account"));
  const shared = makeStorage(first.storage);

  const env2 = load(["nav-focus.js"], {
    extra: { URL: "http://localhost:8000/static/index.html", ...shared },
  });
  mountBody(env2.document);
  env2.window.NavFocus.configure({ getUser: () => ({ id: 7 }), getView: () => "home" });
  assert.equal(env2.window.NavFocus.isEnabled(), true);
  assert.equal(env2.document.querySelector('.app-sidebar .nav-item[data-view="plan"]').hidden, true);
});

test("focus mode: when localStorage throws everywhere, it behaves as off and never throws", () => {
  const ctx = loadFocus({ user: { id: 3 } });
  const broken = new Proxy({}, {
    get() { throw new Error("storage disabled"); },
    set() { throw new Error("storage disabled"); },
  });
  ctx.window.localStorage = broken;
  let threw = false;
  try {
    ctx.window.NavFocus.reset();
    ctx.window.NavFocus.configure({ getUser: () => ({ id: 3 }), getView: () => "home" });
    activate(ctx.document, ctx.document.getElementById("nav-focus-toggle-sidebar"));
  } catch (error) {
    threw = true;
  }
  assert.equal(threw, false);
  assert.equal(ctx.window.NavFocus.isEnabled(), false, "write failure falls back to off");
  assert.equal(ctx.document.getElementById("nav-focus-toggle-sidebar").getAttribute("aria-pressed"), "false");
  assert.equal(ctx.sidebar("forum").hidden, false);
});

test("focus mode: keyboard activation path toggles state from either control", () => {
  const ctx = loadFocus();
  const sidebar = ctx.document.getElementById("nav-focus-toggle-sidebar");
  sidebar.focus();
  assert.equal(ctx.document.activeElement, sidebar);
  // 真实 <button> 聚焦后按 Enter/Space 会在按钮上派发 click 并冒泡到 document；这里模拟这条键盘激活路径。
  activate(ctx.document, sidebar);
  assert.equal(ctx.window.NavFocus.isEnabled(), true);
  const account = ctx.document.getElementById("nav-focus-toggle-account");
  account.focus();
  activate(ctx.document, account);
  assert.equal(ctx.window.NavFocus.isEnabled(), false);
});

test("focus mode: reset restores every marker it touched but leaves data-admin-only hidden", () => {
  const ctx = loadFocus({ view: "leaderboard" });
  const plan = ctx.sidebar("plan");
  const planInitial = plan.hidden;
  // 管理后台入口在静态 HTML 里就是 hidden（data-admin-only），任何时候都不能被专注模式展开。
  assert.equal(ctx.sidebar("admin").hidden, true, "admin starts hidden");
  activate(ctx.document, ctx.document.getElementById("nav-focus-toggle-sidebar"));
  assert.equal(ctx.sidebar("admin").hidden, true, "admin stays hidden while focus is on");
  ctx.setView("leaderboard");
  assert.equal(ctx.document.getElementById("nav-focus-notice").hidden, false);
  ctx.window.NavFocus.reset();
  for (const name of ["home", "today", "all", "print", "new", "insights", "mastery", "clusters", "achievements", "weekly-recap", "groups", "forum", "leaderboard"]) {
    assert.equal(ctx.sidebar(name).hidden, false, `${name} restored`);
  }
  assert.equal(plan.hidden, planInitial, "plan goes back to its original visibility");
  assert.equal(ctx.sidebar("admin").hidden, true, "admin stays hidden after reset");
  assert.equal(ctx.document.getElementById("nav-focus-notice").hidden, true);
  assert.equal(ctx.document.getElementById("tab-groups").dataset.view, "groups");
  assert.equal(ctx.document.getElementById("ov-groups-card").hidden, false, "ov-groups-card 初始可见，还原为可见");
  assert.equal(ctx.document.getElementById("ov-hot-card").hidden, true, "ov-hot-card 初始 hidden，还原为 hidden");
  assert.equal(ctx.window.NavFocus.isEnabled(), false);
});
