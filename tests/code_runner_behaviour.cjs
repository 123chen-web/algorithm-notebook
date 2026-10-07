"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, deferred, FakeEvent } = require("./js_harness.cjs");

const CAPABILITIES = { configured: true, allowed: true, languages: ["Python", "C++"], cpu_seconds: 2, memory_kb: 128000 };
const RESULT = { status: "completed", stdout: "hello\n", stderr: "", compile_output: "", truncated: false };
const ITEM = { id: 7, language: "Python", code: "print(input())" };
const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled rejection"));

function setup(item = ITEM, options = {}) {
  const env = load(["code-runner.js"]);
  for (const id of ["app", "auth", "notice"]) {
    const node = env.document.createElement("div"); node.id = id; env.document.body.append(node);
  }
  env.context.$ = (selector) => env.document.querySelector(selector);
  const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
  const setBusy = source.match(/^function setBusy\(value\) \{[\s\S]*?^\}/m);
  assert.ok(setBusy, "the production global button controller must be present");
  vm.runInContext(setBusy[0], env.context);
  const resetGlobalBusy = () => { env.context.setBusy(true); env.context.setBusy(false); };
  const session = { user: { id: 1, is_trial: false }, epoch: 1, view: "today", selection: 1, detail: 1 };
  const calls = [];
  const hooks = {
    api: (url, request = {}) => { const call = { url, request, ...deferred() }; calls.push(call); return call.promise; },
    getUser: () => session.user, getEpoch: () => session.epoch, getView: () => session.view,
    getSelectionGeneration: () => session.selection, getDetailGeneration: () => session.detail,
  };
  env.window.CodeRunner.configure(hooks);
  const host = env.document.createElement("div"); env.document.body.append(host);
  const mount = (next = item, mountOptions = options) => env.window.CodeRunner.mount(host, next, mountOptions);
  mount();
  const query = (selector) => host.querySelector(selector);
  const open = () => { const details = query("details"); details.open = true; details.dispatchEvent(new FakeEvent("toggle")); };
  const submit = () => { open(); query("form").dispatchEvent(new FakeEvent("submit")); };
  const caps = async (data = CAPABILITIES, call = calls[0]) => { call.resolve(data); await tick(); };
  return { ...env, session, calls, host, mount, query, open, submit, caps, resetGlobalBusy };
}

test("runner: configuration is read before any source is submitted", async () => {
  const env = setup();
  assert.deepEqual(env.calls.map((call) => call.url), ["/api/code-runner"]);
  assert.equal(env.query(".cr-run").disabled, true);
  await env.caps();
  assert.equal(env.query(".cr-code").value, ITEM.code); assert.equal(env.query(".cr-language").value, "Python");
  assert.match(env.host.textContent, /独立运行服务/); assert.match(env.host.textContent, /不消耗 AI/);
  assert.match(env.host.textContent, /不会自动判题/); assert.match(env.host.textContent, /不会保存/);
  assert.equal(env.calls.length, 1);
});

for (const capabilities of [{ ...CAPABILITIES, configured: false }, { ...CAPABILITIES, allowed: false }]) {
  test(`runner: configured=${capabilities.configured} allowed=${capabilities.allowed} cannot send a draft`, async () => {
    const env = setup(); await env.caps(capabilities); env.submit(); await tick();
    assert.equal(env.calls.length, 1); assert.equal(env.query(".cr-run").disabled, true);
    assert.match(env.query(".cr-status").textContent, capabilities.allowed ? /站长配置/ : /普通账号/);
  });
}

test("runner: offline details remain disabled without any capability request", async () => {
  const env = setup(ITEM, { offline: true }); env.submit(); await tick();
  assert.equal(env.calls.length, 0); assert.match(env.host.textContent, /离线/);
  assert.equal(env.query(".cr-run").disabled, true);
});

