// static/app/review-detail.js — 复习：详情打开/错因/变体/编辑器/键盘（openMistake、renderMistakeText 等）。
// 由 static/app/review.js 拆分（C 收尾），纯重构、零行为变化。
// 先于 review-grade.js 加载：openMistake、clearDetail、rvfDetailKeydown 等被 renderDetail/接线代码调用；领域间调用保持顶层全局函数名直接调用。
"use strict";

async function openMistake(id) {
  rvfSelectionGeneration += 1;
  const isCurrent = rvfPageGuard();
  rvfClearDetail();
  const generation = rvfDetailGeneration;
  let item;
  let offlineDetail = false;
  try {
    item = await api(`/api/mistakes/${id}`);
  } catch (error) {
    // 断网时详情接口拉不到：只允许用预取队列里缓存过的题兜底，不编造没见过的数据。
    if (window.OfflineReview?.isNetworkError(error)) {
      item = window.OfflineReview?.cachedItem(id);
      if (item) offlineDetail = true;
    }
    if (!item) throw error;
  }
  if (!isCurrent() || generation !== rvfDetailGeneration || !["today", "all"].includes(view)) return;
  user.today = item.today;
  updateUserInfo();

  const current = rvfListItems.get(item.id);
  if (current) {
    // 列表与详情都保留同一条记录的较新版本，详情独有的历史仍可展示。
    if (current.version > item.version) item = { ...item, ...current };
    rvfListItems.set(item.id, { ...current, ...item });
    for (const sibling of rvfListItems.values()) {
      if (sibling.problem_id !== item.problem_id) continue;
      for (const key of ["progress", "problem_tags", "problem_due_count", "problem_mistakes"]) {
        if (Object.hasOwn(item, key)) sibling[key] = item[key];
      }
    }
    rvfRenderCards(item.today, item.id);
  }

  document.querySelectorAll(".record-button").forEach((button) => {
    const selected = Number(button.closest(".problem-record-card")?.dataset.problemId) === item.problem_id;
    button.classList.toggle("selected", selected);
    button.setAttribute("aria-pressed", String(selected));
  });

  renderDetail(item, { offline: offlineDetail });
}

const VARIANT_SECTION_PATTERN =
  /^【错因】\s*\n([\s\S]*?)\n【讲解】\s*\n([\s\S]*?)\n【核心知识点】\s*\n([\s\S]*?)\n【练习题】\s*\n([\s\S]*)$/;

/* AI 回答有用/没用反馈：只收布尔值。点后变为"已记录，谢谢"；
   离线或失败时静默恢复按钮；晚到响应按登录代次/账号丢弃。 */

function renderVariantDescription(text) {  const match = text.trim().match(VARIANT_SECTION_PATTERN);
  if (!match) {
    // 新变体只含单题正文（编程题还带样例）；也兼容更早的纯文本题目。
    return element("pre", text, "prose");
  }
  const [, summary, explanation, knowledge, question] = match;
  const wrap = element("div", "", "variant-sections");
  for (const [label, content] of [
    ["错因", summary], ["讲解", explanation],
    ["核心知识点", knowledge], ["练习题", question],
  ]) {
    wrap.append(element("h4", label), element("p", content.trim(), "multiline"));
  }
  return wrap;
}

