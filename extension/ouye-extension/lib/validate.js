/* 表单校验纯函数。与后端约束对齐：
 * title 1–200 字，content 1–100000 字（超限后端 422），difficulty ≤40 字。
 */
"use strict";

globalThis.OY = globalThis.OY || {};

OY.LIMITS = { title: 200, content: 100000, difficulty: 40, reason: 500, tags: 20 };

/**
 * 校验"记到欧叶OY"表单。
 * 返回 {ok, errors[], title, content, reason, needsManualContent}。
 * errors 取值：title.empty / title.too_long / content.too_long / reason.too_long。
 * 题面为空不算错误（needsManualContent=true，提示用户手动粘贴或留空）。
 */
OY.validateImportForm = function validateImportForm(input) {
  const errors = [];
  const title = String((input && input.title) || "").trim();
  const content = String((input && input.content) || "").trim();
  const reason = String((input && input.reason) || "").trim();
  const difficulty = String((input && input.difficulty) || "").trim();

  if (!title) errors.push("title.empty");
  else if (title.length > OY.LIMITS.title) errors.push("title.too_long");
  if (content.length > OY.LIMITS.content) errors.push("content.too_long");
  if (difficulty.length > OY.LIMITS.difficulty) errors.push("difficulty.too_long");
  if (reason.length > OY.LIMITS.reason) errors.push("reason.too_long");

  return {
    ok: errors.length === 0,
    errors,
    title,
    content,
    reason,
    difficulty,
    needsManualContent: content.length === 0,
  };
};
