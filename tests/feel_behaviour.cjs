"use strict";

/* 「手感层」（static/feel.js）的行为测试：聚光灯变量、磁吸、数字滚动、FLIP、提示条、
   减少动画 / 触屏下全部关闭、静止后没有空转的 rAF。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, FakeEvent } = require("./js_harness.cjs");

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

function setup({ reduced = false, hover = true, io = null, mo = null } = {}) {
  const frames = { requested: 0 };
  const extra = {
    matchMedia: (query) => ({
      matches: query.includes("prefers-reduced-motion") ? reduced : query.includes("hover: hover") ? hover : false,
      addEventListener() {},
    }),
    requestAnimationFrame: (callback) => { frames.requested += 1; return setTimeout(callback, 0); },
    performance: { now: () => Date.now() },
  };
  if (io) {
    extra.IntersectionObserver = class {
      constructor(callback, options) {
        this.callback = callback;
        this.options = options;
        this.targets = [];
        io.push(this);
      }
      observe(el) { this.targets.push(el); }
      unobserve(el) { this.targets = this.targets.filter((target) => target !== el); }
      disconnect() { this.targets = []; }
      fire(entries) { this.callback(entries, this); }
    };
  }
  if (mo) {
    extra.MutationObserver = class {
      constructor(callback) {
        this.callback = callback;
        this.observed = [];
        mo.push(this);
      }
      observe(target, options) { this.observed.push([target, options]); }
      disconnect() { this.observed = []; }
      fire(records) { this.callback(records, this); }
    };
  }
  const env = load(["feel.js"], { extra });
  return { env, frames, Feel: env.window.Feel };
}

function pointer(env, target, x, y) {
  const event = new FakeEvent("pointermove");
  Object.assign(event, { clientX: x, clientY: y, pointerType: "mouse", target });
  env.document.dispatchEvent(event);
}

/* 把元素的 style 换成可记录的替身：setProperty/removeProperty 的调用都记下来。 */
function styled(el) {
  const written = {};
  const removed = [];
  el.style = {
    setProperty: (name, value) => { written[name] = value; },
    removeProperty: (name) => { removed.push(name); delete written[name]; },
  };
  return { written, removed };
}

function tiltCard(env) {
  const card = env.document.createElement("div");
  card.setAttribute("data-tilt", "");
  card.getBoundingClientRect = () => ({ left: 0, top: 0, width: 200, height: 100 });
  env.document.body.append(card);
  return card;
}