function renderVariant(variant, isCodeZone) {
  const box = document.createElement("details");
  box.className = "variant";
  box.open = variant.result === "unattempted";

  const summary = element(
    "summary",
    `变体 #${variant.id} · ${resultLabels[variant.result]}`
  );
  // AI 出的题没法自动验证对错和难度：标出来，提醒先核对（10/14 评估建议）。
  summary.append(element("span", "AI 出题 · 未验证", "variant-badge"));
  const unverified = element(
    "p",
    "这道题是 AI 按你的错因出的，题意、样例和参考答案都没有经过自动验证。做之前先核对一遍；拿不准时以原题和真实 OJ 为准。",
    "variant-unverified"
  );
  const savedAt = element(
    "p",
    variant.result_updated_at
      ? `结果保存于：${timestamp(variant.result_updated_at)}`
      : "做完后，在下面记下这次的结果。",
    "muted"
  );

  const form = document.createElement("form");
  // 旧数学题没有参考答案，继续允许用户自行记录结果。
  const autoJudge = !isCodeZone && Boolean(variant.expected_answer);
  let result;
  if (!autoJudge) {
    result = document.createElement("select");
    for (const [value, label] of Object.entries(resultLabels)) {
      const option = element("option", label);
      option.value = value;
      result.append(option);
    }
    result.value = variant.result;
    form.append(field("练习结果", result));
  }

  let code;
  let answer;
  if (isCodeZone) {
    code = textarea(variant.answer_code, 40000, 7, true);
    form.append(field("我的解答代码 · 仅保存，不运行", code));
  } else {
    answer = document.createElement("input");
    answer.name = "answer";
    answer.value = variant.answer || "";
    answer.maxLength = 500;
    answer.required = autoJudge;
    answer.placeholder = "多个数值用英文逗号分隔，例如：1, 1, 4";
    form.append(field("我的答案", answer));
    if (!autoJudge) {
      form.append(element("p", "这道旧练习题没有参考答案，请自行记录结果。", "muted"));
    }
  }
  const judgment = element("p");
  const reference = element("p", "", "multiline");

  function updateJudgment(updated) {
    const hasSavedResult = autoJudge && Boolean(updated.result_updated_at);
    judgment.hidden = !hasSavedResult;
    reference.hidden = !hasSavedResult;
    judgment.textContent = updated.result === "solved"
      ? "系统判定：正确"
      : updated.result === "failed" ? "系统判定：错误" : "系统判定：待作答";
    reference.textContent = hasSavedResult ? `参考答案：${updated.expected_answer}` : "";
  }
  updateJudgment(variant);

  const save = element("button", "保存练习结果", "primary");
  save.type = "submit";
  form.append(save);

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    run(async () => {
      const updated = await api(`/api/variants/${variant.id}/result`, {
        method: "PUT",
        body: JSON.stringify(isCodeZone
          ? { result: result.value, answer_code: code.value }
          : { result: result ? result.value : variant.result, answer: answer.value }),
      });
      variant = updated;
      summary.textContent =
        `变体 #${updated.id} · ${resultLabels[updated.result]}`;
      savedAt.textContent =
        `结果保存于：${timestamp(updated.result_updated_at)}`;
      updateJudgment(updated);
      message("练习结果已保存。这次练习不会改变复习日期。");
    });
  });

  box.append(
    summary,
    unverified,
    renderVariantDescription(variant.description),
    savedAt,
    form,
    judgment,
    reference,
    renderAiFeedback("variant", variant.id)
  );
  return box;
}

function renderMistakeText(item) {
  const pendingReason = window.PendingReason?.render(item);
  if (pendingReason) return pendingReason;
  const wrap = element("div", "", "mistake-text");

  function readOnly() {
    const editBtn = element("button", "编辑这条错因");
    editBtn.type = "button";
    editBtn.addEventListener("click", editForm);
    const text = item.description
      ? element("p", item.description, "multiline")
      : element("p", "还没有错因描述，点击下面「诊断错因并出两道新题」让 AI 帮你反推。", "muted");
    wrap.replaceChildren(text, editBtn);
  }

  function editForm() {
    const input = textarea(item.description, 2000, 4);
    const counterexample = textarea(item.counterexample || "", 1000, 3, true);
    counterexample.placeholder = "最小反例（可选）：一个能让原代码出错的最小输入，例如：n=1 时输出 0，应为 1";
    const save = element("button", "保存修改", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    form.append(field("哪里容易错，为什么会错？（可选）", input), field("最小反例（可选）", counterexample), save, cancel);
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        const updated = await api(`/api/mistakes/${item.id}`, {
          method: "PUT",
          body: JSON.stringify({
            description: input.value,
            counterexample: counterexample.value,
            version: item.version,
          }),
        });
        message("易错点描述已更新。");
        await openMistake(updated.id);
      });
    });

    wrap.replaceChildren(form);
  }

  readOnly();
  return wrap;
}

// 题目来源链接：在新标签页打开，只放 http(s) 地址。

function sourceLinkRow(url) {
  const row = element("p", "", "problem-source");
  const link = document.createElement("a");
  link.href = url;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "打开题目链接";
  row.append(link);
  return row;
}

