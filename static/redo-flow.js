/* 复习=重做：共享逻辑（默写对照 diff、按做题结果计分、计时、evidence 组装）和三个界面零件。
   对外契约：window.RedoFlow = {
     MODES, MODE_LABEL, normalize, diffLines, similarityToGrade,
     redoGradeSuggest, qualityFromEvidence, buildEvidence, fetchHint, startTimer,
     RESULT_OPTIONS, QUALITY_LABEL, buildResultAsk, buildRewriteSubmit, buildSimilarPanel,
   }。
   成绩由做题结果决定：界面只上报“做题结果”，不再让用户二次选分；
   qualityFromEvidence 必须与服务端 review_verify.quality_from_evidence 保持一致（服务端会重算并覆盖）。
   纯逻辑部分无 DOM 依赖（startTimer 在无 document 时跳过可见性监听），可在 node 测试里直接加载；
   三个界面零件只在调用时才碰 document。 */
(() => {
  "use strict";

  const MODES = ["recall", "rewrite", "redo", "similar"];
  const MODE_LABEL = { recall: "自评", rewrite: "默写", redo: "限时重做", similar: "类似题" };
  const REDO_LIMITS = [10, 15, 30];
  const DEFAULT_REDO_LIMIT = 15;
  const ALLOWED_KEYS = new Set(["suggested", "final", "similarity", "seconds", "result", "limit_seconds", "auto", "ref"]);
  const RESULTS = ["clean", "retry", "hinted", "failed"];
  const RESULT_OPTIONS = [
    { key: "clean", label: "一次通过，没看提示" },
    { key: "retry", label: "改了几次才通过" },
    { key: "hinted", label: "看了提示才做出来" },
    { key: "failed", label: "没做出来，或看了题解" },
  ];
  const QUALITY_LABEL = { 0: "答错了", 3: "很吃力", 4: "记得", 5: "很熟" };

  // 去掉行注释（//、#）和块注释（/* */），再按行切分。
  // 说明：字符串字面量里恰好出现 // 或 # 会被误伤——默写对照是学习工具，
  // 近似处理即可，REPORT 里已注明这个取舍。
  function stripComments(code) {
    const out = [];
    let i = 0;
    let inBlock = false;
    let inString = null; // ', ", `
    while (i < code.length) {
      const ch = code[i];
      const next = code[i + 1];
      if (inBlock) {
        if (ch === "*" && next === "/") { inBlock = false; i += 2; }
        else i += 1;
        continue;
      }
      if (inString) {
        out.push(ch);
        if (ch === "\\") { out.push(next || ""); i += 2; continue; }
        if (ch === inString) inString = null;
        i += 1;
        continue;
      }
      if (ch === "'" || ch === '"' || ch === "`") { inString = ch; out.push(ch); i += 1; continue; }
      if (ch === "/" && next === "/") { while (i < code.length && code[i] !== "\n") i += 1; continue; }
      if (ch === "/" && next === "*") { inBlock = true; i += 2; continue; }
      if (ch === "#") { while (i < code.length && code[i] !== "\n") i += 1; continue; }
      out.push(ch);
      i += 1;
    }
    return out.join("");
  }

  // 规范化：去注释 → 去首尾空白 → 丢空行 → 行内多余空白压成单个空格。
  function normalize(code) {
    return stripComments(String(code || ""))
      .split("\n")
      .map((line) => line.trim().replace(/\s+/g, " "))
      .filter((line) => line.length > 0);
  }

  // 多重集逐行对比：missing=期望有但没写出的行，extra=多写出来的行。
  // similarity 用 Dice 系数：2*matched/(期望行数+实际行数)，保留两位小数。
  function diffLines(expectedCode, actualCode) {
    const expected = normalize(expectedCode);
    const actual = normalize(actualCode);
    const countOf = (lines) => {
      const map = new Map();
      for (const line of lines) map.set(line, (map.get(line) || 0) + 1);
      return map;
    };
    const countE = countOf(expected);
    const countA = countOf(actual);
    const missing = [];
    const extra = [];
    let matched = 0;
    for (const [line, n] of countE) {
      const m = countA.get(line) || 0;
      matched += Math.min(n, m);
      for (let k = 0; k < n - m; k += 1) missing.push(line);
    }
    for (const [line, n] of countA) {
      const m = countE.get(line) || 0;
      for (let k = 0; k < n - m; k += 1) extra.push(line);
    }
    const total = expected.length + actual.length;
    const similarity = total === 0 ? 1 : Math.round((2 * matched / total) * 100) / 100;
    return { missing, extra, similarity, expectedLines: expected, actualLines: actual };
  }

  // 相似度→建议分：>=0.85 → 4，0.6–0.85 → 3，<0.6 → 0。
  function similarityToGrade(similarity) {
    if (similarity >= 0.85) return 4;
    if (similarity >= 0.6) return 3;
    return 0;
  }

  // 限时重做建议分：一次做对→4，用时不到时限一半→5。
  function redoGradeSuggest(elapsedSeconds, limitSeconds) {
    return elapsedSeconds < limitSeconds / 2 ? 5 : 4;
  }

  // 组装 evidence 小 JSON：校验类型与 JS 码元长度 ≤ 500，非法抛错。
  function buildEvidence(evidenceIn = {}) {
    // 先检查键：解构会静默丢掉未知键，所以检查必须在解构之前。
    for (const key of Object.keys(evidenceIn)) {
      if (!ALLOWED_KEYS.has(key)) throw new Error(`evidence 含未知字段: ${key}`);
    }
    const { suggested, final, similarity, seconds, result, limit_seconds: limitSeconds, auto, ref } = evidenceIn;
    const evidence = {};
    if (suggested !== undefined) {
      if (!Number.isInteger(suggested) || suggested < 0 || suggested > 5) {
        throw new Error("suggested 必须是 0 到 5 的整数");
      }
      evidence.suggested = suggested;
    }
    if (final !== undefined) {
      if (!Number.isInteger(final) || final < 0 || final > 5) {
        throw new Error("final 必须是 0 到 5 的整数");
      }
      evidence.final = final;
    }
    if (similarity !== undefined) {
      if (typeof similarity !== "number" || !(similarity >= 0 && similarity <= 1)) {
        throw new Error("similarity 必须是 0 到 1 的数字");
      }
      evidence.similarity = similarity;
    }
    if (seconds !== undefined) {
      if (!Number.isInteger(seconds) || seconds < 0) {
        throw new Error("seconds 必须是非负整数");
      }
      evidence.seconds = seconds;
    }
    if (limitSeconds !== undefined) {
      if (!Number.isInteger(limitSeconds) || limitSeconds < 0) {
        throw new Error("limit_seconds 必须是非负整数");
      }
      evidence.limit_seconds = limitSeconds;
    }
    if (result !== undefined) {
      if (!RESULTS.includes(result)) throw new Error("result 不合法");
      evidence.result = result;
    }
    if (auto !== undefined) {
      if (auto !== true) throw new Error("auto 只能是 true");
      evidence.auto = true;
    }
    if (ref !== undefined) {
      if (typeof ref !== "string" || ref.length > 60) throw new Error("ref 必须是不超过 60 字的文字");
      evidence.ref = ref;
    }
    const text = JSON.stringify(evidence);
    const units = typeof text === "string" ? text.length : 0;
    if (units > 500) throw new Error("evidence 长度不能超过 500 字符");
    return text;
  }

  async function fetchHint(api, mistakeId) {
    const data = await api(`/api/mistakes/${mistakeId}/redo-hint`);
    return Boolean(data && data.hint);
  }

  // 倒计时：页面不可见时自动暂停（无 document 的环境跳过监听）。
  // 返回 { cancel, elapsed }。
  function startTimer(totalSeconds, onTick, onDone) {
    let remaining = totalSeconds;
    let timer = null;
    let done = false;
    const hasDocument = typeof document !== "undefined";
    const stop = () => { if (timer) { clearInterval(timer); timer = null; } };
    const finish = () => {
      if (done) return;
      done = true;
      stop();
      if (hasDocument) document.removeEventListener("visibilitychange", onVisibility);
      onDone();
    };
    const onVisibility = () => {
      if (!hasDocument) return;
      if (document.hidden) stop();
      else if (!done && !timer) timer = setInterval(tick, 1000);
    };
    const tick = () => {
      remaining -= 1;
      onTick(Math.max(remaining, 0));
      if (remaining <= 0) finish();
    };
    if (hasDocument) document.addEventListener("visibilitychange", onVisibility);
    timer = setInterval(tick, 1000);
    onTick(remaining);
    return {
      cancel: () => {
        if (done) return;
        done = true;
        stop();
        if (hasDocument) document.removeEventListener("visibilitychange", onVisibility);
      },
      elapsed: () => totalSeconds - Math.max(remaining, 0),
    };
  }

  // 按做题结果算成绩（0 答错 / 3 很吃力 / 4 记得 / 5 很熟），规则与服务端一致：
  // 失败 0；看了提示 3；超时 3；一次通过 4；只有“原题重做、一次通过、用时不超过时限 1/3”才是 5；
  // 默写看逐行相似度（>=0.85 记得，>=0.6 很吃力，否则答错），不给 5。
  function qualityFromEvidence(mode, fields) {
    if (mode === "rewrite") {
      const similarity = fields.similarity;
      if (typeof similarity !== "number") throw new Error("默写缺少相似度");
      if (similarity >= 0.85) return 4;
      return similarity >= 0.6 ? 3 : 0;
    }
    if (mode !== "redo" && mode !== "similar") throw new Error("只有默写、重做和类似题可以按结果计分");
    if (!RESULTS.includes(fields.result)) throw new Error("缺少做题结果");
    const { seconds, limit_seconds: limit } = fields;
    const timed = Number.isInteger(seconds) && Number.isInteger(limit) && limit > 0;
    if (fields.result === "failed") return 0;
    if (fields.result === "hinted" || (timed && seconds > limit)) return 3;
    if (fields.result === "clean" && mode === "redo" && timed && seconds * 3 <= limit) return 5;
    return 4;
  }

  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }

  // “做完了吗？”四个按钮：点哪个就是哪个做题结果，成绩由结果算出并直接交给 onPick。
  function buildResultAsk({ mode, seconds, limitSeconds, ref, onPick }) {
    const wrap = el("div", "", "redo-ask");
    wrap.append(el("p", "做完了吗？成绩由做题结果决定，不用自己打分。", "section-label"));
    const row = el("div", "", "redo-actions");
    for (const option of RESULT_OPTIONS) {
      const button = el("button", option.label, "redo-result");
      button.type = "button";
      button.dataset.result = option.key;
      button.addEventListener("click", () => {
        const elapsed = typeof seconds === "function" ? seconds() : seconds;
        const fields = { result: option.key, auto: true };
        if (Number.isInteger(elapsed) && elapsed >= 0) fields.seconds = elapsed;
        if (Number.isInteger(limitSeconds) && limitSeconds > 0) fields.limit_seconds = limitSeconds;
        if (ref) fields.ref = ref.slice(0, 60);
        const quality = qualityFromEvidence(mode, fields);
        const evidence = buildEvidence({ ...fields, suggested: quality, final: quality });
        onPick({ quality, evidence, result: option.key });
      });
      row.append(button);
    }
    wrap.append(row);
    return wrap;
  }

  // 默写：显示按相似度算出的成绩，一个按钮提交。
  function buildRewriteSubmit({ similarity, onPick }) {
    const quality = qualityFromEvidence("rewrite", { similarity });
    const wrap = el("div", "", "redo-ask");
    wrap.append(el("p", `按相似度计分：${QUALITY_LABEL[quality]}（默写最高算“记得”）`, "redo-suggest"));
    const button = el("button", `提交成绩：${QUALITY_LABEL[quality]}`, "primary redo-submit");
    button.type = "button";
    button.dataset.quality = String(quality);
    button.addEventListener("click", () => {
      const evidence = buildEvidence({ similarity, auto: true, suggested: quality, final: quality });
      onPick({ quality, evidence });
    });
    wrap.append(button);
    return wrap;
  }

  // 做类似题：从“举一反三”接口挑同套路、你没做过的题；做对说明真改掉了这个错，而不是记住了这一题。
  function buildSimilarPanel({ api, mistakeId, onPick, quickAdd, onTimer, isAlive = () => true }) {
    const panel = el("div", "", "redo-panel");
    panel.append(el("h4", "做一道类似题"));
    const body = el("div");
    panel.append(body);
    let items = [];
    let index = 0;
    let timer = null;
    const select = document.createElement("select");
    select.setAttribute("aria-label", "类似题时限");
    for (const minutes of REDO_LIMITS) {
      const option = el("option", `${minutes} 分钟`);
      option.value = String(minutes);
      if (minutes === DEFAULT_REDO_LIMIT) option.selected = true;
      select.append(option);
    }
    const limitSeconds = () => Number(select.value) * 60;
    const addFailed = document.createElement("input");
    addFailed.type = "checkbox";
    addFailed.checked = true;
    addFailed.id = `similar-add-${mistakeId}`;

    function render() {
      if (!isAlive()) return;
      body.replaceChildren();
      const item = items[index];
      const head = el("p", "", "similar-pick");
      head.append(el("strong", item.title || ""));
      const meta = [item.source, item.difficulty != null ? String(item.difficulty) : ""].filter(Boolean).join(" · ");
      if (meta) head.append(document.createTextNode(`  ${meta}`));
      body.append(head);
      if (item.reason) body.append(el("p", item.reason, "muted"));
      const limitRow = el("div", "", "redo-limit-row");
      limitRow.append(el("span", "时限"), select);
      const start = el("button", "开始，在新标签页打开", "primary");
      start.type = "button";
      const other = el("button", "换一道", "redo-other");
      other.type = "button";
      other.hidden = items.length < 2;
      other.addEventListener("click", () => { index = (index + 1) % items.length; render(); });
      const timerEl = el("p", "", "redo-timer");
      timerEl.setAttribute("role", "timer");
      const askHost = el("div");
      const label = document.createElement("label");
      label.htmlFor = addFailed.id;
      label.className = "redo-add-failed";
      label.append(addFailed, document.createTextNode(" 没做出来时，把这道题也记进错题本"));
      const showAsk = () => {
        askHost.replaceChildren(buildResultAsk({
          mode: "similar", seconds: () => (timer ? timer.elapsed() : 0), limitSeconds: limitSeconds(), ref: item.title || "",
          onPick: async (picked) => {
            if (timer) { timer.cancel(); timer = null; if (onTimer) onTimer(null); }
            if (picked.result === "failed" && addFailed.checked && quickAdd && item.add_payload) {
              try { await quickAdd(item); } catch (error) { /* 记错题失败不影响这次复习成绩 */ }
            }
            onPick(picked);
          },
        }), label);
      };
      start.addEventListener("click", () => {
        window.open(item.url, "_blank", "noopener");
        start.disabled = true; select.disabled = true; other.disabled = true;
        timer = startTimer(
          limitSeconds(),
          (remaining) => { timerEl.textContent = `剩余 ${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, "0")}`; },
          () => { timerEl.textContent = "时间到"; showAsk(); },
        );
        if (onTimer) onTimer(timer);
        showAsk();
      });
      body.append(limitRow, start, other, timerEl, askHost);
    }

    body.append(el("p", "正在找一道类似题…", "muted"));
    (async () => {
      try {
        const data = await api(`/api/review/similar?mistake_id=${encodeURIComponent(mistakeId)}`);
        if (!isAlive()) return;
        items = ((data && data.items) || []).filter((item) => item && item.url);
        if (!items.length) {
          body.replaceChildren(el("p", "暂时没有合适的类似题（需要带题目链接的同套路题），换一种方式复习吧。", "muted"));
          return;
        }
        render();
      } catch (error) {
        if (isAlive()) body.replaceChildren(el("p", `加载失败：${error.message || "请稍后重试"}`, "muted"));
      }
    })();
    return panel;
  }

  window.RedoFlow = {
    MODES, MODE_LABEL, REDO_LIMITS, DEFAULT_REDO_LIMIT,
    normalize, diffLines, similarityToGrade, redoGradeSuggest, qualityFromEvidence,
    buildEvidence, fetchHint, startTimer,
    RESULT_OPTIONS, QUALITY_LABEL, buildResultAsk, buildRewriteSubmit, buildSimilarPanel,
  };
})();
