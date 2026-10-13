// static/app/core-api.js — 核心·请求层：$ / 常量 / 会话状态 / api / run / 表单与头像上传。
// 由 static/app/core.js 拆分（C 收尾），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

const $ = (selector) => document.querySelector(selector);
const grades = [
  [0, "完全忘了"],
  [2, "答错了"],
  [3, "很吃力"],
  [4, "记得"],
  [5, "很熟"],
];
const resultLabels = {
  unattempted: "待练习",
  solved: "已解决",
  partial: "部分解决",
  failed: "未解决",
};
let user = null;
let sessionReady = false;
let sessionEpoch = 0;
let resetToken = new URLSearchParams(location.search).get("reset_token");
let view = "today";
let busy = false;

async function api(path, options = {}) {
  const requestEpoch = sessionEpoch;
  const response = await fetch(path, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(typeof FormData !== "undefined" && options.body instanceof FormData
        ? {} : { "Content-Type": "application/json" }),
      "X-CSRF-Protection": "1",
      ...(options.headers || {}),
    },
  });

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    if (response.status === 401 && requestEpoch === sessionEpoch) signedOut();

    let detail = data.detail || "请求失败，请稍后重试";
    if (Array.isArray(detail)) {
      detail = detail
        .map((item) => `${item.loc.join(".")}: ${item.msg}`)
        .join("；");
    } else if (typeof detail === "object") {
      detail = detail.message || "请求失败，请稍后重试";
    }

    const error = new Error(String(detail));
    error.status = response.status;
    error.code = data.code;
    throw error;
  }

  return data;
}

async function uploadAvatarFile(file) {
  const body = new FormData();
  body.append("file", file);
  // 不能像 api() 那样固定 Content-Type: application/json——multipart 请求
  // 的 boundary 必须由浏览器自己生成，手动设置反而会破坏它。
  const response = await fetch("/api/me/avatar", {
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
    const error = new Error(String(text || "上传失败，请稍后重试"));
    error.status = response.status;
    throw error;
  }
  return data;
}

async function run(action) {
  if (busy) return;
  busy = true;
  setBusy(true);

  try {
    await action();
  } catch (error) {
    if (error.status === 409 && user && (view === "today" || view === "all")) {
      try {
        await loadList();
      } catch {
        // 保留原始冲突提示。
      }
    }
    message(error.message || "操作失败，请检查网络后重试", true);
  } finally {
    busy = false;
    setBusy(false);
  }
}

function formObject(form) {
  return Object.fromEntries(new FormData(form).entries());
}
