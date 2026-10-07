"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, tick, FakeEvent, deferred } = require("./js_harness.cjs");
const APP = fs.readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const settle = async () => { await tick(); await tick(); };
class Multipart {
  constructor() { this.values = []; }
  append(...value) { this.values.push(value); }
}
function setup() {
  const env = load(["import-wizard.js"], { extra: { FormData: Multipart } });
  const entry = env.document.createElement("button"); entry.id = "list-import"; env.document.body.append(entry);
  const state = { user: { id: 7 }, epoch: 1, view: "all" };
  const imported = [];
  const hooks = { getUser: () => state.user, getEpoch: () => state.epoch, getView: () => state.view,
    getZones: () => ["算法", "后端"], onImported: () => imported.push(true),
    api: async (url, init) => {
      const response = await env.window.fetch(url, { ...init, headers: { "X-CSRF-Protection": "1" } });
      const body = await response.json();
      if (!response.ok) { const error = new Error(body.detail); error.status = response.status; throw error; }
      return body;
    } };
  env.window.ImportWizard.configure(hooks);
  const open = () => { entry.click(); return env.document.getElementById("import-wizard"); };
  return { env, entry, state, imported, open, hooks };
}
function submit(dialog, file = { name: "n.md", size: 500 }) {
  dialog.querySelector("#import-file").files = [file];
  dialog.querySelector("#import-upload").dispatchEvent(new FakeEvent("submit"));
}
const preview = { preview_id: "private-token", items: [{ index: 0, title: "<img src=x onerror=alert(1)>",
  zone: "算法", mistakes_count: 2, warnings: ["示例提醒"] }], skipped: ["知识点：复杂度"] };
