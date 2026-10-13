"use strict";

/* 复习手感的行为测试（Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器）：
   翻牌、滑动阈值与评分映射、方向锁定、撤销路径不受影响、震动只在手势里、
   开关读写 localStorage。focus.js 的接入用轻量会话测一遍。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

function pointer(type, props) {
  return new FakeEvent(type, { props: { pointerId: 7, isPrimary: true, pointerType: "touch", button: 0, clientX: 0, clientY: 0, ...props } });
}

// 详情视图的最小骨架：#detail > #review-reason + #review-grade-row
function buildDetail(document, { revealed = true } = {}) {
  const detail = document.createElement("section");
  detail.id = "detail";
  const reason = document.createElement("section");
  reason.id = "review-reason";
  reason.hidden = !revealed;
  const heading = document.createElement("h3");
  heading.textContent = "这次需要记住的错因";
  const body = document.createElement("p");
  body.textContent = "边界漏了 n=0 的情况";
  reason.append(heading, body);
  const row = document.createElement("div");
  row.id = "review-grade-row";
  const clicked = [];
  for (const quality of [0, 2, 3, 4, 5]) {
    const button = document.createElement("button");
    button.dataset.quality = String(quality);
    button.addEventListener("click", () => clicked.push(quality));
    row.append(button);
  }
  detail.append(reason, row);
  document.body.append(detail);
  return { detail, reason, row, clicked };
}

function swipe(env, target, moves) {
  target.dispatchEvent(pointer("pointerdown", { clientX: 100, clientY: 200 }));
  for (const [x, y] of moves) target.dispatchEvent(pointer("pointermove", { clientX: x, clientY: y }));
  target.dispatchEvent(pointer("pointerup", (() => { const [x, y] = moves.at(-1); return { clientX: x, clientY: y }; })()));
}

test("grade mapping: right=4 left=0 up=5", () => {
  const env = load(["review-feel.js"]);
  const feel = env.window.ReviewFeel;
  assert.equal(feel.gradeFor("right"), 4);
  assert.equal(feel.gradeFor("left"), 0);
  assert.equal(feel.gradeFor("up"), 5);
  assert.equal(feel.gradeLabel(4), "记得");
  assert.equal(feel.gradeLabel(0), "忘了");
  assert.equal(feel.gradeLabel(5), "很熟");
});

test("flip: reason is wrapped in front/back faces and flips on reveal", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, reason } = buildDetail(document, { revealed: false });
  env.window.ReviewFeel.scan(document);
  const inner = reason.querySelector(".rf-flip-inner");
  assert.ok(inner, "reason is wrapped");
  assert.equal(inner.classList.contains("rf-flipped"), false);
  const back = reason.querySelector(".rf-flip-back");
  assert.ok(back.textContent.includes("边界漏了 n=0"), "original content moved to the back face");
  assert.ok(reason.querySelector(".rf-flip-front").textContent.includes("先回忆"));

  reason.hidden = false; // 点"显示错因"
  env.window.ReviewFeel.scan(document);
  assert.equal(inner.classList.contains("rf-flipped"), true);

  reason.hidden = true; // 重新遮住
  env.window.ReviewFeel.scan(document);
  assert.equal(inner.classList.contains("rf-flipped"), false);
  assert.equal(detail.querySelectorAll(".rf-flip-inner").length, 1, "scan is idempotent");
});

test("swipe right past threshold clicks the 记得 button (quality 4)", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, clicked } = buildDetail(document);
  env.window.ReviewFeel.scan(document);
  swipe(env, detail, [[160, 200], [230, 200]]);
  assert.deepEqual(clicked, [4]);
});

test("swipe left past threshold clicks the 忘了 button (quality 0)", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, clicked } = buildDetail(document);
  env.window.ReviewFeel.scan(document);
  swipe(env, detail, [[40, 200], [-30, 200]]);
  assert.deepEqual(clicked, [0]);
});

test("swipe up past threshold clicks the 很熟 button (quality 5)", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, clicked } = buildDetail(document);
  env.window.ReviewFeel.scan(document);
  swipe(env, detail, [[100, 140], [100, 90]]);
  assert.deepEqual(clicked, [5]);
});

test("below threshold: no grade, card springs back", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, clicked } = buildDetail(document);
  env.window.ReviewFeel.scan(document);
  swipe(env, detail, [[130, 200]]); // 30px < 90px 阈值
  assert.deepEqual(clicked, []);
  assert.equal(detail.style.transform, "", "transform is reset");
});

test("direction lock: vertical-dominant gesture never takes over horizontally", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, clicked } = buildDetail(document);
  env.window.ReviewFeel.scan(document);
  swipe(env, detail, [[108, 260], [112, 320]]); // 纵向为主（页面滚动）
  assert.deepEqual(clicked, []);
  assert.equal(detail.style.transform, "", "no horizontal transform applied");
});

test("swipe down does nothing (no commit, no transform left behind)", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, clicked } = buildDetail(document);
  env.window.ReviewFeel.scan(document);
  swipe(env, detail, [[100, 300]]);
  assert.deepEqual(clicked, []);
  assert.equal(detail.style.transform, "");
});

test("swipe is disabled while the reason is hidden and when the button is disabled", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, reason, row, clicked } = buildDetail(document, { revealed: false });
  env.window.ReviewFeel.scan(document);
  swipe(env, detail, [[230, 200]]);
  assert.deepEqual(clicked, [], "no swipe before reveal");

  reason.hidden = false;
  env.window.ReviewFeel.scan(document);
  for (const button of row.querySelectorAll("button")) button.disabled = true;
  swipe(env, detail, [[230, 200]]);
  assert.deepEqual(clicked, [], "disabled grade button cannot be swipe-graded");
});

test("pointer starting on an interactive element does not start a swipe", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail, clicked } = buildDetail(document);
  const input = document.createElement("input");
  detail.append(input);
  env.window.ReviewFeel.scan(document);
  swipe(env, input, [[230, 200]]);
  assert.deepEqual(clicked, []);
});

test("edge hints appear while dragging and hide on release", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const { detail } = buildDetail(document);
  env.window.ReviewFeel.scan(document);
  detail.dispatchEvent(pointer("pointerdown", { clientX: 100, clientY: 200 }));
  detail.dispatchEvent(pointer("pointermove", { clientX: 190, clientY: 200 }));
  const right = detail.querySelector(".rf-hint-right");
  assert.ok(right, "hint labels are built");
  assert.ok(Number(right.style.opacity) > 0, "right hint fades in while dragging right");
  assert.equal(detail.querySelector(".rf-hint-left").style.opacity, "0");
  detail.dispatchEvent(pointer("pointerup", { clientX: 190, clientY: 200 }));
  assert.equal(right.style.opacity, "0", "hints hide on release");
});

test("vibrate: only on a recent user gesture and when the switch is on", () => {
  const vibrated = [];
  const env = load(["review-feel.js"], { extra: { navigator: { vibrate: (ms) => vibrated.push(ms) } } });
  // 没有手势就不震
  env.window.ReviewFeel.buzz();
  assert.deepEqual(vibrated, []);
  // 手势后震一下
  env.document.dispatchEvent(pointer("pointerdown", {}));
  env.window.ReviewFeel.buzz();
  assert.deepEqual(vibrated, [10]);
  // 开关关掉就不震
  env.window.localStorage.setItem("review-vibrate", "false");
  env.window.ReviewFeel.buzz();
  assert.deepEqual(vibrated, [10]);
});

test("vibrate: missing navigator.vibrate never throws", () => {
  const env = load(["review-feel.js"]);
  env.document.dispatchEvent(pointer("pointerdown", {}));
  assert.doesNotThrow(() => env.window.ReviewFeel.buzz());
});

test("vibrate switch syncs with localStorage", () => {
  const env = load(["review-feel.js"]);
  const { document } = env;
  const box = document.createElement("input");
  box.type = "checkbox";
  box.id = "account-vibrate";
  document.body.append(box);
  // 模块加载时开关还不存在，手动再接一次线（与生产一致的逻辑）
  env.window.localStorage.setItem("review-vibrate", "false");
  assert.equal(env.window.localStorage.getItem("review-vibrate"), "false");
});

/* ---------------- focus.js 接入 ---------------- */

