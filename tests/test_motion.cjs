// Run with: node --test tests/test_motion.cjs (no packages or browser required).
const test = require("node:test");
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const appSource = readFileSync(path.join(__dirname, "../static/app.js"), "utf8");
const effectStart = appSource.indexOf("function initMarbleBackground()");
assert.notEqual(effectStart, -1, "The decorative effect must have an isolated initializer");
// Only the independent decoration is executed: application bootstrap and API calls
// are intentionally outside this harness.
const effectSource = appSource.slice(effectStart);

function setup({ reducedMotion = false, finePointer = true, hidden = false,
  contextAvailable = true } = {}) {
  let registrations = 0;
  class EventTarget {
    constructor() { this.listeners = new Map(); }
    addEventListener(type, callback) {
      registrations += 1;
      if (!this.listeners.has(type)) this.listeners.set(type, new Set());
      this.listeners.get(type).add(callback);
    }
    removeEventListener(type, callback) {
      this.listeners.get(type)?.delete(callback);
    }
    dispatch(type, event = {}) {
      for (const listener of [...(this.listeners.get(type) || [])]) {
        listener.call(this, { type, target: this, ...event });
      }
    }
    listenerCount(type) {
      if (type) return this.listeners.get(type)?.size || 0;
      return [...this.listeners.values()].reduce((count, set) => count + set.size, 0);
    }
  }

  class MediaQuery extends EventTarget {
    constructor(matches) { super(); this.matches = matches; }
    set(matches) {
      this.matches = matches;
      this.dispatch("change", { matches });
    }
  }

  const reduce = new MediaQuery(reducedMotion);
  const fine = new MediaQuery(finePointer);
  const queries = [];
  const elements = [];
  const attached = [];
  let drawCalls = 0;
  const frames = new Map();
  let nextFrame = 1;
  let frameRequests = 0;
  const drawingContext = () => ({
    createRadialGradient() { return { addColorStop() {} }; },
    clearRect() {}, setTransform() {}, save() {}, restore() {},
    translate() {}, rotate() {}, scale() {}, beginPath() {}, closePath() {},
    moveTo() {}, lineTo() {}, bezierCurveTo() {}, arc() {},
    fill() {}, stroke() {}, fillRect() {},
    drawImage() { drawCalls += 1; },
    // 大理石纹理用逐像素 ImageData 生成，这里只需要形状正确、不用真的算颜色。
    createImageData(w, h) { return { width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }; },
    putImageData() {},
  });
  const createElement = (tagName) => {
    assert.equal(tagName, "canvas", "Decoration should create canvas elements only");
    const attributes = new Map();
    const element = {
      tagName: "CANVAS", className: "", width: 0, height: 0, style: {},
      setAttribute(name, value) { attributes.set(name, String(value)); },
      getAttribute(name) { return attributes.get(name) ?? null; },
      getContext(type) {
        assert.equal(type, "2d");
        return contextAvailable ? drawingContext() : null;
      },
      remove() {
        const index = attached.indexOf(this);
        if (index >= 0) attached.splice(index, 1);
      },
    };
    elements.push(element);
    return element;
  };
  const body = {
    appendChild(element) { attached.push(element); return element; },
    prepend(element) { attached.unshift(element); },
  };
  const document = Object.assign(new EventTarget(), {
    hidden, visibilityState: hidden ? "hidden" : "visible", body,
    documentElement: { clientWidth: 1440, clientHeight: 900 }, createElement,
  });
  const requestAnimationFrame = (callback) => {
    const id = nextFrame++;
    frameRequests += 1;
    frames.set(id, callback);
    return id;
  };
  const cancelAnimationFrame = (id) => { frames.delete(id); };
  const matchMedia = (query) => {
    queries.push(query);
    if (query === "(prefers-reduced-motion: reduce)") return reduce;
    if (query === "(pointer: fine)") return fine;
    throw new Error(`Unexpected media query: ${query}`);
  };
  const window = Object.assign(new EventTarget(), {
    innerWidth: 1440, innerHeight: 900, devicePixelRatio: 2,
    document, matchMedia, requestAnimationFrame, cancelAnimationFrame,
  });
  const sandbox = {
    window, document, matchMedia, requestAnimationFrame, cancelAnimationFrame,
    performance: { now: () => 0 }, console,
  };
  vm.runInNewContext(effectSource, sandbox, { filename: "app.js (decoration)", timeout: 1000 });

  return {
    window, document, reduce, fine, elements, attached, frames, queries,
    get registrations() { return registrations; },
    get frameRequests() { return frameRequests; },
    get drawCalls() { return drawCalls; },
    allListenerCount() {
      return [window, document, reduce, fine].reduce((sum, target) => sum + target.listenerCount(), 0);
    },
    inputListenerCount() {
      return [window, document].reduce((sum, target) =>
        sum + target.listenerCount("pointermove") + target.listenerCount("resize"), 0);
    },
    visibility(value) {
      document.hidden = value;
      document.visibilityState = value ? "hidden" : "visible";
      document.dispatch("visibilitychange");
    },
    frame(time) {
      const callbacks = [...frames.values()];
      frames.clear();
      for (const callback of callbacks) callback(time);
    },
  };
}

