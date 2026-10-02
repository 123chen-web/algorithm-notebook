"use strict";

// Run the real render functions in a deliberately small DOM. This checks output
// and control states, not browser layout, focus, rendering speed or contrast.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const payload = JSON.parse(fs.readFileSync(0, "utf8"));

class Node {
  constructor(tagName, attrs = {}) {
    this.tagName = tagName;
    this.attributes = { ...attrs };
    this.children = [];
    this.dataset = {};
    this.hidden = Object.hasOwn(attrs, "hidden");
    this.disabled = Object.hasOwn(attrs, "disabled");
    this._text = "";
    this.style = {
      values: {},
      setProperty(name, value) { this.values[name] = String(value); },
      getPropertyValue(name) { return this.values[name] || ""; },
    };
    this.classList = {
      contains: (name) => this.className.split(/\s+/).includes(name),
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter((name) => !names.includes(name)).join(" "); },
    };
    for (const [name, value] of Object.entries(attrs)) {
      if (name.startsWith("data-")) this.dataset[name.slice(5)] = value;
    }
  }
  get className() { return this.attributes.class || ""; }
  set className(value) { this.attributes.class = value; }
  get textContent() { return this._text + this.children.map((node) => node.textContent).join(""); }
  set textContent(value) { this.children = []; this._text = value == null ? "" : String(value); }
  set innerHTML(_) { throw new Error("Report data must be inserted as text, never innerHTML"); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  append(...children) {
    for (const child of children) {
      if (child instanceof Node) this.children.push(child);
      else { const text = new Node("#text"); text.textContent = child; this.children.push(text); }
    }
  }
  replaceChildren(...children) { this._text = ""; this.children = []; this.append(...children); }
}

function descendants(node) { return node.children.flatMap((child) => [child, ...descendants(child)]); }
function byClass(node, name) { return descendants(node).filter((child) => child.classList.contains(name)); }
function byTag(node, tag) { return descendants(node).filter((child) => child.tagName === tag); }
function accessibleText(node) {
  if (node.hidden || node.getAttribute("aria-hidden") === "true") return "";
  return node._text + node.children.map(accessibleText).join("");
}
function oneClass(node, name) {
  const matches = byClass(node, name);
  assert.equal(matches.length, 1, `Expected exactly one .${name}`);
  return matches[0];
}

function harness(state = {}) {
  const nodes = Object.fromEntries(Object.entries(payload.ids).map(([id, attrs]) => [id, new Node("div", attrs)]));
  const get = (id) => { assert.ok(nodes[id], `Unknown DOM id: ${id}`); return nodes[id]; };
  const context = {
    document: {
      createElement: (tag) => new Node(tag),
      createTextNode: (text) => { const node = new Node("#text"); node.textContent = text; return node; },
    },
    $: (selector) => get(selector.slice(1)),
    user: { timezone: "Asia/Shanghai", ai_daily_remaining: 10, ai_daily_limit: 10 },
    weaknessAnalysis: { mistake_count: 5, minimum_mistakes: 5, insight: null },
    weaknessPending: false,
    weaknessQuotaAvailable: true,
    weaknessGeneration: 0,
    updateUserInfo: () => {},
    busy: false,
    growthZones: [],
    ...state,
  };
  vm.createContext(context);
  vm.runInContext(payload.source, context, { timeout: 1000 });
  return { context, get };
}

