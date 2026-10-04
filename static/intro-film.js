"use strict";

/* 首访开场短片：HTML + SVG + CSS 时间轴。
   JS 只在幕与幕之间切换类名（动画本身全是 transform / opacity / stroke-dashoffset），
   并负责首访判断、跳过、焦点、后台暂停。路由由 app.js 的 renderPageRoute 调 route() 告知。 */
(() => {
  const STORAGE_KEY = "oy-intro-film-seen";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const LINES = [
    "你做过的题，大多数会忘。",
    "忘记不是你不努力，是大脑的默认设置。",
    "真正值钱的，不是答案，是你当时怎么想、错在哪一步。",
    "记下来，然后在快忘的时候再见一次。",
    "欧叶OY：把每一次出错，变成下一次的把握。",
  ];
  // 每一幕的开始时刻（毫秒）；第 6 幕是定格，出现“继续探索”。
  const CUES = [0, 5200, 11400, 18800, 25600, 30400];
  const ENDED = "开场结束。可以继续探索，或再看一遍。";

  const search = String(window.location?.search || "");
  const startHash = String(window.location?.hash || "");
  // 从重置邮件或 #/auth 直达进来的这一次页面加载，整段都不自动播放。
  const entryBlocked = new URLSearchParams(search).has("reset_token") || startHash === "#/auth";
  let firstRoute = true;
  let playedThisLoad = false;
  let film = null;

  const now = () => (typeof window.performance?.now === "function" ? window.performance.now() : Date.now());
  const motionAllowed = () => window.matchMedia?.("(prefers-reduced-motion: no-preference)")?.matches === true;

  function seen() {
    try { return window.localStorage.getItem(STORAGE_KEY) === "1"; } catch (_) { return false; }
  }
  function remember() {
    try { window.localStorage.setItem(STORAGE_KEY, "1"); } catch (_) { /* 存不了就只在本次加载内记住。 */ }
  }

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function svg(tag, attrs, ...children) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
    node.append(...children);
    return node;
  }
  const delay = (node, index) => { node.style.setProperty("--i", String(index)); return node; };

  function act(number, ...children) {
    const section = el("div", `film-act film-act-${number}`);
    section.dataset.act = String(number);
    const frame = el("div", "film-frame");
    frame.append(...children);
    section.append(frame);
    return section;
  }
  function caption(...parts) {
    const node = el("p", "film-caption");
    parts.forEach((part, index) => node.append(delay(el("span", "film-caption-line", part), index)));
    return node;
  }

  function buildActs() {
    // 第 1 幕：黑场，逐字浮现；按标点分成两组，窄屏只在组间换行。
    const line = el("p", "film-line");
    let index = 0;
    for (const group of ["你做过的题，", "大多数会忘。"]) {
      const word = el("span", "film-word");
      for (const character of group) word.append(delay(el("span", "film-char", character), index++));
      line.append(word);
    }

    // 第 2 幕：遗忘曲线。
    const chart = el("div", "film-chart");
    chart.append(
      svg("svg", { class: "film-chart-svg", viewBox: "0 0 320 180", focusable: "false" },
        svg("path", { class: "film-axis", d: "M24 12V160H312" }),
        svg("path", { class: "film-curve", pathLength: "1", d: "M24 22C52 96 92 128 150 140S262 154 308 156" })),
      el("span", "film-tag film-axis-y", "记忆"),
      el("span", "film-tag film-axis-x", "时间 →"),
    );

    // 第 3 幕：草稿纸，出错的那一步被朱红笔圈出。
    const draft = el("div", "film-draft");
    const code = el("pre", "film-code");
    const wrong = el("span", "film-wrong", "l = m");
    wrong.append(svg("svg", { class: "film-circle", viewBox: "0 0 92 50", preserveAspectRatio: "none", focusable: "false" },
      svg("path", { pathLength: "1", d: "M10 30C6 12 50 4 80 12C96 18 90 40 62 45C34 49 6 42 6 28C7 17 26 9 48 8" })));
    const codeLines = [["l, r = 0, n"], ["while l < r:"], ["    m = (l + r) // 2"], ["    if a[m] < x: ", wrong], ["    else: r = m"]];
    codeLines.forEach((parts, row) => {
      const codeLine = delay(el("span", "film-code-line"), row);
      parts.forEach((part) => codeLine.append(typeof part === "string" ? el("span", "", part) : part));
      code.append(codeLine);
    });
    draft.append(el("p", "film-tag", "草稿 · 找第一个 ≥ x 的位置"), code,
      el("p", "film-note", "l = m 会原地打转，应为 m + 1"));

    // 第 4 幕：错题收进来，再按越拉越长的间隔重新亮起（与 scheduler.py 的简化 SM-2 一致：1 天、6 天、约 2 周）。
    const slips = el("div", "film-slips");
    ["二分：l = m 会卡住", "两个 int 相加会溢出", "前缀和下标差一"].forEach((text, slip) => {
      slips.append(delay(el("p", "film-slip", text), slip));
    });
    const track = el("div", "film-track");
    const nodes = el("ol", "film-nodes");
    [["今天", 0], ["+1 天", 18], ["+6 天", 46], ["+约 2 周", 100]].forEach(([label, x], node) => {
      const item = delay(el("li", "film-node"), node);
      item.style.setProperty("--x", `${x}%`);
      item.append(el("span", "film-node-card"), el("span", "film-dot"), el("span", "film-tag", label));
      nodes.append(item);
    });
    track.append(el("p", "film-tag film-track-title", "间隔复习"), el("span", "film-track-line"), nodes);

    return [
      act(1, line),
      act(2, chart, caption("忘记不是你不努力，", "是大脑的默认设置。")),
      act(3, draft, caption("真正值钱的，不是答案，", "是你当时怎么想、错在哪一步。")),
      act(4, slips, track, caption("记下来，", "然后在快忘的时候再见一次。")),
    ];
  }

  function buildFinal(state) {
    const seal = el("div", "film-seal");
    seal.setAttribute("aria-hidden", "true");
    seal.append(el("span", "", "欧"), el("span", "", "叶"));
    const underline = svg("svg", { class: "film-underline", viewBox: "0 0 160 10", preserveAspectRatio: "none", "aria-hidden": "true", focusable: "false" },
      svg("path", { pathLength: "1", d: "M2 6Q12 1 22 6T42 6T62 6T82 6T102 6T122 6T142 6Q152 2 158 5" }));
    const brand = el("p", "film-brand", "欧叶OY");
    const tagline = el("p", "film-tagline", "把每一次出错，变成下一次的把握。");

    const actions = el("div", "film-end");
    actions.hidden = true;
    const buttons = el("div", "film-actions");
    const next = el("button", "primary film-continue", "继续探索");
    next.type = "button";
    next.id = "intro-film-continue";
    const again = el("button", "film-replay", "再看一遍");
    again.type = "button";
    again.id = "intro-film-again";
    again.hidden = state.static;
    buttons.append(next, again);

    const transcript = el("details", "film-transcript");
    const summary = el("summary", "", "文字版");
    const list = el("ol");
    LINES.forEach((text) => list.append(el("li", "", text)));
    transcript.append(summary, list);
    actions.append(buttons, transcript);

    next.addEventListener("click", () => close());
    again.addEventListener("click", () => restart());
    const final = act(5, seal, brand, underline, tagline, actions);
    return { final, actions, next, again, summary };
  }

  function build(state) {
    const root = el("div", "intro-film");
    root.id = "intro-film";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "intro-film-title");
    root.tabIndex = -1;
    const title = el("h2", "film-sr", "欧叶OY 开场短片");
    title.id = "intro-film-title";
    const live = el("p", "film-sr");
    live.id = "intro-film-live";
    live.setAttribute("aria-live", "polite");
    const skip = el("button", "film-skip", "跳过");
    skip.type = "button";
    skip.id = "intro-film-skip";
    skip.setAttribute("aria-label", "跳过开场");
    skip.addEventListener("click", () => close());
    const dark = el("div", "film-dark");
    dark.setAttribute("aria-hidden", "true");
    const stage = el("div", "film-stage");
    stage.setAttribute("aria-hidden", "true");
    stage.append(...buildActs());
    const progress = el("div", "film-progress");
    progress.setAttribute("aria-hidden", "true");
    const parts = buildFinal(state);
    root.append(title, live, dark, stage, parts.final, progress, skip);
    if (state.static) root.classList.add("is-static");
    return { root, live, skip, ...parts };
  }

  /* ---------------- 时间轴 ---------------- */
  function setAct(number) {
    const { dom } = film;
    film.act = number;
    dom.root.dataset.act = String(number);
    dom.root.querySelectorAll(".film-act").forEach((section) => {
      const value = Number(section.dataset.act);
      section.classList.toggle("is-on", value === Math.min(number, 5));
    });
    if (number <= 5) dom.live.textContent = LINES[number - 1];
    if (number < 6) return;
    dom.root.classList.add("is-ended");
    dom.actions.hidden = false;
    dom.live.textContent = ENDED;
    // 用户还没主动移开焦点时，把焦点交给“继续探索”。
    const active = document.activeElement;
    if (!active || active === dom.skip || active === dom.root || !dom.root.contains(active)) dom.next.focus({ preventScroll: true });
  }

  function schedule() {
    window.clearTimeout(film.timer);
    film.timer = 0;
    if (film.paused || film.act >= CUES.length) return;
    const due = CUES[film.act];
    film.startedAt = now();
    film.timer = window.setTimeout(() => {
      film.timer = 0;
      film.elapsed = due;
      setAct(film.act + 1);
      schedule();
    }, Math.max(0, due - film.elapsed));
  }

  function pause() {
    if (!film || film.paused || film.static) return;
    if (film.timer) film.elapsed = Math.min(CUES[film.act] ?? film.elapsed, film.elapsed + now() - film.startedAt);
    window.clearTimeout(film.timer);
    film.timer = 0;
    film.paused = true;
    film.dom.root.classList.add("is-paused");
  }
  function resume() {
    if (!film || !film.paused) return;
    film.paused = false;
    film.dom.root.classList.remove("is-paused");
    schedule();
  }
  function onVisibility() {
    if (document.hidden) pause();
    else resume();
  }
  function onMotionChange() {
    // 播放途中改成“减少动态效果”：直接定格到静态版。
    if (!film || film.static || motionAllowed()) return;
    window.clearTimeout(film.timer);
    film.timer = 0;
    film.static = true;
    film.dom.root.classList.add("is-static");
    film.dom.again.hidden = true;
    setAct(6);
  }

  /* ---------------- 焦点与按键 ---------------- */
  function focusables() {
    const { skip, next, again, summary } = film.dom;
    return [skip, next, again, summary].filter((node) => !node.hidden && !node.closest("[hidden]"));
  }
  function onKeydown(event) {
    if (!film) return;
    if (event.key === "Escape" || event.key === "Esc") {
      event.preventDefault();
      close();
    } else if ((event.key === " " || event.key === "Spacebar") && film.act < 6) {
      event.preventDefault();
      close();
    } else if (event.key === "Tab") {
      // 焦点只在开场里循环，不会跑到被遮住的页面上。
      event.preventDefault();
      const items = focusables();
      const current = items.indexOf(document.activeElement);
      const step = event.shiftKey ? -1 : 1;
      const index = current < 0 ? (step > 0 ? 0 : items.length - 1) : (current + step + items.length) % items.length;
      items[index].focus();
    }
  }
  function onFocusIn(event) {
    if (film && !film.dom.root.contains(event.target)) film.dom.skip.focus({ preventScroll: true });
  }

  function lockPage(root) {
    const locked = [];
    for (const child of [...document.body.children]) {
      if (child === root || child.nodeType !== 1) continue;
      locked.push([child, Boolean(child.inert)]);
      child.inert = true;
    }
    document.documentElement.classList.add("intro-film-open");
    return locked;
  }

  function open(opener) {
    const state = { static: !motionAllowed() };
    const dom = build(state);
    playedThisLoad = true;
    remember();
    film = { dom, opener, act: 0, elapsed: 0, startedAt: 0, timer: 0, paused: false, static: state.static, locked: [] };
    film.locked = lockPage(dom.root);
    document.body.append(dom.root);
    document.addEventListener("keydown", onKeydown, true);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("visibilitychange", onVisibility);
    film.motion = window.matchMedia?.("(prefers-reduced-motion: no-preference)");
    film.motion?.addEventListener?.("change", onMotionChange);
    if (state.static) {
      setAct(6);
      dom.live.textContent = `${LINES[4]} ${ENDED}`;
    } else {
      setAct(1);
      dom.live.textContent = "";
      // 实时区域刚插入时改字常被读屏忽略，稍等再念第一句。
      const current = film;
      window.setTimeout(() => { if (film === current && current.act === 1) dom.live.textContent = LINES[0]; }, 600);
      if (document.hidden) pause();
      else schedule();
    }
    dom.skip.focus({ preventScroll: true });
  }

  function teardown() {
    const current = film;
    film = null;
    window.clearTimeout(current.timer);
    document.removeEventListener("keydown", onKeydown, true);
    document.removeEventListener("focusin", onFocusIn);
    document.removeEventListener("visibilitychange", onVisibility);
    current.motion?.removeEventListener?.("change", onMotionChange);
    current.dom.root.remove();
    for (const [node, inert] of current.locked) node.inert = inert;
    document.documentElement.classList.remove("intro-film-open");
    return current;
  }

  /** 跳过与“继续探索”走同一条路：关掉开场，焦点回到欢迎页主内容（重看时回到触发按钮）。 */
  function close({ restoreFocus = true } = {}) {
    if (!film) return;
    const { opener } = teardown();
    if (!restoreFocus) return;
    const intro = document.querySelector("#intro");
    let target = opener && opener.isConnected && !opener.hidden ? opener : null;
    if (!target && intro && !intro.hidden) {
      target = document.querySelector("#intro-title");
      if (target && !target.hasAttribute("tabindex")) target.setAttribute("tabindex", "-1");
    }
    target?.focus();
  }

  function restart() {
    if (!film) return;
    const { opener } = teardown();
    open(opener);
  }

  window.IntroFilm = {
    /** app.js 每次渲染路由时调用；只有本次页面加载第一次落到欢迎页、且未看过时才自动播放。 */
    route(view) {
      const first = firstRoute;
      firstRoute = false;
      if (film && view !== "welcome") close({ restoreFocus: false });
      if (first && view === "welcome" && !entryBlocked && !seen()) open(null);
    },
    replay(opener = null) {
      if (!film) open(opener);
    },
    close,
    isOpen: () => Boolean(film),
    /** 本次加载里放过（或正在放）开场时，intro.js 的一次性欢迎页动效让位，避免两段开场叠放。 */
    ownsOpening: () => Boolean(film) || playedThisLoad,
  };

  document.querySelector("#intro-film-replay")?.addEventListener("click", (event) => {
    window.IntroFilm.replay(event.currentTarget);
  });
})();
