"use strict";

const $ = (selector) => document.querySelector(selector);

const grades = [
  [0, "忘记 / 答错"],
  [3, "困难，但答对"],
  [4, "记得"],
  [5, "熟练"],
];

const resultLabels = {
  unattempted: "待练习",
  solved: "已解决",
  partial: "部分解决",
  failed: "未解决",
};

let user = null;
let sessionReady = false;
let resetToken = new URLSearchParams(location.search).get("reset_token");
let view = "today";
// 上一次拍照识别成功的结果；非空时说明表单当前内容来自 AI 识别，
// 保存记录后要顺带自动生成练习题。手动编辑无关字段不会清空它，
// 但重新选图、移除图片或表单重置都会清空。
let photoRecognition = null;
let busy = false;
let planPurchase = null;
let orderPollTimer = null;
let orderPollGeneration = 0;
let forumPost = null;
let forumSearchQuery = "";
let forumListGeneration = 0;
let adminDashboardGeneration = 0;
let weaknessAnalysis = null;
let weaknessGeneration = 0;
let weaknessPending = false;
let weaknessQuotaAvailable = false;
let achievementsGeneration = 0;
let weeklyRecapGeneration = 0;
let groupsGeneration = 0;
let selectedGroupId = null;
let studyGroup = null;
let growthZones = null;
let zones = [];
let codeZones = new Set();

const sealMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const sealStamps = [];
const achievementStampTimers = new Set();
let homeOpeningPlayed = false;
let finishHomeOpening = null;

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

function stampSeal(text, options = {}) {
  const layer = $("#seal-layer");
  if (!layer || document.hidden || !/^[\u3400-\u9fff]{2,4}$/u.test(text)) return;
  const tone = options.tone === "gamboge" ? "gamboge" : "cinnabar";
  const stamp = document.createElement("span");
  stamp.className = `seal-stamp seal-stamp--${tone}`;
  stamp.setAttribute("aria-hidden", "true");
  stamp.textContent = text;
  stamp.style.setProperty("--rot", `${-12 + Math.random() * 8}deg`);
  stamp.style.setProperty("--seal-height", `${78 + (Array.from(text).length - 2) * 30}px`);
  const timers = new Set();
  const entry = { remove };
  function remove() {
    for (const timer of timers) window.clearTimeout(timer);
    timers.clear();
    stamp.remove();
    const index = sealStamps.indexOf(entry);
    if (index !== -1) sealStamps.splice(index, 1);
  }
  function later(callback, delay) {
    const timer = window.setTimeout(() => {
      timers.delete(timer);
      callback();
    }, delay);
    timers.add(timer);
  }
  while (sealStamps.length >= 3) sealStamps[0].remove();
  layer.append(stamp);
  sealStamps.push(entry);

  const point = sealAnchorPoint(options.anchor);
  const animated = !sealMotion.matches;
  const scale = window.matchMedia("(max-width: 560px)").matches ? .8 : 1;
  // Reserve the rotated landing's full footprint, including the initial large stamp.
  const angle = 18 * Math.PI / 180;
  const reach = scale * (animated ? 2.3 : 1);
  const halfWidth = Math.min(window.innerWidth / 2,
    (stamp.offsetWidth * Math.cos(angle) + stamp.offsetHeight * Math.sin(angle)) * reach / 2 + 10);
  const halfHeight = Math.min(window.innerHeight / 2,
    (stamp.offsetHeight * Math.cos(angle) + stamp.offsetWidth * Math.sin(angle)) * reach / 2 + 10);
  const x = Math.max(halfWidth, Math.min(window.innerWidth - halfWidth, point.x));
  const y = Math.max(halfHeight, Math.min(window.innerHeight - halfHeight, point.y));
  stamp.style.left = `${x}px`;
  stamp.style.top = `${y}px`;

  if (animated) {
    stamp.classList.add("seal-stamp--animated");
    later(() => {
      if (!sealMotion.matches && !document.hidden) window.CursorFX?.drop?.(x, y, 1.1);
    }, 300);
    later(() => stamp.classList.add("seal-stamp--fading"), 1550);
    later(remove, 1900);
  } else {
    window.CursorFX?.drop?.(x, y, 1.1);
    later(remove, 1200);
  }
}

function cancelAchievementStamps() {
  for (const timer of achievementStampTimers) window.clearTimeout(timer);
  achievementStampTimers.clear();
}

function queueAchievementStamps(achievements, emblems, isCurrent) {
  cancelAchievementStamps();
  // This is a per-user presentation history, never an authority for unlocking badges.
  try {
    const key = `achievementsSeen:${user.id}`;
    const unlocked = achievements.filter((badge) => badge.unlocked).map((badge) => badge.key);
    const previous = window.localStorage.getItem(key);
    if (previous === null) {
      window.localStorage.setItem(key, JSON.stringify(unlocked));
      return;
    }
    const saved = JSON.parse(previous);
    if (!Array.isArray(saved)) {
      window.localStorage.setItem(key, JSON.stringify(unlocked));
      return;
    }
    const seen = new Set(saved);
    unlocked.filter((id) => !seen.has(id)).slice(0, 3).forEach((id, index) => {
      const timer = window.setTimeout(() => {
        achievementStampTimers.delete(timer);
        if (!isCurrent() || document.hidden) return;
        const emblem = emblems.get(id);
        if (!emblem?.isConnected) return;
        // A badge below the fold would push the seal onto unrelated content: stamp the summary card instead.
        const box = emblem.getBoundingClientRect();
        const summary = $("#achievements-summary");
        if (box.bottom > 0 && box.top < window.innerHeight || summary.hidden) {
          stampSeal("达成", { anchor: emblem, tone: "gamboge" });
        } else {
          const card = summary.getBoundingClientRect();
          stampSeal("达成", {
            anchor: { x: card.left + card.width * (0.18 + 0.14 * index), y: card.top + card.height / 2 },
            tone: "gamboge",
          });
        }
      }, index * 400);
      achievementStampTimers.add(timer);
    });
    window.localStorage.setItem(key, JSON.stringify(unlocked));
  } catch (_) {
    cancelAchievementStamps();
  }
}

function startHomeOpening() {
  if (homeOpeningPlayed || sealMotion.matches || document.hidden || view !== "home"
    || $("#app").hidden || $("#home-page").hidden) return;
  const title = $("#account-summary-name");
  if (!title) return;
  homeOpeningPlayed = true;
  const text = title.textContent;
  const label = title.getAttribute("aria-label");
  const original = Array.from(title.childNodes);
  title.setAttribute("aria-label", text);
  const letters = Array.from(text);
  const animatedCount = Math.min(letters.length, 16);
  const fragment = document.createDocumentFragment();
  letters.slice(0, animatedCount).forEach((letter, index) => {
    const span = document.createElement("span");
    span.className = "home-ink-char";
    span.setAttribute("aria-hidden", "true");
    span.style.setProperty("--ink-delay", `${index * 50}ms`);
    span.textContent = letter;
    fragment.append(span);
  });
  if (letters.length > animatedCount) {
    const rest = document.createElement("span");
    rest.setAttribute("aria-hidden", "true");
    rest.textContent = letters.slice(animatedCount).join("");
    fragment.append(rest);
  }
  title.replaceChildren(fragment);
  document.body.classList.add("home-anim");
  const timer = window.setTimeout(finish, Math.max(1300, 700 + (animatedCount - 1) * 50));
  function finish() {
    window.clearTimeout(timer);
    document.body.classList.remove("home-anim");
    title.replaceChildren(...original);
    if (label === null) title.removeAttribute("aria-label");
    else title.setAttribute("aria-label", label);
    finishHomeOpening = null;
  }
  finishHomeOpening = finish;
}

function initReviewSpotlight() {
  const card = $(".lobby-tile-review");
  const pointer = window.matchMedia("(hover: hover) and (pointer: fine)");
  if (!card) return;
  let frame = 0;
  let point = null;
  const cancel = () => {
    window.cancelAnimationFrame(frame);
    frame = 0;
    point = null;
  };
  const move = (event) => {
    if (sealMotion.matches || !pointer.matches || document.hidden || view !== "home") return;
    point = { x: event.clientX, y: event.clientY };
    if (frame) return;
    frame = window.requestAnimationFrame(() => {
      frame = 0;
      if (!point || sealMotion.matches || !pointer.matches || document.hidden || view !== "home") return;
      const rect = card.getBoundingClientRect();
      card.style.setProperty("--mx", `${point.x - rect.left}px`);
      card.style.setProperty("--my", `${point.y - rect.top}px`);
    });
  };
  card.addEventListener("pointerenter", move, { passive: true });
  card.addEventListener("pointermove", move, { passive: true });
  card.addEventListener("pointerleave", cancel);
  sealMotion.addEventListener("change", cancel);
  pointer.addEventListener("change", cancel);
  document.addEventListener("visibilitychange", cancel);
}

sealMotion.addEventListener("change", () => {
  if (sealMotion.matches) finishHomeOpening?.();
});
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    finishHomeOpening?.();
    cancelAchievementStamps();
    for (const entry of [...sealStamps]) entry.remove();
  }
});
window.addEventListener("pagehide", () => {
  finishHomeOpening?.();
  cancelAchievementStamps();
  for (const entry of [...sealStamps]) entry.remove();
});

const ORDER_POLL_INTERVAL = 3000;
const ORDER_POLL_DURATION = 5 * 60 * 1000;

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

// 版本号只用于刷新缓存；头像不存在或加载失败时，显示用户名首字母。
function avatarElement(userId, username, avatarVersion, {
  small = false, hasAvatar = avatarVersion > 0,
} = {}) {
  const className = small ? "avatar avatar-sm" : "avatar";
  const fallback = element("span", (username || "?").slice(0, 1).toUpperCase(), className);
  fallback.style.background = `hsl(${avatarHue(username || "")}, 55%, 45%)`;
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

function message(text = "", error = false) {
  $("#notice").textContent = text;
  $("#notice").classList.toggle("error", error);
}

function setBusy(value) {
  $("#app").setAttribute("aria-busy", String(value));
  $("#auth").setAttribute("aria-busy", String(value));
  $("#notice").classList.toggle("pending", value);
  document.querySelectorAll("button").forEach((button) => {
    button.disabled = value || button.dataset.blocked === "1";
  });
}

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
}

window.addEventListener("hashchange", () => {
  message();
  renderPageRoute();
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

function signedOut() {
  finishHomeOpening?.();
  for (const entry of [...sealStamps]) entry.remove();
  stopOrderPolling();
  resetWeaknessAnalysis();
  resetAchievements();
  resetWeeklyRecap();
  resetGroups();
  planPurchase = null;
  $("#plan-subscription").replaceChildren();
  $("#plan-list").replaceChildren();
  $("#plan-orders-list").replaceChildren();
  $("#plan-payment").replaceChildren();
  $("#plan-order-details").replaceChildren();
  $("#plan-order-status").textContent = "";
  $("#plan-order").hidden = true;
  user = null;
  document.body.classList.remove("home-view");
  $("#home-page").hidden = true;
  $("#home-admin").hidden = true;
  resetHomeSummary();
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
  forumSearchQuery = "";
  forumListGeneration += 1;
  $("#forum-search-form").reset();
  $("#forum-list-title").textContent = "全部帖子";
  $("#forum-posts").replaceChildren();
  $("#forum-list-status").textContent = "";
  $("#forum-post").replaceChildren();
  $("#forum-comments").replaceChildren();
  $("#forum-compose-form").reset();
  $("#forum-comment-form").reset();
  $("#admin-tab").hidden = true;
  resetAdminDashboard();
  $("#admin-reports").replaceChildren();
  $("#admin-status").textContent = "";
  $("#problem-form").reset();
  $("#mistake-inputs").replaceChildren();
  addMistakeInput();
  resetPhotoForm();
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Protection": "1",
      ...(options.headers || {}),
    },
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    if (response.status === 401) signedOut();

    let detail = data.detail || "请求失败，请稍后重试";
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => `${item.loc.join(".")}: ${item.msg}`)
        .join("；");
    } else if (typeof detail === "object") {
      detail = detail.message || "请求失败，请稍后重试";
    }

    const error = new Error(String(detail));
    error.status = response.status;
    throw error;
  }

  return data;
}

