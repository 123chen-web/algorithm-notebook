// static/app/review-list.js — 复习：复习队列/列表、表头、每日上限（rvf* 列表相关）。
// 由 static/app/review.js 拆分（C 收尾），纯重构、零行为变化。
// 先于 review-detail.js、review-grade.js 加载：loadList、rvfSetDailyCap 等被后续文件调用；领域间调用保持顶层全局函数名直接调用。
"use strict";

// ---- 领域状态 ----
let rvfPageGeneration = 0;
let rvfListGeneration = 0;
let rvfDetailGeneration = 0;
// 只计选题动作；列表重新绘制/清空详情不会改变用户最后的选题意图。
let rvfSelectionGeneration = 0;
let rvfHeaderGeneration = 0;
let rvfDetailState = null;
let rvfListItems = new Map();
let rvfQueue = null;
// 今日上限：超过上限的到期记录默认不进今天的列表（和 Anki 一样只放出上限内的），
// 用户点“今天全部显示”后当天在本页会话内放开；记的是放开那天的日期，过了零点自动恢复。
let rvfShowAllOn = "";

// ---- 事件绑定与模块配置 ----

// 复习操作跨详情渲染仍可撤销，但跨页面 / 账号不可继续更新界面。

function rvfPageGuard() {
  const epoch = sessionEpoch;
  const owner = user?.id;
  const page = rvfPageGeneration;
  return () => Boolean(user) && owner === user.id && epoch === sessionEpoch && page === rvfPageGeneration;
}

function rvfClearDetail() {
  window.Scratch?.unmount();
  window.CodeRunner?.unmount();
  rvfDetailState?.menu?.destroy?.();
  rvfDetailGeneration += 1;
  rvfDetailState = null;
}

// 列表一题一卡；详情里的切换器保留每条易错点的独立操作。

function rvfRenderCards(today, selectedId = null) {
  const root = $("#cards");
  root.replaceChildren();
  for (const group of window.ProblemCards.group([...rvfListItems.values()], today)) {
    root.append(window.ProblemCards.card(group, {
      selected: group.members.some(item => item.id === selectedId),
      onOpen: item => run(async () => {
        await openMistake(item.id);
        if (rvfDetailState?.isCurrent() && rvfDetailState.item.id === item.id) {
          $("#detail").scrollIntoView({ block: "start", behavior: "auto" });
        }
      }),
    }));
  }
}

// 评分/暂停/推迟/撤销之后重新读服务端整题元数据，避免兄弟卡片显示旧完成度。

async function rvfRefreshProblem(item) {
  if (!Object.hasOwn(item, "progress")) return; // 老后端/缓存无题级元数据。
  const valid = rvfPageGuard();
  const generation = rvfListGeneration;
  try {
    const fresh = await api(`/api/mistakes/${item.id}`);
    if (!valid() || generation !== rvfListGeneration) return;
    for (const sibling of rvfListItems.values()) {
      if (sibling.problem_id !== fresh.problem_id) continue;
      for (const key of ["progress", "problem_tags", "problem_due_count", "problem_mistakes"]) sibling[key] = fresh[key];
    }
    rvfRenderCards(user.today, rvfDetailState?.item.id);
  } catch (_) { /* 离线仍保留最后一次服务端快照；不推算完成度。 */ }
}

function rvfListSummary(today, offline = false) {
  const count = window.ProblemCards.group([...rvfListItems.values()], today).length;
  return view === "today"
    ? `${today} · ${count} 道题待复习 · ${rvfListItems.size} 条易错点（含逾期）${offline ? " · 离线模式，评分先存本机" : ""}`
    : `共 ${count} 道题 · ${rvfListItems.size} 条易错点`;
}

function rvfRenderQueueHeader(queue) {
  rvfQueue = queue;
  $("#review-done-today").textContent = queue.cap == null
    ? `今天已复习 ${queue.done_today} · 不限`
    : `今天已复习 ${queue.done_today} / ${queue.cap}`;
  const select = $("#review-daily-cap");
  const value = queue.cap == null ? "" : String(queue.cap);
  if (value && ![...select.querySelectorAll("option")].some((option) => option.value === value)) {
    const option = element("option", value);
    option.value = value;
    select.append(option);
  }
  select.value = value;
  rvfRenderCapNote(queue);
}