function renderProblemEditor(item) {
  const wrap = element("div", "", "problem-editor");

  function readOnly() {
    const editBtn = element("button", "编辑题目、代码和思路");
    editBtn.type = "button";
    editBtn.addEventListener("click", editForm);
    // 判题类型：填了就显示，中文名从词表来。
    const verdictRow = item.verdict
      ? [element("p", `判题结果：${window.TagEditor?.verdictLabel(item.verdict) || item.verdict}`, "problem-verdict")]
      : [];
    wrap.replaceChildren(
      ...(/^https?:\/\//i.test(item.source_url || "") ? [sourceLinkRow(item.source_url)] : []),
      ...verdictRow,
      element("h4", "当时的思路"),
      window.Capture ? window.Capture.renderThinking(item.thinking) : element("p", item.thinking, "multiline"),
      element("h4", codeZones.has(item.zone) ? "当时的代码" : "当时的解题过程"),
      element("pre", item.code, "code"),
      ...(item.correct_code
        ? [element("h4", "正确代码"), element("pre", item.correct_code, "code")]
        : []),
      editBtn
    );
  }

  function editForm() {
    const title = document.createElement("input");
    title.value = item.title;
    title.maxLength = 200;
    title.required = true;

    const zoneSelect = document.createElement("select");
    zoneSelect.required = true;
    for (const zone of zones) {
      const option = element("option", zone);
      option.value = zone;
      zoneSelect.append(option);
    }
    zoneSelect.value = item.zone;

    const language = document.createElement("input");
    language.name = "language";
    language.value = item.language;
    language.maxLength = 40;
    const languageField = element("label", "编程语言", "");
    languageField.dataset.role = "language-field";
    languageField.append(language);

    // 判题类型：只在算法分区显示，其他分区隐藏。
    const verdictSelect = document.createElement("select");
    verdictSelect.name = "verdict";
    const verdictField = element("label", "判题结果", "");
    verdictField.dataset.role = "verdict-field";
    verdictField.append(verdictSelect);
    fillVerdictSelect(verdictSelect, item.verdict || "");

    const code = textarea(item.code, 40000, 10, true);
    const codeLabelText = element("span", "当时的代码");
    codeLabelText.dataset.role = "code-label";
    const codeField = element("label");
    codeField.append(codeLabelText, code);

    const thinking = textarea(item.thinking, 8000, 4);
    const correctCode = textarea(item.correct_code || "", 40000, 6, true);

    // 可选：这道错题属于哪些套路（最多 3 个）。套路清单是静态资源，异步拿到后
    // 再插入选择器；期间不显示这个字段，保存时也就不会带上 pattern_ids（服务端
    // 按"没传 = 保持不变"处理）。
    const patternHost = element("div", "");
    let patternPicker = null;
    window.PatternPicker?.load().then(() => {
      if (patternPicker || !patternHost.isConnected) return;
      patternPicker = window.PatternPicker.create({ selected: item.pattern_ids || [] });
      patternHost.replaceChildren(patternPicker.el);
    }).catch(() => {});

    const save = element("button", "保存题目信息", "primary");
    save.type = "submit";
    const cancel = element("button", "取消");
    cancel.type = "button";
    cancel.addEventListener("click", readOnly);

    const form = document.createElement("form");
    zoneSelect.addEventListener("change", () => applyZoneFieldMode(form, zoneSelect.value));
    form.append(
      field("标题", title),
      field("分区", zoneSelect),
      languageField,
      verdictField,
      codeField,
      field("正确代码（可选，自己补）", correctCode),
      field("思路", thinking),
      field("属于哪个套路（可选，最多 3 个）", patternHost),
      save,
      cancel
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      run(async () => {
        await api(`/api/problems/${item.problem_id}`, {
          method: "PUT",
          body: JSON.stringify({
            title: title.value,
            zone: zoneSelect.value,
            language: language.value,
            code: code.value,
            correct_code: correctCode.value,
            thinking: thinking.value,
            verdict: verdictSelect.value,
            // 选择器还没插进来时不带这个字段，服务端按"保持不变"处理；
            // 插进来之后以选择器的值为准，传 [] 就是清空。
            ...(patternPicker ? { pattern_ids: patternPicker.values() } : {}),
          }),
        });
        message("题目信息已更新。");
        await openMistake(item.id);
      });
    });

    wrap.replaceChildren(form);
    applyZoneFieldMode(form, zoneSelect.value);
  }

  readOnly();
  return wrap;
}

