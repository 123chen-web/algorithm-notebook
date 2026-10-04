"use strict";

/* 首访开场短片的行为测试（Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器）。
   时间轴用 node:test 的假计时器推进；节拍全部取自 IntroFilm.timing（与 intro-film.js 顶部的 TIMING 表同源）。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { mock } = require("node:test");
const { load, FakeEvent, FakeElement } = require("./js_harness.cjs");

let clock = 0;
test.beforeEach(() => {
  clock = 0;
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 0 });
});
test.afterEach(() => {
  mock.timers.reset();
  restorePrototype();
});

/* ---- 可选的 Web Animations / 布局替身：只有 FLIP 相关的测试才装上 ---- */
const originals = {};
function restorePrototype() {
  for (const [name, value] of Object.entries(originals)) {
    if (value === undefined) delete FakeElement.prototype[name];
    else FakeElement.prototype[name] = value;
    delete originals[name];
  }
}
function patch(name, value) {
  if (!(name in originals)) originals[name] = Object.prototype.hasOwnProperty.call(FakeElement.prototype, name) ? FakeElement.prototype[name] : undefined;
  FakeElement.prototype[name] = value;
}
/** 装上 element.animate / cloneNode，并按类名给出不同的位置，返回记录下来的动画。 */
function installFlip(boxes = {}) {
  const animations = [];
  patch("animate", function animate(keyframes, options) {
    const animation = {
      target: this, keyframes, options, state: "running", onfinish: null,
      pause() { this.state = "paused"; },
      play() { this.state = "running"; },
      cancel() { this.state = "idle"; },
      finish() { this.state = "finished"; this.onfinish?.(); },
    };
    animations.push(animation);
    return animation;
  });
  patch("cloneNode", function cloneNode() {
    const copy = new FakeElement(this.tagName, this.ownerDocument);
    copy.className = this.className;
    return copy;
  });
  patch("getBoundingClientRect", function getBoundingClientRect() {
    const key = Object.keys(boxes).find((name) => this.classList.contains(name));
    const box = key ? boxes[key] : { left: 0, top: 0, width: 100, height: 20 };
    return { ...box, right: box.left + box.width, bottom: box.top + box.height };
  });
  return animations;
}

/** 装好假页面：欢迎页、主标题、被遮住的页面容器，再加载开场脚本。 */
function setup({ motion = true, search = "", hash = "", storage, files = ["intro-glyphs.js", "intro-film.js"], before } = {}) {
  const motionListeners = [];
  const media = { motion };
  const extra = {
    performance: { now: () => Date.now() },
    location: { search, hash },
    matchMedia: (query) => ({
      get matches() { return /no-preference/.test(query) ? media.motion : !media.motion; },
      addEventListener: (_type, listener) => motionListeners.push(listener),
      removeEventListener: (_type, listener) => {
        const index = motionListeners.indexOf(listener);
        if (index >= 0) motionListeners.splice(index, 1);
      },
    }),
  };
  if (storage) extra.localStorage = storage;
  const env = load(files, { extra });
  const { document } = env;
  before?.(env);
  const container = document.querySelector("#page-container");
  const intro = document.querySelector("#intro");
  const title = document.querySelector("#intro-title");
  intro.append(title);
  container.append(intro);
  const film = () => document.documentElement.querySelector("#intro-film");
  const byId = (id) => document.documentElement.querySelector(`#${id}`);
  const section = (number) => film().querySelector(`.film-act-${number}`);
  const key = (name, props = {}) => {
    const event = new FakeEvent("keydown", { props: { key: name, ...props } });
    document.dispatchEvent(event);
    return event;
  };
  // 点击会冒泡到 document（开场在 document 上监听“点一下屏幕”）。
  const tap = (target) => {
    const event = new FakeEvent("click", { bubbles: true });
    target.dispatchEvent(event);
    document.dispatchEvent(event);
    return event;
  };
  const changeMotion = (value) => {
    media.motion = value;
    for (const listener of [...motionListeners]) listener({ matches: value });
  };
  // 比较焦点时用 id：断言失败时 Node 会把整棵假 DOM（有环）打印出来，又慢又占内存。
  const focused = () => {
    const active = document.activeElement;
    return active ? active.id || active.tagName.toLowerCase() : null;
  };
  const T = env.window.IntroFilm.timing;
  const cue = (kind, act) => T.cues.find((item) => item.kind === kind && (act === undefined || item.act === act)).at;
  return { ...env, T, cue, focused, IntroFilm: env.window.IntroFilm, container, intro, title, film, byId, section, key, tap, changeMotion, motionListeners };
}

