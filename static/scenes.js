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

  const canPlay = () => pageActive && !document.hidden && root.dataset.view === "welcome"
    && !reducedMotion.matches && !mobile.matches && !paused;

  function reconcile() {
    window.clearTimeout(timer);
    timer = null;
    play.hidden = mobile.matches || reducedMotion.matches;
    play.textContent = paused ? "自动轮播" : "暂停轮播";
    play.setAttribute("aria-pressed", String(paused));
    if (canPlay()) timer = window.setTimeout(() => selectScene((index + 1) % SCENES.length), 9000);
  }

  async function selectScene(nextIndex) {
    const request = ++generation;
    const scene = SCENES[nextIndex];
    if (!scene) return;
    const nextLayer = index === nextIndex && layers[activeLayer].src ? activeLayer : 1 - activeLayer;
    const layer = layers[nextLayer];
    layer.src = scene.src;
    try { await layer.decode(); } catch (_) { return; }
    if (request !== generation) return;
    index = nextIndex;
    activeLayer = nextLayer;
    layers.forEach((img, i) => img.classList.toggle("is-active", i === activeLayer));
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
  new MutationObserver(reconcile).observe(root, { attributes: true, attributeFilter: ["data-view"] });
  reducedMotion.addEventListener("change", reconcile);
  mobile.addEventListener("change", reconcile);
  document.addEventListener("visibilitychange", reconcile);
  window.addEventListener("pagehide", () => { pageActive = false; reconcile(); });
  window.addEventListener("pageshow", () => { pageActive = true; reconcile(); });
  selectScene(0);
})();