function clearDetail(
  title = "从一条易错点开始",
  description = "选中列表中的一条记录，先回忆如何避免这个错误，再对照笔记，给这次掌握程度打个分。"
) {
  rvfClearDetail();
  const empty = element("div", "", "empty-state");
  const symbol = element("span", "≡", "empty-symbol");
  symbol.setAttribute("aria-hidden", "true");
  empty.append(
    symbol,
    element("h3", title),
    element("p", description, "muted")
  );
  $("#detail").replaceChildren(empty);
}

// 复习完成提示：按服务端给的今天（user.today，core.time.today_for 口径）与
// result.due_date 算下次复习在几天后；算不出就只说"复习完成"。
function rvfReviewDoneText(result) {
  const dayNumber = (value) => {
    const parts = String(value || "").split("-").map(Number);
    if (parts.length !== 3 || parts.some((part) => !Number.isInteger(part))) return NaN;
    return Date.UTC(parts[0], parts[1] - 1, parts[2]);
  };
  const diff = Math.round((dayNumber(result?.due_date) - dayNumber(user?.today)) / 86400000);
  if (!Number.isFinite(diff) || diff < 1) return "✅ 复习完成";
  return `✅ 复习完成，下次在 ${diff} 天后`;
}

// 三种复习方式共用的评分提交：mode 默认为 recall（旧行为），rewrite/redo
// 会带上 evidence（建议分/最终分/相似度/用时）。离线时同样把 mode/evidence
// 写进离线队列，联网后一起补交。
async function rvfSubmitGrade(state, item, { quality, button, reviewButtons, status, offline, pageGuard, root, due, mode = "recall", evidence = "", gradeButtons = null }) {
  if (!state.isCurrent() || !due || state.submitting) return;
  // 只回忆要先看错因再自评；做题验证（默写、重做、类似题）不需要先看答案。
  if (!state.revealed && mode === "recall") return;
  state.submitting = true;
  const buttons = gradeButtons || state.buttons;
  for (const gradeButton of buttons) gradeButton.disabled = true;
  reviewButtons.setAttribute("aria-busy", "true");
  const anchor = button ? sealAnchorPoint(button) : null;
  const sealText = { 0: "再练", 2: "再练", 3: "过关", 4: "记住", 5: "掌握" }[quality];
  const payload = { quality, version: item.version };
  if (mode !== "recall") payload.mode = mode;
  if (evidence) payload.evidence = evidence;
  try {
    let result = null;
    if (offline) {
      await window.OfflineReview?.grade(item.id, quality, { mode, evidence }); // 内部已提示「已记下，联网后同步」
    } else {
      try {
        result = await api(`/api/mistakes/${item.id}/review`, {
          method: "POST",
          body: JSON.stringify(payload),
        });
      } catch (error) {
        // 在线详情打开后突然断网：POST 直接网络失败时转入离线队列，不让评分丢失。
        if (window.OfflineReview?.isNetworkError(error)) {
          await window.OfflineReview?.grade(item.id, quality, { mode, evidence });
          window.OfflineReview?.restrictDetail(root);
        } else {
          throw error;
        }
      }
    }
    if (!state.isCurrent()) return;
    stampSeal(sealText, { anchor, tone: quality <= 2 ? "ink" : undefined });
    if (result && result.revenge) {
      // 复仇成功：在现有盖章流程上加一枚特别印章 + 一句文案，不另起一套。
      stampSeal("雪耻", { anchor, tone: "cinnabar" });
      rvfShowRevengeLine(root, result.revenge);
    }
    window.Feel?.toast(rvfReviewDoneText(result)); // 手感层：盖章后提示下次复习时间
    if (result?.unverified_cap) message("连续两次只自评，下次间隔最多 14 天。做一次题验证，间隔就能拉长。");
    window.ReviewFeel?.buzz();
    if (result) {
      rvfRemoveItem({ ...item, ...result });
      window.ReviewExtras?.rememberReview({ item, result, quality, isCurrent: pageGuard,
        onUndo: (restored) => rvfRestoreItem(item, restored) });
      // F6 趁热打铁：复习完成后推荐 3 道同类题
      const similarHost = element("div", "", "similar-host");
      $("#detail").append(similarHost);
      window.Similar?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
      window.Similar?.mount(similarHost, item.id);
    } else {
      // 离线评分没有撤销入口（没发到服务器，无从撤销）。
      rvfRemoveItem({ ...item }, { offline: true });
    }
    // 评分已提交（在线成功或离线入队）：停掉可能还在跑的重做计时器。
    if (state.redo?.timer) { state.redo.timer.cancel(); state.redo.timer = null; }
  } catch (error) {
    if (state.isCurrent()) status.textContent = error.message;
  } finally {
    state.submitting = false;
    if (state.isCurrent()) {
      for (const gradeButton of buttons) gradeButton.disabled = !due;
      reviewButtons.removeAttribute("aria-busy");
    }
  }
}

