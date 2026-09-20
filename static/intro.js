"use strict";

(() => {
  const intro = document.getElementById("intro");
  const app = document.getElementById("app");
  if (!intro || !app) return;

  // 只跟随现有界面的显示状态，不读 Cookie，也不调用认证接口。
  const sync = () => { intro.hidden = !app.hidden; };
  const observer = new MutationObserver(sync);
  observer.observe(app, { attributes: true, attributeFilter: ["hidden"] });
  sync();
})();
