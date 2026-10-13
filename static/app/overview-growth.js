// static/app/overview-growth.js — 总览·成长：印章特效（stampSeal/queueAchievementStamps）/成就页/事件绑定。
// 由 static/app/overview.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

let achievementsGeneration = 0;

const sealMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const sealStamps = [];
const achievementStampTimers = new Set();

function stampSeal(text, options = {}) {
  const layer = $("#seal-layer");
  const isGroupUpgrade = /^升至 Lv\.[1-8]$/u.test(text);
  if (!layer || document.hidden || (!/^[\u3400-\u9fff]{2,4}$/u.test(text) && !isGroupUpgrade)) return;
  const tone = options.tone === "ink" ? "ink" : (["gamboge", "gold"].includes(options.tone) ? "gamboge" : "cinnabar");
  const stamp = document.createElement("span");
  stamp.className = `seal-stamp seal-stamp--${tone}`;
  stamp.setAttribute("aria-hidden", "true");
  stamp.textContent = text;
  stamp.style.setProperty("--rot", `${-12 + Math.random() * 8}deg`);
  stamp.style.setProperty("--seal-height", isGroupUpgrade ? "64px" : `${78 + (Array.from(text).length - 2) * 30}px`);
  if (isGroupUpgrade) {
    stamp.style.setProperty("--seal-width", "176px");
    stamp.style.setProperty("--seal-writing-mode", "horizontal-tb");
  }
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

// ---- 事件绑定与模块配置 ----

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
