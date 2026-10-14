/* 工具栏弹窗：连接状态 / 统计 / 后备导入通道。
 * "打开欧叶OY"一律用 homeUrl（用户配置的后端地址），不拼 /problems/<id>
 *（v1 的 404 修法，用户已在自修版中验证）。
 */
"use strict";

const connDot = document.getElementById("connDot");
const connText = document.getElementById("connText");
const reviewCount = document.getElementById("reviewCount");
const recordedCount = document.getElementById("recordedCount");
const pendingCount = document.getElementById("pendingCount");
const pageInfo = document.getElementById("pageInfo");
const pageTitle = document.getElementById("pageTitle");
const importBtn = document.getElementById("importBtn");
const openBtn = document.getElementById("openBtn");
const optionsBtn = document.getElementById("optionsBtn");
const msgEl = document.getElementById("msg");

let homeUrl = "https://ouyeoy.com";
let pageData = null;

function sendMsg(msg) {
  return new Promise((resolve) => {
    try {
      chrome.runtime.sendMessage(msg, (r) => resolve(r || null));
    } catch (e) {
      resolve(null);
    }
  });
}

function showMsg(text, ok) {
  msgEl.textContent = text;
  msgEl.className = ok ? "ok" : "";
}

function srcName(s) {
  const names = {
    leetcode: "LeetCode",
    "leetcode-cn": "力扣中国",
    codeforces: "Codeforces",
    nowcoder: "牛客",
    luogu: "洛谷",
    atcoder: "AtCoder",
  };
  return names[s] || s;
}

async function initConnection() {
  const st = await sendMsg({ type: "ouye-connection" });
  if (!st) {
    connDot.className = "dot bad";
    connText.textContent = "检测失败";
    return;
  }
  homeUrl = (st.baseUrl || homeUrl).replace(/\/+$/, "");
  const map = {
    ok: ["dot ok", "已连接"],
    "bad-token": ["dot bad", "token 无效"],
    unreachable: ["dot bad", "连不上后端"],
    "no-token": ["dot warn", "未配置 token"],
    "no-config": ["dot warn", "未配置"],
    "bad-config": ["dot bad", "地址格式错误"],
    "no-permission": ["dot warn", "未授权该地址"],
  };
  const m = map[st.status] || map.unreachable;
  connDot.className = m[0];
  connText.textContent = m[1];
}

async function initStats() {
  const st = await sendMsg({ type: "ouye-stats" });
  if (st) {
    recordedCount.textContent = String(st.imported);
    pendingCount.textContent = String(st.pendingReasons);
  }
  const due = await sendMsg({ type: "ouye-due-count" });
  reviewCount.textContent = due && due.ok ? String(due.count) : "—";
}

async function initPage() {
  let tab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = tabs && tabs[0];
  } catch (e) {}
  if (!tab || !tab.id || !tab.url) {
    pageInfo.textContent = "读不到当前标签页。";
    return;
  }
  let extracted = null;
  try {
    extracted = await chrome.tabs.sendMessage(tab.id, { type: "ouye-extract-quick" });
  } catch (e) {
    extracted = null;
  }
  if (!extracted || !extracted.ok) {
    pageInfo.textContent =
      extracted && extracted.error === "EXTRACT_FAILED"
        ? "这页没能识别出题目（网站可能改版了）。"
        : "当前页不是支持的题目页。";
    return;
  }
  pageData = extracted.data;
  pageInfo.textContent =
    srcName(pageData.source) +
    (pageData.difficulty ? " · " + pageData.difficulty : "") +
    (pageData.truncated ? " · 题面过长已截断" : "");
  pageTitle.textContent = pageData.title;
  importBtn.disabled = false;
}

importBtn.addEventListener("click", async () => {
  let tab = null;
  try {
    const tabs = await chrome.tabs.query({ active: true, currentWindow: true });
    tab = tabs && tabs[0];
  } catch (e) {}
  if (!tab || !tab.id) {
    showMsg("读不到当前标签页。", false);
    return;
  }
  try {
    await chrome.tabs.sendMessage(tab.id, { type: "ouye-open-form" });
    window.close();
  } catch (e) {
    showMsg("页面脚本未响应，请刷新题目页后重试。", false);
  }
});

openBtn.addEventListener("click", () => {
  chrome.tabs.create({ url: homeUrl });
});

optionsBtn.addEventListener("click", () => {
  chrome.runtime.openOptionsPage();
});

initConnection();
initStats();
initPage();
