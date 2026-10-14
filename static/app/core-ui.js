// static/app/core-ui.js — 核心·界面层：DOM 工具/消息/用户栏 + window.OY 出口与尾部接线。
// 由 static/app/core.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

// ---- window.OY：跨领域共享入口（api/会话/消息/路由） ----
// 各领域文件通过 window.OY 访问核心能力；getter 闭包保证读到最新值。
// 函数声明已提升，此处可直接引用。
window.OY = {
  api,
  getUser: () => user,
  getEpoch: () => sessionEpoch,
  isSessionReady: () => sessionReady,
  getView: () => view,
  message,
  notify: notifyDataChanged,
  run,
  setBusy,
  showView,
  renderPageRoute,
};

function sealAnchorPoint(anchor) {
  if (anchor && typeof anchor.getBoundingClientRect === "function") {
    const rect = anchor.getBoundingClientRect();
    return { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 };
  }
  return {
    x: Number.isFinite(anchor?.x) ? anchor.x : window.innerWidth / 2,
    y: Number.isFinite(anchor?.y) ? anchor.y : window.innerHeight * .35,
  };
}

function element(tag, text = "", className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  return node;
}

function avatarHue(username) {
  let hash = 0;
  for (const char of username) hash = (hash * 31 + char.charCodeAt(0)) >>> 0;
  return hash % 360;
}

// ---- 事件绑定与模块配置 ----

// 白色首字母压在头像底色上要有 4.6:1 以上的对比度：黄、绿、青色相天生偏亮，
// 先把明度往下压到够用为止，其余色相保持原来的 45%。

function avatarBackground(hue) {
  const channel = (value) => (value <= 0.03928 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  const luminance = (lightness) => {
    const spread = 0.55 * Math.min(lightness, 1 - lightness);
    const [red, green, blue] = [0, 8, 4].map((offset) => {
      const k = (offset + hue / 30) % 12;
      return lightness - spread * Math.max(-1, Math.min(k - 3, 9 - k, 1));
    });
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue);
  };
  let lightness = 45;
  while (lightness > 20 && 1.05 / (luminance(lightness / 100) + 0.05) < 4.6) lightness -= 1;
  return `hsl(${hue}, 55%, ${lightness}%)`;
}

// 版本号只用于刷新缓存；头像不存在或加载失败时，显示用户名首字母。

function avatarElement(userId, username, avatarVersion, {
  small = false, hasAvatar = avatarVersion > 0,
} = {}) {
  const className = small ? "avatar avatar-sm" : "avatar";
  const fallback = element("span", (username || "?").slice(0, 1).toUpperCase(), className);
  fallback.style.background = avatarBackground(avatarHue(username || ""));
  fallback.setAttribute("aria-hidden", "true");
  if (hasAvatar) {
    const img = document.createElement("img");
    img.className = className;
    img.addEventListener("error", () => img.replaceWith(fallback), { once: true });
    img.src = `/api/users/${userId}/avatar?v=${avatarVersion}`;
    img.alt = `${username} 的头像`;
    return img;
  }
  return fallback;
}

// 举报某个用户的头像；用在帖子/评论作者旁边，跟举报帖子/评论内容是两件事。
// onCancel 通常是把界面切回举报前的只读状态。

function avatarReportForm(userId, onCancel) {
  const reason = textarea("", 500, 3);
  const submit = element("button", "提交举报", "primary");
  submit.type = "submit";
  const cancel = element("button", "取消");
  cancel.type = "button";
  cancel.addEventListener("click", onCancel);

  const form = document.createElement("form");
  form.append(field("举报头像的原因（可选）", reason), submit, cancel);
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      await api(`/api/users/${userId}/avatar/report`, {
        method: "POST",
        body: JSON.stringify({ reason: reason.value }),
      });
      message("已提交头像举报，管理员会尽快处理。");
      onCancel();
    });
  });
  return form;
}

// 复习、新增、删除之后告诉各个小部件（侧栏角标、热力图……）重新取数。

function notifyDataChanged(reason) {
  document.dispatchEvent(new CustomEvent("app:data-changed", { detail: { reason } }));
}

function message(text = "", error = false) {
  $("#notice").textContent = text;
  $("#notice").classList.toggle("error", error);
  // 手感层：非空提示额外弹一条 toast；#notice 原有逻辑与错误态保持不变。
  if (text) window.Feel?.toast(text);
}

function setBusy(value) {
  $("#app").setAttribute("aria-busy", String(value));
  $("#auth").setAttribute("aria-busy", String(value));
  $("#notice").classList.toggle("pending", value);
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = value || button.dataset.blocked === "1";
  });
}