test("runner: an explicit edited draft goes only to the owned-record endpoint, and duplicate activation sends once", async () => {
  const env = setup(); await env.caps();
  env.query(".cr-language").value = "C++"; env.query(".cr-code").value = "int main() {}";
  env.query(".cr-stdin").value = "5\n"; env.submit(); env.submit();
  assert.equal(env.calls.length, 2); assert.equal(env.query(".cr-run").disabled, true);
  assert.equal(env.calls[1].url, "/api/mistakes/7/run"); assert.equal(env.calls[1].request.method, "POST");
  assert.deepEqual(JSON.parse(env.calls[1].request.body), { language: "C++", code: "int main() {}", stdin: "5\n" });
  env.calls[1].resolve(RESULT); await tick();
  assert.match(env.query(".cr-results").textContent, /hello/);
  assert.equal(env.query(".cr-run").disabled, false);
  assert.equal(ITEM.code, "print(input())", "running must not modify the saved record");
});

test("runner: saved quick placeholder is not treated as executable source", async () => {
  const env = setup({ ...ITEM, code: "（速记：代码待补）" }); await env.caps(); env.submit();
  assert.equal(env.query(".cr-code").value, ""); assert.equal(env.calls.length, 1);
  assert.match(env.query(".cr-status").textContent, /填写.*代码/);
});

test("runner: unsupported record language requires an explicit supported choice", async () => {
  const env = setup({ ...ITEM, language: "Java" }); await env.caps(); env.submit();
  assert.equal(env.query(".cr-language").value, ""); assert.equal(env.calls.length, 1);
  assert.match(env.query(".cr-status").textContent, /选择.*Python.*C\+\+/);
});

for (const invalid of [{ code: " " }, { code: "x".repeat(40001) }, { stdin: "x".repeat(10001) }, { language: "Bash" }]) {
  test(`runner: invalid draft ${Object.keys(invalid)[0]} is rejected before POST`, async () => {
    const env = setup(); await env.caps();
    for (const [name, value] of Object.entries(invalid)) env.query(`.cr-${name}`).value = value;
    env.submit(); assert.equal(env.calls.length, 1); assert.equal(env.query(".cr-run").disabled, false);
  });
}

for (const status of ["completed", "time_limit", "compile_error", "runtime_error"]) {
  test(`runner: ${status} outputs are plain text and never interpreted as a verdict`, async () => {
    const env = setup(); await env.caps(); env.submit();
    const text = '<script>window.bad = true</script><img src=x onerror="bad()">';
    env.calls[1].resolve({ ...RESULT, status, stdout: text, stderr: "错误", compile_output: "编译输出", truncated: true }); await tick();
    assert.equal(env.query(".cr-results").querySelectorAll("script, img").length, 0);
    assert.equal(env.query(".cr-results").querySelectorAll("pre")[0].textContent, text);
    assert.equal(env.query(".cr-results").querySelectorAll("pre").length, 3);
    assert.match(env.host.textContent, /截断/); assert.doesNotMatch(env.query(".cr-status").textContent, /答案正确|通过判题/);
  });
}

for (const mutate of [
  (s) => { s.epoch += 1; }, (s) => { s.user = { id: 2 }; }, (s) => { s.user = null; },
  (s) => { s.view = "home"; }, (s) => { s.selection += 1; }, (s) => { s.detail += 1; },
]) {
  for (const error of [false, true]) {
    test(`runner: late ${error ? "error" : "success"} after identity or detail change is discarded`, async () => {
      const env = setup(); await env.caps(); env.submit(); mutate(env.session);
      const before = env.host.textContent;
      if (error) env.calls[1].reject(new Error("OLD ACCOUNT ERROR"));
      else env.calls[1].resolve({ ...RESULT, stdout: "OLD ACCOUNT OUTPUT" });
      await tick(); assert.equal(env.host.textContent, before);
      assert.doesNotMatch(env.host.textContent, /OLD ACCOUNT/);
    });
  }
}

