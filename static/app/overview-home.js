// static/app/overview-home.js — 总览·首页：today 首页加载/配额展示/开场动画/复习聚光灯/订单轮询常量。
// 由 static/app/overview.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
// 顺序要求：overview-growth.js 必须先加载（initReviewSpotlight 顶层调用需要 sealMotion 已初始化）。
"use strict";

let homeOpeningPlayed = false;
let finishHomeOpening = null;

const ORDER_POLL_INTERVAL = 3000;
const ORDER_POLL_DURATION = 5 * 60 * 1000;

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
  const card = $(".ov-tile-review");
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

function resetHomeSummary() {
  window.PendingReason?.resetHome();
  window.Overview?.reset();
  $("#home-quota").hidden = true;
  $("#home-quota-text").textContent = "";
  $("#home-quota-progress").value = 0;
  $("#home-quota-progress").max = 1;
}

function renderHomeQuota() {
  const limit = user?.ai_daily_limit;
  const remaining = user?.ai_daily_remaining;
  const available = Number.isInteger(limit) && limit > 0
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
  const [trendEpoch, trendUserId] = [sessionEpoch, user?.id];
  window.Overview?.loadTrend({ api, isCurrent: () => trendEpoch === sessionEpoch && user?.id === trendUserId && view === "home" });
  // Rank.mountCard：趋势区下面的“昨日之星”小卡片，复用 /api/rank/yesterday，不新增接口。
  window.Rank?.mountCard($("#ov-yesterday-card"));
  // 总览统计始终覆盖全部分区；返回时重新读额度，包含 AI 失败后实际扣除的次数。
  // 两份数据独立降级，读取失败不显示旧值或假定的零值。
  const [profile, overview] = await Promise.allSettled([
    refreshUser ? api("/api/me") : Promise.resolve(user),
    api("/api/overview"),
  ]);
  if (!user || user !== currentUser || view !== "home") return;
  if (profile.status === "fulfilled") {
    user = profile.value;
    updateUserInfo();
  }
  if (overview.status === "fulfilled") {
    window.Overview.render(overview.value, {
      username: user.username,
      onOpenRecord: (id) => run(async () => {
        message();
        await showView("today");
        await openMistake(id);
      }),
      onOpenPost: (id) => run(async () => {
        message();
        await showView("forum");
        await openForumPost(id);
      }),
      onFocus: () => window.FocusReview?.start({}),
    });
    // 首次清单 (onboarding.js) 在总览渲染完之后把自己插到最上方。
    document.dispatchEvent(new CustomEvent("app:home-rendered", {
      detail: { overview: overview.value, user: { id: user.id, is_trial: Boolean(user.is_trial) } },
    }));
    window.GoalCard?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
    window.GoalCard?.mount(document.querySelector("#goal-card"));
    window.RecommendCard?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
    window.RecommendCard?.mount(document.querySelector("#recommend-card"));
    window.SignCard?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
    window.SignCard?.mount(document.querySelector("#sign-card"));
    window.BriefingCard?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
    window.BriefingCard?.mount(document.querySelector("#briefing-card"));
    window.Landscape?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
    window.Landscape?.mount(document.querySelector("#landscape-card"));
    window.RevengeCard?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
    window.RevengeCard?.mount(document.querySelector("#revenge-card"));
    // GROWTH F3 下周主攻卡片：空数据/失败时模块自带兜底文案
    $("#ov-focus-card").hidden = false;
    window.Heatmap?.mountFocusCard($("#ov-focus"));
  } else {
    window.Overview.renderError();
  }
  if (profile.status === "fulfilled" && overview.status === "fulfilled") startHomeOpening();
}

initReviewSpotlight();