function rvfShowRevengeLine(root, revenge) {
  // 复仇成功文案行：插在详情区顶部，用户文本一律 textContent。
  const host = root.querySelector("#detail") || root;
  const line = element("p", "", "revenge-line");
  const strong = element("strong", "复仇成功", "");
  line.append(strong);
  line.append(document.createTextNode(
    `：${revenge.days_gap} 天前在这道题上栽过，今天一次做对。`
  ));
  host.prepend(line);
}

function rvfRevealDetail() {
  const state = rvfDetailState;
  if (!state || state.revealed || !state.isCurrent()) return;
  state.revealed = true;
  for (const part of state.answers) part.hidden = false;
  state.reveal.hidden = true;
  state.reveal.setAttribute("aria-expanded", "true");
  state.status.textContent = "错因与当时的思路已显示，可以对照回忆评分。";
  rvfSyncRedoVisibility(state);
  const first = state.buttons.find((button) => !button.disabled);
  (first || state.answers[0]).focus({ preventScroll: true });
}

function rvfDetailKeydown(event) {
  const state = rvfDetailState;
  if (!state || !state.isCurrent() || window.FocusReview?.isOpen() || window.ReviewExtras?.blockedKey(event)
    || event.defaultPrevented || event.repeat || event.isComposing || event.ctrlKey || event.metaKey || event.altKey || event.shiftKey) return;
  const target = event.target;
  if (target?.closest?.("input, textarea, select, [contenteditable], dialog[open], [role=dialog], [role=menu]")) return;
  if (event.key === " " && !state.revealed && !target?.closest?.("button, a")) {
    event.preventDefault();
    rvfRevealDetail();
  } else if (/^[1-5]$/.test(event.key) && state.revealed) {
    // 重做模式（默写/限时）进行中时，数字键不触发隐藏的普通评分按钮。
    if (state.redo && state.redo.mode && state.redo.mode !== "recall") return;
    const button = state.buttons[Number(event.key) - 1];
    if (button && !button.disabled) { event.preventDefault(); button.click(); }
  } else if ((event.key === "ArrowRight" || event.key === "ArrowLeft") && state.revealed) {
    // 方向键评分：与滑动手势方向一致，→ = 记得(4)，← = 忘了(0)
    const quality = event.key === "ArrowRight" ? 4 : 0;
    const button = state.buttons.find((item) => Number(item.dataset.quality) === quality);
    if (button && !button.disabled) { event.preventDefault(); button.click(); }
  } else if (/^[tTpP]$/.test(event.key) && state.menu) {
    const action = state.menu.querySelector(event.key.toLowerCase() === "t" ? '[data-review-action="snooze"][data-days="1"]' : '[data-review-action="suspend"]');
    if (action && !action.hidden && !action.disabled) { event.preventDefault(); action.click(); }
  }
}

// ---- 复习=重做：四种复习方式（只增强算法分区，其他分区保持原样） ----
// 成绩由做题结果算出：默写看相似度，重做和类似题看“做完了吗”的结果；
// 只回忆（自评）最高算“记得”，连续两次自评下次间隔最多 14 天。
function rvfRedoAvailability(item) {
  return {
    rewrite: Boolean((item.correct_code || "").trim()),
    redo: Boolean(window.RedoFlow ? window.RedoFlow.problemUrl(item) : (item.source_url || "").trim()),
    similar: true,
  };
}

// 上一次复习只是自评：这次建议用做题验证。reviews 按时间倒序，第 0 条是最近一次。
function rvfNeedsVerification(item) {
  const last = (item.reviews || [])[0];
  return Boolean(last) && (last.mode || "recall") === "recall";
}

async function rvfPickDefaultMode(item) {
  const avail = rvfRedoAvailability(item);
  const preference = await rvfFetchRedoPreference();
  let hint = false;
  if (window.RedoFlow && (avail.rewrite || avail.redo)) {
    try { hint = await window.RedoFlow.fetchHint(api, item.id); }
    catch (error) { hint = false; }
  }
  if (hint && avail.rewrite) return { mode: "rewrite", hint };
  if (hint && avail.redo) return { mode: "redo", hint };
  const nudge = rvfNeedsVerification(item);
  if (nudge || preference !== "recall_only") {
    if (avail.redo) return { mode: "redo", hint };
    if (avail.rewrite) return { mode: "rewrite", hint };
    if (nudge && avail.similar) return { mode: "similar", hint };
  }
  return { mode: "recall", hint };
}

