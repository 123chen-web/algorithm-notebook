// static/app/overview-insights.js — 总览·洞察：每周回顾/薄弱点分析/成长洞察。
// 由 static/app/overview.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

let weaknessAnalysis = null;
let weaknessGeneration = 0;
let weaknessPending = false;
let weaknessQuotaAvailable = false;

let weeklyRecapGeneration = 0;
let growthZones = null;

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

function resetGrowthInsights() {
  growthZones = null;
  $("#growth-zones").replaceChildren();
  $("#growth-summary").hidden = true;
  $("#growth-empty").hidden = true;
  $("#growth-empty").textContent = "";
  const communityNote = $("#growth-community-note");
  if (communityNote) {
    communityNote.hidden = true;
    communityNote.textContent = "";
  }
}

function resetWeaknessAnalysis() {
  weaknessGeneration += 1;
  window.PracticeNow?.reset();
  weaknessAnalysis = null;
  weaknessPending = false;
  weaknessQuotaAvailable = false;
  resetGrowthInsights();
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
  const countText = Number.isInteger(count)
    ? insufficient
      ? `已积累 ${count} 条易错点，再记录 ${minimum - count} 条就可以开始分析。`
      : `已积累 ${count} 条易错点，可以开始分析。`
    : "先积累至少 5 条易错点，让分析有足够的线索。";
  const countNode = $("#weakness-count");
  const readiness = $("#weakness-readiness");
  countNode.replaceChildren();
  readiness.replaceChildren();
  if (Number.isInteger(count)) {
    const visual = element("span", "", "weakness-count-visual");
    visual.setAttribute("aria-hidden", "true");
    visual.append(
      element("span", "已积累", "weakness-count-label"),
      element("span", String(count), "weakness-count-number"),
      element("span", "条易错点", "weakness-count-unit")
    );
    countNode.append(element("span", countText, "weakness-sr-only"), visual);
    if (insufficient) {
      const progress = element("span", "", "weakness-progress-track");
      progress.setAttribute("aria-hidden", "true");
      const fill = element("span", "", "weakness-progress-fill");
      fill.style.setProperty("--weakness-progress", String(Math.max(0, count / minimum)));
      progress.append(fill);
      readiness.append(progress, element("span", `${count} / ${minimum} · 再记录 ${minimum - count} 条`, "weakness-progress-label"));
    } else {
      readiness.append(element("span", "可以开始分析", "weakness-ready"));
    }
  } else {
    countNode.append(element("span", countText, "weakness-count-prompt"));
  }
  const remaining = user?.ai_daily_remaining;
  const limit = user?.ai_daily_limit;
  const quotaKnown = weaknessQuotaAvailable && Number.isInteger(remaining) && Number.isInteger(limit);
  $("#weakness-quota").textContent = quotaKnown
    ? `今日 AI 额度剩余 ${remaining} / ${limit} 次${remaining === 0 ? "，明天可再次分析，或前往“我的套餐”查看额度。" : "。"}`
    : weaknessPending ? "正在读取今日 AI 额度…" : "暂时无法读取剩余额度，分析前会由服务端检查额度。";
  const quotaMeter = $("#weakness-quota-meter");
  quotaMeter.replaceChildren();
  quotaMeter.hidden = !quotaKnown;
  if (quotaKnown) {
    const filled = limit > 0 ? Math.max(0, Math.min(1, remaining / limit)) * 10 : 0;
    for (let index = 0; index < 10; index += 1) {
      const dot = element("span", "", "weakness-quota-dot");
      const fill = element("span", "", "weakness-quota-dot-fill");
      fill.style.setProperty("--weakness-quota-fill", String(Math.max(0, Math.min(1, filled - index))));
      dot.append(fill);
      quotaMeter.append(dot);
    }
  }
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

function renderErrorProfile(profile) {
  const host = $("#weakness-profile");
  host.replaceChildren();
  if (!profile || !profile.total_tagged) {
    host.hidden = true;
    return;
  }
  host.hidden = false;
  // 大类统计句：「你的错因里，想错了 X% · 写错了 Y% · 跑得慢 Z%」
  const byCode = Object.fromEntries(profile.groups.map((group) => [group.code, group]));
  const sentence = element("p", "", "weakness-profile-sentence");
  sentence.append(
    document.createTextNode("你的错因里，"),
    element("strong", `想错了 ${byCode.reasoning?.percent ?? 0}%`),
    document.createTextNode(" · "),
    element("strong", `写错了 ${byCode.implementation?.percent ?? 0}%`),
    document.createTextNode(" · "),
    element("strong", `跑得慢 ${byCode.performance?.percent ?? 0}%`),
  );
  host.append(sentence);

  // 判题类型分布（横向条）。
  if (profile.verdicts.length) {
    host.append(element("h4", "判题类型分布", "weakness-profile-title"));
    const bars = element("ul", "", "weakness-bars");
    const max = Math.max(...profile.verdicts.map((item) => item.count));
    for (const item of profile.verdicts) {
      const entry = element("li", "", "weakness-bar-row");
      entry.append(element("span", `${item.code} · ${item.label}`, "weakness-bar-label"));
      const track = element("span", "", "weakness-bar-track");
      const fill = element("span", "", "weakness-bar-fill");
      fill.style.width = `${Math.max(2, Math.round((item.count / max) * 100))}%`;
      track.append(fill);
      entry.append(track, element("span", `${item.count} · ${item.percent}%`, "weakness-bar-value"));
      bars.append(entry);
    }
    host.append(bars);
  }

  // 最常见错因 Top 3。
  if (profile.top_causes.length) {
    host.append(element("h4", "最常见的错因", "weakness-profile-title"));
    const top = element("ol", "", "weakness-top-causes");
    profile.top_causes.forEach((item, index) => {
      const entry = element("li");
      const seal = element("span", String(index + 1), "weakness-rank-seal");
      seal.setAttribute("aria-hidden", "true");
      entry.append(
        seal,
        element("span", item.label, "weakness-cause-label"),
        element("span", `${item.count} 条`, "weakness-cause-count")
      );
      top.append(entry);
    });
    host.append(top);
  }
}

function renderWeaknessAnalysis() {
  const insight = weaknessAnalysis?.insight;
  const result = $("#weakness-result");
  result.replaceChildren();
  result.hidden = !insight;
  $("#weakness-empty").hidden = Boolean(insight);
  $("#weakness-empty-text").textContent = weaknessAnalysis?.message
    || "还没有分析过。点击分析按钮，把积累的错因和复习评分连起来，看看哪些问题值得先解决。";
  const weaknessShort = !insight && Number.isInteger(weaknessAnalysis?.mistake_count)
    && weaknessAnalysis.mistake_count < weaknessAnalysis.minimum_mistakes;
  window.Onboarding?.emptyNext("weakness", $("#weakness-empty"), weaknessShort
    ? { count: weaknessAnalysis.mistake_count, minimum: weaknessAnalysis.minimum_mistakes, total: weaknessAnalysis.mistake_count } : null);
  renderWeaknessControls();
  if (!insight) return;

  const { content } = insight;
  const overview = element("section", "", "panel weakness-overview weakness-reveal");
  overview.style.setProperty("--weakness-order", "0");
  overview.append(
    element("p", `最近一次分析 · 更新于 ${timestamp(insight.created_at)}`, "eyebrow weakness-eyebrow"),
    element("h3", content.patterns.length ? "值得优先关注的规律" : "目前的记录还不足以确认重复规律"),
    element("p", content.summary, "multiline weakness-summary")
  );
  const sample = content.sample;
  const sampleStats = element("dl", "", "weakness-sample");
  sampleStats.setAttribute("aria-label", "本次分析依据");
  for (const [amount, label] of [
    [sample.problem_count, "道题"],
    [sample.mistake_count, "条易错点"],
    [sample.review_count, "次复习评分"],
  ]) {
    const stat = element("div", "", "weakness-stat");
    stat.append(element("dt", label), element("dd", String(amount), "weakness-stat-number"));
    sampleStats.append(stat);
  }
  if (weaknessAnalysis.status === "insufficient_data") {
    overview.append(element("p", "以下是上次保存的分析；当前易错点数量不足，暂时无法更新。", "muted"));
  }
  overview.append(sampleStats);
  const rangeText = sample.period_start && sample.period_end
    ? `题目记录范围：${timestamp(sample.period_start)} 至 ${timestamp(sample.period_end)} · 只保留最近一次分析`
    : "只保留最近一次分析";
  overview.append(element("p", rangeText, "muted weakness-range"));
  result.append(overview);
  const patterns = element("ol", "", "weakness-patterns");
  patterns.setAttribute("role", "list");
  content.patterns.forEach((pattern, index) => {
    const item = element("li");
    const card = element("article", "", "panel weakness-pattern weakness-reveal");
    card.style.setProperty("--weakness-order", String(index + 1));
    const number = element("span", String(index + 1).padStart(2, "0"), "weakness-pattern-number");
    number.setAttribute("aria-hidden", "true");
    const heading = element("div", "", "weakness-pattern-heading");
    const title = element("h3");
    title.append(
      element("span", `第 ${index + 1} 条规律：`, "weakness-sr-only"),
      document.createTextNode(pattern.title)
    );
    const confidence = element("span", pattern.confidence, "weakness-confidence");
    if (pattern.confidence === "较明确") confidence.classList.add("weakness-confidence-clear");
    heading.append(
      title,
      confidence
    );
    card.append(number, heading, element("p", pattern.explanation, "multiline weakness-explanation"));
    const evidence = element("ul", "", "weakness-evidence");
    evidence.setAttribute("role", "list");
    const metadata = new Map((weaknessAnalysis.problem_cards || []).map(item => [item.problem_id, item]));
    for (const group of window.ProblemCards.group(pattern.evidence.map(item => ({ ...item, ...metadata.get(item.problem_id) })), user.today)) {
      const entry = element("li");
      entry.append(window.ProblemCards.card(group, { onOpen: item => run(async () => {
        await showView("all");
        await openMistake(item.mistake_id);
      }) }));
      // 同题证据保留所有观察，折叠在题卡之下，不重复题名。
      const observations = element("details", "", "problem-evidence-observations");
      observations.append(element("summary", `${group.members.length} 条分析依据`));
      for (const member of group.members) observations.append(element("p", member.observation, "multiline"));
      entry.append(observations);
      evidence.append(entry);
    }
    card.append(element("h4", "哪些记录支持这个判断"), evidence);
    const action = element("div", "", "weakness-action");
    action.append(element("h4", "下一步可以这样练"), element("p", pattern.action, "multiline"));
    card.append(action);
    const patternZones = [...new Set(pattern.evidence.map((entry) => entry.zone))];
    const patternPractice = window.PracticeNow?.slot({
      ids: pattern.evidence.map((entry) => entry.mistake_id),
      emptyText: "这一块今天没有要复习的",
      link: { zone: patternZones.length === 1 ? patternZones[0] : "" },
    });
    if (patternPractice) card.append(patternPractice);
    item.append(card);
    patterns.append(item);
  });
  if (content.patterns.length) result.append(patterns);
  const note = element("p", "分析是基于当前样本的学习建议。继续记录具体错因、如实复习评分，下次更新时再验证这些判断。", "muted weakness-report-note weakness-reveal");
  note.style.setProperty("--weakness-order", String(content.patterns.length + 1));
  result.append(note);
}

async function loadWeaknessAnalysis() {
  const generation = ++weaknessGeneration;
  const userId = user.id;
  weaknessPending = true;
  weaknessQuotaAvailable = false;
  renderWeaknessControls();
  resetGrowthInsights();
  $("#growth-empty").hidden = false;
  $("#growth-empty").textContent = "正在读取长期成长趋势…";
  const status = $("#weakness-status");
  status.classList.remove("error");
  status.textContent = "正在读取已保存的分析，不消耗 AI 额度…";
  const [analysis, profile, growth, errorProfile] = await Promise.allSettled([
    api("/api/insights/weakness-analysis"),
    api("/api/me"),
    api("/api/insights/growth"),
    api("/api/stats/error-profile"),
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
  // 错因画像：纯统计，失败就隐藏这一节，不影响其他。
  renderErrorProfile(errorProfile.status === "fulfilled" ? errorProfile.value : null);
  // 独立降级：成长趋势和薄弱点分析是两个互不依赖的接口，一个失败不影响另一个展示。
  growthZones = growth.status === "fulfilled" ? growth.value.zones : null;
  renderGrowthInsights(growth.status === "fulfilled");
  if (window.PracticeNow) void loadWeaknessDue(generation, userId);
}

// "现在就练 5 条"需要知道哪些易错点今天到期：读一次现有的到期列表，按分区 / 引用 id 在前端分给各个按钮。
// 读不到就不显示按钮；离开本页、登出或换账号之后才回来的响应丢弃。

async function loadWeaknessDue(generation, userId) {
  try {
    const due = await api("/api/mistakes?due_only=true");
    if (!weaknessRequestCurrent(generation, userId) || view !== "insights") return;
    window.PracticeNow?.setDue(due.items, due.today);
  } catch {
    // 按钮保持隐藏，薄弱点页本身不受影响。
  }
}

function renderGrowthInsights(loadedOk) {
  const panel = $("#growth-summary");
  const empty = $("#growth-empty");
  const list = $("#growth-zones");
  const communityNote = $("#growth-community-note");
  list.replaceChildren();
  communityNote.hidden = true;
  communityNote.textContent = "";

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
  let hasCommunityComparison = false;
  for (const [index, zone] of growthZones.entries()) {
    const card = element("article", "", "panel growth-zone weakness-reveal");
    card.style.setProperty("--weakness-order", String(index));
    if (zone.quiet_streak) card.classList.add("growth-zone-quiet");
    const heading = element("div", "", "growth-zone-heading");
    const title = element("h4");
    const dot = element("span", "", "growth-zone-dot");
    dot.setAttribute("aria-hidden", "true");
    title.append(dot, document.createTextNode(zone.zone));
    heading.append(title);
    if (zone.quiet_streak) {
      heading.append(element("span", `已 ${zone.days_since_last_mistake} 天没有新增`, "growth-quiet-badge"));
    }
    const headline = element("p", "", "growth-headline");
    headline.append(
      element("span", String(zone.recent_30_days), "growth-count-number"),
      element("span", "近 30 天新增条数", "growth-period")
    );
    card.append(heading, headline);
    const comparison = element("div", "", "growth-comparison");
    comparison.setAttribute("aria-hidden", "true");
    const maximum = Math.max(zone.recent_30_days, zone.prior_30_days, 1);
    for (const [amount, label, previous] of [
      [zone.recent_30_days, "近 30 天", false],
      [zone.prior_30_days, "前 30 天", true],
    ]) {
      const row = element("div", "", "growth-comparison-row");
      if (previous) row.classList.add("growth-comparison-prior");
      const track = element("span", "", "growth-bar-track");
      const bar = element("span", "", "growth-bar");
      bar.style.setProperty("--growth-ratio", String(amount / maximum));
      track.append(bar);
      row.append(
        element("span", label, "growth-comparison-label"),
        track,
        element("span", String(amount), "growth-comparison-value")
      );
      comparison.append(row);
    }
    card.append(comparison, element("p", `近 30 天新增 ${zone.recent_30_days} 条，前 30 天新增 ${zone.prior_30_days} 条。`, "weakness-sr-only"));
    card.append(element(
      "p",
      `累计 ${zone.total_mistakes} 条 · 距上次新增 ${zone.days_since_last_mistake} 天`,
      "muted growth-total"
    ));
    if (zone.community_struggling_ratio != null) {
      hasCommunityComparison = true;
      const community = element("p", `群体挣扎占比 ${Math.round(zone.community_struggling_ratio * 100)}%`, "muted growth-community");
      community.append(element("span", "：全站在该分区有记录的用户中，反复出错（≥3 条易错点）的用户占比。", "weakness-sr-only"));
      card.append(community);
    }
    const zonePractice = window.PracticeNow?.slot({
      zone: zone.zone,
      emptyText: "这一块今天没有要复习的",
      link: { zone: zone.zone },
    });
    if (zonePractice) card.append(zonePractice);
    list.append(card);
  }
  if (!hasCommunityComparison) {
    communityNote.textContent = "同分区记录的人还不够多，暂不显示群体对比";
    communityNote.hidden = false;
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
        // 调用结果按统一规则结算；读取失败时不显示过期的剩余次数。
      }
      if (weaknessRequestCurrent(generation, userId)) {
        weaknessPending = false;
        renderWeaknessControls();
      }
    }
  }
}
