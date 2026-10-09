"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, deferred, tick } = require("./js_harness.cjs");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function setup(file, name) {
  const env = load([file]);
  const state = { user: { id: 1 }, epoch: 1 };
  const calls = [];
  env.window[name].configure({
    api: (path, options) => {
      const request = { path, options, ...deferred() };
      calls.push(request);
      return request.promise;
    },
    getUser: () => state.user,
    getEpoch: () => state.epoch,
  });
  const host = env.document.createElement("div");
  env.document.body.append(host);
  return { ...env, state, requests: calls, host, module: env.window[name] };
}
function changeUser(env) {
  env.module.reset(); env.state.epoch++; env.state.user = { id: 2 };
}

for (const stale of [false, true]) {
  test(`the real link button ${stale ? "discards a prior session" : "requests and fills the form"}`, async () => {
    const env = setup("import-problem.js", "ImportProblem");
    const button = env.document.createElement("button"); button.id = "import-from-url-btn";
    const status = env.document.createElement("p"); status.id = "problem-import-status";
    env.document.body.append(button, status);
    let actionPromise;
    const scope = vm.createContext({
      window: env.window, user: env.state.user, sessionEpoch: 1,
      $: (selector) => env.document.querySelector(selector),
      prompt: () => "https://leetcode.com/problems/two-sum/",
      run: (action) => { actionPromise = action(); actionPromise.catch(() => {}); },
    });
    const source = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
    const start = source.indexOf('$("#import-from-url-btn")');
    vm.runInContext(source.slice(start, source.indexOf("/* ---- ", start)), scope);
    button.click();
    if (!env.requests.length) await actionPromise; // surfaces a synchronous wiring error
    assert.equal(env.requests[0].path, "/api/problems/fetch-from-url");
    if (stale) { scope.sessionEpoch++; changeUser(env); status.textContent = "new account"; }
    env.requests[0].resolve({ title: "two sum" });
    await actionPromise;
    assert.equal(status.textContent, stale ? "new account" : "已预填，请核对后保存。");
  });
}

for (const failure of [false, true]) {
  test(`similar add discards stale ${failure ? "failure" : "success"} without a second write`, async () => {
    const env = setup("similar.js", "Similar");
    env.module.mount(env.host, 7);
    env.requests[0].resolve({ items: [{title:"safe", url:"javascript:alert(1)", add_payload:{title:"safe"}}] });
    await tick(); await tick();
    assert.equal(env.host.querySelector("a"), null);
    env.host.querySelector(".similar-card-add").click();
    const status = env.host.querySelector(".similar-card-status");
    assert.equal(env.requests[1].path, "/api/problems");
    changeUser(env);
    if (failure) env.requests[1].reject(new Error("old account failure"));
    else env.requests[1].resolve({id:9, mistake_ids:[10]});
    await tick(); await tick();
    assert.equal(status.textContent, "加入中…");
    assert.equal(env.requests.length, 2, "stale response must not write tags for the next account");
  });

  test(`similar current ${failure ? "failure" : "empty result"} renders a usable notice`, async () => {
    const env = setup("similar.js", "Similar");
    env.module.mount(env.host, 7);
    if (failure) env.requests[0].reject(new Error("offline"));
    else env.requests[0].resolve({ items: [] });
    await tick(); await tick();
    assert.ok(env.host.querySelector(".similar-card-error"));
    assert.equal(Boolean(env.host.querySelector(".similar-card-retry")), failure);
  });
}

for (const failure of [false, true]) {
  test(`link import discards old account ${failure ? "error" : "prefill"}`, async () => {
    const env = setup("import-problem.js", "ImportProblem");
    const pending = env.module.fetchPrefill("https://leetcode.com/problems/two-sum/");
    assert.equal(env.requests[0].path, "/api/problems/fetch-from-url");
    assert.equal(env.requests[0].options.method, "POST");
    changeUser(env);
    if (failure) env.requests[0].reject(new Error("old account error"));
    else env.requests[0].resolve({ title: "old private title" });
    assert.equal(await pending, null);
    assert.equal(env.calls.length, 0, "must not use native fetch");
  });

  test(`heatmap and focus discard stale ${failure ? "failure" : "success"}`, async () => {
    const env = setup("heatmap.js", "Heatmap");
    const heat = env.module.loadHeatmap(env.host, 8);
    const focusHost = env.document.createElement("div");
    env.document.body.append(focusHost);
    const focus = env.module.mountFocusCard(focusHost);
    changeUser(env);
    env.host.textContent = "new account heatmap";
    focusHost.textContent = "new account focus";
    for (const request of env.requests) {
      if (failure) request.reject(new Error("old account failed"));
      else request.resolve({ tags: ["private"], weeks: ["2026-10-05"], cells: { private: [0.5] }, tag: "private", plan: [1] });
    }
    await Promise.all([heat, focus]);
    assert.equal(env.host.textContent, "new account heatmap");
    assert.equal(focusHost.textContent, "new account focus");
    assert.equal(env.calls.length, 0);
  });

  test(`similar recommendations discard stale ${failure ? "error" : "success"}`, async () => {
    const env = setup("similar.js", "Similar");
    env.module.mount(env.host, 7);
    changeUser(env);
    env.host.textContent = "new account similar";
    if (failure) env.requests[0].reject(new Error("old account error"));
    else env.requests[0].resolve({ items: [{ title: "private" }] });
    await tick(); await tick();
    assert.equal(env.host.textContent, "new account similar");
    assert.equal(env.calls.length, 0);
  });
}

test("last heatmap request wins and unsafe labels stay text", async () => {
  const env = setup("heatmap.js", "Heatmap");
  const first = env.module.loadHeatmap(env.host);
  const second = env.module.loadHeatmap(env.host);
  const label = '<img src=x onerror=alert(1)>';
  env.requests[1].resolve({ tags: [label], weeks: ["2026-10-05"], cells: { [label]: [0.5] } });
  await second;
  assert.equal(env.host.querySelector("img"), null);
  assert.ok(env.host.textContent.includes(label));
  env.requests[0].resolve({ tags: [], weeks: [], cells: {} });
  await first;
  assert.ok(env.host.querySelector("table"));
  env.host.querySelector(".hm-taglink").click();
  assert.equal(env.events.at(-1).type, "records:filter");
  assert.equal(env.events.at(-1).detail.tag, label);
});