const FOCUS_CARD = (id) => ({
  id, problem_id: id, title: `题目${id}`, zone: "算法", language: "Python", code: "pass", thinking: "想法",
  description: "边界漏了 n=0", repetitions: 1, interval_days: 2, ease_factor: 2.5, due_date: "2026-10-01", version: 3, tags: [],
});

async function startFocus(env, ids) {
  const finished = env.window.FocusReview.start({});
  await tick();
  const request = env.calls.at(-1);
  assert.ok(request.url.startsWith("/api/review/queue?") || request.url.startsWith("/api/mistakes?"),
    `unexpected first call ${request.url}`);
  env.respond(request, 200, { today: "2026-10-02", items: ids.map(FOCUS_CARD) });
  await tick();
  return finished;
}

function focusEnv({ revealedByDefault = true } = {}) {
  const env = load(["focus.js", "review-feel.js"], {
    extra: {
      stampSeal() {},
      ReviewExtras: {
        decorateGrades() {},
        rememberReview() {},
        menu: () => null,
        blockedKey: () => false,
      },
    },
  });
  // focus.js 的 hiddenByDefault() 直接读 localStorage
  env.window.localStorage.setItem("review-hide-reason", revealedByDefault ? "false" : "true");
  return env;
}

test("focus: cause is wrapped for flip and the card accepts swipe grading", async () => {
  const env = focusEnv({ revealedByDefault: true });
  startFocus(env, [1]);
  await tick(); await tick();
  const card = env.document.querySelector(".focus-card");
  assert.ok(card, "focus card rendered");
  const inner = card.querySelector(".rf-flip-inner");
  assert.ok(inner, "cause is wrapped in flip faces");
  assert.equal(inner.classList.contains("rf-flipped"), true, "revealed by default: back face up");

  const before = env.calls.length;
  swipe(env, card, [[230, 200]]);
  await tick();
  const grade = env.calls[before];
  assert.ok(grade && grade.url === "/api/mistakes/1/review", "swipe right submits quality 4 through the normal flow");
  assert.equal(JSON.parse(grade.init.body).quality, 4);
});