for (const preference of [
  { reducedMotion: true, finePointer: true },
  { reducedMotion: false, finePointer: false },
  { reducedMotion: true, finePointer: false },
]) {
  test(`initial preferences skip all decoration work: ${JSON.stringify(preference)}`, () => {
    const env = setup(preference);
    assert.deepEqual(env.queries.sort(), ["(pointer: fine)", "(prefers-reduced-motion: reduce)"].sort());
    assert.equal(env.elements.length, 0);
    assert.equal(env.registrations, 0);
    assert.equal(env.frameRequests, 0);
    env.reduce.set(false);
    env.fine.set(true);
    assert.equal(env.elements.length, 0, "Skipped sessions must remain free of background work");
  });
}

test("eligible desktop gets one decorative canvas and a single animation loop", () => {
  const env = setup();
  assert.equal(env.attached.length, 1);
  assert.equal(env.attached[0].className, "marble-background");
  assert.equal(env.attached[0].getAttribute("aria-hidden"), "true");
  assert.equal(env.frames.size, 1);
  assert.ok(env.inputListenerCount() > 0);
  const initialDrawCalls = env.drawCalls;
  env.window.dispatch("pointermove", { pointerType: "mouse", clientX: 600, clientY: 400 });
  env.frame(100);
  env.frame(200);
  assert.ok(env.drawCalls > initialDrawCalls, "The scheduled loop actually draws the decoration");
  assert.equal(env.frames.size, 1);
});

test("hidden pages release input listeners and RAF, then resume without duplicate loops", () => {
  const env = setup();
  env.visibility(true);
  assert.equal(env.frames.size, 0);
  assert.equal(env.inputListenerCount(), 0);
  const requestsWhenHidden = env.frameRequests;
  env.window.dispatch("pointermove", { clientX: 200, clientY: 100 });
  env.window.dispatch("resize");
  assert.equal(env.frameRequests, requestsWhenHidden);
  env.visibility(false);
  const inputListeners = env.inputListenerCount();
  assert.ok(inputListeners > 0);
  assert.equal(env.frames.size, 1);
  env.visibility(false);
  env.window.dispatch("pageshow", { persisted: true });
  assert.equal(env.frames.size, 1);
  assert.equal(env.inputListenerCount(), inputListeners);
});

test("an initially hidden page starts no animation until shown", () => {
  const env = setup({ hidden: true });
  assert.equal(env.elements.length, 0);
  assert.equal(env.frames.size, 0);
  assert.equal(env.inputListenerCount(), 0);
  env.visibility(false);
  assert.equal(env.frames.size, 1);
});

