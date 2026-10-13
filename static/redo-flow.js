/* 复习=重做：共享纯逻辑（默写对照 diff、相似度→建议分、计时、evidence 组装）。
   对外契约：window.RedoFlow = {
     MODES, MODE_LABEL, normalize, diffLines, similarityToGrade,
     redoGradeSuggest, buildEvidence, fetchHint, startTimer,
   }。
   无 DOM 依赖（startTimer 在无 document 时跳过可见性监听），可在 node 测试里直接加载。 */
(() => {
  "use strict";

  const MODES = ["recall", "rewrite", "redo"];
  const MODE_LABEL = { recall: "自评", rewrite: "默写", redo: "限时重做" };
  const REDO_LIMITS = [10, 15, 30];
  const DEFAULT_REDO_LIMIT = 15;
  const ALLOWED_KEYS = new Set(["suggested", "final", "similarity", "seconds"]);

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
    const { suggested, final, similarity, seconds } = evidenceIn;
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

  window.RedoFlow = {
    MODES, MODE_LABEL, REDO_LIMITS, DEFAULT_REDO_LIMIT,
    normalize, diffLines, similarityToGrade, redoGradeSuggest,
    buildEvidence, fetchHint, startTimer,
  };
})();
