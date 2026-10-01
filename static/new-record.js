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
})();
