"use strict";

(() => {
  const intro = document.getElementById("intro");
  const app = document.getElementById("app");
  if (!intro || !app) return;

  const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  const timers = new Set();
  let openingPlayed = false;
  let openingActive = false;
  let restoreTitle = null;

  const canAnimate = () => !intro.hidden && app.hidden && !document.hidden
    && !motionQuery.matches && intro.getClientRects().length > 0
    && !window.IntroFilm?.ownsOpening(); // 本次加载放过开场短片时不再叠一段欢迎页动效。

  const later = (callback, delay) => {
    const timer = window.setTimeout(() => {
      timers.delete(timer);
      callback();
    }, delay);
    timers.add(timer);
  };

  const finishOpening = () => {
    if (!openingActive) return;
    openingActive = false;
    timers.forEach((timer) => window.clearTimeout(timer));
    timers.clear();
    intro.classList.remove("intro-anim");
    restoreTitle?.();
    restoreTitle = null;
    document.removeEventListener("visibilitychange", reconcileOpening);
    motionQuery.removeEventListener("change", reconcileOpening);
    window.removeEventListener("pagehide", finishOpening);
  };

  const startOpening = () => {
    if (openingPlayed || !canAnimate()) return;
    openingPlayed = true;
    openingActive = true;
    const title = document.getElementById("intro-title");
    let characterCount = 0;
    if (title) {
      const originalNodes = Array.from(title.childNodes);
      const originalLabel = title.getAttribute("aria-label");
      const fragment = document.createDocumentFragment();
      title.setAttribute("aria-label", title.textContent);
      originalNodes.forEach((node) => {
        if (node.nodeName === "BR") {
          fragment.append(node.cloneNode());
          return;
        }
        Array.from(node.textContent).forEach((character) => {
          const span = document.createElement("span");
          span.className = "intro-char";
          span.textContent = character;
          span.setAttribute("aria-hidden", "true");
          span.style.setProperty("--ink-delay", `${characterCount * 70}ms`);
          fragment.append(span);
          characterCount += 1;
        });
      });
      title.replaceChildren(fragment);
      restoreTitle = () => {
        title.replaceChildren(...originalNodes);
        if (originalLabel === null) title.removeAttribute("aria-label");
        else title.setAttribute("aria-label", originalLabel);
      };
    }
    intro.classList.add("intro-anim");
    later(() => {
      if (!canAnimate()) {
        finishOpening();
        return;
      }
      const seal = intro.querySelector(".sample-status");
      if (!seal) return;
      const bounds = seal.getBoundingClientRect();
      window.CursorFX?.drop?.(bounds.left + bounds.width / 2,
        bounds.top + bounds.height / 2, 1.1);
    }, 2200); // The seal starts at 1.9s and touches the paper about .3s later.
    later(finishOpening, Math.max(2400, (characterCount - 1) * 70 + 750));
  };

  function reconcileOpening() {
    if (openingActive && !canAnimate()) finishOpening();
    else startOpening();
  }

  // 路由负责视图可见性；这里仅管理一次性开场动画。
  const observer = new MutationObserver(reconcileOpening);
  observer.observe(app, { attributes: true, attributeFilter: ["hidden"] });
  observer.observe(intro, { attributes: true, attributeFilter: ["hidden"] });
  document.addEventListener("visibilitychange", reconcileOpening);
  motionQuery.addEventListener("change", reconcileOpening);
  window.addEventListener("pagehide", finishOpening);
  reconcileOpening();
})();
