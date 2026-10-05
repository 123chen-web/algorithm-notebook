"use strict";

/* 总览页「目标」卡（static/goal-card.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   覆盖字符串断言测不到的东西：渲染、迟到响应被丢弃（登出 / 换号 / reset / 乱序）、
   表单本地校验与 422 文案、防重复提交、二次确认删除。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const plain = (value) => JSON.parse(JSON.stringify(value));

const GOAL = (over = {}) => ({
  name: "考研上岸", goal_date: "2026-12-20", days_left: 76, status: "active",
  total_workload: 300, daily_target: 4, done_today: 1, remaining_today: 3, on_track: false,
  ...over,
});

function setup({ user = { id: 7 }, epoch = 1 } = {}) {
  const env = load(["goal-card.js"]);
  const state = { user, epoch };
  const hooks = { getUser: () => state.user, getEpoch: () => state.epoch };
  // 假 fetch 的响应对象只有 json()；api() 里失败要抛错，这里照着宿主 app.js 的样子包一层。
  hooks.api = async (path, options = {}) => {
    const response = await env.window.fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.detail || "请求失败，请稍后重试");
      error.status = response.status;
      throw error;
    }
    return data;
  };
  env.window.GoalCard.configure(hooks);
  const container = env.document.createElement("div");
  env.document.body.append(container);
  return { env, state, container };
}

const q = (container, selector) => container.querySelector(selector);
const settle = async () => { await tick(); await tick(); };

/** mount 并回应最近一次 GET /api/goal。 */
async function mountWith(ctx, status, body) {
  const mounted = ctx.env.window.GoalCard.mount(ctx.container);
  await tick();
  ctx.env.respond(ctx.env.calls.at(-1), status, body);
  await mounted;
  await tick();
}

function openFormWith(ctx, { name, date }) {
  const form = q(ctx.container, "form.gc-form");
  q(ctx.container, ".gc-input-name").value = name;
  q(ctx.container, ".gc-input-name").dispatchEvent(new FakeEvent("input"));
  q(ctx.container, ".gc-input-date").value = date;
  q(ctx.container, ".gc-input-date").dispatchEvent(new FakeEvent("input"));
  return form;
}

function submit(ctx) {
  q(ctx.container, "form.gc-form").dispatchEvent(new FakeEvent("submit", { bubbles: true }));
}

/* ---------------- 加载与渲染 ---------------- */

test("goal: mount asks GET /api/goal once and announces busy while loading", async () => {
  const ctx = setup();
  const mounted = ctx.env.window.GoalCard.mount(ctx.container);
  await tick();
  assert.equal(ctx.env.calls.length, 1);
  assert.equal(ctx.env.calls[0].url, "/api/goal");
  assert.equal(q(ctx.container, ".gc-card").getAttribute("aria-busy"), "true");
  assert.equal(q(ctx.container, ".gc-note").textContent, "正在读取目标…");
  ctx.env.respond(ctx.env.calls[0], 200, { goal: null });
  await mounted;
  assert.equal(q(ctx.container, ".gc-card").getAttribute("aria-busy"), "false");
});

test("goal: no goal renders the guidance and a '设置目标' button", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  assert.match(q(ctx.container, ".gc-empty-text").textContent, /还没有目标/);
  const set = q(ctx.container, ".gc-set");
  assert.ok(set, "设置目标按钮存在");
  assert.equal(set.textContent, "设置目标");
  assert.equal(q(ctx.container, ".gc-progress"), null);
});

test("goal: an active goal renders name, countdown, daily advice and aria is released", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  assert.equal(q(ctx.container, ".gc-name").textContent, "考研上岸");
  assert.equal(q(ctx.container, ".gc-countdown").textContent, "还有 76 天。");
  assert.equal(q(ctx.container, ".gc-daily").textContent, "今天建议 4 条，已完成 1 条。");
  assert.equal(q(ctx.container, ".gc-card").getAttribute("aria-busy"), "false");
  assert.equal(q(ctx.container, ".gc-card").getAttribute("aria-labelledby"), "gc-title");
});

