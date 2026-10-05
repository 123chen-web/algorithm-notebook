/* nav-focus.js —— 专注模式（FOCUS-RANKNAV）。
 *
 * 开启后侧栏 / 手机“更多”抽屉只保留学习入口，手机底栏“小组”换成“分析”，
 * 总览页收起社区与套餐相关卡片；被收起的页面仍可直接打开，页面顶部出现轻提示。
 * 状态按用户 id 存在 localStorage，任何存储异常都按“关闭”处理，绝不抛出。
 * 不碰网络、不拼 HTML：用户可见文字只用 textContent，样式全部在 nav-focus.css。
 */
(function () {
  "use strict";

  const STORAGE_PREFIX = "nav-focus:";
  // 专注模式收起的视图（任务点名，不多收也不漏收）。
  const HIDDEN_VIEWS = ["groups", "forum", "leaderboard", "achievements", "weekly-recap", "plan"];
  // 总览页上与社区 / 套餐相关的卡片。
  const HOME_HIDDEN_CARDS = ["ov-groups-card", "ov-hot-card"];
  const MARK = "navFocusHidden";
  const LABEL_ON = "专注模式：开";
  const LABEL_OFF = "专注模式：关";

  let hooks = { getUser: () => null, getView: () => null };
  let enabled = false;
  let userId = null;

  function storageKey(id) { return STORAGE_PREFIX + id; }

  function readStored(id) {
    try {
      return window.localStorage.getItem(storageKey(id)) === "1";
    } catch (error) {
      return false;
    }
  }

  function writeStored(id, on) {
    try {
      if (on) {
        window.localStorage.setItem(storageKey(id), "1");
      } else {
        window.localStorage.removeItem(storageKey(id));
      }
      return true;
    } catch (error) {
      return false;
    }
  }

  function isHiddenView(view) {
    return HIDDEN_VIEWS.indexOf(view) !== -1;
  }

  function markHidden(element, on) {
    if (!element) return;
    if (on) {
      if (!element.hidden) {
        element.dataset[MARK] = "1";
        element.hidden = true;
      }
    } else if (element.dataset[MARK] === "1") {
      delete element.dataset[MARK];
      element.hidden = false;
    }
  }

  function renderToggles() {
    document.querySelectorAll(".nav-focus-toggle").forEach((button) => {
      button.setAttribute("aria-pressed", enabled ? "true" : "false");
      const label = button.querySelector(".nav-focus-label");
      if (label) label.textContent = enabled ? LABEL_ON : LABEL_OFF;
      else button.textContent = enabled ? LABEL_ON : LABEL_OFF;
    });
  }

  function renderChrome() {
    // 侧栏条目与“更多”抽屉条目。
    document.querySelectorAll(".app-sidebar .nav-item[data-view], .more-item[data-view]").forEach((item) => {
      markHidden(item, enabled && isHiddenView(item.dataset.view));
    });
    // 社区分组整组收起时连分组标题一起收起；组里还有条目则保留标题。
    document.querySelectorAll(".app-sidebar .sidebar-group").forEach((group) => {
      const items = Array.prototype.slice.call(group.querySelectorAll(".nav-item[data-view]"));
      const collapse = enabled && items.length > 0 && items.every((item) => item.hidden);
      markHidden(group, collapse);
    });
    // 总览页社区 / 套餐卡片。
    HOME_HIDDEN_CARDS.forEach((id) => {
      markHidden(document.getElementById(id), enabled);
    });
    renderPhoneTab();
  }

  function renderPhoneTab() {
    const tab = document.getElementById("tab-groups");
    if (!tab) return;
    const label = tab.querySelector(".tab-label");
    const groupsIcon = tab.querySelector('[data-nav-focus-icon="groups"]');
    const insightsIcon = tab.querySelector('[data-nav-focus-icon="insights"]');
    tab.dataset.view = enabled ? "insights" : "groups";
    if (label) label.textContent = enabled ? "分析" : "小组";
    if (groupsIcon) groupsIcon.hidden = enabled;
    if (insightsIcon) insightsIcon.hidden = !enabled;
  }

  function renderNotice() {
    const notice = document.getElementById("nav-focus-notice");
    if (!notice) return;
    const view = hooks.getView ? hooks.getView() : null;
    notice.hidden = !(enabled && isHiddenView(view));
  }

  function render() {
    renderToggles();
    renderChrome();
    renderNotice();
  }

  function setEnabled(next) {
    const wanted = next === true;
    if (wanted && userId == null) return; // 未登录不写键，也不开启。
    if (wanted && !writeStored(userId, true)) {
      enabled = false; // 存储不可用（隐私模式 / 配额 / 被禁用）：当作关闭。
    } else {
      if (!wanted) writeStored(userId, false);
      enabled = wanted;
    }
    render();
  }

  function configure(nextHooks) {
    hooks = Object.assign({}, hooks, nextHooks || {});
    const user = hooks.getUser ? hooks.getUser() : null;
    const nextId = user && user.id != null ? user.id : null;
    if (nextId !== userId) {
      userId = nextId;
      enabled = nextId != null && readStored(nextId);
    }
    render();
  }

  function reset() {
    enabled = false;
    userId = null;
    render();
  }

  function isEnabled() {
    return enabled === true;
  }

  function bindStaticControls() {
    document.addEventListener("click", (event) => {
      const toggle = event.target.closest ? event.target.closest(".nav-focus-toggle") : null;
      if (toggle) {
        event.preventDefault();
        setEnabled(!enabled);
        return;
      }
      const close = event.target.closest ? event.target.closest("#nav-focus-notice-close") : null;
      if (close) {
        event.preventDefault();
        setEnabled(false);
      }
    });
    document.addEventListener("app:view-changed", renderNotice);
  }

  window.NavFocus = { configure, reset, isEnabled, isHiddenView };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bindStaticControls);
  } else {
    bindStaticControls();
  }
})();
