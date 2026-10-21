// static/app.js — 应用启动文件（C2 领域拆分后）。
//
// 原 app.js 的 174 个函数与 wiring 块已按领域拆到 static/app/<领域>.js；
// 本文件只保留必须在最后执行的启动代码。脚本加载顺序（defer，按文档顺序执行）
// 见 static/index.html：
//   1. static/app/core-api.js / core-session.js / core-ui.js — window.OY、api/会话/路由/消息/认证/DOM 工具
//   2. static/app/overview-growth.js / overview-home.js / overview-insights.js — 总览（today/成就/周报/薄弱点/印章）
//   3. static/app/review-list.js / review-detail.js / review-grade.js — 复习与专注
//   4. static/app/problem-form.js — 记录表单 + 拍照
//   5. static/app/forum-board.js / forum-post.js / forum-comments.js / forum-map.js — 论坛
//   6. static/app/groups.js       — 小组
//   7. static/app/rank.js         — 榜单
//   8. static/app/account.js      — 账号设置
//   9. static/app/admin.js        — 管理后台
//   10. static/app/payments.js    — 支付
//   11. static/app/ai.js          — AI 反馈
//   12. static/app.js             — 本文件：应用启动（最后执行）
//
// 领域间调用保持顶层全局函数名直接调用；各文件共享全局词法作用域。
"use strict";

// ---- 启动（原文件尾部；依赖全部领域文件已求值，必须最后执行） ----
window.ShareTarget?.intake(); // 手机“分享到欧叶”带来的 ?title&text&url，先收下并从地址栏去掉
window.Capture?.intake();
const emailToken = new URLSearchParams(location.search).get("email_token");
if (emailToken) {
  // 从确认更换邮箱的邮件点进来的：先清掉地址栏里的令牌，再提交确认。
  const url = new URL(location.href);
  url.searchParams.delete("email_token");
  history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  api("/api/me/email/confirm", { method: "POST", body: JSON.stringify({ token: emailToken }) })
    .then((result) => {
      if (user) user.email = result.email;
      message("新邮箱已生效。");
    })
    .catch((error) => message(error.message, true));
}
if (resetToken) {
  // 从密码重置邮件点进来的，不管当前是否登录，先处理重置。
  showAuthPanels(["reset-form"]);
  sessionReady = true;
  renderPageRoute();
} else {
  run(async () => {
    try {
      await enterApp();
    } catch (error) {
      if (error.status !== 401) {
        sessionReady = true;
        renderPageRoute();
        throw error;
      }
    }
  });
}