async function uploadAvatarFile(file) {
  const body = new FormData();
  body.append("file", file);
  // 不能像 api() 那样固定 Content-Type: application/json——multipart 请求
  // 的 boundary 必须由浏览器自己生成，手动设置反而会破坏它。
  const response = await fetch("/api/me/avatar", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Protection": "1" },
    body,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) signedOut();
    const detail = data.detail;
    const text = typeof detail === "object" && detail !== null ? detail.message : detail;
    const error = new Error(String(text || "上传失败，请稍后重试"));
    error.status = response.status;
    throw error;
  }
  return data;
}

async function run(action) {
  if (busy) return;
  busy = true;
  setBusy(true);

  try {
    await action();
  } catch (error) {
    if (error.status === 409 && user && (view === "today" || view === "all")) {
      try {
        await loadList();
      } catch {
        // 保留原始冲突提示。
      }
    }
    message(error.message || "操作失败，请检查网络后重试", true);
  } finally {
    busy = false;
    setBusy(false);
  }
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
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
  return new Date(value).toLocaleString("zh-CN", {
    timeZone: user.timezone,
    hour12: false,
  });
}

function renderUserInfo() {
  const wrap = $("#user-info-wrap");

  function readOnly() {
    finishHomeOpening?.();
    $("#account-summary-name").textContent = user.username;
    $("#account-summary-name").title = user.username;
    if (view === "home") {
      wrap.replaceChildren(
        element("span", "欢迎回来，继续积累你的解题力", "home-greeting"),
        element("strong", user.username, "home-username"),
        element("span", `${user.timezone} · ${user.today}`, "home-user-context")
      );
    } else {
      wrap.replaceChildren(
        element("strong", user.username, "user-name"),
        element("span", `${user.timezone} · ${user.today}`, "user-context")
      );
    }
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
  $("#admin-tab").hidden = !user.is_admin;
  $("#home-admin").hidden = !user.is_admin;
  renderHomeQuota();

  $("#my-avatar-wrap").hidden = false;
  $("#my-avatar").replaceChildren(
    avatarElement(user.id, user.username, user.avatar_version, { hasAvatar: user.has_avatar })
  );
  $(".avatar-upload-label").hidden = user.is_trial;
  $("#remove-avatar-btn").hidden = user.is_trial || !user.has_avatar;
}

async function loadZones() {
  const data = await api("/api/zones");
  zones = data.zones;
  codeZones = new Set(data.code_zones);

  const problemZone = $("#problem-zone");
  problemZone.replaceChildren();
  for (const zone of zones) {
    const option = element("option", zone);
    option.value = zone;
    problemZone.append(option);
  }

  const zoneFilter = $("#zone-filter");
  for (const zone of zones) {
    const option = element("option", zone);
    option.value = zone;
    zoneFilter.append(option);
  }

  applyZoneFieldMode($("#problem-form"), problemZone.value);
}

// 编程类分区要求填写编程语言，"代码"字段就是字面意义的代码；
// 数学类分区没有编程语言，同一个字段改用来记录解题过程/演算。
function applyZoneFieldMode(form, zoneValue) {
  const isCode = codeZones.has(zoneValue);
  const languageField = form.querySelector("[data-role=language-field]");
  const languageInput = form.querySelector("[name=language]");
  const codeLabel = form.querySelector("[data-role=code-label]");
  languageField.hidden = !isCode;
  languageInput.required = isCode;
  if (!isCode) languageInput.value = "";
  codeLabel.textContent = isCode ? "当时的代码" : "当时的解题过程";
  const codeInput = codeLabel.nextElementSibling;
  if (codeInput) {
    codeInput.placeholder = isCode
      ? "粘贴当时的代码，保留错误也没关系。"
      : "写下当时的解题过程或演算，保留错误也没关系。";
  }
}

function renderPhotoQuota() {
  const limit = user?.ai_daily_limit;
  const remaining = user?.ai_daily_remaining;
  const available = Number.isInteger(limit) && limit > 0 && Number.isInteger(remaining);
  $("#problem-photo-quota").textContent = available
    ? `今日 AI 额度剩余 ${Math.max(0, remaining)} / ${limit} 次`
    : "";
}

function resetPhotoForm() {
  photoRecognition = null;
  $("#problem-photo-form").reset();
  $("#problem-photo-preview").hidden = true;
  $("#problem-photo-preview").src = "";
  $("#problem-photo-recognize").disabled = true;
  $("#problem-photo-clear").hidden = true;
  $("#problem-photo-status").textContent = "";
  $("#problem-save").hidden = false;
  $("#problem-photo-save").hidden = true;
  renderPhotoQuota();
}

async function uploadPhotoForRecognition(file) {
  const body = new FormData();
  body.append("file", file);
  // 不能像 api() 那样固定 Content-Type: application/json——multipart 请求
  // 的 boundary 必须由浏览器自己生成，手动设置反而会破坏它。
  const response = await fetch("/api/problems/photo", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Protection": "1" },
    body,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) signedOut();
    const detail = data.detail;
    const text = typeof detail === "object" && detail !== null ? detail.message : detail;
    const error = new Error(String(text || "识别失败，请稍后重试"));
    error.status = response.status;
    throw error;
  }
  return data;
}

function fillProblemFormFromPhoto(fields) {
  const form = $("#problem-form");
  form.reset();
  form.title.value = fields.title;
  form.zone.value = fields.zone;
  applyZoneFieldMode(form, form.zone.value);
  form.language.value = fields.language;
  form.code.value = fields.code;
  form.thinking.value = fields.thinking;

  $("#mistake-inputs").replaceChildren();
  addMistakeInput();
  $("#mistake-inputs").querySelector("[name=mistake]").value = fields.description;

  $("#problem-save").hidden = true;
  $("#problem-photo-save").hidden = false;
}

async function enterApp() {
  resetWeaknessAnalysis();
  resetAchievements();
  resetWeeklyRecap();
  resetGroups();
  user = await api("/api/me");
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
  await showView("home", { refreshUser: false });
}

function closeNavMenu() {
  $("#nav-menu").hidden = true;
  $("#nav-toggle").setAttribute("aria-expanded", "false");
}

$("#nav-toggle").addEventListener("click", () => {
  const open = $("#nav-menu").hidden;
  $("#nav-menu").hidden = !open;
  $("#nav-toggle").setAttribute("aria-expanded", String(open));
});
document.addEventListener("click", (event) => {
  if (!event.target.closest("#page-nav")) closeNavMenu();
});
document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape" || $("#nav-menu").hidden) return;
  const restoreFocus = document.activeElement === $("#nav-toggle")
    || $("#nav-menu").contains(document.activeElement);
  closeNavMenu();
  if (restoreFocus) $("#nav-toggle").focus();
});
$("#page-nav").addEventListener("focusout", (event) => {
  if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) closeNavMenu();
});

async function showView(nextView, { refreshUser = true } = {}) {
  stopOrderPolling();
  if (view !== nextView) {
    finishHomeOpening?.();
    cancelAchievementStamps();
    if (view === "achievements") achievementsGeneration += 1;
  }
  if (view === "groups" && nextView !== "groups") groupsGeneration += 1;
  view = nextView;
  document.body.classList.toggle("home-view", view === "home");
  $("#home-page").hidden = view !== "home";
  $("#page-nav").hidden = view === "home";
  $("#new-page").hidden = view !== "new";
  $("#list-page").hidden = view !== "today" && view !== "all";
  $("#plan-page").hidden = view !== "plan";
  $("#leaderboard-page").hidden = view !== "leaderboard";
  $("#weakness-page").hidden = view !== "insights";
  $("#achievements-page").hidden = view !== "achievements";
  $("#weekly-recap-page").hidden = view !== "weekly-recap";
  $("#groups-page").hidden = view !== "groups";
  $("#forum-page").hidden = view !== "forum";
  $("#admin-page").hidden = view !== "admin";
  renderUserInfo();
  renderHomeQuota();

  document.querySelectorAll("#app button[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === view);
    button.setAttribute("aria-pressed", String(button.dataset.view === view));
  });

  closeNavMenu();
  const activeTab = $("#nav-menu").querySelector("[data-view].active");
  $("#nav-current").textContent = activeTab ? activeTab.textContent : "";

  if (view === "home") await loadHome({ refreshUser });
  else if (view === "admin") await loadAdminPage();
  else if (view === "forum") await showForumList();
  else if (view === "leaderboard") await loadLeaderboard();
  else if (view === "insights") await loadWeaknessAnalysis();
  else if (view === "achievements") await loadAchievements();
  else if (view === "weekly-recap") await loadWeeklyRecap();
  else if (view === "groups") await loadGroups();
  else if (view === "plan") await loadPlanPage();
  else if (view === "new") renderPhotoQuota();
  else if (view === "today" || view === "all") await loadList();
}

function resetHomeSummary() {
  $("#home-due-count").closest(".tile-count-wrap").classList.remove("has-due");
  $("#home-due-count").hidden = true;
  $("#home-due-count").textContent = "";
  $("#home-due-caption").textContent = "正在读取待复习记录…";
  $("#home-quota").hidden = true;
  $("#home-quota-text").textContent = "";
  $("#home-quota-progress").value = 0;
  $("#home-quota-progress").max = 1;
}

function renderHomeQuota() {
  const limit = user?.ai_daily_limit;
  const remaining = user?.ai_daily_remaining;
  const available = view === "home" && Number.isInteger(limit) && limit > 0
    && Number.isInteger(remaining) && remaining >= 0 && remaining <= limit;
  $("#home-quota").hidden = !available;
  if (!available) return;
  $("#home-quota-progress").max = limit;
  $("#home-quota-progress").value = remaining;
  $("#home-quota-text").textContent = `剩余 ${remaining} / ${limit} 次`;
}

async function loadHome({ refreshUser = true } = {}) {
  // A refresh during the opening must not restart the count's pen animation.
  finishHomeOpening?.();
  const currentUser = user;
  resetHomeSummary();
  // 大厅统计始终覆盖全部分区；返回时重新读额度，包含 AI 失败后实际扣除的次数。
  // 两份数据独立降级，读取失败不显示旧值或假定的零值。
  const [profile, reviews] = await Promise.allSettled([
    refreshUser ? api("/api/me") : Promise.resolve(user),
    api("/api/mistakes?due_only=true"),
  ]);
  if (!user || user !== currentUser || view !== "home") return;
  if (profile.status === "fulfilled") {
    user = profile.value;
    updateUserInfo();
  }
  if (reviews.status === "fulfilled") {
    const count = reviews.value.items.length;
    $("#home-due-count").closest(".tile-count-wrap").classList.toggle("has-due", count > 0);
    $("#home-due-count").textContent = String(count);
    $("#home-due-count").hidden = false;
    $("#home-due-caption").textContent = count
      ? "条易错点，等你来巩固" : "今日暂无待复习，去记录新的发现吧";
  } else {
    $("#home-due-caption").textContent = "暂时无法读取数量，可进入复习重试";
  }
  if (profile.status === "fulfilled" && reviews.status === "fulfilled") startHomeOpening();
}

async function loadLeaderboard() {
  const currentUser = user;
  const page = $("#leaderboard-page");
  const status = $("#leaderboard-status");
  const entries = $("#leaderboard-entries");
  const mine = $("#leaderboard-me");
  page.setAttribute("aria-busy", "true");
  status.textContent = "正在加载连续打卡排行榜…";
  entries.replaceChildren();
  mine.replaceChildren();
  $("#leaderboard-table-wrap").hidden = true;

  try {
    const data = await api("/api/leaderboard");
    if (user !== currentUser || !user || view !== "leaderboard") return;
    const { me } = data;
    $("#leaderboard-list-title").textContent = `排行榜 · 前 ${data.leaderboard_size} 名`;
    const rankText = me.is_trial
      ? "体验账号不参与排名，你仍可查看自己的连续打卡天数。"
      : me.rank === null
        ? "暂无排名，连续打卡至少 1 天即可参与排名。"
        : `第 ${me.rank} 名${me.rank > data.leaderboard_size ? `（未进入前 ${data.leaderboard_size} 名）` : ""}`;
    mine.append(
      element("p", `${me.streak_days} 天`, "leaderboard-streak"),
      element("p", rankText, "leaderboard-my-rank")
    );
    for (const entry of data.entries) {
      const row = element("tr");
      row.append(
        element("td", `第 ${entry.rank} 名`, "leaderboard-rank"),
        element("td", entry.display_name),
        element("td", `${entry.streak_days} 天`, "leaderboard-days")
      );
      entries.append(row);
    }
    status.textContent = data.entries.length ? "" : "暂无上榜用户，完成一次复习评分，开始连续打卡吧。";
    $("#leaderboard-table-wrap").hidden = !data.entries.length;
  } catch (error) {
    if (user === currentUser && user && view === "leaderboard") {
      status.textContent = "排行榜加载失败，请点击上方“刷新”重试。";
    }
    throw error;
  } finally {
    if (user === currentUser && user && view === "leaderboard") {
      page.setAttribute("aria-busy", "false");
    }
  }
}

