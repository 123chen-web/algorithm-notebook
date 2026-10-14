"use strict";

/* 讨论区冻结开关前端（static/forum-switch.js）的行为测试：
   Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   验证：开关关闭时所有 data-view="forum" 入口被隐藏；开启时恢复；
   features 缺省视为开启；命令面板的帖子可见性跟随侧栏按钮。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection"));

function setup() {
  const { window, document } = load(["forum-switch.js"]);
  const sidebar = document.createElement("nav");
  sidebar.className = "app-sidebar";
  const sidebarBtn = document.createElement("button");
  sidebarBtn.className = "nav-item";
  sidebarBtn.setAttribute("data-view", "forum");
  sidebar.appendChild(sidebarBtn);
  const moreBtn = document.createElement("button");
  moreBtn.className = "more-item";
  moreBtn.setAttribute("data-view", "forum");
  document.documentElement.appendChild(sidebar);
  document.documentElement.appendChild(moreBtn);
  return { window, document, sidebarBtn, moreBtn };
}

test("isEnabled: features 缺省/空视为开启，只有 forum:false 才关闭", () => {
  const { window } = load(["forum-switch.js"]);
  const { isEnabled } = window.ForumSwitch;
  assert.equal(isEnabled(undefined), true);
  assert.equal(isEnabled(null), true);
  assert.equal(isEnabled({}), true);
  assert.equal(isEnabled({ forum: true }), true);
  assert.equal(isEnabled({ forum: false }), false);
});

test("apply({forum:false}): 侧栏与手机'更多'的讨论区入口都被隐藏", () => {
  const { window, sidebarBtn, moreBtn } = setup();
  const enabled = window.ForumSwitch.apply({ forum: false });
  assert.equal(enabled, false);
  assert.equal(sidebarBtn.hidden, true);
  assert.equal(moreBtn.hidden, true);
});

test("apply({forum:true}): 入口恢复可见", () => {
  const { window, sidebarBtn, moreBtn } = setup();
  window.ForumSwitch.apply({ forum: false });
  const enabled = window.ForumSwitch.apply({ forum: true });
  assert.equal(enabled, true);
  assert.equal(sidebarBtn.hidden, false);
  assert.equal(moreBtn.hidden, false);
});

test("apply(): features 缺省时视为开启，不隐藏", () => {
  const { window, sidebarBtn } = setup();
  window.ForumSwitch.apply(undefined);
  assert.equal(sidebarBtn.hidden, false);
});

test("postsVisible: 侧栏按钮隐藏时命令面板不再展示帖子", () => {
  const { window, document } = setup();
  assert.equal(window.ForumSwitch.postsVisible(), true);
  window.ForumSwitch.apply({ forum: false });
  assert.equal(window.ForumSwitch.postsVisible(), false);
  // 侧栏按钮不存在时（极简布局）默认展示帖子，由后端 /api/search 置空兜底。
  document.documentElement.children.length = 0;
  assert.equal(window.ForumSwitch.postsVisible(), true);
});
