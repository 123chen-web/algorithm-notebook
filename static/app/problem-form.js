// static/app/problem-form.js — 记录表单：快速记录/拍照识别/分区字段。
// 由 static/app.js 按领域拆分（C2），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

// ---- 事件绑定与模块配置 ----
// 上一次拍照识别成功的结果；非空时说明表单当前内容来自 AI 识别，
// 保存记录后要顺带自动生成练习题。手动编辑无关字段不会清空它，
// 但重新选图、移除图片或表单重置都会清空。
let photoRecognition = null;
let zones = [];
let codeZones = new Set();

async function loadZones() {
  const data = await api("/api/zones");
  zones = data.zones;
  codeZones = new Set(data.code_zones);
  window.AnkiExport?.setZones(zones);

  const problemZone = $("#problem-zone");
  problemZone.replaceChildren();
  for (const zone of zones) {
    const option = element("option", zone);
    option.value = zone;
    problemZone.append(option);
  }

  const zoneFilter = $("#zone-filter");
  for (const zone of zones) {
    const option = element("option", zone);
    option.value = zone;
    zoneFilter.append(option);
  }

  applyZoneFieldMode($("#problem-form"), problemZone.value);
  fillVerdictSelect($("#problem-verdict"));
}

// 编程类分区要求填写编程语言，"代码"字段就是字面意义的代码；
// 数学类分区没有编程语言，同一个字段改用来记录解题过程/演算。

function applyZoneFieldMode(form, zoneValue) {
  const isCode = codeZones.has(zoneValue);
  const languageField = form.querySelector("[data-role=language-field]");
  const languageInput = form.querySelector("[name=language]");
  const codeLabel = form.querySelector("[data-role=code-label]");
  languageField.hidden = !isCode;
  languageInput.required = isCode;
  if (!isCode) languageInput.value = "";
  // 判题类型只在算法分区有意义：其他分区隐藏，值清空。
  const verdictField = form.querySelector("[data-role=verdict-field]");
  const verdictSelect = form.querySelector("[name=verdict]");
  if (verdictField) verdictField.hidden = !isCode;
  if (verdictSelect && !isCode) verdictSelect.value = "";
  codeLabel.textContent = isCode ? "当时的代码" : "当时的解题过程";
  const codeInput = codeLabel.nextElementSibling;
  if (codeInput) {
    codeInput.placeholder = isCode
      ? "粘贴当时的代码，保留错误也没关系。"
      : "写下当时的解题过程或演算，保留错误也没关系。";
  }
}

// 判题类型下拉的选项从词表接口来，不在前端硬编码。
let verdictOptionsCache = null;
async function verdictOptions() {
  if (!verdictOptionsCache) {
    verdictOptionsCache = (async () => {
      try {
        const response = await fetch("/api/error-taxonomy", { credentials: "same-origin", headers: { "X-CSRF-Protection": "1" } });
        if (!response.ok) return [];
        return (await response.json()).verdicts || [];
      } catch { return []; } // 词表加载失败时下拉只留"未填"，不阻塞建档
    })();
  }
  return verdictOptionsCache;
}
async function fillVerdictSelect(select, current = "") {
  const options = await verdictOptions();
  const keep = select.value || current;
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "未填";
  select.append(empty);
  for (const verdict of options) {
    const option = document.createElement("option");
    option.value = verdict.code;
    option.textContent = `${verdict.code} · ${verdict.label}`;
    select.append(option);
  }
  select.value = keep;
}

function renderPhotoQuota() {
  const limit = user?.ai_daily_limit;
  const remaining = user?.ai_daily_remaining;
  const available = Number.isInteger(limit) && limit > 0 && Number.isInteger(remaining);
  $("#problem-photo-quota").textContent = available
    ? `今日 AI 额度剩余 ${Math.max(0, remaining)} / ${limit} 次`
    : "";
}

