"use strict";
const test = require("node:test");
const assert = require("node:assert/strict");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const card = (id, extra = {}) => ({ id, title: `题目${id}`, zone: "算法", tags: ["边界"],
  description: "隐藏的错因", thinking: "隐藏的思路", code: "pass", language: "Python",
  repetitions: 1, due_date: "2026-10-03", version: 3, ...extra });
function setup() {
  const memory = { last: null, menu: null, previews: [] };
  const state = { epoch: 1, user: { id: 7 }, view: "review" };
  const extras = {
    preview(item) { memory.previews.push(item.id); return Promise.resolve(null); },
    decorateGrades(buttons, item) { memory.decorated = item.id; },
    rememberReview(entry) { memory.last = entry; },
    notify(text, options = {}) { memory.notice = { text, ...options }; },
    blockedKey(event) { return Boolean(event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey || event.target?.closest?.("input, textarea, select, [contenteditable], [role=menu], dialog[open]")); },
    editable(target) { return Boolean(target?.closest?.("input, textarea, select, [contenteditable]")); },
    menu(options) {
      memory.menu = options;
      const wrap = env.document.createElement("div");
      wrap.className = "review-more";
      for (const [action, days] of [["snooze", 1], ["suspend", undefined]]) {
        const button = env.document.createElement("button");
        button.dataset.reviewAction = action;
        if (days) button.dataset.days = String(days);
        button.textContent = action;
        button.addEventListener("click", () => options.onAction(action, days, { version: 4, due_date: "2026-10-05", suspended_at: action === "suspend" ? "now" : null }));
        wrap.append(button);
      }
      return wrap;
    },
  };
  const env = load(["problem-cards.js", "focus.js"], { extra: { ReviewExtras: extras } });
  const api = async (url, init = {}) => {
    const response = await env.window.fetch(url, init);
    const data = await response.json();
    if (!response.ok) { const error = new Error(data.detail || "请求失败"); error.status = response.status; throw error; }
    return data;
  };
  env.window.FocusReview.configure({ api, getEpoch: () => state.epoch, getUser: () => state.user, getView: () => state.view });
  return { ...env, memory, state };
}
async function begin(env, options = {}, items = [card(1), card(2)], queue = {}) {
  env.window.FocusReview.start(options);
  await tick();
  const request = env.calls.at(-1);
  env.respond(request, 200, { today: "2026-10-04", cap: 20, done_today: 12, total_due: items.length, capped: false, items, ...queue });
  await tick();
  return request;
}
function key(env, value, target = env.document.querySelector(".focus-stage"), props = {}) {
  const event = new FakeEvent("keydown", { props: { key: value, target, ...props } });
  env.document.querySelector("#focus").dispatchEvent(event);
  return event;
}
function reveal(env) { env.document.querySelector(".focus-reveal").click(); }