/** 像真实时间一样一小步一小步推进：一次 tick 跨太大时，回调里读到的“现在”会是终点时刻。 */
function advance(ms) {
  for (let left = ms; left > 0; left -= 50) mock.timers.tick(Math.min(50, left));
  clock += Math.max(0, ms);
}
const advanceTo = (at) => advance(at - clock);
const has = (node, name) => node.classList.contains(name);
const hanzi = (text) => (text.match(/\p{Script=Han}/gu) || []).length;

/* ---------------- 时间表本身 ---------------- */
test("timing table: one named table drives a 16–20 s film with reading-length holds and 150–300 ms overlaps", () => {
  const { T } = setup();
  assert.equal(T.acts.length, 5);
  assert.ok(T.total >= 16000 && T.total <= 20000, `total ${T.total} ms`);
  assert.ok(T.cues.find((item) => item.kind === "cta").at <= 20000);
  assert.equal(T.easeOut, "cubic-bezier(0.22, 1, 0.36, 1)");
  assert.equal(T.easeMove, "cubic-bezier(0.4, 0, 0.2, 1)");
  assert.ok(T.stagger >= 25 && T.stagger <= 40, "letters and lines are staggered 25–40 ms");
  assert.ok(T.acts[0].beats.lead <= 400, "the opening black frame lasts at most 400 ms");
  assert.ok(T.overlap >= 150 && T.overlap <= 300);
  const lines = [T.lines[0], T.lines[1], T.lines[2], T.lines[3], "把每一次出错，变成下一次的把握。"];
  T.acts.forEach((act, index) => {
    const count = hanzi(lines[index]);
    const [shortest, longest] = [count / 8 * 1000 + 300, count / 6 * 1000 + 300];
    assert.ok(act.hold >= shortest - 1 && act.hold <= longest + 1,
      `act ${index + 1}: hold ${act.hold} ms for ${count} hanzi, expected ${Math.round(shortest)}–${Math.round(longest)}`);
  });
  for (let number = 1; number < T.acts.length; number += 1) {
    const both = T.cues.find((item) => item.kind === "leave" && item.act === number).at + T.acts[number - 1].exit
      - T.cues.find((item) => item.kind === "enter" && item.act === number + 1).at;
    assert.ok(both >= 150 && both <= 300, `act ${number} → ${number + 1} overlap ${both} ms`);
  }
});

test("first visit: landing on the welcome page plays the film as a focused modal dialog", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  assert.ok(root, "the film is mounted");
  assert.equal(root.getAttribute("role"), "dialog");
  assert.equal(root.getAttribute("aria-modal"), "true");
  assert.equal(root.getAttribute("aria-labelledby"), "intro-film-title");
  assert.equal(root.dataset.act, "1");
  assert.equal(has(page.section(1), "is-on"), true);
  assert.equal(page.focused(), "intro-film-skip", "focus starts on Skip");
  assert.equal(page.container.inert, true, "the covered page is inert");
  assert.equal(page.document.documentElement.classList.contains("intro-film-open"), true);
  assert.equal(page.window.localStorage.getItem("oy-intro-film-seen"), "1");
  assert.equal(page.IntroFilm.ownsOpening(), true, "the old one-off welcome animation stands down");
  advance(600);
  assert.equal(page.byId("intro-film-live").textContent, "你做过的题，大多数会忘。", "the first line reaches screen readers");
});

test("timeline: acts follow the table and Continue appears within 20 s", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  for (let number = 2; number <= 5; number += 1) {
    const at = page.cue("enter", number);
    advanceTo(at - 50);
    assert.equal(root.dataset.act, String(number - 1), `still act ${number - 1} just before ${at} ms`);
    advanceTo(at);
    assert.equal(root.dataset.act, String(number));
    assert.equal(has(page.section(number), "is-on"), true);
    assert.equal(page.byId("intro-film-live").textContent, page.T.lines[number - 1]);
  }
  const cta = page.cue("cta");
  advanceTo(cta - 50);
  assert.equal(root.querySelector(".film-end").hidden, true, "no buttons before the halfway point of the last act");
  advanceTo(cta);
  assert.ok(cta <= 20000);
  assert.equal(root.querySelector(".film-end").hidden, false);
  assert.equal(page.focused(), "intro-film-continue", "focus moves to Continue");
  assert.equal(has(root, "is-ended"), false, "Continue is usable while the last act is still animating");
  page.byId("intro-film-continue").click();
  assert.ok(!page.film(), "Continue closes the film mid-animation");
  assert.equal(page.focused(), "intro-title", "focus returns to the welcome page's main heading");
  assert.equal(page.container.inert, false);
  advance(30000);
  assert.ok(!page.film(), "no timer survives closing");
});