function resetGroups() {
  groupsGeneration += 1;
  selectedGroupId = null;
  studyGroup = null;
  $("#groups-page").hidden = true;
  $("#groups-page").setAttribute("aria-busy", "false");
  $("#groups-overview").hidden = false;
  $("#groups-detail").hidden = true;
  $("#groups-detail-content").hidden = true;
  $("#groups-list").replaceChildren();
  $("#groups-members").replaceChildren();
  $("#groups-member-actions").hidden = true;
  $("#groups-weakness").replaceChildren();
  $("#groups-detail-title").textContent = "";
  $("#groups-invite-code").textContent = "";
  $("#groups-delete").hidden = true;
  $("#groups-status").textContent = "";
  $("#groups-status").classList.remove("error");
  $("#groups-retry").hidden = true;
  $("#groups-create-form").reset();
  $("#groups-join-form").reset();
}

async function requestGroups(action, onSuccess, loadingText, { retry = false } = {}) {
  if (!user || view !== "groups") return;
  const generation = ++groupsGeneration;
  const userId = user.id;
  const isCurrent = () => user?.id === userId && view === "groups"
    && generation === groupsGeneration;
  const page = $("#groups-page");
  const status = $("#groups-status");
  page.setAttribute("aria-busy", "true");
  status.classList.remove("error");
  status.textContent = loadingText;
  $("#groups-retry").hidden = true;
  try {
    const data = await action();
    if (!isCurrent()) return;
    await onSuccess(data);
    if (isCurrent()) status.textContent = "";
  } catch (error) {
    if (!isCurrent()) return;
    status.classList.add("error");
    status.textContent = `暂时无法完成：${error.message || "请检查网络后重试"}`;
    $("#groups-retry").hidden = !retry;
  } finally {
    if (isCurrent()) page.setAttribute("aria-busy", "false");
  }
}

function renderGroups(groups) {
  const list = $("#groups-list");
  list.replaceChildren();
  if (!groups.length) {
    list.append(element("p", "还没有加入小组。创建一个，或用熟人的邀请码加入吧。", "muted"));
    return;
  }
  for (const group of groups) {
    const button = element("button", "", "record-button");
    button.type = "button";
    button.disabled = busy;
    button.append(
      element("strong", group.name),
      element("small", `${group.member_count} 位成员${group.is_creator ? " · 我创建的小组" : ""}`, "muted")
    );
    button.addEventListener("click", () => run(() => openStudyGroup(group.id)));
    list.append(button);
  }
}

async function loadGroups() {
  selectedGroupId = null;
  studyGroup = null;
  $("#groups-overview").hidden = false;
  $("#groups-detail").hidden = true;
  $("#groups-detail-content").hidden = true;
  $("#groups-list").replaceChildren();
  await requestGroups(
    () => api("/api/groups"),
    (data) => renderGroups(data.groups),
    "正在加载你的小组…",
    { retry: true }
  );
}

function renderStudyGroup(group) {
  studyGroup = group;
  selectedGroupId = group.id;
  $("#groups-overview").hidden = true;
  $("#groups-detail").hidden = false;
  $("#groups-detail-content").hidden = false;
  $("#groups-detail-title").textContent = group.name;
  $("#groups-invite-code").textContent = group.invite_code;
  $("#groups-members-title").textContent = `小组成员 · ${group.members.length} 人`;
  $("#groups-member-actions").hidden = !group.is_creator;
  const members = $("#groups-members");
  members.replaceChildren();
  // 服务端已按连续天数降序、用户名升序排列，保留相同的排序口径。
  for (const member of group.members) {
    const row = element("tr");
    row.append(
      element("td", member.username),
      element("td", `${member.current_streak_days} 天`, "leaderboard-days")
    );
    if (group.is_creator) {
      const actions = element("td", "", "groups-member-actions");
      if (member.id !== user.id) {
        const remove = element("button", "移除", "danger");
        remove.type = "button";
        remove.disabled = busy;
        remove.addEventListener("click", () => run(() => removeStudyGroupMember(member)));
        actions.append(remove);
      }
      row.append(actions);
    }
    members.append(row);
  }
  const signals = $("#groups-weakness");
  signals.replaceChildren();
  const zones = Object.entries(group.weakness_by_zone);
  if (!zones.length) {
    signals.append(element("p", "还没有足够的学习记录，积累一些易错点后再来看看。", "muted"));
  }
  for (const [zone, signal] of zones) {
    const card = element("article", "", "panel growth-zone");
    card.append(
      element("h4", zone),
      element("p", signal.struggling_ratio == null
        ? "这个分区的有效样本还不够，暂不显示群体挣扎占比。"
        : `群体挣扎占比 ${Math.round(signal.struggling_ratio * 100)}%`, "growth-headline"),
      element("p", "统计本组在该分区记录过易错点的非体验成员，不展示个人错题。", "muted growth-community")
    );
    signals.append(card);
  }
  $("#groups-delete").hidden = !group.is_creator;
}

async function openStudyGroup(groupId) {
  selectedGroupId = groupId;
  studyGroup = null;
  $("#groups-overview").hidden = true;
  $("#groups-detail").hidden = false;
  $("#groups-detail-content").hidden = true;
  await requestGroups(
    () => api(`/api/groups/${groupId}`),
    renderStudyGroup,
    "正在加载小组成员与学习信号…",
    { retry: true }
  );
}

async function submitStudyGroup(form, path, fieldName) {
  if (!user || view !== "groups") return;
  const value = form.elements.namedItem(fieldName).value.trim();
  if (!value) {
    $("#groups-status").classList.add("error");
    $("#groups-status").textContent = fieldName === "name" ? "请输入小组名称。" : "请输入邀请码。";
    return;
  }
  await requestGroups(
    () => api(path, { method: "POST", body: JSON.stringify({ [fieldName]: value }) }),
    (data) => {
      form.reset();
      renderStudyGroup(data);
    },
    fieldName === "name" ? "正在创建小组…" : "正在通过邀请码加入小组…"
  );
}

async function leaveStudyGroup(dissolve = false) {
  if (!user || view !== "groups" || !studyGroup) return;
  if (dissolve && (!studyGroup.is_creator || !confirm(`确定解散“${studyGroup.name}”吗？所有成员都将退出此小组。`))) return;
  const groupId = studyGroup.id;
  await requestGroups(
    () => api(`/api/groups/${groupId}${dissolve ? "" : "/leave"}`, { method: dissolve ? "DELETE" : "POST" }),
    async () => {
      message(dissolve ? "小组已解散。" : "已退出小组。");
      await loadGroups();
    },
    dissolve ? "正在解散小组…" : "正在退出小组…"
  );
}

async function removeStudyGroupMember(member) {
  if (!user || view !== "groups" || !studyGroup || !studyGroup.is_creator || member.id === user.id) return;
  if (!confirm(`确定将“${member.username}”移出“${studyGroup.name}”吗？`)) return;
  const groupId = studyGroup.id;
  await requestGroups(
    () => api(`/api/groups/${groupId}/members/${member.id}`, { method: "DELETE" }),
    async () => {
      message("已移除成员。");
      await openStudyGroup(groupId);
    },
    "正在移除成员…"
  );
}

function resetAchievementShareCard() {
  $("#achievements-share").hidden = true;
  const image = $("#achievements-share-image");
  image.hidden = true;
  image.removeAttribute("src");
  $("#achievements-share-hint").hidden = true;
  $("#achievements-share-error").hidden = true;
  $("#achievements-share-error").textContent = "";
}

function resetAchievements() {
  cancelAchievementStamps();
  achievementsGeneration += 1;
  resetAchievementShareCard();
  $("#achievements-page").hidden = true;
  $("#achievements-page").setAttribute("aria-busy", "false");
  $("#achievements-list").replaceChildren();
  $("#achievements-summary").replaceChildren();
  $("#achievements-summary").hidden = true;
  $("#achievements-status").textContent = "";
  $("#achievements-status").classList.remove("error");
  $("#achievements-retry").hidden = true;
}

function renderAchievements(achievements) {
  const emblems = new Map();
  const groups = [
    ["streak", "连续打卡", "每天回来复习，让坚持形成习惯。"],
    ["mistakes", "易错点积累", "把具体错因记下来，让每一次做错都有收获。"],
    ["zones", "多分区探索", "到不同分区留下记录，拓宽自己的解题视野。"],
    ["practice", "AI 深度使用", "围绕易错点生成新练习，换一道题检验理解。只统计成功生成的练习题。"],
    ["analysis", "薄弱点分析", "积累易错点后，完成一次分析，找到下一步练习方向。"],
  ];
  const unlockedCount = achievements.filter((badge) => badge.unlocked).length;
  const summary = $("#achievements-summary");
  summary.replaceChildren(
    element("strong", `已解锁 ${unlockedCount} / ${achievements.length} 枚徽章`),
    element("p", unlockedCount
      ? "看看下一枚徽章还差多少，把目标变成今天的一小步。"
      : "从记录第一条易错点开始，点亮你的第一枚徽章。")
  );
  summary.hidden = false;
  $("#achievements-share").hidden = false;

  const list = $("#achievements-list");
  list.replaceChildren();
  for (const [category, title, description] of groups) {
    const badges = achievements.filter((badge) => badge.category === category);
    if (!badges.length) continue;
    const group = element("section", "", "achievement-group");
    const heading = element("div", "", "achievement-group-heading");
    const groupTitle = element("h3", title);
    groupTitle.id = `achievement-category-${category}`;
    group.setAttribute("aria-labelledby", groupTitle.id);
    heading.append(
      groupTitle,
      element("span", `${badges.filter((badge) => badge.unlocked).length} / ${badges.length} 已解锁`, "achievement-group-count")
    );
    const grid = element("div", "", "achievement-grid");
    for (const badge of badges) {
      const { current, target, unit, message: progressMessage } = badge.progress;
      const card = element("article", "", `achievement-card ${badge.unlocked ? "is-unlocked" : "is-locked"}`);
      const top = element("div", "", "achievement-card-top");
      const emblem = element("span", "", "achievement-emblem");
      emblems.set(badge.key, emblem);
      emblem.setAttribute("aria-hidden", "true");
      emblem.append(element("strong", String(target)), element("span", unit));
      top.append(emblem, element("span", badge.unlocked ? "✓ 已解锁" : "待解锁", "achievement-state"));
      const progress = document.createElement("progress");
      progress.max = target;
      progress.value = Math.min(Number(current), target);
      progress.setAttribute("aria-label", `${badge.name}：当前 ${current} ${unit}，目标 ${target} ${unit}`);
      card.append(
        top,
        element("h4", badge.name),
        element("p", badge.description, "achievement-description"),
        element("p", `当前 ${current} ${unit} · 目标 ${target} ${unit}`, "achievement-progress-count"),
        progress,
        element("p", progressMessage, "achievement-progress-message")
      );
      grid.append(card);
    }
    group.append(heading, element("p", description, "achievement-group-description"), grid);
    list.append(group);
  }
  return emblems;
}

async function loadAchievements() {
  cancelAchievementStamps();
  const generation = ++achievementsGeneration;
  resetAchievementShareCard();
  const userId = user.id;
  const isCurrent = () => Boolean(user) && user.id === userId
    && generation === achievementsGeneration && view === "achievements";
  const page = $("#achievements-page");
  const status = $("#achievements-status");
  page.setAttribute("aria-busy", "true");
  $("#achievements-list").replaceChildren();
  $("#achievements-summary").replaceChildren();
  $("#achievements-summary").hidden = true;
  $("#achievements-retry").hidden = true;
  status.classList.remove("error");
  status.textContent = "正在查看你的徽章进度…";
  try {
    const data = await api("/api/achievements");
    if (!isCurrent()) return;
    const emblems = renderAchievements(data.achievements);
    status.textContent = "";
    queueAchievementStamps(data.achievements, emblems, isCurrent);
  } catch (error) {
    if (!isCurrent()) return;
    status.classList.add("error");
    status.textContent = `徽章暂时无法加载：${error.message || "请检查网络后重试"}`;
    $("#achievements-retry").hidden = false;
  } finally {
    if (isCurrent()) page.setAttribute("aria-busy", "false");
  }
}

