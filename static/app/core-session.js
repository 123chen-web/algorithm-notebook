// static/app/core-session.js — 核心·会话层：认证/路由/视图切换/登录态事件接线。
// 由 static/app/core.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

const AUTH_PANELS = ["login-form", "register-form", "forgot-form", "reset-form"];

function renderPageRoute() {
  if (!sessionReady) return;
  const next = user ? "app" : location.hash === "#/welcome" ? "welcome"
    : resetToken || location.hash === "#/auth" ? "auth" : "welcome";
  document.documentElement.dataset.view = next;
  $("#intro").hidden = next !== "welcome";
  $("#auth").hidden = next !== "auth";
  $("#app").hidden = next !== "app";
  if (location.hash !== `#/${next}`) {
    history.replaceState(null, "", `${location.pathname}${location.search}#/${next}`);
  }
  // 首访开场短片只在登录状态检测完成后、第一次落到欢迎页时播放（见 intro-film.js）。
  window.IntroFilm?.route(next);
}

window.addEventListener("hashchange", () => {
  message();
  const captured = window.Capture?.intake(); // 先收下书签参数，renderPageRoute 会把地址栏改回 #/app
  renderPageRoute();
  if (captured && user) run(applyCapturePending);
});

function showAuthPanels(visibleIds) {
  for (const id of AUTH_PANELS) {
    $(`#${id}`).hidden = !visibleIds.includes(id);
  }
  const tabs = $("#auth-switch");
  tabs.hidden = !visibleIds.some((id) => id === "login-form" || id === "register-form");
  $("#auth-trial-start").hidden = tabs.hidden;
  tabs.querySelectorAll("[data-auth-panel]").forEach((tab) => {
    const selected = visibleIds.includes(tab.dataset.authPanel);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  });
}

$("#auth-switch").addEventListener("click", (event) => {
  const tab = event.target.closest("[data-auth-panel]");
  if (!tab) return;
  message();
  showAuthPanels([tab.dataset.authPanel]);
});
$("#auth-switch").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  event.preventDefault();
  const tabs = [...event.currentTarget.querySelectorAll("[data-auth-panel]")];
  const current = tabs.indexOf(document.activeElement);
  const index = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1
    : (current + (event.key === "ArrowLeft" ? -1 : 1) + tabs.length) % tabs.length;
  tabs[index].click();
  tabs[index].focus();
});

function secClearEmailForm() {
  $("#email-prompt").reset();
}