function rvfBuildRedoSection(state, item) {
  const section = element("section", "", "redo-section");
  section.setAttribute("aria-label", "选择复习方式");
  const avail = rvfRedoAvailability(item);
  const buttons = {};
  const row = element("div", "", "redo-modes");
  for (const [mode, label, ok, why] of [
    ["redo", "去原题限时重做", avail.redo, "这道题还没有原题链接：点下面“编辑题目”填上来源链接就能用"],
    ["similar", "做一道类似题", avail.similar, ""],
    ["rewrite", "默写关键代码", avail.rewrite, "该题没有正确代码，默写不可用"],
    ["recall", "只回忆（自评）", true, ""],
  ]) {
    const button = element("button", label, "redo-mode");
    button.type = "button";
    button.disabled = !ok;
    if (!ok) button.title = why;
    button.setAttribute("aria-pressed", "false");
    button.dataset.mode = mode;
    button.addEventListener("click", () => rvfSelectRedoMode(state, mode));
    row.append(button);
    buttons[mode] = button;
  }
  const hintEl = element("p", "这条用重做更靠谱", "redo-hint");
  hintEl.hidden = true;
  const nudgeEl = element("p", "上次只是自评。这次建议做题验证：连续两次自评，下次间隔最多 14 天。", "redo-nudge");
  nudgeEl.hidden = !rvfNeedsVerification(item);
  const panelHost = element("div", "", "redo-panel-host");
  section.append(element("h3", "这次怎么复习？", "section-label"), row, hintEl, nudgeEl, panelHost);
  state.redo = { section, buttons, hintEl, panelHost, mode: null, timer: null };
  return section;
}

async function rvfInitRedoModes(state) {
  if (!state.redo || state.redo.mode || !state.isCurrent()) return;
  const { mode, hint } = await rvfPickDefaultMode(state.item);
  if (!state.isCurrent() || !state.redo || state.redo.mode) return;
  state.redo.hintEl.hidden = !hint;
  rvfSelectRedoMode(state, mode);
}

// 自评的评分按钮只在“先显示错因、并且选了只回忆”时出现；做题验证的方式没有评分按钮。
function rvfSyncRedoVisibility(state) {
  const redo = state.redo;
  if (!redo || !state.reviewSection) return;
  const selfRating = !redo.mode || redo.mode === "recall";
  const show = selfRating && state.revealed;
  state.reviewSection.hidden = !show;
  if (state.recallBadge) state.recallBadge.hidden = !show;
}

function rvfSelectRedoMode(state, mode) {
  const redo = state.redo;
  if (!redo || redo.mode === mode || !state.isCurrent()) return;
  if (redo.timer) { redo.timer.cancel(); redo.timer = null; }
  redo.mode = mode;
  for (const [name, button] of Object.entries(redo.buttons)) {
    button.setAttribute("aria-pressed", String(name === mode));
  }
  redo.panelHost.replaceChildren();
  rvfSyncRedoVisibility(state);
  if (mode === "rewrite") redo.panelHost.append(rvfBuildRewritePanel(state));
  else if (mode === "redo") redo.panelHost.append(rvfBuildRedoPanel(state));
  else if (mode === "similar") redo.panelHost.append(rvfBuildSimilarPanel(state));
}

// 做题验证交卷：成绩已由结果算出，直接提交，不再让用户二次选分。
function rvfSubmitVerified(state, item, mode, picked) {
  return rvfSubmitGrade(state, item, {
    quality: picked.quality, button: null, ...state.gradeCtx, mode, evidence: picked.evidence, gradeButtons: [],
  });
}

