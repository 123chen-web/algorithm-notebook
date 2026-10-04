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

  /* ---------- 趋势区：指标条 + 随指标切换的主图（GET /api/stats/summary） ---------- */
  const TREND_DAYS = [7, 30, 90];
  const TREND_STORE = "home-trend-days";
  const TREND_KEYS = ["due", "streak", "reviews", "retention"];
  const RETENTION_TARGET = 0.85;
  const TREND_HEIGHT = 224;
  const TREND_MARGIN = { left: 40, right: 14, top: 30, bottom: 30 };
  const trend = { epoch: 0, seq: 0, api: null, days: 30, key: "due", summary: null, width: 0 };

  function niceScale(maxValue) {
    if (!(maxValue > 0)) return { max: 1, ticks: [0, 1] };
    const magnitude = 10 ** Math.floor(Math.log10(maxValue));
    const step = [0.5, 1, 2, 5, 10].map((unit) => unit * magnitude).filter((candidate) => candidate >= 1).find((candidate) => maxValue / candidate <= 5);
    const top = Math.ceil(maxValue / step) * step;
    const ticks = [];
    for (let value = 0; value <= top + step / 2; value += step) ticks.push(Math.round(value * 1e6) / 1e6);
    return { max: top, ticks };
  }
  const monthDay = (date) => date.slice(5);
  function pointsLabel(points) {
    return `${points < 0 ? "−" : "+"}${Math.abs(points).toFixed(1)} 个点`;
  }

  function reviewsTrend(current, previous) {
    if (previous === 0) {
      return current === 0
        ? { dir: "flat", text: "持平", label: "与上一周期持平" }
        : { dir: "new", text: "新", label: "上一周期没有复习，本周期为新增" };
    }
    const percent = Math.round(((current - previous) / previous) * 100);
    if (percent === 0) return { dir: "flat", text: "→ 持平", label: "与上一周期持平" };
    const up = percent > 0;
    return {
      dir: up ? "up" : "down",
      text: `${up ? "↗ +" : "↘ −"}${Math.abs(percent)}%`,
      label: `比上一周期${up ? "增加" : "减少"} ${Math.abs(percent)}%`,
    };
  }
  function retentionTrend(current, previous) {
    if (current === null || previous === null) return null;
    const points = Math.round((current - previous) * 1000) / 10;
    if (points === 0) return { dir: "flat", text: "→ 持平", label: "与上一周期持平" };
    return {
      dir: points > 0 ? "up" : "down",
      text: `${points > 0 ? "↗" : "↘"} ${pointsLabel(points)}`,
      label: `比上一周期${points > 0 ? "高" : "低"} ${Math.abs(points).toFixed(1)} 个点`,
    };
  }

  /* 纯函数：指标块文案、涨跌方向与百分比、各图的数据与刻度。 */
  function trendModel(summary) {
    const days = summary.days;
    const rate = summary.retention.current.rate;
    const previousRate = summary.retention.previous.rate;
    const overdue = summary.due.overdue;
    const forecast = summary.forecast.map((item, index) => ({
      date: item.date,
      value: item.due,
      today: index === 0,
      label: index === 0 ? "今天" : index === 1 ? "明" : index === 2 ? "后" : monthDay(item.date),
    }));
    const weekly = summary.retention.weekly;
    return {
      days,
      metrics: [
        {
          key: "due", label: "待复习", value: String(summary.due.today), unit: "条", trend: null,
          sub: overdue > 0 ? `其中逾期 ${overdue}` : "没有逾期",
          how: "到期日在今天或更早的易错点数量（含逾期）。图为未来 14 天每天到期的条数，今天的柱包含全部逾期。",
        },
        {
          key: "streak", label: "连续打卡", value: String(summary.streak_days), unit: "天", trend: null,
          sub: "按你的本地日统计",
          how: "每天至少完成一次复习算一天；今天还没复习但昨天复习了，连续天数不会清零。图为近期每天是否复习。",
        },
        {
          key: "reviews", label: `近 ${days} 天复习`, value: String(summary.reviews.current), unit: "次",
          trend: reviewsTrend(summary.reviews.current, summary.reviews.previous),
          sub: `上一周期 ${summary.reviews.previous} 次`,
          how: `最近 ${days} 天（含今天）的复习评分次数，对比紧邻的前 ${days} 天。`,
        },
        {
          key: "retention", label: "真实保持率", value: rate === null ? "—" : `${Math.round(rate * 100)}%`,
          unit: "", trend: retentionTrend(rate, previousRate),
          sub: rate === null
            ? "需要至少一次非首次复习"
            : `${summary.retention.current.passed}/${summary.retention.current.reviews} 次非首次复习评分 ≥ 3`,
          how: `只看「非首次复习」（这条易错点之前已经复习过）里评分 ≥ 3 的占比，最近 ${days} 天，对比前 ${days} 天。分母为 0 时显示「—」。图为最近 8 个自然周（周一起算）。`,
        },
      ],
      charts: {
        due: {
          kind: "bars", items: forecast, scale: niceScale(Math.max(0, ...forecast.map((item) => item.value))),
          empty: forecast.every((item) => item.value === 0), emptyText: "未来 14 天没有到期的易错点。",
          caption: "未来 14 天每天到期条数（今天含逾期）",
          table: { headers: ["日期", "到期条数"], rows: forecast.map((item) => [item.date + (item.today ? "（今天，含逾期）" : ""), String(item.value)]) },
        },
        streak: {
          kind: "cells", items: summary.daily_reviews, scale: niceScale(Math.max(0, ...summary.daily_reviews.map((item) => item.count))),
          empty: summary.daily_reviews.every((item) => item.count === 0), emptyText: `近 ${days} 天还没有复习记录。`,
          caption: `近 ${days} 天每天是否复习`,
          table: { headers: ["日期", "复习次数"], rows: summary.daily_reviews.map((item) => [item.date, String(item.count)]) },
        },
        reviews: {
          kind: "area", items: summary.daily_reviews, scale: niceScale(Math.max(0, ...summary.daily_reviews.map((item) => item.count))),
          empty: summary.daily_reviews.every((item) => item.count === 0), emptyText: `近 ${days} 天还没有复习记录。`,
          caption: `近 ${days} 天每天的复习次数`,
          table: { headers: ["日期", "复习次数"], rows: summary.daily_reviews.map((item) => [item.date, String(item.count)]) },
        },
        retention: {
          kind: "line",
          items: weekly.map((week) => ({ date: week.week_start, value: week.rate, reviews: week.reviews, passed: week.passed })),
          scale: { max: 1, ticks: [0, 0.25, 0.5, 0.75, 1] }, target: RETENTION_TARGET,
          empty: weekly.every((week) => week.rate === null), emptyText: "需要至少一次非首次复习，才能算出真实保持率。",
          caption: "最近 8 周的真实保持率（周一起算）",
          table: {
            headers: ["周起始", "非首次复习", "通过", "保持率"],
            rows: weekly.map((week) => [week.week_start, String(week.reviews), String(week.passed), week.rate === null ? "—" : `${(week.rate * 100).toFixed(1)}%`]),
          },
        },
      },
    };
  }

  /* ---- 图：同一个比例尺画刻度、标签和数据点 ---- */
  function chartGeometry(width) {
    const { left, right, top, bottom } = TREND_MARGIN;
    return { width, height: TREND_HEIGHT, left, top, plotW: width - left - right, plotH: TREND_HEIGHT - top - bottom };
  }
  function svgText(text, attributes, className) {
    const item = svgNode("text", { class: className, ...attributes });
    item.textContent = text;
    return item;
  }
  function chartFrame(model, geo, formatTick) {
    const parts = [];
    const y = (value) => geo.top + geo.plotH - (value / model.scale.max) * geo.plotH;
    for (const tick of model.scale.ticks) {
      parts.push(svgNode("line", { class: tick === 0 ? "ov-ch-axis" : "ov-ch-grid", x1: geo.left, x2: geo.left + geo.plotW, y1: y(tick), y2: y(tick) }));
      parts.push(svgText(formatTick(tick), { x: geo.left - 6, y: y(tick) + 4, "text-anchor": "end" }, "ov-ch-label"));
    }
    return { parts, y };
  }
  function pickLabelIndexes(count, plotW, minGap) {
    const slots = Math.max(2, Math.floor(plotW / minGap));
    if (count <= slots) return Array.from({ length: count }, (_, index) => index);
    const picked = new Set([0, count - 1]);
    for (let slot = 1; slot < slots - 1; slot += 1) picked.add(Math.round((slot * (count - 1)) / (slots - 1)));
    return [...picked].sort((a, b) => a - b);
  }

  function drawBars(model, geo) {
    const frame = chartFrame(model, geo, (tick) => String(tick));
    const count = model.items.length;
    const pitch = geo.plotW / count;
    const barW = Math.max(4, Math.min(28, pitch * 0.62));
    const every = pitch < 34 ? 2 : 1;
    model.items.forEach((item, index) => {
      const cx = geo.left + pitch * (index + 0.5);
      const top = frame.y(item.value);
      const base = frame.y(0);
      if (item.value === 0) {
        frame.parts.push(svgNode("line", { class: "ov-ch-zero", x1: cx - barW / 2, x2: cx + barW / 2, y1: base, y2: base }));
      } else {
        frame.parts.push(svgNode("rect", { class: `ov-ch-bar${item.today ? " is-today" : ""}`, x: cx - barW / 2, y: top, width: barW, height: base - top, rx: 2 }));
        frame.parts.push(svgText(String(item.value), { x: cx, y: top - 5, "text-anchor": "middle" }, `ov-ch-value${item.today ? " is-today" : ""}`));
      }
      if (item.today) frame.parts.push(svgText("含逾期", { x: cx, y: Math.max(11, (item.value === 0 ? base : top) - 19), "text-anchor": "middle" }, "ov-ch-note"));
      if (index % every === 0) frame.parts.push(svgText(item.label, { x: cx, y: geo.top + geo.plotH + 18, "text-anchor": "middle" }, `ov-ch-label${item.today ? " is-today" : ""}`));
    });
    return { nodes: frame.parts, xAt: (index) => geo.left + pitch * (index + 0.5), count, pitch };
  }

  function drawCells(model, geo) {
    const count = model.items.length;
    const gap = count > 60 ? 1 : 2;
    const cell = (geo.plotW - gap * (count - 1)) / count;
    const rowY = geo.top + geo.plotH / 2 - 14;
    const nodes = [];
    model.items.forEach((item, index) => {
      const level = item.count === 0 ? 0 : item.count / model.scale.max;
      const rect = svgNode("rect", {
        class: `ov-ch-cell${item.count > 0 ? " is-on" : ""}`, x: geo.left + index * (cell + gap), y: rowY,
        width: cell, height: 28, rx: 2,
      });
      if (item.count > 0) rect.setAttribute("fill-opacity", String(Math.round((0.35 + 0.65 * level) * 100) / 100));
      nodes.push(rect);
    });
    for (const index of pickLabelIndexes(count, geo.plotW, 64)) {
      const x = geo.left + index * (cell + gap) + cell / 2;
      nodes.push(svgText(index === count - 1 ? "今天" : monthDay(model.items[index].date), { x, y: rowY + 28 + 18, "text-anchor": "middle" }, "ov-ch-label"));
    }
    return { nodes, xAt: (index) => geo.left + index * (cell + gap) + cell / 2, count, pitch: cell + gap };
  }

  function drawArea(model, geo) {
    const frame = chartFrame(model, geo, (tick) => String(tick));
    const count = model.items.length;
    const step = count > 1 ? geo.plotW / (count - 1) : 0;
    const xAt = (index) => geo.left + step * index;
    const points = model.items.map((item, index) => `${xAt(index).toFixed(1)},${frame.y(item.count).toFixed(1)}`);
    const base = frame.y(0).toFixed(1);
    frame.parts.push(svgNode("polygon", { class: "ov-ch-area", points: `${xAt(0).toFixed(1)},${base} ${points.join(" ")} ${xAt(count - 1).toFixed(1)},${base}` }));
    frame.parts.push(svgNode("polyline", { class: "ov-ch-line", points: points.join(" "), fill: "none" }));
    const last = model.items[count - 1];
    frame.parts.push(svgNode("circle", { class: "ov-ch-dot", cx: xAt(count - 1), cy: frame.y(last.count), r: 4 }));
    for (const index of pickLabelIndexes(count, geo.plotW, 64)) {
      frame.parts.push(svgText(index === count - 1 ? "今天" : monthDay(model.items[index].date), { x: xAt(index), y: geo.top + geo.plotH + 18, "text-anchor": index === 0 ? "start" : index === count - 1 ? "end" : "middle" }, "ov-ch-label"));
    }
    return { nodes: frame.parts, xAt, count, pitch: step };
  }

  function drawLine(model, geo) {
    const frame = chartFrame(model, geo, (tick) => `${Math.round(tick * 100)}%`);
    const count = model.items.length;
    const step = count > 1 ? geo.plotW / (count - 1) : 0;
    const xAt = (index) => geo.left + step * index;
    const targetY = frame.y(model.target);
    frame.parts.push(svgNode("line", { class: "ov-ch-target", x1: geo.left, x2: geo.left + geo.plotW, y1: targetY, y2: targetY }));
    frame.parts.push(svgText(`目标线 ${Math.round(model.target * 100)}%`, { x: geo.left + geo.plotW, y: targetY - 5, "text-anchor": "end" }, "ov-ch-note"));
    let segment = [];
    const flush = () => {
      if (segment.length > 1) frame.parts.push(svgNode("polyline", { class: "ov-ch-line", fill: "none", points: segment.join(" ") }));
      segment = [];
    };
    model.items.forEach((item, index) => {
      if (item.value === null) { flush(); return; }
      segment.push(`${xAt(index).toFixed(1)},${frame.y(item.value).toFixed(1)}`);
    });
    flush();
    model.items.forEach((item, index) => {
      if (item.value === null) return;
      frame.parts.push(svgNode("circle", { class: `ov-ch-dot${index === count - 1 ? " is-last" : ""}`, cx: xAt(index), cy: frame.y(item.value), r: index === count - 1 ? 4 : 3 }));
    });
    model.items.forEach((item, index) => {
      frame.parts.push(svgText(monthDay(item.date), { x: xAt(index), y: geo.top + geo.plotH + 18, "text-anchor": "middle" }, "ov-ch-label"));
    });
    return { nodes: frame.parts, xAt, count, pitch: step };
  }

  function readoutFor(key, model, index) {
    const item = model.items[index];
    if (key === "due") return `${item.today ? "今天（含逾期）" : item.date} · ${item.value} 条到期`;
    if (key === "retention") {
      return item.value === null
        ? `${item.date} 起的一周 · 没有非首次复习`
        : `${item.date} 起的一周 · 保持率 ${(item.value * 100).toFixed(1)}%（${item.passed}/${item.reviews}）`;
    }
    return `${item.date} · 复习 ${item.count} 次`;
  }

  function buildChart(key, model, width) {
    const geo = chartGeometry(width);
    const drawer = { due: drawBars, streak: drawCells, reviews: drawArea, retention: drawLine }[key];
    const drawn = drawer(model, geo);
    const svg = svgNode("svg", {
      class: "ov-chart-svg", viewBox: `0 0 ${geo.width} ${geo.height}`, role: "img", focusable: "false",
      "aria-label": model.caption,
    });
    svg.append(...drawn.nodes);
    return { svg, geo, drawn };
  }

  function nearestIndex(event, svg, geo, drawn) {
    const rect = svg.getBoundingClientRect();
    const scale = rect.width > 0 ? geo.width / rect.width : 1;
    const x = (event.clientX - rect.left) * scale;
    let best = 0;
    let distance = Infinity;
    for (let index = 0; index < drawn.count; index += 1) {
      const gap = Math.abs(drawn.xAt(index) - x);
      if (gap < distance) { best = index; distance = gap; }
    }
    return best;
  }

  function renderTrendChart() {
    const wrap = $("#home-trend-chart");
    const readout = $("#home-trend-readout");
    const summary = trend.summary;
    if (!wrap || !summary) return;
    const model = trendModel(summary);
    const chart = model.charts[trend.key];
    const panel = $("#home-trend-panel");
    panel.dataset.chart = trend.key;
    if (chart.empty) {
      wrap.replaceChildren(node("p", "ov-trend-empty", chart.emptyText));
      readout.textContent = "";
    } else {
      const width = Math.max(260, Math.min(760, trend.width || 640));
      const { svg, geo, drawn } = buildChart(trend.key, chart, width);
      const show = (index) => { readout.textContent = readoutFor(trend.key, chart, index); };
      for (const type of ["pointermove", "pointerdown"]) svg.addEventListener(type, (event) => show(nearestIndex(event, svg, geo, drawn)));
      wrap.replaceChildren(svg);
      show(drawn.count - 1);
    }
    $("#home-trend-caption").textContent = chart.caption;
    const table = node("table", "ov-trend-table");
    table.append(node("caption", "", chart.caption));
    const head = node("tr");
    for (const header of chart.table.headers) { const cell = node("th", "", header); cell.scope = "col"; head.append(cell); }
    const body = node("tbody");
    for (const row of chart.table.rows) {
      const line = node("tr");
      row.forEach((text, index) => {
        const cell = node(index === 0 ? "th" : "td", "", text);
        if (index === 0) cell.scope = "row";
        line.append(cell);
      });
      body.append(line);
    }
    const headGroup = node("thead");
    headGroup.append(head);
    table.append(headGroup, body);
    $("#home-trend-table-body").replaceChildren(table);
  }

  function trendTabs() { return [...$("#home-trend-metrics").querySelectorAll('[role="tab"]')]; }
  function selectTrendMetric(key, { focus = false } = {}) {
    if (!TREND_KEYS.includes(key)) return;
    trend.key = key;
    for (const tab of trendTabs()) {
      const selected = tab.dataset.metric === key;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      tab.classList.toggle("is-selected", selected);
      if (selected && focus) tab.focus();
    }
    if (trend.summary) {
      const metric = trendModel(trend.summary).metrics.find((item) => item.key === key);
      $("#home-trend-how").textContent = metric.how;
      renderTrendChart();
    }
  }
  function onTrendTabKey(event) {
    const keys = { ArrowRight: 1, ArrowLeft: -1 };
    let target;
    if (event.key in keys) target = TREND_KEYS[(TREND_KEYS.indexOf(trend.key) + keys[event.key] + TREND_KEYS.length) % TREND_KEYS.length];
    else if (event.key === "Home") target = TREND_KEYS[0];
    else if (event.key === "End") target = TREND_KEYS[TREND_KEYS.length - 1];
    else return;
    event.preventDefault();
    selectTrendMetric(target, { focus: true });
  }

  function trendStatus(state, text) {
    const section = $("#home-trend");
    section.dataset.state = state;
    if (state === "loading") section.setAttribute("aria-busy", "true"); else section.removeAttribute("aria-busy");
    $("#home-trend-skeleton").hidden = state !== "loading";
    $("#home-trend-error").hidden = state !== "error";
    $("#home-trend-body").hidden = state !== "ready";
    if (state === "error") $("#home-trend-error-text").textContent = text || "暂时无法读取趋势数据。";
  }

  function renderTrendMetrics(model) {
    const strip = $("#home-trend-metrics");
    strip.replaceChildren(...model.metrics.map((metric) => {
      const tab = node("button", "ov-metric");
      tab.type = "button";
      tab.id = `home-trend-tab-${metric.key}`;
      tab.dataset.metric = metric.key;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-controls", "home-trend-panel");
      const value = node("span", "ov-metric-value");
      value.append(node("strong", "", metric.value), node("span", "ov-metric-unit", metric.unit));
      const parts = [node("span", "ov-metric-label", metric.label), value];
      if (metric.trend) {
        const delta = node("span", `ov-metric-trend is-${metric.trend.dir}`, metric.trend.text);
        delta.title = metric.trend.label;
        delta.setAttribute("aria-label", metric.trend.label);
        parts.push(delta);
      }
      parts.push(node("span", "ov-metric-sub", metric.sub));
      tab.append(...parts);
      tab.addEventListener("click", () => selectTrendMetric(metric.key));
      tab.addEventListener("keydown", onTrendTabKey);
      return tab;
    }));
  }

  function renderTrend(summary) {
    trend.summary = summary;
    trendStatus("ready");
    renderTrendMetrics(trendModel(summary));
    selectTrendMetric(trend.key);
  }
  function renderTrendLoading() { trendStatus("loading"); }
  function renderTrendError(text) { trendStatus("error", text); }

  function storedTrendDays() {
    try {
      const value = Number(window.localStorage.getItem(TREND_STORE));
      return TREND_DAYS.includes(value) ? value : 30;
    } catch (error) { return 30; }
  }
  function markTrendRange() {
    for (const button of $("#home-trend-range").querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.days) === trend.days));
    }
  }
  function setTrendDays(days) {
    if (!TREND_DAYS.includes(days) || days === trend.days) return;
    trend.days = days;
    try { window.localStorage.setItem(TREND_STORE, String(days)); } catch (error) { /* 无存储时只是不记住 */ }
    markTrendRange();
    loadTrend();
  }

  function loadTrend() {
    const epoch = trend.epoch;
    const token = ++trend.seq;
    const path = `/api/stats/summary?days=${trend.days}`;
    const live = () => epoch === trend.epoch && token === trend.seq;
    renderTrendLoading();
    const request = trend.api
      ? trend.api(path)
      : window.fetch(path, { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } })
        .then((response) => (response.ok ? response.json() : Promise.reject(new Error("request failed"))));
    return Promise.resolve(request).then(
      (data) => { if (live()) renderTrend(data); },
      () => { if (live()) renderTrendError("暂时无法读取趋势数据。"); },
    );
  }

  let trendWired = false;
  function wireTrend() {
    if (trendWired) return;
    trendWired = true;
    trend.days = storedTrendDays();
    markTrendRange();
    for (const button of $("#home-trend-range").querySelectorAll("button")) {
      button.addEventListener("click", () => setTrendDays(Number(button.dataset.days)));
    }
    $("#home-trend-retry").addEventListener("click", () => loadTrend());
    if (typeof window.ResizeObserver === "function") {
      new window.ResizeObserver((entries) => {
        const width = Math.round(entries[0]?.contentRect?.width || 0);
        if (width && width !== trend.width) { trend.width = width; if (trend.summary) renderTrendChart(); }
      }).observe($("#home-trend-chart"));
    }
  }

  /* 挂载趋势区：接线（只做一次）并请求；context.api 缺省时退回 fetch。 */
  function mountTrend(context = {}) {
    wireTrend();
    trend.api = context.api || null;
    return loadTrend();
  }

  function resetTrend() {
    trend.epoch += 1;
    trend.seq += 1;
    trend.summary = null;
    trend.api = null;
    $("#home-trend-metrics").replaceChildren();
    $("#home-trend-chart").replaceChildren();
    $("#home-trend-table-body").replaceChildren();
    $("#home-trend-readout").textContent = "";
    $("#home-trend-how").textContent = "";
    trendStatus("loading");
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
    mountTrend(context);
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
    renderTrendError("总览读取失败，趋势暂不可用。");
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
    resetTrend();
  }

  window.Overview = { render, renderError, reset, renderTrend, trendModel, mountTrend };
})();
