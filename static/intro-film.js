"use strict";

/* 首访开场短片：HTML + SVG + CSS 时间轴，幕间用共享元素连续运动（FLIP）。只动 transform / opacity /
   stroke-dashoffset；JS 只在节拍点切换类名。app.js 的 renderPageRoute 调 route() 告知路由。 */
(() => {
  /* ===== 时间参数（毫秒）全部在这里；CSS 的时长和延迟经自定义属性从这里传入 ===== */
  const EASE_OUT = "cubic-bezier(0.22, 1, 0.36, 1)"; // 出场
  const EASE_MOVE = "cubic-bezier(0.4, 0, 0.2, 1)"; // 移动
  const TIMING = Object.freeze({
    overlap: 200, // 下一幕在上一幕退出的中途进入（交叠 150–300）
    stagger: 32, // 逐字 / 逐行错开（25–40）
    rise: 560, // 文字 10px 上浮 + 淡入
    morph: 700, // 共享元素从上一幕飞到下一幕
    ctaAt: 0.5, // 最后一幕进入到一半，“继续探索”就可点
    // enter 进入；hold 读完这句（每秒 6–8 字 + 300）；exit 退出；beats 为幕内节拍（从本幕开始算）。
    acts: Object.freeze([
      Object.freeze({ enter: 1100, hold: 1800, exit: 400, beats: { lead: 200 } }),
      Object.freeze({ enter: 1300, hold: 2600, exit: 400, beats: { dark: 500, drawAt: 100, draw: 1100, text: 300 } }),
      Object.freeze({ enter: 1300, hold: 3300, exit: 400, beats: { lines: 120, circleAt: 600, circle: 500, noteAt: 800, text: 300 } }),
      Object.freeze({ enter: 1500, hold: 2400, exit: 400, beats: { slipGap: 90, trackAt: 200, track: 700, lightAt: 700, lightGap: 250, text: 300 } }),
      Object.freeze({
        enter: 2600, hold: 2200, exit: 0,
        beats: { strokeAt: 150, strokeGap: 110, stroke: 700, press: 150, fillAt: 1300, fill: 500, sealAt: 1800, seal: 450, ringAt: 1900, ring: 600, text: 2000 },
      }),
    ]),
  });
  // 离场幕 → [起点, 终点]：退出时起点飞到下一幕终点的位置。
  const SHARED = { 2: [".film-curve-svg", ".film-mark"], 3: [".film-circle", ".film-dot-first"], 4: [".film-track-line", ".film-ring", true] };

  const STORAGE_KEY = "oy-intro-film-seen";
  const SVG_NS = "http://www.w3.org/2000/svg";
  const LINES = [
    "你做过的题，大多数会忘。",
    "忘记不是你不努力，是大脑的默认设置。",
    "真正值钱的，不是答案，是你当时怎么想、错在哪一步。",
    "记下来，然后在快忘的时候再见一次。",
    "欧叶OY：把每一次出错，变成下一次的把握。",
  ];
  const ENDED = "开场结束。可以继续探索，或再看一遍。";
  const PLAN = plan();

  const search = String(window.location?.search || "");
  const startHash = String(window.location?.hash || "");
  // 从重置邮件或 #/auth 直达的这次加载不自动播放。
  const entryBlocked = new URLSearchParams(search).has("reset_token") || startHash === "#/auth";
  let firstRoute = true;
  let playedThisLoad = false;
  let film = null;

  /** 由 TIMING 推出每一幕的开始时刻和全部节拍点。 */
  function plan() {
    const starts = [];
    const cues = [];
    let at = 0;
    TIMING.acts.forEach((act, index) => {
      const number = index + 1;
      starts.push(at);
      cues.push({ at, kind: "enter", act: number });
      if (number === TIMING.acts.length) return;
      const leave = at + act.enter + act.hold;
      cues.push({ at: leave, kind: "leave", act: number });
      cues.push({ at: leave + Math.max(act.exit, TIMING.morph), kind: "hide", act: number });
      at = leave + act.exit - TIMING.overlap;
    });
    const last = TIMING.acts[TIMING.acts.length - 1];
    cues.push({ at: at + Math.round(last.enter * TIMING.ctaAt), kind: "cta" });
    cues.push({ at: at + last.enter, kind: "settle" });
    cues.sort((a, b) => a.at - b.at);
    return Object.freeze({ starts: Object.freeze(starts), cues: Object.freeze(cues.map(Object.freeze)), total: at + last.enter });
  }

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
  const delay = (node, index, name = "--i") => { node.style.setProperty(name, String(index)); return node; };
  const hidden = (node) => { node.setAttribute("aria-hidden", "true"); return node; };

  function act(number, ...children) {
    const section = el("div", `film-act film-act-${number}`);
    section.dataset.act = String(number);
    section.style.setProperty("--exit", `${TIMING.acts[number - 1].exit}ms`);
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
    // 第 1 幕：黑场逐字浮现；窄屏只在两组之间换行。
    const line = el("p", "film-line");
    let index = 0;
    for (const group of ["你做过的题，", "大多数会忘。"]) {
      const word = el("span", "film-word");
      for (const character of group) word.append(delay(el("span", "film-char", character), index++));
      line.append(word);
    }

    // 第 2 幕：遗忘曲线（单独一层，交接时整层飞走）。
    const chart = el("div", "film-chart");
    chart.append(
      svg("svg", { class: "film-axes", viewBox: "0 0 320 180", focusable: "false" }, svg("path", { d: "M24 12V160H312" })),
      svg("svg", { class: "film-curve-svg", viewBox: "0 0 320 180", focusable: "false" },
        svg("path", { class: "film-curve", pathLength: "1", d: "M24 22C52 96 92 128 150 140S262 154 308 156" })),
      el("span", "film-tag film-axis-y", "记忆"),
      el("span", "film-tag film-axis-x", "时间 →"),
    );

    // 第 3 幕：草稿纸，错的那步被圈出；曲线落下成为批注线。
    const draft = el("div", "film-draft");
    const code = el("pre", "film-code");
    const wrong = el("span", "film-wrong", "l = m");
    wrong.append(svg("svg", { class: "film-circle", viewBox: "0 0 92 50", preserveAspectRatio: "none", focusable: "false" },
      svg("path", { pathLength: "1", d: "M10 30C6 12 50 4 80 12C96 18 90 40 62 45C34 49 6 42 6 28C7 17 26 9 48 8" })));
    [["l, r = 0, n"], ["while l < r:"], ["    m = (l + r) // 2"], ["    if a[m] < x: ", wrong], ["    else: r = m"]].forEach((parts, row) => {
      const codeLine = delay(el("span", "film-code-line"), row);
      parts.forEach((part) => codeLine.append(typeof part === "string" ? el("span", "", part) : part));
      code.append(codeLine);
    });
    const note = el("div", "film-note");
    note.append(el("p", "", "l = m 会原地打转，应为 m + 1"),
      svg("svg", { class: "film-mark", viewBox: "0 0 160 10", preserveAspectRatio: "none", focusable: "false" },
        svg("path", { d: "M2 6Q12 1 22 6T42 6T62 6T82 6T102 6T122 6T142 6Q152 2 158 5" })));
    draft.append(el("p", "film-tag", "草稿 · 找第一个 ≥ x 的位置"), code, note);

    // 第 4 幕：间隔与 scheduler.py 的简化 SM-2 一致（1 天、6 天、约 2 周）。
    const slips = el("div", "film-slips");
    ["二分：l = m 会卡住", "两个 int 相加会溢出", "前缀和下标差一"].forEach((text, slip) => {
      slips.append(delay(el("p", "film-slip", text), slip));
    });
    const track = el("div", "film-track");
    const nodes = el("ol", "film-nodes");
    [["今天", 0], ["+1 天", 18], ["+6 天", 46], ["+约 2 周", 100]].forEach(([label, x], node) => {
      const item = delay(el("li", "film-node"), node - 1, "--n");
      item.style.setProperty("--x", `${x}%`);
      item.append(el("span", "film-node-card"), el("span", node ? "film-dot" : "film-dot film-dot-first"), el("span", "film-tag", label));
      nodes.append(item);
    });
    track.append(el("p", "film-tag film-track-title", "间隔复习"),
      svg("svg", { class: "film-track-line", viewBox: "0 0 100 2", preserveAspectRatio: "none", focusable: "false" },
        svg("path", { pathLength: "1", d: "M0 1H100" })),
      nodes);

    return [
      act(1, line),
      act(2, chart, caption("忘记不是你不努力，", "是大脑的默认设置。")),
      act(3, draft, caption("真正值钱的，不是答案，", "是你当时怎么想、错在哪一步。")),
      act(4, slips, track, caption("记下来，", "然后在快忘的时候再见一次。")),
    ];
  }

  /** 品牌字：细线先描、粗线随后（像笔压渐重），再由下往上填墨；读屏读 aria-label。 */
  function buildBrand() {
    const brand = el("div", "film-brand");
    brand.setAttribute("role", "img");
    brand.setAttribute("aria-label", "欧叶OY");
    const data = window.IntroGlyphs;
    if (!data?.glyphs?.length) {
      brand.append(hidden(el("span", "film-brand-text", "欧叶OY")));
      return brand;
    }
    const [x, y, width, height] = String(data.viewBox).split(/\s+/);
    const contours = data.glyphs.flatMap((glyph) => glyph.contours);
    const strokes = (className) => svg("g", { class: className }, ...contours.map((d, k) => delay(svg("path", { d, pathLength: "1" }), k, "--k")));
    brand.append(hidden(svg("svg", { class: "film-glyphs", viewBox: data.viewBox, focusable: "false" },
      svg("defs", {}, svg("clipPath", { id: "intro-film-ink" }, svg("rect", { class: "film-ink-wipe", x, y, width, height }))),
      svg("g", { class: "film-ink", "clip-path": "url(#intro-film-ink)" }, svg("path", { d: contours.join("") })),
      strokes("film-strokes"),
      strokes("film-strokes film-strokes-press"))));
    return brand;
  }

  function buildFinal(state) {
    const stamp = hidden(el("div", "film-stamp"));
    const seal = el("div", "film-seal");
    seal.append(el("span", "", "欧"), el("span", "", "叶"));
    stamp.append(el("span", "film-ring"), seal);
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
    const final = act(5, stamp, buildBrand(), tagline, actions);
    return { final, actions, next, again, summary };
  }

  function build(state) {
    const root = el("div", "intro-film");
    root.id = "intro-film";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-labelledby", "intro-film-title");
    root.tabIndex = -1;
    const ms = (name, value) => root.style.setProperty(name, `${value}ms`);
    ms("--stagger", TIMING.stagger);
    ms("--rise", TIMING.rise);
    TIMING.acts.forEach((step, index) => {
      ms(`--a${index + 1}-exit`, step.exit);
      for (const [beat, value] of Object.entries(step.beats)) {
        ms(`--a${index + 1}-${beat.replace(/[A-Z]/g, (letter) => `-${letter.toLowerCase()}`)}`, value);
      }
    });
    root.style.setProperty("--ease-out", EASE_OUT);
    root.style.setProperty("--ease-move", EASE_MOVE);
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
    const dark = hidden(el("div", "film-dark"));
    const stage = hidden(el("div", "film-stage"));
    stage.append(...buildActs());
    const flight = hidden(el("div", "film-flight"));
    const parts = buildFinal(state);
    root.append(title, live, dark, stage, parts.final, flight, skip);
    if (state.static) root.classList.add("is-static");
    return { root, live, skip, flight, ...parts };
  }

  /* ---- 共享元素交接（FLIP）：起点、终点各克隆进飞行层，用 transform 从旧位置运动到新位置并交叉淡化，
     真身落地前隐藏。每次交接只量一次位置。不用 View Transitions：其组动画改 width/height，期间页面点不动。 ---- */
  function fly(leaving) {
    const pair = SHARED[leaving];
    const { root, flight } = film.dom;
    if (!pair || typeof flight.animate !== "function") return;
    const from = root.querySelector(pair[0]);
    const to = root.querySelector(pair[1]);
    const a = from?.getBoundingClientRect();
    const b = to?.getBoundingClientRect();
    if (!a?.width || !a.height || !b?.width || !b.height) return;
    const origin = root.getBoundingClientRect();
    const ghost = (node, box) => {
      const shell = el("div", "film-ghost");
      for (const [name, value] of [["--gx", box.left - origin.left], ["--gy", box.top - origin.top], ["--gw", box.width], ["--gh", box.height]]) {
        shell.style.setProperty(name, `${value}px`);
      }
      shell.append(node.cloneNode(true));
      flight.append(shell);
      return shell;
    };
    const out = ghost(from, a);
    const into = ghost(to, b);
    from.classList.add("is-flown");
    to.classList.add("is-landing");
    // 起点飞向终点中心并收拢成终点大小（时间轴保持线宽，只横向收拢）；终点在原地由小变大接住它。
    const dx = b.left + b.width / 2 - a.left - a.width / 2;
    const dy = b.top + b.height / 2 - a.top - a.height / 2;
    const towards = `translate(${dx}px, ${dy}px) scale(${b.width / a.width}, ${pair[2] ? 1 : b.height / a.height})`;
    const options = { duration: TIMING.morph, easing: EASE_MOVE, fill: "both" };
    const current = film;
    const moves = [
      out.animate([{ transform: "none", opacity: 1 }, { opacity: 1, offset: 0.55 }, { transform: towards, opacity: 0 }], options),
      into.animate([{ transform: "scale(0.4)", opacity: 0 }, { opacity: 0, offset: 0.45 }, { transform: "none", opacity: 1 }], options),
    ];
    moves[1].onfinish = () => {
      if (film !== current) return;
      out.remove();
      into.remove();
      to.classList.remove("is-landing");
      moves.forEach((move) => current.flights.delete(move));
    };
    moves.forEach((move) => {
      current.flights.add(move);
      if (current.paused) move.pause();
    });
  }

  function settleFlights() {
    for (const move of [...film.flights]) move.finish();
  }

  /* ---------------- 时间轴 ---------------- */
  function runCue(cue) {
    const { dom } = film;
    const section = cue.act ? dom.root.querySelector(`.film-act-${cue.act}`) : null;
    if (cue.kind === "enter") {
      film.act = cue.act;
      dom.root.dataset.act = String(cue.act);
      section.classList.add("is-on");
      if (cue.act > 1) dom.live.textContent = LINES[cue.act - 1];
    } else if (cue.kind === "leave") {
      fly(cue.act);
      section.classList.remove("is-on");
      section.classList.add("is-leaving");
    } else if (cue.kind === "hide") {
      section.classList.remove("is-leaving");
    } else if (cue.kind === "cta") {
      film.ended = true;
      dom.actions.hidden = false;
      dom.live.textContent = ENDED;
      // 焦点还在“跳过”上时交给“继续探索”。
      const active = document.activeElement;
      if (!active || active === dom.skip || active === dom.root || !dom.root.contains(active)) dom.next.focus({ preventScroll: true });
    } else if (cue.kind === "settle") {
      dom.root.classList.add("is-ended");
    }
  }

  function schedule() {
    window.clearTimeout(film.timer);
    film.timer = 0;
    if (film.paused || film.cue >= PLAN.cues.length) return;
    const cue = PLAN.cues[film.cue];
    film.startedAt = now();
    film.timer = window.setTimeout(() => {
      film.timer = 0;
      film.elapsed = cue.at;
      film.cue += 1;
      runCue(cue);
      schedule();
    }, Math.max(0, cue.at - film.elapsed));
  }

  /** 轻触或按 →：立刻交接到下一幕（最后一幕则直接出“继续探索”）。 */
  function nextAct() {
    if (!film || film.static || film.ended || film.paused) return;
    const target = PLAN.cues.findIndex((cue, index) => index >= film.cue && (cue.kind === "enter" || cue.kind === "cta"));
    if (target < 0) return;
    window.clearTimeout(film.timer);
    film.timer = 0;
    settleFlights();
    while (film && film.cue <= target) runCue(PLAN.cues[film.cue++]);
    if (!film) return;
    film.elapsed = PLAN.cues[target].at;
    schedule();
  }

  function pause() {
    if (!film || film.paused || film.static) return;
    if (film.timer) film.elapsed = Math.min(PLAN.cues[film.cue]?.at ?? film.elapsed, film.elapsed + now() - film.startedAt);
    window.clearTimeout(film.timer);
    film.timer = 0;
    film.paused = true;
    film.dom.root.classList.add("is-paused");
    film.flights.forEach((move) => move.pause());
  }
  function resume() {
    if (!film || !film.paused) return;
    film.paused = false;
    film.dom.root.classList.remove("is-paused");
    film.flights.forEach((move) => move.play());
    schedule();
  }
  function onVisibility() {
    if (document.hidden) pause();
    else resume();
  }

  /** 减少动态效果：只显示第 5 幕完整画面和“继续探索”。 */
  function freeze() {
    const { dom } = film;
    window.clearTimeout(film.timer);
    film.timer = 0;
    film.flights.forEach((move) => move.cancel());
    film.flights.clear();
    dom.flight.replaceChildren();
    film.static = true;
    film.paused = false;
    dom.root.classList.add("is-static");
    dom.root.classList.remove("is-paused");
    dom.again.hidden = true;
    dom.root.querySelectorAll(".film-act").forEach((section) => {
      section.classList.remove("is-leaving");
      section.classList.toggle("is-on", section.dataset.act === "5");
    });
    dom.root.querySelectorAll(".is-flown, .is-landing").forEach((node) => node.classList.remove("is-flown", "is-landing"));
    film.act = 5;
    film.cue = PLAN.cues.length;
    dom.root.dataset.act = "5";
    runCue({ kind: "cta" });
    runCue({ kind: "settle" });
  }
  function onMotionChange() {
    if (film && !film.static && !motionAllowed()) freeze();
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
    } else if ((event.key === " " || event.key === "Spacebar") && !film.ended) {
      event.preventDefault();
      close();
    } else if (event.key === "ArrowRight" || event.key === "Right") {
      event.preventDefault();
      nextAct();
    } else if (event.key === "Tab") {
      event.preventDefault();
      const items = focusables();
      const current = items.indexOf(document.activeElement);
      const step = event.shiftKey ? -1 : 1;
      const index = current < 0 ? (step > 0 ? 0 : items.length - 1) : (current + step + items.length) % items.length;
      items[index].focus();
    }
  }
  function onClick(event) {
    if (!film || event.target?.closest?.("button, summary, details, a")) return;
    nextAct();
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
    film = {
      dom, opener, act: 0, cue: 0, elapsed: 0, startedAt: 0, timer: 0, paused: false, ended: false,
      static: state.static, locked: [], flights: new Set(),
    };
    film.locked = lockPage(dom.root);
    document.body.append(dom.root);
    document.addEventListener("keydown", onKeydown, true);
    document.addEventListener("click", onClick);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("visibilitychange", onVisibility);
    film.motion = window.matchMedia?.("(prefers-reduced-motion: no-preference)");
    film.motion?.addEventListener?.("change", onMotionChange);
    if (state.static) {
      freeze();
      dom.live.textContent = `${LINES[4]} ${ENDED}`;
    } else {
      film.cue = 1;
      runCue(PLAN.cues[0]);
      // 实时区域刚插入时改字常被忽略，稍等再念第一句。
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
    current.flights.forEach((move) => move.cancel());
    document.removeEventListener("keydown", onKeydown, true);
    document.removeEventListener("click", onClick);
    document.removeEventListener("focusin", onFocusIn);
    document.removeEventListener("visibilitychange", onVisibility);
    current.motion?.removeEventListener?.("change", onMotionChange);
    current.dom.root.remove();
    for (const [node, inert] of current.locked) node.inert = inert;
    document.documentElement.classList.remove("intro-film-open");
    return current;
  }

  /** 跳过与“继续探索”同路：关闭后焦点回到欢迎页主标题（重看时回到触发按钮）。 */
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
    /** 只有本次加载第一次落到欢迎页、且没看过时才自动播放。 */
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
    next: nextAct,
    isOpen: () => Boolean(film),
    /** 放过开场后 intro.js 的欢迎页动效让位，避免两段开场叠放。 */
    ownsOpening: () => Boolean(film) || playedThisLoad,
    timing: Object.freeze({ ...TIMING, easeOut: EASE_OUT, easeMove: EASE_MOVE, lines: LINES, ...PLAN }),
  };

  document.querySelector("#intro-film-replay")?.addEventListener("click", (event) => {
    window.IntroFilm.replay(event.currentTarget);
  });
})();
