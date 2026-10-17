// static/app/review-grade.js — 复习：详情主渲染 renderDetail（含评分按钮）、addMistakeInput、模块接线与事件绑定。
// 由 static/app/review.js 拆分（C 收尾），纯重构、零行为变化。
// 最后加载：顶层执行语句（rvfHooks、configure 调用、addEventListener、addMistakeInput() 调用）引用前两个文件声明的名字；领域间调用保持顶层全局函数名直接调用。
"use strict";

function renderDetail(item, options = {}) {
  rvfClearDetail();
  const root = $("#detail");
  root.replaceChildren();
  // 离线详情来自预取缓存（options.offline），或当前确实处于断网状态。
  const offline = Boolean(options.offline) || Boolean(window.OfflineReview?.isOffline?.());
  const isCodeZone = codeZones.has(item.zone);
  const pageGuard = rvfPageGuard();
  const state = { item, revealed: Boolean(item.pending_reason) || window.ReviewExtras?.hideReason() === false, submitting: false, removed: false, answers: [], buttons: [] };
  state.isCurrent = () => pageGuard() && rvfDetailState === state;
  rvfDetailState = state;
  const toolbar = element("div", "", "review-toolbar");
  const preference = element("label", "", "review-preference");
  const hide = document.createElement("input");
  hide.id = "review-hide-reason";
  hide.type = "checkbox";
  hide.checked = window.ReviewExtras?.hideReason() !== false;
  preference.append(hide, element("span", "复习时先遮住错因"));
  hide.addEventListener("change", () => {
    window.ReviewExtras?.setHideReason(hide.checked);
    if (!hide.checked) rvfRevealDetail();
    else {
      state.revealed = false;
      for (const part of state.answers) part.hidden = true;
      state.reveal.hidden = false;
      state.reveal.setAttribute("aria-expanded", "false");
      state.status.textContent = "先回忆，再显示错因。";
    }
    if (view === "today") {
      rvfRenderCards(user.today, state.item.id);
    }
  });
  toolbar.append(preference);
  const source = element("p", `${item.zone} · ${item.title} · ${(item.tags || []).join(" / ") || "无标签"}`, "review-source");
  // 一键收录的来源行保留在题目上方，只展示来源，不透露其后的回忆笔记。
  const sourceLine = (item.thinking || "").split("\n")[0];
  if (/^(来源|题目链接)[：:]/.test(sourceLine)) source.append(element("span", sourceLine, "review-source-link"));
  const recall = document.createElement("input");
  recall.id = "review-recall";
  recall.type = "text";
  recall.maxLength = 2000;
  recall.autocomplete = "off";
  recall.placeholder = "可选，只留在这一页，不保存、不上传";
  const recallField = field("先写下我的回忆", recall);
  recallField.className = "review-recall";
  const reveal = element("button", "显示错因（空格）", "review-reveal");
  reveal.id = "review-reveal";
  reveal.type = "button";
  reveal.setAttribute("aria-keyshortcuts", "Space");
  reveal.setAttribute("aria-expanded", String(state.revealed));
  reveal.setAttribute("aria-controls", "review-reason review-original review-grade-row");
  reveal.hidden = state.revealed;
  reveal.addEventListener("click", rvfRevealDetail);
  state.reveal = reveal;
  const status = element("p", "", "review-detail-status");
  status.id = "review-detail-status";
  status.setAttribute("role", "status");
  status.setAttribute("aria-live", "polite");
  state.status = status;

  const heading = element("div", "", "detail-heading");
  const navigationItem = { ...item, problem_mistakes: item.problem_mistakes
    || [...rvfListItems.values()].filter(member => member.problem_id === item.problem_id) };
  root.append(window.ProblemCards.navigation(navigationItem, id => run(() => openMistake(id))));
  heading.append(
    element("h2", item.title),
    element("p", item.language ? `${item.zone} · ${item.language}` : item.zone, "language-badge")
  );
  let mistakeTextNode = renderMistakeText(item);
  const reason = element("section", "", "review-reason");
  reason.id = "review-reason";
  reason.tabIndex = -1;
  reason.append(element("h3", "这次需要记住的错因", "section-label"));
  // 最小反例是最好的回忆线索：放在背面最显眼的位置，等宽显示。
  if ((item.counterexample || "").trim()) {
    const counterexampleBlock = element("figure", "", "counterexample");
    const caption = element("figcaption", "最小反例", "counterexample-label");
    const body = element("pre", item.counterexample, "code counterexample-body");
    counterexampleBlock.append(caption, body);
    reason.append(counterexampleBlock);
  }
  reason.append(mistakeTextNode);
  root.append(toolbar, source, heading, recallField, reveal, status, reason);
  state.answers.push(reason);
  if (window.TagEditor) root.append(window.TagEditor.render(item));
  root.append(
    element(
      "p",
      `下次复习：${item.due_date} · 连续成功：${item.repetitions} 次`,
      "muted review-meta"
    ),
    element(
      "p",
      `上次复习：${timestamp(item.last_reviewed_at)}`,
      "muted review-meta"
    )
  );

  const original = document.createElement("details");
  original.className = "original-record";
  original.id = "review-original";
  original.append(
    element("summary", "查看当时的思路和代码"),
    renderProblemEditor(item)
  );
  root.append(original);
  state.answers.push(original);

  // 串联：复习时顺带看到自己写过的相关笔记（和错因一起，显示错因后才出现，不提前剧透）。
  const relatedNotes = element("div", "", "related-notes-host");
  relatedNotes.hidden = true;
  root.append(relatedNotes);
  state.answers.push(relatedNotes);
  void window.RelatedNotes?.mount(relatedNotes, item, {
    api,
    isCurrent: () => state.isCurrent(),
    openNote: (id) => run(async () => {
      await showView("notes");
      await window.Notes?.openNoteById(id);
    }),
  }).then(() => {
    // mount 结束时还没显示错因：继续藏着，等 rvfRevealDetail 一起放出来。
    if (!state.revealed && relatedNotes.childNodes?.length) relatedNotes.hidden = true;
  });

  // 串联：这条的错因在“我的老毛病”笔记里写过对策的话，显示错因后一起出现。
  const counterHost = element("div", "", "cm-host");
  counterHost.hidden = true;
  root.append(counterHost);
  state.answers.push(counterHost);
  void window.Countermeasures?.mount(counterHost, item, {
    api,
    isCurrent: () => state.isCurrent(),
    openNote: (id) => run(async () => {
      await showView("notes");
      await window.Notes?.openNoteById(id);
    }),
  }).then(() => {
    if (!state.revealed && counterHost.childNodes?.length) counterHost.hidden = true;
  });

  const duckHost = element("div", "", "duck-panel-host");
  root.append(duckHost);
  window.DuckPanel?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  window.DuckPanel?.mount(duckHost, item);

  const scratchHost = element("div", "", "scratch-host");
  root.append(scratchHost);
  window.Scratch?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
  window.Scratch?.mount(scratchHost, item);
  if (isCodeZone) {
    const runnerHost = element("div", "", "code-runner-host");
    root.append(runnerHost);
    window.CodeRunner?.mount(runnerHost, item, { offline });
    state.answers.push(runnerHost);
  }

  const deleteMistakeBtn = element("button", "删除这条易错点", "danger");
  deleteMistakeBtn.type = "button";
  deleteMistakeBtn.addEventListener("click", () => run(async () => {
    if (!confirm("删除这条易错点？它的复习记录和变体题也会一起删除，无法恢复。同一道题的其他易错点会保留。")) {
      return;
    }
    await api(`/api/mistakes/${item.id}`, { method: "DELETE" });
    clearDetail();
    await loadList();
    notifyDataChanged("delete");
    message("已删除这条易错点。");
  }));

  const deleteProblemBtn = element(
    "button", "删除整道题及全部记录", "danger"
  );
  deleteProblemBtn.type = "button";
  deleteProblemBtn.addEventListener("click", () => run(async () => {
    if (!confirm(
      "确定删除整道题？这道题下的所有易错点、复习记录和变体题都会一起删除，且无法恢复。"
    )) {
      return;
    }
    await api(`/api/problems/${item.problem_id}`, { method: "DELETE" });
    clearDetail();
    await loadList();
    notifyDataChanged("delete");
    message("已删除整道题。");
  }));

  const dangerZone = element("div", "", "danger-zone");
  dangerZone.append(
    element("p", "删除后无法恢复，请确认这些记录不再需要。", "danger-hint"),
    deleteMistakeBtn,
    deleteProblemBtn
  );

  const reviewSection = element("section", "", "review-section");
  const reviewButtons = element("div", "", "actions review-actions");
  reviewButtons.id = "review-grade-row";
  reviewButtons.setAttribute("role", "group");
  reviewButtons.setAttribute("aria-label", "这次掌握程度，数字键一到五或左右方向键评分");
  const due = !item.suspended_at && item.due_date <= item.today;
  const reviewHeading = element("h3", "这次，你掌握得怎么样？");
  // 只回忆 = 自评：卡片上标一个小标记。
  const recallBadge = element("span", "自评", "redo-badge");
  recallBadge.hidden = true;
  reviewHeading.append(recallBadge);
  state.reviewSection = reviewSection;
  state.recallBadge = recallBadge;
  state.gradeCtx = { reviewButtons, status, offline, pageGuard, root, due };
  reviewSection.append(
    reviewHeading,
    element(
      "p",
      due
        ? (item.zone === "算法"
          ? "先回忆，再对照。自评最高算“记得”；想拿“很熟”、把间隔拉长，请用上面的做题方式。"
          : "先回忆，再对照。按真实感受评分，下次复习会据此安排。")
        : "还没到复习日期。先回看笔记或练一道变体题，到期后就能评分。",
      "muted"
    )
  );

  for (const [index, [quality, label]] of grades.entries()) {
    // 算法题的自评没有“很熟”：想拿它必须做题验证（服务端也会封顶）。
    if (quality === 5 && item.zone === "算法") continue;
    const button = element("button", "", "review-grade");
    button.append(element("kbd", String(index + 1)), element("span", label));
    button.type = "button";
    button.dataset.quality = String(quality);
    button.dataset.blocked = due ? "0" : "1";
    button.disabled = !due;
    button.setAttribute("aria-keyshortcuts", String(index + 1));
    button.addEventListener("click", () => {
      rvfSubmitGrade(state, item, { quality, button, reviewButtons, status, offline, pageGuard, root, due });
    });
    reviewButtons.append(button);
    state.buttons.push(button);
  }
  reviewSection.append(reviewButtons);
  root.append(reviewSection);
  state.answers.push(reviewSection);
  // 复习=重做：只增强算法分区；未到期不显示方式选择。
  if (item.zone === "算法" && due && window.RedoFlow) {
    // 复习方式放在“显示错因”之前：重做和类似题要先动手，不能先看到答案。
    root.append(rvfBuildRedoSection(state, item));
  }
  void window.ReviewExtras?.decorateGrades(state.buttons, item, state.isCurrent);
  const menu = window.ReviewExtras?.menu({ item, status, isCurrent: () => pageGuard() && (rvfDetailState === state || state.removed),
    onAction: async (action, days, result) => {
      if (!pageGuard()) return;
      if (action === "unsuspend") await rvfRestoreItem(item, result);
      else { state.removed = true; rvfRemoveItem({ ...item, ...result }); }
    } });
  if (menu) { state.menu = menu; toolbar.append(menu); }

  // 题目详情里的"记笔记"：先切到笔记页，关联题目用 pendingPrefill 传给 Notes，
  // load() 消费它，避免"showView 还没 load 完"的竞态。
  const noteButton = document.createElement("button");
  noteButton.type = "button";
  noteButton.className = "review-more-button";
  noteButton.textContent = "记笔记";
  noteButton.setAttribute("aria-label", "为这道题记笔记");
  noteButton.addEventListener("click", () => run(async () => {
    message();
    await showView("notes");
    // 套“错题复盘”模板，并把自己当时记的错因、最小反例先填进去（都是用户自己写的内容）。
    window.Notes?.openWithProblem(item.problem_id, item.title, { content: rvfReviewNoteDraft(item) });
  }));
  toolbar.append(noteButton);

  // 变强轨迹：复习记录画成一条时间线（和复习历史一起，显示错因后才出现）。
  const growth = window.GrowthTrack?.render(item);
  if (growth) {
    root.append(growth);
    state.answers.push(growth);
  }

  if (item.reviews.length) {
    const history = document.createElement("details");
    history.className = "review-history";
    history.append(element("summary", `复习历史（${item.reviews.length} 次）`));
    const list = document.createElement("ul");
    for (const review of item.reviews) {
      const modeLabel = (window.RedoFlow && window.RedoFlow.MODE_LABEL[review.mode]) || "自评";
      list.append(element(
        "li",
        `${timestamp(review.reviewed_at)} · 评分 ${review.quality}/5 · ${modeLabel}` +
        ` · 下次 ${review.next_due_date}`
      ));
    }
    history.append(list);
    root.append(history);
    state.answers.push(history);
  }

  const aiSection = element("section", "", "ai-section");
  const billingLink = element("a", "AI 额度与计费");
  billingLink.href = "/static/ai-billing.html";
  aiSection.append(
    element("h3", "诊断错因，再练两道"),
    billingLink,
    element(
      "p",
      "生成时会把这道题的代码（或解题过程）、思路发送给 AI 模型服务商（DeepSeek）；" +
      "错因没填时 AI 会自己反推，已经填了则作为参考。" +
      "额度规则见下方说明链接。",
      "muted"
    ),
    element(
      "p",
      isCodeZone
        ? "每次生成两道题。请先核对题意，参考样例自行解答并记录结果；代码仅保存，不运行或自动判题。"
        : "每次生成两道题。提交最终答案后，系统会比对参考答案并显示判定；如有疑问，请自行核对参考答案。",
      "muted"
    )
  );

  const variants = element("div");
  const empty = element("p", "还没有变体题。练两道新题，检查自己是否真的理解了。", "muted");
  if (!item.variants.length) variants.append(empty);
  for (const variant of item.variants) {
    variants.append(renderVariant(variant, isCodeZone));
  }

  const generate = element(
    "button",
    user.ai_enabled ? "诊断错因并出两道新题" : "AI 尚未配置，暂时无法出题",
    "primary"
  );
  generate.type = "button";
  generate.dataset.blocked = user.ai_enabled ? "0" : "1";
  generate.disabled = !user.ai_enabled;

  generate.addEventListener("click", () => run(async () => {
    message("AI 正在诊断错因、准备两道新题，可能需要一两分钟。请保持页面打开。");
    const generated = await api(`/api/mistakes/${item.id}/variants`, {
      method: "POST",
    });
    empty.remove();
    for (const variant of generated.variants) {
      variants.prepend(renderVariant(variant, isCodeZone));
    }
    if (generated.mistake_description !== item.description) {
      item.description = generated.mistake_description;
      const updated = renderMistakeText(item);
      mistakeTextNode.replaceWith(updated);
      mistakeTextNode = updated;
    }
    message("两道新题已生成并保存。");
  }));

  aiSection.append(generate, variants);
  root.append(aiSection, dangerZone);
  state.answers.push(aiSection);
  for (const part of state.answers) part.hidden = !state.revealed;
  // 复习=重做：显示错因后（或一开始就显示时）异步选默认复习方式。
  if (state.redo) run(() => rvfInitRedoModes(state));
  if (offline) window.OfflineReview?.restrictDetail(root); // 禁用推迟/暂停、AI、变体保存、删除并说明原因
}

