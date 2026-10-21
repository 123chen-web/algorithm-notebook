"use strict";

/* 「更新了什么」：读同源的 static/data/changelog.json，只用 createElement / textContent 渲染。
   两处用到：
   - 帮助页 #whats-new-list：渲染最近 5 次更新，章节进入视野后记下“已看过”；
   - 主页侧栏的 [data-whatsnew-dot]：最新一次更新比记下的日期新，就亮一个小圆点。
   localStorage 读写全部 try/catch，用不了就当没看过，不影响任何功能。 */

(() => {
  const CHANGELOG_URL = "/static/data/changelog.json";
  const SEEN_KEY = "oy-whatsnew-seen";
  const SHOW_COUNT = 5;
  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

  function readSeen() {
    try {
      return window.localStorage.getItem(SEEN_KEY) || "";
    } catch (_error) {
      return "";
    }
  }

  function writeSeen(date) {
    try {
      window.localStorage.setItem(SEEN_KEY, date);
    } catch (_error) {
      /* 无痕模式等：忽略 */
    }
  }

  function cleanReleases(data) {
    if (!Array.isArray(data)) return [];
    return data
      .filter((release) => release && DATE_RE.test(String(release.date)) && Array.isArray(release.items))
      .map((release) => ({
        date: String(release.date),
        title: String(release.title || ""),
        items: release.items.filter((item) => typeof item === "string" && item.trim() !== ""),
      }));
  }

  function setDots(visible) {
    document.querySelectorAll("[data-whatsnew-dot]").forEach((dot) => {
      dot.hidden = !visible;
    });
  }

  function renderReleases(container, releases) {
    const nodes = releases.slice(0, SHOW_COUNT).map((release) => {
      const block = document.createElement("article");
      block.className = "whatsnew-release";
      const heading = document.createElement("h3");
      const time = document.createElement("time");
      time.setAttribute("datetime", release.date);
      time.textContent = release.date;
      heading.append(time);
      if (release.title) heading.append(` · ${release.title}`);
      const list = document.createElement("ul");
      release.items.forEach((text) => {
        const item = document.createElement("li");
        item.textContent = text;
        list.append(item);
      });
      block.append(heading, list);
      return block;
    });
    container.replaceChildren(...nodes);
  }

  function showFailure(container) {
    const note = document.createElement("p");
    note.className = "field-hint whatsnew-note";
    note.textContent = "更新记录暂时没加载出来，稍后刷新页面再试。";
    container.replaceChildren(note);
  }

  /** 章节被看到（或直接用 #whats-new 链接进来）时，把最新日期记为已看过。 */
  function markSeenWhenOpened(section, latest) {
    const markSeen = () => {
      writeSeen(latest);
      setDots(false);
    };
    if (window.location && window.location.hash === "#whats-new") {
      markSeen();
      return;
    }
    if (typeof window.IntersectionObserver !== "function") {
      markSeen();
      return;
    }
    const observer = new window.IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect();
        markSeen();
      }
    });
    observer.observe(section);
  }

  async function run() {
    const container = document.getElementById("whats-new-list");
    let releases = [];
    try {
      const response = await window.fetch(CHANGELOG_URL, { credentials: "same-origin" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      releases = cleanReleases(await response.json());
    } catch (_error) {
      if (container) showFailure(container);
      return;
    }
    const latest = releases.length ? releases[0].date : "";
    if (container) {
      if (latest) renderReleases(container, releases);
      else showFailure(container);
      const section = document.getElementById("whats-new");
      if (latest && section) markSeenWhenOpened(section, latest);
    }
    if (latest && !container) setDots(latest > readSeen());
  }

  window.WhatsNew = { run };
  run();
})();