function rvfCapNoteText(queue, showAll) {
  const shown = (queue.items || []).length;
  const later = Math.max(0, (queue.total_due ?? shown) - shown);
  if (showAll) return `今天已放开上限，显示全部 ${queue.total_due ?? shown} 条到期记录`;
  if (!shown) return `今天的 ${queue.cap} 条已经做完，其余 ${later} 条按逾期程度排到之后几天`;
  return `今日上限 ${queue.cap} 条：先做这 ${shown} 条，其余 ${later} 条按逾期程度排到之后几天`;
}

function rvfRenderCapNote(queue) {
  const note = $("#review-cap-note");
  const showAll = Boolean(queue.capped) && rvfShowAllOn !== "" && rvfShowAllOn === queue.today;
  note.hidden = !queue.capped;
  const text = queue.capped ? rvfCapNoteText(queue, showAll) : "";
  const label = $("#review-cap-text");
  if (label) label.textContent = text;
  else note.textContent = text;
  const toggle = $("#review-cap-toggle");
  if (toggle) {
    toggle.hidden = !queue.capped || !queue.today;
    toggle.textContent = showAll ? "恢复上限" : "今天全部显示";
  }
}

// 上限内放出哪几条由服务端按逾期程度排好（queue.items）；列表里多出来的先收起，
// 用户点“今天全部显示”后当天不再收。只删不加：列表本身仍以 /api/mistakes 为准。
function rvfApplyCapToList(queue) {
  if (view !== "today" || !queue.capped || (rvfShowAllOn && rvfShowAllOn === queue.today)) return;
  const allowed = new Set((queue.items || []).map((item) => item.id));
  const held = [...rvfListItems.keys()].filter((id) => !allowed.has(id));
  if (!held.length) return;
  rvfListGeneration += 1;
  for (const id of held) rvfListItems.delete(id);
  const selectedId = rvfDetailState?.isCurrent() ? rvfDetailState.item.id : null;
  rvfRenderCards(user.today, selectedId);
  $("#list-summary").textContent = rvfListSummary(user.today);
  $("#list-focus").hidden = !(rvfListItems.size && window.FocusReview);
  if (!rvfListItems.size) {
    $("#cards").append(element("p", `今天的上限已经做完，还有 ${held.length} 条排在之后几天。`, "muted empty-list"));
  }
}

function rvfToggleShowAll() {
  if (view !== "today" || !rvfQueue?.today) return;
  rvfShowAllOn = rvfShowAllOn === rvfQueue.today ? "" : rvfQueue.today;
  return loadList();
}

function rvfQueueParams(zone, tag) {
  const params = new URLSearchParams();
  if (zone) params.set("zone", zone);
  if (tag) params.set("tag", tag);
  return params;
}

function rvfSettle(promise) {
  return promise.then((value) => ({ status: "fulfilled", value }), (reason) => ({ status: "rejected", reason }));
}

async function rvfLoadQueueHeader(isCurrent = rvfPageGuard(), zone = $("#zone-filter").value, tag = $("#tag-filter").value, prefetched = null) {
  const generation = ++rvfHeaderGeneration;
  const valid = () => isCurrent() && view === "today" && generation === rvfHeaderGeneration;
  $("#review-cap-controls").hidden = true;
  $("#review-cap-note").hidden = true;
  $("#review-queue-status").textContent = "";
  $("#review-daily-cap").disabled = false;
  if (view !== "today") return;
  const queueRequest = prefetched
    ? prefetched.then((result) => (result.status === "fulfilled" ? result.value : Promise.reject(result.reason)))
    : api(`/api/review/queue?${rvfQueueParams(zone, tag)}`);
  const [queue, settings] = await Promise.allSettled([queueRequest, api("/api/me/review-settings")]);
  if (!valid()) return;
  if (queue.status === "fulfilled") {
    rvfRenderQueueHeader(queue.value);
    rvfApplyCapToList(queue.value);
    $("#review-cap-controls").hidden = false;
    rvfCapAvailable(settings.status === "fulfilled");
  } else if (queue.reason.status !== 404 && !window.OfflineReview?.isNetworkError?.(queue.reason)) {
    $("#review-queue-status").textContent = queue.reason.message;
  }
  if (settings.status === "rejected" && settings.reason.status !== 404 && !window.OfflineReview?.isNetworkError?.(settings.reason)) {
    $("#review-queue-status").textContent = settings.reason.message;
  }
}