function resetWeeklyRecap() {
  weeklyRecapGeneration += 1;
  $("#weekly-recap-page").hidden = true;
  $("#weekly-recap-page").setAttribute("aria-busy", "false");
  $("#weekly-recap-cards").replaceChildren();
  $("#weekly-recap-summary").replaceChildren();
  $("#weekly-recap-summary").hidden = true;
  $("#weekly-recap-status").textContent = "";
  $("#weekly-recap-status").classList.remove("error");
  $("#weekly-recap-retry").hidden = true;
}

function renderWeeklyRecap(data) {
  const summary = $("#weekly-recap-summary");
  summary.replaceChildren(
    element("strong", `${data.week_start} — ${data.week_end}`),
    element("p", data.mistakes_recorded || data.reviews_completed || data.practice_generated
      ? "回看这 7 天的积累，也看看和上周相比，你的学习节奏有什么变化。"
      : "这 7 天还没有学习记录。从记下一条易错点或完成一次复习开始吧。")
  );
  summary.hidden = false;

  const metrics = [
    ["mistakes_recorded", "易错点新增", "条", "按所属题目的记录日期统计。"],
    ["reviews_completed", "复习完成", "次", "每完成一次复习评分，计一次复习。"],
    ["practice_generated", "练习生成", "题", "只统计成功生成的练习题。"],
    ["active_days", "活跃天数", "天", "这 7 天里有复习的日期，同一天只计一次。"],
    ["zones_touched", "涉及分区数", "个", "新增易错点或完成复习的分区，同一分区只计一次。"],
    ["current_streak_days", "当前连续打卡", "天", "按全部复习记录计算，不受本周窗口限制。"],
  ];
  const cards = $("#weekly-recap-cards");
  cards.replaceChildren();
  for (const [key, title, unit, description] of metrics) {
    const card = element("article", "", "weekly-recap-card");
    const value = element("p", "", "weekly-recap-value");
    value.append(element("strong", String(data[key])), element("span", unit));
    card.append(
      element("h3", title),
      value,
      element("p", description, "weekly-recap-description")
    );
    if (Object.hasOwn(data.previous_week, key)) {
      card.append(element("p", `本周 ${data[key]} ${unit} · 上周 ${data.previous_week[key]} ${unit}`, "weekly-recap-comparison"));
    }
    cards.append(card);
  }
}

async function loadWeeklyRecap() {
  const generation = ++weeklyRecapGeneration;
  const userId = user.id;
  const isCurrent = () => Boolean(user) && user.id === userId
    && generation === weeklyRecapGeneration && view === "weekly-recap";
  const page = $("#weekly-recap-page");
  const status = $("#weekly-recap-status");
  page.setAttribute("aria-busy", "true");
  $("#weekly-recap-cards").replaceChildren();
  $("#weekly-recap-summary").replaceChildren();
  $("#weekly-recap-summary").hidden = true;
  $("#weekly-recap-retry").hidden = true;
  status.classList.remove("error");
  status.textContent = "正在整理你的本周学习战报…";
  try {
    const data = await api("/api/insights/weekly-recap");
    if (!isCurrent()) return;
    renderWeeklyRecap(data);
    status.textContent = "";
  } catch (error) {
    if (!isCurrent()) return;
    status.classList.add("error");
    status.textContent = `战报暂时无法加载：${error.message || "请检查网络后重试"}`;
    $("#weekly-recap-retry").hidden = false;
  } finally {
    if (isCurrent()) page.setAttribute("aria-busy", "false");
  }
}

function resetWeaknessAnalysis() {
  weaknessGeneration += 1;
  weaknessAnalysis = null;
  weaknessPending = false;
  weaknessQuotaAvailable = false;
  $("#weakness-page").hidden = true;
  $("#weakness-page").setAttribute("aria-busy", "false");
  $("#weakness-result").replaceChildren();
  $("#weakness-result").hidden = true;
  $("#weakness-empty").hidden = true;
  $("#weakness-empty-text").textContent = "";
  $("#weakness-count").textContent = "";
  $("#weakness-quota").textContent = "";
  $("#weakness-status").textContent = "";
  $("#weakness-status").classList.remove("error");
  $("#weakness-analyze").dataset.blocked = "1";
  $("#weakness-analyze").disabled = true;
}

function weaknessRequestCurrent(generation, userId) {
  return Boolean(user) && user.id === userId && weaknessGeneration === generation;
}

function renderWeaknessControls() {
  const minimum = weaknessAnalysis?.minimum_mistakes ?? 5;
  const count = weaknessAnalysis?.mistake_count;
  const insufficient = Number.isInteger(count) && count < minimum;
  $("#weakness-count").textContent = Number.isInteger(count)
    ? insufficient
      ? `已积累 ${count} 条易错点，再记录 ${minimum - count} 条就可以开始分析。`
      : `已积累 ${count} 条易错点，可以开始分析。`
    : "先积累至少 5 条易错点，让分析有足够的线索。";
  const remaining = user?.ai_daily_remaining;
  const limit = user?.ai_daily_limit;
  const quotaKnown = weaknessQuotaAvailable && Number.isInteger(remaining) && Number.isInteger(limit);
  $("#weakness-quota").textContent = quotaKnown
    ? `今日 AI 额度剩余 ${remaining} / ${limit} 次${remaining === 0 ? "，明天可再次分析，或前往“我的套餐”查看额度。" : "。"}`
    : weaknessPending ? "正在读取今日 AI 额度…" : "暂时无法读取剩余额度，分析前会由服务端检查额度。";
  const button = $("#weakness-analyze");
  button.dataset.blocked = weaknessPending || insufficient || (quotaKnown && remaining === 0) ? "1" : "0";
  button.disabled = busy || button.dataset.blocked === "1";
  button.textContent = weaknessPending
    ? "正在处理，请稍候…"
    : weaknessAnalysis?.insight
      ? "更新我的薄弱点分析 · 消耗 1 次 AI 额度"
      : "分析我的薄弱点 · 消耗 1 次 AI 额度";
  $("#weakness-page").setAttribute("aria-busy", String(weaknessPending));
}

function renderWeaknessAnalysis() {
  const insight = weaknessAnalysis?.insight;
  const result = $("#weakness-result");
  result.replaceChildren();
  result.hidden = !insight;
  $("#weakness-empty").hidden = Boolean(insight);
  $("#weakness-empty-text").textContent = weaknessAnalysis?.message
    || "还没有分析过。点击上方按钮，把积累的错因和复习评分连起来，看看哪些问题值得先解决。";
  renderWeaknessControls();
  if (!insight) return;

  const { content } = insight;
  const overview = element("section", "", "panel weakness-overview");
  overview.append(
    element("p", "最近一次分析", "eyebrow"),
    element("h3", content.patterns.length ? "值得优先关注的规律" : "目前的记录还不足以确认重复规律"),
    element("p", content.summary, "multiline"),
    element("p", `更新于 ${timestamp(insight.created_at)} · 只保留最近一次分析`, "muted")
  );
  const sample = content.sample;
  overview.append(element("p", `本次依据：${sample.problem_count} 道题 · ${sample.mistake_count} 条易错点 · ${sample.review_count} 次复习评分`, "weakness-sample"));
  if (sample.period_start && sample.period_end) {
    overview.append(element("p", `题目记录范围：${timestamp(sample.period_start)} 至 ${timestamp(sample.period_end)}`, "muted"));
  }
  if (weaknessAnalysis.status === "insufficient_data") {
    overview.append(element("p", "以下是上次保存的分析；当前易错点数量不足，暂时无法更新。", "muted"));
  }
  result.append(overview);
  content.patterns.forEach((pattern, index) => {
    const card = element("article", "", "panel weakness-pattern");
    const heading = element("div", "", "weakness-pattern-heading");
    heading.append(
      element("h3", `${index + 1}. ${pattern.title}`),
      element("span", pattern.confidence, "weakness-confidence")
    );
    card.append(heading, element("p", pattern.explanation, "multiline"));
    const evidence = element("ul", "", "weakness-evidence");
    for (const item of pattern.evidence) {
      const entry = element("li");
      entry.append(
        element("strong", `${item.zone} · ${item.title}`),
        element("p", item.observation, "multiline")
      );
      evidence.append(entry);
    }
    card.append(element("h4", "哪些记录支持这个判断"), evidence);
    const action = element("div", "", "weakness-action");
    action.append(element("h4", "下一步可以这样练"), element("p", pattern.action, "multiline"));
    card.append(action);
    result.append(card);
  });
  result.append(element("p", "分析是基于当前样本的学习建议。继续记录具体错因、如实复习评分，下次更新时再验证这些判断。", "muted"));
}

async function loadWeaknessAnalysis() {
  const generation = ++weaknessGeneration;
  const userId = user.id;
  weaknessPending = true;
  weaknessQuotaAvailable = false;
  renderWeaknessControls();
  const status = $("#weakness-status");
  status.classList.remove("error");
  status.textContent = "正在读取已保存的分析，不消耗 AI 额度…";
  const [analysis, profile, growth] = await Promise.allSettled([
    api("/api/insights/weakness-analysis"),
    api("/api/me"),
    api("/api/insights/growth"),
  ]);
  if (!weaknessRequestCurrent(generation, userId)) return;
  if (profile.status === "fulfilled") {
    user = profile.value;
    weaknessQuotaAvailable = true;
    updateUserInfo();
  }
  weaknessPending = false;
  if (analysis.status === "fulfilled") {
    weaknessAnalysis = analysis.value;
    renderWeaknessAnalysis();
    status.textContent = weaknessAnalysis.message || "";
  } else {
    renderWeaknessControls();
    status.classList.add("error");
    status.textContent = `读取分析失败：${analysis.reason.message || "请检查网络后重试"}。可点击上方“刷新”重新读取。`;
  }
  // 独立降级：成长趋势和薄弱点分析是两个互不依赖的接口，一个失败不影响另一个展示。
  growthZones = growth.status === "fulfilled" ? growth.value.zones : null;
  renderGrowthInsights(growth.status === "fulfilled");
}

function renderGrowthInsights(loadedOk) {
  const panel = $("#growth-summary");
  const empty = $("#growth-empty");
  const list = $("#growth-zones");
  list.replaceChildren();

  if (!loadedOk) {
    panel.hidden = true;
    empty.hidden = false;
    empty.textContent = "暂时无法读取成长趋势，可稍后刷新重试。";
    return;
  }
  if (!growthZones || !growthZones.length) {
    panel.hidden = true;
    empty.hidden = false;
    empty.textContent = "还没有足够的历史记录，积累几条易错点后回来看看趋势。";
    return;
  }
  empty.hidden = true;
  panel.hidden = false;
  for (const zone of growthZones) {
    const card = element("article", "", "panel growth-zone");
    if (zone.quiet_streak) card.classList.add("growth-zone-quiet");
    card.append(element("h4", zone.zone));
    card.append(element(
      "p",
      zone.quiet_streak
        ? `已经 ${zone.days_since_last_mistake} 天没有新的易错点了。`
        : zone.days_since_last_mistake === 0
          ? "今天刚记录了新的易错点。"
          : `距离上一次记录新的易错点已经 ${zone.days_since_last_mistake} 天。`,
      "growth-headline"
    ));
    card.append(element(
      "p",
      `过去 30 天 ${zone.recent_30_days} 条，再往前 30 天 ${zone.prior_30_days} 条 · 累计 ${zone.total_mistakes} 条`,
      "muted"
    ));
    card.append(element(
      "p",
      zone.community_struggling_ratio === null
        ? "这个方向记录的人还不够多，暂时看不出群体趋势。"
        : `全站有记录的用户里，${Math.round(zone.community_struggling_ratio * 100)}% 的人也在这个方向反复出错（≥3 条易错点）。`,
      "muted growth-community"
    ));
    list.append(card);
  }
}

async function analyzeWeakness() {
  if (!user || weaknessPending || view !== "insights" || $("#weakness-analyze").dataset.blocked === "1") return;
  const generation = ++weaknessGeneration;
  const userId = user.id;
  const status = $("#weakness-status");
  weaknessPending = true;
  renderWeaknessControls();
  status.classList.remove("error");
  status.textContent = "正在结合错题与复习历史寻找重复规律，请稍候…";
  try {
    const data = await api("/api/insights/weakness-analysis", { method: "POST" });
    if (!weaknessRequestCurrent(generation, userId)) return;
    weaknessAnalysis = data;
    renderWeaknessAnalysis();
    status.textContent = data.message || "分析已更新，可以查看下方的判断依据和练习建议。";
  } catch (error) {
    if (!weaknessRequestCurrent(generation, userId)) return;
    status.classList.add("error");
    status.textContent = `${error.message || "分析失败，请稍后重试"}${weaknessAnalysis?.insight ? "。已保留上次分析结果。" : ""}`;
  } finally {
    if (weaknessRequestCurrent(generation, userId)) {
      weaknessQuotaAvailable = false;
      try {
        const profile = await api("/api/me");
        if (weaknessRequestCurrent(generation, userId)) {
          user = profile;
          weaknessQuotaAvailable = true;
          updateUserInfo();
        }
      } catch {
        // 调用失败同样可能已扣额度；读取失败时不显示过期的剩余次数。
      }
      if (weaknessRequestCurrent(generation, userId)) {
        weaknessPending = false;
        renderWeaknessControls();
      }
    }
  }
}

