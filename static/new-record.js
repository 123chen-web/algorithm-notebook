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
})();