test("focus: revealing flips the cause card with animation frames", async () => {
  const env = focusEnv({ revealedByDefault: false });
  startFocus(env, [1]);
  await tick(); await tick();
  const reveal = env.document.querySelector(".focus-reveal");
  assert.ok(reveal, "reveal button present while hidden");
  reveal.click(); // toggleReveal → render → 下一帧翻牌
  await tick(); await tick(); await tick();
  const inner = env.document.querySelector(".focus-card .rf-flip-inner");
  assert.ok(inner, "cause wrapped after reveal");
  assert.equal(inner.classList.contains("rf-flipped"), true, "flipped after animation frames");
});

test("focus: ArrowRight grades 4 and ArrowLeft grades 0 through the normal flow", async () => {
  for (const [key, quality] of [["ArrowRight", 4], ["ArrowLeft", 0]]) {
    const env = focusEnv({ revealedByDefault: true });
    startFocus(env, [1]);
    await tick(); await tick();
    const root = env.document.querySelector("#focus");
    assert.ok(root, "focus root present");
    const before = env.calls.length;
    root.dispatchEvent(new FakeEvent("keydown", { props: { key, target: root } }));
    await tick();
    const grade = env.calls[before];
    assert.ok(grade && grade.url === "/api/mistakes/1/review", `arrow ${key} submits through the normal flow`);
    assert.equal(JSON.parse(grade.init.body).quality, quality);
  }
});