test("feel: exposes the public API", () => {
  const { Feel } = setup();
  assert.deepEqual(Object.keys(Feel).sort(), ["countUp", "flip", "reduced", "skeletonHide", "skeletonShow", "tilt", "toast"]);
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

test("feel: tilt lerps toward the pointer target (clamped to ±7°), then stops and cleans up", async () => {
  const { env, frames, Feel } = setup();
  const card = tiltCard(env);
  const { written, removed } = styled(card);
  const cleanup = Feel.tilt(card);
  assert.equal(typeof cleanup, "function");

  pointer(env, card, 200, 100); // 右下角 → ry=+7°, rx=-7°
  await wait(80);
  const rx = parseFloat(written["--rx"]);
  const ry = parseFloat(written["--ry"]);
  assert.ok(Math.abs(rx) <= 7 && Math.abs(ry) <= 7, `clamped: --rx=${written["--rx"]} --ry=${written["--ry"]}`);
  assert.ok(ry > 3, `lerped toward +7°: --ry=${written["--ry"]}`);
  assert.ok(rx < -3, `lerped toward -7°: --rx=${written["--rx"]}`);
  assert.equal(written["--mx"], "200.0px");
  assert.equal(written["--my"], "100.0px");

  pointer(env, env.document.body, 900, 900); // 移出卡片：回到 0
  await wait(1000);
  assert.ok(removed.includes("--rx") && removed.includes("--ry"), `vars cleaned: ${removed.join(",")}`);
  const settledAt = frames.requested;
  await wait(150);
  assert.equal(frames.requested, settledAt, "idle: no more animation frames requested");
  cleanup();
});

test("feel: tilt does nothing when motion is reduced or the device has no hover", async () => {
  for (const options of [{ reduced: true }, { hover: false }]) {
    const { env, frames, Feel } = setup(options);
    const card = tiltCard(env);
    const { written } = styled(card);
    const cleanup = Feel.tilt(card);
    assert.equal(typeof cleanup, "function");
    pointer(env, card, 150, 50);
    await wait(50);
    assert.deepEqual(written, {}, JSON.stringify(options));
    assert.equal(frames.requested, 0, "no frame is even requested");
    cleanup();
  }
});

test("feel: tilt cleanup removes the binding and stops the loop", async () => {
  const { env, frames, Feel } = setup();
  const card = tiltCard(env);
  const { written, removed } = styled(card);
  const cleanup = Feel.tilt(card);
  pointer(env, card, 200, 50);
  await wait(60);
  assert.ok("--rx" in written, "tilt was active");
  cleanup();
  assert.ok(removed.includes("--rx"), "vars removed by cleanup");
  const at = frames.requested;
  pointer(env, card, 200, 50);
  await wait(60);
  assert.equal(frames.requested, at + 1, "pointermove still runs its own frame, but tilt no longer pumps");
  assert.ok(!("--rx" in written), "no tilt writes after cleanup");
});

test("feel: tilt unbinds silently when the card is removed from the DOM", async () => {
  const { env, Feel } = setup();
  const card = tiltCard(env);
  const { written } = styled(card);
  Feel.tilt(card); // 故意不调清理函数：模拟只管接线不管卸载的调用方
  pointer(env, card, 200, 50);
  await wait(60);
  assert.ok("--rx" in written, "tilt was active");
  card.remove();
  assert.ok(!card.isConnected, "card detached");
  pointer(env, env.document.body, 200, 50); // 再来一帧 pump，把 detached 的清理掉
  pointer(env, env.document.body, 10, 10);
  await wait(80);
  const { written: written2 } = styled(card);
  pointer(env, card, 200, 50);
  await wait(60);
  assert.deepEqual(written2, {}, "detached card is unbound: no more tilt writes");
});

test("feel: grow adds .is-grown once when an item first enters the viewport", async () => {
  const ios = [];
  const mos = [];
  const { env } = setup({ io: ios, mo: mos });
  const wrap = env.document.createElement("div");
  wrap.className = "feel-grow";
  const bar = env.document.createElement("div");
  wrap.append(bar);
  env.document.body.append(wrap);
  assert.equal(mos.length, 1, "auto-init observes the document for late nodes");
  mos[0].fire([{ type: "childList", addedNodes: [wrap] }]);
  assert.equal(ios.length, 1, "one IntersectionObserver created");
  assert.ok(ios[0].targets.includes(bar), "bar is observed");
  assert.ok(!bar.classList.contains("is-grown"), "not grown before entering");
  ios[0].fire([{ target: bar, isIntersecting: true }]);
  assert.ok(bar.classList.contains("is-grown"), "grown on first entry");
  assert.ok(!ios[0].targets.includes(bar), "unobserved after the first entry");
  ios[0].fire([{ target: bar, isIntersecting: true }]);
  assert.ok(bar.classList.contains("is-grown"), "still grown: played only once");
});

test("feel: grow applies the final state immediately when motion is reduced", async () => {
  const ios = [];
  const mos = [];
  const { env } = setup({ reduced: true, io: ios, mo: mos });
  const wrap = env.document.createElement("div");
  wrap.className = "feel-grow";
  const bar = env.document.createElement("div");
  wrap.append(bar);
  env.document.body.append(wrap);
  mos[0].fire([{ type: "childList", addedNodes: [wrap] }]);
  assert.ok(bar.classList.contains("is-grown"), "final state without animation");
  assert.equal(ios.length, 0, "no observer needed");
});

test("feel: skeletonShow swaps host content for a skeleton, skeletonHide clears it", () => {
  const { env, Feel } = setup();
  const host = env.document.createElement("div");
  host.textContent = "加载中…";
  env.document.body.append(host);
  const box = Feel.skeletonShow(host, 4);
  assert.equal(box.className, "feel-skeleton");
  assert.equal(box.getAttribute("aria-hidden"), "true");
  assert.equal(box.children.length, 4);
  assert.ok(box.children.every((child) => child.tagName === "I"));
  assert.equal(host.children.length, 1, "host content replaced");
  Feel.skeletonHide(host);
  assert.equal(host.children.length, 0, "host cleared for the caller to re-render");

  const host2 = env.document.createElement("div");
  assert.equal(Feel.skeletonShow(host2).children.length, 3, "default 3 rows");
  assert.equal(Feel.skeletonShow(null), null, "null host is safe");
  Feel.skeletonHide(null); // 不抛错
});

test("feel: auto-init binds tilt and countUp to nodes added later", async () => {
  const mos = [];
  const { env, Feel } = setup({ mo: mos });
  assert.ok(env.document.documentElement.classList.contains("feel-js"), "feel-js marker set at startup");

  const card = tiltCard(env);
  const { written } = styled(card);
  mos[0].fire([{ type: "childList", addedNodes: [card] }]);
  pointer(env, card, 200, 50);
  await wait(60);
  assert.ok("--rx" in written && "--ry" in written, "tilt bound automatically");
  assert.equal(typeof Feel.tilt(card), "function", "re-binding is a safe no-op");

  const num = env.document.createElement("span");
  num.setAttribute("data-countup", "");
  num.textContent = "42";
  env.document.body.append(num);
  mos[0].fire([{ type: "childList", addedNodes: [num] }]);
  assert.equal(num.dataset.feelValue, "42", "countUp picked it up");
});
