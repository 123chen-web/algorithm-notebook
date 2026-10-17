"use strict";

/* 待分类箱：把还没归类的错题“丢进”套路桶的小游戏（水墨 × 红笔批改风）。
   对外契约：window.Inbox = { configure({ api, getUser, getEpoch }), load(), reset(), showPatternResults(patternId, title) }。
   - 接口：GET /api/roadmap/inbox（待分类）、GET /api/roadmap（各桶里已有几道）、
     PUT /api/problems/{id}/patterns（归类，全量替换）、GET /api/roadmap/patterns/{id}/problems（分类结果）；
   - 迟到响应按登录代次（getEpoch()）、账号（getUser().id）、请求序号丢弃（ticket()/current()，同 roadmap.js）；
   - 写入是“先动界面再发请求”，失败或离线时静默回滚；
   - 三种操作方式：Pointer Events 拖拽、点选（点卡再点桶）、键盘（空格拿起、方向键移动、回车放下、Esc 取消），
     每一步用 aria-live 播报；绝不使用 HTML5 原生拖拽；
   - 一切来自服务器的文字都用 textContent；颜色只走主题变量；动画只用 transform / opacity，
     全部包在 @media (prefers-reduced-motion: no-preference) 里。 */
(() => {
  const node = (tag, className, text) => {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  };
  // 用 querySelector 取固定节点：假浏览器里 #id 找不到会自动现造一个挂到 body 上。
  const $ = (selector) => document.querySelector(selector);

  let hooks = null;
  let generation = 0;
  let sequence = 0;

  let catalog = null;
  let catalogPromise = null;

  let queue = [];        // 待分类的题，按展示顺序（队首 = 当前题）
  let total = 0;         // 服务端总数
  let bins = [];         // [{ category, count }]
  let remaining = 0;     // 还剩几道（本次会话的本地计数）
  let drag = null;       // { pointerId, x0, y0, moved, bin }
  let selected = null;   // 点选模式：选中的 problem_id
  let keyHeld = null;    // 键盘模式：{ problemId, binIndex }
  let undoInfo = null;   // { problemId, category, timer }
  let mode = "game";     // "game" | "results"
  let reducedMotion = false;

  /* ---------- 迟到响应守卫 ---------- */

  function ticket() {
    sequence += 1;
    return { sequence, generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }

  function current(request) {
    const user = hooks?.getUser();
    return Boolean(hooks && user && request.generation === generation
      && request.sequence === sequence
      && request.epoch === hooks.getEpoch() && request.userId === user.id);
  }

  /* ---------- 数据 ---------- */

  async function loadCatalog() {
    if (catalog) return catalog;
    if (!catalogPromise) {
      catalogPromise = fetch("/static/data/patterns.json", { credentials: "omit" })
        .then((response) => {
          if (!response.ok) throw new Error(String(response.status));
          return response.json();
        });
    }
    catalog = await catalogPromise;
    return catalog;
  }

  function categoriesOf(list) {
    const seen = new Set();
    const out = [];
    for (const pattern of list) {
      if (!seen.has(pattern.category)) {
        seen.add(pattern.category);
        out.push(pattern.category);
      }
    }
    return out.sort((a, b) => a.localeCompare(b, "zh"));
  }

  function patternsOfCategory(list, category) {
    return list.filter((pattern) => pattern.category === category)
      .sort((a, b) => a.title.localeCompare(b.title, "zh"));
  }

  /* ---------- 小组件 ---------- */

  function announce(text) {
    const live = $("#inbox-live");
    if (live) live.textContent = text;
  }

  function setStatus(text, { retry = false } = {}) {
    $("#inbox-status").textContent = text;
    $("#inbox-retry").hidden = !retry;
  }

  function renderRemaining() {
    const label = $("#inbox-remaining");
    if (mode !== "game") {
      label.hidden = true;
      return;
    }
    label.hidden = false;
    label.textContent = remaining > 0 ? `还剩 ${remaining} 道` : "";
  }

  function sourceLabel(url) {
    if (!url) return "";
    try {
      const host = new URL(url).hostname.replace(/^www\./, "");
      return host;
    } catch {
      return "";
    }
  }

  /* 桶的小图标：三枚印章风的简笔（匣子 / 卷轴 / 算筹），按分类名哈希取一个。 */
  const BIN_GLYPHS = [
    [ // 匣子
      { tag: "rect", attrs: { x: "5", y: "8", width: "14", height: "10", rx: "1.5" } },
      { tag: "path", attrs: { d: "M5 11h14M9 8v10" } },
    ],
    [ // 卷轴
      { tag: "path", attrs: { d: "M6 5h9l3 3v11H6z" } },
      { tag: "path", attrs: { d: "M15 5v3h3M9 12h6M9 15h6" } },
    ],
    [ // 算筹
      { tag: "path", attrs: { d: "M4 7h16M4 12h16M4 17h16" } },
      { tag: "path", attrs: { d: "M8 4v6M16 9v6M11 14v6" } },
    ],
  ];

  function glyphFor(category) {
    let hash = 0;
    for (const char of category) hash = (hash * 31 + char.codePointAt(0)) >>> 0;
    return BIN_GLYPHS[hash % BIN_GLYPHS.length];
  }

  function binIcon(category) {
    const namespace = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(namespace, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    svg.classList.add("inbox-bin-icon");
    // 逐个建 SVG 元素，路径只来自内置表，不拼字符串 HTML。
    for (const { tag, attrs } of glyphFor(category)) {
      const el = document.createElementNS(namespace, tag);
      for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, value);
      el.setAttribute("fill", "none");
      el.setAttribute("stroke", "currentColor");
      el.setAttribute("stroke-width", "1.8");
      el.setAttribute("stroke-linecap", "round");
      el.setAttribute("stroke-linejoin", "round");
      svg.appendChild(el);
    }
    return svg;
  }

  /* ---------- 渲染：桶 ---------- */

  function suggestedCategories(item) {
    const set = new Set();
    for (const suggestion of item.suggestions || []) {
      const pattern = (catalog || []).find((p) => p.id === suggestion.pattern_id);
      if (pattern) set.add(pattern.category);
    }
    return set;
  }

  function renderBins() {
    const wrap = $("#inbox-bins");
    const item = queue[0];
    const glowing = item ? suggestedCategories(item) : new Set();
    wrap.replaceChildren(...bins.map((bin) => {
      const button = node("button", "inbox-bin");
      button.type = "button";
      button.dataset.category = bin.category;
      if (glowing.has(bin.category)) button.classList.add("inbox-bin--suggested");
      button.setAttribute("aria-label", `桶「${bin.category}」，已有 ${bin.count} 道`);
      button.append(binIcon(bin.category));
      button.append(node("span", "inbox-bin-name", bin.category));
      button.append(node("span", "inbox-bin-count", String(bin.count)));
      button.addEventListener("click", () => onBinClick(bin.category, button));
      button.addEventListener("keydown", onBinKey);
      return button;
    }));
  }

  function binElement(category) {
    return [...document.querySelectorAll("#inbox-bins .inbox-bin")]
      .find((button) => button.dataset.category === category) || null;
  }

  /* ---------- 渲染：卡片 ---------- */

  function renderStack() {
    const stack = $("#inbox-card-stack");
    stack.replaceChildren();
    const shown = queue.slice(0, 3);
    shown.forEach((item, index) => {
      const card = node("article", "inbox-card");
      card.dataset.problemId = String(item.problem_id);
      card.style.setProperty("--stack-index", String(index));
      card.tabIndex = index === 0 ? 0 : -1;
      card.setAttribute("aria-label", `待分类的题：${item.title}`);
      if (selected === item.problem_id) card.classList.add("inbox-card--selected");
      if (keyHeld && keyHeld.problemId === item.problem_id) card.classList.add("inbox-card--held");

      const head = node("div", "inbox-card-head");
      head.append(node("span", "inbox-card-eyebrow", "待批的卷子"));
      const skip = node("button", "inbox-skip", "跳过");
      skip.type = "button";
      skip.setAttribute("aria-label", `跳过《${item.title}》，放回牌堆底部`);
      skip.addEventListener("click", (event) => {
        event.stopPropagation();
        skipCurrent();
      });
      head.append(skip);

      card.append(head);
      card.append(node("h3", "inbox-card-title", item.title));
      if (item.snippet) card.append(node("p", "inbox-card-snippet", item.snippet));
      const meta = node("div", "inbox-card-meta");
      const host = sourceLabel(item.source_url);
      if (host) meta.append(node("span", "inbox-source", host));
      if (item.tags && item.tags.length) {
        meta.append(node("span", "inbox-tags", item.tags.slice(0, 3).join(" · ")));
      }
      if (meta.children.length) card.append(meta);

      if (index === 0) {
        card.addEventListener("pointerdown", onCardPointerDown);
        card.addEventListener("click", onCardClick);
        card.addEventListener("keydown", onCardKey);
      }
      stack.append(card);
    });
    renderRemaining();
  }

  /* ---------- 写入 ---------- */

  async function putPatterns(problemId, patternIds) {
    const request = ticket();
    try {
      await hooks.api(`/api/problems/${problemId}/patterns`, {
        method: "PUT",
        body: JSON.stringify({ pattern_ids: patternIds }),
      });
      return current(request);
    } catch {
      return false;
    }
  }

  async function commitClassification(problemId, patternIds, categoryLabel, { silent = false } = {}) {
    const item = queue.find((entry) => entry.problem_id === problemId);
    if (!item) return;
    clearUndo();
    // 先动界面：卡片离场。
    queue = queue.filter((entry) => entry.problem_id !== problemId);
    remaining = Math.max(0, remaining - 1);
    selected = null;
    keyHeld = null;
    renderStack();
    renderBins();
    const ok = await putPatterns(problemId, patternIds);
    if (!ok) {
      // 失败回滚：卡片回到队首。
      queue.unshift(item);
      remaining += 1;
      renderStack();
      renderBins();
      announce(`《${item.title}》没能放进「${categoryLabel}」，已放回牌堆`);
      if (!silent) showToast(`没能放进「${categoryLabel}」，已放回`, null);
      return;
    }
    const bin = bins.find((entry) => entry.category === categoryLabel);
    if (bin) {
      bin.count += 1;
      renderBins();
      stampBin(categoryLabel);
    }
    if (!queue.length) {
      showDoneSeal();
    } else {
      showToast(`已放入「${categoryLabel}」`, () => undoLast(item, categoryLabel));
    }
    announce(`《${item.title}》已放入「${categoryLabel}」${remaining ? `，还剩 ${remaining} 道` : ""}`);
  }

  /* ---------- 撤销 ---------- */

  function clearUndo() {
    if (undoInfo && undoInfo.timer) clearTimeout(undoInfo.timer);
    undoInfo = null;
    const toast = $("#inbox-toast");
    toast.hidden = true;
    toast.replaceChildren();
  }

  async function undoLast(item, categoryLabel) {
    clearUndo();
    // 撤销 = 全量替换回 []（这道题本来就没归类）。
    const ok = await putPatterns(item.problem_id, []);
    queue.unshift(item);
    remaining += 1;
    const bin = bins.find((entry) => entry.category === categoryLabel);
    if (bin && ok) {
      bin.count = Math.max(0, bin.count - 1);
      renderBins();
    }
    renderStack();
    if (!ok) {
      showToast("撤销没成功，卡片已放回，再点一次撤销试试", () => undoLast(item, categoryLabel));
    }
    announce(`已撤销，《${item.title}》回到了牌堆`);
  }

  function showToast(text, onUndo) {
    const toast = $("#inbox-toast");
    toast.replaceChildren();
    toast.append(node("span", "", text));
    if (onUndo) {
      const button = node("button", "inbox-undo", "撤销");
      button.type = "button";
      button.addEventListener("click", () => { void onUndo(); });
      toast.append(button);
      undoInfo = {
        timer: setTimeout(() => { clearUndo(); }, 5000),
      };
    }
    toast.hidden = false;
  }

  function skipCurrent() {
    const [first, ...rest] = queue;
    if (!first) return;
    queue = [...rest, first];
    selected = null;
    keyHeld = null;
    renderStack();
    renderBins();
    announce(`《${first.title}》已放回牌堆底部`);
  }

  /* ---------- 盖章 ---------- */

  function stampBin(category) {
    const bin = binElement(category);
    if (!bin) return;
    const seal = node("span", "inbox-seal", "入");
    seal.setAttribute("aria-hidden", "true");
    const blob = node("span", "inbox-ink", "");
    blob.setAttribute("aria-hidden", "true");
    bin.append(seal, blob);
    const clean = () => {
      seal.remove();
      blob.remove();
      bin.classList.remove("inbox-bin--stamped");
    };
    bin.classList.add("inbox-bin--stamped");
    if (reducedMotion) {
      clean();
      return;
    }
    setTimeout(clean, 1000);
  }

  function showDoneSeal() {
    const area = $("#inbox-arena");
    area.hidden = true;
    const done = $("#inbox-done");
    done.hidden = false;
    done.replaceChildren();
    const seal = node("div", "inbox-done-seal", "毕");
    seal.setAttribute("aria-hidden", "true");
    done.append(
      seal,
      node("p", "inbox-done-text", "卷子都批完了，喝杯茶吧。"),
    );
    announce("全部题都分好类了");
  }

  function hideDoneSeal() {
    $("#inbox-done").hidden = true;
    $("#inbox-arena").hidden = false;
  }

  /* ---------- 选具体套路的气泡 ---------- */

  function closeBubble() {
    const bubble = $("#inbox-bubble");
    bubble.hidden = true;
    bubble.replaceChildren();
  }

  /* “让 AI 猜一下”：点一下才调用，花 1 次 AI 额度；AI 只能从路线清单里选，结果只是建议，
     点哪个才归哪个。失败只在气泡里说一句，不影响手动选择。 */
  function aiSuggestBox(item) {
    const box = node("div", "inbox-bubble-ai");
    const button = node("button", "inbox-bubble-ai-btn", "让 AI 帮我猜一下（用 1 次 AI 额度）");
    button.type = "button";
    const status = node("p", "inbox-bubble-ai-status", "");
    status.setAttribute("aria-live", "polite");
    const list = node("ul", "inbox-bubble-list inbox-bubble-ai-list");
    box.append(button, status, list);
    button.addEventListener("click", async () => {
      if (button.disabled) return;
      button.disabled = true;
      status.textContent = "AI 正在看这道题…";
      const request = ticket();
      try {
        const data = await hooks.api("/api/roadmap/ai-suggest", {
          method: "POST",
          body: JSON.stringify({ problem_id: item.problem_id }),
        });
        if (!current(request) || !box.isConnected) return;
        const picks = Array.isArray(data && data.suggestions) ? data.suggestions : [];
        list.replaceChildren();
        if (!picks.length) {
          status.textContent = "AI 也拿不准，自己挑一个吧。";
          return;
        }
        const remaining = Number.isInteger(data.ai_daily_remaining) ? `，今天还剩 ${data.ai_daily_remaining} 次 AI 额度` : "";
        status.textContent = `AI 的建议（点哪个就归到哪个）${remaining}`;
        for (const pick of picks) {
          const row = node("li", "");
          const choose = node("button", "inbox-bubble-pick inbox-bubble-pick--ai");
          choose.type = "button";
          choose.append(node("span", "", `${pick.title}（${pick.category}）`));
          choose.append(node("span", "inbox-bubble-tag", "AI 建议"));
          if (pick.reason) choose.append(node("span", "inbox-bubble-reason", pick.reason));
          choose.setAttribute("aria-label", `归到套路《${pick.title}》，AI 的理由：${pick.reason || "无"}`);
          choose.addEventListener("click", () => {
            closeBubble();
            void commitClassification(item.problem_id, [pick.pattern_id], pick.category);
          });
          row.append(choose);
          list.append(row);
        }
      } catch (error) {
        if (!current(request)) return;
        status.textContent = (error && error.message) || "AI 暂时不可用，自己挑一个吧。";
        button.disabled = false;
      }
    });
    return box;
  }

  function openBubble(item, category) {
    const bubble = $("#inbox-bubble");
    bubble.replaceChildren();
    bubble.append(node("p", "inbox-bubble-title", `放进「${category}」，具体是哪个套路？`));
    const list = node("ul", "inbox-bubble-list");
    const patterns = patternsOfCategory(catalog || [], category);
    const hitIds = new Set((item.suggestions || []).map((s) => s.pattern_id));
    const ordered = [
      ...patterns.filter((p) => hitIds.has(p.id)),
      ...patterns.filter((p) => !hitIds.has(p.id)),
    ];
    for (const pattern of ordered) {
      const row = node("li", "");
      const button = node("button", "inbox-bubble-pick");
      button.type = "button";
      button.append(node("span", "", pattern.title));
      if (hitIds.has(pattern.id)) {
        button.classList.add("inbox-bubble-pick--hit");
        const tag = node("span", "inbox-bubble-tag", "系统推荐");
        button.append(tag);
      }
      button.setAttribute("aria-label", `归到套路《${pattern.title}》`);
      button.addEventListener("click", () => {
        closeBubble();
        void commitClassification(item.problem_id, [pattern.id], category);
      });
      row.append(button);
      list.append(row);
    }
    if (!ordered.length) {
      list.append(node("li", "inbox-bubble-empty", "这个大类下暂时没有套路"));
    }
    bubble.append(list);
    bubble.append(aiSuggestBox(item));
    const defer = node("button", "inbox-bubble-defer", "只记大类，具体以后再说");
    defer.type = "button";
    defer.addEventListener("click", () => {
      closeBubble();
      // “以后再说” = 卡片回牌堆底部，不调接口、不记状态。
      skipCurrent();
      showToast("先放回牌堆，具体套路以后再定", null);
    });
    const cancel = node("button", "inbox-bubble-cancel", "不放了");
    cancel.type = "button";
    cancel.addEventListener("click", () => {
      closeBubble();
      // 回到队首，什么都没发生。
      renderStack();
      renderBins();
    });
    bubble.append(defer, cancel);
    bubble.hidden = false;
    const first = bubble.querySelector(".inbox-bubble-pick");
    if (first) first.focus();
  }

  /* ---------- 放进桶 ---------- */

  function dropIntoBin(problemId, category) {
    const item = queue.find((entry) => entry.problem_id === problemId);
    if (!item) return;
    openBubble(item, category);
    if (!(item.suggestions || []).length) {
      // 建议为空：标题换一句诚实的，不伪装成“推荐”。
      const title = $("#inbox-bubble .inbox-bubble-title");
      if (title) title.textContent = `「${category}」里没有系统推荐，自己挑一个套路吧`;
    }
  }

  /* ---------- 点选模式 ---------- */

  function onCardClick(event) {
    if (drag && drag.moved) return; // 刚拖完，不触发点选
    const card = event.currentTarget;
    const problemId = Number(card.dataset.problemId);
    if (selected === problemId) {
      selected = null;
      card.classList.remove("inbox-card--selected");
      announce("已取消选中");
      return;
    }
    selected = problemId;
    renderStack();
    const item = queue.find((entry) => entry.problem_id === problemId);
    announce(`已选中《${item ? item.title : ""}》，再点一个桶放进去`);
  }

  function onBinClick(category, button) {
    if (keyHeld) {
      // 键盘拿起后直接用回车放下；点桶也算一种放下。
      const problemId = keyHeld.problemId;
      keyHeld = null;
      dropIntoBin(problemId, category);
      return;
    }
    if (selected == null) {
      // 没选中卡片：桶只是获得焦点，不做事。
      button.focus();
      return;
    }
    const problemId = selected;
    selected = null;
    dropIntoBin(problemId, category);
  }

  /* ---------- 拖拽（Pointer Events 状态机） ---------- */

  function binUnderPoint(x, y, fallback) {
    if (typeof document.elementFromPoint === "function") {
      const hit = document.elementFromPoint(x, y);
      const bin = hit && hit.closest ? hit.closest(".inbox-bin") : null;
      if (bin) return bin;
    }
    return fallback && fallback.closest ? fallback.closest(".inbox-bin") : null;
  }

  function onCardPointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;
    const card = event.currentTarget;
    const problemId = Number(card.dataset.problemId);
    drag = { pointerId: event.pointerId, x0: event.clientX, y0: event.clientY, moved: false, bin: null, card, problemId };
    try {
      card.setPointerCapture(event.pointerId);
    } catch { /* 测试替身没有 capture 也能用 */ }
    card.classList.add("inbox-card--held");
    card.addEventListener("pointermove", onCardPointerMove);
    card.addEventListener("pointerup", onCardPointerUp, { once: true });
    card.addEventListener("pointercancel", onCardPointerCancel, { once: true });
  }

  function onCardPointerMove(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const dx = event.clientX - drag.x0;
    const dy = event.clientY - drag.y0;
    if (!drag.moved && Math.hypot(dx, dy) > 8) {
      drag.moved = true;
      selected = null; // 拖起来就不再是点选
    }
    if (!drag.moved) return;
    event.preventDefault();
    drag.card.style.transform = `translate(${dx}px, ${dy}px) rotate(${dx * 0.04}deg) scale(1.05)`;
    const bin = binUnderPoint(event.clientX, event.clientY, event.target);
    if (drag.bin && drag.bin !== bin) drag.bin.classList.remove("inbox-bin--hot");
    drag.bin = bin;
    if (bin) bin.classList.add("inbox-bin--hot");
  }

  function onCardPointerUp(event) {
    if (!drag || event.pointerId !== drag.pointerId) return;
    const finished = drag;
    cleanupDrag();
    if (!finished.moved) {
      // 没拖动：交给 click 处理点选。
      return;
    }
    if (finished.bin) {
      finished.bin.classList.remove("inbox-bin--hot");
      dropIntoBin(finished.problemId, finished.bin.dataset.category);
    } else {
      // 弹簧回弹。
      const card = finished.card;
      card.classList.add("inbox-card--return");
      card.style.transform = "";
      const done = () => {
        card.classList.remove("inbox-card--return");
        card.removeEventListener("transitionend", done);
      };
      card.addEventListener("transitionend", done);
      setTimeout(done, reducedMotion ? 0 : 350);
      announce("没放进桶，卡片弹回来了");
    }
  }

  function onCardPointerCancel() {
    if (!drag) return;
    const finished = drag;
    cleanupDrag();
    finished.card.style.transform = "";
    finished.card.classList.remove("inbox-card--held");
  }

  function cleanupDrag() {
    if (!drag) return;
    const { card, bin } = drag;
    card.classList.remove("inbox-card--held");
    card.removeEventListener("pointermove", onCardPointerMove);
    if (bin) bin.classList.remove("inbox-bin--hot");
    drag = null;
  }

  /* ---------- 键盘 ---------- */

  function onCardKey(event) {
    const card = event.currentTarget;
    const problemId = Number(card.dataset.problemId);
    if (event.key === " " || event.key === "Enter") {
      event.preventDefault();
      if (keyHeld && keyHeld.problemId === problemId) {
        keyHeld = null;
        renderStack();
        announce("已放下卡片");
        return;
      }
      const item = queue.find((entry) => entry.problem_id === problemId);
      const glowing = [...suggestedCategories(item)];
      const startIndex = glowing.length
        ? Math.max(0, bins.findIndex((b) => b.category === glowing[0]))
        : 0;
      keyHeld = { problemId, binIndex: startIndex };
      renderStack();
      focusBin(startIndex);
      announce(`已拿起《${item.title}》，当前在桶「${bins[startIndex] ? bins[startIndex].category : ""}」，按回车放下，Esc 取消`);
    } else if (event.key === "Escape" && keyHeld) {
      keyHeld = null;
      renderStack();
      announce("已取消拿起");
      card.focus();
    }
  }

  function focusBin(index) {
    const wrap = $("#inbox-bins");
    const buttons = [...wrap.querySelectorAll(".inbox-bin")];
    const button = buttons[index];
    if (button && typeof button.focus === "function") button.focus();
  }

  function onBinKey(event) {
    if (!keyHeld) return;
    const count = bins.length;
    if (!count) return;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      event.preventDefault();
      keyHeld.binIndex = (keyHeld.binIndex + 1) % count;
      focusBin(keyHeld.binIndex);
      announce(`当前在桶「${bins[keyHeld.binIndex].category}」，按回车放下`);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      event.preventDefault();
      keyHeld.binIndex = (keyHeld.binIndex - 1 + count) % count;
      focusBin(keyHeld.binIndex);
      announce(`当前在桶「${bins[keyHeld.binIndex].category}」，按回车放下`);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const problemId = keyHeld.problemId;
      const category = bins[keyHeld.binIndex].category;
      keyHeld = null;
      dropIntoBin(problemId, category);
    } else if (event.key === "Escape") {
      event.preventDefault();
      keyHeld = null;
      renderStack();
      announce("已取消拿起");
      const card = document.querySelector("#inbox-card-stack .inbox-card");
      if (card) card.focus();
    }
  }

  /* ---------- 分类结果（徽标点进来） ---------- */

  async function showPatternResults(patternId, title) {
    mode = "results";
    $("#inbox-arena").hidden = true;
    $("#inbox-done").hidden = true;
    const results = $("#inbox-results");
    const body = $("#inbox-results-body");
    results.hidden = false;
    body.replaceChildren();
    body.append(node("p", "inbox-results-title", `《${title}》的分类结果`));
    const list = node("ul", "inbox-results-list");
    body.append(list);
    setStatus("正在加载分类结果…");
    const request = ticket();
    try {
      const data = await hooks.api(`/api/roadmap/patterns/${encodeURIComponent(patternId)}/problems`);
      if (!current(request)) return;
      const problems = data.problems || [];
      list.replaceChildren(...problems.map((problem) => {
        const row = node("li", "inbox-results-row");
        row.append(node("span", "", problem.title));
        return row;
      }));
      if (!problems.length) {
        list.append(node("li", "inbox-results-empty", "这道套路下还没有归类好的题。在上面的待分类列表里给题目选上这个套路就行。"));
      }
      setStatus("");
      announce(`《${title}》的分类结果：${problems.length} 道题`);
    } catch {
      if (!current(request)) return;
      setStatus("分类结果加载失败，请稍后重试。", { retry: true });
    }
  }

  function backToGame() {
    mode = "game";
    $("#inbox-results").hidden = true;
    $("#inbox-arena").hidden = false;
    $("#inbox-done").hidden = queue.length > 0;
    renderStack();
    renderBins();
  }

  /* ---------- 空状态 ---------- */

  function renderEmpty(kind) {
    const stack = $("#inbox-card-stack");
    stack.replaceChildren();
    const empty = node("div", "inbox-empty");
    if (kind === "none") {
      empty.append(node("div", "inbox-empty-seal", "毕"));
      empty.append(node("p", "", "卷子都批完了，喝杯茶吧。"));
    }
    $("#inbox-bins").replaceChildren();
    renderRemaining();
  }

  /* ---------- 入口 ---------- */

  async function load() {
    mode = "game";
    $("#inbox-results").hidden = true;
    $("#inbox-arena").hidden = false;
    hideDoneSeal();
    closeBubble();
    clearUndo();
    const mine = ++generation;
    reducedMotion = typeof matchMedia === "function"
      && matchMedia("(prefers-reduced-motion: reduce)").matches;
    setStatus("正在打开待分类箱…");
    try {
      const list = await loadCatalog();
      if (mine !== generation) return;
      bins = categoriesOf(list).map((category) => ({ category, count: 0 }));
    } catch {
      if (mine !== generation) return;
      setStatus("套路库加载失败，请稍后重试。", { retry: true });
      return;
    }
    const request = ticket();
    let inbox, state;
    try {
      [inbox, state] = await Promise.all([
        hooks.api("/api/roadmap/inbox?limit=30&offset=0"),
        hooks.api("/api/roadmap"),
      ]);
    } catch {
      if (mine !== generation) return;
      setStatus("待分类箱加载失败，请稍后重试。", { retry: true });
      return;
    }
    if (!current(request)) return;
    // 各桶“已有几道”：按 pattern 的 mistake_counts 汇总到分类。
    const byPattern = new Map();
    for (const [patternId, count] of Object.entries(state.mistake_counts || {})) {
      byPattern.set(patternId, Number(count) || 0);
    }
    const countByCategory = new Map();
    for (const pattern of catalog) {
      const count = byPattern.get(pattern.id) || 0;
      if (count > 0) countByCategory.set(pattern.category, (countByCategory.get(pattern.category) || 0) + count);
    }
    bins = categoriesOf(catalog).map((category) => ({
      category,
      count: countByCategory.get(category) || 0,
    }));
    total = Number(inbox.total) || 0;
    queue = Array.isArray(inbox.items) ? [...inbox.items] : [];
    remaining = queue.length;
    selected = null;
    keyHeld = null;
    setStatus("");
    if (!queue.length) {
      showDoneSeal();
      announce("待分类箱是空的，卷子都批完了");
      return;
    }
    renderStack();
    renderBins();
    announce(`待分类箱：共 ${total} 道题，先分这一批 ${queue.length} 道`);
  }

  function reset() {
    generation += 1;
    queue = [];
    bins = [];
    total = 0;
    remaining = 0;
    drag = null;
    selected = null;
    keyHeld = null;
    mode = "game";
    clearUndo();
    closeBubble();
    $("#inbox-card-stack").replaceChildren();
    $("#inbox-bins").replaceChildren();
    $("#inbox-results").hidden = true;
    hideDoneSeal();
    setStatus("");
  }

  function bind() {
    $("#inbox-retry").addEventListener("click", () => { void load(); });
    $("#inbox-back").addEventListener("click", () => {
      if (typeof window.showView === "function") void window.showView("roadmap");
    });
    $("#inbox-back-results").addEventListener("click", backToGame);
  }

  let bound = false;

  window.Inbox = {
    configure(next) {
      hooks = next;
      if (!bound) {
        bind();
        bound = true;
      }
    },
    load, reset, showPatternResults,
    // 纯函数/状态，供行为测试直接断言
    __test: {
      get queue() { return queue; },
      get bins() { return bins; },
      get selected() { return selected; },
      get keyHeld() { return keyHeld; },
      setQueue(items) { queue = items; remaining = items.length; },
      setBins(list) { bins = list; },
      renderStack, renderBins, dropIntoBin, skipCurrent,
    },
  };
})();
