"use strict";

/* 工作台外壳：侧栏/底栏的当前页高亮与角标、分区列表、复习日历、"更多"抽屉。
   对外契约：window.AppShell = { setActive, setCounts, setZones, setStreak, setUser, mountCalendar, openMore, closeMore, reset }。
   视图切换由 app.js 完成，这里只监听 app:view-changed；入口按钮都带 data-view，点击逻辑在 app.js。 */
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const desktop = window.matchMedia("(min-width: 1024px)");
  const sheet = $("#more-sheet");
  const moreButton = $("#tab-more");
  const panel = sheet ? $(".more-panel", sheet) : null;
  let returnFocus = null;

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  /* ---------- 当前页 ---------- */
  function setActive(view) {
    for (const button of document.querySelectorAll(".app-sidebar [data-view], .app-tabbar [data-view], .more-sheet [data-view]")) {
      if (button.dataset.view === view) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    // "总览"在手机上收进了"更多"，所以进入这些页面时让"更多"也亮着。
    const inTabbar = Boolean($(`.app-tabbar [data-view="${view}"]`));
    if (moreButton) {
      if (!inTabbar && view !== "new") moreButton.setAttribute("aria-current", "page");
      else moreButton.removeAttribute("aria-current");
    }
    closeMore({ restore: false });
  }

  /* ---------- 角标 ---------- */
  function setBadge(name, count, { suffix = "", showZero = false } = {}) {
    for (const badge of document.querySelectorAll(`[data-badge="${name}"]`)) {
      const visible = Number.isInteger(count) && (count > 0 || showZero);
      badge.hidden = !visible;
      badge.textContent = visible ? (count > 99 ? "99+" : String(count)) : "";
      const owner = badge.closest("button");
      if (owner) {
        const base = owner.dataset.label || owner.querySelector(".nav-label, .tab-label")?.textContent || "";
        owner.dataset.label = base;
        owner.setAttribute("aria-label", visible ? `${base}，${count} ${suffix}` : base);
      }
    }
  }
  function setCounts(data) {
    if (!data) return;
    setBadge("today", data.due_count, { suffix: "条待复习" });
    setBadge("all", data.total_mistakes, { suffix: "条易错点" });
    setBadge("groups", data.group_count, { suffix: "个小组" });
  }

  /* ---------- 分区列表 ---------- */
  function setZones(zones) {
    const list = $("#sidebar-zones");
    const section = $("#sidebar-zones-section");
    if (!list || !section) return;
    list.replaceChildren();
    for (const item of zones || []) {
      const entry = node("li");
      const button = node("button", "");
      button.type = "button";
      button.dataset.zone = item.zone;
      button.title = `查看「${item.zone}」的全部记录`;
      const dot = node("span", "zone-dot");
      dot.dataset.zone = item.zone;
      dot.setAttribute("aria-hidden", "true");
      button.append(dot, node("span", "zone-name", item.zone), node("span", "zone-count", String(item.total)));
      button.addEventListener("click", () => {
        document.dispatchEvent(new CustomEvent("records:filter", { detail: { zone: item.zone } }));
      });
      entry.append(button);
      list.append(entry);
    }
    section.hidden = !(zones && zones.length);
  }

  function setStreak(days) {
    const badge = $("#sidebar-streak");
    if (!badge) return;
    badge.hidden = !(Number.isInteger(days) && days > 0);
    badge.textContent = badge.hidden ? "" : `连续 ${days} 天`;
  }

  let refreshTimer = 0;
  let epoch = 0; // 登出/换账号时加一：旧账号发出的请求回来也不能改新账号的侧栏
  function refreshCounts() {
    window.clearTimeout(refreshTimer);
    refreshTimer = window.setTimeout(async () => {
      if (document.documentElement.dataset.view !== "app") return;
      const startedIn = epoch;
      try {
        const response = await fetch("/api/overview", { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } });
        if (!response.ok) return;
        const data = await response.json();
        if (startedIn !== epoch) return;
        setCounts(data);
        setZones(data.zones);
        setStreak(data.streak_days);
      } catch {
        // 角标取不到就保持原样，下次操作再试。
      }
    }, 400);
  }

  function setUser(name) {
    const target = $("#sidebar-username");
    if (target) target.textContent = name || "";
  }

  /* ---------- 复习日历（侧栏） ---------- */
  let calendarMounted = false;
  function mountCalendar() {
    calendarMounted = true;
    // 侧栏的"错因标签"和"全部记录"页的标签下拉也在登录后第一次进入时挂上。
    window.TagFilters?.mountSidebar($("#sidebar-tags"));
    window.TagFilters?.bindSelect($("#tag-filter"));
    const mount = $("#sidebar-calendar");
    const section = $("#sidebar-calendar-section");
    if (!mount || !section) return;
    if (!window.ActivityWidgets) {
      section.hidden = true;
      return;
    }
    section.hidden = false;
    window.ActivityWidgets.mountHeatmap(mount, {
      weeks: 5,
      calendar: true,
      onSelectDay: (date) => document.dispatchEvent(new CustomEvent("records:filter", { detail: { created_on: date } })),
      onData: (data) => setStreak(data.streak_days),
    });
  }

  /* ---------- "更多"抽屉 ---------- */
  function focusables() {
    return [...sheet.querySelectorAll("button:not(:disabled):not([hidden])")].filter((item) => item.offsetParent !== null);
  }
  function openMore() {
    if (!sheet || !sheet.hidden || desktop.matches) return;
    returnFocus = document.activeElement;
    sheet.hidden = false;
    document.body.classList.add("sheet-open");
    moreButton?.setAttribute("aria-expanded", "true");
    panel?.focus({ preventScroll: true });
  }
  function closeMore({ restore = true } = {}) {
    if (!sheet || sheet.hidden) return;
    sheet.hidden = true;
    document.body.classList.remove("sheet-open");
    moreButton?.setAttribute("aria-expanded", "false");
    if (restore) {
      const target = returnFocus?.isConnected && returnFocus.offsetParent !== null ? returnFocus : moreButton;
      target?.focus();
    }
    returnFocus = null;
  }

  moreButton?.addEventListener("click", () => (sheet.hidden ? openMore() : closeMore()));
  sheet?.addEventListener("click", (event) => {
    if (event.target.closest("[data-more-close]")) closeMore();
  });
  sheet?.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    const items = focusables();
    if (!items.length) {
      event.preventDefault();
      panel.focus();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    if (event.shiftKey && (document.activeElement === first || document.activeElement === panel)) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sheet && !sheet.hidden) {
      event.preventDefault();
      closeMore();
    }
  });
  desktop.addEventListener("change", () => { if (desktop.matches) closeMore({ restore: false }); });
  $("#more-refresh")?.addEventListener("click", () => {
    closeMore({ restore: false });
    $("#refresh")?.click();
  });

  /* ---------- 搜索入口：命令面板存在才显示 ---------- */
  const searchTrigger = $("#app-search-trigger");
  function syncSearch() {
    if (searchTrigger) searchTrigger.hidden = !window.CommandPalette;
  }
  searchTrigger?.addEventListener("click", () => window.CommandPalette?.open());
  document.addEventListener("DOMContentLoaded", syncSearch);
  document.addEventListener("app:view-changed", syncSearch);
  document.addEventListener("app:data-changed", refreshCounts);

  document.addEventListener("app:view-changed", (event) => {
    setActive(event.detail.view);
    if (!calendarMounted) mountCalendar();
  });

  function reset() {
    epoch += 1;
    calendarMounted = false;
    closeMore({ restore: false });
    setBadge("today", 0);
    setBadge("all", 0);
    setBadge("groups", 0);
    setZones([]);
    setStreak(0);
    setUser("");
    window.clearTimeout(refreshTimer);
    window.TagFilters?.reset();
    window.Mastery?.reset();
    window.Clusters?.reset();
    window.Account?.reset();
    window.PrintNotebook?.reset();
    window.ActivityWidgets?.reset();
  }

  window.AppShell = { setActive, setCounts, setZones, setStreak, setUser, mountCalendar, refreshCounts, openMore, closeMore, reset };
})();
