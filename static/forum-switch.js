"use strict";

/* 讨论区冻结开关的前端部分。
   对外契约：window.ForumSwitch = { isEnabled, apply }。
   - isEnabled(features)：features 是 GET /api/me 返回的 features 对象；缺省视为开启。
   - apply(features)：按开关隐藏所有 data-view="forum" 的入口（侧栏、手机"更多"抽屉等）。
     命令面板的页面条目是从侧栏读的（自动跳过 hidden），帖子搜索结果由后端 /api/search
     在关闭时直接置空，这里再按侧栏按钮状态做一道前端过滤。
   所有用户文本一律走 textContent；本文件不拼 HTML。 */
(() => {
  function isEnabled(features) {
    return !features || features.forum !== false;
  }

  function apply(features) {
    const enabled = isEnabled(features);
    for (const button of document.querySelectorAll('[data-view="forum"]')) {
      button.hidden = !enabled;
    }
    return enabled;
  }

  // 命令面板用：侧栏的讨论区按钮被隐藏时，不再展示帖子分组。
  function postsVisible() {
    const button = document.querySelector('.app-sidebar .nav-item[data-view="forum"]');
    return !button || !button.hidden;
  }

  window.ForumSwitch = { isEnabled, apply, postsVisible };
})();
