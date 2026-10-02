"""Execute group rendering regressions in Node without temporary files."""

import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

from test_cursor_fx_assets import function_body


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
    this.style = { setProperty: (key, value) => { this.attrs[key] = value; } };
    this.classList = { add() {}, remove() {} };
  }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(node => node.textContent).join(""); }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this._text = ""; this.children = []; this.append(...nodes); }
  setAttribute(name, value) { this.attrs[name] = value; }
  addEventListener() {}
}
const document = { createElement: tag => new Element(tag) };
const nodes = new Map();
const $ = selector => {
  if (!nodes.has(selector)) nodes.set(selector, new Element("div"));
  return nodes.get(selector);
};
const window = { clearTimeout() {} };
const storage = new Map();
const localStorage = {
  getItem: key => storage.has(key) ? storage.get(key) : null,
  setItem: (key, value) => storage.set(key, value),
};
const stamps = [];
function stampSeal(text) { stamps.push(text); }
function avatarElement() { return new Element("span"); }
let user = { id: 7, timezone: "Factory" };
let view = "groups";
let busy = false;
let studyGroup = null;
let selectedGroupId = null;
let groupCopyTimer = null;
let groupLevelsGeneration = 0;
let groupLevelRules = null;
async function api(path) {
  assert.equal(path, "/api/group-levels");
  return {
    levels: [{ number: 1, name: "启程", min_points: 0 }],
    rules: [{ key: "review", label: "复习", points: 1, daily_cap: 10 }],
  };
}
const initialGroup = {
  id: 13, name: "学习小组", invite_code: "STUDY123",
  created_at: "2026-09-19T23:30:00+00:00",
  member_limit: 10, is_creator: true, points: 9,
  level: {
    number: 1, name: "启程", points: 9, floor: 0, next_points: 120,
    next_name: "同行", points_to_next: 111, progress: 0.075,
  },
  members: [{ id: 7, username: "组长", current_streak_days: 1, points: 9,
    is_creator: true, avatar_version: 0, has_avatar: false }],
  weakness_by_zone: {},
};
"""


NODE_CHECKS = r"""
(async () => {
  const value = initialGroup.created_at;
  const date = new Date(value);
  if (scenario === "dates") {
    for (const zone of ["UTC", "Asia/Shanghai", "America/Los_Angeles"]) {
      user.timezone = zone;
      assert.equal(formatGroupDate(value, zone), date.toLocaleDateString("zh-CN", { timeZone: zone }));
      assert.equal(timestamp(value), date.toLocaleString("zh-CN", { timeZone: zone, hour12: false }));
    }
    user.timezone = "Factory";
    assert.equal(formatGroupDate(value, user.timezone), date.toLocaleDateString("zh-CN", { timeZone: "UTC" }) + "（UTC）");
    assert.equal(timestamp(value), date.toLocaleString("zh-CN", { timeZone: "UTC", hour12: false }) + "（UTC）");
    assert.equal(timestamp(null), "尚无");
  } else if (scenario === "detail") {
    assert.doesNotThrow(() => renderStudyGroup(initialGroup));
    await new Promise(resolve => setImmediate(resolve));
    assert.equal($("#groups-detail-title").textContent, initialGroup.name);
    assert.equal($("#groups-invite-code").textContent, initialGroup.invite_code);
    assert.equal($("#groups-detail-content").hidden, false);
    assert.ok($("#groups-detail-meta").textContent.includes("创建于 " + date.toLocaleDateString("zh-CN", { timeZone: "UTC" }) + "（UTC）"));
    assert.equal($("#groups-level-progress").attrs["aria-valuenow"], "8");
    assert.ok($("#groups-level-caption").textContent.includes("9 / 120 分"));
    assert.equal($("#groups-members").children.length, 1);
    assert.ok($("#groups-members").textContent.includes("组长"));
    assert.equal($("#groups-level-ladder").children.length, 1);
    assert.equal($("#groups-level-rules").children.length, 1);
    assert.equal($("#groups-levels-status").textContent, "");
    assert.equal($("#groups-upgrade").attrs["aria-busy"], "false");
  } else if (scenario === "reused-id") {
    const first = { ...initialGroup, level: { number: 1 } };
    const second = { ...initialGroup, created_at: "2026-09-20T00:00:00+00:00", level: { number: 3 } };
    rememberGroupLevel(first);
    rememberGroupLevel(second);
    assert.deepEqual(stamps, []);
    assert.equal(storage.get(`groupLevelSeen:7:13:${first.created_at}`), "1");
    assert.equal(storage.get(`groupLevelSeen:7:13:${second.created_at}`), "3");
    rememberGroupLevel({ ...first, level: { number: 2 } });
    assert.deepEqual(stamps, ["升至 Lv.2"]);
    rememberGroupLevel(second);
    assert.deepEqual(stamps, ["升至 Lv.2"]);
  } else if (scenario === "legacy-key") {
    storage.set("groupLevelSeen:7:13", "1");
    const group = { ...initialGroup, level: { number: 3 } };
    assert.doesNotThrow(() => rememberGroupLevel(group));
    assert.deepEqual(stamps, []);
    assert.equal(storage.get("groupLevelSeen:7:13"), "1");
    assert.equal(storage.get(`groupLevelSeen:7:13:${group.created_at}`), "3");
    rememberGroupLevel({ ...group, level: { number: 4 } });
    assert.deepEqual(stamps, ["升至 Lv.4"]);
  } else if (scenario === "storage-unavailable") {
    localStorage.getItem = () => { throw new Error("Storage unavailable"); };
    assert.doesNotThrow(() => rememberGroupLevel(initialGroup));
    assert.deepEqual(stamps, []);
  } else {
    throw new Error("Unknown scenario: " + scenario);
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@pytest.mark.parametrize("scenario", [
    "dates", "detail", "reused-id", "legacy-key", "storage-unavailable",
])
def test_group_frontend_timezone_and_level_storage_regressions(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the group frontend behavior checks")
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    functions = []
    for name in (
        "element", "timestamp", "formatGroupDate", "groupLevelBadge",
        "groupNextLevelText", "setGroupProgress", "renderStudyGroup",
        "renderGroupWeakness", "renderGroupLevelRules", "loadGroupLevelRules",
        "rememberGroupLevel",
    ):
        declaration = re.search(
            rf"(?m)^(?:async\s+)?function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{",
            source,
        )
        assert declaration, f"Missing {name} renderer or helper"
        functions.append(declaration[0] + function_body(source, name) + "}")
    script = "\n".join((
        NODE_SETUP, *functions,
        f"const scenario = {json.dumps(scenario)};", NODE_CHECKS,
    ))
    result = subprocess.run(
        [node, "-"], input=script, text=True, encoding="utf-8",
        capture_output=True, timeout=10, cwd=STATIC.parent, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