test("focus feel: queue preserves zone/tag and the first question hides cause, thinking and grades", async () => {
  const env = setup();
  const request = await begin(env, { zone: "算法", tag: "边界" });
  assert.match(request.url, /^\/api\/review\/queue\?/);
  assert.match(request.url, /zone=%E7%AE%97%E6%B3%95/);
  assert.match(request.url, /tag=%E8%BE%B9%E7%95%8C/);
  const stage = env.document.querySelector(".focus-stage");
  assert.doesNotMatch(stage.textContent, /隐藏的错因|隐藏的思路/);
  assert.equal(env.document.querySelector(".focus-grade-row").hidden, true);
  assert.match(stage.textContent, /算法.*题目1.*边界/);
  env.window.FocusReview.close();
});
test("focus feel: space reveals once and focuses first grade; input, links and modifiers are protected", async () => {
  const env = setup(); await begin(env);
  const input = env.document.createElement("input");
  key(env, " ", input); assert.doesNotMatch(env.document.querySelector(".focus-stage").textContent, /隐藏的错因/);
  key(env, " ", undefined, { ctrlKey: true }); assert.doesNotMatch(env.document.querySelector(".focus-stage").textContent, /隐藏的错因/);
  key(env, " "); assert.match(env.document.querySelector(".focus-stage").textContent, /隐藏的错因.*隐藏的思路/);
  assert.equal(env.document.activeElement.dataset.quality, "0");
  key(env, " "); assert.match(env.document.querySelector(".focus-stage").textContent, /隐藏的错因/);
  env.window.FocusReview.close();
});
test("focus feel: grade shortcuts wait for reveal and do not consume editable, menu or dialog keys", async () => {
  const env = setup(); await begin(env);
  const before = env.calls.length;
  key(env, "4"); assert.equal(env.calls.length, before);
  reveal(env);
  for (const tag of ["input", "textarea", "select"]) key(env, "4", env.document.createElement(tag));
  const menu = env.document.createElement("div"); menu.setAttribute("role", "menu"); key(env, "4", menu);
  const dialog = env.document.createElement("dialog"); dialog.showModal(); key(env, "4", dialog);
  key(env, "4", undefined, { shiftKey: true });
  assert.equal(env.calls.length, before);
  key(env, "4"); assert.equal(env.calls.at(-1).url, "/api/mistakes/1/review");
  env.window.FocusReview.close();
});
test("focus feel: success stores undo, inserts restored card before current and removes its grade", async () => {
  const env = setup(); await begin(env); reveal(env);
  key(env, "4"); env.respond(env.calls.at(-1), 200, { interval_days: 6, version: 4 }); await tick();
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目2");
  assert.equal(env.memory.last.quality, 4);
  assert.equal(env.memory.last.result.interval_days, 6);
  env.memory.last.onUndo({ version: 5, due_date: "2026-10-03" });
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目1");
  assert.match(env.document.querySelector(".focus-count").textContent, /第 1 \/ 2 条/);
  reveal(env); key(env, "4");
  assert.equal(JSON.parse(env.calls.at(-1).init.body).version, 5);
  env.window.FocusReview.close();
});
test("focus feel: undo displays the restored card after the session zone filter changed", async () => {
  const env = setup(); await begin(env, {}, [card(1), card(2, { zone: "数学" }), card(3, { zone: "物理" })]);
  reveal(env); key(env, "4"); env.respond(env.calls.at(-1), 200, { interval_days: 6, version: 4 }); await tick();
  env.document.querySelectorAll(".focus-chip").find((button) => button.textContent.startsWith("数学")).click(); await tick();
  env.respond(env.calls.at(-1), 200, { today: "2026-10-04", total_due: 1, capped: false, items: [card(2, { zone: "数学" })] }); await tick();
  env.memory.last.onUndo({ version: 5, due_date: "2026-10-03" });
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目1");
  env.window.FocusReview.close();
});
test("focus feel: next preview is prefetched and busy grading does not double submit", async () => {
  const env = setup(); await begin(env); assert.ok(env.memory.previews.includes(2));
  reveal(env); key(env, "4"); key(env, "5");
  assert.equal(env.calls.filter((call) => call.url.endsWith("/review")).length, 1);
  env.window.FocusReview.close();
});
test("focus feel: capped summary names remaining due and continue uses ignore_cap", async () => {
  const env = setup(); await begin(env, {}, [card(1)], { total_due: 7, capped: true }); reveal(env);
  key(env, "4"); env.respond(env.calls.at(-1), 200, { interval_days: 6, version: 4 }); await tick();
  assert.match(env.document.querySelector(".focus-summary").textContent, /今天已达上限，还有 6 条明天再复习/);
  env.document.querySelector(".focus-continue").click(); await tick();
  assert.match(env.calls.at(-1).url, /ignore_cap=true/);
  env.respond(env.calls.at(-1), 200, { today: "2026-10-04", cap: 20, done_today: 20, total_due: 6, capped: false, items: [card(3)] }); await tick();
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目3");
  env.window.FocusReview.close();
});
test("focus feel: ids fetch the supplied records directly and bypass queue caps", async () => {
  const env = setup(); env.window.FocusReview.start({ ids: [5, 3] }); await tick();
  assert.deepEqual(env.calls.map((call) => call.url), ["/api/mistakes/5", "/api/mistakes/3"]);
  for (const call of env.calls) env.respond(call, 200, card(Number(call.url.split("/").at(-1))));
  await tick(); assert.equal(env.document.querySelector("#focus-title").textContent, "题目5");
  env.window.FocusReview.close();
});
test("focus feel: absent queue falls back to the previous due list", async () => {
  const env = setup(); env.window.FocusReview.start({}); await tick();
  env.respond(env.calls[0], 404, { detail: "Not Found" }); await tick();
  assert.match(env.calls.at(-1).url, /^\/api\/mistakes\?due_only=true/);
  env.respond(env.calls.at(-1), 200, { today: "2026-10-04", items: [card(1)] }); await tick();
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目1");
  env.window.FocusReview.close();
});
test("focus feel: snooze and suspend remove cards; a suspended card gets a restore action", async () => {
  const env = setup(); await begin(env);
  env.memory.menu.onAction("snooze", 1, { version: 4, due_date: "2026-10-05" });
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目2");
  env.memory.menu.onAction("suspend", undefined, { version: 4, suspended_at: "now" });
  assert.ok(env.document.querySelector(".focus-summary"));
  env.window.FocusReview.close();
});
test("focus feel: restoring a suspended card displays it after a zone switch", async () => {
  const env = setup(); await begin(env, {}, [card(1), card(2, { zone: "数学" }), card(3, { zone: "物理" })]);
  const original = env.memory.menu;
  original.onAction("suspend", undefined, { version: 4, suspended_at: "now" });
  env.document.querySelectorAll(".focus-chip").find((button) => button.textContent.startsWith("数学")).click(); await tick();
  env.respond(env.calls.at(-1), 200, { today: "2026-10-04", total_due: 1, capped: false, items: [card(2, { zone: "数学" })] }); await tick();
  original.onAction("unsuspend", undefined, { version: 5, suspended_at: null });
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目1");
  assert.equal(env.document.querySelector("#focus-status").textContent, "已恢复复习");
  env.window.FocusReview.close();
});
test("focus feel: session epoch changes discard late grades and their widget notifications", async () => {
  const env = setup(); await begin(env); reveal(env); key(env, "4");
  env.state.epoch += 1; env.state.user = { id: 8 };
  env.respond(env.calls.at(-1), 200, { interval_days: 6, version: 4 }); await tick();
  assert.equal(env.memory.last, null);
  assert.equal(env.events.some((event) => event.type === "app:data-changed"), false);
  env.window.FocusReview.close();
});
test("focus feel: closing a session makes remembered undo stale", async () => {
  const env = setup(); await begin(env); reveal(env); key(env, "4");
  env.respond(env.calls.at(-1), 200, { interval_days: 6, version: 4 }); await tick();
  const old = env.memory.last; env.window.FocusReview.close();
  assert.equal(old.isCurrent(), false);
});
test("focus feel: errors retain Chinese API detail in the status", async () => {
  const env = setup(); await begin(env); reveal(env); key(env, "4");
  env.respond(env.calls.at(-1), 503, { detail: "服务暂时不可用，请稍后再试" }); await tick();
  assert.equal(env.document.querySelector("#focus-status").textContent, "服务暂时不可用，请稍后再试");
  env.window.FocusReview.close();
});
test("focus feel: 409 skips the conflicted card while preserving the exact Chinese detail after render", async () => {
  const env = setup(); await begin(env); reveal(env); key(env, "4");
  env.respond(env.calls.at(-1), 409, { detail: "这条记录已更新，请刷新后再操作" }); await tick();
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目2");
  assert.equal(env.document.querySelector("#focus-status").textContent, "这条记录已更新，请刷新后再操作");
  env.window.FocusReview.close();
});