function signedOut() {
  sessionEpoch += 1;
  secClearEmailForm();
  rvfPageGeneration += 1;
  rvfClearDetail();
  rvfListGeneration += 1;
  rvfHeaderGeneration += 1;
  rvfListItems.clear();
  rvfQueue = null;
  window.ReviewExtras?.reset();
  window.Onboarding?.reset();
  window.OfflineReview?.onSignedOut(); // 先提示队列保留，再 reset（IndexedDB 里该用户的队列仍在）
  window.OfflineSync?.reset();
  window.OfflineReview?.reset();
  if (user) window.Capture?.reset(); // 真正的登出才清；启动时 401 不能丢掉书签带来的预填
  window.Account?.reset();
  window.Profile?.reset();
  window.AnkiExport?.reset();
  window.ImportWizard?.reset();
  finishHomeOpening?.();
  for (const entry of [...sealStamps]) entry.remove();
  stopOrderPolling();
  resetWeaknessAnalysis();
  resetAchievements();
  resetWeeklyRecap();
  resetGroups();
  planPurchase = null;
  planCatalog = [];
  $("#plan-subscription").replaceChildren();
  $("#plan-list").replaceChildren();
  $("#plan-orders-list").replaceChildren();
  $("#plan-payment").replaceChildren();
  $("#plan-order-details").replaceChildren();
  $("#plan-order-status").textContent = "";
  $("#plan-order").hidden = true;
  window.FocusReview?.close();
  user = null;
  window.EmojiPicker?.close();
  window.CommandPalette?.close({ restore: false });
  document.body.classList.remove("home-view");
  $("#home-page").hidden = true;
  resetHomeSummary();
  window.AppShell?.reset(); // 侧栏角标、日历、热力图缓存都属于上一位用户
  sessionReady = true;
  renderPageRoute();
  $("#logout").hidden = true;
  $("#user-info-wrap").replaceChildren();
  closeAccountMenu();
  $("#my-avatar-wrap").hidden = true;
  $("#avatar-file-input").value = "";
  $("#email-prompt").hidden = true;
  $("#trial-banner").hidden = true;
  showAuthPanels(resetToken ? ["reset-form"] : ["login-form"]);
  $("#cards").replaceChildren();
  $("#detail").replaceChildren();
  $("#leaderboard-me").replaceChildren();
  $("#leaderboard-entries").replaceChildren();
  $("#leaderboard-status").textContent = "";
  $("#leaderboard-table-wrap").hidden = true;
  $("#leaderboard-page").setAttribute("aria-busy", "false");
  forumPost = null;
  forumDetailGeneration += 1;
  forumSummaryController?.reset();
  forumCodeOnly = false;
  forumMentionOnly = false;
  forumCurrentComment = null;
  forumMutations.clear();
  setForumPreview(false);
  forumCommentOrder = "earliest";
  forumOnlyOp = false;
  clearForumReply();
  forumListGeneration += 1;
  window.Board?.reset(); // 列表、搜索词、发帖表单都属于上一位用户（草稿按用户 id 存在 localStorage 里）
  window.Notes?.reset(); // 笔记列表、筛选和题目下拉同样属于上一位用户
  window.NotesLinks?.reset(); // 关联区与图谱属于上一位用户，登出一并还原
  window.DrawHost?.reset(); // 画板列表与打开中的面板也属于上一位用户
  window.NotesEditor?.reset(); // 所见即所得编辑器实例与草稿表面一并复位
  $("#forum-post").replaceChildren();
  $("#forum-comments").replaceChildren();
  $("#forum-comment-form").reset();
  document.querySelectorAll("[data-admin-only]").forEach((item) => { item.hidden = true; });
  resetAdminDashboard();
  window.AdminMetrics?.reset();
  window.RankAdmin?.reset();
  window.Rank?.reset();
  window.GoalCard?.reset();
  window.RecommendCard?.reset();
  window.SignCard?.reset();
  window.BriefingCard?.reset();
  window.Landscape?.reset();
  window.DuckPanel?.reset();
  window.Reminder?.reset();
  window.Heatmap?.reset();
  window.Similar?.reset();
  window.ImportProblem?.reset();
  window.Scratch?.reset();
  window.CodeRunner?.reset();
window.PushSettings?.reset();
  window.NavFocus?.reset(); // 专注模式开关属于已注销的用户，收起状态与横幅一并还原
  window.Typical?.reset(); // 典型失误卡与一页纸属于已注销的用户，请求代次一并作废
  window.Roadmap?.reset(); // 学习路线的进度属于已注销的用户，一并作废
  window.ManualClaims?.reset();
  window.ManualClaimsAdmin?.reset();
  $("#admin-reports").replaceChildren();
  $("#admin-status").textContent = "";
  $("#problem-form").reset();
  resetQuickMode(); // 登出时如正处速记模式，一并恢复普通模式，避免界面卡在隐藏状态
  $("#mistake-inputs").replaceChildren();
  addMistakeInput();
  resetPhotoForm();
}

async function enterApp() {
  secClearEmailForm();
  resetWeaknessAnalysis();
  resetAchievements();
  resetWeeklyRecap();
  resetGroups();
  user = await api("/api/me");
  sessionEpoch += 1;
  window.Onboarding?.reset(user);
  window.OfflineSync?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  window.OfflineReview?.configure({ notify: (text) => message(text), getUser: () => user, getEpoch: () => sessionEpoch });
  window.OfflineSync?.prefetch(); // 静默预取今日复习队列，断网时兜底
  if (resetToken) {
    resetToken = null;
    const url = new URL(location.href);
    url.searchParams.delete("reset_token");
    history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }
  sessionReady = true;
  renderPageRoute();
  $("#logout").hidden = false;
  updateUserInfo();
  await loadZones();
  if (window.Capture?.hasPending()) await applyCapturePending();
  else await showView("home", { refreshUser: false });
}