function rvfBuildRewritePanel(state) {
  const item = state.item;
  const panel = element("div", "", "redo-panel");
  panel.append(element("h4", "默写关键代码"));
  panel.append(element("p", `题目：${item.title}`, "muted"));
  if ((item.counterexample || "").trim()) {
    panel.append(element("p", "最小反例", "section-label"));
    panel.append(element("pre", item.counterexample, "code"));
  }
  panel.append(element("p", "你记的错因", "section-label"));
  panel.append(renderMistakeText(item));
  const area = document.createElement("textarea");
  area.setAttribute("aria-label", "默写代码输入区");
  area.placeholder = "把关键几行写出来，不用整段照抄";
  const compare = element("button", "对照", "primary");
  compare.type = "button";
  const resultHost = element("div");
  compare.addEventListener("click", () => {
    if (!state.isCurrent()) return;
    const diff = window.RedoFlow.diffLines(item.correct_code, area.value);
    const diffBox = element("div", "", "redo-diff");
    for (const line of diff.missing) diffBox.append(element("pre", `− 没写出：${line}`, "redo-missing"));
    for (const line of diff.extra) diffBox.append(element("pre", `＋ 多写了：${line}`, "redo-extra"));
    resultHost.replaceChildren(
      element("p", `相似度 ${Math.round(diff.similarity * 100)}%`, "redo-suggest"),
      diffBox,
    );
    if (!diff.missing.length && !diff.extra.length) {
      diffBox.append(element("p", "逐行一致，漂亮。", "muted"));
    }
    resultHost.append(window.RedoFlow.buildRewriteSubmit({
      similarity: diff.similarity,
      onPick: (picked) => rvfSubmitVerified(state, item, "rewrite", picked),
    }));
    compare.disabled = true;
  });
  panel.append(area, compare, resultHost);
  return panel;
}

function rvfFormatCountdown(remaining, state) {
  return window.RedoFlow.formatCountdown(remaining, state);
}

function rvfBuildRedoPanel(state) {
  const item = state.item;
  const panel = element("div", "", "redo-panel");
  panel.append(element("h4", "去原题限时重做"));
  panel.append(element("p", "从空白开始重做，别先看答案；卡住了先想提示，实在不行再看题解（会按“没做出来”计分）。", "muted"));
  const limitRow = element("div", "", "redo-limit-row");
  limitRow.append(element("span", "时限"));
  const select = document.createElement("select");
  select.setAttribute("aria-label", "重做时限");
  for (const minutes of window.RedoFlow.REDO_LIMITS) {
    const option = document.createElement("option");
    option.value = String(minutes);
    option.textContent = `${minutes} 分钟`;
    if (minutes === window.RedoFlow.DEFAULT_REDO_LIMIT) option.selected = true;
    select.append(option);
  }
  limitRow.append(select);
  const start = element("button", "开始，在新标签页打开原题", "primary");
  start.type = "button";
  const timerEl = element("p", "", "redo-timer");
  timerEl.setAttribute("role", "timer");
  const askHost = element("div");
  const showAsk = () => {
    askHost.replaceChildren(window.RedoFlow.buildResultAsk({
      mode: "redo",
      seconds: () => (state.redo.timer ? state.redo.timer.elapsed() : 0),
      limitSeconds: Number(select.value) * 60,
      onPick: (picked) => {
        if (!state.isCurrent()) return;
        if (state.redo.timer) { state.redo.timer.cancel(); state.redo.timer = null; }
        rvfSubmitVerified(state, item, "redo", picked);
      },
    }));
  };
  const pause = window.RedoFlow.buildPauseButton(() => state.redo.timer);
  start.addEventListener("click", () => {
    if (!state.isCurrent()) return;
    window.RedoFlow.openProblem(window.RedoFlow.problemUrl(item));
    const limitSeconds = Number(select.value) * 60;
    start.disabled = true;
    select.disabled = true;
    state.redo.timer = window.RedoFlow.startTimer(
      limitSeconds,
      (remaining, tick) => { timerEl.textContent = rvfFormatCountdown(remaining, tick); },
      () => { timerEl.textContent = "时间到"; pause.sync(); showAsk(); },
    );
    pause.sync();
    showAsk();
  });
  panel.append(limitRow, start, timerEl, pause, askHost);
  return panel;
}

function rvfBuildSimilarPanel(state) {
  const item = state.item;
  window.Similar?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  return window.RedoFlow.buildSimilarPanel({
    api,
    mistakeId: item.id,
    isAlive: () => state.isCurrent() && state.redo?.mode === "similar",
    quickAdd: (picked) => window.Similar.quickAdd(picked, api),
    onTimer: (timer) => { state.redo.timer = timer; },
    onPick: (picked) => rvfSubmitVerified(state, item, "similar", picked),
  });
}