test("goal: server text is shown as text, never parsed as HTML", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL({ name: "<img src=x onerror=alert(1)>" }) });
  assert.equal(q(ctx.container, ".gc-name").textContent, "<img src=x onerror=alert(1)>");
  assert.equal(ctx.container.querySelectorAll("img, script, b").length, 0);
});

test("goal: status 'today' and 'passed' get their own countdown wording", async () => {
  const ctx = setup();
  const { countdownText } = ctx.env.window.GoalCard.helpers;
  assert.equal(countdownText("today", 0), "就是今天——目标日到了。");
  assert.equal(countdownText("passed", -2), "目标日已过 2 天。");
  assert.equal(countdownText("passed", 0), "目标日已经过了。");
  assert.equal(countdownText("active", 5), "还有 5 天。");
  assert.equal(countdownText("empty", 12), "还有 12 天。");
  await mountWith(ctx, 200, { goal: GOAL({ status: "today", days_left: 0 }) });
  assert.equal(q(ctx.container, ".gc-countdown").textContent, "就是今天——目标日到了。");
});

test("goal: status 'empty' says there is nothing scheduled today", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL({ status: "empty", daily_target: 0, done_today: 0, remaining_today: 0 }) });
  assert.equal(q(ctx.container, ".gc-daily").textContent, "今天没有安排任务。");
});

test("goal: the progress bar exposes role/aria values and a text percentage", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  const bar = q(ctx.container, ".gc-progress-fill");
  assert.equal(bar.getAttribute("role"), "progressbar");
  assert.equal(bar.getAttribute("aria-valuemin"), "0");
  assert.equal(bar.getAttribute("aria-valuemax"), "4");
  assert.equal(bar.getAttribute("aria-valuenow"), "1");
  assert.equal(bar.getAttribute("aria-label"), "今日完成进度");
  assert.equal(q(ctx.container, ".gc-progress-text").textContent, "25%");
});

test("goal: done is clamped into the aria range and percent never exceeds 100", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL({ done_today: 9, daily_target: 4, remaining_today: 0, on_track: true }) });
  assert.equal(q(ctx.container, ".gc-progress-fill").getAttribute("aria-valuenow"), "4");
  assert.equal(q(ctx.container, ".gc-progress-text").textContent, "100%");
});

test("goal: a zero daily target falls back to a 0-100 range", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL({ daily_target: 0, done_today: 2 }) });
  const bar = q(ctx.container, ".gc-progress-fill");
  assert.equal(bar.getAttribute("aria-valuemax"), "100");
  assert.equal(bar.getAttribute("aria-valuenow"), "100");
  assert.equal(q(ctx.container, ".gc-progress-text").textContent, "100%");
});

test("goal: on_track is expressed in words and symbols, not color alone", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL({ on_track: true, remaining_today: 0 }) });
  assert.equal(q(ctx.container, ".gc-track").textContent, "✓ 已达标");
  assert.ok(q(ctx.container, ".gc-track").classList.contains("is-on"));
});

test("goal: behind schedule says how many are left", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL({ on_track: false, remaining_today: 3 }) });
  assert.equal(q(ctx.container, ".gc-track").textContent, "○ 还差 3 条");
  assert.ok(q(ctx.container, ".gc-track").classList.contains("is-behind"));
});

test("goal: a failed load shows the server message with role=alert and a retry", async () => {
  const ctx = setup();
  await mountWith(ctx, 500, { detail: "服务器出错了" });
  const error = q(ctx.container, ".gc-error");
  assert.equal(error.textContent, "服务器出错了");
  assert.equal(error.getAttribute("role"), "alert");
  assert.equal(q(ctx.container, ".gc-card").getAttribute("aria-busy"), "false");
  q(ctx.container, ".gc-retry").click();
  await tick();
  assert.equal(ctx.env.calls.length, 2, "重试会重新请求");
  ctx.env.respond(ctx.env.calls[1], 200, { goal: GOAL() });
  await settle();
  assert.equal(q(ctx.container, ".gc-name").textContent, "考研上岸");
});

