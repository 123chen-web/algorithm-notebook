"use strict";

/* 复习手感：翻牌 / 滑动评分 / 盖章墨印 / 震动反馈。
   只加交互层，不改现有评分流程与接口：滑动提交就是去点原来那颗评分按钮，
   离线队列、撤销、AI 材料都不受影响。
   对外契约：window.ReviewFeel = { scan, wrapFlip, setFlipped, attachSwipe, buzz, gradeFor }。
   - app.js 的详情视图：靠 MutationObserver 自动 scan（app.js 零改动，只有盖章墨印和 buzz 两处小钩子）。
   - focus.js：显式调用 wrapFlip / setFlipped / attachSwipe。
   用户文字一律 textContent；只用 DOM API 拼节点；动效只动 transform / opacity。 */
(() => {
  const VIBRATE_KEY = "review-vibrate";
  const SWIPE_X = 90;    // 横向超过多少 px 松手才提交
  const SWIPE_UP = 70;   // 上滑超过多少 px 松手才提交
  const LOCK_SLOP = 12;  // 超过多少 px 才锁定方向
  const GESTURE_WINDOW = 5000; // 手势有效期：只在用户手势里震动

  // 滑动方向 → 现有评分档位（与评分按钮的 data-quality 一致）
  const GRADE_FOR = { right: 4, left: 0, up: 5 };
  const GRADE_LABEL = { 4: "记得", 0: "忘了", 5: "很熟" };

  let lastGestureAt = 0;
  const enterArmed = new WeakSet(); // 滑动提交后，下一张内容进来时轻轻升起

  function markGesture() { lastGestureAt = Date.now(); }

  function armEnter(el) { if (el) enterArmed.add(el); }
  function consumeEnter(el) {
    if (!el || !enterArmed.has(el)) return false;
    enterArmed.delete(el);
    el.classList.add("rf-enter");
    window.setTimeout(() => el.classList.remove("rf-enter"), 500);
    return true;
  }

  function vibrateEnabled() {
    try { return window.localStorage.getItem(VIBRATE_KEY) !== "false"; }
    catch { return true; }
  }

  function buzz() {
    if (!vibrateEnabled()) return;
    const nav = window.navigator;
    if (!nav || typeof nav.vibrate !== "function") return;
    if (Date.now() - lastGestureAt > GESTURE_WINDOW) return; // 只在用户手势里触发
    try { nav.vibrate(10); } catch { /* 不支持就安静跳过 */ }
  }

  /* ---------- 翻牌 ---------- */

  // 把错因 section 的内容包进正反两面：正面是"先回忆"占位，背面是真正的错因。
  // startFlipped=true 时离线（未插入 DOM 前）就加上类，避免首帧闪一下正面。
  function wrapFlip(section, startFlipped = false) {
    if (!section || section.dataset.rfFlip === "1") return section;
    section.dataset.rfFlip = "1";
    const inner = document.createElement("div");
    inner.className = "rf-flip-inner" + (startFlipped ? " rf-flipped" : "");
    const front = document.createElement("div");
    front.className = "rf-flip-face rf-flip-front";
    front.setAttribute("aria-hidden", "true");
    const hint = document.createElement("p");
    hint.className = "rf-flip-hint";
    hint.textContent = "先回忆，再翻开看错因";
    front.appendChild(hint);
    const back = document.createElement("div");
    back.className = "rf-flip-face rf-flip-back";
    while (section.firstChild) back.appendChild(section.firstChild);
    inner.append(front, back);
    section.appendChild(inner);
    section.classList.add("rf-flip");
    return section;
  }

  function setFlipped(section, on) {
    const inner = section && section.querySelector ? section.querySelector(".rf-flip-inner") : null;
    if (inner) inner.classList.toggle("rf-flipped", Boolean(on));
  }

  /* ---------- 滑动评分 ---------- */

  function isInteractive(target) {
    return Boolean(target && target.closest && target.closest(
      "input, textarea, select, button, a, summary, details, pre, code, [contenteditable], .rf-no-swipe"
    ));
  }

  function buildHints(card) {
    const hints = {};
    for (const [dir, text] of [["left", "忘了"], ["right", "记得"], ["up", "很熟"]]) {
      const el = document.createElement("div");
      el.className = `rf-hint rf-hint-${dir}`;
      el.setAttribute("aria-hidden", "true");
      el.textContent = text;
      card.appendChild(el);
      hints[dir] = el;
    }
    return hints;
  }

  function clamp01(value) { return Math.max(0, Math.min(1, value)); }

  function applyDrag(card, hints, dx, dy, dir) {
    card.style.transform = `translate(${dx}px, ${dy}px) rotate(${(dx * 0.06).toFixed(2)}deg)`;
    hints.left.style.opacity = dir === "left" ? String(clamp01(-dx / SWIPE_X)) : "0";
    hints.right.style.opacity = dir === "right" ? String(clamp01(dx / SWIPE_X)) : "0";
    hints.up.style.opacity = dir === "up" ? String(clamp01(-dy / SWIPE_UP)) : "0";
  }

  function resetDrag(card, hints) {
    card.style.transform = "";
    for (const key of Object.keys(hints)) hints[key].style.opacity = "0";
  }

  function attachSwipe(card, opts = {}) {
    if (!card || card.dataset.rfSwipe === "1") return;
    card.dataset.rfSwipe = "1";
    card.classList.add("rf-swipe");
    const hints = buildHints(card);
    const state = { id: null, x0: 0, y0: 0, locked: null, active: false };

    function gradeButton(quality) {
      if (typeof opts.gradeButton === "function") return opts.gradeButton(quality);
      return card.querySelector(`[data-quality="${quality}"]`);
    }
    function canSwipe() {
      if (typeof opts.canSwipe === "function") return opts.canSwipe();
      return true;
    }

    card.addEventListener("pointerdown", (event) => {
      if (event.isPrimary === false) return;
      if (event.pointerType === "mouse" && event.button !== 0) return;
      if (state.active || isInteractive(event.target) || !canSwipe()) return;
      state.id = event.pointerId;
      state.x0 = event.clientX; state.y0 = event.clientY;
      state.locked = null; state.active = true;
      markGesture();
    });

    card.addEventListener("pointermove", (event) => {
      if (!state.active || event.pointerId !== state.id) return;
      const dx = event.clientX - state.x0;
      const dy = event.clientY - state.y0;
      if (!state.locked) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) < LOCK_SLOP) return;
        // 方向锁定：先判断主方向再决定接管；纵向为主就放手让页面滚动
        if (Math.abs(dx) >= Math.abs(dy)) state.locked = "h";
        else if (dy < 0) state.locked = "up";
        else { state.locked = "down"; return; }
        card.classList.add("rf-dragging");
      }
      if (state.locked === "h") {
        if (event.cancelable) event.preventDefault();
        applyDrag(card, hints, dx, 0, dx >= 0 ? "right" : "left");
      } else if (state.locked === "up") {
        if (event.cancelable) event.preventDefault();
        applyDrag(card, hints, 0, Math.min(0, dy), "up");
      }
    });

    function end(event, cancelled) {
      if (!state.active || event.pointerId !== state.id) return;
      state.active = false;
      card.classList.remove("rf-dragging");
      const dx = event.clientX - state.x0;
      const dy = event.clientY - state.y0;
      let dir = null;
      if (!cancelled && state.locked === "h" && Math.abs(dx) >= SWIPE_X) dir = dx > 0 ? "right" : "left";
      else if (!cancelled && state.locked === "up" && dy <= -SWIPE_UP) dir = "up";
      if (!dir || !canSwipe()) { resetDrag(card, hints); return; }
      const quality = GRADE_FOR[dir];
      const button = gradeButton(quality);
      if (!button || button.disabled) { resetDrag(card, hints); return; }
      // 提交：飞出当前卡；下一张由 onCommit 负责轻轻升起
      markGesture();
      const commitTarget = typeof opts.onCommitTarget === "function" ? opts.onCommitTarget() : null;
      armEnter(commitTarget);
      card.style.transform = "";
      card.classList.add(`rf-fly-${dir === "left" ? "left" : dir === "up" ? "up" : "right"}`);
      resetDrag(card, hints);
      button.click(); // 走原来那颗按钮的提交流程：离线、撤销、盖章都在
      window.setTimeout(() => {
        if (card.isConnected) {
          card.classList.remove("rf-fly-left", "rf-fly-right", "rf-fly-up");
          card.style.transform = "";
        }
      }, 900);
    }
    card.addEventListener("pointerup", (event) => end(event, false));
    card.addEventListener("pointercancel", (event) => end(event, true));
  }

  /* ---------- 自动接入（app.js 详情视图） ---------- */

  function scan(root) {
    const doc = root && root.querySelector ? root : document;
    const detail = doc.id === "detail" ? doc : doc.querySelector("#detail");
    if (!detail) return;
    const reason = detail.querySelector("#review-reason");
    if (reason) {
      const revealed = !reason.hidden;
      wrapFlip(reason, revealed);
      setFlipped(reason, revealed);
    }
    attachSwipe(detail, {
      gradeButton: (quality) => detail.querySelector(`#review-grade-row [data-quality="${quality}"]`),
      canSwipe: () => {
        const target = detail.querySelector("#review-reason");
        return Boolean(target && !target.hidden);
      },
      onCommitTarget: () => detail,
    });
    if (detail.children.length > 0) consumeEnter(detail);
  }

  function watch() {
    const detail = document.getElementById("detail");
    if (!detail) return;
    scan(detail);
    if (typeof MutationObserver === "undefined") return;
    new MutationObserver((mutations) => {
      let rescan = false;
      let flipCheck = false;
      for (const mutation of mutations) {
        if (mutation.type === "childList") rescan = true;
        if (mutation.type === "attributes" && mutation.target && mutation.target.id === "review-reason") flipCheck = true;
      }
      if (rescan) scan(detail);
      if (flipCheck) {
        const reason = detail.querySelector("#review-reason");
        if (reason) setFlipped(reason, !reason.hidden);
      }
    }).observe(detail, { childList: true, subtree: true, attributes: true, attributeFilter: ["hidden"] });
  }

  /* ---------- 震动开关 ---------- */

  function wireVibrateToggle() {
    const box = document.getElementById("account-vibrate");
    if (!box) return;
    try { box.checked = window.localStorage.getItem(VIBRATE_KEY) !== "false"; }
    catch { box.checked = true; }
    box.addEventListener("change", () => {
      try { window.localStorage.setItem(VIBRATE_KEY, String(box.checked)); } catch { /* 读写失败也能用 */ }
    });
  }

  // 手势计时：click / keydown 也算手势，保证按钮评分后 buzz() 能通过检查
  document.addEventListener("pointerdown", markGesture, { passive: true });
  document.addEventListener("keydown", markGesture);
  document.addEventListener("click", markGesture);

  wireVibrateToggle();
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", watch);
  else watch();

  window.ReviewFeel = { scan, wrapFlip, setFlipped, attachSwipe, buzz, consumeEnter,
    gradeFor: (dir) => GRADE_FOR[dir], gradeLabel: (q) => GRADE_LABEL[q] };
})();