test("no dead frames: some act is on screen at every moment and each hand-off overlaps", () => {
  const page = setup();
  const { T } = page;
  page.IntroFilm.route("welcome");
  let lastVisible = 0;
  for (let at = 0; at <= T.total; at += 50) {
    advanceTo(at);
    const visible = [1, 2, 3, 4, 5].filter((number) => {
      const node = page.section(number);
      if (has(node, "is-on")) return true;
      if (!has(node, "is-leaving")) return false;
      return at - page.cue("leave", number) < T.acts[number - 1].exit; // 退出动画还在播
    });
    if (visible.length) lastVisible = at;
    assert.ok(at - lastVisible <= 100, `nothing on screen for ${at - lastVisible} ms at ${at} ms`);
    for (let number = 2; number <= 5; number += 1) {
      if (at !== page.cue("enter", number)) continue;
      assert.equal(has(page.section(number - 1), "is-leaving"), true, `act ${number - 1} is still leaving when ${number} enters`);
      const remaining = page.cue("leave", number - 1) + T.acts[number - 2].exit - at;
      assert.ok(remaining >= 150, `act ${number - 1} keeps ${remaining} ms of exit after act ${number} starts`);
    }
  }
});

for (const [label, skip] of [
  ["the Skip button", (page) => page.byId("intro-film-skip").click()],
  ["Escape", (page) => page.key("Escape")],
  ["the space bar", (page) => page.key(" ")],
]) {
  test(`skip: ${label} closes the whole film at any point and leads to the same welcome flow`, () => {
    const page = setup();
    page.IntroFilm.route("welcome");
    advanceTo(page.cue("enter", 3) + 500);
    assert.equal(page.film().dataset.act, "3");
    skip(page);
    assert.ok(!page.film());
    assert.equal(page.IntroFilm.isOpen(), false);
    assert.equal(page.focused(), "intro-title");
    assert.equal(page.container.inert, false);
    advance(30000);
    assert.ok(!page.film());
  });
}

for (const [label, step] of [
  ["→", (page) => page.key("ArrowRight")],
  ["a tap on the screen", (page) => page.tap(page.section(Number(page.film().dataset.act)))],
]) {
  test(`next act: ${label} starts the next act immediately and the timeline carries on from there`, () => {
    const page = setup();
    page.IntroFilm.route("welcome");
    advanceTo(500);
    step(page);
    assert.equal(page.film().dataset.act, "2");
    assert.equal(has(page.section(2), "is-on"), true);
    assert.equal(has(page.section(1), "is-leaving"), true, "the jump still overlaps the outgoing act");
    advance(page.cue("enter", 3) - page.cue("enter", 2) - 50);
    assert.equal(page.film().dataset.act, "2", "the rest of act 2 keeps its full length");
    advance(50);
    assert.equal(page.film().dataset.act, "3");
    step(page);
    step(page);
    assert.equal(page.film().dataset.act, "5");
    assert.equal(page.film().querySelector(".film-end").hidden, true);
    step(page);
    assert.equal(page.film().querySelector(".film-end").hidden, false, "from the last act it jumps straight to Continue");
    assert.equal(page.focused(), "intro-film-continue");
    step(page);
    assert.ok(page.film(), "after Continue appears, further taps or → do nothing");
  });
}

test("next act: taps on buttons or the transcript are not treated as 'next', and space is left to the buttons", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  advanceTo(page.cue("enter", 2) + 100);
  page.tap(page.film().querySelector("summary"));
  assert.equal(page.film().dataset.act, "2", "a tap on a control is not a tap on the screen");
  advanceTo(page.cue("cta"));
  const space = page.key(" ");
  assert.equal(space.defaultPrevented, false);
  assert.ok(page.film(), "space on the final frame does not dismiss it");
  page.key("Escape");
  assert.ok(!page.film());
});

test("storage: a throwing localStorage is treated as a first visit without errors", () => {
  const failing = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  const page = setup({ storage: failing });
  assert.doesNotThrow(() => page.IntroFilm.route("welcome"));
  assert.ok(page.film(), "plays as a first visit");
  assert.doesNotThrow(() => page.key("Escape"));
  assert.ok(!page.film());
  page.IntroFilm.route("welcome");
  assert.ok(!page.film(), "but still only once per page load");
});