function yuan(cents) {
  return `¥${(cents / 100).toFixed(2)}`;
}

function planFacts(entries) {
  const facts = element("dl", "", "plan-facts");
  for (const [label, value] of entries) {
    facts.append(element("dt", label), element("dd", value));
  }
  return facts;
}

async function refreshPlanSubscription() {
  const currentUser = user;
  const updated = await api("/api/me");
  if (user !== currentUser || !user || view !== "plan") return false;
  user = updated;
  updateUserInfo();
  const entries = [
    ["套餐", user.plan_name || "当前使用免费额度"],
    ["状态", user.plan_active ? "有效" : "无有效套餐，当前使用免费额度"],
  ];
  if (user.plan_expires_at) {
    entries.push(["到期时间", timestamp(user.plan_expires_at)]);
  }
  entries.push(
    ["今日 AI 用量", `${user.ai_daily_used} / ${user.ai_daily_limit} 次`],
    ["今日剩余", `${user.ai_daily_remaining} 次`]
  );
  $("#plan-subscription").replaceChildren(planFacts(entries));
  $("#plan-trial-note").hidden = !user.is_trial;
  return true;
}

async function loadPlanOrders() {
  const currentUser = user;
  const { orders } = await api("/api/orders");
  if (user !== currentUser || !user || view !== "plan") return false;
  const list = $("#plan-orders-list");
  list.replaceChildren();
  if (!orders.length) {
    list.append(element("p", "暂无订单。", "muted"));
    return true;
  }
  const statuses = {
    pending: "待支付",
    paid: "已支付",
    failed: "支付失败",
    closed: "已关闭",
    refunded: "已退款",
  };
  for (const order of orders) {
    const row = element("article", "", "plan-order-row");
    const heading = element("div", "", "plan-order-heading");
    heading.append(
      element("h4", order.plan_name),
      element("span", yuan(order.amount_cents), "plan-order-amount")
    );
    row.append(heading, planFacts([
      ["订单号", order.id],
      ["状态", statuses[order.status] || "未知状态"],
      ["创建时间", timestamp(order.created_at)],
    ]));
    if (order.status === "paid") {
      const refund = element("button", "申请退款", "danger");
      refund.type = "button";
      refund.disabled = busy;
      refund.setAttribute("aria-label", `申请退款：${order.plan_name}，订单 ${order.id}`);
      refund.addEventListener("click", () => run(() => refundPlanOrder(order)));
      const actions = element("div", "", "actions");
      actions.append(refund);
      row.append(actions);
    }
    list.append(row);
  }
  return true;
}

async function refundPlanOrder(order) {
  if (!confirm(`确认申请退回「${order.plan_name}」订单 ${order.id} 的全部款项 ${yuan(order.amount_cents)}？\n\n退款成功后会立即清空当前整体套餐，即使当前套餐来自其他订单；不按剩余天数折算，也不恢复今天已用掉的 AI 次数。此操作不可撤销。`)) return;
  const currentUser = user;
  message();
  let refunded;
  try {
    const result = await api(`/api/orders/${encodeURIComponent(order.id)}/refund`, { method: "POST" });
    refunded = result.order;
  } catch (error) {
    // 另一页面可能已经退款；刷新状态后仍保留原始错误，未确认结果时允许重试。
    if (error.status === 409 && user === currentUser && view === "plan") {
      try {
        if (await refreshPlanSubscription()) await loadPlanOrders();
      } catch {
        // 保留原始退款错误。
      }
    }
    throw error;
  }
  if (user !== currentUser || !user || view !== "plan") return;
  if (planPurchase && planPurchase.order.id === refunded.id) {
    planPurchase.order = refunded;
    renderPlanOrder();
  }
  try {
    if (!await refreshPlanSubscription()) return;
    if (!await loadPlanOrders()) return;
    message("退款成功，当前套餐已收回，今日已用 AI 次数保持不变。");
  } catch (error) {
    if (error.status === 401) throw error;
    message("退款成功，当前套餐已收回；页面刷新失败，请稍后刷新查看。", true);
  }
}

function renderPlans(plans) {
  const list = $("#plan-list");
  list.replaceChildren();
  if (!plans.length) {
    list.append(element("p", "暂无可购买的套餐。", "muted"));
    return;
  }
  for (const plan of plans) {
    const card = element("article", "", "plan-card panel");
    card.append(
      element("h4", plan.name),
      element("p", yuan(plan.price_cents), "plan-price"),
      element("p", `${plan.period_days} 天`, "muted"),
      element("p", `每天 ${plan.ai_daily_limit} 次 AI 生成`)
    );
    if (!plan.purchasable) {
      card.append(element("p", "即将开放购买，敬请期待", "muted"));
    } else if (!user.is_trial) {
      const buy = element("button", "购买", "primary");
      buy.type = "button";
      buy.setAttribute("aria-label", `购买${plan.name}`);
      buy.addEventListener("click", () => run(async () => {
        if (user.is_trial) return;
        message();
        const purchase = await api("/api/orders", {
          method: "POST",
          body: JSON.stringify({ plan_id: plan.id, channel: "alipay" }),
        });
        stopOrderPolling();
        planPurchase = purchase;
        renderPlanOrder();
        if (purchase.order.status === "pending") {
          startOrderPolling();
          await loadPlanOrders();
        } else await finishPlanOrder();
      }));
      const actions = element("div", "", "actions");
      actions.append(buy);
      card.append(actions);
    }
    list.append(card);
  }
}

function renderPlanOrder() {
  $("#plan-catalog").hidden = Boolean(planPurchase);
  $("#plan-order").hidden = !planPurchase;
  if (!planPurchase) return;

  const { order, payment } = planPurchase;
  $("#plan-order-details").replaceChildren(planFacts([
    ["订单号", order.id],
    ["金额", yuan(order.amount_cents)],
  ]));
  const statuses = {
    pending: "等待支付，每 3 秒自动查询一次，最多查询 5 分钟。",
    paid: "支付成功。",
    failed: "支付失败，可以返回套餐列表重新下单。",
    closed: "订单已关闭，可以返回套餐列表重新下单。",
    refunded: "订单已退款，可以返回套餐列表。",
  };
  $("#plan-order-status").textContent = statuses[order.status] || "未知订单状态，请稍后刷新。";
  const paymentBox = $("#plan-payment");
  paymentBox.replaceChildren();
  if (order.status !== "pending") return;

  const paymentText = payment.qr_code_url || "";
  const isMock = payment.provider === "mock" || paymentText.startsWith("mock://");
  const providerName = { alipay: "支付宝", wechat: "微信支付" }[payment.provider] || "对应 App";
  // 支付内容来自接口，仅允许预期的链接协议；原文始终以文本节点展示。
  if (/^(https?:\/\/|alipays?:\/\/|weixin:\/\/|mock:\/\/)/i.test(paymentText)) {
    const link = element("a", isMock ? "Mock 支付链接（仅占位）" : `打开${providerName}完成支付`, "plan-payment-link");
    link.href = paymentText;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    paymentBox.append(link);
  }
  if (paymentText) {
    paymentBox.append(element("pre", paymentText, "code plan-payment-text"));
  }
  paymentBox.append(element("p", isMock
    ? "这是本地 Mock 支付占位内容，不能扫码或完成真实付款。"
    : `手机上点击链接直接跳转${providerName}完成支付；电脑上可先将上面的原始文本生成二维码，再用${providerName}扫一扫。当前页面暂不提供图形二维码，不能直接扫描这段文字。`,
  "muted"));
  if (!paymentText) {
    paymentBox.append(element("p", "暂未取得支付链接，请稍后刷新查看订单状态。", "muted"));
  }
}

function stopOrderPolling() {
  clearInterval(orderPollTimer);
  orderPollTimer = null;
  // 使已经发出的旧查询失效，避免切换页面或账号后写回旧订单。
  orderPollGeneration += 1;
}

async function finishPlanOrder() {
  stopOrderPolling();
  const status = planPurchase.order.status;
  if (!await refreshPlanSubscription()) return;
  if (!await loadPlanOrders()) return;
  if (status === "paid") {
    message(user.plan_active
      ? "购买成功，套餐已生效。"
      : "订单已支付；当前无有效套餐，请查看当前订阅。");
  } else {
    message($("#plan-order-status").textContent, true);
  }
}

async function checkPlanOrder(generation) {
  const purchase = planPurchase;
  const { order } = await api(`/api/orders/${encodeURIComponent(purchase.order.id)}`);
  if (generation !== orderPollGeneration || planPurchase !== purchase || view !== "plan" || !user) return;
  const statusChanged = purchase.order.status !== order.status;
  purchase.order = order;
  // pending 时保留支付文本的选区和链接焦点，方便复制或打开。
  if (statusChanged) renderPlanOrder();
  if (statusChanged && order.status !== "pending") await finishPlanOrder();
}

function startOrderPolling() {
  stopOrderPolling();
  const generation = orderPollGeneration;
  const deadline = Date.now() + ORDER_POLL_DURATION;
  orderPollTimer = setInterval(() => {
    if (generation !== orderPollGeneration) return;
    if (view !== "plan" || !user) {
      stopOrderPolling();
      return;
    }
    // run 会串行处理异步操作；其他操作进行中时跳过查询和超时提示。
    if (busy) return;
    if (Date.now() >= deadline) {
      stopOrderPolling();
      const text = "暂未检测到支付结果，可以稍后刷新这个页面查看。";
      $("#plan-order-status").textContent = text;
      message(text);
      return;
    }
    run(async () => {
      await checkPlanOrder(generation);
    });
  }, ORDER_POLL_INTERVAL);
}

async function loadPlanPage() {
  stopOrderPolling();
  if (!await refreshPlanSubscription()) return;
  if (user.is_trial) planPurchase = null;
  const currentUser = user;
  const { plans } = await api("/api/plans");
  if (user !== currentUser || !user || view !== "plan") return;
  renderPlans(plans);
  renderPlanOrder();
  if (!await loadPlanOrders()) return;
  if (planPurchase) {
    if (planPurchase.order.status === "pending") startOrderPolling();
    await checkPlanOrder(orderPollGeneration);
  }
}

async function loadList() {
  const zoneParam = $("#zone-filter").value;
  const query = `due_only=${view === "today"}` + (zoneParam ? `&zone=${encodeURIComponent(zoneParam)}` : "");
  const data = await api(`/api/mistakes?${query}`);
  user.today = data.today;
  updateUserInfo();

  $("#list-title").textContent =
    view === "today" ? "今日复习" : "全部记录";
  $("#list-summary").textContent = view === "today"
    ? `${data.today} · 今天有 ${data.items.length} 条易错点待复习（含逾期）`
    : `共 ${data.items.length} 条易错点 · 每一条，都有自己的复习节奏`;

  $("#cards").replaceChildren();
  clearDetail();

  if (!data.items.length) {
    $("#cards").append(element(
      "p",
      view === "today" ? "今天没有待复习的易错点。" : "你的第一条记录，会出现在这里。",
      "muted empty-list"
    ));
    clearDetail(
      view === "today" ? "今天的复习，告一段落" : "从一道做错的题开始",
      view === "today"
        ? "可以去「全部记录」回看笔记，也可以在「新增记录」留下今天的新发现。"
        : "点击「新增记录」，留下代码、思路和错因。每条易错点都会单独安排复习。"
    );
    return;
  }

  for (const item of data.items) {
    const button = element("button", "", "record-button");
    button.type = "button";
    button.dataset.id = String(item.id);
    button.setAttribute("aria-pressed", "false");
    button.append(
      element("strong", item.title),
      element("span", item.description || "错因待 AI 诊断", "record-description"),
      element("small", `${item.zone} · ${item.due_date <= data.today ? "待复习" : "下次复习"} · ${item.due_date}`, "muted")
    );
    button.addEventListener("click", () => run(() => openMistake(item.id)));
    $("#cards").append(button);
  }
}