test("goal: without a signed-in user nothing is requested and the container stays empty", async () => {
  const ctx = setup({ user: null });
  assert.equal(await ctx.env.window.GoalCard.mount(ctx.container), false);
  assert.equal(ctx.env.calls.length, 0);
  assert.equal(ctx.container.childElementCount, 0);
});

test("goal: remounting reuses the cache; after reset() it asks again", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  const other = ctx.env.document.createElement("div");
  ctx.env.document.body.append(other);
  assert.equal(await ctx.env.window.GoalCard.mount(other), true);
  assert.equal(ctx.env.calls.length, 1, "有缓存不重复请求");
  assert.equal(q(other, ".gc-name").textContent, "考研上岸");
  ctx.env.window.GoalCard.reset();
  assert.equal(other.childElementCount, 0, "reset 清空已挂载的内容");
  const again = ctx.env.window.GoalCard.mount(other);
  await tick();
  assert.equal(ctx.env.calls.length, 2, "reset 清掉缓存后重新请求");
  ctx.env.respond(ctx.env.calls[1], 200, { goal: null });
  await again;
});

/* ---------------- 迟到响应 ---------------- */

test("goal: a GET answered after reset() never reaches the page", async () => {
  const ctx = setup();
  const mounted = ctx.env.window.GoalCard.mount(ctx.container);
  await tick();
  ctx.env.window.GoalCard.reset(); // 登出
  ctx.env.respond(ctx.env.calls[0], 200, { goal: GOAL({ name: "上一位用户的目标" }) });
  await mounted;
  await tick();
  assert.equal(ctx.container.textContent.includes("上一位用户的目标"), false);
  assert.equal(ctx.container.childElementCount, 0);
});

test("goal: a GET answered after switching accounts is dropped", async () => {
  const ctx = setup();
  const mounted = ctx.env.window.GoalCard.mount(ctx.container);
  await tick();
  ctx.state.user = { id: 99 }; // 同一页面换了号
  ctx.state.epoch += 1;
  ctx.env.respond(ctx.env.calls[0], 200, { goal: GOAL({ name: "旧账号的目标" }) });
  await mounted;
  await tick();
  assert.equal(ctx.container.textContent.includes("旧账号的目标"), false);
});

test("goal: signing out and back into the same account still drops the old session's answer", async () => {
  const ctx = setup();
  const mounted = ctx.env.window.GoalCard.mount(ctx.container);
  await tick();
  ctx.state.epoch += 1; // 登出再登录同一个账号：user.id 没变，只有登录代次变了
  ctx.env.respond(ctx.env.calls[0], 200, { goal: GOAL({ name: "上个会话的目标" }) });
  await mounted;
  await tick();
  assert.equal(ctx.container.textContent.includes("上个会话的目标"), false);
});

test("goal: an older response cannot overwrite a newer one (out of order)", async () => {
  const ctx = setup();
  const first = ctx.env.window.GoalCard.mount(ctx.container);
  await tick();
  const second = ctx.env.window.GoalCard.refresh();
  await tick();
  assert.equal(ctx.env.calls.length, 2, "强制刷新真的再问一次");
  ctx.env.respond(ctx.env.calls[1], 200, { goal: GOAL({ name: "新的目标" }) });
  await second;
  ctx.env.respond(ctx.env.calls[0], 200, { goal: GOAL({ name: "旧的目标" }) });
  await first;
  await tick();
  assert.equal(q(ctx.container, ".gc-name").textContent, "新的目标");
});

test("goal: a PUT answered after reset() is dropped", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  const bounds = ctx.env.window.GoalCard.helpers.dateBounds();
  openFormWith(ctx, { name: "旧目标", date: bounds.min });
  submit(ctx);
  await tick();
  assert.equal(ctx.env.calls.length, 2);
  ctx.env.window.GoalCard.reset(); // 保存还没回来就登出
  ctx.env.respond(ctx.env.calls[1], 200, { goal: GOAL({ name: "旧目标" }) });
  await settle();
  assert.equal(ctx.container.textContent.includes("旧目标"), false);
});