function resetPhotoForm() {
  photoRecognition = null;
  $("#problem-photo-form").reset();
  $("#problem-photo-preview").hidden = true;
  $("#problem-photo-preview").src = "";
  $("#problem-photo-recognize").disabled = true;
  $("#problem-photo-clear").hidden = true;
  $("#problem-photo-status").textContent = "";
  $("#problem-save").hidden = false;
  $("#problem-photo-save").hidden = true;
  renderPhotoQuota();
}

async function uploadPhotoForRecognition(file) {
  const body = new FormData();
  body.append("file", file);
  // 不能像 api() 那样固定 Content-Type: application/json——multipart 请求
  // 的 boundary 必须由浏览器自己生成，手动设置反而会破坏它。
  const response = await fetch("/api/problems/photo", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-CSRF-Protection": "1" },
    body,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) signedOut();
    const detail = data.detail;
    const text = typeof detail === "object" && detail !== null ? detail.message : detail;
    const error = new Error(String(text || "识别失败，请稍后重试"));
    error.status = response.status;
    throw error;
  }
  return data;
}

function fillProblemFormFromPhoto(fields) {
  const form = $("#problem-form");
  form.reset();
  resetQuickMode();
  form.title.value = fields.title;
  form.zone.value = fields.zone;
  applyZoneFieldMode(form, form.zone.value);
  form.language.value = fields.language;
  form.code.value = fields.code;
  form.thinking.value = fields.thinking;

  $("#mistake-inputs").replaceChildren();
  addMistakeInput();
  $("#mistake-inputs").querySelector("[name=mistake]").value = fields.description;

  $("#problem-save").hidden = true;
  $("#problem-photo-save").hidden = false;
}

function isQuickMode() {
  return ["quick", "sentence"].includes($("#problem-quick")?.value)
    || Boolean($("#problem-skip-thinking")?.checked || $("#problem-skip-mistakes")?.checked);
}

// 切换速记显隐：code/thinking 去 required 并隐藏区块，易错点换提示文案；
// 关闭时原样恢复（required 只还原"原本就有"的，避免误加）。

function applyQuickMode(on) {
  const form = $("#problem-form");
  if (!form) return;
  const code = form.querySelector('[name="code"]');
  const thinking = form.querySelector('[name="thinking"]');
  const skipThinking = !on && Boolean($("#problem-skip-thinking")?.checked);
  const skipMistakes = !on && Boolean($("#problem-skip-mistakes")?.checked);
  for (const field of [code, thinking]) {
    if (!field) continue;
    if (on || (field === thinking && skipThinking)) {
      if (field.hasAttribute("required")) {
        field.dataset.quickRequired = "1";
        field.removeAttribute("required");
      }
    } else if (field.dataset.quickRequired === "1") {
      field.setAttribute("required", "");
      delete field.dataset.quickRequired;
    }
  }
  // 代码整块隐藏（basics 区没有折叠，直接隐藏 label）；思路整节隐藏。
  const codeLabel = code?.closest("label");
  if (codeLabel) codeLabel.hidden = on;
  const thinkingSection = form.querySelector('[data-form-section="thinking"]');
  if (thinkingSection) thinkingSection.hidden = on;
  if (thinking?.closest("label")) thinking.closest("label").hidden = skipThinking;
  const mode = $("#problem-quick")?.value || "full";
  const mistakesSection = form.querySelector('[data-form-section="mistakes"]');
  if (mistakesSection) mistakesSection.hidden = on;
  const skipOptions = $("#problem-skips");
  if (skipOptions) skipOptions.hidden = on;
  const mistakeInputs = $("#mistake-inputs");
  if (mistakeInputs) mistakeInputs.hidden = skipMistakes;
  const sentence = $("#problem-sentence");
  const sentenceField = $("#problem-sentence-field");
  if (sentenceField) sentenceField.hidden = mode !== "sentence";
  if (sentence) sentence.required = mode === "sentence";
  const addButton = $("#add-mistake");
  if (addButton) addButton.hidden = on || skipMistakes;
  for (const progress of document.querySelectorAll('[data-form-progress="thinking"], [data-form-progress="mistakes"]')) {
    progress.hidden = on;
  }
  // 易错点提示：速记时换文案，关闭时恢复原文案。
  const hint = document.getElementById("mistake-hint");
  if (hint) {
    if (on || skipMistakes) {
      if (hint.dataset.quickHint === undefined) hint.dataset.quickHint = hint.textContent;
      hint.textContent = on ? QUICK_MISTAKE_HINT : "保存后会创建一条待补原因的易错点，复习时可以再补。";
    } else if (hint.dataset.quickHint !== undefined) {
      hint.textContent = hint.dataset.quickHint;
      delete hint.dataset.quickHint;
    }
  }
}