async function showView(nextView, { refreshUser = true } = {}) {
  stopOrderPolling();
  if (view !== nextView) {
    finishHomeOpening?.();
    cancelAchievementStamps();
    if (view === "achievements") achievementsGeneration += 1;
  }
  if (view === "groups" && nextView !== "groups") groupsGeneration += 1;
  const changedView = view !== nextView;
  if (changedView) {
    rvfPageGeneration += 1;
    rvfListGeneration += 1;
    rvfHeaderGeneration += 1;
    rvfClearDetail();
    window.ReviewExtras?.reset();
  }
  view = nextView;
  document.body.classList.toggle("home-view", view === "home");
  $("#home-page").hidden = view !== "home";
  $("#new-page").hidden = view !== "new";
  $("#list-page").hidden = view !== "today" && view !== "all";
  $("#plan-page").hidden = view !== "plan";
  $("#leaderboard-page").hidden = view !== "leaderboard";
  $("#weakness-page").hidden = view !== "insights";
  $("#achievements-page").hidden = view !== "achievements";
  $("#weekly-recap-page").hidden = view !== "weekly-recap";
  $("#mastery-page").hidden = view !== "mastery";
  $("#roadmap-page").hidden = view !== "roadmap";
  $("#inbox-page").hidden = view !== "inbox";
  $("#heatmap-page").hidden = view !== "heatmap";
  $("#clusters-page").hidden = view !== "clusters";
  $("#print-page").hidden = view !== "print";
  $("#groups-page").hidden = view !== "groups";
  $("#forum-page").hidden = view !== "forum";
  $("#notes-page").hidden = view !== "notes";
  $("#whiteboard-page").hidden = view !== "whiteboard";
  $("#admin-page").hidden = view !== "admin";
  renderUserInfo();
  renderHomeQuota();

  // 侧栏/底栏/抽屉的当前页高亮由 shell.js 监听这个事件完成。
  if (changedView) window.scrollTo({ top: 0, left: 0, behavior: "instant" });
  document.dispatchEvent(new CustomEvent("app:view-changed", { detail: { view } }));

  if (view === "home") await loadHome({ refreshUser });
  else if (view === "admin") await loadAdminPage();
  else if (view === "forum") await showForumList();
  else if (view === "notes") {
    await window.Notes.load();
    // 首次进入静默拉取画板列表后，仍需让笔记列表再渲染一次：卡片正文里的
    // ![标题](drawing:ID) 只有在画板 id 集合就绪后才能渲染成缩略图，否则首次
    // 进入笔记页（尤其手机端直接落到笔记视图）会一直显示原始 Markdown 文本。
    // onDrawingsChanged 内部有“正在编辑不重渲染”的保护，不会冲掉未保存正文。
    await window.DrawHost?.load();
  }
  else if (view === "whiteboard") await window.DrawHost?.load();
  else if (view === "leaderboard") await loadLeaderboard();
  else if (view === "insights") await loadWeaknessAnalysis();
  else if (view === "achievements") await loadAchievements();
  else if (view === "weekly-recap") await loadWeeklyRecap();
  else if (view === "mastery") await window.Mastery.load();
  else if (view === "roadmap") await window.Roadmap.load();
  else if (view === "inbox") await window.Inbox.load();
  else if (view === "heatmap") await window.Heatmap.loadHeatmap($("#heatmap-grid"), 8);
  else if (view === "clusters") {
    await window.Clusters.load();
    window.Typical?.mount($("#typical-card")); // 我的三大典型失误：自带登录代次与视图守卫
  }
  else if (view === "print") await window.PrintNotebook.load();
  else if (view === "groups") await loadGroups();
  else if (view === "plan") await loadPlanPage();
  else if (view === "new") renderPhotoQuota();
  else if (view === "today" || view === "all") await loadList();
}

$("#login-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (window.Account?.isPending()) return;
  const form = event.currentTarget;
  run(async () => {
    $("#login-username-hint").hidden = true;
    try {
      await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify(formObject(form)),
      });
    } catch (error) {
      $("#login-username-hint").hidden = !form.username.value.includes("@");
      throw error;
    }
    form.reset();
    await enterApp();
    message("已登录，今天的复习已经准备好了。");
  });
});

$("#register-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (window.Account?.isPending()) return;
  const form = event.currentTarget;
  run(async () => {
    if (!form.querySelector('[name="accept_terms"]').checked) {
      throw new Error("请先阅读并同意服务条款和隐私政策");
    }
    await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ ...formObject(form), accept_terms: true }),
    });
    form.reset();
    await enterApp();
    message("账号已创建。去「新增记录」留下你的第一条错因吧。");
  });
});

function startTrial() {
  if (window.Account?.isPending()) return;
  return run(async () => {
    await api("/api/auth/trial", {
      method: "POST",
      body: JSON.stringify({
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai",
      }),
    });
    await enterApp();
    message();
  });
}

$("#trial-start").addEventListener("click", startTrial);
$("#auth-trial-start").addEventListener("click", startTrial);

$("#forgot-link").addEventListener("click", (event) => {
  event.preventDefault();
  message();
  showAuthPanels(["forgot-form"]);
});