test("goal: a DELETE answered after switching accounts is dropped", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  q(ctx.container, ".gc-delete").click();
  q(ctx.container, ".gc-confirm-yes").click();
  await tick();
  assert.equal(ctx.env.calls.length, 2);
  ctx.state.user = { id: 8 };
  ctx.state.epoch += 1;
  ctx.env.respond(ctx.env.calls[1], 200, { goal: null });
  await settle();
  assert.equal(q(ctx.container, ".gc-set"), null, "被丢弃的 DELETE 不会把界面切成空状态");
  assert.ok(q(ctx.container, ".gc-confirm"), "界面停在确认视图，等宿主 reset 后由新账号重新挂载");
});

/* ---------------- 表单 ---------------- */

test("goal: '设置目标' opens a form with min=tomorrow and max=+365 days", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  const date = q(ctx.container, ".gc-input-date");
  const bounds = ctx.env.window.GoalCard.helpers.dateBounds();
  assert.equal(date.getAttribute("min"), bounds.min);
  assert.equal(date.getAttribute("max"), bounds.max);
  assert.match(q(ctx.container, ".gc-field-label").textContent, /最多 30 字/);
  assert.ok(q(ctx.container, ".gc-input-name").getAttribute("maxlength"));
  assert.equal(ctx.env.document.activeElement, q(ctx.container, ".gc-input-name"), "打开表单后焦点进名称框");
});

test("goal: '修改' opens the form prefilled with the current goal", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  q(ctx.container, ".gc-edit").click();
  assert.equal(q(ctx.container, ".gc-input-name").value, "考研上岸");
  assert.equal(q(ctx.container, ".gc-input-date").value, "2026-12-20");
  assert.equal(q(ctx.container, ".gc-save").textContent, "保存修改");
});

test("goal: cancelling from the empty state sends nothing and returns to the guidance", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  q(ctx.container, ".gc-cancel").click();
  assert.equal(ctx.env.calls.length, 1);
  assert.ok(q(ctx.container, ".gc-set"), "回到空状态");
});

test("goal: cancelling an edit returns to the goal view unchanged", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  q(ctx.container, ".gc-edit").click();
  openFormWith(ctx, { name: "改掉的名字", date: "2027-01-01" });
  q(ctx.container, ".gc-cancel").click();
  assert.equal(ctx.env.calls.length, 1);
  assert.equal(q(ctx.container, ".gc-name").textContent, "考研上岸");
});

test("goal: an empty name is rejected locally before any request", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  openFormWith(ctx, { name: "   ", date: ctx.env.window.GoalCard.helpers.dateBounds().min });
  submit(ctx);
  await tick();
  assert.equal(ctx.env.calls.length, 1, "没有发出 PUT");
  assert.equal(q(ctx.container, ".gc-error").textContent, "请填写目标名称。");
  assert.equal(q(ctx.container, ".gc-error").getAttribute("role"), "alert");
});

test("goal: a name over 30 characters is rejected; exactly 30 passes", async () => {
  const ctx = setup();
  const { formProblem, countChars, dateBounds } = ctx.env.window.GoalCard.helpers;
  const bounds = dateBounds();
  assert.equal(countChars("😀字"), 2);
  assert.match(formProblem({ name: "字".repeat(31), date: bounds.min }, bounds), /最多 30/);
  assert.equal(formProblem({ name: "字".repeat(30), date: bounds.min }, bounds), "");
  assert.equal(formProblem({ name: "😀".repeat(30), date: bounds.min }, bounds), "", "emoji 按码点算一个字");
});

