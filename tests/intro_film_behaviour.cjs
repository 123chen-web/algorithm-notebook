"use strict";

/* 首访开场短片的行为测试（Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器）。
   时间轴用 node:test 的假计时器推进，不真的等 30 秒。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { mock } = require("node:test");
const { load, FakeEvent } = require("./js_harness.cjs");

const CUES = [0, 5200, 11400, 18800, 25600, 30400];

test.beforeEach(() => mock.timers.enable({ apis: ["setTimeout", "Date"], now: 0 }));
test.afterEach(() => mock.timers.reset());

/** 装好假页面：欢迎页、主标题、被遮住的页面容器，再加载 intro-film.js。 */
function setup({ motion = true, search = "", hash = "", storage } = {}) {
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
  const env = load(["intro-film.js"], { extra });
  const { document } = env;
  const container = document.querySelector("#page-container");
  const intro = document.querySelector("#intro");
  const title = document.querySelector("#intro-title");
  intro.append(title);
  container.append(intro);
  const film = () => document.documentElement.querySelector("#intro-film");
  const byId = (id) => document.documentElement.querySelector(`#${id}`);
  const key = (name, props = {}) => {
    const event = new FakeEvent("keydown", { props: { key: name, ...props } });
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
  return { ...env, focused, IntroFilm: env.window.IntroFilm, container, intro, title, film, byId, key, changeMotion, motionListeners };
}

/** 像真实时间一样一小步一小步推进：一次 tick 跨太大时，回调里读到的“现在”会是终点时刻。 */
function advance(ms) {
  for (let left = ms; left > 0; left -= 100) mock.timers.tick(Math.min(100, left));
}
const advanceTo = (from, to) => advance(to - from);

test("first visit: landing on the welcome page plays the film as a focused modal dialog", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  assert.ok(root, "the film is mounted");
  assert.equal(root.getAttribute("role"), "dialog");
  assert.equal(root.getAttribute("aria-modal"), "true");
  assert.equal(root.getAttribute("aria-labelledby"), "intro-film-title");
  assert.equal(root.dataset.act, "1");
  assert.equal(root.classList.contains("is-static"), false);
  assert.equal(page.focused(), "intro-film-skip", "focus starts on Skip");
  assert.equal(page.container.inert, true, "the covered page is inert");
  assert.equal(page.document.documentElement.classList.contains("intro-film-open"), true);
  assert.equal(page.window.localStorage.getItem("oy-intro-film-seen"), "1");
  assert.equal(page.IntroFilm.isOpen(), true);
  assert.equal(page.IntroFilm.ownsOpening(), true, "the old one-off welcome animation stands down");
  advance(600);
  assert.equal(page.byId("intro-film-live").textContent, "你做过的题，大多数会忘。", "the first line reaches screen readers");
});

test("timeline: every act arrives on its cue and the ending offers Continue / Watch again", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  const live = page.byId("intro-film-live");
  for (let act = 2; act <= 5; act += 1) {
    advanceTo(CUES[act - 2], CUES[act - 1] - 1);
    assert.equal(root.dataset.act, String(act - 1), `still act ${act - 1} just before its cue`);
    advance(1);
    assert.equal(root.dataset.act, String(act));
    const on = root.querySelectorAll(".film-act.is-on");
    assert.equal(on.length, 1);
    assert.equal(on[0].dataset.act, String(act));
  }
  assert.equal(live.textContent, "欧叶OY：把每一次出错，变成下一次的把握。");
  const end = root.querySelector(".film-end");
  assert.equal(end.hidden, true, "no buttons before the final frame");
  advanceTo(CUES[4], CUES[5]);
  assert.equal(root.dataset.act, "6");
  assert.equal(end.hidden, false);
  assert.equal(root.classList.contains("is-ended"), true);
  assert.equal(page.byId("intro-film-again").hidden, false);
  assert.equal(page.focused(), "intro-film-continue", "focus moves to Continue");
  assert.match(live.textContent, /开场结束/);
  page.byId("intro-film-continue").click();
  assert.ok(!page.film(), "Continue closes the film");
  assert.equal(page.focused(), "intro-title", "focus returns to the welcome page's main heading");
  assert.equal(page.container.inert, false, "the page is interactive again");
  assert.equal(page.document.documentElement.classList.contains("intro-film-open"), false);
  advance(60000);
  assert.ok(!page.film(), "no timer survives closing");
});

