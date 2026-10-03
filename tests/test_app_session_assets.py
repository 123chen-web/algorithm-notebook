"""Session behavior checks use the real app functions without temp files."""

from pathlib import Path
import json
import re
import shutil
import subprocess

import pytest

from test_cursor_fx_assets import function_body


STATIC = Path(__file__).resolve().parents[1] / "static"


SESSION_HARNESS = r"""
const assert = require("node:assert/strict");
const vm = require("node:vm");
const payload = JSON.parse(require("node:fs").readFileSync(0, "utf8"));
const nodes = new Map();
const node = (selector) => {
  if (!nodes.has(selector)) nodes.set(selector, {
    hidden: false, children: [], value: "", textContent: "", attributes: {},
    replaceChildren(...children) { this.children = children; this.textContent = ""; },
    setAttribute(name, value) { this.attributes[name] = value; },
    reset() {},
  });
  return nodes.get(selector);
};
const context = {
  $: node,
  document: {
    documentElement: { dataset: {} },
    body: { classList: { remove() {} } },
    querySelectorAll: () => [],
  },
  window: {},
  location: { hash: "", pathname: "/", search: "" },
  history: { replaceState(_state, _unused, url) { context.location.hash = url.slice(url.indexOf("#")); } },
  finishHomeOpening: null, sealStamps: [], resetToken: null,
  planPurchase: null, forumPost: null, forumSearchQuery: "", forumCommentOrder: "earliest",
  forumOnlyOp: false, forumListGeneration: 0,
};
for (const name of ["stopOrderPolling", "resetWeaknessAnalysis", "resetAchievements",
  "resetWeeklyRecap", "resetGroups", "resetHomeSummary", "closeAccountMenu", "showAuthPanels",
  "clearForumReply", "resetAdminDashboard", "addMistakeInput", "resetPhotoForm"]) context[name] = () => {};
context.loadZones = async () => {};
context.showView = async () => {};
context.updateUserInfo = () => node("#user-info-wrap").replaceChildren(vm.runInContext("user.username", context));
vm.createContext(context);
vm.runInContext(payload.source, context);
const session = () => vm.runInContext("({ user, sessionReady, sessionEpoch })", context);
const response = (status, data, parse = () => Promise.resolve(data)) => ({
  status, ok: status >= 200 && status < 300, json: parse,
});
async function login(id, username) {
  context.fetch = async (path) => {
    assert.equal(path, "/api/me");
    return response(200, { id, username });
  };
  await context.enterApp();
}
const deferred = () => {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
};
async function verify() {
  await login(1, "账号 A");
  assert.equal(session().sessionEpoch, 1);
  if (payload.scenario.startsWith("stale_")) {
    const waiting = deferred();
    context.fetch = payload.scenario === "stale_response"
      ? () => waiting.promise
      : async () => response(401, null, () => waiting.promise);
    const pending = context.api("/api/insights/clusters");
    const rejected = assert.rejects(pending, (error) => error.status === 401 && error.message === "旧请求过期");
    await Promise.resolve();
    context.signedOut();
    assert.equal(session().sessionEpoch, 2);
    await login(2, "账号 B");
    assert.equal(session().sessionEpoch, 3);
    node("#cards").replaceChildren("账号 B 的错题");
    waiting.resolve(payload.scenario === "stale_response"
      ? response(401, { detail: "旧请求过期" }) : { detail: "旧请求过期" });
    await rejected;
    assert.equal(session().user.id, 2);
    assert.equal(session().sessionReady, true);
    assert.equal(session().sessionEpoch, 3);
    assert.equal(context.document.documentElement.dataset.view, "app");
    assert.equal(node("#app").hidden, false);
    assert.equal(node("#logout").hidden, false);
    assert.deepEqual(node("#user-info-wrap").children, ["账号 B"]);
    assert.deepEqual(node("#cards").children, ["账号 B 的错题"]);
  } else if (payload.scenario === "current_401") {
    node("#cards").replaceChildren("账号 A 的错题");
    context.fetch = async () => response(401, { detail: "请重新登录" });
    await assert.rejects(context.api("/api/me"), (error) => error.status === 401 && error.message === "请重新登录");
    assert.equal(session().user, null);
    assert.equal(session().sessionEpoch, 2);
    assert.equal(context.document.documentElement.dataset.view, "welcome");
    assert.equal(node("#app").hidden, true);
    assert.equal(node("#logout").hidden, true);
    assert.deepEqual(node("#user-info-wrap").children, []);
    assert.deepEqual(node("#cards").children, []);
  } else if (payload.scenario === "api_behavior") {
    const calls = [];
    const data = { value: 42 };
    context.fetch = async (path, options) => { calls.push({ path, options }); return response(200, data); };
    assert.equal(await context.api("/api/example", {
      method: "POST", body: "{}", headers: { "X-Extra": "yes" },
    }), data);
    assert.equal(calls[0].path, "/api/example");
    assert.equal(calls[0].options.credentials, "same-origin");
    assert.equal(calls[0].options.method, "POST");
    assert.equal(calls[0].options.body, "{}");
    assert.equal(calls[0].options.headers["Content-Type"], "application/json");
    assert.equal(calls[0].options.headers["X-CSRF-Protection"], "1");
    assert.equal(calls[0].options.headers["X-Extra"], "yes");
    context.fetch = async () => response(204, null, async () => { throw new SyntaxError("empty"); });
    assert.equal(JSON.stringify(await context.api("/api/example")), "{}");
    for (const [status, detail, expected] of [
      [422, [{ loc: ["body", "title"], msg: "必填" }, { loc: ["query", "page"], msg: "无效" }], "body.title: 必填；query.page: 无效"],
      [503, { message: "服务暂不可用" }, "服务暂不可用"],
      [500, {}, "请求失败，请稍后重试"],
    ]) {
      context.fetch = async () => response(status, { detail });
      await assert.rejects(context.api("/api/example"), (error) => error.status === status && error.message === expected);
      assert.equal(session().user.id, 1);
      assert.equal(session().sessionEpoch, 1);
    }
  } else throw new Error(`Unknown scenario ${payload.scenario}`);
}
verify().catch((error) => { console.error(error); process.exitCode = 1; });
"""


def run_session_scenario(scenario):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is needed for session behavior checks")
    source = (STATIC / "app.js").read_text(encoding="utf-8")
    parts = []
    for name in ("user", "sessionReady", "sessionEpoch"):
        declaration = re.search(rf"(?m)^let {name} = [^;]+;", source)
        assert declaration, f"Missing {name} session state"
        parts.append(declaration[0])
    for name in ("api", "signedOut", "enterApp", "renderPageRoute"):
        declaration = re.search(rf"(?m)^(?:async\s+)?function\s+{name}\s*\([^)]*\)\s*\{{", source)
        assert declaration, f"Missing {name} session helper"
        parts.append(declaration[0] + function_body(source, name) + "}")
    result = subprocess.run(
        [node, "-e", SESSION_HARNESS],
        input=json.dumps({"source": "\n".join(parts), "scenario": scenario}, ensure_ascii=False),
        text=True, encoding="utf-8", capture_output=True, timeout=10,
        cwd=STATIC.parent, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("scenario", ["stale_response", "stale_body"])
def test_stale_unauthorized_request_keeps_new_session(scenario):
    run_session_scenario(scenario)


def test_current_unauthorized_request_signs_out():
    run_session_scenario("current_401")


def test_api_keeps_success_and_error_behavior():
    run_session_scenario("api_behavior")