// 简写模式只提交当前可见的错因；完整模式的字段及空错因语义沿用原行为。
// 「属于哪个套路」选择器：新增记录页常驻一个，编辑题目弹窗各自新建一个。

// ---- 领域状态 ----
// 新增记录页现在是“一行小标签”：「可能属于：滑动窗口 · 双指针」（输入题名/链接后
// 防抖 400ms 调 /api/roadmap/suggest，点一下加入，最多 3 个，已选的可点掉），
// 旁边一个很小的“手动选择”折叠入口，里面放原来的搜索选择器。不选不影响保存。
let newRecordPicker = null;
let suggestTimer = null;
let suggestSeq = 0;

function patternTitleOf(id) {
  return window.PatternPicker?.titleOf?.(id) || id;
}

function mountPatternPicker() {
  const host = $("#problem-patterns");
  if (!host || newRecordPicker || !window.PatternPicker) return;
  window.PatternPicker.load().then(() => {
    if (newRecordPicker || !host.isConnected) return;
    newRecordPicker = window.PatternPicker.create({});

    const suggestRow = element("div", "", "pattern-suggest-row");
    suggestRow.hidden = true;
    const suggestLabel = element("span", "可能属于：", "pattern-suggest-label");
    const suggestChips = element("span", "", "pattern-suggest-chips");
    suggestRow.append(suggestLabel, suggestChips);

    const pickedRow = element("div", "", "pattern-picked-row");
    pickedRow.hidden = true;
    pickedRow.append(element("span", "已选：", "pattern-suggest-label"));
    const pickedChips = element("span", "", "pattern-picked-chips");
    pickedRow.append(pickedChips);

    const manual = document.createElement("details");
    manual.className = "pattern-manual";
    const summary = document.createElement("summary");
    summary.textContent = "手动选择";
    manual.append(summary, newRecordPicker.el);
    manual.addEventListener("toggle", () => paintPickedChips());

    host.replaceChildren(suggestRow, pickedRow, manual);
    paintPickedChips();
    bindSuggestInputs(suggestRow, suggestChips);
    // 手动选择器里点的选项也要同步到“已选”一行。
    newRecordPicker.el.addEventListener("click", () => {
      setTimeout(paintPickedChips, 0);
    });

    function paintPickedChips() {
      const values = newRecordPicker.values();
      pickedChips.replaceChildren();
      pickedRow.hidden = values.length === 0;
      for (const id of values) {
        const chip = element("button", "", "pattern-picked-chip");
        chip.type = "button";
        chip.append(element("span", patternTitleOf(id), ""));
        const remove = element("span", "×", "pattern-picked-x");
        remove.setAttribute("aria-hidden", "true");
        chip.append(remove);
        chip.setAttribute("aria-label", `去掉${patternTitleOf(id)}`);
        chip.addEventListener("click", () => {
          newRecordPicker.setValues(newRecordPicker.values().filter((v) => v !== id));
          paintPickedChips();
        });
        pickedChips.append(chip);
      }
    }

    function bindSuggestInputs(row, chips) {
      const form = $("#problem-form");
      if (!form) return;
      const titleInput = form.querySelector('input[name="title"]');
      const urlInput = form.querySelector('input[name="source_url"]');
      const zoneSelect = $("#problem-zone");
      const schedule = () => {
        if (suggestTimer) clearTimeout(suggestTimer);
        suggestTimer = setTimeout(() => { void fetchSuggest(row, chips); }, 400);
      };
      titleInput?.addEventListener("input", schedule);
      urlInput?.addEventListener("input", schedule);
      zoneSelect?.addEventListener("change", schedule);
    }

    async function fetchSuggest(row, chips) {
      const form = $("#problem-form");
      if (!form || !user) return;
      const title = form.querySelector('input[name="title"]')?.value.trim() || "";
      const sourceUrl = form.querySelector('input[name="source_url"]')?.value.trim() || "";
      const zone = $("#problem-zone")?.value || "算法";
      if (title.length < 2 && sourceUrl.length < 8) {
        row.hidden = true;
        chips.replaceChildren();
        return;
      }
      const mine = ++suggestSeq;
      const epoch = sessionEpoch;
      const userId = user.id;
      let suggestions = [];
      try {
        const data = await api("/api/roadmap/suggest", {
          method: "POST",
          body: JSON.stringify({ title, source_url: sourceUrl, zone }),
        });
        suggestions = data.suggestions || [];
      } catch {
        return; // 失败就当没这回事，不打断新增记录。
      }
      if (mine !== suggestSeq || epoch !== sessionEpoch || !user || user.id !== userId) return;
      chips.replaceChildren();
      const picked = new Set(newRecordPicker.values());
      const fresh = suggestions.filter((s) => !picked.has(s.pattern_id)).slice(0, 3);
      row.hidden = fresh.length === 0;
      for (const suggestion of fresh) {
        const chip = element("button", suggestion.title, "pattern-suggest-chip");
        chip.type = "button";
        chip.title = suggestion.reason || "";
        chip.addEventListener("click", () => {
          const values = newRecordPicker.values();
          if (values.length >= 3 || values.includes(suggestion.pattern_id)) return;
          newRecordPicker.setValues([...values, suggestion.pattern_id]);
          paintPickedChips();
          chip.remove();
          if (!chips.children.length) row.hidden = true;
        });
        chips.append(chip);
      }
    }
  }).catch(() => {
    // 加载失败只是少了这个功能，不阻断新增记录。
  });
}