test("storage: a localStorage accessor that throws on access is also tolerated", () => {
  const page = setup();
  Object.defineProperty(page.window, "localStorage", { get() { throw new Error("SecurityError"); } });
  assert.doesNotThrow(() => page.IntroFilm.route("welcome"));
  assert.ok(page.film());
});

test("returning visitor: once recorded the film does not autoplay and the old opening keeps its turn", () => {
  const page = setup();
  page.window.localStorage.setItem("oy-intro-film-seen", "1");
  page.IntroFilm.route("welcome");
  assert.ok(!page.film());
  assert.equal(page.IntroFilm.ownsOpening(), false);
});

test("replay: the footer link plays again, Watch again restarts, and focus returns to the link", () => {
  const page = setup();
  page.window.localStorage.setItem("oy-intro-film-seen", "1");
  page.IntroFilm.route("welcome");
  const link = page.byId("intro-film-replay");
  link.focus();
  link.click();
  assert.ok(page.film(), "the replay link opens the film");
  assert.equal(page.film().dataset.act, "1");
  assert.equal(page.focused(), "intro-film-skip");
  advanceTo(page.cue("cta"));
  page.byId("intro-film-again").click();
  assert.equal(page.film().dataset.act, "1", "Watch again restarts from act 1");
  assert.equal(page.film().querySelector(".film-end").hidden, true);
  assert.equal(page.container.inert, true, "the page stays inert across the restart");
  assert.equal(page.focused(), "intro-film-skip");
  advance(page.cue("enter", 2));
  assert.equal(page.film().dataset.act, "2", "the restarted timeline runs");
  page.key("Escape");
  assert.ok(!page.film());
  assert.equal(page.focused(), "intro-film-replay", "focus goes back to the replay link");
  assert.equal(page.container.inert, false);
});

test("reduced motion: the complete brand frame with Continue is shown and nothing is scheduled", () => {
  const animations = installFlip();
  const page = setup({ motion: false });
  page.IntroFilm.route("welcome");
  const root = page.film();
  assert.equal(has(root, "is-static"), true);
  assert.equal(root.dataset.act, "5");
  const on = root.querySelectorAll(".film-act.is-on").map((node) => node.dataset.act);
  assert.deepEqual([...on], ["5"]);
  assert.equal(root.querySelector(".film-end").hidden, false);
  assert.equal(page.byId("intro-film-again").hidden, true, "nothing to watch again without motion");
  assert.ok(root.querySelector(".film-glyphs .film-ink path"), "the full wordmark is drawn, not written stroke by stroke");
  assert.equal(page.focused(), "intro-film-skip");
  page.key("ArrowRight");
  advance(30000);
  assert.equal(root.dataset.act, "5");
  assert.equal(animations.length, 0, "no transitions run");
  page.byId("intro-film-continue").click();
  assert.ok(!page.film());
  assert.equal(page.focused(), "intro-title");
});

test("reduced motion switched on mid-film freezes on the static frame and drops in-flight transitions", () => {
  const animations = installFlip();
  const page = setup();
  page.IntroFilm.route("welcome");
  advanceTo(page.cue("leave", 2) + 100);
  assert.ok(animations.length > 0);
  page.changeMotion(false);
  const root = page.film();
  assert.equal(has(root, "is-static"), true);
  assert.equal(root.dataset.act, "5");
  assert.equal(root.querySelectorAll(".film-ghost").length, 0);
  assert.equal(root.querySelectorAll(".is-flown").length + root.querySelectorAll(".is-landing").length, 0);
  assert.ok(animations.every((animation) => animation.state === "idle"));
  page.key("Escape");
  assert.equal(page.motionListeners.length, 0, "the media listener is removed on close");
});

for (const [label, options, view] of [
  ["a password-reset link", { search: "?reset_token=abc" }, "welcome"],
  ["a direct #/auth visit", { hash: "#/auth" }, "welcome"],
  ["the auth route", {}, "auth"],
  ["a signed-in user", {}, "app"],
]) {
  test(`no autoplay: ${label}`, () => {
    const page = setup(options);
    page.IntroFilm.route(view);
    assert.ok(!page.film());
    page.IntroFilm.route("welcome"); // 之后再回到欢迎页（例如登出）也不补放
    assert.ok(!page.film());
    assert.equal(page.window.localStorage.getItem("oy-intro-film-seen"), null);
  });
}