test("runner: remount starts enabled and old finally cannot unlock its pending run", async () => {
  const env = setup(); await env.caps(); env.submit(); const old = env.calls[1];
  const oldPanel = env.query(".cr-panel"); env.mount({ ...ITEM, id: 9, code: "print(2)" });
  assert.equal(oldPanel.textContent, "", "unmount must erase detached private code and results");
  await env.caps(CAPABILITIES, env.calls[2]); env.submit();
  assert.equal(env.calls[3].url, "/api/mistakes/9/run");
  old.resolve({ ...RESULT, stdout: "OLD RESULT" }); await tick();
  assert.equal(env.query(".cr-run").disabled, true); assert.doesNotMatch(env.host.textContent, /OLD RESULT/);
  env.calls[3].resolve(RESULT); await tick(); assert.equal(env.query(".cr-run").disabled, false);
});

test("runner: closing the panel cancels its display request and reopening does not remain busy", async () => {
  const env = setup(); await env.caps(); env.submit(); const old = env.calls[1];
  const details = env.query("details"); details.open = false; details.dispatchEvent(new FakeEvent("toggle"));
  env.open(); assert.equal(env.query(".cr-run").disabled, false); env.submit();
  old.resolve({ ...RESULT, stdout: "LATE CLOSED RESULT" }); await tick();
  assert.doesNotMatch(env.host.textContent, /LATE CLOSED RESULT/); assert.equal(env.query(".cr-run").disabled, true);
  env.calls[2].resolve(RESULT); await tick(); assert.equal(env.query(".cr-run").disabled, false);
});

test("runner: late capabilities after remount cannot enable a disabled new panel", async () => {
  const env = setup(); const old = env.calls[0]; env.mount({ ...ITEM, id: 8 });
  await env.caps({ ...CAPABILITIES, configured: false }, env.calls[1]); old.resolve(CAPABILITIES); await tick();
  assert.equal(env.query(".cr-run").disabled, true); assert.match(env.host.textContent, /站长配置/);
});

test("runner: capability failure is local, retry reads only config, and reset clears private contents", async () => {
  const env = setup(); env.calls[0].reject(new Error("配置读取失败")); await tick();
  assert.match(env.host.textContent, /配置读取失败/); env.query(".cr-retry").click();
  assert.equal(env.calls[1].url, "/api/code-runner"); await env.caps(CAPABILITIES, env.calls[1]);
  env.submit(); env.calls[2].reject(Object.assign(new Error("运行过于频繁"), { status: 429 })); await tick();
  assert.match(env.host.textContent, /运行过于频繁/); assert.equal(env.query(".cr-run").disabled, false);
  const panel = env.query(".cr-panel"); env.window.CodeRunner.reset();
  assert.equal(env.host.textContent, ""); assert.equal(panel.textContent, "");
});

for (const invalid of [null, {}, { ...CAPABILITIES, allowed: "yes" }, { ...CAPABILITIES, languages: ["Bash"] }]) {
  test("runner: malformed capability responses fail closed", async () => {
    const env = setup(); await env.caps(invalid); env.submit();
    assert.equal(env.query(".cr-run").disabled, true); assert.equal(env.calls.length, 1);
  });
}

test("runner: hidden review content cannot submit a request", async () => {
  const env = setup(); await env.caps(); env.host.hidden = true; env.submit();
  assert.equal(env.calls.length, 1);
});

for (const mutate of [
  (s) => { s.epoch += 1; }, (s) => { s.user = { id: 2 }; }, (s) => { s.user = null; },
  (s) => { s.view = "home"; }, (s) => { s.selection += 1; }, (s) => { s.detail += 1; },
]) {
  for (const error of [false, true]) {
    test(`runner: late configuration ${error ? "failure" : "success"} cannot change an obsolete detail`, async () => {
      const env = setup(); mutate(env.session);
      const before = env.host.textContent;
      if (error) env.calls[0].reject(new Error("OLD CONFIG ERROR"));
      else env.calls[0].resolve(CAPABILITIES);
      await tick();
      assert.equal(env.host.textContent, before);
      assert.equal(env.query(".cr-run").disabled, true);
      assert.doesNotMatch(env.host.textContent, /OLD CONFIG/);
    });
  }
}