function field(labelText, control) {
  const label = element("label", labelText);
  label.append(control);
  return label;
}

function textarea(value, maxLength, rows, code = false) {
  const control = document.createElement("textarea");
  control.value = value;
  control.maxLength = maxLength;
  control.rows = rows;
  if (code) {
    control.className = "code";
    control.spellcheck = false;
  }
  return control;
}

function timestamp(value) {
  if (!value) return "尚无";
  const date = new Date(value);
  try {
    return date.toLocaleString("zh-CN", { timeZone: user.timezone, hour12: false });
  } catch (error) {
    if (!(error instanceof RangeError)) throw error;
    return `${date.toLocaleString("zh-CN", { timeZone: "UTC", hour12: false })}（UTC）`;
  }
}

let redoPreferenceWired = false;
let redoPreferenceCache = null; // "redo_first" | "recall_only"，读过 /api/me/review-settings 后缓存

async function rvfFetchRedoPreference() {
  if (redoPreferenceCache) return redoPreferenceCache;
  try {
    const settings = await api("/api/me/review-settings");
    redoPreferenceCache = settings.redo_preference === "recall_only" ? "recall_only" : "redo_first";
  } catch (error) {
    redoPreferenceCache = "recall_only";
  }
  return redoPreferenceCache;
}

// 账号设置里的“复习方式偏好”：只接线一次，读/写都走 /api/me/review-settings。
function rvfInitRedoPreference() {
  if (redoPreferenceWired) return;
  const select = $("#account-redo-preference");
  if (!select) return;
  redoPreferenceWired = true;
  run(async () => {
    select.value = await rvfFetchRedoPreference();
  });
  select.addEventListener("change", () => run(async () => {
    const value = select.value === "recall_only" ? "recall_only" : "redo_first";
    const previous = redoPreferenceCache;
    redoPreferenceCache = value;
    try {
      await api("/api/me/review-settings", {
        method: "PUT",
        body: JSON.stringify({ redo_preference: value }),
      });
      message("复习方式偏好已保存。");
    } catch (error) {
      redoPreferenceCache = previous;
      select.value = previous || "recall_only";
      message(error.message);
    }
  }));
}

function renderUserInfo() {
  const wrap = $("#user-info-wrap");
  for (const id of ["account-password", "account-revoke-others", "account-delete"]) {
    $("#" + id).hidden = Boolean(user.is_trial);
  }

  function readOnly() {
    finishHomeOpening?.();
    $("#account-summary-name").textContent = user.username;
    $("#account-summary-name").title = user.username;
    wrap.replaceChildren(
      element("strong", user.username, "user-name"),
      element("span", `${user.timezone} · ${user.today}`, "user-context")
    );
    if (!user.is_trial) {
      const editBtn = element("button", "改用户名", "link-button");
      editBtn.type = "button";
      editBtn.addEventListener("click", editForm);
      wrap.append(editBtn);
    }
  }

  function editForm() {
    const input = document.createElement("input");
    input.value = user.username;
    input.maxLength = 32;
    input.required = true;
    input.autocomplete = "off";
    const save = element("button", "保存", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.className = "inline-form";
    form.append(field("新用户名", input), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api("/api/me/username", {
          method: "PUT",
          body: JSON.stringify({ username: input.value }),
        });
        user.username = updated.username;
        message("用户名已更新。");
        readOnly();
      });
    });

    wrap.replaceChildren(form);
  }

  readOnly();
}

function updateCursorFxToggle() {
  const enabled = window.CursorFX?.isEnabled() ?? false;
  document.querySelectorAll(".fx-toggle").forEach((button) => {
    button.textContent = `山水涟漪：${enabled ? "开" : "关"}`;
    button.setAttribute("aria-pressed", String(enabled));
  });
}

