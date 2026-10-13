// static/app/forum-map.js — 论坛：楼层地图/导航。
// 由 static/app/forum.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

// One animation frame reads every floor before writing sticky offset, folds, and map indicators.

function scheduleForumMap() {
  if (forumMapFrame !== null) return;
  forumMapFrame = window.requestAnimationFrame(() => {
    forumMapFrame = null;
    updateForumMap();
  });
}

function updateForumMap() {
  if (!forumDetailVisible()) {
    $("#forum-minimap").hidden = true;
    $("#forum-floor-nav").hidden = true;
    return;
  }
  const visible = window.Thread.arrange(forumPost.comments, forumFilters());
  const nodes = visible.map((item) => $(`#forum-comment-${item.id}`));
  const rects = nodes.map((node) => node.getBoundingClientRect());
  const measurements = nodes.map((node) => node.readForumMeasure?.()).filter(Boolean);
  const topbar = $(".header");
  const topRect = topbar?.getBoundingClientRect();
  const sticky = Math.max(12, (topRect?.bottom || 0) + 12);
  const mapItems = visible.map((item) => ({ ...item, isAccepted: item.id === forumPost.accepted_comment_id }));
  const model = window.Thread.mapModel(mapItems, rects, window.innerHeight);
  // All geometry above is read before any style or DOM write below.
  $("#forum-page").style.setProperty("--thread-sticky-top", `${sticky}px`);
  let geometryChanged = false;
  for (const { body, more, height, limit } of measurements) {
    const collapsible = Number.isFinite(height) && Number.isFinite(limit) && height > limit;
    if (body.classList.contains("is-collapsible") !== collapsible || more.hidden === collapsible) geometryChanged = true;
    body.classList.toggle("is-collapsible", collapsible);
    more.hidden = !collapsible;
  }
  if (geometryChanged) scheduleForumMap();
  forumMapModel = model;
  $("#forum-minimap").hidden = model.hidden;
  $("#forum-floor-nav").hidden = model.hidden;
  if (model.hidden) return;
  $("#forum-map-floor").textContent = `#${String(model.currentFloor).padStart(2, "0")}`;
  $("#forum-map-total").textContent = `共 ${model.total} 条`;
  $("#forum-floor-current").textContent = `#${String(model.currentFloor).padStart(2, "0")}`;
  $("#forum-floor-total").textContent = String(model.total);
  const ticks = $("#forum-map-ticks");
  const signature = JSON.stringify(model.ticks);
  if (ticks.threadSignature !== signature) {
    ticks.threadSignature = signature;
    ticks.replaceChildren();
    for (const tick of model.ticks) {
      const mark = element("span", "", "thread-map-tick");
      mark.classList.toggle("is-op", tick.isOp);
      mark.classList.toggle("has-code", tick.hasCode);
      mark.classList.toggle("is-accepted", tick.isAccepted);
      mark.style.setProperty("--thread-tick-position", `${tick.position * 100}%`);
      ticks.append(mark);
    }
  }
  [...ticks.children].forEach((tick, index) => {
    if (index === model.currentIndex) tick.setAttribute("aria-current", "true");
    else tick.removeAttribute("aria-current");
  });
  $("#forum-map-thumb").style.setProperty("--thread-thumb-top", `${model.thumb.top * 100}%`);
  $("#forum-map-thumb").style.setProperty("--thread-thumb-height", `${model.thumb.height * 100}%`);
}


function forumMoveFloor(direction) {
  if (!forumPost) return;
  const visible = window.Thread.arrange(forumPost.comments, forumFilters());
  if (!visible.length) return;
  const focused = document.activeElement?.closest(".forum-comment");
  const id = focused ? Number(focused.id.replace("forum-comment-", "")) : forumCurrentComment;
  const current = visible.findIndex((comment) => comment.id === id);
  const index = current < 0 ? (direction > 0 ? 0 : visible.length - 1) : Math.max(0, Math.min(visible.length - 1, current + direction));
  scrollToForumComment(visible[index].id);
}


function forumPointerJump(event) {
  if (!forumMapModel || forumMapModel.hidden || !forumDetailVisible()) return;
  const box = $("#forum-map-track").getBoundingClientRect();
  const position = Math.max(0, Math.min(1, (event.clientY - box.top) / Math.max(1, box.height)));
  const index = Math.round(position * (forumMapModel.ticks.length - 1));
  const tick = forumMapModel.ticks[index];
  if (forumMapDrag?.lastId === tick.id) return;
  if (forumMapDrag) forumMapDrag.lastId = tick.id;
  scrollToForumComment(tick.id);
}


$("#forum-map-top").addEventListener("click", () => {
  $("#forum-post").scrollIntoView({ behavior: sealMotion.matches ? "auto" : "smooth", block: "start" });
});
$("#forum-map-latest").addEventListener("click", () => {
  const visible = window.Thread.arrange(forumPost?.comments || [], forumFilters());
  const latest = visible.reduce((found, item) => !found || item.floor > found.floor ? item : found, null);
  if (latest) scrollToForumComment(latest.id);
});
$("#forum-floor-prev").addEventListener("click", () => {
  forumCurrentComment = forumMapModel?.ticks[forumMapModel.currentIndex]?.id ?? forumCurrentComment;
  forumMoveFloor(-1);
});
$("#forum-floor-next").addEventListener("click", () => {
  forumCurrentComment = forumMapModel?.ticks[forumMapModel.currentIndex]?.id ?? forumCurrentComment;
  forumMoveFloor(1);
});
$("#forum-map-track").addEventListener("pointerdown", (event) => {
  if (event.button !== 0 || !forumDetailVisible()) return;
  event.preventDefault();
  forumMapDrag = { pointerId: event.pointerId, lastId: null, clientY: event.clientY };
  event.currentTarget.setPointerCapture(event.pointerId);
  forumPointerJump(event);
});
$("#forum-map-track").addEventListener("pointermove", (event) => {
  if (!forumMapDrag || forumMapDrag.pointerId !== event.pointerId) return;
  forumMapDrag.clientY = event.clientY;
  if (forumMapDragFrame !== null) return;
  forumMapDragFrame = window.requestAnimationFrame(() => {
    forumMapDragFrame = null;
    if (forumMapDrag) forumPointerJump(forumMapDrag);
  });
});

function endForumMapDrag() {
  if (forumMapDrag) forumPointerJump(forumMapDrag);
  forumMapDrag = null;
}

$("#forum-map-track").addEventListener("pointerup", endForumMapDrag);
$("#forum-map-track").addEventListener("pointercancel", () => { forumMapDrag = null; });
window.addEventListener("scroll", scheduleForumMap, { passive: true });
window.addEventListener("resize", scheduleForumMap);
window.visualViewport?.addEventListener("resize", keepForumReplyVisible);
if (typeof ResizeObserver !== "undefined") {
  const observer = new ResizeObserver(scheduleForumMap);
  observer.observe($(".header"));
  observer.observe($("#forum-comments"));
}
document.addEventListener("app:view-changed", () => {
  if (view !== "forum") {
    forumDetailGeneration += 1;
    forumSummaryController?.reset();
    forumMapDrag = null;
    $("#forum-floor-nav").hidden = true;
    $("#forum-minimap").hidden = true;
  }
});