function rvfCapAvailable(available) {
  $("#review-daily-cap").closest("label").hidden = !available;
}

async function rvfSetDailyCap() {
  if (view !== "today" || !user) return;
  const isCurrent = rvfPageGuard();
  const generation = ++rvfHeaderGeneration;
  const select = $("#review-daily-cap");
  const old = rvfQueue?.cap == null ? "" : String(rvfQueue.cap);
  const selected = select.value;
  const valid = () => isCurrent() && generation === rvfHeaderGeneration && view === "today";
  select.disabled = true;
  try {
    await api("/api/me/review-settings", { method: "PUT", body: JSON.stringify({ daily_review_cap: selected ? Number(selected) : null }) });
    if (!valid()) return;
    await loadList(); // 上限决定今天的列表放出多少条，换了上限要整页重排。
  } catch (error) {
    if (!valid()) return;
    select.value = old;
    if (error.status === 404) rvfCapAvailable(false);
    else $("#review-queue-status").textContent = error.message;
  } finally {
    if (isCurrent()) select.disabled = false;
  }
}

function rvfRemoveItem(item, { offline = false } = {}) {
  rvfListGeneration += 1; // 已发出的旧列表不得把刚移除的卡片放回来。
  if (view === "today") {
    rvfListItems.delete(item.id);
  } else {
    rvfListItems.set(item.id, item);
  }
  // 手感层：移除一张卡后其余卡片 FLIP 上移；reduced-motion 下 flip 直接执行 mutate。
  const mutate = () => rvfRenderCards(user.today);
  if (window.Feel?.flip) window.Feel.flip($("#cards"), mutate);
  else mutate();
  $("#list-summary").textContent = rvfListSummary(user.today, offline);
  clearDetail(offline ? "已记下，联网后同步" : "已更新复习安排", offline ? "这条评分存在本机队列里，恢复联网后会自动补交。" : item.leech_suspended ? "这道题连续失败 8 次，已自动暂停，建议换种方式学习，可随时恢复。" : "可以选择下一条继续复习。");
  if (offline) return; // 离线不触发会打网络的头部刷新与跨视图联动。
  void rvfRefreshProblem(item);
  notifyDataChanged("review");
  void rvfLoadQueueHeader(rvfPageGuard());
}

async function rvfRestoreItem(item, data) {
  const restored = { ...item, ...data };
  rvfListGeneration += 1;
  rvfListItems.delete(item.id);
  if (view !== "today" || (!restored.suspended_at && restored.due_date <= user.today)) {
    rvfListItems = new Map([[item.id, restored], ...rvfListItems]);
  }
  rvfRenderCards(user.today);
  $("#list-summary").textContent = rvfListSummary(user.today);
  notifyDataChanged("review");
  void rvfLoadQueueHeader(rvfPageGuard());
  await openMistake(item.id);
  void rvfRefreshProblem(restored);
}

