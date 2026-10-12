"use strict";

/* 让浏览器的“后退/前进”在站内各页面之间生效。
   站内所有页面（总览、复习、笔记、学习路线……）共用同一个地址 #/app，
   以前切换页面只改显示、不留浏览器历史，所以点“后退”会直接离开网站。
   做法：每次站内换页（app.js 会派发 app:view-changed）就压入一格历史，
   后退时（popstate）调用 showView 回到上一页，且这次回退不再重复压入历史。
   - 登录后的第一个页面只给当前历史格打标记，不压入新的一格（否则后退会回到“登录前”的怪状态）；
   - 退出登录、回到欢迎页或登录页后重新“待命”；
   - 只处理带 oyView 标记的历史格，画板面板用的 oyDraw 历史格不归这里管。 */
(() => {
  let armed = false;      // 当前这次登录里，是否已经给基准历史格打过标记
  let restoring = false;  // 正在响应后退/前进，这期间的换页不再压历史
  let current = "";       // 当前页面名

  const inApp = () => document.documentElement.dataset.view === "app";

  document.addEventListener("app:view-changed", (event) => {
    const view = event?.detail?.view;
    if (!view || !inApp()) return;
    const previous = current;
    current = view;
    if (restoring) return;
    if (!armed) {
      armed = true;
      history.replaceState({ ...(history.state || {}), oyView: view }, "", location.href);
      return;
    }
    if (view === previous) return;
    history.pushState({ oyView: view }, "", location.href);
  });

  window.addEventListener("popstate", (event) => {
    const target = event.state?.oyView;
    if (typeof target !== "string" || !inApp() || target === current) return;
    if (typeof window.showView !== "function") return;
    restoring = true;
    Promise.resolve(window.showView(target, { refreshUser: false }))
      .catch(() => {})
      .finally(() => { restoring = false; });
  });

  // 离开应用（退出登录、回到欢迎页/登录页）后重新待命。
  new MutationObserver(() => {
    if (!inApp()) { armed = false; current = ""; }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-view"] });
})();
