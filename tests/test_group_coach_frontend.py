"""小组薄弱点汇总前端渲染：在 Node 里跑真实函数，断言行为（非源码字符串）。"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from test_cursor_fx_assets import function_body
from static_helpers import app_source


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
"""


NODE_CHECKS = r"""
try {
  const host = $("#groups-coach-summary");
  if (scenario === "full") {
    const data = {
      group_id: 1, eligible_members: 4, has_summary: true,
      cause_groups: [
        { code: "reasoning", label: "想错了", count: 2, percent: 25 },
        { code: "implementation", label: "写错了", count: 6, percent: 75 },
        { code: "performance", label: "跑得慢", count: 0, percent: 0 },
        { code: "environment", label: "环境与其他", count: 0, percent: 0 },
      ],
      causes: [
        { code: "boundary", label: "边界/特例", group: "implementation", count: 4 },
        { code: "impl", label: "实现细节", group: "implementation", count: 2 },
      ],
      verdicts: [{ code: "WA", label: "答案错误", count: 3, percent: 100 }],
      top_patterns: [{ pattern_id: "two-pointers", title: "双指针", mistake_count: 5 }],
      roadmap: { learning: 3, mastered: 2 },
      active_this_week: 3,
      review_completion: { due: 10, reviewed: 7, percent: 70 },
    };
    renderGroupCoachSummary(host, data);
    const text = host.textContent;
    assert.ok(text.includes("参与汇总 4 人"));
    assert.ok(text.includes("想错了"));
    assert.ok(text.includes("边界/特例"));
    assert.ok(text.includes("答案错误"));
    assert.ok(text.includes("双指针"));
    assert.ok(text.includes("2 个已掌握"));
    assert.ok(text.includes("70%"));
    // 不含任何个人字段。
    for (const secret of ["张三", "秘密题名"]) {
      assert.ok(!text.includes(secret), `leaked ${secret}`);
    }
  } else if (scenario === "insufficient") {
    renderGroupCoachSummary(host, {
      group_id: 1, eligible_members: 2, has_summary: false,
      reason: "参与汇总的成员只有 2 人，不足 3 人。",
    });
    assert.ok(host.textContent.includes("不足 3 人"));
    assert.ok(!host.textContent.includes("想错了"));
  } else if (scenario === "empty") {
    renderGroupCoachSummary(host, {
      group_id: 1, eligible_members: 3, has_summary: true,
      cause_groups: [], causes: [], verdicts: [], top_patterns: [],
      roadmap: { learning: 0, mastered: 0 },
      active_this_week: 0,
      review_completion: { due: 0, reviewed: 0, percent: 0 },
    });
    assert.ok(host.textContent.includes("参与汇总 3 人"));
  } else {
    throw new Error("Unknown scenario: " + scenario);
  }
} catch (error) { console.error(error); process.exitCode = 1; }
"""


@pytest.mark.parametrize("scenario", ["full", "insufficient", "empty"])
def test_group_coach_summary_renders(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for the coach summary behavior checks")
    source = app_source()
    functions = []
    for name in ("element", "coachBarRow", "coachBlock", "renderGroupCoachSummary"):
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