function buildProblemPayload(form, quick) {
  const data = new FormData(form);
  const mistakeInputs = [...form.querySelectorAll("textarea[name=mistake]")];
  const counterexampleInputs = [...form.querySelectorAll("textarea[name=counterexample]")];
  // 易错点带上可选的最小反例；都为空时退化成字符串，保持旧格式兼容。
  const mistakes = mistakeInputs.map((input, index) => {
    const description = input.value;
    const counterexample = (counterexampleInputs[index]?.value || "").trim();
    return counterexample ? { description, counterexample } : description;
  });
  const mode = $("#problem-quick")?.value || "full";
  const skipThinking = mode === "full" && $("#problem-skip-thinking")?.checked;
  const skipMistakes = mode === "full" && $("#problem-skip-mistakes")?.checked;
  const verdictSelect = form.querySelector("[name=verdict]");
  return {
    title: data.get("title"),
    zone: data.get("zone"),
    language: data.get("language"),
    code: data.get("code"),
    correct_code: mode === "full" ? String(data.get("correct_code") || "") : "",
    source_url: String(data.get("source_url") || ""),
    verdict: form.querySelector("[name=verdict]")?.value || "",
    thinking: skipThinking ? "" : data.get("thinking"),
    mistakes: !quick || (skipThinking && !skipMistakes) ? mistakes
      : (mode === "sentence" ? [String(data.get("quick_reason") || "").trim()] : []),
    quick: Boolean(quick),
    // 可选：最多 3 个套路；选择器还没加载好时按"不关联"提交，不影响建档。
    pattern_ids: newRecordPicker ? newRecordPicker.values() : [],
  };
}

