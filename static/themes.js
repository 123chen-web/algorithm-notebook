"use strict";

(() => {
  const STORAGE_KEY = "algorithm-notebook-theme";
  const root = document.documentElement;
  let theme = "ink";
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "ink" || saved === "qixi") theme = saved;
  } catch (_) { /* Storage may be unavailable; ink remains the default. */ }
  root.dataset.theme = theme;

  function syncControls() {
    document.querySelectorAll("[data-theme-select]").forEach((select) => {
      select.value = theme;
    });
    document.querySelectorAll(".account-preference:has([data-scene-select])").forEach((label) => {
      label.hidden = theme !== "qixi";
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    syncControls();
    document.querySelectorAll("[data-theme-select]").forEach((select) => {
      select.addEventListener("change", () => {
        if (select.value !== "ink" && select.value !== "qixi") return;
        theme = select.value;
        root.dataset.theme = theme;
        try { localStorage.setItem(STORAGE_KEY, theme); } catch (_) { /* Keep the live selection. */ }
        syncControls();
      });
    });
  }, { once: true });
})();