async function openMistake(id) {
  const item = await api(`/api/mistakes/${id}`);
  user.today = item.today;
  updateUserInfo();

  document.querySelectorAll(".record-button").forEach((button) => {
    button.classList.toggle("selected", Number(button.dataset.id) === id);
    button.setAttribute("aria-pressed", String(Number(button.dataset.id) === id));
  });

  renderDetail(item);
}

const VARIANT_SECTION_PATTERN =
  /^【错因】\s*\n([\s\S]*?)\n【讲解】\s*\n([\s\S]*?)\n【核心知识点】\s*\n([\s\S]*?)\n【练习题】\s*\n([\s\S]*)$/;

function renderVariantDescription(text) {
  const match = text.trim().match(VARIANT_SECTION_PATTERN);
  if (!match) {
    // 新变体只含单题正文（编程题还带样例）；也兼容更早的纯文本题目。
    return element("pre", text, "prose");
  }
  const [, summary, explanation, knowledge, question] = match;
  const wrap = element("div", "", "variant-sections");
  for (const [label, content] of [
    ["错因", summary], ["讲解", explanation],
    ["核心知识点", knowledge], ["练习题", question],
  ]) {
    wrap.append(element("h4", label), element("p", content.trim(), "multiline"));
  }
  return wrap;
}

function renderVariant(variant, isCodeZone) {
  const box = document.createElement("details");
  box.className = "variant";
  box.open = variant.result === "unattempted";

  const summary = element(
    "summary",
    `变体 #${variant.id} · ${resultLabels[variant.result]}`
  );
  const savedAt = element(
    "p",
    variant.result_updated_at
      ? `结果保存于：${timestamp(variant.result_updated_at)}`
      : "做完后，在下面记下这次的结果。",
    "muted"
  );

  const form = document.createElement("form");
  // 旧数学题没有参考答案，继续允许用户自行记录结果。
  const autoJudge = !isCodeZone && Boolean(variant.expected_answer);
  let result;
  if (!autoJudge) {
    result = document.createElement("select");
    for (const [value, label] of Object.entries(resultLabels)) {
      const option = element("option", label);
      option.value = value;
      result.append(option);
    }
    result.value = variant.result;
    form.append(field("练习结果", result));
  }

  let code;
  let answer;
  if (isCodeZone) {
    code = textarea(variant.answer_code, 40000, 7, true);
    form.append(field("我的解答代码 · 仅保存，不运行", code));
  } else {
    answer = document.createElement("input");
    answer.name = "answer";
    answer.value = variant.answer || "";
    answer.maxLength = 500;
    answer.required = autoJudge;
    answer.placeholder = "多个数值用英文逗号分隔，例如：1, 1, 4";
    form.append(field("我的答案", answer));
    if (!autoJudge) {
      form.append(element("p", "这道旧练习题没有参考答案，请自行记录结果。", "muted"));
    }
  }
  const judgment = element("p");
  const reference = element("p", "", "multiline");

  function updateJudgment(updated) {
    const hasSavedResult = autoJudge && Boolean(updated.result_updated_at);
    judgment.hidden = !hasSavedResult;
    reference.hidden = !hasSavedResult;
    judgment.textContent = updated.result === "solved"
      ? "系统判定：正确"
      : updated.result === "failed" ? "系统判定：错误" : "系统判定：待作答";
    reference.textContent = hasSavedResult ? `参考答案：${updated.expected_answer}` : "";
  }
  updateJudgment(variant);

  const save = element("button", "保存练习结果", "primary");
  save.type = "submit";
  form.append(save);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      const updated = await api(`/api/variants/${variant.id}/result`, {
        method: "PUT",
        body: JSON.stringify(isCodeZone
          ? { result: result.value, answer_code: code.value }
          : { result: result ? result.value : variant.result, answer: answer.value }),
      });
      variant = updated;
      summary.textContent =
        `变体 #${updated.id} · ${resultLabels[updated.result]}`;
      savedAt.textContent =
        `结果保存于：${timestamp(updated.result_updated_at)}`;
      updateJudgment(updated);
      message("练习结果已保存。这次练习不会改变复习日期。");
    });
  });

  box.append(
    summary,
    renderVariantDescription(variant.description),
    savedAt,
    form,
    judgment,
    reference
  );
  return box;
}

function renderMistakeText(item) {
  const wrap = element("div", "", "mistake-text");

  function readOnly() {
    const editBtn = element("button", "编辑这条错因");
    editBtn.type = "button";
    editBtn.addEventListener("click", editForm);
    const text = item.description
      ? element("p", item.description, "multiline")
      : element("p", "还没有错因描述，点击下面「诊断错因并出两道新题」让 AI 帮你反推。", "muted");
    wrap.replaceChildren(text, editBtn);
  }

  function editForm() {
    const input = textarea(item.description, 2000, 4);
    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("哪里容易错，为什么会错？（可选）", input), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/mistakes/${item.id}`, {
          method: "PUT",
          body: JSON.stringify({
            description: input.value,
            version: item.version,
          }),
        });
        message("易错点描述已更新。");
        await openMistake(updated.id);
      });
    });

    wrap.replaceChildren(form);
  }

  readOnly();
  return wrap;
}

function renderProblemEditor(item) {
  const wrap = element("div", "", "problem-editor");

  function readOnly() {
    const editBtn = element("button", "编辑题目、代码和思路");
    editBtn.type = "button";
    editBtn.addEventListener("click", editForm);
    wrap.replaceChildren(
      element("h4", "当时的思路"),
      element("p", item.thinking, "multiline"),
      element("h4", codeZones.has(item.zone) ? "当时的代码" : "当时的解题过程"),
      element("pre", item.code, "code"),
      editBtn
    );
  }

  function editForm() {
    const title = document.createElement("input");
    title.value = item.title;
    title.maxLength = 200;
    title.required = true;

    const zoneSelect = document.createElement("select");
    zoneSelect.required = true;
    for (const zone of zones) {
      const option = element("option", zone);
      option.value = zone;
      zoneSelect.append(option);
    }
    zoneSelect.value = item.zone;

    const language = document.createElement("input");
    language.name = "language";
    language.value = item.language;
    language.maxLength = 40;
    const languageField = element("label", "编程语言", "");
    languageField.dataset.role = "language-field";
    languageField.append(language);

    const code = textarea(item.code, 40000, 10, true);
    const codeLabelText = element("span", "当时的代码");
    codeLabelText.dataset.role = "code-label";
    const codeField = element("label");
    codeField.append(codeLabelText, code);

    const thinking = textarea(item.thinking, 8000, 4);

    const save = element("button", "保存题目信息", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    zoneSelect.addEventListener("change", () => applyZoneFieldMode(form, zoneSelect.value));
    form.append(
      field("标题", title),
      field("分区", zoneSelect),
      languageField,
      codeField,
      field("思路", thinking),
      save,
      cancel
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/problems/${item.problem_id}`, {
          method: "PUT",
          body: JSON.stringify({
            title: title.value,
            zone: zoneSelect.value,
            language: language.value,
            code: code.value,
            thinking: thinking.value,
          }),
        });
        message("题目信息已更新。");
        await openMistake(item.id);
      });
    });

    wrap.replaceChildren(form);
    applyZoneFieldMode(form, zoneSelect.value);
  }

  readOnly();
  return wrap;
}

function clearDetail(
  title = "从一条易错点开始",
  description = "选中列表中的一条记录，先回忆如何避免这个错误，再对照笔记，给这次掌握程度打个分。"
) {
  const empty = element("div", "", "empty-state");
  const symbol = element("span", "≡", "empty-symbol");
  symbol.setAttribute("aria-hidden", "true");
  empty.append(
    symbol,
    element("h3", title),
    element("p", description, "muted")
  );
  $("#detail").replaceChildren(empty);
}

function renderDetail(item) {
  const root = $("#detail");
  root.replaceChildren();
  const isCodeZone = codeZones.has(item.zone);

  const heading = element("div", "", "detail-heading");
  heading.append(
    element("h2", item.title),
    element("p", item.language ? `${item.zone} · ${item.language}` : item.zone, "language-badge")
  );
  let mistakeTextNode = renderMistakeText(item);
  root.append(
    heading,
    element("h3", "这次需要记住的错因", "section-label"),
    mistakeTextNode,
    element(
      "p",
      `下次复习：${item.due_date} · 连续成功：${item.repetitions} 次`,
      "muted review-meta"
    ),
    element(
      "p",
      `上次复习：${timestamp(item.last_reviewed_at)}`,
      "muted review-meta"
    )
  );

  const original = document.createElement("details");
  original.className = "original-record";
  original.append(
    element("summary", "查看当时的思路和代码"),
    renderProblemEditor(item)
  );
  root.append(original);

  const deleteMistakeBtn = element("button", "删除这条易错点", "danger");
  deleteMistakeBtn.type = "button";
  deleteMistakeBtn.addEventListener("click", () => run(async () => {
    if (!confirm("删除这条易错点？它的复习记录和变体题也会一起删除，无法恢复。同一道题的其他易错点会保留。")) {
      return;
    }
    await api(`/api/mistakes/${item.id}`, { method: "DELETE" });
    clearDetail();
    await loadList();
    message("已删除这条易错点。");
  }));

  const deleteProblemBtn = element(
    "button", "删除整道题及全部记录", "danger"
  );
  deleteProblemBtn.type = "button";
  deleteProblemBtn.addEventListener("click", () => run(async () => {
    if (!confirm(
      "确定删除整道题？这道题下的所有易错点、复习记录和变体题都会一起删除，且无法恢复。"
    )) {
      return;
    }
    await api(`/api/problems/${item.problem_id}`, { method: "DELETE" });
    clearDetail();
    await loadList();
    message("已删除整道题。");
  }));

  const dangerZone = element("div", "", "danger-zone");
  dangerZone.append(
    element("p", "删除后无法恢复，请确认这些记录不再需要。", "danger-hint"),
    deleteMistakeBtn,
    deleteProblemBtn
  );

  const reviewSection = element("section", "", "review-section");
  const reviewButtons = element("div", "", "actions review-actions");
  const due = item.due_date <= item.today;
  reviewSection.append(
    element("h3", "这次，你掌握得怎么样？"),
    element(
      "p",
      due
        ? "先回忆，再对照。按真实感受评分，下次复习会据此安排。"
        : "还没到复习日期。先回看笔记或练一道变体题，到期后就能评分。",
      "muted"
    )
  );

  for (const [quality, label] of grades) {
    const button = element("button", label);
    button.type = "button";
    button.dataset.quality = String(quality);
    button.dataset.blocked = due ? "0" : "1";
    button.disabled = !due;
    button.addEventListener("click", () => {
      const anchor = sealAnchorPoint(button);
      run(async () => {
        const state = await api(`/api/mistakes/${item.id}/review`, {
          method: "POST",
          body: JSON.stringify({ quality, version: item.version }),
        });
        stampSeal({ 0: "再练", 3: "过关", 4: "记住", 5: "掌握" }[quality], { anchor });
        await loadList();
        message(`评分已保存。${state.due_date} 再来复习这条易错点。`);
      });
    });
    reviewButtons.append(button);
  }
  reviewSection.append(reviewButtons);
  root.append(reviewSection);

  if (item.reviews.length) {
    const history = document.createElement("details");
    history.className = "review-history";
    history.append(element("summary", `复习历史（${item.reviews.length} 次）`));
    const list = document.createElement("ul");
    for (const review of item.reviews) {
      list.append(element(
        "li",
        `${timestamp(review.reviewed_at)} · 评分 ${review.quality}/5` +
        ` · 下次 ${review.next_due_date}`
      ));
    }
    history.append(list);
    root.append(history);
  }

  const aiSection = element("section", "", "ai-section");
  aiSection.append(
    element("h3", "诊断错因，再练两道"),
    element(
      "p",
      "生成时会把这道题的代码（或解题过程）、思路发送给 OpenAI；" +
      "错因没填时 AI 会自己反推，已经填了则作为参考。" +
      `每天最多 ${user.ai_daily_limit} 次尝试，失败也计入次数。`,
      "muted"
    ),
    element(
      "p",
      isCodeZone
        ? "每次生成两道题。请先核对题意，参考样例自行解答并记录结果；代码仅保存，不运行或自动判题。"
        : "每次生成两道题。提交最终答案后，系统会比对参考答案并显示判定；如有疑问，请自行核对参考答案。",
      "muted"
    )
  );

  const variants = element("div");
  const empty = element("p", "还没有变体题。练两道新题，检查自己是否真的理解了。", "muted");
  if (!item.variants.length) variants.append(empty);
  for (const variant of item.variants) {
    variants.append(renderVariant(variant, isCodeZone));
  }

  const generate = element(
    "button",
    user.ai_enabled ? "诊断错因并出两道新题" : "AI 尚未配置，暂时无法出题",
    "primary"
  );
  generate.type = "button";
  generate.dataset.blocked = user.ai_enabled ? "0" : "1";
  generate.disabled = !user.ai_enabled;

  generate.addEventListener("click", () => run(async () => {
    message("AI 正在诊断错因、准备两道新题，可能需要一两分钟。请保持页面打开。");
    const generated = await api(`/api/mistakes/${item.id}/variants`, {
      method: "POST",
    });
    empty.remove();
    for (const variant of generated.variants) {
      variants.prepend(renderVariant(variant, isCodeZone));
    }
    if (generated.mistake_description !== item.description) {
      item.description = generated.mistake_description;
      const updated = renderMistakeText(item);
      mistakeTextNode.replaceWith(updated);
      mistakeTextNode = updated;
    }
    message("两道新题已生成并保存。");
  }));

  aiSection.append(generate, variants);
  root.append(aiSection, dangerZone);
}