async function loadList() {
  const isCurrent = rvfPageGuard();
  const generation = ++rvfListGeneration;
  const selection = rvfSelectionGeneration;
  const valid = () => isCurrent() && generation === rvfListGeneration;
  const zoneParam = $("#zone-filter").value;
  const tagParam = $("#tag-filter").value;
  if (view !== "all") listCreatedOn = "";
  $("#list-focus").hidden = true;
  window.TagFilters?.setSelected(tagParam);
  const query = `due_only=${view === "today"}` + (zoneParam ? `&zone=${encodeURIComponent(zoneParam)}` : "")
    + (tagParam ? `&tag=${encodeURIComponent(tagParam)}` : "")
    + (listCreatedOn ? `&created_on=${encodeURIComponent(listCreatedOn)}` : "");
  let data;
  let offlineToday = false;
  // 第一次打开、或在“今日复习 / 全部记录”之间切换时，旧卡片不该留着冒充新列表：慢的时候先放骨架。
  const cardsHost = $("#cards");
  const skeleton = (!cardsHost.childNodes.length || cardsHost.dataset?.listView !== view) ? window.Feel?.skeletonCards?.(cardsHost, 6) : null;
  const listRequest = api(`/api/mistakes?${query}`);
  // 今日复习同时取队列：上限内放出哪几条由服务端按逾期程度排好；取不到（老后端/出错）就不截断。
  const queuePromise = view === "today" ? rvfSettle(api(`/api/review/queue?${rvfQueueParams(zoneParam, tagParam)}`)) : null;
  try {
    data = await listRequest;
  } catch (error) {
    // 今日复习在断网（或请求直接网络失败）时，用预取队列兜底；其他视图/无缓存照常报错。
    if (view === "today" && window.OfflineReview?.isNetworkError(error)) {
      data = await window.OfflineReview?.todayFallback();
      if (data) offlineToday = true;
    }
    if (!offlineToday) {
      if (skeleton?.cancel() && valid()) cardsHost.replaceChildren();
      throw error;
    }
  }
  skeleton?.cancel();
  if (!valid()) return;
  if (cardsHost.dataset) cardsHost.dataset.listView = view; // #cards 里现在画的是哪个视图
  // 与 SQLite NOCASE 一致，只折叠 ASCII 大写标签字符。
  const tagKey = tagParam.trim().replace(/[A-Z]/g, (letter) => letter.toLowerCase());
  let scheduleChanged = false;
  const items = data.items.flatMap((item) => {
    const current = rvfListItems.get(item.id);
    if (!current || !(current.version > item.version)) return [item];
    const fresh = { ...item, ...current };
    // 新筛选决定成员；同一条记录已保存的较新版本不能被旧快照覆盖。
    if ((zoneParam && fresh.zone !== zoneParam)
      || (tagKey && !fresh.tags?.some((tag) => tag.replace(/[A-Z]/g, (letter) => letter.toLowerCase()) === tagKey))
      || (view === "today" && (fresh.suspended_at || fresh.due_date > data.today))) return [];
    scheduleChanged ||= fresh.due_date !== item.due_date;
    return [fresh];
  });
  if (scheduleChanged) items.sort((left, right) => left.due_date.localeCompare(right.due_date) || left.id - right.id);
  data = { ...data, items };
  rvfListItems = new Map(data.items.map((item) => [item.id, item]));
  if (offlineToday) {
    $("#review-cap-controls").hidden = true;
    $("#review-cap-note").hidden = true;
    $("#review-queue-status").textContent = "离线模式：展示的是联网时预取的今日队列，评分会先存在本机。";
  } else {
    void rvfLoadQueueHeader(valid, zoneParam, tagParam, queuePromise);
  }
  $("#list-focus").hidden = !(view === "today" && data.items.length && window.FocusReview);
  $("#list-filter").hidden = !listCreatedOn;
  if (listCreatedOn) {
    const [, month, day] = listCreatedOn.split("-").map(Number);
    $("#list-filter-text").textContent = `只看 ${month} 月 ${day} 日新增的记录`;
  }
  user.today = data.today;
  updateUserInfo();

  $("#list-title").textContent =
    view === "today" ? "今日复习" : "全部记录";
  $("#list-summary").textContent = rvfListSummary(data.today, offlineToday);

  $("#cards").replaceChildren();
  // 请求期间用户另选了题：列表可更新，但不能清掉其已显示或仍在加载的详情。
  const selectionChanged = selection !== rvfSelectionGeneration;
  if (!selectionChanged) clearDetail();

  if (!data.items.length) {
    $("#cards").append(element(
      "p",
      view === "today" ? "今天没有待复习的易错点。" : "你的第一条记录，会出现在这里。",
      "muted empty-list"
    ));
    if (!selectionChanged) {
      clearDetail(
        view === "today" ? "今天的复习，告一段落" : "从一道做错的题开始",
        view === "today"
          ? "可以去「全部记录」回看笔记，也可以在「新增记录」留下今天的新发现。"
          : "点击「新增记录」，留下代码、思路和错因。每条易错点都会单独安排复习。"
      );
      // 筛选条件下的"空"不代表没有记录，只在没有任何筛选时给出下一步按钮。
      const unfiltered = !zoneParam && !tagParam && !listCreatedOn;
      window.Onboarding?.emptyNext(view, $("#detail .empty-state"), unfiltered
        ? (view === "all" ? { total: 0, view: "all" } : { view: "today" }) : null);
    }
    return;
  }

  rvfRenderCards(data.today, selectionChanged && rvfDetailState?.isCurrent() ? rvfDetailState.item.id : null);
}

