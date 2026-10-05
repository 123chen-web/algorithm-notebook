"use strict";

/* 总览页的「目标」卡：倒计时 + 今日建议进度 + 设置 / 修改 / 结束目标。
   对外契约：window.GoalCard = { configure({ api, getUser, getEpoch }), mount(container), refresh(), reset(), helpers }。
   - 请求走宿主传入的 api()；每个响应都按登录代次（getEpoch()）、账号（getUser().id）和请求序号校验，
     登出、换号、reset() 之后晚到的响应一律丢弃；
   - 一切来自服务器的文字都用 textContent；表单提交前先在本地校验，422 的 detail 由 api() 放进
     error.message，这里原样显示；
   - 午夜有一个自动刷新计时器，reset() 会把它和缓存一起清掉。 */
(() => {
  const NAME_MAX = 30;
  const RANGE_DAYS = 365;

  let hooks = null;
  let container = null;
  let mode = "idle"; // idle | loading | error | empty | goal | form | confirm
  let goal = null; // 最近一次确认的目标；null 表示「没有目标」（与 mode 一起理解）
  let editing = false; // form 是「修改」还是「设置」
  let formValues = { name: "", date: "" };
  let formError = "";
  let notice = ""; // goal / confirm 视图里的错误提示（删除失败等）
  let busy = ""; // "" | "load" | "save" | "delete"
  let generation = 0;
  const sequence = { load: 0, save: 0, delete: 0 };
  let midnightTimer = 0;

  /* ---------- 纯函数（也挂在 window.GoalCard.helpers 上供测试） ---------- */

  /** 本地时区的 YYYY-MM-DD。 */
  function isoDate(date) {
    const month = String(date.getMonth() + 1).padStart(2, "0");
    const day = String(date.getDate()).padStart(2, "0");
    return `${date.getFullYear()}-${month}-${day}`;
  }

  /** 表单可选日期：明天 ～ 今天 + 365 天。 */
  function dateBounds(now = new Date()) {
    const min = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    const max = new Date(now.getFullYear(), now.getMonth(), now.getDate() + RANGE_DAYS);
    return { min: isoDate(min), max: isoDate(max) };
  }

  /** 倒计时那一行；today / passed / empty 各有说法。 */
  function countdownText(status, daysLeft) {
    const days = Number.isInteger(daysLeft) ? daysLeft : 0;
    if (status === "today") return "就是今天——目标日到了。";
    if (status === "passed") return days < 0 ? `目标日已过 ${-days} 天。` : "目标日已经过了。";
    return `还有 ${Math.max(0, days)} 天。`;
  }

  /** 「今天建议 M 条，已完成 K 条」；没有安排时直说。 */
  function dailyText(status, dailyTarget, doneToday) {
    const target = Number.isInteger(dailyTarget) ? dailyTarget : 0;
    const done = Number.isInteger(doneToday) ? doneToday : 0;
    if (status === "empty" || target <= 0) return "今天没有安排任务。";
    return `今天建议 ${target} 条，已完成 ${done} 条。`;
  }

  /** on_track 用文字表达，不靠颜色：「✓ 已达标」/「○ 还差 N 条」。 */
  function trackText(onTrack, remainingToday) {
    if (onTrack) return "✓ 已达标";
    const remaining = Number.isInteger(remainingToday) ? Math.max(0, remainingToday) : 0;
    return `○ 还差 ${remaining} 条`;
  }

  /** 今日进度百分比；目标为 0 时完成了算 100，否则 0。 */
  function progressPercent(doneToday, dailyTarget) {
    const target = Number.isFinite(dailyTarget) ? dailyTarget : 0;
    const done = Number.isFinite(doneToday) ? doneToday : 0;
    if (target <= 0) return done > 0 ? 100 : 0;
    return Math.max(0, Math.min(100, Math.round((done / target) * 100)));
  }

  /** 与 rank-admin 一致：按码点计数，😀 算 1 字。 */
  function countChars(text) {
    return [...text].length;
  }

  /** 提交前的本地校验；返回 "" 表示可以发请求。 */
  function formProblem({ name, date }, { min, max }) {
    const trimmed = String(name ?? "").trim();
    if (!trimmed) return "请填写目标名称。";
    if (countChars(trimmed) > NAME_MAX) return `名称最多 ${NAME_MAX} 字。`;
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(date ?? ""))) return "请选择目标日期。";
    if (date < min) return `目标日期最早是明天（${min}）。`;
    if (date > max) return `目标日期要在 ${RANGE_DAYS} 天内（最晚 ${max}）。`;
    return "";
  }

  function failure(error) {
    return error?.message || "请求失败，请稍后重试。";
  }

  /* ---------- 迟到响应守卫 ---------- */

  function ticket(kind) {
    sequence[kind] += 1;
    return { kind, sequence: sequence[kind], generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }

  function current(request) {
    const user = hooks?.getUser();
    return Boolean(hooks && user && request.generation === generation
      && request.sequence === sequence[request.kind]
      && request.epoch === hooks.getEpoch() && request.userId === user.id);
  }

  /* ---------- DOM ---------- */

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  function button(label, className, onClick) {
    const item = node("button", className, label);
    item.type = "button";
    item.addEventListener("click", onClick);
    return item;
  }

  /** 跨午夜后倒计时要变，安排一次刷新；reset() 会清掉它。 */
  function scheduleMidnight() {
    window.clearTimeout(midnightTimer);
    midnightTimer = 0;
    if (mode !== "goal" || !goal) return;
    const now = new Date();
    const next = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, 0, 0, 2);
    const stamp = generation;
    midnightTimer = window.setTimeout(() => {
      midnightTimer = 0;
      if (stamp === generation && hooks?.getUser()) load();
    }, Math.max(1000, next - now));
    midnightTimer?.unref?.(); // Node 测试环境：别让计时器拖住进程
  }

  function statusLine(text, isError) {
    const line = node("p", isError ? "gc-error" : "gc-note", text);
    line.setAttribute("role", isError ? "alert" : "status");
    return line;
  }

  function head() {
    const wrap = node("div", "gc-head");
    const eyebrow = node("p", "gc-eyebrow", "GOAL");
    eyebrow.setAttribute("aria-hidden", "true");
    const title = node("h3", "gc-title", "目标");
    title.id = "gc-title";
    wrap.append(eyebrow, title);
    return wrap;
  }

  function emptyView() {
    const wrap = node("div", "gc-empty");
    wrap.append(
      node("p", "gc-empty-text", "还没有目标。定一个名字和日期，我们按剩余天数算出每天建议复习多少条。"),
      button("设置目标", "gc-set primary", () => openForm(false)),
    );
    return wrap;
  }

  function goalView() {
    const wrap = node("div", "gc-body");
    wrap.append(node("p", "gc-name", String(goal.name ?? "")));
    wrap.append(node("p", "gc-countdown", countdownText(goal.status, goal.days_left)));
    wrap.append(node("p", "gc-daily", dailyText(goal.status, goal.daily_target, goal.done_today)));

    const target = Number.isInteger(goal.daily_target) ? goal.daily_target : 0;
    const done = Number.isInteger(goal.done_today) ? goal.done_today : 0;
    const percent = progressPercent(done, target);
    const progress = node("div", "gc-progress");
    const track = node("div", "gc-progress-track");
    const fill = node("div", "gc-progress-fill");
    fill.setAttribute("role", "progressbar");
    fill.setAttribute("aria-label", "今日完成进度");
    fill.setAttribute("aria-valuemin", "0");
    fill.setAttribute("aria-valuemax", String(target > 0 ? target : 100));
    fill.setAttribute("aria-valuenow", String(target > 0 ? Math.min(Math.max(0, done), target) : percent));
    fill.style.setProperty("width", `${percent}%`);
    track.append(fill);
    progress.append(track, node("span", "gc-progress-text", `${percent}%`));
    wrap.append(progress);

    const onTrack = Boolean(goal.on_track);
    wrap.append(node("p", `gc-track ${onTrack ? "is-on" : "is-behind"}`, trackText(onTrack, goal.remaining_today)));

    if (notice) wrap.append(statusLine(notice, true));
    const actions = node("div", "gc-actions");
    actions.append(
      button("修改", "gc-edit", () => openForm(true)),
      button("结束目标", "gc-delete danger", () => { notice = ""; mode = "confirm"; render(); }),
    );
    wrap.append(actions);
    return wrap;
  }

  function formView() {
    const bounds = dateBounds();
    const form = node("form", "gc-form");
    form.setAttribute("novalidate", "");

    const nameField = node("label", "gc-field");
    nameField.append(node("span", "gc-field-label", `目标名称（最多 ${NAME_MAX} 字）`));
    const nameInput = node("input", "gc-input-name");
    nameInput.type = "text";
    nameInput.setAttribute("maxlength", String(NAME_MAX * 4)); // 宽松的物理上限，字数由校验按码点算
    nameInput.setAttribute("required", "");
    nameInput.setAttribute("autocomplete", "off");
    nameInput.value = formValues.name;
    nameField.append(nameInput);

    const dateField = node("label", "gc-field");
    dateField.append(node("span", "gc-field-label", `目标日期（${bounds.min} 至 ${bounds.max}）`));
    const dateInput = node("input", "gc-input-date");
    dateInput.type = "date";
    dateInput.setAttribute("min", bounds.min);
    dateInput.setAttribute("max", bounds.max);
    dateInput.setAttribute("required", "");
    dateInput.value = formValues.date;
    dateField.append(dateInput);

    nameInput.addEventListener("input", () => { formValues.name = nameInput.value; });
    dateInput.addEventListener("input", () => { formValues.date = dateInput.value; });

    form.append(nameField, dateField);
    if (formError) form.append(statusLine(formError, true));

    const actions = node("div", "gc-form-actions");
    const save = node("button", "gc-save primary", busy === "save" ? "正在保存…" : (editing ? "保存修改" : "保存目标"));
    save.type = "submit";
    const cancel = button("取消", "gc-cancel", () => {
      formError = "";
      mode = goal ? "goal" : "empty";
      render();
    });
    if (busy) {
      save.disabled = true;
      cancel.disabled = true;
    }
    actions.append(save, cancel);
    form.append(actions);

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      saveGoal();
    });
    return form;
  }

  function confirmView() {
    const wrap = node("div", "gc-confirm");
    wrap.setAttribute("role", "group");
    wrap.setAttribute("aria-label", "确认结束目标");
    wrap.append(node("p", "gc-confirm-text",
      `确定结束「${String(goal?.name ?? "")}」吗？结束后倒计时和每日建议都会消失，你的错题记录不受影响。`));
    if (notice) wrap.append(statusLine(notice, true));
    const actions = node("div", "gc-confirm-actions");
    const yes = button(busy === "delete" ? "正在结束…" : "确认结束", "gc-confirm-yes danger", () => removeGoal());
    const no = button("再想想", "gc-confirm-no", () => { notice = ""; mode = "goal"; render(); });
    if (busy) {
      yes.disabled = true;
      no.disabled = true;
    }
    actions.append(yes, no);
    wrap.append(actions);
    return wrap;
  }

  function render() {
    if (!container) return;
    const card = node("section", "gc-card");
    card.setAttribute("aria-labelledby", "gc-title");
    card.setAttribute("aria-busy", busy ? "true" : "false");
    card.append(head());
    if (mode === "loading") card.append(statusLine("正在读取目标…", false));
    else if (mode === "error") {
      card.append(statusLine(notice || "暂时无法读取目标。", true));
      card.append(button("重试", "gc-retry", () => load()));
    } else if (mode === "empty") card.append(emptyView());
    else if (mode === "goal" && goal) card.append(goalView());
    else if (mode === "form") card.append(formView());
    else if (mode === "confirm") card.append(confirmView());
    container.replaceChildren(card);
  }

  function openForm(edit) {
    editing = edit;
    formValues = edit && goal
      ? { name: String(goal.name ?? ""), date: String(goal.goal_date ?? "") }
      : { name: "", date: "" };
    formError = "";
    notice = "";
    mode = "form";
    render();
    container?.querySelector(".gc-input-name")?.focus();
  }

  /* ---------- 请求 ---------- */

  async function load() {
    if (!hooks || !hooks.getUser() || !container) return false;
    const request = ticket("load");
    busy = "load";
    if (mode === "idle" || mode === "error") mode = "loading";
    notice = "";
    render();
    try {
      const data = await hooks.api("/api/goal");
      if (!current(request)) return false;
      goal = data?.goal && typeof data.goal === "object" ? data.goal : null;
      mode = goal ? "goal" : "empty";
      return true;
    } catch (error) {
      if (!current(request)) return false;
      notice = failure(error);
      mode = "error";
      return false;
    } finally {
      if (current(request)) {
        busy = "";
        render();
        scheduleMidnight();
      }
    }
  }

  async function saveGoal() {
    if (busy) return; // 防重复提交：先占位再发请求
    const name = formValues.name.trim();
    const date = formValues.date;
    const problem = formProblem({ name, date }, dateBounds());
    if (problem) {
      formError = problem;
      render();
      container?.querySelector(".gc-error")?.focus?.();
      return;
    }
    const request = ticket("save");
    busy = "save";
    formError = "";
    render();
    try {
      const data = await hooks.api("/api/goal", { method: "PUT", body: JSON.stringify({ name, goal_date: date }) });
      if (!current(request)) return;
      goal = data?.goal && typeof data.goal === "object" ? data.goal : null;
      mode = goal ? "goal" : "empty";
      formValues = { name: "", date: "" };
    } catch (error) {
      if (!current(request)) return;
      formError = failure(error); // 422 的 detail 由 api() 放进 message，原样显示
    } finally {
      if (current(request)) {
        busy = "";
        render();
        scheduleMidnight();
      }
    }
  }

  async function removeGoal() {
    if (busy) return;
    const request = ticket("delete");
    busy = "delete";
    notice = "";
    render();
    try {
      await hooks.api("/api/goal", { method: "DELETE" });
      if (!current(request)) return;
      goal = null;
      mode = "empty";
    } catch (error) {
      if (!current(request)) return;
      notice = failure(error); // 留在确认视图里，可以再点一次
    } finally {
      if (current(request)) {
        busy = "";
        render();
      }
    }
  }

  /* ---------- 对外 ---------- */

  function mount(next) {
    container = next || null;
    if (!container || !hooks?.getUser()) {
      container?.replaceChildren();
      return Promise.resolve(false);
    }
    if (mode === "goal" || mode === "empty" || mode === "error") {
      render(); // 有缓存直接画，不重复请求
      return Promise.resolve(true);
    }
    return load();
  }

  function refresh() {
    return load();
  }

  function reset() {
    generation += 1;
    for (const kind of Object.keys(sequence)) sequence[kind] += 1;
    window.clearTimeout(midnightTimer);
    midnightTimer = 0;
    goal = null;
    mode = "idle";
    editing = false;
    formValues = { name: "", date: "" };
    formError = "";
    notice = "";
    busy = "";
    container?.replaceChildren();
  }

  window.GoalCard = {
    configure(options) { hooks = options || null; },
    mount,
    refresh,
    reset,
    helpers: { isoDate, dateBounds, countdownText, dailyText, trackText, progressPercent, countChars, formProblem },
  };
})();