function controls() {
  for (const [count, minimum] of [[0, 5], [4, 5], [7, 8]]) {
    const { context, get } = harness({ weaknessAnalysis: { mistake_count: count, minimum_mistakes: minimum } });
    context.renderWeaknessControls();
    assert.equal(accessibleText(get("weakness-count")), `已积累 ${count} 条易错点，再记录 ${minimum - count} 条就可以开始分析。`);
    assert.equal(oneClass(get("weakness-count"), "weakness-count-visual").getAttribute("aria-hidden"), "true");
    assert.equal(oneClass(get("weakness-count"), "weakness-count-number").textContent, String(count));
    assert.ok(accessibleText(get("weakness-readiness")).includes(`${count} / ${minimum}`));
    assert.equal(Number(oneClass(get("weakness-readiness"), "weakness-progress-fill").style.getPropertyValue("--weakness-progress")), count / minimum);
    assert.equal(get("weakness-analyze").disabled, true);
  }

  const create = "分析我的薄弱点 · 消耗 1 次 AI 额度";
  const update = "更新我的薄弱点分析 · 消耗 1 次 AI 额度";
  const loading = "正在处理，请稍候…";
  const cases = [
    ["ready", {}, false, "0", "false", create],
    ["saved", { weaknessAnalysis: { mistake_count: 8, insight: {} } }, false, "0", "false", update],
    ["insufficient saved", { weaknessAnalysis: { mistake_count: 3, insight: {} } }, true, "1", "false", update],
    ["loading", { weaknessPending: true }, true, "1", "true", loading],
    ["loading saved", { weaknessPending: true, weaknessAnalysis: { mistake_count: 8, insight: {} } }, true, "1", "true", loading],
    ["shared busy", { busy: true }, true, "0", "false", create],
    ["exhausted", { user: { ai_daily_remaining: 0, ai_daily_limit: 10 } }, true, "1", "false", create],
    ["quota unavailable", { weaknessQuotaAvailable: false, user: { ai_daily_remaining: 0, ai_daily_limit: 10 } }, false, "0", "false", create],
    ["count unknown", { weaknessAnalysis: null }, false, "0", "false", create],
  ];
  for (const [label, state, disabled, blocked, ariaBusy, text] of cases) {
    const { context, get } = harness(state);
    get("weakness-status").textContent = "网络错误，原状态行仍应保留";
    context.renderWeaknessControls();
    const button = get("weakness-analyze");
    assert.deepEqual([button.disabled, button.dataset.blocked, get("weakness-page").getAttribute("aria-busy"), button.textContent], [disabled, blocked, ariaBusy, text], label);
    assert.equal(get("weakness-status").textContent, "网络错误，原状态行仍应保留", label);
  }
  const ready = harness();
  ready.context.renderWeaknessControls();
  assert.equal(accessibleText(ready.get("weakness-count")), "已积累 5 条易错点，可以开始分析。");
  assert.ok(ready.get("weakness-readiness").textContent.includes("可以开始分析"));
  assert.equal(byClass(ready.get("weakness-readiness"), "weakness-progress-fill").length, 0);
  const unknown = harness({ weaknessAnalysis: null });
  unknown.context.renderWeaknessControls();
  assert.equal(accessibleText(unknown.get("weakness-count")), "先积累至少 5 条易错点，让分析有足够的线索。");
}

function quota() {
  for (const [remaining, limit, expectedFilled] of [[0, 10, 0], [10, 10, 10], [3, 6, 5], [1, 3, 10 / 3], [0, 0, 0]]) {
    const { context, get } = harness({ user: { ai_daily_remaining: remaining, ai_daily_limit: limit } });
    context.renderWeaknessControls();
    assert.equal(get("weakness-quota-meter").hidden, false);
    assert.equal(get("weakness-quota-meter").getAttribute("aria-hidden"), "true");
    const fills = byClass(get("weakness-quota-meter"), "weakness-quota-dot-fill").map((node) => Number(node.style.getPropertyValue("--weakness-quota-fill")));
    assert.equal(fills.length, 10);
    assert.ok(fills.every((value) => Number.isFinite(value) && value >= 0 && value <= 1));
    assert.ok(Math.abs(fills.reduce((sum, value) => sum + value, 0) - expectedFilled) < 1e-8, `${remaining}/${limit} quota must be proportional`);
    assert.ok(get("weakness-quota").textContent.includes(`今日 AI 额度剩余 ${remaining} / ${limit} 次`));
    if (remaining === 0) assert.ok(get("weakness-quota").textContent.includes("明天可再次分析"));
  }
  const { context, get } = harness();
  context.renderWeaknessControls();
  context.weaknessQuotaAvailable = false;
  context.renderWeaknessControls();
  assert.equal(get("weakness-quota-meter").hidden, true);
  assert.equal(get("weakness-quota-meter").children.length, 0, "Do not keep stale visible quota marks");
  assert.ok(get("weakness-quota").textContent.includes("暂时无法读取剩余额度"));
  context.weaknessPending = true;
  context.renderWeaknessControls();
  assert.equal(get("weakness-quota").textContent, "正在读取今日 AI 额度…");
}

