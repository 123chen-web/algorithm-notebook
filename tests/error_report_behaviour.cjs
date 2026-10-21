"use strict";

/* 前端错误上报（static/error-report.js）的行为测试：假浏览器里触发 error / unhandledrejection。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const vm = require("node:vm");
const { load } = require("./js_harness.cjs");

function setup({ hash = "#/app/12?x=1", fetchImpl } = {}) {
  const listeners = {};
  const sent = [];
  const env = load(["error-report.js"], {
    extra: {
      addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
      location: { hash },
      navigator: { userAgent: "UA/1.0 " + "x".repeat(300) },
      fetch: fetchImpl || ((url, init) => { sent.push({ url, init, body: JSON.parse(init.body) }); return Promise.resolve({ ok: true }); }),
    },
  });
  const fire = (type, event) => listeners[type].forEach((fn) => fn(event));
  return { ...env, listeners, sent, fire };
}

const err = (extra = {}) => ({
  message: "x is undefined",
  filename: "https://ouyeoy.com/static/app/review-list.js?v=6",
  lineno: 10,
  colno: 4,
  error: { stack: "TypeError: x\n    at f (https://ouyeoy.com/static/app/review-list.js?v=6:10:4)" },
  ...extra,
});

test("registers both listeners and exposes helpers", () => {
  const { window, listeners } = setup();
  assert.equal(typeof window.ErrorReport.build, "function");
  assert.equal(typeof window.ErrorReport.shouldSend, "function");
  assert.equal(listeners.error.length, 1);
  assert.equal(listeners.unhandledrejection.length, 1);
});

test("error report is sanitised and posted with the CSRF header", () => {
  const { fire, sent } = setup();
  fire("error", err());
  assert.equal(sent.length, 1);
  const { url, init, body } = sent[0];
  assert.equal(url, "/api/client-errors");
  assert.equal(init.method, "POST");
  assert.equal(init.keepalive, true);
  assert.equal(init.credentials, "same-origin");
  assert.equal(init.headers["X-CSRF-Protection"], "1");
  assert.equal(body.source, "review-list.js");
  assert.equal(body.route, "#/app");
  assert.equal(body.line, 10);
  assert.equal(body.col, 4);
  assert.ok(body.ua.length <= 200);
  assert.ok(!/https?:|ouyeoy|\?v=/.test(body.stack), body.stack);
  assert.ok(body.stack.includes("review-list.js"));
});

test("build strips query strings from stack and caps lengths", () => {
  const { window } = setup();
  const report = window.ErrorReport.build(
    { message: "m".repeat(500), source: "https://h.example/a.js?token=SECRET#frag", stack: "s ".repeat(2000), line: 1, col: 2 },
    { hash: "#/notes/99", ua: "ua", build: "103" },
  );
  assert.equal(report.message.length, 300);
  assert.ok(report.stack.length <= 1500);
  assert.equal(report.source, "a.js");
  assert.equal(report.route, "#/notes");
  assert.equal(report.build, "103");
  assert.ok(!JSON.stringify(report).includes("SECRET"));
  assert.equal(window.ErrorReport.build({ message: "m" }, { hash: "" }).build, undefined);
  assert.equal(window.ErrorReport.build({ message: "m" }, { hash: "#/12345" }).route, "");
});

test("cross-origin 'Script error.' and extension errors are ignored", () => {
  const { fire, sent, window } = setup();
  fire("error", { message: "Script error.", filename: "", lineno: 0, colno: 0 });
  fire("error", err({ error: { stack: "Error\n    at chrome-extension://abc/content.js:1:1" } }));
  fire("error", err({ filename: "moz-extension://abc/x.js" }));
  fire("error", { message: "ResizeObserver loop completed with undelivered notifications.", filename: "", lineno: 0 });
  assert.equal(sent.length, 0);
  assert.equal(window.ErrorReport.build({ message: "Script error." }, {}), null);
});

test("identical reports are sent once per page load", () => {
  const { fire, sent } = setup();
  fire("error", err());
  fire("error", err());
  fire("error", err());
  assert.equal(sent.length, 1);
});

test("at most one report per second and five per page load", () => {
  const { window } = setup();
  const state = window.ErrorReport.newState();
  const report = (n) => ({ message: "m" + n, source: "a.js", line: n, col: 0 });
  assert.equal(window.ErrorReport.shouldSend(state, report(1), 10000), true);
  assert.equal(window.ErrorReport.shouldSend(state, report(2), 10500), false); // 距上一条不足 1 秒
  assert.equal(window.ErrorReport.shouldSend(state, report(2), 11000), true);
  for (let n = 3; n <= 5; n++) assert.equal(window.ErrorReport.shouldSend(state, report(n), 11000 + n * 1000), true);
  assert.equal(state.sent, 5);
  assert.equal(window.ErrorReport.shouldSend(state, report(6), 99000), false); // 第 6 条被封顶
});

test("per-load cap holds through the listener too", () => {
  const { fire, sent, context } = setup();
  // 脚本在假浏览器的独立上下文里跑，要改那个上下文里的时钟。
  vm.runInContext("var __t = 1000000; Date.now = function () { return (__t += 2000); };", context);
  for (let n = 0; n < 9; n++) fire("error", err({ message: "distinct " + n }));
  assert.equal(sent.length, 5);
});

test("unhandled rejections are reported with the reason message", () => {
  const { fire, sent } = setup();
  fire("unhandledrejection", { reason: { message: "boom", stack: "Error: boom\n    at g (https://h.example/static/app.js?v=103:3:9)" } });
  assert.equal(sent.length, 1);
  assert.equal(sent[0].body.message, "Unhandled rejection: boom");
  assert.equal(sent[0].body.source, "app.js");
  assert.ok(!sent[0].body.stack.includes("h.example"));
});

test("never throws when fetch rejects, throws, or events are malformed", async () => {
  const unhandled = [];
  const onUnhandled = (e) => unhandled.push(e);
  process.on("unhandledRejection", onUnhandled);
  try {
    const rejecting = setup({ fetchImpl: () => Promise.reject(new Error("offline")) });
    assert.doesNotThrow(() => rejecting.fire("error", err()));
    const throwing = setup({ fetchImpl: () => { throw new Error("sync"); } });
    assert.doesNotThrow(() => throwing.fire("error", err()));
    const plain = setup();
    assert.doesNotThrow(() => plain.fire("error", undefined));
    assert.doesNotThrow(() => plain.fire("error", null));
    assert.doesNotThrow(() => plain.fire("unhandledrejection", {}));
    assert.doesNotThrow(() => plain.fire("unhandledrejection", { reason: null }));
    assert.doesNotThrow(() => plain.fire("unhandledrejection", { get reason() { throw new Error("getter"); } }));
    await new Promise((resolve) => setTimeout(resolve, 10));
    assert.deepEqual(unhandled.map(String), []);
  } finally {
    process.off("unhandledRejection", onUnhandled);
  }
});
