"use strict";

(() => {
  const STORAGE_KEY = "algorithm-notebook-theme";
  const root = document.documentElement;
  // 10/18 起默认“清爽”；用户自己选过的（存在本机）照旧。
  const THEMES = ["bright", "fog", "ink", "qixi"];
  let theme = "bright";
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (THEMES.includes(saved)) theme = saved;
  } catch (_) { /* Storage may be unavailable; bright remains the default. */ }
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
        if (!THEMES.includes(select.value)) return;
        theme = select.value;
        root.dataset.theme = theme;
        try { localStorage.setItem(STORAGE_KEY, theme); } catch (_) { /* Keep the live selection. */ }
        syncControls();
      });
    });
  }, { once: true });
})();
