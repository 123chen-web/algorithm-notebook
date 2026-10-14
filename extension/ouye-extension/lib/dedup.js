/* 重复收录检测的纯函数。实际读写 chrome.storage.local 的在 background.js。 */
"use strict";

globalThis.OY = globalThis.OY || {};

/** 去重键：source + source_id。 */
OY.recordKey = function recordKey(source, source_id) {
  return String(source || "") + "::" + String(source_id || "");
};

/**
 * 查本地收录记录。records: {key: record}。
 * 返回命中的 record 或 null。
 */
OY.checkDuplicate = function checkDuplicate(records, source, source_id) {
  if (!records || !source || !source_id) return null;
  const rec = records[OY.recordKey(source, source_id)];
  return rec || null;
};

/** 构造一条收录记录（含暂存的错因，待后端支持追加接口后同步）。 */
OY.makeRecord = function makeRecord(data, extra) {
  const e = extra || {};
  return {
    title: data.title || "",
    source: data.source || "",
    source_id: data.source_id || "",
    url: data.url || "",
    importedAt: e.importedAt || new Date().toISOString(),
    problemId: e.problemId != null ? e.problemId : null,
    pendingReason: e.pendingReason || "",
  };
};

/** 统计：已收录数 / 有待同步错因的条数。 */
OY.summarizeRecords = function summarizeRecords(records) {
  let imported = 0;
  let pendingReasons = 0;
  if (records) {
    for (const k of Object.keys(records)) {
      imported += 1;
      if (records[k] && records[k].pendingReason) pendingReasons += 1;
    }
  }
  return { imported, pendingReasons };
};

/* ---- 结果页提示去重 ---- */

/** 结果页提示去重键：source + submissionId（同一次提交只弹一次）。 */
OY.resultPromptKey = function resultPromptKey(source, submissionId) {
  return "result::" + String(source || "") + "::" + String(submissionId || "");
};

/**
 * 是否已对某次提交弹过提示。prompted: {key: timestamp}。
 * 返回 true 表示已弹过，不应再弹。
 */
OY.hasPromptedResult = function hasPromptedResult(prompted, source, submissionId) {
  if (!prompted || !source || !submissionId) return false;
  return !!prompted[OY.resultPromptKey(source, submissionId)];
};

/** 标记某次提交已弹过提示。返回新的 prompted 对象（不改原对象）。 */
OY.markResultPrompted = function markResultPrompted(prompted, source, submissionId) {
  const next = Object.assign({}, prompted || {});
  if (source && submissionId) {
    next[OY.resultPromptKey(source, submissionId)] = new Date().toISOString();
  }
  return next;
};

/** "今天不再提醒这道题" 的键：source + problemKey + 日期。 */
OY.resultMuteKey = function resultMuteKey(source, problemKey, dateStr) {
  return "muted::" + String(source || "") + "::" + String(problemKey || "") + "::" + String(dateStr || "");
};

/** 今天（dateStr，YYYY-MM-DD）是否已对该题静音。 */
OY.isResultMutedToday = function isResultMutedToday(muted, source, problemKey, dateStr) {
  if (!muted || !source || !problemKey || !dateStr) return false;
  return !!muted[OY.resultMuteKey(source, problemKey, dateStr)];
};

/** 标记今天不再提醒这道题。返回新的 muted 对象。 */
OY.muteResultToday = function muteResultToday(muted, source, problemKey, dateStr) {
  const next = Object.assign({}, muted || {});
  if (source && problemKey && dateStr) {
    next[OY.resultMuteKey(source, problemKey, dateStr)] = new Date().toISOString();
  }
  return next;
};

/** 当地日期 YYYY-MM-DD（用于"今天不再提醒"）。 */
OY.todayStr = function todayStr(d) {
  const t = d || new Date();
  const y = t.getFullYear();
  const m = String(t.getMonth() + 1).padStart(2, "0");
  const day = String(t.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
};
