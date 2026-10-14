/* 选项页：后端地址 + token（存 chrome.storage.local）。 */
"use strict";

const baseUrlEl = document.getElementById("baseUrl");
const tokenEl = document.getElementById("token");
const msgEl = document.getElementById("msg");
const resultNudgeEl = document.getElementById("resultNudge");

function showMsg(text, cls) {
  msgEl.textContent = text;
  msgEl.className = cls || "";
}

async function load() {
  const s = await chrome.storage.local.get(["baseUrl", "token", "resultNudgeEnabled"]);
  baseUrlEl.value = s.baseUrl || "https://ouyeoy.com";
  tokenEl.value = s.token || "";
  // 默认开：只有显式设为 false 才关。
  resultNudgeEl.checked = s.resultNudgeEnabled !== false;
}

document.getElementById("saveBtn").addEventListener("click", async () => {
  const raw = baseUrlEl.value.trim().replace(/\/+$/, "") || "https://ouyeoy.com";
  let origin;
  try {
    const u = new URL(raw);
    if (u.protocol !== "https:" && u.protocol !== "http:") throw new Error("proto");
    origin = u.origin;
  } catch (e) {
    showMsg("后端地址格式不正确", "err");
    return;
  }
  if (origin !== "https://ouyeoy.com") {
    let granted = false;
    try {
      granted = await chrome.permissions.request({ origins: [origin + "/*"] });
    } catch (e) {
      granted = false;
    }
    if (!granted) {
      showMsg("未授予该地址的访问权限，保存已取消", "err");
      return;
    }
  }
  await chrome.storage.local.set({
    baseUrl: origin,
    token: tokenEl.value.trim(),
    resultNudgeEnabled: !!resultNudgeEl.checked,
  });
  showMsg("已保存", "ok");
});

document.getElementById("testBtn").addEventListener("click", async () => {
  const raw = (baseUrlEl.value.trim() || "https://ouyeoy.com").replace(/\/+$/, "");
  showMsg("连接中…", "");
  try {
    const resp = await fetch(raw + "/api/zones");
    if (resp.ok) showMsg("连接正常（后端有响应）。token 是否有效以实际导入为准。", "ok");
    else showMsg("后端返回 HTTP " + resp.status + "，地址可能不对。", "err");
  } catch (e) {
    showMsg("连不上：" + raw + "。检查地址或网络。", "err");
  }
});

load();
