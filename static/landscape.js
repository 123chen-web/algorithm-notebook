"use strict";

/* 总览页「成长山水」：一幅会随着已掌握套路数生长的水墨小山水（栖间主题是绿色山水）。
   对外契约：window.Landscape = { configure({ api, getUser, getEpoch }), mount(container), reset() }。
   - 数据来自 GET /api/roadmap 的 progress（{pattern_id: "learning"|"mastered"}）和
     /static/data/patterns.json（总数与套路标题）；两个请求并行，晚到响应按登录代次丢弃；
   - 哪个套路长成哪个元素由套路 id 的 fnv1a 哈希决定，稳定不乱跳；
   - 新长出的元素播一次“墨晕开”动画（记 localStorage，读写失败也能用）；
   - 常驻微动画（雾、飞鸟、水波）只在画面可见时跑，prefers-reduced-motion 下全部静止；
   - /api/roadmap 失败或太慢不阻塞首屏：先画静态占位，再补一句温和的话。 */
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const VIEW_W = 800;
  const VIEW_H = 240;
  // 整体阶段阈值（已掌握数）：远山添层、日出。
  const MOUNTAIN_2_AT = 10;
  const MOUNTAIN_3_AT = 40;
  const SUN_AT = 135;
  const TOTAL_FALLBACK = 150;
  const LS_PREFIX = "oy-landscape-grown:";

  let hooks = null;
  let container = null;
  let generation = 0;
  let sequence = 0;
  let catalogPromise = null;
  let liveObservers = [];

  function ticket() {
    sequence += 1;
    return { sequence, generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }

  function current(request) {
    const user = hooks?.getUser();
    return Boolean(hooks && user && request.generation === generation
      && request.sequence === sequence
      && request.epoch === hooks.getEpoch() && request.userId === user.id);
  }

  function reducedMotion() {
    try {
      return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    } catch {
      return false;
    }
  }

  function hashId(text) {
    let hash = 0x811c9dc5;
    const value = String(text);
    for (let index = 0; index < value.length; index += 1) {
      hash ^= value.charCodeAt(index);
      hash = Math.imul(hash, 0x01000193);
    }
    return hash >>> 0;
  }

  /* 12 个“可生长”槽位：位置与元素种类固定，同一个套路永远落到同一个槽位。 */
  const SLOTS = [
    { kind: "tree", x: 120, y: 186 }, { kind: "tree", x: 205, y: 193 },
    { kind: "tree", x: 470, y: 188 }, { kind: "tree", x: 560, y: 195 },
    { kind: "tree", x: 660, y: 190 }, { kind: "tree", x: 92, y: 199 },
    { kind: "tree", x: 716, y: 197 },
    { kind: "bird", x: 300, y: 62 }, { kind: "bird", x: 522, y: 48 },
    { kind: "boat", x: 400, y: 207 },
    { kind: "ripple", x: 250, y: 221 }, { kind: "ripple", x: 560, y: 223 },
  ];

  const MOUNTAIN_1 = "M-10 190 L90 118 L170 160 L280 78 L400 170 L490 128 L620 185 L720 138 L810 175 L810 250 L-10 250 Z";
  const MOUNTAIN_2 = "M-10 202 L140 140 L260 186 L420 118 L580 190 L700 148 L810 196 L810 250 L-10 250 Z";
  const MOUNTAIN_3 = "M-10 212 L200 160 L350 200 L520 148 L700 206 L810 180 L810 250 L-10 250 Z";
  const WATER = "M40 206 h150 M596 213 h130 M296 224 h190";

  function svgEl(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    return node;
  }

  function htmlEl(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function treePath(x, y) {
    return `M${x} ${y - 38} L${x + 11} ${y - 20} L${x + 5} ${y - 20} L${x + 14} ${y - 6} `
      + `L${x + 3} ${y - 6} L${x + 3} ${y} L${x - 3} ${y} L${x - 3} ${y - 6} `
      + `L${x - 14} ${y - 6} L${x - 5} ${y - 20} L${x - 11} ${y - 20} Z`;
  }

  function birdPath(x, y) {
    return `M${x - 14} ${y} Q${x - 7} ${y - 9} ${x} ${y} Q${x + 7} ${y - 9} ${x + 14} ${y}`;
  }

  function buildSlotShape(slot) {
    const group = svgEl("g", { class: "ls-slot" });
    const { kind, x, y } = slot;
    if (kind === "tree") {
      group.appendChild(svgEl("path", { class: "ls-tree", d: treePath(x, y) }));
    } else if (kind === "bird") {
      group.appendChild(svgEl("path", { class: "ls-bird", d: birdPath(x, y) }));
    } else if (kind === "boat") {
      group.appendChild(svgEl("path", {
        class: "ls-boat-hull",
        d: `M${x - 26} ${y} Q${x} ${y + 10} ${x + 26} ${y} L${x + 18} ${y + 9} L${x - 18} ${y + 9} Z`,
      }));
      group.appendChild(svgEl("path", {
        class: "ls-boat-sail",
        d: `M${x} ${y - 2} L${x + 16} ${y - 2} L${x} ${y - 22} Z`,
      }));
    } else {
      group.appendChild(svgEl("ellipse", {
        class: "ls-ripple", cx: String(x), cy: String(y), rx: "26", ry: "7",
      }));
    }
    return group;
  }

  function captionFor(count, total) {
    if (count <= 0) return "学会第一个套路，这里就会长出第一棵树";
    const base = `已掌握 ${count} / ${total}`;
    const next = [
      [MOUNTAIN_2_AT, "远山将多一层"],
      [MOUNTAIN_3_AT, "远山再添一层"],
      [SUN_AT, "迎来日出"],
    ].find(([at]) => count < at);
    if (!next) return `${base} · 山水已满，继续精进`;
    return `${base} · 再学 ${next[0] - count} 个，${next[1]}`;
  }

  function seenKey(userId) {
    return `${LS_PREFIX}${userId}`;
  }

  function loadSeen(userId) {
    try {
      const raw = JSON.parse(window.localStorage.getItem(seenKey(userId)) || "[]");
      return new Set(Array.isArray(raw) ? raw.filter((value) => typeof value === "string") : []);
    } catch {
      return new Set();
    }
  }

  function saveSeen(userId, ids) {
    try {
      window.localStorage.setItem(seenKey(userId), JSON.stringify([...ids]));
    } catch {
      /* 存储不可用也不影响使用：下次还会再播一次生长动画而已。 */
    }
  }

  function getCatalog() {
    if (!catalogPromise) {
      catalogPromise = fetch("/static/data/patterns.json", { credentials: "omit" })
        .then((response) => {
          if (!response.ok) throw new Error(`catalog ${response.status}`);
          return response.json();
        })
        .catch(() => null);
    }
    return catalogPromise;
  }

  function watchVisibility(svg) {
    if (typeof IntersectionObserver === "undefined") return;
    try {
      const observer = new IntersectionObserver((entries) => {
        svg.classList.toggle("ls-paused", !entries.some((entry) => entry.isIntersecting));
      }, { threshold: 0 });
      observer.observe(svg);
      liveObservers.push(observer);
    } catch {
      /* 不支持就一直播着，动画本身很轻。 */
    }
  }

  function disconnectObservers() {
    for (const observer of liveObservers) {
      try { observer.disconnect(); } catch { /* ignore */ }
    }
    liveObservers = [];
  }

  function buildFigure({ count, total, owners, titles, freshSlots, captionText }) {
    const figure = htmlEl("figure", "ls-wrap");
    const svg = svgEl("svg", {
      class: "ls-svg",
      viewBox: `0 0 ${VIEW_W} ${VIEW_H}`,
      role: "img",
      "aria-label": `成长山水：已掌握 ${count} 个套路`,
    });
    svg.appendChild(svgEl("path", { class: "ls-mountain ls-m1", d: MOUNTAIN_1 }));
    if (count >= MOUNTAIN_2_AT) svg.appendChild(svgEl("path", { class: "ls-mountain ls-m2", d: MOUNTAIN_2 }));
    if (count >= MOUNTAIN_3_AT) svg.appendChild(svgEl("path", { class: "ls-mountain ls-m3", d: MOUNTAIN_3 }));
    if (count >= SUN_AT) svg.appendChild(svgEl("circle", { class: "ls-sun", cx: "682", cy: "52", r: "24" }));

    const tip = htmlEl("p", "ls-tip");
    tip.hidden = true;
    tip.setAttribute("role", "status");

    if (count > 0) {
      const mist1 = svgEl("ellipse", { class: "ls-mist", cx: "250", cy: "150", rx: "120", ry: "13" });
      const mist2 = svgEl("ellipse", { class: "ls-mist ls-mist-b", cx: "580", cy: "166", rx: "100", ry: "11" });
      mist1.setAttribute("aria-hidden", "true");
      mist2.setAttribute("aria-hidden", "true");
      svg.append(mist1, mist2);
      const water = svgEl("path", { class: "ls-water", d: WATER });
      water.setAttribute("aria-hidden", "true");
      svg.appendChild(water);
      const flyer = svgEl("path", { class: "ls-fly", d: birdPath(-40, 96) });
      flyer.setAttribute("aria-hidden", "true");
      svg.appendChild(flyer);

      const showTip = (label) => {
        tip.textContent = label;
        tip.hidden = false;
      };
      const hideTip = () => { tip.hidden = true; };
      const sortedSlots = [...owners.keys()].sort((a, b) => a - b);
      for (const slotIndex of sortedSlots) {
        const slot = SLOTS[slotIndex];
        const ids = owners.get(slotIndex);
        const names = ids.map((id) => titles.get(id) || id);
        const label = names.length > 1
          ? `因为你掌握了「${names[0]}」等 ${names.length} 个套路`
          : `因为你掌握了「${names[0]}」`;
        const item = svgEl("g", {
          class: "ls-el",
          tabindex: "0",
          role: "img",
          "aria-label": label,
          "data-slot": String(slotIndex),
          "data-kind": slot.kind,
        });
        if (freshSlots.has(slotIndex) && !reducedMotion()) {
          item.classList.add("ls-grow", `ls-delay-${slotIndex % 5}`);
        }
        item.appendChild(buildSlotShape(slot));
        item.addEventListener("mouseenter", () => showTip(label));
        item.addEventListener("mouseleave", hideTip);
        item.addEventListener("focus", () => showTip(label));
        item.addEventListener("blur", hideTip);
        item.addEventListener("click", () => showTip(label));
        svg.appendChild(item);
      }
      if (!reducedMotion()) watchVisibility(svg);
    }

    const caption = htmlEl("figcaption", "ls-caption", captionText);
    caption.setAttribute("role", "status");
    figure.append(svg, tip, caption);
    return figure;
  }

  async function mount(target) {
    container = target;
    if (!container || !hooks) return;
    disconnectObservers();
    const mine = ++generation;
    const request = ticket();
    // 先画静态占位：不阻塞首屏，数据回来再补全。
    container.replaceChildren(buildFigure({
      count: 0, total: TOTAL_FALLBACK, owners: new Map(), titles: new Map(),
      freshSlots: new Set(), captionText: "正在读取学习进度…",
    }));
    try {
      const [roadmap, catalog] = await Promise.all([hooks.api("/api/roadmap"), getCatalog()]);
      if (mine !== generation || !current(request)) return;
      const progress = (roadmap && typeof roadmap.progress === "object" && roadmap.progress) || {};
      const mastered = Object.entries(progress)
        .filter(([, status]) => status === "mastered")
        .map(([patternId]) => patternId);
      const titles = new Map();
      let total = TOTAL_FALLBACK;
      if (Array.isArray(catalog) && catalog.length > 0) {
        total = catalog.length;
        for (const item of catalog) {
          if (item && typeof item.id === "string") titles.set(item.id, item.title || item.id);
        }
      }
      const owners = new Map();
      for (const patternId of mastered) {
        const slotIndex = hashId(patternId) % SLOTS.length;
        if (!owners.has(slotIndex)) owners.set(slotIndex, []);
        owners.get(slotIndex).push(patternId);
      }
      for (const ids of owners.values()) ids.sort();
      const seen = loadSeen(request.userId);
      const freshSlots = new Set(
        mastered.filter((patternId) => !seen.has(patternId)).map((patternId) => hashId(patternId) % SLOTS.length),
      );
      saveSeen(request.userId, new Set([...seen, ...mastered]));
      container.replaceChildren(buildFigure({
        count: mastered.length,
        total,
        owners,
        titles,
        freshSlots,
        captionText: captionFor(mastered.length, total),
      }));
    } catch {
      if (mine !== generation || !container) return;
      container.replaceChildren(buildFigure({
        count: 0, total: TOTAL_FALLBACK, owners: new Map(), titles: new Map(),
        freshSlots: new Set(), captionText: "暂时读不到学习进度，山水先静静待着",
      }));
    }
  }

  function reset() {
    generation += 1;
    disconnectObservers();
    if (container) container.replaceChildren();
  }

  if (typeof document !== "undefined" && typeof document.addEventListener === "function") {
    document.addEventListener("visibilitychange", () => {
      if (typeof document.hidden !== "boolean" || typeof document.querySelectorAll !== "function") return;
      for (const svg of document.querySelectorAll(".ls-svg")) {
        svg.classList.toggle("ls-paused", document.hidden);
      }
    });
  }

  window.Landscape = {
    configure(options) { hooks = options; },
    mount,
    reset,
  };
})();