function growth() {
  const zones = [
    { zone: "算法", total_mistakes: 12, recent_30_days: 0, prior_30_days: 0, days_since_last_mistake: 90, quiet_streak: true, community_struggling_ratio: null },
    { zone: "前端", total_mistakes: 33, recent_30_days: 2, prior_30_days: 8, days_since_last_mistake: 2, quiet_streak: false, community_struggling_ratio: null },
  ];
  const { context, get } = harness({ growthZones: zones });
  context.renderGrowthInsights(true);
  assert.equal(get("growth-summary").hidden, false);
  assert.equal(get("growth-empty").hidden, true);
  const cards = byClass(get("growth-zones"), "growth-zone");
  assert.equal(cards.length, 2);
  assert.equal(oneClass(cards[0], "growth-quiet-badge").textContent, "已 90 天没有新增");
  assert.equal(byClass(cards[1], "growth-quiet-badge").length, 0);
  assert.equal(oneClass(cards[0], "growth-count-number").textContent, "0");
  assert.ok(accessibleText(cards[1]).includes("近 30 天新增 2 条，前 30 天新增 8 条。"));
  assert.ok(accessibleText(cards[1]).includes("累计 33 条 · 距上次新增 2 天"));
  const ratios = cards.map((card) => byClass(card, "growth-bar").map((bar) => Number(bar.style.getPropertyValue("--growth-ratio"))));
  assert.deepEqual(ratios, [[0, 0], [0.25, 1]]);
  assert.ok(ratios.flat().every(Number.isFinite), "All-zero comparison must never divide by zero");
  for (const card of cards) assert.equal(oneClass(card, "growth-comparison").getAttribute("aria-hidden"), "true");
  assert.equal(get("growth-community-note").hidden, false);
  assert.equal(get("growth-community-note").textContent, "同分区记录的人还不够多，暂不显示群体对比");
  assert.ok(cards.every((card) => !card.textContent.includes("暂不显示群体对比")), "Show insufficient community data once per section");

  context.growthZones = zones.map((zone, index) => ({ ...zone, community_struggling_ratio: index ? 0.375 : 0 }));
  context.renderGrowthInsights(true);
  const updated = byClass(get("growth-zones"), "growth-zone");
  assert.equal(updated.length, 2, "Refresh must replace cards rather than duplicate them");
  assert.ok(oneClass(updated[0], "growth-community").textContent.includes("群体挣扎占比 0%"), "Zero is a real community signal");
  assert.ok(oneClass(updated[1], "growth-community").textContent.includes("群体挣扎占比 38%"));
  assert.equal(get("growth-community-note").hidden, true);
  assert.equal(get("growth-community-note").textContent, "");
  context.renderGrowthInsights(false);
  assert.equal(get("growth-summary").hidden, true);
  assert.equal(get("growth-empty").hidden, false);
  assert.ok(get("growth-empty").textContent.includes("暂时无法读取成长趋势"));
  assert.equal(get("growth-zones").children.length, 0);
  context.growthZones = [];
  context.renderGrowthInsights(true);
  assert.ok(get("growth-empty").textContent.includes("还没有足够的历史记录"));
}