function realSetup() {
  const env = load(["problem-cards.js", "review-extras.js", "focus.js"]);
  const state = { epoch: 1, user: { id: 7 }, view: "review" };
  const options = { getEpoch: () => state.epoch, getUser: () => state.user, getView: () => state.view,
    api: async (url, init = {}) => {
      const response = await env.window.fetch(url, init);
      const data = await response.json();
      if (!response.ok) throw Object.assign(new Error(data.detail || "请求失败"), { status: response.status });
      return data;
    } };
  env.window.ReviewExtras.configure(options);
  env.window.FocusReview.configure(options);
  return { ...env, state };
}
async function settleCapabilities(env) {
  for (let step = 0; step < 3; step += 1) {
    for (const call of env.calls) {
      if (call.answered || call.init.method === "POST") continue;
      if (/\/(snooze|suspend|unsuspend|review\/undo)$/.test(call.url)) {
        call.answered = true; env.respond(call, 405, { detail: "Method Not Allowed" });
      } else if (call.url.endsWith("/preview")) {
        call.answered = true;
        env.respond(call, 200, { version: 3, previews: { "0": { interval_days: 1 }, "2": { interval_days: 1 }, "3": { interval_days: 6 }, "4": { interval_days: 6 }, "5": { interval_days: 6 } } });
      }
    }
    await tick();
  }
}
test("focus integration: real menu survives card removal through a suspend toast and restores the card", async () => {
  const env = realSetup(); await begin(env); await settleCapabilities(env);
  const originalMenu = env.document.querySelector(".review-more");
  originalMenu.querySelector('[data-review-action="suspend"]').click(); await tick();
  const suspend = env.calls.find((call) => call.url.endsWith("/suspend") && call.init.method === "POST");
  env.respond(suspend, 200, { version: 4, suspended_at: "now" }); await tick(); await settleCapabilities(env);
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目2");
  const toast = env.document.querySelector(".review-toast");
  assert.ok(toast, "rendering the next card must keep the committed operation toast");
  assert.equal(toast.querySelector("button").textContent, "恢复");
  toast.querySelector("button").click(); await tick();
  const restore = env.calls.find((call) => call.url.endsWith("/unsuspend") && call.init.method === "POST");
  assert.equal(JSON.parse(restore.init.body).version, 4);
  env.respond(restore, 200, { version: 5, suspended_at: null }); await tick();
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目1");
  assert.equal(env.document.querySelector(".review-toast").textContent, "已恢复复习");
  env.window.FocusReview.close(); env.window.ReviewExtras.reset();
});
test("focus integration: real preview labels match success interval and undo restores the current queue", async () => {
  const env = realSetup(); await begin(env); await settleCapabilities(env); reveal(env); await tick();
  assert.equal(env.document.querySelector('[data-quality="4"] .review-interval').textContent, "6 天后");
  key(env, "4"); const grade = env.calls.find((call) => call.url.endsWith("/review") && call.init.method === "POST");
  env.respond(grade, 200, { interval_days: 6, version: 4 }); await tick(); await settleCapabilities(env);
  assert.match(env.document.querySelector(".review-toast").textContent, /记得.*6 天后.*撤销/);
  env.document.querySelector(".review-toast button").click(); await tick();
  const undo = env.calls.find((call) => call.url.endsWith("/review/undo") && call.init.method === "POST");
  env.respond(undo, 200, { version: 5, due_date: "2026-10-03" }); await tick();
  assert.equal(env.document.querySelector("#focus-title").textContent, "题目1");
  assert.match(env.document.querySelector(".focus-count").textContent, /第 1 \/ 2 条/);
  env.window.FocusReview.close(); env.window.ReviewExtras.reset();
});
test("focus integration: an unfinished menu request is discarded after S moves to another card", async () => {
  const env = realSetup(); await begin(env); await settleCapabilities(env);
  env.document.querySelector('[data-review-action="snooze"][data-days="1"]').click(); await tick();
  const request = env.calls.find((call) => call.url.endsWith("/snooze") && call.init.method === "POST");
  key(env, "s"); assert.equal(env.document.querySelector("#focus-title").textContent, "题目2");
  env.respond(request, 200, { version: 4, due_date: "2026-10-05" }); await tick();
  assert.equal(env.document.querySelector(".review-toast"), null);
  key(env, "s"); assert.equal(env.document.querySelector("#focus-title").textContent, "题目1");
  env.window.FocusReview.close(); env.window.ReviewExtras.reset();
});
test("focus feel: disabling the local preference reveals the first card without a request payload", async () => {
  const env = setup(); env.window.localStorage.setItem("review-hide-reason", "false"); await begin(env);
  assert.match(env.document.querySelector(".focus-stage").textContent, /隐藏的错因.*隐藏的思路/);
  assert.equal(env.document.querySelector(".focus-grade-row").hidden, false);
  key(env, "4"); env.respond(env.calls.at(-1), 200, { interval_days: 6, version: 4 }); await tick();
  assert.equal(env.document.querySelector('[data-quality="4"]').disabled, false, "the next already-revealed card must accept another grade");
  key(env, "4"); assert.equal(env.calls.at(-1).url, "/api/mistakes/2/review");
  env.window.FocusReview.close();
});
test("focus integration: Escape closes the menu, shifted Tab traps focus and toast undo remains reachable", async () => {
  const env = realSetup(); await begin(env); await settleCapabilities(env);
  env.document.querySelector(".review-more-button").click();
  key(env, "Escape");
  assert.equal(env.window.FocusReview.isOpen(), true);
  assert.equal(env.document.querySelector(".review-more-menu").hidden, true);
  reveal(env); key(env, "4");
  const grade = env.calls.find((call) => call.url.endsWith("/review") && call.init.method === "POST");
  env.respond(grade, 200, { interval_days: 6, version: 4 }); await tick(); await settleCapabilities(env);
  const undo = env.document.querySelector(".review-toast button");
  assert.ok(env.document.querySelector(".focus-shell").contains(undo), "undo belongs inside the modal focus boundary");
  env.document.querySelector(".focus-exit").focus();
  key(env, "Tab", env.document.activeElement, { shiftKey: true });
  assert.equal(env.document.activeElement, undo);
  key(env, "Tab", undo); assert.equal(env.document.activeElement.className, "focus-exit");
  env.window.FocusReview.close(); env.window.ReviewExtras.reset();
});