function updateUserInfo() {
  updateCursorFxToggle();
  renderUserInfo();
  $("#email-prompt").hidden = Boolean(user.email) || Boolean(user.is_trial);
  $("#trial-banner").hidden = !user.is_trial;
  document.querySelectorAll("[data-admin-only]").forEach((item) => { item.hidden = !user.is_admin; });
  window.AppShell?.setUser(user.username);
  renderHomeQuota();
  configureRank();
  window.Profile?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch, getView: () => view, avatar: avatarElement });
  window.Rank?.syncSetting(user);
  window.GroupExtras?.renderTodayVisibilitySetting();
  window.NavFocus?.configure({ getUser: () => user, getView: () => view }); // 专注模式按用户 id 读取开关
  window.PushSettings?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  $("#push-settings-host") && window.PushSettings?.mount($("#push-settings-host"));
  window.Reminder?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  $("#reminder-settings-host") && window.Reminder?.mount($("#reminder-settings-host"));
  window.Roadmap?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  window.Inbox?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  mountPatternPicker();
  rvfInitRedoPreference();

  $("#my-avatar-wrap").hidden = false;
  $("#my-avatar").replaceChildren(
    avatarElement(user.id, user.username, user.avatar_version, { hasAvatar: user.has_avatar })
  );
  $(".avatar-upload-label").hidden = user.is_trial;
  $("#remove-avatar-btn").hidden = user.is_trial || !user.has_avatar;
}

// 书签带来的预填：切到新增记录页再填；登出再登录别的账号后才完成的，丢弃。

async function applyCapturePending() {
  const epoch = sessionEpoch;
  const pending = window.Capture?.take();
  if (!pending) return;
  await showView("new");
  if (epoch !== sessionEpoch || !user) return;
  window.Capture.applyIntake(pending);
}

// 侧栏、底栏、"更多"抽屉和页面里的入口都带 data-view；用事件委托，总览里动态渲染的入口也生效。
document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-view]");
  if (!button || button.disabled) return;
  run(async () => {
    message();
    listCreatedOn = "";
    await showView(button.dataset.view);
  });
});

// 命令面板选中后的跳转：页面、某条记录（在"全部记录"里打开）、某个帖子。
document.addEventListener("app:navigate", (event) => {
  const { view: target, recordId, postId } = event.detail || {};
  run(async () => {
    message();
    listCreatedOn = "";
    if (recordId) {
      $("#zone-filter").value = "";
      await showView("all");
      await openMistake(recordId);
    } else if (postId) {
      await showView("forum");
      await openForumPost(postId);
    } else if (target) {
      await showView(target);
    }
  });
});

// 侧栏的分区列表和复习日历：切到"全部记录"并按分区/新增日期筛选。
let listCreatedOn = "";
document.addEventListener("records:filter", (event) => {
  const { zone = "", tag = "", created_on: createdOn = "" } = event.detail || {};
  run(async () => {
    message();
    listCreatedOn = createdOn;
    const select = $("#zone-filter");
    select.value = [...select.options].some((option) => option.value === zone) ? zone : "";
    const tagSelect = $("#tag-filter");
    if (tag && ![...tagSelect.options].some((option) => option.value === tag)) {
      const extra = document.createElement("option");
      extra.value = tag;
      extra.textContent = tag;
      tagSelect.append(extra);
    }
    tagSelect.value = tag;
    await showView("all");
  });
});

$("#list-focus").addEventListener("click", () => window.FocusReview?.start({ zone: $("#zone-filter").value, tag: $("#tag-filter").value }));

// 专注复习关闭后：要求跳转就跳转，否则（评过分的话）重新加载当前页，让数字和列表跟上。
document.addEventListener("focus:closed", (event) => {
  const { graded, view: target } = event.detail || {};
  if (!user) return;
  run(async () => {
    message();
    if (target) {
      await showView(target);
    } else if (graded && view === "home") {
      await loadHome({ refreshUser: false });
    } else if (graded && (view === "today" || view === "all")) {
      await loadList();
    } else if (graded && view === "mastery") {
      await window.Mastery.load();
    } else if (graded && view === "clusters") {
      await window.Clusters.load();
      window.Typical?.mount($("#typical-card"));
    }
  });
});

$("#list-filter-clear").addEventListener("click", () => run(async () => {
  listCreatedOn = "";
  await loadList();
}));

$("#zone-filter").addEventListener("change", () => run(loadList));
$("#tag-filter").addEventListener("change", () => run(loadList));

// 详情页里改了标签：同步更新列表里那张卡片上的小标签。
document.addEventListener("mistake:tags-changed", (event) => {
  const { id, tags } = event.detail || {};
  const item = rvfListItems.get(Number(id));
  if (item) item.tags = tags;
  if (!item) return;
  rvfRenderCards(user.today, rvfDetailState?.item.id);
  void rvfRefreshProblem(item);
});

window.Onboarding?.configure({ api });
window.OfflineSync?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
window.OfflineReview?.configure({ notify: (text) => message(text), getUser: () => user, getEpoch: () => sessionEpoch });
window.PwaRegister?.configure({ version: "1" });
window.PwaRegister?.register();

$("#home-refresh").addEventListener("click", () => run(async () => {
  message();
  await loadHome();
}));