async function growthReset() {
  const previousZone = "A 账号独有的旧分区";
  const { context, get } = harness({
    user: { id: 1, timezone: "Asia/Shanghai", ai_daily_remaining: 10, ai_daily_limit: 10 },
    growthZones: [{ zone: previousZone, total_mistakes: 12, recent_30_days: 2, prior_30_days: 8, days_since_last_mistake: 1, quiet_streak: false, community_struggling_ratio: null }],
  });
  context.renderGrowthInsights(true);
  assert.ok(get("growth-zones").textContent.includes(previousZone));
  context.resetWeaknessAnalysis();
  assert.equal(context.growthZones, null);
  assert.equal(get("growth-zones").children.length, 0);
  for (const id of ["growth-summary", "growth-empty", "growth-community-note"]) {
    assert.equal(get(id).hidden, true, `${id} must be hidden during account reset`);
    assert.ok(!get(id).textContent.includes(previousZone));
  }
  assert.equal(get("growth-empty").textContent, "");
  assert.equal(get("growth-community-note").textContent, "");

  context.user = { id: 2, timezone: "Asia/Shanghai", ai_daily_remaining: 10, ai_daily_limit: 10 };
  const requests = [];
  context.api = (path) => new Promise((resolve) => requests.push({ path, resolve }));
  const loading = context.loadWeaknessAnalysis();
  assert.equal(requests.length, 3);
  assert.equal(get("weakness-page").getAttribute("aria-busy"), "true");
  assert.equal(get("growth-summary").hidden, true);
  assert.equal(get("growth-empty").hidden, false);
  assert.equal(get("growth-empty").textContent, "正在读取长期成长趋势…");
  assert.equal(get("growth-zones").children.length, 0, "B must not see A's growth cards while the three reads are pending");
  for (const request of requests) {
    if (request.path === "/api/insights/weakness-analysis") request.resolve({ mistake_count: 5, minimum_mistakes: 5, insight: null });
    else if (request.path === "/api/me") request.resolve(context.user);
    else if (request.path === "/api/insights/growth") request.resolve({ zones: [] });
    else assert.fail(`Unexpected request: ${request.path}`);
  }
  await loading;
  assert.equal(get("weakness-page").getAttribute("aria-busy"), "false");
  assert.ok(get("growth-empty").textContent.includes("还没有足够的历史记录"));
  assert.ok(!get("growth-zones").textContent.includes(previousZone));
}

