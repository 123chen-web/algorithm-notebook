/* Presentation enhancements reuse app.js's forms and native validation. */
(() => {
  "use strict";
  const page = document.querySelector("#new-page");
  if (!page) return;
  const cards = [...page.querySelectorAll("[data-form-section]")];
  const progress = [...page.querySelectorAll("[data-form-progress]")];
  let progressFrame = 0;

  function scheduleProgress() {
    if (page.hidden || progressFrame) return;
    progressFrame = requestAnimationFrame(() => {
      progressFrame = 0;
      if (page.hidden) return;
      const anchor = Math.min(260, innerHeight / 3);
      let current = cards[0];
      for (const card of cards) {
        if (card.getBoundingClientRect().top <= anchor) current = card;
      }
      if (scrollY > 0 && scrollY + innerHeight >= document.documentElement.scrollHeight - 2) current = cards.at(-1);
      for (const segment of progress) {
        segment.classList.toggle("is-current", segment.dataset.formProgress === current.dataset.formSection);
      }
    });
  }
  addEventListener("scroll", scheduleProgress, { passive: true });
  addEventListener("resize", scheduleProgress);
  new MutationObserver(scheduleProgress).observe(page, { attributes: true, attributeFilter: ["hidden"] });
  scheduleProgress();

  const photoForm = page.querySelector("#problem-photo-form");
  const fileInput = page.querySelector("#problem-photo-file");
  const dropzone = page.querySelector(".photo-dropzone");
  const filename = page.querySelector("#problem-photo-filename");
  let dragDepth = 0;

  function syncFilename() {
    const file = fileInput.files[0];
    filename.textContent = file?.name || "";
    filename.hidden = !file;
  }
  fileInput.addEventListener("change", syncFilename);
  photoForm.addEventListener("reset", () => requestAnimationFrame(syncFilename));

  function canDrop() {
    return matchMedia("(hover: hover) and (pointer: fine)").matches
      && !fileInput.disabled && document.querySelector("#app").getAttribute("aria-busy") !== "true";
  }
  function clearDrag() {
    dragDepth = 0;
    dropzone.classList.remove("is-dragging");
  }
  dropzone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    if (!canDrop()) return;
    dragDepth += 1;
    dropzone.classList.add("is-dragging");
  });
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = canDrop() ? "copy" : "none";
  });
  dropzone.addEventListener("dragleave", () => {
    dragDepth -= 1;
    if (dragDepth <= 0) clearDrag();
  });
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    clearDrag();
    if (!canDrop()) return;
    const file = event.dataTransfer?.files[0];
    if (!file) return;
    const transfer = new DataTransfer();
    transfer.items.add(file);
    fileInput.files = transfer.files;
    fileInput.dispatchEvent(new Event("change", { bubbles: true }));
  });

  const recordForm = page.querySelector("#problem-form");
  const folds = [...recordForm.querySelectorAll(".form-fold")];

  function setExpanded(card, expanded, immediate = false) {
    const button = card.querySelector(".form-fold-toggle");
    const content = document.getElementById(button.getAttribute("aria-controls"));
    if (immediate) {
      card.classList.add("form-fold-instant");
      requestAnimationFrame(() => requestAnimationFrame(() => card.classList.remove("form-fold-instant")));
    }
    button.setAttribute("aria-expanded", String(expanded));
    card.dataset.expanded = String(expanded);
    content.inert = !expanded;
    scheduleProgress();
  }
  function syncFoldStatus() {
    for (const card of folds) {
      const filled = [...card.querySelectorAll("input, select, textarea")].some(field => field.value.trim());
      const badge = card.querySelector(".form-fold-state");
      badge.textContent = filled ? "已填写" : "待填写";
      badge.classList.toggle("is-filled", filled);
    }
  }
  for (const card of folds) {
    const button = card.querySelector(".form-fold-toggle");
    button.addEventListener("click", () => setExpanded(card, button.getAttribute("aria-expanded") !== "true"));
  }
  recordForm.addEventListener("input", syncFoldStatus);
  recordForm.addEventListener("change", syncFoldStatus);
  recordForm.addEventListener("reset", () => requestAnimationFrame(() => {
    for (const card of folds) setExpanded(card, false, true);
    syncFoldStatus();
  }));
  // Child changes also cover app.js's programmatic photo fill and mistake removal.
  new MutationObserver(syncFoldStatus).observe(page.querySelector("#mistake-inputs"), { childList: true });
  page.querySelector("#add-mistake").addEventListener("click", () => setExpanded(folds[1], true));

  let firstInvalid = null;
  recordForm.addEventListener("invalid", (event) => {
    const field = event.target;
    const card = field.closest(".form-fold");
    // Restore focusability synchronously, before the browser handles validation.
    if (card) setExpanded(card, true, true);
    if (firstInvalid) return;
    firstInvalid = field;
    requestAnimationFrame(() => {
      const target = firstInvalid;
      firstInvalid = null;
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: "center", behavior: "auto" });
    });
  }, true);
  syncFoldStatus();

  const viewport = window.visualViewport;
  const footer = recordForm.querySelector(".form-footer");
  let focusFrame = 0;
  let viewportSettleTimer = 0;

  function keepFocusVisible() {
    if (page.hidden || !matchMedia("(max-width: 599px)").matches || focusFrame) return;
    focusFrame = requestAnimationFrame(() => {
      focusFrame = 0;
      const field = document.activeElement;
      if (page.hidden || !recordForm.contains(field) || !field.matches("input, select, textarea")) return;
      const visualTop = viewport?.offsetTop || 0;
      const visualBottom = visualTop + (viewport?.height || innerHeight);
      const progressRect = page.querySelector("#form-section-progress").getBoundingClientRect();
      const top = Math.max(visualTop, progressRect.top <= visualTop + 1 ? progressRect.bottom : visualTop) + 8;
      const bottom = Math.min(visualBottom, footer.getBoundingClientRect().top) - 8;
      const rect = (field.closest("label") || field).getBoundingClientRect();
      let delta = 0;
      if (rect.height > bottom - top) {
        if (rect.top < top || rect.top > bottom - 44) delta = rect.top - top;
      } else if (rect.top < top) delta = rect.top - top;
      else if (rect.bottom > bottom) delta = rect.bottom - bottom;
      if (Math.abs(delta) > 1) scrollBy({ top: delta, behavior: "auto" });
    });
  }
  function syncViewport() {
    const keyboardInset = viewport ? Math.max(0, innerHeight - viewport.height - viewport.offsetTop) : 0;
    page.style.setProperty("--form-keyboard-inset", `${keyboardInset}px`);
    keepFocusVisible();
    // Native keyboard panning can finish after the first viewport resize frame.
    clearTimeout(viewportSettleTimer);
    viewportSettleTimer = setTimeout(keepFocusVisible, 200);
  }
  recordForm.addEventListener("focusin", keepFocusVisible);
  addEventListener("resize", syncViewport);
  viewport?.addEventListener("resize", syncViewport);
  viewport?.addEventListener("scroll", syncViewport);
  syncViewport();
})();