// 提交成功后恢复普通模式：开关复位、界面还原，避免用户无意识连续速记。

function resetQuickMode() {
  const toggle = $("#problem-quick");
  if (toggle) toggle.value = "full";
  for (const id of ["#problem-skip-thinking", "#problem-skip-mistakes"]) {
    if ($(id)) $(id).checked = false;
  }
  applyQuickMode(false);
}

$("#problem-quick")?.addEventListener("change", (event) => {
  applyQuickMode(event.currentTarget.value !== "full");
});
for (const id of ["#problem-skip-thinking", "#problem-skip-mistakes"]) {
  $(id)?.addEventListener("change", () => applyQuickMode($("#problem-quick")?.value !== "full"));
}

$("#problem-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const anchor = sealAnchorPoint(event.submitter || form.querySelector('[type="submit"]:not([hidden])'));
  const cameFromPhoto = Boolean(photoRecognition);
  const quick = isQuickMode(); // 提交瞬间锁定模式，避免请求在途时开关被拨动
  const mode = $("#problem-quick")?.value || "full";
  const skipped = mode === "full" && quick;
  const epoch = sessionEpoch;
  const owner = user?.id;
  const payload = buildProblemPayload(form, quick);
  const isCurrent = () => epoch === sessionEpoch && owner === user?.id && Boolean(user);
  // quick 接口允许缺少证据，但完整记录只能跳过明确勾选的部分。
  if (skipped) {
    for (const [name, label] of [["code", "代码或解题过程"], ["thinking", "思路"]]) {
      if (name === "thinking" && $("#problem-skip-thinking")?.checked) continue;
      if (String(payload[name] || "").trim()) continue;
      message(`请填写${label}${name === "thinking" ? "，或勾选暂时跳过思路" : ""}。`, true);
      const field = form.querySelector(`[name="${name}"]`);
      const fold = field?.closest(".form-fold")?.querySelector(".form-fold-toggle");
      if (fold?.getAttribute("aria-expanded") === "false") fold.click();
      field?.focus();
      return;
    }
  }
  if (mode === "sentence" && !payload.mistakes[0]) {
    message("请写一句真正的错因。", true);
    $("#problem-sentence")?.focus();
    return;
  }

  run(async () => {
    let created;
    try {
      created = await api("/api/problems", {
        method: "POST",
        body: JSON.stringify(payload),
      });
    } catch (error) {
      if (!isCurrent()) return;
      throw error;
    }
    if (!isCurrent()) return;
    stampSeal("已录", { anchor });
    notifyDataChanged("create");

    form.reset();
    newRecordPicker?.setValues([]); // 套路选择不在 <form> 里，重置表单时单独清掉
    resetQuickMode(); // 成功后恢复普通模式（开关复位）
    applyZoneFieldMode(form, form.zone.value);
    $("#mistake-inputs").replaceChildren();
    addMistakeInput();
    resetPhotoForm();

    let noticeText = quick
      ? (mode === "sentence" ? "一句话记录已保存，已加入今日复习。"
        : (mode === "full" ? "记录已保存，暂时跳过的内容可以稍后补充。" : "速记已保存，记得回来补上代码、思路和错因。"))
      : "记录已保存，新的易错点已加入今日复习。";
    if (cameFromPhoto && !skipped && created.mistake_ids[0]) {
      try {
        await api(`/api/mistakes/${created.mistake_ids[0]}/variants`, { method: "POST" });
        if (!isCurrent()) return;
        noticeText = "记录已保存，已根据识别结果自动生成练习题。";
      } catch (error) {
        if (!isCurrent()) return;
        noticeText = `记录已保存，但自动生成练习题失败：${error.message}`;
      }
    }

    try {
      await showView("today");
      if (!isCurrent()) return;
      await openMistake(created.mistake_ids[0]);
      if (!isCurrent()) return;
      message(noticeText);
    } catch (error) {
      if (!isCurrent()) return;
      throw error;
    }
  });
});