for (const invalid of [null, {}, { ...RESULT, status: "__proto__" }, { ...RESULT, stdout: 3 },
  { ...RESULT, truncated: "false" }]) {
  test("runner: malformed execution results show a local error and allow retry", async () => {
    const env = setup(); await env.caps(); env.submit();
    env.calls[1].resolve(invalid); await tick();
    assert.match(env.query(".cr-status").textContent, /无效结果/);
    assert.equal(env.query(".cr-results").textContent, "");
    assert.equal(env.query(".cr-run").disabled, false);
    env.submit(); assert.equal(env.calls.length, 3);
    env.calls[2].resolve(RESULT); await tick();
    assert.match(env.query(".cr-results").textContent, /hello/);
  });
}

test("runner: oversized output is clipped even when the provider misses its truncation flag", async () => {
  const env = setup(); await env.caps(); env.submit();
  env.calls[1].resolve({ ...RESULT, stdout: "x".repeat(9000) }); await tick();
  assert.equal(env.query(".cr-results").querySelectorAll("pre")[0].textContent.length, 8000);
  assert.match(env.query(".cr-results").textContent, /截断/);
});

test("runner: a trial account cannot run even with an incorrectly permissive capability response", async () => {
  const env = setup(); env.session.user.is_trial = true;
  await env.caps(); env.submit();
  assert.equal(env.query(".cr-run").disabled, true);
  assert.equal(env.calls.length, 1);
  assert.match(env.query(".cr-status").textContent, /普通账号/);
});

test("runner: unavailable language is cleared and an explicit supported language can be selected", async () => {
  const env = setup(); await env.caps({ ...CAPABILITIES, languages: ["C++"] });
  assert.equal(env.query(".cr-language").value, "");
  env.submit(); assert.equal(env.calls.length, 1);
  env.query(".cr-language").value = "C++"; env.query(".cr-code").value = "int main() {}";
  env.submit(); assert.equal(JSON.parse(env.calls[1].request.body).language, "C++");
  env.calls[1].resolve(RESULT); await tick();
});

test("runner: global busy cleanup cannot enable run or retry during the initial configuration request", () => {
  const env = setup(); env.resetGlobalBusy();
  assert.equal(env.query(".cr-run").disabled, true);
  assert.equal(env.query(".cr-retry").disabled, true);
});

for (const state of ["unconfigured", "disallowed", "trial", "offline"]) {
  test(`runner: global busy cleanup preserves the ${state} run restriction`, async () => {
    const env = setup(ITEM, { offline: state === "offline" });
    if (state === "trial") env.session.user.is_trial = true;
    if (state !== "offline") await env.caps({ ...CAPABILITIES,
      configured: state !== "unconfigured", allowed: state !== "disallowed" });
    env.resetGlobalBusy();
    assert.equal(env.query(".cr-run").disabled, true);
    env.submit(); assert.equal(env.calls.length, state === "offline" ? 0 : 1);
  });
}

test("runner: global busy cleanup cannot unlock either button during a run, and completion releases both", async () => {
  const env = setup(); await env.caps(); env.submit(); env.resetGlobalBusy();
  assert.equal(env.query(".cr-run").disabled, true);
  assert.equal(env.query(".cr-retry").disabled, true);
  env.calls[1].resolve(RESULT); await tick(); env.resetGlobalBusy();
  assert.equal(env.query(".cr-run").disabled, false);
  assert.equal(env.query(".cr-retry").disabled, false);
});

test("runner: global busy cleanup preserves a pending configuration retry and releases it after failure", async () => {
  const env = setup(); env.calls[0].reject(new Error("配置读取失败")); await tick();
  env.resetGlobalBusy(); assert.equal(env.query(".cr-retry").disabled, false);
  env.query(".cr-retry").click(); env.resetGlobalBusy();
  assert.equal(env.query(".cr-run").disabled, true);
  assert.equal(env.query(".cr-retry").disabled, true);
  env.calls[1].reject(new Error("配置仍不可用")); await tick(); env.resetGlobalBusy();
  assert.equal(env.query(".cr-run").disabled, true);
  assert.equal(env.query(".cr-retry").disabled, false);
});
