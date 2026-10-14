/* 判题结果映射：各 OJ 结果页的原文 → 欧叶OY 的 verdict code。
 *
 * code 取值与后端 error_taxonomy.VERDICTS 一致：
 *   WA（答案错误）、TLE（超时）、MLE（超内存）、RE（运行错误）、CE（编译错误）。
 * 通过（AC 及各类 Accepted）映射为 null，调用方据此决定不弹提示。
 *
 * 表驱动：每个站点一个数组，逐项用正则匹配原文；顺序重要，
 * 更具体的写前面。补新站点/新文案时只加表项，不改逻辑。
 *
 * 注意：这些原文是静态推断（2026-10-14），未在真实页面逐一验证；
 * 匹配不到时返回 null（= 未知，不弹），绝不猜测。
 */
"use strict";

globalThis.OY = globalThis.OY || {};

/**
 * 站点 id → [{re: RegExp, code: "WA"|"TLE"|"MLE"|"RE"|"CE"|null}]
 * code 为 null 表示"通过"，调用方不应弹提示。
 */
OY.VERDICT_TABLE = {
  atcoder: [
    // AtCoder 提交页状态列原文（英文缩写）。
    { re: /\bAC\b/, code: null },
    { re: /\bWA\b/, code: "WA" },
    { re: /\bTLE\b/, code: "TLE" },
    { re: /\bMLE\b/, code: "MLE" },
    { re: /\bRE\b/, code: "RE" },
    { re: /\bCE\b/, code: "CE" },
  ],
  codeforces: [
    // Codeforces 提交页 verdict 文本。
    { re: /Accepted/i, code: null },
    { re: /Wrong answer/i, code: "WA" },
    { re: /Time limit exceeded/i, code: "TLE" },
    { re: /Memory limit exceeded/i, code: "MLE" },
    { re: /Runtime error/i, code: "RE" },
    { re: /Compilation error/i, code: "CE" },
    { re: /Idleness limit exceeded/i, code: "TLE" },
  ],
  "leetcode-cn": [
    // 力扣中国站提交结果文案（中文）。
    { re: /通过|执行用时/, code: null },
    { re: /解答错误/, code: "WA" },
    { re: /超出时间限制/, code: "TLE" },
    { re: /超出内存限制/, code: "MLE" },
    { re: /运行出错/, code: "RE" },
    { re: /编译出错/, code: "CE" },
  ],
  // leetcode.com 与 leetcode.cn 共用同一套中文文案（cn 站必为中文）。
  leetcode: [
    { re: /通过|执行用时/, code: null },
    { re: /解答错误/, code: "WA" },
    { re: /超出时间限制/, code: "TLE" },
    { re: /超出内存限制/, code: "MLE" },
    { re: /运行出错/, code: "RE" },
    { re: /编译出错/, code: "CE" },
  ],
  luogu: [
    // 洛谷评测记录页状态（英文）。
    { re: /\bAccepted\b/i, code: null },
    { re: /Wrong Answer/i, code: "WA" },
    { re: /Time Limit Exceeded/i, code: "TLE" },
    { re: /Memory Limit Exceeded|Out Of Memory/i, code: "MLE" },
    { re: /Runtime Error/i, code: "RE" },
    { re: /Compile Error/i, code: "CE" },
  ],
  nowcoder: [
    // 牛客：尽力而为。中文文案为主，拿不准的不写。
    { re: /答案正确|通过/, code: null },
    { re: /答案错误/, code: "WA" },
    { re: /运行超时|超时/, code: "TLE" },
    { re: /内存超限/, code: "MLE" },
    { re: /运行错误|段错误/, code: "RE" },
    { re: /编译错误/, code: "CE" },
  ],
};

/**
 * 把某站点的判题原文映射为 verdict code。
 * 返回 "WA"|"TLE"|"MLE"|"RE"|"CE"、null（通过）或 undefined（未知）。
 */
OY.mapVerdict = function mapVerdict(siteId, rawText) {
  const table = OY.VERDICT_TABLE[siteId];
  if (!table) return undefined;
  const text = String(rawText || "");
  if (!text) return undefined;
  for (const entry of table) {
    if (entry.re.test(text)) return entry.code;
  }
  return undefined;
};

/** 是否为失败判定（需要弹提示）。 */
OY.isFailedVerdict = function isFailedVerdict(code) {
  return code === "WA" || code === "TLE" || code === "MLE" || code === "RE" || code === "CE";
};

/** verdict code → 中文展示名（用于提示条文案）。 */
OY.VERDICT_NAMES = {
  WA: "答案错误",
  TLE: "超时",
  MLE: "超内存",
  RE: "运行错误",
  CE: "编译错误",
};