$("#my-profile").addEventListener("click", () => {
  if (!user) return;
  closeAccountMenu();
  window.Profile?.open(user.id);
});

$("#weakness-analyze").addEventListener("click", () => run(analyzeWeakness));
$("#achievements-retry").addEventListener("click", () => run(loadAchievements));

$("#achievements-share-generate").addEventListener("click", () => {
  if (!user || view !== "achievements") return;
  const image = $("#achievements-share-image");
  $("#achievements-share-error").hidden = true;
  $("#achievements-share-error").textContent = "";
  image.src = `/api/achievements/share-card?t=${Date.now()}`;
  image.hidden = false;
  $("#achievements-share-hint").hidden = false;
});

$("#achievements-share-image").addEventListener("error", (event) => {
  const image = event.currentTarget;
  if (!image.hasAttribute("src")) return;
  image.hidden = true;
  $("#achievements-share-hint").hidden = true;
  $("#achievements-share-error").textContent = "分享卡片加载失败，请检查网络后重新生成。";
  $("#achievements-share-error").hidden = false;
});

$("#weekly-recap-retry").addEventListener("click", () => run(loadWeeklyRecap));
$("#groups-retry").addEventListener("click", () => run(() => selectedGroupId === null
  ? loadGroups() : openStudyGroup(selectedGroupId)));
$("#groups-back").addEventListener("click", () => run(loadGroups));
$("#groups-copy-invite").addEventListener("click", () => run(copyGroupInvite));
$("#groups-levels-retry").addEventListener("click", () => { void loadGroupLevelRules(); });
$("#groups-create-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(() => submitStudyGroup(form, "/api/groups", "name"));
});
$("#groups-join-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(() => submitStudyGroup(form, "/api/groups/join", "invite_code"));
});
$("#groups-leave").addEventListener("click", () => run(() => leaveStudyGroup()));
$("#groups-delete").addEventListener("click", () => run(() => leaveStudyGroup(true)));

$("#admin-dashboard-refresh").addEventListener("click", () => run(async () => {
  message();
  await loadAdminDashboard();
}));

$("#problem-zone").addEventListener("change", (event) => {
  applyZoneFieldMode($("#problem-form"), event.currentTarget.value);
});

for (const module of [window.ImportProblem, window.Heatmap]) module?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });

// 记笔记页（static/notes.js）：请求带登录代次由内部 generation 守卫，登出时 reset 清掉数据。
window.Notes?.configure({
  api,
  getUser: () => user,
  getEpoch: () => sessionEpoch,
  getView: () => view,
  confirm: (text) => window.confirm(text),
  notify: (text) => message(text),
  // 画板引用只渲染当前用户自己的画板；集合由 draw-host.js 维护。
  getDrawingIds: () => window.DrawHost?.ownedIds?.() ?? new Set(),
});

// 画板宿主（static/draw-host.js）：画板列表、iframe 面板与自动保存。
window.DrawHost?.configure({
  api,
  getUser: () => user,
  getEpoch: () => sessionEpoch,
  getView: () => view,
  confirm: (text) => window.confirm(text),
  notify: (text, isError) => message(text, isError),
  // 画板增删改后刷新笔记正文里的引用缩略图（仅在笔记视图）；
  // 但笔记正在卡片内编辑时不能重渲染，否则会冲掉未保存正文（含刚插入的画板引用）。
  // .notes-editor 是编辑态外壳（富文本/降级 textarea 都在其中），.notes-edit-content
  // 是降级路径的 textarea：两者任一存在都视为正在编辑。
  onDrawingsChanged: () => {
    if (view === "notes"
        && !document.querySelector(".notes-editor")
        && !document.querySelector(".notes-edit-content")) {
      window.Notes?.load();
    }
  },
});

// N1 笔记双向链接 + 关系图谱（static/notes-links.js）：在 notes.js 之后接线。
window.NotesLinks?.configure({
  api,
  getUser: () => user,
  getEpoch: () => sessionEpoch,
  getView: () => view,
  showView,
  notify: (text) => message(text),
});

// N5 所见即所得编辑器适配层（static/notes-editor.js）：图片上传走 NotesRich，
// 互链/画板/题目回调由其内部复用 NotesLinks / DrawHost；这里只注入宿主能力。
window.NotesEditor?.configure({
  api,
  getUser: () => user,
  getEpoch: () => sessionEpoch,
  getView: () => view,
  showView,
  notify: (text, isError) => message(text, isError),
});

$("#timezone").value =
  Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
