"use strict";

/* 总览页渲染（原"学习大厅"）。数据来自 GET /api/overview，由 app.js 的 loadHome() 调用。
   对外契约：window.Overview = { render(data, context), renderError(), reset(), loadTrend(context), renderTrend(summary), trendModel(summary) }。
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
  function renderDue(data, context) {
    const list = $("#ov-due-list");
    const all = $("#ov-due-all");
    all.hidden = data.due_count === 0;
    all.textContent = `查看全部 ${data.due_count} 条 →`;
    $("#ov-due-empty").hidden = data.due_preview.length > 0;
    list.replaceChildren(...window.ProblemCards.group(data.due_preview, data.today).map((group) => {
      const entry = node("li");
      entry.append(window.ProblemCards.card(group, { onOpen: item => context.onOpenRecord?.(item.id) }));
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

  /* ---------- 趋势区：指标条 + 随指标切换的主图 ----------
     数据来自 GET /api/stats/summary?days=N（由 app.js 传入 context.api）。
     trendModel() 是纯函数（文案、涨跌、图数据与刻度），renderTrend() 把它画出来；
     几何（比例尺、留白）只在画图时按容器宽度算，刻度与标签用同一个比例尺。 */
  const TREND_DAYS = [7, 30, 90];
  const TREND_DAYS_KEY = "home-trend-days";
  const TREND_DEFAULT_DAYS = 30;
  const TREND_TARGET_RATE = 0.85;
  const TREND_METRICS = ["due", "streak", "reviews", "retention"];
  const TREND_DIRECTION_GLYPH = { up: "↗", down: "↘", flat: "→", new: "↗" };
  const MINUS = "−";
  const trendState = {
    seq: 0, context: null, days: TREND_DEFAULT_DAYS, metric: "due", summary: null,
    status: "loading", width: 0, observer: null,
  };

  function trendStoredDays() {
    try {
      const value = Number(window.localStorage.getItem(TREND_DAYS_KEY));
      return TREND_DAYS.includes(value) ? value : TREND_DEFAULT_DAYS;
    } catch {
      return TREND_DEFAULT_DAYS;
    }
  }
  function trendStoreDays(days) {
    try {
      window.localStorage.setItem(TREND_DAYS_KEY, String(days));
    } catch {
      // 隐私模式 / 存储被禁：只是不记住选择。
    }
  }

  function shortDate(text) {
    return text.slice(5);
  }
  function roundTo(value, places) {
    const factor = 10 ** places;
    return Math.round(value * factor) / factor;
  }
  function isNumber(value) {
    return typeof value === "number" && Number.isFinite(value);
  }

  /* 计数轴：从 0 起，整数刻度，最多 5 条，顶端刻度 >= 数据最大值。 */
  function countTicks(max) {
    const peak = Math.max(1, Math.ceil(max));
    const steps = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 5000, 10000];
    const step = steps.find((candidate) => Math.ceil(peak / candidate) <= 4)
      || 10 ** Math.ceil(Math.log10(peak / 4));
    const count = Math.ceil(peak / step);
    return { ticks: Array.from({ length: count + 1 }, (_, index) => index * step), top: count * step };
  }
  /* 比例轴：上端固定 100%，下端取能装下全部数据和目标线的整齐刻度。 */
  function rateTicks(rates) {
    const lowest = Math.min(TREND_TARGET_RATE, ...rates);
    for (const step of [0.05, 0.1, 0.2, 0.25, 0.5]) {
      const count = Math.ceil((1 - lowest) / step - 1e-9);
      if (count > 4) continue;
      const bottom = roundTo(1 - count * step, 4);
      if (bottom < 0) continue;
      return { ticks: Array.from({ length: count + 1 }, (_, index) => roundTo(bottom + index * step, 4)), bottom, top: 1 };
    }
    return { ticks: [0, 0.25, 0.5, 0.75, 1], bottom: 0, top: 1 };
  }

  function percentText(rate) {
    return `${Math.round(rate * 100)}%`;
  }
  function changeBadge(current, previous) {
    // 复习次数：与上一等长周期比；上一周期为 0 时百分比没有意义，显示"新"。
    if (current === previous) return { direction: "flat", text: "持平" };
    if (previous === 0) return { direction: "new", text: "新" };
    const percent = Math.round(((current - previous) / previous) * 100);
    if (percent === 0) return { direction: "flat", text: "持平" };
    return percent > 0
      ? { direction: "up", text: `+${percent}%` }
      : { direction: "down", text: `${MINUS}${-percent}%` };
  }
  function pointsBadge(current, previous) {
    if (!isNumber(current) || !isNumber(previous)) return null;
    const points = roundTo((current - previous) * 100, 1);
    if (points === 0) return { direction: "flat", text: "持平" };
    return points > 0
      ? { direction: "up", text: `+${points} 个点` }
      : { direction: "down", text: `${MINUS}${-points} 个点` };
  }
  function badgeSpeech(badge) {
    // 读屏文案：箭头和 +/− 号在视觉上表达方向，这里补一句话。
    if (!badge) return "";
    if (badge.direction === "new") return "较上一周期：此前没有复习记录";
    if (badge.direction === "flat") return "较上一周期持平";
    return `较上一周期${badge.direction === "up" ? "上升" : "下降"} ${badge.text.slice(1)}`;
  }

  function forecastLabel(date, index) {
    return ["今天", "明", "后"][index] || shortDate(date);
  }
  function heatLevel(count, peak) {
    if (count <= 0) return 0;
    return Math.min(4, Math.max(1, Math.ceil((count / peak) * 4)));
  }

  function trendModel(summary) {
    const data = summary || {};
    const days = TREND_DAYS.includes(data.days) ? data.days : TREND_DEFAULT_DAYS;
    const due = data.due || {};
    const dueToday = isNumber(due.today) ? due.today : null;
    const overdue = isNumber(due.overdue) ? due.overdue : 0;
    const streak = isNumber(data.streak_days) ? data.streak_days : null;
    const reviews = data.reviews || {};
    const current = isNumber(reviews.current) ? reviews.current : null;
    const previous = isNumber(reviews.previous) ? reviews.previous : null;
    const retention = data.retention || {};
    const rateNow = isNumber(retention.current?.rate) ? retention.current.rate : null;
    const ratePrev = isNumber(retention.previous?.rate) ? retention.previous.rate : null;
    const forecast = Array.isArray(data.forecast) ? data.forecast : [];
    const daily = Array.isArray(data.daily_reviews) ? data.daily_reviews : [];
    const weekly = Array.isArray(retention.weekly) ? retention.weekly : [];
    const activeDays = daily.filter((item) => item.count > 0).length;

    const reviewsBadge = current !== null && previous !== null ? changeBadge(current, previous) : null;
    const retentionBadge = pointsBadge(rateNow, ratePrev);
    const retentionReviews = retention.current?.reviews ?? 0;

    const metrics = [
      {
        key: "due", label: "待复习",
        value: dueToday === null ? "—" : String(dueToday), unit: dueToday === null ? "" : "条",
        sub: dueToday === null ? "" : (overdue > 0 ? `其中逾期 ${overdue}` : "没有逾期"),
        badge: null, badgeNote: "",
        explain: "今天到期的易错点（含逾期）。",
        how: "待复习 = 到期日不晚于今天的易错点数量，逾期的也算在内。主图按到期日统计未来 14 天每天要复习多少；今天那一格包含全部逾期。",
      },
      {
        key: "streak", label: "连续打卡",
        value: streak === null ? "—" : String(streak), unit: streak === null ? "" : "天",
        sub: daily.length ? `近 ${days} 天有 ${activeDays} 天复习` : "",
        badge: null, badgeNote: "",
        explain: "每天至少复习一次就算打卡。",
        how: "连续打卡 = 从今天（今天还没复习就从昨天）往前，每天至少完成一次复习的连续天数。主图是所选时间范围内每天的复习次数，颜色越深次数越多。",
      },
      {
        key: "reviews", label: `近 ${days} 天复习`,
        value: current === null ? "—" : String(current), unit: current === null ? "" : "次",
        sub: previous === null ? "" : `上一个 ${days} 天：${previous} 次`,
        badge: reviewsBadge,
        badgeNote: badgeSpeech(reviewsBadge),
        explain: `评分次数，对比再往前 ${days} 天。`,
        how: `复习次数 = 所选 ${days} 天内（含今天，按你的本地日）提交的评分条数；涨跌与紧邻的上一个 ${days} 天比较，上一周期为 0 次时只显示“新”。`,
      },
      {
        key: "retention", label: "真实保持率",
        value: rateNow === null ? "—" : percentText(rateNow), unit: "",
        sub: rateNow === null ? "" : `${retention.current.passed}/${retentionReviews} 次想起来了`,
        badge: retentionBadge,
        badgeNote: badgeSpeech(retentionBadge),
        explain: rateNow === null ? "需要至少一次非首次复习。" : "非首次复习里，评分 ≥ 3 的占比。",
        how: "真实保持率 = 所选时间范围内的非首次复习（该易错点之前已经复习过）中，评分 ≥ 3 的占比。首次复习不算，因为还谈不上“保持”；没有符合条件的复习时显示“—”。它用来检查复习安排是否有效，85% 左右是常用的参考线。",
      },
    ];

    const forecastBars = forecast.map((item, index) => ({
      date: item.date, label: forecastLabel(item.date, index), value: item.due, today: index === 0,
    }));
    const forecastMax = forecastBars.reduce((peak, bar) => Math.max(peak, bar.value), 0);
    const reviewPeak = daily.reduce((peak, item) => Math.max(peak, item.count), 0);
    const dailyPoints = daily.map((item) => ({ date: item.date, value: item.count, level: heatLevel(item.count, reviewPeak) }));
    const weeklyPoints = weekly.map((week) => ({
      weekStart: week.week_start, label: shortDate(week.week_start), reviews: week.reviews, passed: week.passed,
      rate: isNumber(week.rate) ? week.rate : null,
    }));
    const rates = weeklyPoints.filter((point) => point.rate !== null).map((point) => point.rate);

    const charts = {
      due: {
        kind: "bars", bars: forecastBars, max: forecastMax, ...countTicks(forecastMax),
        overdue, empty: forecastBars.length === 0 ? "暂时没有预测数据。"
          : (forecastMax === 0 ? "未来 14 天没有到期的易错点。" : ""),
      },
      streak: {
        kind: "heat", cells: dailyPoints, peak: reviewPeak,
        empty: dailyPoints.length === 0 ? "暂时没有复习记录数据。"
          : (reviewPeak === 0 ? `近 ${days} 天还没有复习记录。` : ""),
      },
      reviews: {
        kind: "area", points: dailyPoints, max: reviewPeak, ...countTicks(reviewPeak),
        empty: dailyPoints.length === 0 ? "暂时没有复习记录数据。"
          : (reviewPeak === 0 ? `近 ${days} 天还没有复习记录。` : ""),
      },
      retention: {
        kind: "line", points: weeklyPoints, target: TREND_TARGET_RATE,
        ...(rates.length ? rateTicks(rates) : { ticks: [], bottom: 0, top: 1 }),
        empty: rates.length === 0 ? "还没有保持率：需要至少一次非首次复习（同一条易错点复习第二次起才算）。" : "",
      },
    };
    return { days, metrics, charts, today: data.today || "" };
  }

  /* ---------- 画图 ---------- */
  const CHART = {
    bars: { left: 34, right: 12, top: 40, bottom: 28, height: 232 },
    area: { left: 34, right: 16, top: 16, bottom: 28, height: 220 },
    line: { left: 40, right: 20, top: 24, bottom: 28, height: 232 },
    heat: { left: 8, right: 8, top: 8, bottom: 28 },
  };
  function chartSvg(width, height, label) {
    const svg = svgNode("svg", {
      class: "ov-chart", viewBox: `0 0 ${width} ${height}`, width: String(width), height: String(height),
      role: "img", "aria-label": label, focusable: "false",
    });
    return svg;
  }
  function svgText(x, y, text, className, anchor) {
    const item = svgNode("text", { x: String(roundTo(x, 2)), y: String(roundTo(y, 2)), class: className, "text-anchor": anchor || "middle" });
    item.textContent = text;
    return item;
  }
  function plotBox(width, spec) {
    return { x0: spec.left, x1: width - spec.right, y0: spec.top, y1: spec.height - spec.bottom };
  }
  function gridLines(svg, box, scaleY, ticks, formatter) {
    for (const tick of ticks) {
      const y = roundTo(scaleY(tick), 2);
      svg.append(svgNode("line", { class: "ov-chart-grid", x1: String(box.x0), x2: String(box.x1), y1: String(y), y2: String(y) }));
      svg.append(svgText(box.x0 - 6, y + 3.5, formatter(tick), "ov-chart-tick", "end"));
    }
  }

  function drawBars(chart, width) {
    const spec = CHART.bars;
    const height = spec.height;
    const box = plotBox(width, spec);
    const svg = chartSvg(width, height, `未来 ${chart.bars.length} 天每天到期的易错点数量，今天含逾期 ${chart.bars[0].value} 条`);
    const scaleY = (value) => box.y1 - (value / chart.top) * (box.y1 - box.y0);
    gridLines(svg, box, scaleY, chart.ticks, String);
    const pitch = (box.x1 - box.x0) / chart.bars.length;
    const barWidth = Math.max(6, Math.min(34, pitch * 0.62));
    const labelEvery = pitch >= 38 ? 1 : (pitch >= 24 ? 2 : 3);
    chart.bars.forEach((bar, index) => {
      const centre = box.x0 + pitch * (index + 0.5);
      const barHeight = bar.value > 0 ? box.y1 - scaleY(bar.value) : 0;
      const rect = svgNode("rect", {
        class: `ov-bar-rect${bar.today ? " is-today" : ""}${bar.value === 0 ? " is-zero" : ""}`,
        x: String(roundTo(centre - barWidth / 2, 2)),
        y: String(roundTo(bar.value > 0 ? scaleY(bar.value) : box.y1 - 2, 2)),
        width: String(roundTo(barWidth, 2)),
        height: String(roundTo(bar.value > 0 ? barHeight : 2, 2)),
        rx: "2",
      });
      const title = svgNode("title", {});
      title.textContent = `${bar.today ? "今天（含逾期）" : bar.date}：${bar.value} 条`;
      rect.append(title);
      svg.append(rect);
      const top = bar.value > 0 ? scaleY(bar.value) : box.y1 - 2;
      svg.append(svgText(centre, top - 5, String(bar.value), `ov-chart-value${bar.today ? " is-today" : ""}`));
      if (bar.today) svg.append(svgText(centre, top - 18, "含逾期", "ov-chart-note", index === 0 ? "start" : "middle"));
      if (index % labelEvery === 0 || bar.today) {
        svg.append(svgText(centre, height - 8, bar.label, `ov-chart-axis${bar.today ? " is-today" : ""}`));
      }
    });
    return svg;
  }

  function drawHeat(chart, width, today) {
    // 一条按时间从左到右、放不下就折行的小方块条：每格一天，颜色深浅 = 当天复习次数。
    const spec = CHART.heat;
    const cells = chart.cells;
    const gap = 3;
    const room = width - spec.left - spec.right;
    const target = width < 500 ? 16 : 26;
    const columns = Math.max(1, Math.min(cells.length, Math.floor((room + gap) / (target + gap))));
    const cell = Math.max(10, Math.min(26, Math.floor((room - gap * (columns - 1)) / columns)));
    const rows = Math.ceil(cells.length / columns);
    const height = spec.top + rows * cell + (rows - 1) * gap + spec.bottom;
    const svg = chartSvg(width, height, `近 ${cells.length} 天每天的复习次数，颜色越深次数越多`);
    cells.forEach((item, index) => {
      const rect = svgNode("rect", {
        class: `ov-heat-cell level-${item.level}`,
        x: String(spec.left + (index % columns) * (cell + gap)), y: String(spec.top + Math.floor(index / columns) * (cell + gap)),
        width: String(cell), height: String(cell), rx: "3",
      });
      const title = svgNode("title", {});
      title.textContent = `${item.date}：复习 ${item.value} 次`;
      rect.append(title);
      svg.append(rect);
    });
    const last = cells[cells.length - 1].date;
    svg.append(svgText(spec.left, height - 8, `${shortDate(cells[0].date)} 起`, "ov-chart-axis", "start"));
    svg.append(svgText(width - spec.right, height - 8, last === today ? "今天" : shortDate(last), "ov-chart-axis is-today", "end"));
    return svg;
  }

  function axisIndexes(count, wanted) {
    if (count <= wanted) return Array.from({ length: count }, (_, index) => index);
    const picked = new Set();
    for (let step = 0; step < wanted; step += 1) picked.add(Math.round((step * (count - 1)) / (wanted - 1)));
    return [...picked];
  }

  function drawArea(chart, width, today) {
    const spec = CHART.area;
    const box = plotBox(width, spec);
    const points = chart.points;
    const svg = chartSvg(width, spec.height, `近 ${points.length} 天每天的复习次数，最多 ${chart.max} 次`);
    const scaleY = (value) => box.y1 - (value / chart.top) * (box.y1 - box.y0);
    const scaleX = (index) => (points.length === 1 ? (box.x0 + box.x1) / 2 : box.x0 + (index / (points.length - 1)) * (box.x1 - box.x0));
    gridLines(svg, box, scaleY, chart.ticks, String);
    const coordinates = points.map((point, index) => `${roundTo(scaleX(index), 2)},${roundTo(scaleY(point.value), 2)}`);
    svg.append(svgNode("path", {
      class: "ov-area-fill",
      d: `M${roundTo(scaleX(0), 2)},${box.y1} L${coordinates.join(" L")} L${roundTo(scaleX(points.length - 1), 2)},${box.y1} Z`,
    }));
    svg.append(svgNode("path", { class: "ov-area-line", d: `M${coordinates.join(" L")}` }));
    for (const index of axisIndexes(points.length, width < 420 ? 3 : 5)) {
      const last = index === points.length - 1;
      const anchor = index === 0 ? "start" : (last ? "end" : "middle");
      svg.append(svgText(scaleX(index), spec.height - 8, last && points[index].date === today ? "今天" : shortDate(points[index].date), `ov-chart-axis${last ? " is-today" : ""}`, anchor));
    }
    const end = points.length - 1;
    svg.append(svgNode("circle", { class: "ov-area-end", cx: String(roundTo(scaleX(end), 2)), cy: String(roundTo(scaleY(points[end].value), 2)), r: "4.5" }));
    attachHover(svg, box, points, scaleX, scaleY, width);
    return svg;
  }

  /* 鼠标 / 触摸悬停：竖线 + 圆点 + 当天数值。 */
  function attachHover(svg, box, points, scaleX, scaleY, width) {
    const guide = svgNode("g", { class: "ov-hover" });
    const line = svgNode("line", { class: "ov-hover-line", y1: String(box.y0), y2: String(box.y1) });
    const dot = svgNode("circle", { class: "ov-hover-dot", r: "4" });
    const tipBox = svgNode("rect", { class: "ov-hover-box", width: "92", height: "22", rx: "4" });
    const tipText = svgText(0, 0, "", "ov-hover-text", "middle");
    guide.append(line, dot, tipBox, tipText);
    const overlay = svgNode("rect", {
      class: "ov-hover-overlay", x: String(box.x0), y: String(box.y0),
      width: String(box.x1 - box.x0), height: String(box.y1 - box.y0),
    });
    const show = (event) => {
      const rect = svg.getBoundingClientRect();
      const ratio = rect.width > 0 ? width / rect.width : 1;
      const x = (event.clientX - rect.left) * ratio;
      const span = box.x1 - box.x0;
      const index = points.length === 1 ? 0 : Math.max(0, Math.min(points.length - 1, Math.round(((x - box.x0) / span) * (points.length - 1))));
      const point = points[index];
      const px = scaleX(index);
      const py = scaleY(point.value);
      line.setAttribute("x1", String(roundTo(px, 2)));
      line.setAttribute("x2", String(roundTo(px, 2)));
      dot.setAttribute("cx", String(roundTo(px, 2)));
      dot.setAttribute("cy", String(roundTo(py, 2)));
      const left = Math.max(2, Math.min(width - 94, px - 46));
      const top = py - 34 < box.y0 - 10 ? py + 10 : py - 34;
      tipBox.setAttribute("x", String(roundTo(left, 2)));
      tipBox.setAttribute("y", String(roundTo(top, 2)));
      tipText.setAttribute("x", String(roundTo(left + 46, 2)));
      tipText.setAttribute("y", String(roundTo(top + 15, 2)));
      tipText.textContent = `${shortDate(point.date)} · ${point.value} 次`;
      guide.classList.add("is-on");
    };
    const hide = () => guide.classList.remove("is-on");
    overlay.addEventListener("pointermove", show);
    overlay.addEventListener("pointerdown", show);
    overlay.addEventListener("pointerleave", hide);
    overlay.addEventListener("pointercancel", hide);
    svg.append(guide, overlay);
  }

  function drawLine(chart, width) {
    const spec = CHART.line;
    const box = plotBox(width, spec);
    const points = chart.points;
    const span = chart.top - chart.bottom;
    const svg = chartSvg(width, spec.height, `最近 ${points.length} 周的真实保持率，虚线是 ${percentText(chart.target)} 的目标线`);
    const scaleY = (value) => box.y1 - ((value - chart.bottom) / span) * (box.y1 - box.y0);
    const scaleX = (index) => (points.length === 1 ? (box.x0 + box.x1) / 2 : box.x0 + (index / (points.length - 1)) * (box.x1 - box.x0));
    gridLines(svg, box, scaleY, chart.ticks, percentText);
    const targetY = roundTo(scaleY(chart.target), 2);
    svg.append(svgNode("line", { class: "ov-chart-target", x1: String(box.x0), x2: String(box.x1), y1: String(targetY), y2: String(targetY) }));
    svg.append(svgText(box.x1, targetY - 5, `目标线 ${percentText(chart.target)}`, "ov-chart-note", "end"));
    // rate 为 null 的周断线：每一段连续有数据的周单独画一条折线。
    let run = [];
    const flush = () => {
      if (run.length > 1) svg.append(svgNode("path", { class: "ov-trend-line", d: `M${run.join(" L")}` }));
      run = [];
    };
    points.forEach((point, index) => {
      if (point.rate === null) flush();
      else run.push(`${roundTo(scaleX(index), 2)},${roundTo(scaleY(point.rate), 2)}`);
    });
    flush();
    const labelEvery = (box.x1 - box.x0) / Math.max(1, points.length - 1) >= 44 ? 1 : 2;
    points.forEach((point, index) => {
      const x = scaleX(index);
      const last = index === points.length - 1;
      if (point.rate !== null) {
        const dot = svgNode("circle", { class: `ov-trend-dot${last ? " is-last" : ""}`, cx: String(roundTo(x, 2)), cy: String(roundTo(scaleY(point.rate), 2)), r: last ? "4.5" : "3.5" });
        const title = svgNode("title", {});
        title.textContent = `${point.weekStart} 起的一周：${percentText(point.rate)}（${point.passed}/${point.reviews}）`;
        dot.append(title);
        svg.append(dot);
        if ((points.length - 1 - index) % labelEvery === 0) svg.append(svgText(x, scaleY(point.rate) - 9, percentText(point.rate), "ov-chart-value", "middle"));
      }
      if ((points.length - 1 - index) % labelEvery === 0) {
        svg.append(svgText(x, spec.height - 8, last ? "本周" : point.label, `ov-chart-axis${last ? " is-today" : ""}`, "middle"));
      }
    });
    return svg;
  }

  /* ---------- 数据表（可访问的 <table>） ---------- */
  function dataTable(model, key) {
    const table = node("table", "ov-trend-table");
    const chart = model.charts[key];
    const metric = model.metrics.find((item) => item.key === key);
    table.append(node("caption", "", `${metric.label}（${key === "due" ? "未来 14 天" : key === "retention" ? "最近 8 周" : `近 ${model.days} 天`}）`));
    const headings = {
      due: ["日期", "到期（今天含逾期）"],
      streak: ["日期", "复习次数"],
      reviews: ["日期", "复习次数"],
      retention: ["周起始日", "非首次复习", "评分 ≥ 3", "保持率"],
    }[key];
    const headRow = node("tr");
    for (const text of headings) {
      const cell = node("th", "", text);
      cell.setAttribute("scope", "col");
      headRow.append(cell);
    }
    const head = node("thead");
    head.append(headRow);
    const body = node("tbody");
    const rows = key === "due" ? chart.bars.map((bar) => [bar.today ? `${bar.date}（今天）` : bar.date, String(bar.value)])
      : key === "retention" ? chart.points.map((point) => [point.weekStart, String(point.reviews), String(point.passed), point.rate === null ? "—" : percentText(point.rate)])
        : (chart.cells || chart.points).map((point) => [point.date, String(point.value)]);
    for (const row of rows) {
      const tr = node("tr");
      row.forEach((text, index) => tr.append(node(index === 0 ? "th" : "td", "", text)));
      if (tr.children[0].tagName === "TH") tr.children[0].setAttribute("scope", "row");
      body.append(tr);
    }
    table.append(head, body);
    return table;
  }

  /* ---------- 外壳（只建一次）与各个状态 ---------- */
  function trendRoot() {
    return $("#home-trend");
  }
  function buildTrendShell(root) {
    if (root.dataset.built === "1") return;
    root.dataset.built = "1";
    const head = node("div", "ov-trend-head");
    const title = node("h3", "", "趋势");
    title.id = "home-trend-title";
    const range = node("div", "ov-trend-range");
    range.id = "home-trend-range";
    range.setAttribute("role", "group");
    range.setAttribute("aria-label", "时间范围");
    for (const days of TREND_DAYS) {
      const button = node("button", "ov-trend-range-button", `${days} 天`);
      button.type = "button";
      button.dataset.days = String(days);
      button.addEventListener("click", () => chooseTrendDays(days));
      range.append(button);
    }
    head.append(title, range);

    const error = node("p", "ov-trend-error");
    error.id = "home-trend-error";
    error.setAttribute("role", "alert");
    error.hidden = true;
    const retry = node("button", "ov-link", "重试");
    retry.type = "button";
    retry.id = "home-trend-retry";
    retry.addEventListener("click", () => fetchTrend());
    error.append(node("span", "", "趋势数据暂时无法读取。"), retry);

    const tabs = node("div", "ov-trend-tabs");
    tabs.id = "home-trend-tabs";
    tabs.setAttribute("role", "tablist");
    tabs.setAttribute("aria-label", "趋势指标");
    tabs.addEventListener("keydown", onTrendKey);

    const panel = node("div", "ov-trend-panel");
    panel.id = "home-trend-panel";
    panel.setAttribute("role", "tabpanel");
    const chart = node("div", "ov-trend-chart");
    chart.id = "home-trend-chart";
    const note = node("p", "ov-trend-note");
    note.id = "home-trend-note";
    const how = node("details", "ov-trend-how");
    how.id = "home-trend-how";
    how.append(node("summary", "", "怎么算"), node("p", ""));
    const data = node("details", "ov-trend-data");
    data.id = "home-trend-data";
    data.append(node("summary", "", "查看数据表"), node("div", "ov-trend-table-wrap"));
    panel.append(chart, note, how, data);
    root.append(head, error, tabs, panel);
    root.setAttribute("aria-labelledby", "home-trend-title");

    if (typeof window.ResizeObserver === "function") {
      trendState.observer = new window.ResizeObserver(() => {
        const width = trendChartWidth();
        if (trendState.status === "ready" && width !== trendState.width) drawTrendChart();
      });
      trendState.observer.observe(chart);
    }
  }
  function trendChartWidth() {
    const width = Math.round($("#home-trend-chart")?.clientWidth || 0);
    return width >= 240 ? Math.min(width, 960) : (width > 0 ? 240 : 640);
  }
  function syncTrendRange() {
    for (const button of $("#home-trend-range").querySelectorAll("button")) {
      button.setAttribute("aria-pressed", String(Number(button.dataset.days) === trendState.days));
    }
  }
  function setTrendStatus(status) {
    trendState.status = status;
    const root = trendRoot();
    root.dataset.state = status;
    root.setAttribute("aria-busy", String(status === "loading"));
    $("#home-trend-error").hidden = status !== "error";
    $("#home-trend-panel").hidden = status === "error";
    $("#home-trend-tabs").hidden = status === "error";
  }

  function renderTrendLoading() {
    const root = trendRoot();
    if (!root) return;
    buildTrendShell(root);
    syncTrendRange();
    setTrendStatus("loading");
    const tabs = $("#home-trend-tabs");
    tabs.removeAttribute("aria-activedescendant");
    tabs.replaceChildren(...TREND_METRICS.map(() => {
      const placeholder = node("div", "ov-trend-tab is-skeleton");
      placeholder.setAttribute("aria-hidden", "true");
      return placeholder;
    }));
    $("#home-trend-chart").replaceChildren(node("div", "ov-trend-skeleton"));
    $("#home-trend-note").textContent = "";
    $("#home-trend-how").hidden = true;
    $("#home-trend-data").hidden = true;
  }
  function renderTrendError() {
    const root = trendRoot();
    if (!root) return;
    buildTrendShell(root);
    syncTrendRange();
    setTrendStatus("error");
  }

  function renderTrendTabs(model) {
    const tabs = $("#home-trend-tabs");
    tabs.replaceChildren(...model.metrics.map((metric) => {
      const selected = metric.key === trendState.metric;
      const tab = node("button", "ov-trend-tab");
      tab.type = "button";
      tab.id = `home-trend-tab-${metric.key}`;
      tab.dataset.metric = metric.key;
      tab.setAttribute("role", "tab");
      tab.setAttribute("aria-selected", String(selected));
      tab.setAttribute("aria-controls", "home-trend-panel");
      tab.tabIndex = selected ? 0 : -1;
      const value = node("span", "ov-trend-value");
      value.append(node("strong", "", metric.value));
      if (metric.unit) value.append(node("small", "", metric.unit));
      const line = node("span", "ov-trend-line-row");
      if (metric.badge) {
        const badge = node("span", `ov-trend-delta is-${metric.badge.direction}`);
        badge.append(node("span", "", TREND_DIRECTION_GLYPH[metric.badge.direction]), node("span", "", metric.badge.text));
        badge.firstChild.setAttribute("aria-hidden", "true");
        line.append(badge);
        if (metric.badgeNote) line.append(node("span", "ov-sr-only", metric.badgeNote));
      }
      if (metric.sub) line.append(node("span", "ov-trend-sub", metric.sub));
      tab.append(node("span", "ov-trend-label", metric.label), value, line, node("span", "ov-trend-explain", metric.explain));
      tab.addEventListener("click", () => selectTrendMetric(metric.key));
      return tab;
    }));
    $("#home-trend-panel").setAttribute("aria-labelledby", `home-trend-tab-${trendState.metric}`);
  }

  function drawTrendChart() {
    if (!trendState.summary) return;
    const model = trendModel(trendState.summary);
    const key = trendState.metric;
    const chart = model.charts[key];
    const mount = $("#home-trend-chart");
    const metric = model.metrics.find((item) => item.key === key);
    const how = $("#home-trend-how");
    how.hidden = false;
    how.querySelector("p").textContent = metric.how;
    const data = $("#home-trend-data");
    const note = $("#home-trend-note");
    if (chart.empty) {
      mount.replaceChildren(node("p", "ov-trend-empty", chart.empty));
      note.textContent = "";
      data.hidden = true;
      return;
    }
    const width = trendChartWidth();
    trendState.width = width;
    const svg = key === "due" ? drawBars(chart, width)
      : key === "streak" ? drawHeat(chart, width, model.today)
        : key === "reviews" ? drawArea(chart, width, model.today)
          : drawLine(chart, width);
    mount.replaceChildren(svg);
    note.textContent = key === "due"
      ? `今天 ${chart.bars[0].value} 条，其中逾期 ${chart.overdue} 条；其余每天按到期日统计。`
      : key === "retention" ? `虚线为 ${percentText(TREND_TARGET_RATE)} 的目标线；没有非首次复习的一周不连线。` : "";
    data.hidden = false;
    data.querySelector(".ov-trend-table-wrap").replaceChildren(dataTable(model, key));
  }

  function renderTrend(summary) {
    const root = trendRoot();
    if (!root) return;
    buildTrendShell(root);
    trendState.summary = summary;
    if (TREND_DAYS.includes(summary?.days)) trendState.days = summary.days;
    syncTrendRange();
    setTrendStatus("ready");
    renderTrendTabs(trendModel(summary));
    drawTrendChart();
  }

  function selectTrendMetric(key, { focus = false } = {}) {
    if (!TREND_METRICS.includes(key) || trendState.status !== "ready") return;
    trendState.metric = key;
    const tabs = $("#home-trend-tabs");
    for (const tab of tabs.querySelectorAll("[role=tab]")) {
      const selected = tab.dataset.metric === key;
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
      if (selected && focus) tab.focus();
    }
    $("#home-trend-panel").setAttribute("aria-labelledby", `home-trend-tab-${key}`);
    drawTrendChart();
  }
  function onTrendKey(event) {
    const index = TREND_METRICS.indexOf(trendState.metric);
    let next = null;
    if (event.key === "ArrowRight") next = (index + 1) % TREND_METRICS.length;
    else if (event.key === "ArrowLeft") next = (index + TREND_METRICS.length - 1) % TREND_METRICS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TREND_METRICS.length - 1;
    if (next === null) return;
    event.preventDefault();
    selectTrendMetric(TREND_METRICS[next], { focus: true });
  }

  /* ---------- 请求 ---------- */
  async function fetchTrend() {
    const { context } = trendState;
    if (!context) return;
    trendState.seq += 1;
    const seq = trendState.seq;
    const days = trendState.days;
    renderTrendLoading();
    // 迟到的响应（又发了新请求、重置、登出再登录别的账号、已离开总览页）一律丢弃。
    const current = () => seq === trendState.seq && (context.isCurrent ? context.isCurrent() : true);
    try {
      const summary = await context.api(`/api/stats/summary?days=${days}`);
      if (!current()) return;
      renderTrend(summary);
    } catch {
      if (!current()) return;
      renderTrendError();
    }
  }
  function chooseTrendDays(days) {
    if (!TREND_DAYS.includes(days) || (days === trendState.days && trendState.status !== "error")) return;
    trendState.days = days;
    trendStoreDays(days);
    return fetchTrend();
  }
  /* 进入总览页：context = { api(path), isCurrent() }。 */
  function loadTrend(context) {
    trendState.context = context;
    trendState.days = trendStoredDays();
    return fetchTrend();
  }
  function resetTrend() {
    trendState.seq += 1;
    trendState.context = null;
    trendState.summary = null;
    renderTrendLoading();
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
    resetTrend();
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

  window.Overview = { render, renderError, reset, renderTrend, trendModel, loadTrend };
})();