test("background tab: the timeline and in-flight transitions pause while hidden and resume where they stopped", () => {
  const animations = installFlip();
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  const at = page.cue("enter", 3) + 100; // 曲线正飞向批注线
  advanceTo(at);
  page.document.hidden = true;
  page.document.dispatchEvent(new FakeEvent("visibilitychange"));
  assert.equal(has(root, "is-paused"), true, "CSS animations are paused too");
  assert.ok(animations.length && animations.every((animation) => animation.state === "paused"), "shared-element flights pause");
  advance(120000);
  assert.equal(root.dataset.act, "3", "nothing advances in the background");
  page.key("ArrowRight");
  assert.equal(root.dataset.act, "3", "→ waits until the tab is visible again");
  page.document.hidden = false;
  page.document.dispatchEvent(new FakeEvent("visibilitychange"));
  assert.equal(has(root, "is-paused"), false);
  assert.ok(animations.every((animation) => animation.state === "running"));
  const rest = page.cue("enter", 4) - at;
  advance(rest - 50);
  assert.equal(root.dataset.act, "3");
  advance(50);
  assert.equal(root.dataset.act, "4", "resumes exactly where it stopped");
});

test("background tab: a film opened in a hidden tab starts paused", () => {
  const page = setup();
  page.document.hidden = true;
  page.IntroFilm.route("welcome");
  advance(60000);
  assert.equal(page.film().dataset.act, "1");
  page.document.hidden = false;
  page.document.dispatchEvent(new FakeEvent("visibilitychange"));
  advance(page.cue("enter", 2));
  assert.equal(page.film().dataset.act, "2");
});

test("FLIP: each hand-off flies a shared element from its old box to its new one, even where View Transitions exist", () => {
  const animations = installFlip({
    "film-curve-svg": { left: 100, top: 100, width: 300, height: 150 },
    "film-mark": { left: 120, top: 400, width: 160, height: 10 },
  });
  let transitions = 0;
  const page = setup({ before: (env) => { env.document.startViewTransition = () => { transitions += 1; }; } });
  page.IntroFilm.route("welcome");
  const root = page.film();
  advanceTo(page.cue("leave", 2));
  assert.equal(transitions, 0, "document.startViewTransition is never used");
  const ghosts = root.querySelectorAll(".film-ghost");
  assert.equal(ghosts.length, 2, "a ghost for the outgoing and one for the incoming element");
  assert.equal(has(root.querySelector(".film-curve-svg"), "is-flown"), true, "the forgetting curve flies…");
  assert.equal(has(root.querySelector(".film-mark"), "is-landing"), true, "…into the red annotation line");
  const [out, into] = animations;
  assert.equal(out.options.duration, page.T.morph);
  assert.equal(out.options.easing, page.T.easeMove);
  // 中心 (250, 175) → (200, 405)，收拢成 160×10。
  assert.equal(out.keyframes[out.keyframes.length - 1].transform, `translate(-50px, 230px) scale(${160 / 300}, ${10 / 150})`);
  assert.equal(into.keyframes[into.keyframes.length - 1].transform, "none");
  advanceTo(page.cue("leave", 3));
  assert.equal(has(root.querySelector(".film-circle"), "is-flown"), true, "the red circle shrinks into…");
  assert.equal(has(root.querySelector(".film-dot-first"), "is-landing"), true, "…the first lit dot of the timeline");
  advanceTo(page.cue("leave", 4));
  assert.equal(has(root.querySelector(".film-track-line"), "is-flown"), true, "the timeline contracts into…");
  assert.equal(has(root.querySelector(".film-ring"), "is-landing"), true, "…the seal's impact ring");
  const track = animations[animations.length - 2];
  assert.match(track.keyframes[track.keyframes.length - 1].transform, /scale\([^,]+, 1\)$/, "the timeline keeps its line weight while contracting");
  for (const animation of animations) {
    assert.equal(animation.options.easing, page.T.easeMove);
    for (const frame of animation.keyframes) {
      assert.deepEqual(Object.keys(frame).filter((name) => !["transform", "opacity", "offset"].includes(name)), [], "only transform and opacity move");
      assert.doesNotMatch(String(frame.transform || ""), /rotate/, "no rotation");
    }
  }
});

