"use strict";

/* 「手感层」：聚光灯、磁吸按钮、数字滚动、列表挪位（FLIP）、提示条。
   对外：window.Feel = { countUp(el), flip(container, mutate), toast(text, options), reduced() }。
   规则：只有一个全局 pointermove 监听（rAF 节流）；指针静止、没有磁吸元素在动时没有任何 rAF；
   减少动画（prefers-reduced-motion）和触屏（hover: none）下全部关闭，数字和列表直接给最终状态。 */
(() => {
  const SPOT = ".ov-tile, .ov-card, .problem-record-card, .pl-card, .pattern-card, .board-card, .groups-card, .plan-card, .rc-card, .ls-wrap";
  const MAGNET_RANGE = 130;
  const reducedQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const hoverQuery = window.matchMedia("(hover: hover) and (pointer: fine)");
  const reduced = () => reducedQuery.matches;
  const canHover = () => hoverQuery.matches && !reducedQuery.matches;

  /* ---------- 指针：聚光灯 + 磁吸 ---------- */

  let lastEvent = null;
  let frame = 0;
  let loop = 0;
  const magnets = new Map(); // el -> { x, y, tx, ty }

  function scheduleFrame() {
    if (!frame) frame = window.requestAnimationFrame(onPointerFrame);
  }

  function onPointerFrame() {
    frame = 0;
    const event = lastEvent;
    if (!event || !canHover()) return;
    const card = event.target && event.target.closest ? event.target.closest(SPOT) : null;
    if (card) {
      const rect = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${(event.clientX - rect.left).toFixed(1)}px`);
      card.style.setProperty("--my", `${(event.clientY - rect.top).toFixed(1)}px`);
    }
    let any = false;
    document.querySelectorAll("[data-magnetic]").forEach((el) => {
      const rect = el.getBoundingClientRect();
      if (!rect.width) return;
      const dx = event.clientX - (rect.left + rect.width / 2);
      const dy = event.clientY - (rect.top + rect.height / 2);
      const near = Math.hypot(dx, dy) < MAGNET_RANGE + Math.max(rect.width, rect.height) / 2;
      const state = magnets.get(el) || { x: 0, y: 0, tx: 0, ty: 0 };
      state.tx = near ? dx * 0.3 : 0;
      state.ty = near ? dy * 0.4 : 0;
      magnets.set(el, state);
      any = true;
    });
    if (any) startLoop();
  }

  function startLoop() {
    if (!loop) loop = window.requestAnimationFrame(tick);
  }

  function tick() {
    loop = 0;
    if (document.hidden) { magnets.clear(); return; }
    let moving = false;
    magnets.forEach((state, el) => {
      state.x += (state.tx - state.x) * 0.14;
      state.y += (state.ty - state.y) * 0.14;
      const settled = Math.abs(state.tx - state.x) < 0.05 && Math.abs(state.ty - state.y) < 0.05;
      if (settled && state.tx === 0 && state.ty === 0) {
        el.style.translate = "";
        magnets.delete(el);
        return;
      }
      el.style.translate = `${state.x.toFixed(2)}px ${state.y.toFixed(2)}px`;
      moving = true;
    });
    if (moving) loop = window.requestAnimationFrame(tick);
  }

  document.addEventListener("pointermove", (event) => {
    if (event.pointerType === "touch" || !canHover()) return;
    lastEvent = event;
    scheduleFrame();
  }, { passive: true });
  document.addEventListener("pointerleave", () => {
    magnets.forEach((state) => { state.tx = 0; state.ty = 0; });
    if (magnets.size) startLoop();
  });
  for (const query of [reducedQuery, hoverQuery]) {
    const clear = () => {
      if (canHover()) return;
      magnets.forEach((state, el) => { el.style.translate = ""; });
      magnets.clear();
    };
    if (query.addEventListener) query.addEventListener("change", clear);
  }

  /* ---------- 数字滚动 ---------- */

  function countUp(el, duration = 900) {
    if (!el) return;
    const finalText = el.textContent.trim();
    if (!/^\d{1,9}$/.test(finalText)) return;
    const target = Number(finalText);
    const from = Number(el.dataset.feelValue ?? 0);
    el.dataset.feelValue = String(target);
    if (reduced() || document.hidden || from === target) return;
    const started = window.performance.now();
    const step = (now) => {
      if (el.dataset.feelValue !== String(target)) return; // 中途被新值覆盖：让新一轮接管
      const k = Math.min(1, (now - started) / duration);
      const eased = 1 - Math.pow(1 - k, 3);
      el.textContent = k < 1 ? String(Math.round(from + (target - from) * eased)) : finalText;
      if (k < 1) window.requestAnimationFrame(step);
    };
    window.requestAnimationFrame(step);
  }

  /* ---------- 列表挪位（FLIP） ---------- */

  function flip(container, mutate) {
    if (!container) { mutate?.(); return; }
    if (reduced() || typeof container.animate !== "function") { mutate?.(); return; }
    const before = new Map();
    for (const child of container.children) before.set(child, child.getBoundingClientRect());
    mutate?.();
    for (const child of container.children) {
      const was = before.get(child);
      if (!was) continue;
      const now = child.getBoundingClientRect();
      const dx = was.left - now.left;
      const dy = was.top - now.top;
      if (!dx && !dy) continue;
      child.animate(
        [{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }],
        { duration: 520, easing: "cubic-bezier(.2, .9, .25, 1.08)" },
      );
    }
  }

  /* ---------- 提示条 ---------- */

  function toast(text, { duration = 3200 } = {}) {
    let box = document.getElementById("feel-toasts");
    if (!box) {
      box = document.createElement("div");
      box.id = "feel-toasts";
      box.className = "feel-toasts";
      box.setAttribute("role", "status");
      box.setAttribute("aria-live", "polite");
      document.body.append(box);
    }
    const item = document.createElement("div");
    item.className = "feel-toast";
    item.style.setProperty("--feel-toast-ms", `${duration}ms`);
    item.textContent = String(text ?? "");
    box.append(item);
    const close = () => {
      if (reduced()) { item.remove(); return; }
      item.classList.add("is-out");
      window.setTimeout(() => item.remove(), 380);
    };
    item.addEventListener("click", close);
    window.setTimeout(close, duration);
    return item;
  }

  window.Feel = { countUp, flip, toast, reduced };
})();