test("import wizard exposes three steps and real aria label targets", () => {
  const ctx = setup(), dialog = ctx.open();
  assert.equal(dialog.open, true);
  assert.equal(dialog.getAttribute("aria-labelledby"), "import-title");
  assert.equal(dialog.querySelector("#import-title").textContent, "导入错题");
  assert.equal(dialog.querySelector("#import-file").getAttribute("accept"), ".md,.markdown,.json");
  assert.match(dialog.textContent, /1\. 选择文件/);
  assert.equal(dialog.querySelector("#import-zone").children.length, 2);
});
test("preview uses host api and renders untrusted titles as text", async () => {
  const ctx = setup(), dialog = ctx.open(); submit(dialog);
  assert.equal(ctx.env.calls.length, 1); assert.equal(ctx.env.calls[0].url, "/api/import/preview");
  assert.equal(ctx.env.calls[0].init.headers["X-CSRF-Protection"], "1");
  assert.equal(ctx.env.calls[0].init.body.values[0][0], "file");
  ctx.env.respond(ctx.env.calls[0], 200, preview); await settle();
  assert.match(dialog.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(dialog.querySelectorAll("img").length, 0);
  assert.match(dialog.textContent, /2\. 预览并勾选/);
});
test("confirmation sends only stored token and checked indices and shows result", async () => {
  const ctx = setup(), dialog = ctx.open(); submit(dialog);
  ctx.env.respond(ctx.env.calls[0], 200, preview); await settle();
  dialog.querySelector("#import-confirm").click();
  assert.deepEqual(JSON.parse(ctx.env.calls[1].init.body), { preview_id: "private-token", indices: [0] });
  ctx.env.respond(ctx.env.calls[1], 200, { imported: 1, duplicates: [], failed: [] }); await settle();
  assert.match(dialog.textContent, /新增 1 道题/); assert.deepEqual(ctx.imported, [true]);
  assert.equal(dialog.querySelector("#import-confirm"), null);
});
test("close and reopen while preview pending resets busy and discards late result", async () => {
  const ctx = setup(), first = ctx.open(); submit(first);
  first.querySelector("#import-close").click();
  assert.equal(ctx.entry.hidden, false, "same account can reopen from the visible entry");
  const dialog = ctx.open();
  assert.equal(dialog.querySelector("#import-preview").disabled, false);
  assert.equal(dialog.querySelector("#import-file").value, "");
  ctx.env.respond(ctx.env.calls[0], 200, preview); await settle();
  assert.equal(dialog.querySelector("#import-confirm"), null);
  assert.doesNotMatch(dialog.textContent, /private-token|示例提醒/);
});
test("a queued close event from an earlier opening cannot clear the new upload", async () => {
  const ctx = setup(), first = ctx.open(); first.querySelector("#import-close").click();
  const dialog = ctx.open(); submit(dialog);
  dialog.dispatchEvent(new FakeEvent("close"));
  assert.equal(dialog.open, true);
  ctx.env.respond(ctx.env.calls[0], 200, preview); await settle();
  assert.ok(dialog.querySelector("#import-confirm"));
});
test("signed-out reset erases selected file and private preview", async () => {
  const ctx = setup(), dialog = ctx.open(); submit(dialog);
  ctx.env.respond(ctx.env.calls[0], 200, preview); await settle();
  ctx.env.window.ImportWizard.reset(); ctx.state.user = { id: 8 }; ctx.state.epoch += 1;
  const fresh = ctx.open(); assert.equal(fresh.querySelector("#import-confirm"), null);
  assert.equal(fresh.querySelector("#import-file").value, "");
  assert.doesNotMatch(fresh.textContent, /示例提醒/);
});
test("new account before late confirm cannot receive status or refresh", async () => {
  const ctx = setup(), dialog = ctx.open(); submit(dialog);
  ctx.env.respond(ctx.env.calls[0], 200, preview); await settle(); dialog.querySelector("#import-confirm").click();
  ctx.env.window.ImportWizard.reset(); ctx.state.user = { id: 8 }; ctx.state.epoch += 1;
  ctx.open(); ctx.env.respond(ctx.env.calls[1], 200, { imported: 1, duplicates: [], failed: [] }); await settle();
  assert.deepEqual(ctx.imported, []); assert.doesNotMatch(dialog.textContent, /新增 1 道题/);
});
test("view change closes pending dialog and suppresses delayed errors", async () => {
  const ctx = setup(), dialog = ctx.open(); submit(dialog);
  ctx.state.view = "today"; ctx.env.document.dispatchEvent(new FakeEvent("app:view-changed"));
  ctx.env.respond(ctx.env.calls[0], 422, { detail: "old error" }); await settle();
  assert.equal(dialog.open, false); assert.equal(ctx.entry.hidden, true);
  assert.doesNotMatch(dialog.textContent, /old error/);
});
test("local size check blocks oversized upload and releases preview button", async () => {
  const ctx = setup(), dialog = ctx.open(); submit(dialog, { name: "n.md", size: 2097153 }); await settle();
  assert.equal(ctx.env.calls.length, 0); assert.match(dialog.textContent, /2 MiB/);
  assert.equal(dialog.querySelector("#import-preview").disabled, false);
});
test("failed confirmation preserves preview so retry stays idempotent", async () => {
  const ctx = setup(), dialog = ctx.open(); submit(dialog);
  ctx.env.respond(ctx.env.calls[0], 200, preview); await settle(); dialog.querySelector("#import-confirm").click();
  ctx.env.respond(ctx.env.calls[1], 503, { detail: "此文件未写入" }); await settle();
  assert.equal(dialog.querySelector("#import-confirm").disabled, false); dialog.querySelector("#import-confirm").click();
  assert.deepEqual(JSON.parse(ctx.env.calls[2].init.body), JSON.parse(ctx.env.calls[1].init.body));
});
test("production api lets browser set multipart boundary while retaining csrf", async () => {
  const source = APP.slice(APP.indexOf("async function api("), APP.indexOf("\n}", APP.indexOf("async function api(")) + 2);
  const calls = [];
  const context = vm.createContext({ sessionEpoch: 1, FormData: Multipart, signedOut() {},
    fetch: async (url, init) => { calls.push(init); return { ok: true, json: async () => ({ ok: true }) }; } });
  vm.runInContext(source, context); context.body = new Multipart();
  await vm.runInContext("api('/api/import/preview', { method: 'POST', body })", context);
  assert.equal(calls[0].headers["Content-Type"], undefined); assert.equal(calls[0].headers["X-CSRF-Protection"], "1");
  await vm.runInContext("api('/api/problems', { method: 'POST', body: '{}' })", context);
  assert.equal(calls[1].headers["Content-Type"], "application/json");
});
