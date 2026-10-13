// static/app/account.js — 账号设置。
// 由 static/app.js 按领域拆分（C2），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

function initAccountMenu() {
  const menu = $(".account-menu");
  const summary = menu.querySelector("summary");
  const panel = menu.querySelector(".account-menu-panel");

  function close(restoreFocus = false) {
    menu.open = false;
    document.removeEventListener("click", onOutsideClick);
    document.removeEventListener("keydown", onKeydown);
    if (restoreFocus) summary.focus();
  }
  function onOutsideClick(event) {
    // Editing replaces the clicked control before this event reaches document.
    if (!event.composedPath().includes(menu)) close();
  }
  function onKeydown(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      close(true);
    }
  }
  // Global listeners exist only while open; repeated toggles reuse the same callbacks.
  menu.addEventListener("toggle", () => {
    if (menu.open) {
      document.addEventListener("click", onOutsideClick);
      document.addEventListener("keydown", onKeydown);
    } else {
      close();
    }
  });
  panel.addEventListener("keydown", (event) => {
    const upload = event.target.closest(".avatar-upload-label");
    const exportLink = event.target.closest('a[href="/api/export"]');
    if (upload && (event.key === "Enter" || event.key === " ")) {
      event.preventDefault();
      if (!event.repeat) $("#avatar-file-input").click();
    } else if (exportLink && event.key === " ") {
      event.preventDefault();
      if (!event.repeat) exportLink.click();
    }
  });
  panel.addEventListener("click", (event) => {
    // The toggle's own handler updates its text before this bubbling close handler.
    if (event.target.closest('a[href="/api/export"], #fx-toggle, #remove-avatar-btn')) {
      close(true);
    }
  });
  $("#avatar-file-input").addEventListener("change", () => close(true));
  window.addEventListener("pagehide", () => close());
  return close;
}

// ---- 事件绑定与模块配置 ----

const closeAccountMenu = initAccountMenu();

document.querySelectorAll(".fx-toggle").forEach((button) => {
  button.addEventListener("click", () => {
    window.CursorFX?.setEnabled(!window.CursorFX?.isEnabled());
    updateCursorFxToggle();
  });
});
updateCursorFxToggle();

$("#avatar-file-input").addEventListener("change", (event) => {
  const input = event.currentTarget;
  const file = input.files[0];
  if (!file) return;
  run(async () => {
    const result = await uploadAvatarFile(file);
    user.avatar_version = result.avatar_version;
    user.has_avatar = result.has_avatar;
    updateUserInfo();
    input.value = "";
    message("头像已更新。");
  });
});

$("#remove-avatar-btn").addEventListener("click", () => run(async () => {
  const result = await api("/api/me/avatar", { method: "DELETE" });
  user.avatar_version = result.avatar_version;
  user.has_avatar = result.has_avatar;
  updateUserInfo();
  message("头像已移除。");
}));

$("#refresh").addEventListener("click", () => run(async () => {
  if (view === "home") {
    message();
    await loadHome();
    return;
  }
  if (view === "groups") {
    message();
    if (selectedGroupId === null) await loadGroups();
    else await openStudyGroup(selectedGroupId);
    return;
  }
  if (view === "weekly-recap") {
    message();
    await loadWeeklyRecap();
    return;
  }
  if (view === "mastery") {
    message();
    await window.Mastery.load();
    message("已刷新。");
    return;
  }
  if (view === "clusters") {
    message();
    await window.Clusters.load();
    window.Typical?.mount($("#typical-card"));
    message("已刷新。");
    return;
  }
  if (view === "print") {
    message();
    await window.PrintNotebook.load();
    message("已刷新。");
    return;
  }
  if (view === "achievements") {
    message();
    await loadAchievements();
    return;
  }
  if (view === "insights") {
    message();
    await loadWeaknessAnalysis();
    return;
  }
  if (view === "admin") {
    message();
    const dashboardLoaded = await loadAdminPage();
    message(dashboardLoaded ? "已刷新。" : "举报队列已刷新；数据看板加载失败，请重试。", !dashboardLoaded);
    return;
  }
  if (view === "forum") {
    message();
    if (forumPost) await openForumPost(forumPost.id);
    else await showForumList();
    message("已刷新。");
    return;
  }
  if (view === "leaderboard") {
    message();
    await loadLeaderboard();
    message("已刷新。");
    return;
  }
  if (view === "plan") {
    message();
    await loadPlanPage();
    return;
  }
  if (view !== "new") await loadList();
  message(view === "new" ? "请先保存当前记录。" : "已刷新。");
}));

$("#plan-back").addEventListener("click", () => run(async () => {
  stopOrderPolling();
  planPurchase = null;
  message();
  await loadPlanPage();
}));

$("#add-mistake").addEventListener("click", addMistakeInput);

$("#problem-photo-file").addEventListener("change", (event) => {
  photoRecognition = null;
  $("#problem-photo-save").hidden = true;
  $("#problem-save").hidden = false;
  $("#problem-photo-status").textContent = "";
  const file = event.currentTarget.files[0];
  if (!file) {
    $("#problem-photo-preview").hidden = true;
    $("#problem-photo-preview").src = "";
    $("#problem-photo-recognize").disabled = true;
    $("#problem-photo-clear").hidden = true;
    return;
  }
  $("#problem-photo-preview").src = URL.createObjectURL(file);
  $("#problem-photo-preview").hidden = false;
  $("#problem-photo-recognize").disabled = false;
  $("#problem-photo-clear").hidden = false;
});

$("#problem-photo-clear").addEventListener("click", () => resetPhotoForm());

$("#problem-photo-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const file = $("#problem-photo-file").files[0];
  if (!file) return;

  run(async () => {
    $("#problem-photo-status").textContent = "正在识别图片，可能需要几十秒…";
    try {
      const fields = await uploadPhotoForRecognition(file);
      photoRecognition = fields;
      fillProblemFormFromPhoto(fields);
      $("#problem-photo-status").textContent = "已识别，请核对下方内容后再保存。";
    } catch (error) {
      $("#problem-photo-status").textContent = "识别未成功，可以换一张更清晰的照片重试。";
      throw error;
    } finally {
      user = await api("/api/me");
      updateUserInfo();
      renderPhotoQuota();
    }
  });
});

/* ---- GROWTH F1：从链接导入题目预填 ----
   只预填表单不直接建题，用户核对后走现有保存流程。 */
$("#import-from-url-btn")?.addEventListener("click", () => run(async () => {
  const url = prompt("粘贴 LeetCode / Codeforces / 洛谷题目链接：");
  if (!url || !url.trim()) return;
  const epoch = sessionEpoch;
  const owner = user?.id;
  const guard = () => Boolean(user) && user.id === owner && sessionEpoch === epoch;
  $("#problem-import-status").textContent = "正在解析链接…";
  try {
    const prefill = await window.ImportProblem.fetchPrefill(url.trim());
    if (!prefill || !guard()) return;
    window.ImportProblem.fillProblemForm(prefill);
    $("#problem-import-status").textContent = "已预填，请核对后保存。";
  } catch (error) {
    if (!guard()) return;
    $("#problem-import-status").textContent = error.message || "抓取失败，请检查链接后重试。";
    throw error;
  }
}));
/* ---- 速记模式（E2 事项一）----
   默认完整记录，保持原校验；速记只填题名和分区，一句话再补一条真正的错因。
   两种简写模式都隐藏代码和思路，保留切换前写下的草稿。 */const QUICK_MISTAKE_HINT = "速记模式下将自动创建一条待补原因的易错点";

// 开关当前是否打开。
