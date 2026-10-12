"use strict";

/* 「手感层」（static/feel.js）的行为测试：聚光灯变量、磁吸、数字滚动、FLIP、提示条、
   减少动画 / 触屏下全部关闭、静止后没有空转的 rAF。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, FakeEvent } = require("./js_harness.cjs");

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function setup({ reduced = false, hover = true } = {}) {
  const frames = { requested: 0 };
  const env = load(["feel.js"], {
    extra: {
      matchMedia: (query) => ({
        matches: query.includes("prefers-reduced-motion") ? reduced : query.includes("hover: hover") ? hover : false,
        addEventListener() {},
      }),
      requestAnimationFrame: (callback) => { frames.requested += 1; return setTimeout(callback, 0); },
      performance: { now: () => Date.now() },
    },
  });
  return { env, frames, Feel: env.window.Feel };
}

function pointer(env, target, x, y) {
  const event = new FakeEvent("pointermove");
  Object.assign(event, { clientX: x, clientY: y, pointerType: "mouse", target });
  env.document.dispatchEvent(event);
}

test("feel: exposes the public API", () => {
  const { Feel } = setup();
  assert.deepEqual(Object.keys(Feel).sort(), ["countUp", "flip", "reduced", "toast"]);
});

test("feel: count-up ends on the exact final text and remembers the value", async () => {
  const { env, Feel } = setup();
  const el = env.document.createElement("span");
  el.textContent = "34";
  Feel.countUp(el, 30);
  assert.equal(el.dataset.feelValue, "34");
  await wait(80);
  assert.equal(el.textContent, "34");
  // 数字变小：从旧值滚到新值，最终文字精确
  el.textContent = "33";
  Feel.countUp(el, 30);
  await wait(80);
  assert.equal(el.textContent, "33");
});

test("feel: count-up leaves non-numbers alone and never animates when motion is reduced", async () => {
  const { env, Feel } = setup({ reduced: true });
  const word = env.document.createElement("span");
  word.textContent = "完成";
  Feel.countUp(word, 30);
  assert.equal(word.textContent, "完成");
  const num = env.document.createElement("span");
  num.textContent = "12";
  Feel.countUp(num, 30);
  assert.equal(num.textContent, "12", "final value is shown immediately");
  await wait(40);
  assert.equal(num.textContent, "12");
});

test("feel: spotlight writes --mx/--my on the card under the pointer", async () => {
  const { env } = setup();
  const card = env.document.createElement("div");
  card.className = "ov-tile";
  card.getBoundingClientRect = () => ({ left: 100, top: 50, width: 200, height: 120 });
  const written = {};
  card.style = { setProperty: (name, value) => { written[name] = value; } };
  const inner = env.document.createElement("span");
  card.append(inner);
  env.document.body.append(card);
  pointer(env, inner, 160, 90);
  await wait(30);
  assert.equal(written["--mx"], "60.0px");
  assert.equal(written["--my"], "40.0px");
});

test("feel: no spotlight or magnet when motion is reduced or the device has no hover", async () => {
  for (const options of [{ reduced: true }, { hover: false }]) {
    const { env, frames } = setup(options);
    const card = env.document.createElement("div");
    card.className = "ov-tile";
    const written = {};
    card.style = { setProperty: (name, value) => { written[name] = value; } };
    env.document.body.append(card);
    pointer(env, card, 10, 10);
    await wait(30);
    assert.deepEqual(written, {}, JSON.stringify(options));
    assert.equal(frames.requested, 0, "no frame is even requested");
  }
});

test("feel: magnetic buttons pull toward the pointer then settle back, and the loop stops", async () => {
  const { env, frames } = setup();
  const button = env.document.createElement("button");
  button.setAttribute("data-magnetic", "");
  button.getBoundingClientRect = () => ({ left: 100, top: 100, width: 100, height: 40 });
  env.document.body.append(button);
  pointer(env, env.document.body, 240, 120); // 按钮中心 (150,120)，距离 90，在吸附范围内
  await wait(60);
  assert.match(button.style.translate, /^[\d.]+px [-\d.]+px$/);
  const pulled = parseFloat(button.style.translate);
  assert.ok(pulled > 5 && pulled < 40, `pulled ${pulled}`);
  pointer(env, env.document.body, 900, 900); // 远离
  await wait(700);
  assert.equal(button.style.translate, "", "released");
  const settledAt = frames.requested;
  await wait(120);
  assert.equal(frames.requested, settledAt, "idle: no more animation frames requested");
});

test("feel: flip runs the mutation and animates moved children from their old position", () => {
  const { env, Feel } = setup();
  const list = env.document.createElement("div");
  const a = env.document.createElement("div");
  const b = env.document.createElement("div");
  const tops = new Map([[a, 0], [b, 40]]);
  for (const child of [a, b]) {
    child.getBoundingClientRect = () => ({ left: 0, top: tops.get(child), width: 100, height: 40 });
  }
  const animated = [];
  list.animate = () => {};
  for (const child of [a, b]) child.animate = (frames, options) => animated.push({ child, frames, options });
  list.append(a, b);
  Feel.flip(list, () => { tops.set(a, 40); tops.set(b, 0); list.append(a); });
  assert.equal(animated.length, 2);
  const forA = animated.find((item) => item.child === a);
  assert.equal(forA.frames[0].transform, "translate(0px, -40px)");
  assert.equal(forA.frames[1].transform, "none");
});

test("feel: flip under reduced motion just applies the mutation", () => {
  const { env, Feel } = setup({ reduced: true });
  const list = env.document.createElement("div");
  let ran = 0;
  list.animate = () => assert.fail("must not animate");
  Feel.flip(list, () => { ran += 1; });
  assert.equal(ran, 1);
});

test("feel: toast shows text with textContent and removes itself", async () => {
  const { env, Feel } = setup({ reduced: true });
  const item = Feel.toast("✅ 复习完成，下次在 6 天后", { duration: 20 });
  const box = env.document.getElementById("feel-toasts");
  assert.ok(box, "container created");
  assert.equal(item.textContent, "✅ 复习完成，下次在 6 天后");
  assert.equal(box.attributes["aria-live"], "polite");
  await wait(60);
  assert.equal(box.children.length, 0, "toast removed after its time");
});
