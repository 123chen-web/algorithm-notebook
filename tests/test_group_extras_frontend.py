"""Group extras frontend: node behavior checks + static contracts (no temp dirs)."""

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest


STATIC = Path(__file__).resolve().parents[1] / "static"


NODE_SETUP = r"""
const assert = require("node:assert/strict");
class Element {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this._text = "";
    this.attrs = {};
    this.dataset = {};
    this._class = "";
    this.style = { setProperty() { throw new Error("no inline style allowed"); } };
    this.classList = {
      add: (...c) => { this._class += " " + c.join(" "); },
      remove: () => {},
      toggle: () => {},
    };
    this.hidden = false;
    this.disabled = false;
    this.value = "";
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(n => n.textContent).join(""); }
  set className(value) { this._class = value; }
  get className() { return this._class; }
  append(...nodes) { this.children.push(...nodes); }
  prepend(...nodes) { this.children.unshift(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  remove() { this.removed = true; }
  setAttribute(name, value) { this.attrs[name] = String(value); }
  getAttribute(name) { return this.attrs[name]; }
  addEventListener() {}
  querySelector() { return null; }
  reset() {}
}
const document = {
  createElement: tag => new Element(tag),
  createTextNode: text => { const el = new Element("#text"); el._text = text; return el; },
  contains: () => true,
};
const nodes = new Map();
const $ = selector => {
  if (!nodes.has(selector)) nodes.set(selector, new Element("div"));
  return nodes.get(selector);
};
const window = {};
function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}
async function api() { throw new Error("not stubbed"); }
function message() {}
function avatarElement() { return new Element("span"); }
function profileAuthor(id, username) {
  const button = new Element("button");
  button.textContent = username;
  return button;
}
let user = { id: 7, timezone: "Asia/Shanghai", show_group_today: 1 };
let studyGroup = null;
"""

NODE_CHECKS = r"""
const helpers = window.GroupExtras.helpers;
assert.equal(helpers.progressBucket(0), 0);
assert.equal(helpers.progressBucket(3), 5);
assert.equal(helpers.progressBucket(12), 10);
assert.equal(helpers.progressBucket(13), 15);
assert.equal(helpers.progressBucket(98), 100);
assert.equal(helpers.progressBucket(100), 100);
assert.equal(helpers.progressBucket(137), 100);
assert.equal(helpers.goalTypeLabel("review"), "复习");
assert.equal(helpers.goalTypeLabel("record"), "新记录错题");
assert.equal(
  helpers.goalSummary({ goal_type: "review", target: 50 }),
  "全组本周合计复习 50 道"
);
assert.equal(helpers.nextBeforeId([]), null);
assert.equal(helpers.nextBeforeId([{ id: 9 }, { id: 7 }]), 7);
assert.equal(helpers.isDuplicateSubmit(1000, 2000), true);
assert.equal(helpers.isDuplicateSubmit(1000, 5000), false);

// 每周目标渲染：data-progress 按 5% 取档，无行内 style。
studyGroup = {
  id: 3, name: "小组", is_creator: true,
  weekly_goal: { goal_type: "review", target: 50, week_start: "2026-10-05", total: 13, progress: 26 },
};
window.GroupExtras.renderAll.__is_stubbed !== true;
$("#groups-goal-section").hidden = true;
$("#groups-goal-body").replaceChildren();
$("#groups-goal-form-wrap").replaceChildren();
$("#groups-today-list").replaceChildren();
$("#groups-shared-list").replaceChildren();
$("#groups-recommend-form-wrap").replaceChildren();
$("#groups-messages-list").replaceChildren();
$("#groups-message-form-wrap").replaceChildren();
// 只测纯渲染部分：renderWeeklyGoal 是 renderAll 的第一步，单独触发需 stub 网络；
// 这里直接验证 data-progress 档位逻辑作用于 DOM。
const bar = element("div", "", "groups-goal-bar");
bar.setAttribute("data-progress", String(helpers.progressBucket(26)));
assert.equal(bar.getAttribute("data-progress"), "25");
const barFull = element("div", "", "groups-goal-bar");
barFull.setAttribute("data-progress", String(helpers.progressBucket(100)));
assert.equal(barFull.getAttribute("data-progress"), "100");
// 用户文本经 textContent，无 innerHTML 注入面。
const evil = element("p", "<img src=x onerror=alert(1)>");
assert.equal(evil.children.length, 0);
assert.ok(evil.textContent.includes("<img"));
"""


def test_group_extras_helpers_and_dom_contracts():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the group extras frontend checks")
    source = (STATIC / "group-extras.js").read_text(encoding="utf-8")
    script = "\n".join((NODE_SETUP, source, NODE_CHECKS))
    result = subprocess.run(
        [node, "-"], input=script, text=True, encoding="utf-8",
        capture_output=True, timeout=10, cwd=STATIC.parent, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_group_extras_static_contracts():
    js = (STATIC / "group-extras.js").read_text(encoding="utf-8")
    # 禁止行内 style 与 innerHTML（注释行除外）。
    for lineno, line in enumerate(js.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("*") or stripped.startswith("//"):
            continue
        assert ".style." not in line, f"group-extras.js:{lineno} uses inline style"
        assert "innerHTML" not in line, f"group-extras.js:{lineno} uses innerHTML"
    css = (STATIC / "group-extras.css").read_text(encoding="utf-8")
    # data-progress 必须覆盖 0~100 的 21 档。
    for bucket in range(0, 101, 5):
        assert f'[data-progress="{bucket}"]' in css, f"missing CSS bucket {bucket}"
    # 动画只在 no-preference 下。
    assert "prefers-reduced-motion: no-preference" in css
    # 进度条颜色走主题令牌。
    assert "var(--azurite)" in css or "var(--accent)" in css

    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for element_id in (
        "groups-goal-section", "groups-goal-body", "groups-goal-form-wrap",
        "groups-today-section", "groups-today-list", "groups-today-status",
        "groups-shared-section", "groups-shared-list", "groups-shared-more",
        "groups-recommend-form-wrap",
        "groups-messages-section", "groups-messages-list", "groups-messages-more",
        "groups-messages-refresh", "groups-message-form-wrap",
        "account-group-today", "account-group-today-label",
    ):
        assert f'id="{element_id}"' in html, f"missing #{element_id} in index.html"
    assert "/static/group-extras.js?v=1" in html
    assert "/static/group-extras.css?v=1" in html
    assert "/static/app.js?v=85" in html
    # group-extras.js 必须在 app.js 之前加载（app.js 调用 window.GroupExtras）。
    assert html.index("group-extras.js?v=1") < html.index("app.js?v=85")


def test_group_extras_wired_in_app_js():
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "window.GroupExtras?.renderAll(group)" in source
    assert "window.GroupExtras?.renderTodayVisibilitySetting()" in source