test("goal: dates outside tomorrow..+365d or malformed are rejected locally", async () => {
  const ctx = setup();
  const { formProblem, dateBounds, isoDate } = ctx.env.window.GoalCard.helpers;
  const bounds = dateBounds();
  const today = isoDate(new Date());
  assert.match(formProblem({ name: "好", date: today }, bounds), /最早是明天/);
  const over = new Date();
  over.setDate(over.getDate() + 366);
  assert.match(formProblem({ name: "好", date: isoDate(over) }, bounds), /365 天内/);
  assert.match(formProblem({ name: "好", date: "20-12-20" }, bounds), /请选择目标日期/);
  assert.equal(formProblem({ name: "好", date: bounds.min }, bounds), "");
  assert.equal(formProblem({ name: "好", date: bounds.max }, bounds), "");
});

test("goal: a successful save PUTs trimmed values and renders the response without an extra GET", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  const bounds = ctx.env.window.GoalCard.helpers.dateBounds();
  openFormWith(ctx, { name: "  期末冲刺  ", date: bounds.min });
  submit(ctx);
  await tick();
  assert.equal(ctx.env.calls.length, 2);
  const put = ctx.env.calls[1];
  assert.equal(put.url, "/api/goal");
  assert.equal(put.init.method, "PUT");
  assert.deepEqual(plain(JSON.parse(put.init.body)), { name: "期末冲刺", goal_date: bounds.min });
  ctx.env.respond(put, 200, { goal: GOAL({ name: "期末冲刺", goal_date: bounds.min }) });
  await settle();
  assert.equal(q(ctx.container, ".gc-name").textContent, "期末冲刺");
  assert.equal(ctx.env.calls.length, 2, "保存成功直接用响应渲染，不再 GET");
});

test("goal: while saving, the submit is disabled and a second submit sends nothing", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  const bounds = ctx.env.window.GoalCard.helpers.dateBounds();
  openFormWith(ctx, { name: "目标", date: bounds.min });
  submit(ctx);
  await tick();
  assert.equal(ctx.env.calls.length, 2);
  const save = q(ctx.container, ".gc-save");
  assert.equal(save.disabled, true);
  assert.equal(save.textContent, "正在保存…");
  assert.equal(q(ctx.container, ".gc-cancel").disabled, true);
  submit(ctx); // 再点一次
  await tick();
  assert.equal(ctx.env.calls.length, 2, "防重复提交");
  ctx.env.respond(ctx.env.calls[1], 422, { detail: "日期必须是未来 1–365 天内" });
  await settle();
  assert.equal(q(ctx.container, ".gc-save").disabled, false, "回来后按钮恢复");
});

test("goal: a 422 shows the server's detail in place, keeps the form open and keeps the input", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  const bounds = ctx.env.window.GoalCard.helpers.dateBounds();
  openFormWith(ctx, { name: "期末冲刺", date: bounds.min });
  submit(ctx);
  await tick();
  ctx.env.respond(ctx.env.calls[1], 422, { detail: "日期必须是未来 1–365 天内" });
  await settle();
  assert.equal(q(ctx.container, ".gc-error").textContent, "日期必须是未来 1–365 天内");
  assert.ok(q(ctx.container, "form.gc-form"), "表单还在");
  assert.equal(q(ctx.container, ".gc-input-name").value, "期末冲刺", "输入保留");
  assert.equal(q(ctx.container, ".gc-save").disabled, false);
});

test("goal: a non-422 failure also shows the message and re-enables the form", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: null });
  q(ctx.container, ".gc-set").click();
  const bounds = ctx.env.window.GoalCard.helpers.dateBounds();
  openFormWith(ctx, { name: "期末冲刺", date: bounds.min });
  submit(ctx);
  await tick();
  ctx.env.respond(ctx.env.calls[1], 500, { detail: "服务器出错了" });
  await settle();
  assert.equal(q(ctx.container, ".gc-error").textContent, "服务器出错了");
  assert.equal(q(ctx.container, ".gc-save").disabled, false);
});

/* ---------------- 结束目标 ---------------- */

