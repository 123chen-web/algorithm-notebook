"use strict";

(() => {
  // Replace only this array when licensed photographs become available.
  const SCENES = [
    { id: "ink", name: "水墨山水", src: "/static/scenes/ink-landscape.svg", accent: "#32645b" },
    { id: "snow", name: "雪山夜窗", src: "/static/scenes/snow-window.svg", accent: "#365e79" },
    { id: "lake", name: "雾湖森林", src: "/static/scenes/mist-lake.svg", accent: "#296357" },
    { id: "rain", name: "雨夜暖灯", src: "/static/scenes/rain-study.svg", accent: "#85562b" },
  ];
  const root = document.documentElement;
  const backdrop = document.getElementById("scene-backdrop");
  const dots = document.getElementById("scene-dots");
  const play = document.getElementById("scene-play");
  if (!backdrop || !dots || !play) return;
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  const mobile = window.matchMedia("(max-width: 720px), (hover: none), (pointer: coarse)");
  const layers = [0, 1].map(() => {
    const img = document.createElement("img");
    img.className = "scene-layer";
    img.alt = "";
    img.decoding = "async";
    backdrop.append(img);
    return img;
  });
  let index = 0;
  let activeLayer = 0;
  let generation = 0;
  let timer = null;
  let paused = false;
  let pageActive = true;
  let hasScene = false;
  let previousView = root.dataset.view;
  let transitionFrame = 0;
  let transitionTimer = null;
  let transitionEnd = null;
  let preloadIdle = null;
  let preloadTimer = null;
  let preloadSource = null;
  let preloadImage = null;

  const canPlay = () => pageActive && !document.hidden && root.dataset.view === "welcome"
    && !reducedMotion.matches && !mobile.matches && !paused;
  const canTransition = () => pageActive && !document.hidden && !reducedMotion.matches && !mobile.matches
    && (root.dataset.view === "welcome" || root.dataset.theme === "qixi");
  const canPreload = () => hasScene && canTransition() && root.dataset.view === "welcome"
    && !transitionFrame && !transitionEnd;

  function cancelPreload() {
    if (preloadIdle !== null) window.cancelIdleCallback(preloadIdle);
    preloadIdle = null;
    window.clearTimeout(preloadTimer);
    preloadTimer = null;
    preloadSource = null;
    preloadImage = null;
  }

  function schedulePreload() {
    if (!canPreload()) { cancelPreload(); return; }
    const source = SCENES[(index + 1) % SCENES.length].src;
    if (preloadSource === source) return;
    cancelPreload();
    preloadSource = source;
    const prepare = () => {
      preloadIdle = null;
      preloadTimer = null;
      if (!canPreload() || preloadSource !== source) return;
      // Keep only the next decoded image, without inserting or promoting a layer.
      const image = new Image();
      image.decoding = "async";
      preloadImage = image;
      image.src = source;
      image.decode().catch(() => {
        if (preloadImage === image) { preloadImage = null; preloadSource = null; }
      });
    };
    if (window.requestIdleCallback) preloadIdle = window.requestIdleCallback(prepare, { timeout: 1500 });
    else preloadTimer = window.setTimeout(prepare, 250);
  }

  function finishTransition() {
    window.cancelAnimationFrame(transitionFrame);
    transitionFrame = 0;
    window.clearTimeout(transitionTimer);
    transitionTimer = null;
    layers.forEach((img, i) => {
      if (transitionEnd) img.removeEventListener("transitionend", transitionEnd);
      img.classList.remove("is-transitioning", "is-incoming");
      img.classList.toggle("is-active", hasScene && i === activeLayer);
    });
    transitionEnd = null;
    schedulePreload();
  }

  function startTransition(request) {
    const incoming = layers[activeLayer];
    incoming.classList.add("is-incoming", "is-transitioning");
    // Only the incoming image needs promotion; the opaque outgoing image stays below.
    transitionFrame = window.requestAnimationFrame(() => {
      transitionFrame = window.requestAnimationFrame(() => {
        transitionFrame = 0;
        if (request !== generation) return;
        if (!canTransition()) { finishTransition(); return; }
        transitionEnd = (event) => {
          if (event.propertyName === "opacity" && event.target === incoming && request === generation) {
            finishTransition();
          }
        };
        incoming.addEventListener("transitionend", transitionEnd);
        incoming.classList.add("is-active");
        // Also release the layers if a transition event is interrupted or omitted.
        transitionTimer = window.setTimeout(finishTransition, 1400);
      });
    });
  }

  function reconcile() {
    if (root.dataset.view !== previousView || !canTransition()) finishTransition();
    previousView = root.dataset.view;
    window.clearTimeout(timer);
    timer = null;
    play.hidden = mobile.matches || reducedMotion.matches;
    play.textContent = paused ? "自动轮播" : "暂停轮播";
    play.setAttribute("aria-pressed", String(paused));
    if (canPlay()) timer = window.setTimeout(() => selectScene((index + 1) % SCENES.length), 9000);
    schedulePreload();
  }

  async function selectScene(nextIndex) {
    const request = ++generation;
    const scene = SCENES[nextIndex];
    if (!scene) return;
    // A rapid selection can reuse the outgoing image only after its old fade ends.
    finishTransition();
    const nextLayer = index === nextIndex && layers[activeLayer].src ? activeLayer : 1 - activeLayer;
    const layer = layers[nextLayer];
    layer.src = scene.src;
    try { await layer.decode(); } catch (_) { return; }
    if (request !== generation) return;
    const animate = hasScene && index !== nextIndex && canTransition();
    index = nextIndex;
    activeLayer = nextLayer;
    hasScene = true;
    if (animate) startTransition(request);
    else finishTransition();
    root.dataset.scene = scene.id;
    root.style.setProperty("--scene-accent", scene.accent);
    document.getElementById("scene-name").textContent = scene.name;
    dots.querySelectorAll("button").forEach((button, i) => {
      button.setAttribute("aria-pressed", String(i === index));
    });
    document.querySelectorAll("[data-scene-select]").forEach((select) => { select.value = scene.id; });
    reconcile();
  }

  SCENES.forEach((scene, i) => {
    const button = document.createElement("button");
    button.type = "button";
    button.setAttribute("aria-label", `切换风景：${scene.name}`);
    button.setAttribute("aria-pressed", String(i === index));
    button.addEventListener("click", () => selectScene(i));
    dots.append(button);
  });
  dots.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === "Home" ? 0 : event.key === "End" ? SCENES.length - 1
      : (index + (event.key === "ArrowLeft" ? -1 : 1) + SCENES.length) % SCENES.length;
    dots.children[next].focus();
    selectScene(next);
  });
  play.addEventListener("click", () => { paused = !paused; reconcile(); });

  // Touch gestures change only the scenery, never intercept links or vertical scrolling.
  const hero = document.querySelector(".intro-hero");
  let touch = null;
  hero.addEventListener("pointerdown", (event) => {
    if (event.pointerType !== "touch" || event.target.closest("a, button, input, select")) return;
    touch = { x: event.clientX, y: event.clientY, id: event.pointerId };
  });
  hero.addEventListener("pointerup", (event) => {
    if (!touch || touch.id !== event.pointerId) return;
    const dx = event.clientX - touch.x;
    const dy = event.clientY - touch.y;
    touch = null;
    if (Math.abs(dx) > 45 && Math.abs(dx) > Math.abs(dy) * 1.5) {
      selectScene((index + (dx < 0 ? 1 : -1) + SCENES.length) % SCENES.length);
    }
  });
  hero.addEventListener("pointercancel", () => { touch = null; });
  document.querySelectorAll("[data-scene-select]").forEach((select) => {
    SCENES.forEach((scene) => select.add(new Option(scene.name, scene.id)));
    select.addEventListener("change", () => selectScene(SCENES.findIndex((scene) => scene.id === select.value)));
  });
  new MutationObserver(reconcile).observe(root, { attributes: true, attributeFilter: ["data-view", "data-theme"] });
  reducedMotion.addEventListener("change", reconcile);
  mobile.addEventListener("change", reconcile);
  document.addEventListener("visibilitychange", reconcile);
  window.addEventListener("pagehide", () => { pageActive = false; reconcile(); });
  window.addEventListener("pageshow", () => { pageActive = true; reconcile(); });
  selectScene(0);
})();
