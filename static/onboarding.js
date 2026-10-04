"use strict";

/* 新用户首次清单 + 示例错题 + 空状态的"下一步"按钮。
   对外契约：window.Onboarding = { configure({ api }), reset(user), emptyNext(kind, container, context), ... }。
   - 总览渲染完 app.js 会派发 app:home-rendered，清单卡片 #onboarding-card 由这里插到 #home-page 最上方；
   - 三步的完成状态全部从数据推导（记录 / 复习时间），唯一存在浏览器里的是"看过薄弱点分析"和"不再显示"两个本地标记；
   - 示例记录靠标题前缀【示例】识别，用现有的 POST /api/problems 与 DELETE /api/problems/{id} 创建 / 清除，不碰后端；
   - 登出再登录别的账号后，旧账号发出的请求结果一律丢弃（epoch）。
   所有用户可控文字一律 textContent。 */
(() => {
  const DEMO_PREFIX = "【示例】";
  const OLD_USER_RECORDS = 10;
  const WEAKNESS_KEY = "onboarding-weakness-seen";
  const DISMISS_KEY = "onboarding-dismissed";
  const DEMO_HINT_KEY = "onboarding-demo-loaded";
  const CELEBRATE_MS = 9000;
  const SVG_NS = "http://www.w3.org/2000/svg";

  const STEPS = [
    { key: "record", title: "记下第一条错题", detail: "写下题目、当时的思路和错在哪，系统会自动安排复习。", view: "new" },
    { key: "review", title: "完成第一次复习", detail: "回忆一遍再对照笔记，给掌握程度打个分。", view: "today" },
    { key: "weakness", title: "看一眼薄弱点分析", detail: "记录攒够之后，这里会指出下一步值得练的方向。", view: "insights" },
  ];

  /* 四条示例：覆盖 算法 / 数据库 / 高等数学 / 概率统计。
     数学类分区没有编程语言，"代码"字段就是解题过程（和新增记录页一致）。 */
  const DEMOS = [
    {
      title: `${DEMO_PREFIX}二分查找：循环条件写成 left < right，漏掉最后一个元素`,
      zone: "算法",
      language: "Python",
      code: [
        "def binary_search(nums, target):",
        "    left, right = 0, len(nums) - 1",
        "    while left < right:            # 错误：区间只剩一个元素时直接退出",
        "        mid = left + (right - left) // 2",
        "        if nums[mid] == target:",
        "            return mid",
        "        if nums[mid] < target:",
        "            left = mid + 1",
        "        else:",
        "            right = mid - 1",
        "    return -1",
      ].join("\n"),
      thinking: "用闭区间 [left, right] 的写法：区间里还有元素就必须继续查，所以循环条件应该是 left <= right；"
        + "收缩时 mid 已经比较过，左右边界分别取 mid + 1 和 mid - 1，否则会死循环。",
      mistake: "把循环条件写成 left < right。数组只剩一个元素、或目标恰好是最后一个被检查的位置时，循环提前结束，错误地返回 -1。",
    },
    {
      title: `${DEMO_PREFIX}LEFT JOIN 后把右表条件写进 WHERE，没有订单的用户消失了`,
      zone: "数据库",
      language: "SQL",
      code: [
        "-- 目标：列出所有用户，以及他们最近 30 天的订单数（没下单的显示 0）",
        "SELECT u.id, COUNT(o.id) AS orders",
        "FROM users u",
        "LEFT JOIN orders o ON o.user_id = u.id",
        "WHERE o.created_at >= date('now', '-30 day')   -- 错误：右表条件放在了 WHERE",
        "GROUP BY u.id;",
      ].join("\n"),
      thinking: "LEFT JOIN 对没有匹配的用户会补一行全是 NULL 的订单，WHERE 里的 o.created_at >= … 对 NULL 不成立，这行被过滤掉，"
        + "结果就退化成内连接。想保留所有用户，要把时间条件移到 ON 子句里。",
      mistake: "右表的过滤条件写在 WHERE 里，LEFT JOIN 补出来的 NULL 行被一并过滤，没有订单的用户直接不见了。",
    },
    {
      title: `${DEMO_PREFIX}导数之比的极限不存在，就断定原极限不存在`,
      zone: "高等数学",
      language: "",
      code: [
        "求 lim(x→∞) (x + sin x) / x。",
        "",
        "我的解法：这是 ∞/∞ 型，用洛必达法则，",
        "分子分母各求导得 (1 + cos x) / 1，",
        "x→∞ 时 cos x 来回振荡，极限不存在，所以原极限不存在。（错误）",
      ].join("\n"),
      thinking: "洛必达法则的结论方向是：导数之比的极限存在（或为无穷），原极限才等于它。导数之比的极限不存在时，法则根本不适用，"
        + "不能反推原极限不存在。这里应该拆项：原式 = 1 + sin x / x，sin x 有界、1/x → 0，所以极限为 1。",
      mistake: "把洛必达法则当成“双向”的：导数之比的极限不存在，并不能说明原极限不存在，这时要换方法（拆项、夹逼等）。",
    },
    {
      title: `${DEMO_PREFIX}“至少一次”当成“恰好一次”来算，忘了用对立事件`,
      zone: "概率统计",
      language: "",
      code: [
        "题目：连续抛一枚均匀硬币 5 次，求至少出现一次正面的概率。",
        "",
        "我的解法：P = C(5,1) · (1/2)^5 = 5/32。（错误：这是“恰好一次正面”的概率）",
        "正确做法：P = 1 - P(5 次全是反面) = 1 - (1/2)^5 = 31/32。",
      ].join("\n"),
      thinking: "“至少一次”包含 1 到 5 次正面共五种情况，逐个相加既麻烦又容易漏；它的对立事件“一次正面都没有”只有一种，"
        + "直接用 1 减去它。",
      mistake: "审题时把“至少一次”读成了“恰好一次”，直接套用二项分布的单项公式。遇到“至少 / 至多”先想对立事件。",
    },
  ];

  /* ---------- 状态（全部属于当前登录的账号，登出时清空） ---------- */
  let api = null;
  let epoch = 0; // 只在登出 / 换账号时加一，用来丢弃旧账号发出的请求结果
  let renderSeq = 0;
  let session = null; // { userId, isTrial }
  let overviewTotal = 0;
  let records = null; // [{ id, problemId, title, reviewed }]；null 表示没取到
  let sawIncomplete = false;
  let job = null; // { kind: "load" | "clear", done, total, error, running, returnView }
  let confirming = false;
  let settling = false; // 示例任务刚做完、总览还没重新读取：此时手里的记录是旧的，先不给按钮
  let flash = "";
  let celebrateTimer = 0;
  let celebrating = false; // 祝贺一旦出现就保持到自动收起，中途重画也不消失
  let currentView = "";
  let card = null;
  const surfaces = new Set();

  const $ = (selector) => document.querySelector(selector);
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

  /* ---------- 本地标记（localStorage 可能抛异常：当作没有） ---------- */
  function flagName(base) {
    return session ? `${base}:${session.userId}` : null;
  }
  function readFlag(base) {
    const name = flagName(base);
    if (!name) return false;
    try {
      return window.localStorage.getItem(name) === "1";
    } catch {
      return false;
    }
  }
  function writeFlag(base) {
    const name = flagName(base);
    if (!name) return;
    try {
      window.localStorage.setItem(name, "1");
    } catch {
      // 存不下就算了：下次照常显示。
    }
  }

  /* ---------- 纯函数：步骤推导与显示规则 ---------- */
  const isDemo = (record) => record.title.startsWith(DEMO_PREFIX);

  function deriveSteps({ total, records: list, weaknessSeen }) {
    return [
      Number.isInteger(total) && total > 0,
      Array.isArray(list) && list.some((record) => record.reviewed),
      Boolean(weaknessSeen),
    ];
  }

  /** "checklist" 完整清单 · "celebrate" 祝贺 · "demo" 只留示例控制条 · "none" 不显示 */
  function decideMode({ total, steps, dismissed, sawIncomplete: seen, demoCount, flashText }) {
    const fallback = demoCount > 0 || flashText ? "demo" : "none";
    if (total >= OLD_USER_RECORDS) return fallback;
    if (steps.every(Boolean)) return seen && !dismissed ? "celebrate" : fallback;
    if (dismissed) return fallback;
    return "checklist";
  }

  /* ---------- 请求 ---------- */
  class Stale extends Error {}
  async function guarded(ticket, promise) {
    const result = await promise;
    if (ticket !== epoch) throw new Stale();
    return result;
  }
  async function fetchRecords(ticket) {
    const data = await guarded(ticket, api("/api/mistakes?due_only=false"));
    return (data.items || []).map((item) => ({
      id: item.id,
      problemId: item.problem_id,
      title: String(item.title ?? ""),
      reviewed: Boolean(item.last_reviewed_at),
    }));
  }
  function announceDataChanged() {
    document.dispatchEvent(new CustomEvent("app:data-changed", { detail: { reason: "onboarding" } }));
  }
  function refreshView(returnView) {
    if (returnView && returnView === currentView) {
      document.dispatchEvent(new CustomEvent("app:navigate", { detail: { view: returnView } }));
    }
  }
  function goto(view) {
    document.dispatchEvent(new CustomEvent("app:navigate", { detail: { view } }));
  }

  /* ---------- 示例：载入 / 清除 ---------- */
  function failure(error) {
    return error?.message || "请求失败，请稍后重试";
  }

  async function runLoad({ returnView, retry = false }) {
    if (!api || job?.running) return; // 重复点击只发一次：先占位再发请求
    const ticket = epoch;
    job = { kind: "load", done: 0, total: DEMOS.length, error: null, running: true, returnView };
    flash = "";
    confirming = false;
    rerender();
    try {
      const existing = await fetchRecords(ticket);
      if (!retry && existing.length > 0) {
        job.error = "你已经有记录了，不需要再载入示例。";
        job.running = false;
        job.retryable = false;
        rerender();
        return;
      }
      const have = new Set(existing.map((record) => record.title));
      const todo = DEMOS.filter((demo) => !have.has(demo.title));
      job.done = DEMOS.length - todo.length;
      rerender();
      for (const demo of todo) {
        await guarded(ticket, api("/api/problems", {
          method: "POST",
          body: JSON.stringify({
            title: demo.title, zone: demo.zone, language: demo.language,
            code: demo.code, thinking: demo.thinking, mistakes: [demo.mistake],
          }),
        }));
        job.done += 1;
        rerender();
      }
    } catch (error) {
      if (error instanceof Stale || ticket !== epoch) return;
      job.error = failure(error);
      job.running = false;
      job.retryable = true;
      rerender();
      announceDataChanged(); // 已经创建的那几条要算进侧栏数字
      return;
    }
    job = null;
    settling = true;
    writeFlag(DEMO_HINT_KEY);
    flash = returnView === currentView
      ? "已载入示例，可以去今日复习试试；不需要时点“清除示例数据”。" : "";
    announceDataChanged();
    refreshView(returnView);
    rerender();
  }

  async function runClear({ returnView }) {
    if (!api || job?.running) return;
    const ticket = epoch;
    job = { kind: "clear", done: 0, total: 0, error: null, running: true, returnView, retryable: true };
    flash = "";
    confirming = false;
    rerender();
    let removed = 0;
    try {
      const fresh = await fetchRecords(ticket);
      const demoRecords = fresh.filter(isDemo); // 只认标题前缀，用户自己的记录一条都不碰
      const problemIds = [...new Set(demoRecords.map((record) => record.problemId))];
      job.total = problemIds.length;
      removed = demoRecords.length;
      rerender();
      for (const problemId of problemIds) {
        try {
          await guarded(ticket, api(`/api/problems/${problemId}`, { method: "DELETE" }));
        } catch (error) {
          if (error instanceof Stale || error?.status !== 404) throw error; // 已经没了就当删掉了
        }
        job.done += 1;
        rerender();
      }
    } catch (error) {
      if (error instanceof Stale || ticket !== epoch) return;
      job.error = failure(error);
      job.running = false;
      rerender();
      announceDataChanged();
      return;
    }
    job = null;
    settling = true;
    flash = returnView === currentView ? `已清除 ${removed} 条示例记录。` : "";
    announceDataChanged();
    refreshView(returnView);
    rerender();
  }

  /* ---------- 示例控制条（清单卡片与各空状态共用） ---------- */
  function demoControls({ canLoad, demoCount, returnView, compact = false }) {
    const wrap = node("div", "ob-demo");
    const status = node("p", "ob-demo-status");
    status.setAttribute("role", "status");
    wrap.append(status);
    if (job?.running) {
      const label = job.kind === "load"
        ? `正在载入 ${Math.min(job.done + 1, job.total)} / ${job.total}`
        : (job.total ? `正在清除 ${Math.min(job.done + 1, job.total)} / ${job.total}` : "正在清除…");
      const busy = button(label, "ob-demo-button", () => {});
      busy.disabled = true;
      busy.setAttribute("aria-busy", "true");
      status.textContent = label;
      status.classList.add("ob-sr-only"); // 按钮上已经写了进度；这一行只给读屏软件
      wrap.append(busy);
      return wrap;
    }
    if (job?.error) {
      const verb = job.kind === "load" ? "载入" : "清除";
      const error = node("p", "ob-demo-error", `${verb}没有全部完成：${job.error}${job.done ? `（已完成 ${job.done} 条，保留不动）` : ""}`);
      error.setAttribute("role", "alert");
      wrap.append(error);
      if (job.retryable !== false) {
        wrap.append(button("重试剩下的", "ob-demo-button", () => (job.kind === "load"
          ? runLoad({ returnView: job.returnView, retry: true })
          : runClear({ returnView: job.returnView }))));
      } else {
        wrap.append(button("知道了", "ob-demo-button", () => { job = null; rerender(); }));
      }
      return wrap;
    }
    if (confirming && demoCount > 0) {
      const ask = node("div", "ob-confirm");
      ask.setAttribute("role", "group");
      ask.setAttribute("aria-label", "确认清除示例数据");
      const yes = button("确认清除", "ob-demo-button ob-danger", () => runClear({ returnView }));
      yes.dataset.ob = "confirm";
      ask.append(
        node("p", "ob-confirm-text", `将删除 ${demoCount} 条以${DEMO_PREFIX}开头的记录，你自己的记录不受影响。`),
        yes,
        button("取消", "ob-demo-button", () => { confirming = false; rerender(); }),
      );
      wrap.append(ask);
      return wrap;
    }
    if (flash) status.textContent = flash;
    if (settling) return wrap;
    if (canLoad) {
      wrap.append(button(compact ? "载入示例" : `载入 ${DEMOS.length} 条示例错题体验一下`, "ob-demo-button primary", () => runLoad({ returnView })));
      wrap.append(node("p", "ob-demo-note", "示例记录也会计入统计，清除后一并消失。"));
    } else if (demoCount > 0) {
      const clear = button("清除示例数据", "ob-demo-button", () => { confirming = true; rerender("confirm"); });
      wrap.append(clear);
      wrap.append(node("p", "ob-demo-note", "示例记录也会计入统计（打卡、热力图等），清除后一并消失。"));
    }
    return wrap;
  }

  /* ---------- 清单卡片 ---------- */
  function ring(done) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("class", "ob-ring-svg");
    svg.setAttribute("viewBox", "0 0 36 36");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    for (const [className, dash] of [["ob-ring-track", "100 100"], ["ob-ring-fill", `${(done / STEPS.length) * 100} 100`]]) {
      const circle = document.createElementNS(SVG_NS, "circle");
      circle.setAttribute("class", className);
      circle.setAttribute("cx", "18");
      circle.setAttribute("cy", "18");
      circle.setAttribute("r", "15.9155");
      circle.setAttribute("fill", "none");
      circle.setAttribute("stroke-width", "3");
      circle.setAttribute("stroke-dasharray", dash);
      circle.setAttribute("transform", "rotate(-90 18 18)");
      svg.append(circle);
    }
    const wrap = node("div", "ob-ring");
    const label = node("span", "ob-ring-label", `${done} / ${STEPS.length}`);
    wrap.append(svg, label);
    return wrap;
  }

  function stepRow(step, index, done, current) {
    const row = node("li", `ob-step${done ? " is-done" : ""}${current ? " is-current" : ""}`);
    row.dataset.step = step.key;
    const mark = node("span", "ob-check", done ? "✓" : String(index + 1));
    mark.setAttribute("aria-hidden", "true");
    const copy = node("div", "ob-step-copy");
    const title = node("span", "ob-step-title", step.title);
    copy.append(title);
    if (done) {
      const sr = node("span", "ob-sr-only", "（已完成）");
      copy.append(sr);
    } else {
      copy.append(node("span", "ob-step-detail", step.detail));
      if (step.key === "weakness" && session?.isTrial) {
        copy.append(node("span", "ob-step-detail", "体验账号的 AI 额度很少，先看看页面里的说明就算完成。"));
      }
    }
    row.append(mark, copy);
    if (current) {
      const go = button("去做", "ob-go primary", () => goto(step.view));
      go.setAttribute("aria-label", `去做：${step.title}`);
      row.append(go);
    }
    return row;
  }

  function buildCard(model) {
    const { mode, steps, done, demoCount } = model;
    const demo = demoControls({ canLoad: overviewTotal === 0, demoCount, returnView: "home" });
    if (mode === "demo") {
      const bar = node("div", "ob-slim");
      bar.append(node("p", "ob-slim-title", "示例记录"), demo);
      return [bar];
    }
    const head = node("div", "ob-head");
    const copy = node("div", "ob-head-copy");
    const title = node("h3", "ob-title", mode === "celebrate" ? "第一轮完成 · 你已经走完了整个流程" : "开始之前 · 3 步走完第一轮");
    title.id = "ob-title";
    copy.append(node("p", "ob-eyebrow", mode === "celebrate" ? "ALL CLEAR" : "FIRST ROUND"), title);
    if (mode === "celebrate") {
      copy.append(node("p", "ob-sub", "记录、复习、看分析，一轮走完了。这张卡片接下来会自动收起。"));
    }
    head.append(ring(done), copy);
    if (mode === "celebrate") return [head];
    const list = node("ol", "ob-steps");
    const firstOpen = steps.findIndex((value) => !value);
    STEPS.forEach((step, index) => list.append(stepRow(step, index, steps[index], index === firstOpen)));
    const foot = node("div", "ob-foot");
    foot.append(button("不再显示", "ob-dismiss", () => {
      writeFlag(DISMISS_KEY);
      paint();
    }));
    return [head, list, demo, foot];
  }

  function currentModel() {
    const demoCount = records ? records.filter(isDemo).length : 0;
    const steps = deriveSteps({ total: overviewTotal, records, weaknessSeen: readFlag(WEAKNESS_KEY) });
    const mode = decideMode({
      total: overviewTotal, steps, dismissed: readFlag(DISMISS_KEY), sawIncomplete,
      demoCount, flashText: Boolean(flash || job),
    });
    if (celebrating) return { mode: "celebrate", steps, done: steps.filter(Boolean).length, demoCount };
    return { mode, steps, done: steps.filter(Boolean).length, demoCount };
  }

  function removeCard() {
    window.clearTimeout(celebrateTimer);
    celebrateTimer = 0;
    celebrating = false;
    card?.remove();
    card = null;
  }

  function paint(focus) {
    const host = $("#home-page");
    if (!host || !session) return;
    const model = currentModel();
    if (model.mode === "none") {
      removeCard();
      return;
    }
    if (model.mode === "checklist" && model.done < STEPS.length) sawIncomplete = true;
    card = host.querySelector("#onboarding-card") || card;
    if (!card) {
      card = node("section", "ob-card");
      card.id = "onboarding-card";
    }
    card.className = `ob-card ob-${model.mode}`;
    card.dataset.mode = model.mode;
    if (model.mode === "demo") card.removeAttribute("aria-labelledby");
    else card.setAttribute("aria-labelledby", "ob-title");
    card.replaceChildren(...buildCard(model));
    if (host.firstChild !== card) host.prepend(card);
    if (model.mode === "celebrate" && !celebrating) {
      celebrating = true;
      writeFlag(DISMISS_KEY); // 祝贺只出现一次
      const ticket = epoch;
      celebrateTimer = window.setTimeout(() => {
        celebrateTimer = 0;
        celebrating = false;
        if (ticket === epoch) paint();
      }, CELEBRATE_MS);
    }
    if (focus) card.querySelector(`[data-ob="${focus}"]`)?.focus();
  }

  /** 示例任务进度变化后，重画所有显示着示例控制条的位置。 */
  function rerender(focus) {
    if (card?.isConnected) paint(focus);
    for (const surface of [...surfaces]) {
      if (!surface.block.isConnected) surfaces.delete(surface);
      else surface.fill(focus);
    }
  }

  /* ---------- 空状态的"下一步" ---------- */
  function clearNext(container) {
    for (const surface of [...surfaces]) {
      if (surface.container === container) {
        surface.block.remove();
        surfaces.delete(surface);
      }
    }
    container.querySelector(".ob-next")?.remove();
  }

  async function emptyNext(kind, container, context) {
    if (!container) return;
    clearNext(container);
    if (!api || !context || context.show === false) return;
    const ticket = epoch;
    let total = context.total;
    if (!Number.isInteger(total)) {
      try {
        total = (await fetchRecords(ticket)).length;
      } catch {
        return; // 取不到数就不加按钮，原有说明文字照常显示
      }
    }
    if (ticket !== epoch || !container.isConnected || container.querySelector(".ob-next")) return;

    const view = context.view || kind;
    const block = node("div", "ob-next");
    block.dataset.kind = kind;
    const fill = (focus) => {
      const items = [];
      const actions = node("div", "ob-next-actions");
      let text;
      let allowDemo = false;
      if (kind === "today") {
        if (total > 0) {
          text = "今天没有要复习的，去看看薄弱点。";
          actions.append(button("去看薄弱点分析", "primary", () => goto("insights")), button("新增记录", "", () => goto("new")));
        } else {
          text = "还没有任何记录，先去新增第一条错题。";
          actions.append(button("去新增第一条错题", "primary", () => goto("new")));
          allowDemo = true;
        }
      } else if (kind === "all") {
        text = "还没有记录，新增一条，或者先载入几条示例看看长什么样。";
        actions.append(button("新增记录", "primary", () => goto("new")));
        allowDemo = true;
      } else if (kind === "mastery") {
        text = "有了记录，曲线才画得出来。";
        actions.append(button("去新增记录", "primary", () => goto("new")));
        allowDemo = true;
      } else {
        const missing = Math.max(0, (context.minimum || 0) - (context.count || 0));
        text = missing > 0 ? `还差 ${missing} 条易错点就够了，先去记录几条。` : "先去记录几条易错点。";
        actions.append(button("去新增记录", "primary", () => goto("new")));
      }
      items.push(node("p", "ob-next-text", text), actions);
      if (allowDemo && total === 0) {
        items.push(demoControls({ canLoad: true, demoCount: 0, returnView: view, compact: true }));
      } else if (job) {
        items.push(demoControls({ canLoad: false, demoCount: 0, returnView: view, compact: true }));
      }
      block.replaceChildren(...items);
      if (focus) block.querySelector(`[data-ob="${focus}"]`)?.focus();
    };
    fill();
    container.append(block);
    surfaces.add({ container, block, fill });
  }

  /* ---------- 总览渲染完之后 ---------- */
  async function onHomeRendered(detail) {
    const overview = detail?.overview;
    const who = detail?.user;
    if (!api || !overview || !who) return;
    const ticket = epoch;
    const seq = ++renderSeq;
    session = { userId: who.id, isTrial: Boolean(who.is_trial) };
    overviewTotal = Number.isInteger(overview.total_mistakes) ? overview.total_mistakes : 0;
    // 老用户不必读整张记录表；只有"还可能有示例"（本地留过标记）时才取一次。
    let list = [];
    if (overviewTotal > 0 && (overviewTotal < OLD_USER_RECORDS || readFlag(DEMO_HINT_KEY))) {
      try {
        list = await fetchRecords(ticket);
      } catch {
        list = null;
      }
    }
    if (ticket !== epoch || seq !== renderSeq) return;
    records = list;
    settling = false;
    if (list === null && overviewTotal < OLD_USER_RECORDS) {
      removeCard(); // 读不到记录就没法判断第二步，宁可不显示
      return;
    }
    if (celebrating) { // 再次回到总览：祝贺已经看过了，收起
      window.clearTimeout(celebrateTimer);
      celebrateTimer = 0;
      celebrating = false;
    }
    paint();
    flash = "";
  }

  document.addEventListener("app:home-rendered", (event) => { onHomeRendered(event.detail); });
  document.addEventListener("app:view-changed", (event) => {
    currentView = event.detail?.view || "";
    if (currentView === "insights") writeFlag(WEAKNESS_KEY);
  });

  function reset(user) {
    epoch += 1;
    renderSeq += 1;
    session = user && user.id !== undefined ? { userId: user.id, isTrial: Boolean(user.is_trial) } : null;
    overviewTotal = 0;
    records = null;
    sawIncomplete = false;
    job = null;
    confirming = false;
    settling = false;
    flash = "";
    currentView = "";
    removeCard();
    for (const surface of surfaces) surface.block.remove();
    surfaces.clear();
  }

  window.Onboarding = {
    configure(options) { api = options?.api || null; },
    reset,
    emptyNext,
    demoPrefix: DEMO_PREFIX,
    demos: () => DEMOS.map((demo) => ({ ...demo, mistakes: [demo.mistake] })),
    deriveSteps,
    decideMode,
  };
})();