function rvfReviewNoteDraft(item) {
  const lines = [
    `# 复盘：${item.title}`, "", "## 题目", `[[题:${item.title}]]`, "",
    "## 我当时怎么想的", "", "## 错在哪", (item.description || "").trim(), "",
  ];
  if ((item.counterexample || "").trim()) lines.push("## 最小反例", "```", item.counterexample.trim(), "```", "");
  lines.push("## 这次改对的关键", "");
  return lines.join("\n");
}

function addMistakeInput() {
  const container = $("#mistake-inputs");
  if (container.children.length >= 10) {
    message("每道题最多添加 10 条易错点", true);
    return;
  }

  const row = element("div", "", "mistake-row");
  const input = textarea("", 2000, 3);
  input.name = "mistake";
  input.placeholder = "不确定的话可以留空，AI 会从代码和思路里帮你反推错因。";

  const counterexample = textarea("", 1000, 2);
  counterexample.name = "counterexample";
  counterexample.classList.add("code");
  counterexample.placeholder = "最小反例（可选）：一个能让原代码出错的最小输入，例如：n=1 时输出 0，应为 1";

  const remove = element("button", "移除这条输入");
  remove.type = "button";
  remove.addEventListener("click", () => {
    if (container.children.length > 1) row.remove();
  });

  row.append(field("哪里容易错，为什么会错？（可选）", input), field("最小反例（可选）", counterexample), remove);
  container.append(row);
}