for (const preference of ["reduce", "fine"]) {
  test(`changing ${preference} preference completely destroys the effect`, () => {
    const env = setup();
    env[preference].set(preference === "reduce");
    assert.equal(env.attached.length, 0);
    assert.equal(env.frames.size, 0);
    assert.equal(env.allListenerCount(), 0);
    env[preference].set(preference !== "reduce");
    env.visibility(false);
    env.window.dispatch("pageshow", { persisted: true });
    assert.equal(env.attached.length, 0);
    assert.equal(env.frames.size, 0);
  });
}

test("BFCache suspends and restores; leaving the document destroys every resource", () => {
  const env = setup();
  env.window.dispatch("pagehide", { persisted: true });
  assert.equal(env.frames.size, 0);
  assert.equal(env.inputListenerCount(), 0);
  assert.equal(env.attached.length, 1);
  env.window.dispatch("pageshow", { persisted: true });
  assert.equal(env.frames.size, 1);
  assert.ok(env.inputListenerCount() > 0);
  env.window.dispatch("pagehide", { persisted: false });
  assert.equal(env.attached.length, 0);
  assert.equal(env.frames.size, 0);
  assert.equal(env.allListenerCount(), 0);
});

test("unavailable Canvas 2D exits cleanly without affecting application startup", () => {
  const env = setup({ contextAvailable: false });
  assert.equal(env.attached.length, 0);
  assert.equal(env.allListenerCount(), 0);
  assert.equal(env.frameRequests, 0);
});

test("high DPI and large viewports keep the displayed canvas within its pixel budget", () => {
  const env = setup();
  const canvas = env.attached[0];
  env.window.devicePixelRatio = 4;
  for (const [width, height, time] of [[8000, 2000, 100], [2000, 8000, 200], [320, 240, 300]]) {
    env.window.innerWidth = width;
    env.window.innerHeight = height;
    const beforeResize = env.drawCalls;
    env.window.dispatch("resize");
    assert.equal(env.drawCalls, beforeResize, "Resize defers work to the animation frame");
    env.frame(time);
    assert.ok(canvas.width > 0 && canvas.width <= 960);
    assert.ok(canvas.height > 0 && canvas.height <= 720);
    assert.ok(canvas.width <= width && canvas.height <= height);
    assert.ok(Math.abs(canvas.width / canvas.height - width / height) < .01);
    assert.equal(env.frames.size, 1);
  }
});

test("painting is capped at 30 fps even when RAF runs more frequently", () => {
  const env = setup();
  env.frame(100);
  const firstFrameDraws = env.drawCalls;
  env.frame(116);
  env.frame(132);
  assert.equal(env.drawCalls, firstFrameDraws);
  assert.equal(env.frames.size, 1);
  env.frame(134);
  assert.ok(env.drawCalls > firstFrameDraws);
});

for (const pointerType of ["mouse", "pen"]) {
  test(`${pointerType} activates the following glow; touch and input bursts do not cause extra frames`, () => {
    const env = setup();
    const initialDraws = env.drawCalls;
    env.frame(100);
    const ambientDrawsPerFrame = env.drawCalls - initialDraws;
    const beforeTouch = env.drawCalls;
    env.window.dispatch("pointermove", { pointerType: "touch", clientX: 300, clientY: 400 });
    assert.equal(env.drawCalls, beforeTouch);
    env.frame(140);
    assert.equal(env.drawCalls - beforeTouch, ambientDrawsPerFrame, "Touch must not activate the glow");

    const beforeInput = env.drawCalls;
    const requestsBeforeInput = env.frameRequests;
    for (let i = 0; i < 20; i += 1) {
      env.window.dispatch("pointermove", { pointerType, clientX: 500 + i, clientY: 400 });
    }
    assert.equal(env.drawCalls, beforeInput, "Input only updates the pointer target");
    assert.equal(env.frameRequests, requestsBeforeInput, "Input reuses the existing animation loop");
    env.frame(180);
    assert.ok(env.drawCalls - beforeInput > ambientDrawsPerFrame, "Fine pointer input paints a following glow");
    assert.equal(env.frames.size, 1);
  });
}