function addMistakeInput() {
  const container = $("#mistake-inputs");
  if (container.children.length >= 10) {
    message("每道题最多添加 10 条易错点", true);
    return;
  }

  const row = element("div", "", "mistake-row");
  const input = textarea("", 2000, 3);
  input.name = "mistake";
  input.placeholder = "不确定的话可以留空，AI 会从代码和思路里帮你反推错因。";

  const remove = element("button", "移除这条输入");
  remove.type = "button";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
  });

  row.append(field("哪里容易错，为什么会错？（可选）", input), remove);
  container.append(row);
}

$("#login-form").addEventListener("submit", (event) => {
  event.preventDefault();
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
  const form = event.currentTarget;
  run(async () => {
    await api("/api/auth/register", {
      method: "POST",
      body: JSON.stringify(formObject(form)),
    });
    form.reset();
    await enterApp();
    message("账号已创建。去「新增记录」留下你的第一条错因吧。");
  });
});

function startTrial() {
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

$("#forgot-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    await api("/api/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email: form.email.value }),
    });
    form.reset();
    showAuthPanels(["login-form"]);
    message("如果这个邮箱注册过账号，重置邮件已经发出，请查收（包括垃圾邮件文件夹）。");
  });
});

$("#reset-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
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
      if (error.status === 400) {
        resetToken = null;
        history.replaceState(null, "", `${location.pathname}#/auth`);
        showAuthPanels(["forgot-form"]);
        message("重置链接无效或已过期，请重新申请。", true);
        return;
      }
      throw error;
    }
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
  run(async () => {
    const updated = await api("/api/me/email", {
      method: "PUT",
      body: JSON.stringify({ email: form.email.value }),
    });
    user.email = updated.email;
    $("#email-prompt").hidden = true;
    form.reset();
    message("邮箱绑定成功。");
  });
});

$("#logout").addEventListener("click", () => run(async () => {
  await api("/api/auth/logout", { method: "POST" });
  history.replaceState(null, "", `${location.pathname}#/welcome`);
  signedOut();
  message("已退出登录。");
}));

document.querySelectorAll("#app button[data-view]").forEach((button) => {
  button.addEventListener("click", () => run(async () => {
    message();
    await showView(button.dataset.view);
  }));
});

$("#zone-filter").addEventListener("change", () => run(loadList));

$("#home-refresh").addEventListener("click", () => run(async () => {
  message();
  await loadHome();
}));

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

$("#problem-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const anchor = sealAnchorPoint(event.submitter || form.querySelector('[type="submit"]:not([hidden])'));
  const cameFromPhoto = Boolean(photoRecognition);

  run(async () => {
    const data = new FormData(form);
    const created = await api("/api/problems", {
      method: "POST",
      body: JSON.stringify({
        title: data.get("title"),
        zone: data.get("zone"),
        language: data.get("language"),
        code: data.get("code"),
        thinking: data.get("thinking"),
        mistakes: data.getAll("mistake"),
      }),
    });
    stampSeal("已录", { anchor });

    form.reset();
    applyZoneFieldMode(form, form.zone.value);
    $("#mistake-inputs").replaceChildren();
    addMistakeInput();
    resetPhotoForm();

    let noticeText = "记录已保存，新的易错点已加入今日复习。";
    if (cameFromPhoto && created.mistake_ids[0]) {
      try {
        await api(`/api/mistakes/${created.mistake_ids[0]}/variants`, { method: "POST" });
        noticeText = "记录已保存，已根据识别结果自动生成练习题。";
      } catch (error) {
        noticeText = `记录已保存，但自动生成练习题失败：${error.message}`;
      }
    }

    await showView("today");
    await openMistake(created.mistake_ids[0]);
    message(noticeText);
  });
});

async function showForumList() {
  forumPost = null;
  $("#forum-search").value = forumSearchQuery;
  $("#forum-compose").hidden = true;
  $("#forum-detail").hidden = true;
  $("#forum-list").hidden = false;
  $("#forum-new-post-btn").hidden = user.is_trial;
  await loadForumPosts();
}

async function loadForumPosts() {
  const currentUser = user;
  const generation = ++forumListGeneration;
  const query = forumSearchQuery;
  const status = $("#forum-list-status");
  const list = $("#forum-posts");
  const isCurrent = () => generation === forumListGeneration && user === currentUser
    && user && view === "forum" && !$("#forum-list").hidden;
  $("#forum-list-title").textContent = query ? "搜索结果" : "全部帖子";
  status.textContent = "正在加载帖子列表…";
  list.replaceChildren();
  let posts;
  try {
    ({ posts } = await api(query ? `/api/posts?q=${encodeURIComponent(query)}` : "/api/posts"));
  } catch (error) {
    if (isCurrent()) status.textContent = error.message || "帖子列表加载失败，请稍后重试。";
    return;
  }
  if (!isCurrent()) return;
  status.textContent = posts.length ? "" : (query
    ? "没有找到相关帖子，请试试其他关键词。" : "还没有帖子，来发第一条吧。");
  for (const post of posts) {
    const row = element("button", "", "record-button");
    row.type = "button";
    const authorLine = element("div", "", "author-line muted");
    authorLine.append(
      avatarElement(post.user_id, post.username, post.avatar_version, {
        small: true, hasAvatar: post.has_avatar,
      }),
      element(
        "small",
        `${post.username} · ${timestamp(post.created_at)} · ${post.comment_count} 条评论`
      )
    );
    row.append(element("strong", post.title), authorLine);
    row.addEventListener("click", () => run(() => openForumPost(post.id)));
    list.append(row);
  }
}

function showForumCompose() {
  $("#forum-list").hidden = true;
  $("#forum-detail").hidden = true;
  $("#forum-compose").hidden = false;
}

async function openForumPost(postId) {
  const post = await api(`/api/posts/${postId}`);
  forumPost = post;
  $("#forum-list").hidden = true;
  $("#forum-compose").hidden = true;
  $("#forum-detail").hidden = false;
  renderForumPost(post);
  renderForumComments(post.comments);
}

function renderForumPost(post) {
  const root = $("#forum-post");

  function readOnly() {
    root.replaceChildren();
    const authorLine = element("p", "", "muted author-line");
    authorLine.append(
      avatarElement(post.user_id, post.username, post.avatar_version, {
        small: true, hasAvatar: post.has_avatar,
      }),
      element(
        "span",
        `${post.username} · ${timestamp(post.created_at)}` +
          (post.updated_at ? `（编辑于 ${timestamp(post.updated_at)}）` : "")
      )
    );
    root.append(
      element("h2", post.title),
      authorLine,
      element("p", post.body, "multiline")
    );
    if (user.id === post.user_id) {
      const editBtn = element("button", "编辑");
      editBtn.type = "button";
      editBtn.addEventListener("click", editForm);
      const deleteBtn = element("button", "删除这条帖子", "danger");
      deleteBtn.type = "button";
      deleteBtn.addEventListener("click", () => run(async () => {
        if (!confirm("删除这条帖子？帖子下的评论也会一起不可见，无法恢复。")) return;
        await api(`/api/posts/${post.id}`, { method: "DELETE" });
        message("已删除这条帖子。");
        await showForumList();
      }));
      const actions = element("div", "", "actions");
      actions.append(editBtn, deleteBtn);
      root.append(actions);
    } else if (!user.is_trial) {
      const actions = element("div", "", "actions");
      const reportBtn = element("button", "举报这条帖子");
      reportBtn.type = "button";
      reportBtn.addEventListener("click", () => actions.replaceWith(reportForm()));
      const reportAvatarBtn = element("button", "举报头像");
      reportAvatarBtn.type = "button";
      reportAvatarBtn.addEventListener("click", () =>
        actions.replaceWith(avatarReportForm(post.user_id, readOnly))
      );
      actions.append(reportBtn, reportAvatarBtn);
      root.append(actions);
    }
  }

  function editForm() {
    const title = document.createElement("input");
    title.value = post.title;
    title.maxLength = 200;
    title.required = true;

    const body = textarea(post.body, 8000, 8);
    body.required = true;

    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("标题", title), field("正文", body), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/posts/${post.id}`, {
          method: "PUT",
          body: JSON.stringify({ title: title.value, body: body.value }),
        });
        post.title = updated.title;
        post.body = updated.body;
        post.updated_at = updated.updated_at;
        message("帖子已更新。");
        readOnly();
      });
    });

    root.replaceChildren(form);
  }

  // 只替换"举报"按钮所在的操作区，不动上面已经展示的标题和正文。
  function reportForm() {
    const reason = textarea("", 500, 3);
    const submit = element("button", "提交举报", "primary");
    submit.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("举报原因（可选）", reason), submit, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/posts/${post.id}/report`, {
          method: "POST",
          body: JSON.stringify({ reason: reason.value }),
        });
        message("已提交举报，管理员会尽快处理。");
        readOnly();
      });
    });

    return form;
  }

  readOnly();
}

function renderForumComment(comment) {
  const wrap = element("div", "", "forum-comment");

  function readOnly() {
    wrap.replaceChildren();
    const meta = element("p", "", "muted forum-comment-meta author-line");
    meta.append(
      avatarElement(comment.user_id, comment.username, comment.avatar_version, {
        small: true, hasAvatar: comment.has_avatar,
      }),
      element(
        "span",
        `${comment.username} · ${timestamp(comment.created_at)}` +
          (comment.updated_at ? `（编辑于 ${timestamp(comment.updated_at)}）` : "")
      )
    );
    wrap.append(meta, element("p", comment.body, "multiline"));
    if (user.id === comment.user_id) {
      const editBtn = element("button", "编辑");
      editBtn.type = "button";
      editBtn.addEventListener("click", editForm);
      const deleteBtn = element("button", "删除", "danger");
      deleteBtn.type = "button";
      deleteBtn.addEventListener("click", () => run(async () => {
        if (!confirm("删除这条评论？无法恢复。")) return;
        await api(`/api/comments/${comment.id}`, { method: "DELETE" });
        wrap.remove();
        message("已删除这条评论。");
      }));
      const actions = element("div", "", "actions");
      actions.append(editBtn, deleteBtn);
      wrap.append(actions);
    } else if (!user.is_trial) {
      const actions = element("div", "", "actions");
      const reportBtn = element("button", "举报");
      reportBtn.type = "button";
      reportBtn.addEventListener("click", () => actions.replaceWith(reportForm()));
      const reportAvatarBtn = element("button", "举报头像");
      reportAvatarBtn.type = "button";
      reportAvatarBtn.addEventListener("click", () =>
        actions.replaceWith(avatarReportForm(comment.user_id, readOnly))
      );
      actions.append(reportBtn, reportAvatarBtn);
      wrap.append(actions);
    }
  }

  function editForm() {
    const body = textarea(comment.body, 2000, 3);
    body.required = true;
    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("评论内容", body), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/comments/${comment.id}`, {
          method: "PUT",
          body: JSON.stringify({ body: body.value }),
        });
        comment.body = updated.body;
        comment.updated_at = updated.updated_at;
        message("评论已更新。");
        readOnly();
      });
    });

    wrap.replaceChildren(form);
  }

  function reportForm() {
    const reason = textarea("", 500, 3);
    const submit = element("button", "提交举报", "primary");
    submit.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("举报原因（可选）", reason), submit, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/comments/${comment.id}/report`, {
          method: "POST",
          body: JSON.stringify({ reason: reason.value }),
        });
        message("已提交举报，管理员会尽快处理。");
        readOnly();
      });
    });

    return form;
  }

  readOnly();
  return wrap;
}

