"use strict";

/* 「手感层」：聚光灯、磁吸按钮、数字滚动、列表挪位（FLIP）、提示条、
   主角卡倾斜反光、图表生长、骨架屏。
   对外：window.Feel = { countUp(el), flip(container, mutate), toast(text, options),
   reduced(), tilt(el), skeletonShow(host, rows), skeletonHide(host) }。
   规则：只有一个全局 pointermove 监听（rAF 节流）；指针静止、没有磁吸/倾斜元素在动时没有任何 rAF；
   减少动画（prefers-reduced-motion）和触屏（hover: none）下全部关闭，数字、列表和图表直接给最终状态。
   启动时自动：给 [data-tilt] 绑 tilt，[data-countup] 跑 countUp，.feel-grow 的子项用
   IntersectionObserver 做首次进入视口生长（后加进 DOM 的节点由 MutationObserver 补上）。 */
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
    if (tiltStates.size) updateTiltTargets(event);
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
    let tiltNeed = false;
    tiltStates.forEach((state) => {
      if (state.targetX !== 0 || state.targetY !== 0) { state.targetX = 0; state.targetY = 0; tiltNeed = true; }
    });
    if (tiltNeed) startTiltPump();
  });
  for (const query of [reducedQuery, hoverQuery]) {
    const clear = () => {
      if (canHover()) return;
      magnets.forEach((state, el) => { el.style.translate = ""; });
      magnets.clear();
      resetTilt();
    };
    if (query.addEventListener) query.addEventListener("change", clear);
  }

  /* ---------- 主角卡倾斜 + 反光（03） ----------
     复用唯一的全局 pointermove 监听：onPointerFrame 按指针位置更新目标角度，
     tiltPump 每帧用 0.12 的 lerp 系数把实际值靠拢过去；全部静止后停掉 rAF 并清理变量。
     反光位置 --mx/--my 在指针帧里直接写（不需要 lerp）。只在 canHover() 时生效。 */

  const TILT_MAX = 7;     // 目标角度最大 ±7°
  const TILT_LERP = 0.12; // 实际值每帧向目标靠拢的系数
  const tiltStates = new Map(); // el -> { rx, ry, targetX, targetY, dirty }
  const tiltBound = new WeakSet();
  let tiltFrame = 0;

  function clampTilt(deg) {
    return Math.max(-TILT_MAX, Math.min(TILT_MAX, deg));
  }

  function updateTiltTargets(event) {
    let needPump = false;
    tiltStates.forEach((state, el) => {
      const rect = el.getBoundingClientRect();
      const over = rect.width > 0 && rect.height > 0
        && event.clientX >= rect.left && event.clientX <= rect.left + rect.width
        && event.clientY >= rect.top && event.clientY <= rect.top + rect.height;
      if (over) {
        const px = (event.clientX - rect.left) / rect.width - 0.5;
        const py = (event.clientY - rect.top) / rect.height - 0.5;
        const targetX = clampTilt(-py * 2 * TILT_MAX);
        const targetY = clampTilt(px * 2 * TILT_MAX);
        if (state.targetX !== targetX || state.targetY !== targetY) {
          state.targetX = targetX;
          state.targetY = targetY;
          needPump = true;
        }
        el.style.setProperty("--mx", `${(event.clientX - rect.left).toFixed(1)}px`);
        el.style.setProperty("--my", `${(event.clientY - rect.top).toFixed(1)}px`);
      } else if (state.targetX !== 0 || state.targetY !== 0) {
        state.targetX = 0;
        state.targetY = 0;
        needPump = true;
      }
    });
    if (needPump) startTiltPump();
  }

  function startTiltPump() {
    if (!tiltFrame) tiltFrame = window.requestAnimationFrame(tiltPump);
  }

  function tiltPump() {
    tiltFrame = 0;
    if (document.hidden) { resetTilt(); return; }
    let active = false;
    tiltStates.forEach((state, el) => {
      if (!el.isConnected) {
        // 卡片已被移出 DOM（调用者没调清理函数）：静默解绑，不留垃圾。
        tiltStates.delete(el);
        tiltBound.delete(el);
        return;
      }      const dx = state.targetX - state.rx;
      const dy = state.targetY - state.ry;
      if (Math.abs(dx) < 0.05 && Math.abs(dy) < 0.05) {
        if (state.dirty) {
          state.rx = state.targetX;
          state.ry = state.targetY;
          if (state.targetX === 0 && state.targetY === 0) {
            el.style.removeProperty("--rx");
            el.style.removeProperty("--ry");
            el.style.removeProperty("--mx");
            el.style.removeProperty("--my");
          } else {
            el.style.setProperty("--rx", `${state.rx.toFixed(2)}deg`);
            el.style.setProperty("--ry", `${state.ry.toFixed(2)}deg`);
          }
          state.dirty = false;
        }
        return;
      }
      state.rx += dx * TILT_LERP;
      state.ry += dy * TILT_LERP;
      el.style.setProperty("--rx", `${state.rx.toFixed(2)}deg`);
      el.style.setProperty("--ry", `${state.ry.toFixed(2)}deg`);
      state.dirty = true;
      active = true;
    });
    if (active) tiltFrame = window.requestAnimationFrame(tiltPump);
  }

  function resetTilt() {
    tiltStates.forEach((state, el) => {
      state.rx = 0; state.ry = 0; state.targetX = 0; state.targetY = 0; state.dirty = false;
      el.style.removeProperty("--rx");
      el.style.removeProperty("--ry");
      el.style.removeProperty("--mx");
      el.style.removeProperty("--my");
    });
    if (tiltFrame) { window.cancelAnimationFrame(tiltFrame); tiltFrame = 0; }
  }

  /* 给主角卡加倾斜 + 反光。只在可悬停设备上生效，否则返回空清理函数。
     返回清理函数：解绑、停掉可能在跑的帧、清除写过的 CSS 变量。
     调用者也可以不管清理函数：元素被移出 DOM 后，下一次 pump 会静默解绑。 */
  function tilt(el) {
    const noop = () => {};
    if (!el || tiltBound.has(el)) return noop;
    if (!canHover()) return noop;
    tiltBound.add(el);
    tiltStates.set(el, { rx: 0, ry: 0, targetX: 0, targetY: 0, dirty: false });
    return function cleanup() {
      tiltBound.delete(el);
      tiltStates.delete(el);
      el.style.removeProperty("--rx");
      el.style.removeProperty("--ry");
      el.style.removeProperty("--mx");
      el.style.removeProperty("--my");
      if (!tiltStates.size && tiltFrame) { window.cancelAnimationFrame(tiltFrame); tiltFrame = 0; }
    };
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

  /* ---------- 骨架屏（12） ----------
     skeletonShow(host, rows) 把 host 内容替换为骨架；skeletonHide(host) 清空 host，
     调用者负责重新渲染真实内容（这里不保存/恢复原 HTML）。 */

  function skeletonShow(host, rows = 3) {
    if (!host) return null;
    const count = Math.min(12, Math.max(1, Math.floor(Number(rows)) || 3));
    const box = document.createElement("div");
    box.className = "feel-skeleton";
    box.setAttribute("aria-hidden", "true");
    for (let i = 0; i < count; i += 1) box.append(document.createElement("i"));
    host.replaceChildren(box);
    return box;
  }

  function skeletonHide(host) {
    if (host) host.replaceChildren();
  }

  /* ---------- 图表生长（05） ----------
     .feel-grow 的子项首次进入视口时加 .is-grown（CSS 做 scaleY 生长），每页只播一次。
     减少动画或不支持 IntersectionObserver 时直接给最终态（不做 transition）。 */

  const grownSeen = new WeakSet();
  let growObserver = null;

  function growItemsIn(root) {
    const items = [];
    if (!root || root.nodeType !== 1) return items;
    const containers = [];
    if (root.classList.contains("feel-grow")) containers.push(root);
    else if (root.parentNode && root.parentNode.classList && root.parentNode.classList.contains("feel-grow")) items.push(root);
    root.querySelectorAll(".feel-grow").forEach((found) => containers.push(found));
    for (const container of containers) {
      for (const child of container.children) {
        if (child.nodeType === 1) items.push(child);
      }
    }
    return items;
  }

  function armGrow(items) {
    const fresh = items.filter((el) => !grownSeen.has(el));
    if (!fresh.length) return;
    if (reduced() || typeof window.IntersectionObserver !== "function") {
      for (const el of fresh) { grownSeen.add(el); el.classList.add("is-grown"); }
      return;
    }
    if (!growObserver) {
      growObserver = new window.IntersectionObserver((entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const el = entry.target;
          growObserver.unobserve(el);
          if (grownSeen.has(el)) continue;
          grownSeen.add(el);
          el.classList.add("is-grown");
        }
      }, { threshold: 0.15 });
    }
    for (const el of fresh) growObserver.observe(el);
  }

  /* ---------- 自动初始化 ----------
     启动时扫描一次；之后加进 DOM 的节点由 MutationObserver 补上（事件驱动，无空转）。
     html.feel-js 标记让 CSS 只在 JS 生效时才隐藏 .feel-grow 子项（无 JS 时内容保持可见）。 */

  function collectAttrTargets(root, name, apply) {
    const found = [];
    if (!root || root.nodeType !== 1) return found;
    if (root.hasAttribute(name)) found.push(root);
    root.querySelectorAll(`[${name}]`).forEach((el) => found.push(el));
    for (const el of found) apply(el);
  }

  function scanNode(node) {
    collectAttrTargets(node, "data-tilt", tilt);
    collectAttrTargets(node, "data-countup", countUp);
    armGrow(growItemsIn(node));
  }

  document.documentElement.classList.add("feel-js");
  scanNode(document.documentElement);
  if (typeof window.MutationObserver === "function") {
    const late = new window.MutationObserver((records) => {
      for (const record of records) {
        for (const node of record.addedNodes || []) scanNode(node);
      }
    });
    late.observe(document.body, { childList: true, subtree: true });
  }

  window.Feel = { countUp, flip, toast, reduced, tilt, skeletonShow, skeletonHide };
})();