const rvfHooks = { api, getUser: () => user, getEpoch: () => sessionEpoch, getView: () => view };
window.ImportWizard?.configure({ ...rvfHooks, getZones: () => zones,
  onImported: async () => { notifyDataChanged("import"); await loadList(); } });
window.ReviewExtras?.configure(rvfHooks);
window.FocusReview?.configure(rvfHooks);
window.CodeRunner?.configure({ ...rvfHooks,
  getDetailGeneration: () => rvfDetailGeneration, getSelectionGeneration: () => rvfSelectionGeneration });

async function pendingReasonSaved(id) {
  const pageCurrent = rvfPageGuard();
  const savedView = view;
  const selectedGeneration = rvfDetailGeneration;
  notifyDataChanged("reason");
  if (!pageCurrent() || view !== savedView || selectedGeneration !== rvfDetailGeneration) return;
  const refreshing = openMistake(id);
  const refreshGeneration = rvfDetailGeneration;
  try { await refreshing; }
  catch {
    if (pageCurrent() && view === savedView && refreshGeneration === rvfDetailGeneration) {
      message("原因已保存，但详情暂时无法重新读取；请重新选择这条记录。", true);
    }
  }
}

window.PendingReason?.configure({ ...rvfHooks, showView, openMistake,
  getDetailGeneration: () => rvfDetailGeneration,
  getSelectionGeneration: () => rvfSelectionGeneration,
  onSaved: pendingReasonSaved,
  reportError: (text) => message(text, true),
});
document.addEventListener("keydown", rvfDetailKeydown);
$("#review-daily-cap").addEventListener("change", rvfSetDailyCap);
$("#review-cap-toggle")?.addEventListener("click", () => run(rvfToggleShowAll));
$("#review-spread")?.addEventListener("click", () => run(rvfSpreadBacklog));

addMistakeInput();