for (const [label, skip] of [
  ["the Skip button", (page) => page.byId("intro-film-skip").click()],
  ["Escape", (page) => page.key("Escape")],
  ["the space bar", (page) => page.key(" ")],
]) {
  test(`skip: ${label} closes the film at any point and leads to the same welcome flow`, () => {
    const page = setup();
    page.IntroFilm.route("welcome");
    advance(12000);
    assert.equal(page.film().dataset.act, "3");
    skip(page);
    assert.ok(!page.film());
    assert.equal(page.IntroFilm.isOpen(), false);
    assert.equal(page.focused(), "intro-title");
    assert.equal(page.container.inert, false);
    advance(60000);
    assert.ok(!page.film());
  });
}

test("keys: space prevents page scrolling while playing but is left to the buttons on the final frame", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  advance(CUES[5]);
  const space = page.key(" ");
  assert.equal(space.defaultPrevented, false);
  assert.ok(page.film(), "space on the final frame does not dismiss it");
  const escape = page.key("Escape");
  assert.equal(escape.defaultPrevented, true);
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
  advance(CUES[5]);
  page.byId("intro-film-again").click();
  assert.equal(page.film().dataset.act, "1", "Watch again restarts from act 1");
  assert.equal(page.film().querySelector(".film-end").hidden, true);
  assert.equal(page.container.inert, true, "the page stays inert across the restart");
  assert.equal(page.focused(), "intro-film-skip");
  advance(CUES[1]);
  assert.equal(page.film().dataset.act, "2", "the restarted timeline runs");
  page.key("Escape");
  assert.ok(!page.film());
  assert.equal(page.focused(), "intro-film-replay", "focus goes back to the replay link");
  assert.equal(page.container.inert, false);
});

test("reduced motion: the static brand frame with Continue is shown and nothing is scheduled", () => {
  const page = setup({ motion: false });
  page.IntroFilm.route("welcome");
  const root = page.film();
  assert.ok(root);
  assert.equal(root.classList.contains("is-static"), true);
  assert.equal(root.dataset.act, "6");
  const on = root.querySelectorAll(".film-act.is-on").map((node) => node.dataset.act);
  assert.deepEqual([...on], ["5"]);
  assert.equal(root.querySelector(".film-end").hidden, false);
  assert.equal(page.byId("intro-film-continue").hidden, false);
  assert.equal(page.byId("intro-film-again").hidden, true, "nothing to watch again without motion");
  assert.equal(page.focused(), "intro-film-skip");
  advance(60000);
  assert.equal(root.dataset.act, "6");
  page.byId("intro-film-continue").click();
  assert.ok(!page.film());
  assert.equal(page.focused(), "intro-title");
});

test("reduced motion switched on mid-film freezes on the static frame", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  advance(8000);
  page.changeMotion(false);
  const root = page.film();
  assert.equal(root.classList.contains("is-static"), true);
  assert.equal(root.dataset.act, "6");
  assert.equal(page.byId("intro-film-again").hidden, true);
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

test("background tab: the timeline pauses while hidden and resumes with the remaining time", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const root = page.film();
  advance(3000);
  page.document.hidden = true;
  page.document.dispatchEvent(new FakeEvent("visibilitychange"));
  assert.equal(root.classList.contains("is-paused"), true, "CSS animations are paused too");
  advance(120000);
  assert.equal(root.dataset.act, "1", "nothing advances in the background");
  page.document.hidden = false;
  page.document.dispatchEvent(new FakeEvent("visibilitychange"));
  assert.equal(root.classList.contains("is-paused"), false);
  advance(CUES[1] - 3000 - 1);
  assert.equal(root.dataset.act, "1");
  advance(1);
  assert.equal(root.dataset.act, "2", "resumes exactly where it stopped");
});

test("background tab: a film opened in a hidden tab starts paused", () => {
  const page = setup();
  page.document.hidden = true;
  page.IntroFilm.route("welcome");
  advance(60000);
  assert.equal(page.film().dataset.act, "1");
  page.document.hidden = false;
  page.document.dispatchEvent(new FakeEvent("visibilitychange"));
  advance(CUES[1]);
  assert.equal(page.film().dataset.act, "2");
});

test("focus: Tab cycles inside the dialog and stray focus is pulled back", () => {
  const page = setup();
  page.IntroFilm.route("welcome");
  const skip = page.byId("intro-film-skip");
  const tab = page.key("Tab");
  assert.equal(tab.defaultPrevented, true);
  assert.equal(page.focused(), "intro-film-skip", "Skip is the only stop while playing");
  advance(CUES[5]);
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
  assert.equal(page.film().querySelector(".film-brand").textContent, "欧叶OY");
});
