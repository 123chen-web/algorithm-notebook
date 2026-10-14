/* 与后端 API 相关的纯函数：请求构造、来源白名单。
 * 不含任何 chrome.* 调用，可在 node --test 里直接测。
 * 后端文档见 muse-plugin-api（PLUGIN_API_REPORT.md）。
 */
"use strict";

globalThis.OY = globalThis.OY || {};

/** 后端 POST /api/problems/import-from-extension 接受的 source 白名单。 */
OY.BACKEND_SOURCES = ["leetcode", "leetcode-cn", "codeforces", "nowcoder", "luogu", "atcoder"];

/** 适配器产出的 source → 展示名（含后端暂不支持的来源）。 */
OY.SOURCE_NAMES = {
  leetcode: "LeetCode",
  "leetcode-cn": "力扣中国",
  codeforces: "Codeforces",
  nowcoder: "牛客",
  luogu: "洛谷",
  atcoder: "AtCoder",
};

OY.DEFAULT_BASE_URL = "https://ouyeoy.com";

/** 后端是否支持该来源导入（白名单校验，客户端先行拦截，省一次 422）。 */
OY.isSourceSupported = function isSourceSupported(source) {
  return OY.BACKEND_SOURCES.indexOf(source) !== -1;
};

/** 规范化后端地址；非法返回 null。 */
OY.normalizeBaseUrl = function normalizeBaseUrl(raw) {
  const s = String(raw || "").trim().replace(/\/+$/, "");
  if (!s) return null;
  try {
    const u = new URL(s);
    if (u.protocol !== "https:" && u.protocol !== "http:") return null;
    return u.origin;
  } catch (e) {
    return null;
  }
};

/**
 * 构造导入请求。返回 {url, headers, body}。
 * 硬性要求：token 只出现在 Authorization 头里，绝不进 URL。
 */
OY.buildImportRequest = function buildImportRequest(opts) {
  const base = OY.normalizeBaseUrl(opts.baseUrl);
  if (!base) throw new Error("BAD_BASE_URL");
  const token = String(opts.token || "").trim();
  if (!token) throw new Error("NO_TOKEN");
  const d = opts.data || {};
  const body = {
    source: d.source,
    source_url: d.url,
    title: d.title,
    content: d.content,
    difficulty: d.difficulty || "",
    tags: Array.isArray(d.tags) ? d.tags.slice(0, 20) : [],
    zone: "算法",
  };
  if (d.language) body.language = d.language;
  // 一句话错因：后端按速记模式建一条错题（最多 500 字）。
  if (d.pending_reason) body.pending_reason = String(d.pending_reason).trim().slice(0, 500);
  // 判题类型：WA/TLE/MLE/RE/CE（后端 error_taxonomy.VERDICTS 校验，非法会被 422）。
  if (d.verdict) body.verdict = String(d.verdict).trim();
  // 用户本次提交的代码：后端存入 problems.code；超长截断，截断信息通过返回值告知调用方。
  let codeTruncated = false;
  if (d.submitted_code) {
    const CODE_LIMIT = 20000;
    const code = String(d.submitted_code);
    if (code.length > CODE_LIMIT) {
      body.submitted_code = code.slice(0, CODE_LIMIT);
      codeTruncated = true;
    } else {
      body.submitted_code = code;
    }
  }
  return {
    url: base + "/api/problems/import-from-extension",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
      "X-CSRF-Protection": "1",
    },
    body,
    codeTruncated,
  };
};

/** 连接状态检测用的请求（GET /api/me，带 token）。 */
OY.buildMeRequest = function buildMeRequest(opts) {
  const base = OY.normalizeBaseUrl(opts.baseUrl);
  if (!base) throw new Error("BAD_BASE_URL");
  const token = String(opts.token || "").trim();
  if (!token) throw new Error("NO_TOKEN");
  return {
    url: base + "/api/me",
    headers: { Authorization: "Bearer " + token, "X-CSRF-Protection": "1" },
  };
};

/** 查询今日待复习条数（GET /api/review/due-count，带 token）。 */
OY.buildDueCountRequest = function buildDueCountRequest(opts) {
  const base = OY.normalizeBaseUrl(opts.baseUrl);
  if (!base) throw new Error("BAD_BASE_URL");
  const token = String(opts.token || "").trim();
  if (!token) throw new Error("NO_TOKEN");
  return {
    url: base + "/api/review/due-count",
    headers: { Authorization: "Bearer " + token, "X-CSRF-Protection": "1" },
  };
};

/** 给已收录的题追加一条错因（POST /api/problems/{id}/mistakes，带 token）。 */
OY.buildAppendReasonRequest = function buildAppendReasonRequest(opts) {
  const base = OY.normalizeBaseUrl(opts.baseUrl);
  if (!base) throw new Error("BAD_BASE_URL");
  const token = String(opts.token || "").trim();
  if (!token) throw new Error("NO_TOKEN");
  const id = Number(opts.problemId);
  if (!Number.isInteger(id) || id <= 0) throw new Error("BAD_PROBLEM_ID");
  const reason = String(opts.reason || "").trim().slice(0, 500);
  if (!reason) throw new Error("NO_REASON");
  return {
    url: base + "/api/problems/" + id + "/mistakes",
    headers: {
      "Content-Type": "application/json",
      Authorization: "Bearer " + token,
      "X-CSRF-Protection": "1",
    },
    body: { reason, quick: true },
  };
};