$("#back-to-login-link").addEventListener("click", (event) => {
  event.preventDefault();
  message();
  showAuthPanels(["login-form"]);
});

// 找回密码：提交成功后按钮倒计时禁用，避免重复点击连发多封邮件。
// 倒计时用 data-blocked 表示（全局 setBusy 会据此恢复按钮状态），
// 用会话轮次守卫：登出 / 换账号后迟到的计时器不再碰按钮。
const FORGOT_COOLDOWN_SECONDS = 60;
const FORGOT_BUTTON_LABEL = "发送重置邮件";
let forgotCooldownTimer = null;

function stopForgotCooldown() {
  clearInterval(forgotCooldownTimer);
  forgotCooldownTimer = null;
  const button = $("#forgot-form").querySelector("button[type=submit]");
  button.dataset.blocked = "0";
  button.disabled = busy;
  button.textContent = FORGOT_BUTTON_LABEL;
}

function startForgotCooldown() {
  const epoch = sessionEpoch;
  const button = $("#forgot-form").querySelector("button[type=submit]");
  let remaining = FORGOT_COOLDOWN_SECONDS;
  clearInterval(forgotCooldownTimer);
  const paint = () => {
    button.dataset.blocked = "1";
    button.disabled = true;
    button.textContent = `${FORGOT_BUTTON_LABEL}（${remaining} 秒后可重发）`;
  };
  paint();
  forgotCooldownTimer = setInterval(() => {
    if (epoch !== sessionEpoch) {
      stopForgotCooldown();
      return;
    }
    remaining -= 1;
    if (remaining <= 0) stopForgotCooldown();
    else paint();
  }, 1000);
}

$("#forgot-form").addEventListener("submit", (event) => {
  event.preventDefault();
  if (forgotCooldownTimer !== null) return;
  const form = event.currentTarget;
  const epoch = sessionEpoch;
  run(async () => {
    await api("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email: form.email.value }),
    });
    // 响应回来时用户已登出 / 换了账号：不再改界面，也不开倒计时。
    if (epoch !== sessionEpoch) return;
    form.reset();
    showAuthPanels(["login-form"]);
    startForgotCooldown();
    message("如果该邮箱已注册，邮件会在几分钟内送达；没收到请查看垃圾邮件箱。");
  });
});

$("#reset-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const secEpoch = sessionEpoch;
  const secToken = resetToken;
  run(async () => {
    try {
      await api("/api/auth/reset-password", {
        method: "POST",
        body: JSON.stringify({
          token: resetToken || "",
          password: form.password.value,
        }),
      });
    } catch (error) {
      if (secEpoch !== sessionEpoch || secToken !== resetToken) return;
      if (error.status === 400 && error.code === "invalid_token") {
        resetToken = null;
        history.replaceState(null, "", `${location.pathname}#/auth`);
        showAuthPanels(["forgot-form"]);
        message("重置链接无效或已过期，请重新申请。", true);
        return;
      }
      throw error;
    }
    if (secEpoch !== sessionEpoch || secToken !== resetToken) return;
    form.reset();
    resetToken = null;
    history.replaceState(null, "", `${location.pathname}#/auth`);
    showAuthPanels(["login-form"]);
    message("密码已重置，请用新密码登录。");
  });
});

$("#email-prompt").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const secEpoch = sessionEpoch;
  const secOwnerId = user?.id;
  if (!secOwnerId) return;
  run(async () => {
    let updated;
    try {
      updated = await api("/api/me/email", {
        method: "PUT",
        body: JSON.stringify({ email: form.email.value, password: form.password.value }),
      });
    } catch (error) {
      if (secEpoch !== sessionEpoch || secOwnerId !== user?.id) return;
      throw error;
    }
    if (secEpoch !== sessionEpoch || secOwnerId !== user?.id) return;
    $("#email-prompt").hidden = true;
    form.reset();
    message("确认邮件已发到新邮箱，点击链接后生效。");
  });
});

$("#logout").addEventListener("click", () => {
  if (window.Account?.isPending()) return;
  return run(async () => {
    const secEpoch = sessionEpoch;
    const secOwnerId = user?.id;
    await api("/api/auth/logout", { method: "POST" });
    if (secEpoch !== sessionEpoch || secOwnerId !== user?.id) return;
    history.replaceState(null, "", `${location.pathname}#/welcome`);
    signedOut();
    message("已退出登录。");
  });
});

// ---- 认证区事件绑定结束（测试 between() 切片的上界标记） ----