test("FLIP: ghosts are removed once landed, and a jump finishes in-flight transitions first", () => {
  const animations = installFlip();
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  advanceTo(page.cue("leave", 2) + 100);
  assert.equal(animations.length, 2);
  animations[1].finish();
  assert.equal(root.querySelectorAll(".film-ghost").length, 0, "ghosts are removed once landed");
  assert.equal(has(root.querySelector(".film-mark"), "is-landing"), false, "the real element takes over");
  advanceTo(page.cue("leave", 3) + 100);
  assert.equal(animations.length, 4);
  page.key("ArrowRight");
  assert.ok(animations.slice(2).every((animation) => animation.state === "finished"), "the jump lands the circle first");
  assert.equal(has(root.querySelector(".film-dot-first"), "is-landing"), false);
});

test("fallback: without the Web Animations API the acts still switch, with no ghosts and no errors", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  advanceTo(page.cue("enter", 5));
  const root = page.film();
  assert.equal(root.dataset.act, "5");
  assert.equal(root.querySelectorAll(".film-ghost").length, 0);
  assert.equal(root.querySelectorAll(".is-flown").length + root.querySelectorAll(".is-landing").length, 0);
});

test("focus: Tab cycles inside the dialog and stray focus is pulled back", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const skip = page.byId("intro-film-skip");
  const tab = page.key("Tab");
  assert.equal(tab.defaultPrevented, true);
  assert.equal(page.focused(), "intro-film-skip", "Skip is the only stop while playing");
  advanceTo(page.cue("cta"));
  const order = ["intro-film-skip", "intro-film-continue", "intro-film-again", "summary"];
  skip.focus();
  const forward = [];
  for (let index = 0; index < order.length; index += 1) {
    page.key("Tab");
    forward.push(page.focused());
  }
  assert.deepEqual(forward, [...order.slice(1), order[0]], "Tab wraps around");
  page.key("Tab", { shiftKey: true });
  assert.equal(page.focused(), "summary", "Shift+Tab wraps backwards");
  const outside = page.document.createElement("button");
  page.container.append(outside);
  page.document.dispatchEvent(new FakeEvent("focusin", { props: { target: outside } }));
  assert.equal(page.focused(), "intro-film-skip", "focus cannot escape to the covered page");
});

test("routing away while the film is open closes it without stealing focus", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  page.IntroFilm.route("welcome"); // 重复渲染同一路由不会重开
  assert.equal(page.document.documentElement.querySelectorAll("#intro-film").length, 1);
  page.intro.hidden = true;
  page.IntroFilm.route("auth");
  assert.ok(!page.film());
  assert.notEqual(page.focused(), "intro-title");
  assert.equal(page.container.inert, false);
});

test("brand: the written wordmark is an aria-hidden SVG of every contour behind a real-text label", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  const brand = root.querySelector(".film-brand");
  assert.equal(brand.getAttribute("role"), "img");
  assert.equal(brand.getAttribute("aria-label"), "欧叶OY", "screen readers read the real text");
  const glyphs = brand.querySelector(".film-glyphs");
  assert.equal(glyphs.getAttribute("aria-hidden"), "true");
  const contours = page.window.IntroGlyphs.glyphs.reduce((count, glyph) => count + glyph.contours.length, 0);
  assert.equal(glyphs.querySelectorAll(".film-strokes path").length, contours * 2, "a thin and a pressed stroke per contour");
  assert.equal(glyphs.querySelectorAll(".film-ink path").length, 1, "one ink fill");
  assert.equal(glyphs.querySelector(".film-ink").getAttribute("clip-path"), "url(#intro-film-ink)");
  advanceTo(page.T.total);
  assert.equal(brand.getAttribute("aria-label"), "欧叶OY", "still readable after the animation");
});

test("brand: without the glyph data the wordmark falls back to plain text", () => {
  const page = setup({ files: ["intro-film.js"] });
  page.IntroFilm.route("welcome");
  const brand = page.film().querySelector(".film-brand");
  assert.equal(brand.getAttribute("aria-label"), "欧叶OY");
  assert.equal(brand.querySelector(".film-brand-text").textContent, "欧叶OY");
});

test("text is written with textContent and the transcript lists every line", () => {
  const page = setup({ motion: false });
  page.IntroFilm.route("welcome");
  const items = page.film().querySelectorAll(".film-transcript li").map((node) => node.textContent);
  assert.deepEqual([...items], [
    "你做过的题，大多数会忘。",
    "忘记不是你不努力，是大脑的默认设置。",
    "真正值钱的，不是答案，是你当时怎么想、错在哪一步。",
    "记下来，然后在快忘的时候再见一次。",
    "欧叶OY：把每一次出错，变成下一次的把握。",
  ]);
});
