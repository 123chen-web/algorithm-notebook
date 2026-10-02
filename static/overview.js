"use strict";

/* 总览页渲染（原"学习大厅"）。数据来自 GET /api/overview，由 app.js 的 loadHome() 调用。
   对外契约：window.Overview = { render(data, context), renderError(), reset() }。
   context = { username, onOpenRecord(id), onOpenPost(id), onFocus() }；所有用户文本一律走 textContent。 */
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const WEEKDAYS = ["日", "一", "二", "三", "四", "五", "六"];
  const DEFAULT_TITLE = "把薄弱点，练成得分点。";
  const $ = (selector) => document.querySelector(selector);

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function svgNode(tag, attributes) {
    const item = document.createElementNS(SVG_NS, tag);
    for (const [name, value] of Object.entries(attributes)) item.setAttribute(name, value);
    return item;
  }
  function parseDay(text) {
    const [year, month, day] = text.split("-").map(Number);
    return new Date(year, month - 1, day, 12);
  }
  function greetingFor(hour) {
    if (hour < 5) return "夜深了";
    if (hour < 11) return "早上好";
    if (hour < 13) return "中午好";
    if (hour < 18) return "下午好";
    return "晚上好";
  }

  /* ---------- 问候 ---------- */
  function renderGreeting(data, username) {
    $("#home-title").textContent = username ? `${greetingFor(new Date().getHours())}，${username}` : DEFAULT_TITLE;
    const streak = data.streak_days > 0
      ? `已经连续第 ${data.streak_days} 天` : "今天复习就能开始连续打卡";
    $("#home-subtitle").textContent = data.due_count > 0
      ? `今天有 ${data.due_count} 条等你批改，${streak}`
      : `今天的复习都完成了，${data.streak_days > 0 ? streak : "去记录新的发现吧"}`;
  }

  /* ---------- 今日复习磁贴 ---------- */
  function renderReview(data, context) {
    const tile = $("#tile-review");
    const due = data.due_count;
    tile.dataset.state = due > 0 ? "due" : "done";
    tile.querySelector(".ov-stamp").textContent = due > 0 ? "待批" : "完成";
    const count = $("#home-due-count");
    count.closest(".tile-count-wrap").classList.toggle("has-due", due > 0);
    count.textContent = String(due);
    count.hidden = false;
    $("#home-due-caption").textContent = due > 0
      ? `条易错点等你批改${data.overdue_count > 0 ? `，其中 ${data.overdue_count} 条已逾期` : ""}`
      : "今天的复习都完成了";

    const dueZones = data.zones.filter((zone) => zone.due > 0);
    const bar = $("#tile-review-bar");
    bar.replaceChildren(...dueZones.map((zone) => {
      const segment = node("span");
      segment.style.flexGrow = String(zone.due);
      return segment;
    }));
    $("#tile-review-zones").textContent = dueZones.map((zone) => `${zone.zone} ${zone.due}`).join(" · ");
    bar.closest(".ov-zones").hidden = dueZones.length === 0;

    const start = $("#tile-review-start");
    start.dataset.view = due > 0 ? "today" : "new";
    start.textContent = due > 0 ? "开始复习" : "去新增记录";
    const focus = $("#tile-review-focus");
    focus.hidden = !(due > 0 && window.FocusReview);
    focus.onclick = () => context.onFocus?.();
  }

  /* ---------- 连续打卡磁贴 ---------- */
  function tickIcon(done, today) {
    const icon = svgNode("svg", { class: "ov-tick", viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" });
    if (done) {
      icon.append(
        svgNode("circle", { cx: "12", cy: "12", r: "10", fill: "currentColor" }),
        svgNode("path", {
          d: "m7.5 12.5 3 3 6-7", fill: "none", stroke: "var(--surface)", "stroke-width": "2.4",
          "stroke-linecap": "round", "stroke-linejoin": "round",
        }),
      );
    } else {
      icon.append(svgNode("circle", {
        cx: "12", cy: "12", r: "9", fill: "none", stroke: "currentColor", "stroke-width": "1.8",
        ...(today ? { "stroke-dasharray": "3.4 3.4" } : {}),
      }));
    }
    return icon;
  }
  function renderStreak(data) {
    $("#streak-days").textContent = String(data.streak_days);
    const today = parseDay(data.today);
    const ticks = data.last7.map((done, index) => {
      const date = new Date(today.getTime());
      date.setDate(date.getDate() - (6 - index));
      const isToday = index === 6;
      const item = node("li", `${done ? "is-done" : ""} ${isToday ? "is-today" : ""}`.trim());
      const label = `${date.getMonth() + 1}月${date.getDate()}日${isToday ? "（今天）" : ""}，${done ? "已复习" : "未复习"}`;
      item.title = label;
      const hidden = node("span", "ov-sr-only", label);
      const letter = node("span", "", WEEKDAYS[date.getDay()]);
      letter.setAttribute("aria-hidden", "true");
      item.append(tickIcon(done, isToday), letter, hidden);
      return item;
    });
    $("#streak-ticks").replaceChildren(...ticks);
  }

  /* ---------- 热力图磁贴 ---------- */
  function renderHeat(data) {
    $("#tile-heat-summary").textContent = `近 7 天复习 ${data.week.reviews} 次 · 新增 ${data.week.records} 条`;
    const mount = $("#overview-heatmap");
    const total = $("#tile-heat-total");
    if (window.ActivityWidgets?.isMounted(mount)) {
      // 手动刷新：磁贴和侧栏日历一起重取（跨过午夜后侧栏的"今天"也要跟上）。
      window.ActivityWidgets.refresh();
    } else if (window.ActivityWidgets) {
      window.ActivityWidgets.mountHeatmap(mount, {
        weeks: 26,
        onData: (activity) => {
          total.replaceChildren(
            node("strong", "", String(activity.totals.reviews)),
            node("span", "", `次复习 · 活跃 ${activity.totals.active_days} 天`),
          );
          total.hidden = false;
        },
      });
    } else {
      mount.replaceChildren(node("p", "ov-heat-note", "热力图即将上线。"));
    }
  }

  /* ---------- 待复习预览 ---------- */
  function masteryDots(repetitions) {
    const wrap = node("span", "ov-dots");
    wrap.setAttribute("aria-hidden", "true");
    for (let index = 0; index < 5; index += 1) wrap.append(node("span", `ov-dot${index < repetitions ? " is-on" : ""}`));
    return wrap;
  }
  function renderDue(data, context) {
    const list = $("#ov-due-list");
    const all = $("#ov-due-all");
    all.hidden = data.due_count === 0;
    all.textContent = `查看全部 ${data.due_count} 条 →`;
    $("#ov-due-empty").hidden = data.due_preview.length > 0;
    list.replaceChildren(...data.due_preview.map((item) => {
      const entry = node("li");
      const button = node("button", "ov-due-item");
      button.type = "button";
      const head = node("span", "ov-due-head");
      head.append(node("span", "ov-zone-tag", item.zone), node("span", "ov-due-title", item.title));
      const main = node("span", "ov-due-main");
      main.append(head, node("span", "ov-due-desc", item.description || "错因待 AI 诊断"));
      const side = node("span", "ov-due-side");
      side.append(
        item.overdue_days > 0
          ? node("span", "ov-chip ov-chip-late", `逾期 ${item.overdue_days} 天`)
          : node("span", "ov-chip ov-chip-today", "今天"),
        masteryDots(Math.min(5, item.repetitions)),
        node("span", "", `复习 ${item.repetitions} 次`),
      );
      button.append(main, side);
      button.setAttribute("aria-label", `${item.zone}：${item.title}，${item.overdue_days > 0 ? `逾期 ${item.overdue_days} 天` : "今天到期"}，已复习 ${item.repetitions} 次`);
      button.addEventListener("click", () => context.onOpenRecord?.(item.id));
      entry.append(button);
      return entry;
    }));
  }

  /* ---------- 右侧信息栏 ---------- */
  function renderWeakness(data) {
    const target = $("#ov-weakness");
    const { status, mistake_count: count, minimum_mistakes: minimum, top } = data.weakness;
    const parts = [];
    if (status === "ready" && top) {
      parts.push(node("span", "ov-card-title", top.title));
      parts.push(node("span", "ov-confidence", top.confidence));
      parts.push(node("span", "ov-card-note", "查看完整分析与改进建议"));
    } else if (status === "ready") {
      parts.push(node("span", "ov-card-title", "暂未发现明确的反复规律"));
      parts.push(node("span", "ov-card-note", "继续记录和复习，规律会更清楚。"));
    } else if (status === "not_analyzed") {
      parts.push(node("span", "ov-card-title", "还没有分析过"));
      parts.push(node("span", "ov-card-note", `已经积累 ${count} 条易错点，可以让 AI 找出反复卡住你的原因。`));
    } else {
      const missing = Math.max(1, (minimum || 5) - count);
      parts.push(node("span", "ov-card-title", "再积累几条就能分析"));
      parts.push(node("span", "ov-card-note", `还差 ${missing} 条易错点，不消耗 AI 额度。`));
    }
    target.replaceChildren(...parts);
  }
  function sealTier(level) {
    if (level.number >= 6) return "high";
    return level.number >= 3 ? "mid" : "low";
  }
  function renderGroups(data) {
    const list = $("#ov-groups");
    if (!data.groups_preview.length) {
      const empty = node("li");
      const text = node("p", "ov-card-empty", "和朋友一起复习，互相督促。");
      const link = node("button", "ov-link", "创建或加入小组 →");
      link.type = "button";
      link.dataset.view = "groups";
      text.append(link);
      empty.append(text);
      list.replaceChildren(empty);
      return;
    }
    list.replaceChildren(...data.groups_preview.map((group) => {
      const entry = node("li");
      const button = node("button", "ov-group");
      button.type = "button";
      button.dataset.view = "groups";
      const seal = node("span", "ov-seal", group.level.name.slice(0, 1));
      seal.dataset.tier = sealTier(group.level);
      seal.setAttribute("aria-hidden", "true");
      const fill = node("span");
      fill.style.width = `${Math.round(Math.max(0, Math.min(1, group.level.progress)) * 100)}%`;
      const bar = node("span", "ov-bar");
      bar.setAttribute("aria-hidden", "true");
      bar.append(fill);
      const line = node("span", "ov-group-line");
      line.append(node("span", "ov-group-name", group.name), node("span", "ov-group-level", `Lv.${group.level.number}`));
      const copy = node("span", "ov-group-copy");
      copy.append(line, bar);
      button.append(seal, copy);
      button.setAttribute("aria-label", `${group.name}，${group.level.name} Lv.${group.level.number}，${group.member_count} 人`);
      entry.append(button);
      return entry;
    }));
  }
  function renderHot(data, context) {
    const card = $("#ov-hot-card");
    const post = data.hot_post;
    card.hidden = !post;
    const target = $("#ov-hot");
    target.onclick = null;
    if (!post) {
      target.replaceChildren();
      return;
    }
    target.replaceChildren(
      node("span", "ov-card-title", post.title),
      node("span", "ov-card-note", `${post.comment_count} 层楼 · ${post.username}`),
    );
    target.onclick = () => context.onOpenPost?.(post.id);
  }

  /* ---------- 对外 ---------- */
  function render(data, context = {}) {
    $("#overview-error").hidden = true;
    renderGreeting(data, context.username);
    renderReview(data, context);
    renderStreak(data);
    renderHeat(data);
    renderDue(data, context);
    renderWeakness(data);
    window.Mastery?.mountAlert($("#ov-fading-card"));
    renderGroups(data);
    renderHot(data, context);
    const shell = window.AppShell;
    if (shell) {
      shell.setCounts(data);
      shell.setZones(data.zones);
      shell.setStreak(data.streak_days);
      shell.setUser(context.username);
    }
  }

  function renderError() {
    $("#overview-error").hidden = false;
    $("#home-subtitle").textContent = "";
    $("#home-due-caption").textContent = "暂时无法读取数量，可进入复习重试";
  }

  function reset() {
    $("#overview-error").hidden = true;
    $("#home-title").textContent = DEFAULT_TITLE;
    $("#home-subtitle").textContent = "正在读取今天的复习情况…";
    const count = $("#home-due-count");
    count.closest(".tile-count-wrap").classList.remove("has-due");
    count.hidden = true;
    count.textContent = "";
    $("#home-due-caption").textContent = "正在读取待复习记录…";
    $("#tile-review").dataset.state = "loading";
    $("#tile-review").querySelector(".ov-stamp").textContent = "待批";
    $("#tile-review-bar").replaceChildren();
    $("#tile-review-zones").textContent = "";
    $("#tile-review-focus").hidden = true;
    $("#streak-days").textContent = "0";
    $("#streak-ticks").replaceChildren();
    $("#tile-heat-summary").textContent = "";
    $("#tile-heat-total").hidden = true;
    $("#tile-heat-total").replaceChildren();
    $("#ov-due-list").replaceChildren();
    $("#ov-due-all").hidden = true;
    $("#ov-due-empty").hidden = true;
    $("#ov-weakness").replaceChildren();
    $("#ov-groups").replaceChildren();
    $("#ov-hot-card").hidden = true;
    $("#ov-hot").replaceChildren();
  }

  window.Overview = { render, renderError, reset };
})();