function analysis() {
  const hostile = '<img src=x onerror="globalThis.__injected=1">';
  const sample = { problem_count: 10, mistake_count: 21, review_count: 8, period_start: "2026-08-03T00:00:00Z", period_end: "2026-09-25T02:00:00Z" };
  const patterns = ["较明确", "待验证"].map((confidence, index) => ({
    title: `规律${index + 1} ${hostile}`,
    confidence,
    explanation: `解释${index + 1} ${hostile}`,
    evidence: [{ zone: `算法${index + 1}`, title: `证据题${index + 1} ${hostile}`, observation: `观察${index + 1} ${hostile}` }],
    action: `先画边界${index + 1} ${hostile}`,
  }));
  const insight = { created_at: "2026-10-01T12:00:00Z", content: { summary: `报告摘要 ${hostile}`, sample, patterns } };
  const { context, get } = harness({ weaknessAnalysis: { mistake_count: 21, minimum_mistakes: 5, status: "ok", insight } });
  context.renderWeaknessAnalysis();
  const result = get("weakness-result");
  assert.equal(result.hidden, false);
  assert.equal(get("weakness-empty").hidden, true);
  const hero = oneClass(result, "weakness-overview");
  assert.equal(oneClass(hero, "weakness-summary").textContent, insight.content.summary);
  assert.ok(oneClass(hero, "weakness-eyebrow").textContent.includes(context.timestamp(insight.created_at)));
  assert.deepEqual(byTag(oneClass(hero, "weakness-sample"), "dd").map((node) => node.textContent), ["10", "21", "8"]);
  assert.deepEqual(byTag(oneClass(hero, "weakness-sample"), "dt").map((node) => node.textContent), ["道题", "条易错点", "次复习评分"]);
  const range = oneClass(hero, "weakness-range");
  assert.ok(range.textContent.includes(context.timestamp(sample.period_start)));
  assert.ok(range.textContent.includes(context.timestamp(sample.period_end)));
  assert.ok(range.textContent.includes("只保留最近一次分析"));
  const cards = byClass(result, "weakness-pattern");
  assert.equal(cards.length, 2);
  const list = oneClass(result, "weakness-patterns");
  assert.equal(list.tagName, "ol");
  assert.equal(list.getAttribute("role"), "list");
  assert.equal(list.children.length, patterns.length);
  assert.ok(list.children.every((item) => item.tagName === "li" && item.children.length === 1 && item.children[0].tagName === "article"));
  for (const [index, card] of cards.entries()) {
    const pattern = patterns[index];
    assert.equal(byTag(card, "h3")[0].textContent, `第 ${index + 1} 条规律：${pattern.title}`);
    assert.ok(accessibleText(byTag(card, "h3")[0]).includes(`第 ${index + 1} 条规律`));
    assert.equal(oneClass(byTag(card, "h3")[0], "weakness-sr-only").textContent, `第 ${index + 1} 条规律：`);
    assert.equal(oneClass(card, "weakness-pattern-number").textContent, index ? "02" : "01");
    assert.equal(oneClass(card, "weakness-pattern-number").getAttribute("aria-hidden"), "true");
    const confidence = oneClass(card, "weakness-confidence");
    assert.equal(confidence.textContent, pattern.confidence);
    assert.equal(confidence.classList.contains("weakness-confidence-clear"), index === 0);
    assert.equal(oneClass(card, "weakness-explanation").textContent, pattern.explanation);
    const evidence = oneClass(card, "weakness-evidence");
    assert.equal(evidence.getAttribute("role"), "list");
    assert.equal(byTag(evidence, "li").length, 1);
    assert.equal(oneClass(evidence, "weakness-evidence-zone").textContent, pattern.evidence[0].zone);
    assert.equal(byTag(evidence, "strong")[0].textContent, pattern.evidence[0].title);
    assert.equal(byTag(evidence, "p")[0].textContent, pattern.evidence[0].observation);
    assert.equal(byTag(oneClass(card, "weakness-action"), "p")[0].textContent, pattern.action);
  }
  assert.equal(byTag(result, "img").length + byTag(result, "script").length, 0);
  assert.equal(context.__injected, undefined);
  context.weaknessPending = true;
  context.renderWeaknessControls();
  assert.equal(result.hidden, false, "Keep a saved report visible while another analysis loads");
  assert.equal(get("weakness-page").getAttribute("aria-busy"), "true");
  context.weaknessPending = false;
  context.weaknessAnalysis = { mistake_count: 3, minimum_mistakes: 5, status: "insufficient_data", insight };
  context.renderWeaknessAnalysis();
  assert.equal(result.hidden, false, "Insufficient current data must retain the last saved report");
  assert.equal(byClass(result, "weakness-pattern").length, 2);
  assert.ok(result.textContent.includes("以下是上次保存的分析；当前易错点数量不足，暂时无法更新。"));
  assert.equal(get("weakness-analyze").disabled, true);

  context.weaknessAnalysis = { mistake_count: 5, insight: { ...insight, content: { summary: "尚未发现可靠规律", sample: { ...sample, review_count: 0, period_start: null, period_end: null }, patterns: [] } } };
  context.renderWeaknessAnalysis();
  assert.equal(byClass(result, "weakness-pattern").length, 0);
  assert.ok(result.textContent.includes("目前的记录还不足以确认重复规律"));
  assert.equal(byTag(oneClass(result, "weakness-sample"), "dd")[2].textContent, "0");
  assert.equal(oneClass(result, "weakness-range").textContent, "只保留最近一次分析");
  context.weaknessAnalysis = { mistake_count: 3, message: "再记两条具体错因", insight: null };
  context.renderWeaknessAnalysis();
  assert.equal(result.hidden, true);
  assert.equal(result.children.length, 0);
  assert.equal(get("weakness-empty").hidden, false);
  assert.equal(get("weakness-empty-text").textContent, "再记两条具体错因");
}

const scenarios = { controls, quota, growth, "growth-reset": growthReset, analysis };
assert.ok(Object.hasOwn(scenarios, payload.scenario), "Unknown report test scenario");
Promise.resolve(scenarios[payload.scenario]()).then(() => {
  process.stdout.write(`${payload.scenario}: render behavior passed\n`);
}).catch((error) => {
  process.stderr.write(`${error.stack}\n`);
  process.exitCode = 1;
});