test("goal: '结束目标' asks for confirmation first and sends nothing yet", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  q(ctx.container, ".gc-delete").click();
  const group = q(ctx.container, ".gc-confirm");
  assert.equal(group.getAttribute("role"), "group");
  assert.equal(group.getAttribute("aria-label"), "确认结束目标");
  assert.match(group.textContent, /考研上岸/);
  assert.match(group.textContent, /错题记录不受影响/);
  assert.equal(ctx.env.calls.length, 1, "确认之前不发请求");
});

test("goal: cancelling the confirmation returns to the goal view", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  q(ctx.container, ".gc-delete").click();
  q(ctx.container, ".gc-confirm-no").click();
  assert.equal(q(ctx.container, ".gc-name").textContent, "考研上岸");
  assert.equal(ctx.env.calls.length, 1);
});

test("goal: confirming sends DELETE once; buttons stay disabled while pending", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  q(ctx.container, ".gc-delete").click();
  q(ctx.container, ".gc-confirm-yes").click();
  await tick();
  assert.equal(ctx.env.calls.length, 2);
  assert.equal(ctx.env.calls[1].url, "/api/goal");
  assert.equal(ctx.env.calls[1].init.method, "DELETE");
  assert.equal(q(ctx.container, ".gc-confirm-yes").disabled, true);
  assert.equal(q(ctx.container, ".gc-confirm-yes").textContent, "正在结束…");
  q(ctx.container, ".gc-confirm-yes").click(); // 再点也不重复发
  await tick();
  assert.equal(ctx.env.calls.length, 2);
  ctx.env.respond(ctx.env.calls[1], 200, { goal: null });
  await settle();
  assert.ok(q(ctx.container, ".gc-set"), "结束后回到空状态");
});

test("goal: a failed DELETE shows the message inside the confirmation and can be retried", async () => {
  const ctx = setup();
  await mountWith(ctx, 200, { goal: GOAL() });
  q(ctx.container, ".gc-delete").click();
  q(ctx.container, ".gc-confirm-yes").click();
  await tick();
  ctx.env.respond(ctx.env.calls[1], 500, { detail: "删除失败，请稍后再试" });
  await settle();
  assert.equal(q(ctx.container, ".gc-error").textContent, "删除失败，请稍后再试");
  assert.ok(q(ctx.container, ".gc-confirm"), "仍在确认视图");
  assert.equal(q(ctx.container, ".gc-confirm-yes").disabled, false);
  q(ctx.container, ".gc-confirm-yes").click();
  await tick();
  assert.equal(ctx.env.calls.length, 3, "可以重试");
  ctx.env.respond(ctx.env.calls[2], 200, { goal: null });
  await settle();
  assert.ok(q(ctx.container, ".gc-set"));
});

/* ---------------- helpers（纯函数） ---------------- */

test("helpers: dailyText, trackText and progressPercent edge cases", async () => {
  const ctx = setup();
  const { dailyText, trackText, progressPercent } = ctx.env.window.GoalCard.helpers;
  assert.equal(dailyText("active", 4, 1), "今天建议 4 条，已完成 1 条。");
  assert.equal(dailyText("empty", 4, 1), "今天没有安排任务。");
  assert.equal(dailyText("active", 0, 0), "今天没有安排任务。");
  assert.equal(trackText(true, 0), "✓ 已达标");
  assert.equal(trackText(false, 5), "○ 还差 5 条");
  assert.equal(trackText(false, -1), "○ 还差 0 条");
  assert.equal(progressPercent(1, 4), 25);
  assert.equal(progressPercent(0, 0), 0);
  assert.equal(progressPercent(3, 0), 100);
  assert.equal(progressPercent(9, 4), 100);
  assert.equal(progressPercent(-1, 4), 0);
});

test("helpers: isoDate pads and dateBounds spans tomorrow to +365 days", async () => {
  const ctx = setup();
  const { isoDate, dateBounds } = ctx.env.window.GoalCard.helpers;
  assert.equal(isoDate(new Date(2026, 0, 5)), "2026-01-05");
  const now = new Date(2026, 9, 5); // 2026-10-05
  assert.deepEqual(plain(dateBounds(now)), { min: "2026-10-06", max: "2027-10-05" });
});