function renderForumComments(comments) {
  const list = $("#forum-comments");
  list.replaceChildren();
  if (!comments.length) {
    list.append(element("p", "还没有评论，来发表第一条看法吧。", "muted"));
  }
  for (const comment of comments) {
    list.append(renderForumComment(comment));
  }
  $("#forum-comment-form").hidden = user.is_trial;
}

function resetAdminDashboard() {
  adminDashboardGeneration += 1;
  $("#admin-dashboard").setAttribute("aria-busy", "false");
  $("#admin-dashboard-content").hidden = true;
  $("#admin-dashboard-cards").replaceChildren();
  $("#admin-dashboard-zones").replaceChildren();
  $("#admin-dashboard-zones-empty").hidden = true;
  $("#admin-dashboard-status").textContent = "";
  $("#admin-dashboard-status").classList.remove("error");
  $("#admin-dashboard-updated").textContent = "";
  $("#admin-dashboard-refresh").textContent = "刷新数据";
}

function renderAdminDashboard(data) {
  const count = (value) => {
    if (!Number.isSafeInteger(value) || value < 0) throw new Error("统计数据不完整，请重试");
    return value.toLocaleString("zh-CN");
  };
  const money = (value) => {
    count(value);
    return (value / 100).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };
  const definitions = [
    ["用户总数", data.users.total, "人", [
      ["体验账号", `${count(data.users.trial)} 人`],
      ["正式账号", `${count(data.users.registered)} 人`],
      ["最近 7 天新增", `${count(data.users.new_7_days)} 人`],
      ["最近 30 天新增", `${count(data.users.new_30_days)} 人`],
    ], "正式账号指非体验账号，与是否付费订阅无关。"],
    ["最近 7 天活跃用户", data.activity.active_users_7_days, "人", [],
      "新增过题目或提交过复习评分的用户；同一人只计算一次。"],
    ["今天 AI 调用", data.ai_usage.today, "次", [
      ["最近 7 天调用", `${count(data.ai_usage.last_7_days)} 次`],
    ]],
    ["题目总数", data.content.problems, "道", [
      ["易错点总数", `${count(data.content.mistakes)} 个`],
      ["讨论区帖子", `${count(data.content.posts)} 篇`],
    ], "帖子数量不含已删除的帖子。"],
    ["待处理举报", data.pending_reports, "条", [], "包含讨论区内容举报与头像举报。"],
    ["当前有效订阅", data.subscriptions.active, "份", [
      ["已支付订单", `${count(data.subscriptions.paid_orders)} 笔`],
      ["已支付订单总金额", `${money(data.subscriptions.paid_amount_cents)} 元`],
    ], "订阅尚未到期；订单与金额为全站累计，仅统计当前状态为已支付的订单。"],
  ];
  const cards = definitions.map(([title, value, unit, facts, note]) => {
    const card = element("article", "", "panel admin-stat-card");
    const number = element("p", count(value), "admin-stat-value");
    number.append(element("small", unit));
    card.append(element("h4", title), number);
    if (facts.length) {
      const list = element("dl", "", "admin-stat-facts");
      for (const [label, text] of facts) list.append(element("dt", label), element("dd", text));
      card.append(list);
    }
    if (note) card.append(element("p", note, "muted admin-dashboard-note"));
    return card;
  });
  const zoneRows = data.zones.map((zone) => {
    const row = element("li");
    row.append(element("span", zone.zone), element("strong", `${count(zone.mistake_count)} 个`));
    return row;
  });
  const updated = new Date(data.generated_at);
  if (Number.isNaN(updated.getTime())) throw new Error("统计时间无效，请重试");
  $("#admin-dashboard-cards").replaceChildren(...cards);
  $("#admin-dashboard-zones").replaceChildren(...zoneRows);
  $("#admin-dashboard-zones-empty").hidden = zoneRows.length > 0;
  $("#admin-dashboard-updated").textContent = `数据更新于 ${updated.toLocaleString("zh-CN", { timeZone: "UTC", hour12: false })} UTC`;
  $("#admin-dashboard-content").hidden = false;
}

async function loadAdminDashboard() {
  const generation = ++adminDashboardGeneration;
  const currentUser = user;
  if (!currentUser?.is_admin) return false;
  const isCurrent = () => generation === adminDashboardGeneration && user === currentUser && view === "admin";
  const status = $("#admin-dashboard-status");
  const refresh = $("#admin-dashboard-refresh");
  $("#admin-dashboard").setAttribute("aria-busy", "true");
  $("#admin-dashboard-content").hidden = true;
  $("#admin-dashboard-updated").textContent = "";
  status.classList.remove("error");
  status.textContent = "正在加载全站数据…";
  refresh.textContent = "加载中…";
  try {
    const data = await api("/api/admin/dashboard");
    if (!isCurrent()) return false;
    renderAdminDashboard(data);
    status.textContent = "";
    return true;
  } catch (error) {
    if (!isCurrent()) return false;
    status.textContent = error.status === 403
      ? "无权查看数据看板，请使用管理员账号登录。"
      : "数据看板加载失败，请检查网络后点击「重试加载」。";
    status.classList.add("error");
    return false;
  } finally {
    if (isCurrent()) {
      $("#admin-dashboard").setAttribute("aria-busy", "false");
      refresh.textContent = status.classList.contains("error") ? "重试加载" : "刷新数据";
    }
  }
}

async function loadAdminPage() {
  // 两个区块独立加载，看板失败不会阻断原有的举报处理。
  const [dashboard, reports] = await Promise.allSettled([loadAdminDashboard(), loadAdminReports()]);
  if (reports.status === "rejected") throw reports.reason;
  return dashboard.status === "fulfilled" && dashboard.value;
}

function removeReportCard(card) {
  card.remove();
  if (!$("#admin-reports").children.length) {
    $("#admin-status").textContent = "暂无待处理的举报。";
  }
}

async function loadAdminReports() {
  const currentUser = user;
  const status = $("#admin-status");
  const list = $("#admin-reports");
  status.textContent = "正在加载举报队列…";
  list.replaceChildren();
  const { reports } = await api("/api/admin/reports");
  if (user !== currentUser || !user || view !== "admin") return;
  status.textContent = reports.length ? "" : "暂无待处理的举报。";
  for (const report of reports) {
    list.append(renderAdminReport(report));
  }
}

function renderAdminReport(report) {
  if (report.type === "avatar") return renderAdminAvatarReport(report);

  const isPost = report.type === "post";
  const kind = isPost ? "帖子" : "评论";
  const authorId = isPost ? report.post_author_id : report.comment_author_id;
  const authorName = isPost
    ? report.post_author_username
    : report.comment_author_username;
  const deletedAt = isPost ? report.post_deleted_at : report.comment_deleted_at;
  const contentPreview = isPost
    ? `${report.post_title}\n${report.post_body}`
    : report.comment_body;

  const card = element("article", "", "panel admin-report");
  card.append(
    element("h3", `举报的${kind} · 作者 ${authorName}`),
    element(
      "p",
      `举报人：${report.reporter_username} · ${timestamp(report.created_at)}`,
      "muted"
    )
  );
  if (report.reason) {
    card.append(element("p", `举报原因：${report.reason}`));
  }
  card.append(element("pre", contentPreview, "prose"));
  if (deletedAt) {
    card.append(element("p", "该内容已被作者自行删除，只能忽略这条举报。", "muted"));
  }

  const actions = element("div", "", "actions");
  const deleteBtn = element("button", `删除这条${kind}`, "danger");
  deleteBtn.type = "button";
  deleteBtn.disabled = Boolean(deletedAt);
  deleteBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`删除这条${kind}？无法恢复，关联的举报会一并标记为已处理。`)) return;
    const path = isPost
      ? `/api/admin/posts/${report.post_id}`
      : `/api/admin/comments/${report.comment_id}`;
    await api(path, { method: "DELETE" });
    removeReportCard(card);
    message("已删除，举报已处理。");
  }));

  const dismissBtn = element("button", "忽略举报");
  dismissBtn.type = "button";
  dismissBtn.addEventListener("click", () => run(async () => {
    await api(`/api/admin/reports/${report.id}/resolve`, { method: "POST" });
    removeReportCard(card);
    message("已忽略这条举报。");
  }));

  const banBtn = element("button", `封禁 ${authorName}`, "danger");
  banBtn.type = "button";
  banBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`封禁账号「${authorName}」？该账号将无法再登录。`)) return;
    await api(`/api/admin/users/${authorId}/ban`, { method: "POST" });
    message(`已封禁 ${authorName}。`);
  }));

  actions.append(deleteBtn, dismissBtn, banBtn);
  card.append(actions);
  return card;
}

function renderAdminAvatarReport(report) {
  const authorId = report.avatar_owner_id;
  const authorName = report.avatar_owner_username;

  const card = element("article", "", "panel admin-report");
  card.append(
    element("h3", `举报的头像 · 用户 ${authorName}`),
    element(
      "p",
      `举报人：${report.reporter_username} · ${timestamp(report.created_at)}`,
      "muted"
    )
  );
  if (report.reason) {
    card.append(element("p", `举报原因：${report.reason}`));
  }
  const preview = avatarElement(authorId, authorName, report.avatar_owner_avatar_version, {
    hasAvatar: report.avatar_owner_has_avatar,
  });
  preview.style.width = "96px";
  preview.style.height = "96px";
  preview.style.fontSize = "36px";
  card.append(preview);
  if (!report.avatar_owner_has_avatar) {
    card.append(element("p", "该用户目前没有自定义头像（可能已被清除或本人移除），只能忽略这条举报。", "muted"));
  }

  const actions = element("div", "", "actions");
  const clearBtn = element("button", "清除该头像", "danger");
  clearBtn.type = "button";
  clearBtn.disabled = !report.avatar_owner_has_avatar;
  clearBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`清除「${authorName}」的头像？无法恢复，关联的举报会一并标记为已处理。`)) return;
    await api(`/api/admin/users/${authorId}/avatar`, { method: "DELETE" });
    removeReportCard(card);
    message("已清除头像，举报已处理。");
  }));

  const dismissBtn = element("button", "忽略举报");
  dismissBtn.type = "button";
  dismissBtn.addEventListener("click", () => run(async () => {
    await api(`/api/admin/avatar-reports/${report.id}/resolve`, { method: "POST" });
    removeReportCard(card);
    message("已忽略这条举报。");
  }));

  const banBtn = element("button", `封禁 ${authorName}`, "danger");
  banBtn.type = "button";
  banBtn.addEventListener("click", () => run(async () => {
    if (!confirm(`封禁账号「${authorName}」？该账号将无法再登录。`)) return;
    await api(`/api/admin/users/${authorId}/ban`, { method: "POST" });
    message(`已封禁 ${authorName}。`);
  }));

  actions.append(clearBtn, dismissBtn, banBtn);
  card.append(actions);
  return card;
}

$("#forum-search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const input = $("#forum-search");
  const query = input.value.trim();
  input.value = query;
  if (Array.from(query).length > 200) {
    $("#forum-list-status").textContent = "搜索关键词不能超过 200 个字符。";
    return;
  }
  forumSearchQuery = query;
  message();
  loadForumPosts();
});

$("#forum-new-post-btn").addEventListener("click", () => {
  message();
  showForumCompose();
});

$("#forum-compose-cancel").addEventListener("click", () => run(async () => {
  message();
  await showForumList();
}));

$("#forum-compose-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    const data = new FormData(form);
    const created = await api("/api/posts", {
      method: "POST",
      body: JSON.stringify({
        title: data.get("title"),
        body: data.get("body"),
      }),
    });
    form.reset();
    await openForumPost(created.id);
    message("帖子已发布。");
  });
});

$("#forum-back").addEventListener("click", () => run(async () => {
  message();
  await showForumList();
}));

$("#forum-comment-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  run(async () => {
    const data = new FormData(form);
    const created = await api(`/api/posts/${forumPost.id}/comments`, {
      method: "POST",
      body: JSON.stringify({ body: data.get("body") }),
    });
    form.reset();
    forumPost.comments.push(created);
    renderForumComments(forumPost.comments);
    message("评论已发表。");
  });
});

$("#timezone").value =
  Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";

addMistakeInput();
initReviewSpotlight();

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
