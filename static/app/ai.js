// static/app/ai.js — AI 反馈：回答有用/没用。
// 由 static/app.js 按领域拆分（C2），纯重构、零行为变化。
// 加载顺序与依赖见 static/index.html；领域间调用保持顶层全局函数名直接调用。
"use strict";

function renderAiFeedback(kind, refId) {
  const wrap = element("div", "", "ai-feedback");
  const label = element("span", "这道题对你有帮助吗？", "ai-feedback-label");
  const yesBtn = element("button", "有用", "ai-feedback-btn");
  const noBtn = element("button", "没用", "ai-feedback-btn");
  yesBtn.type = "button";
  noBtn.type = "button";
  let voted = false;

  async function vote(useful) {
    if (voted) return;
    const epoch = sessionEpoch;
    const userId = user?.id;
    if (!userId) return;
    voted = true;
    yesBtn.disabled = true;
    noBtn.disabled = true;
    try {
      await api("/api/ai-feedback", {
        method: "POST",
        body: JSON.stringify({ kind, ref_id: refId ?? null, useful }),
      });
    } catch (error) {
      // 离线或失败时静默：恢复按钮，不打断使用。
      voted = false;
      yesBtn.disabled = false;
      noBtn.disabled = false;
      return;
    }
    // 登出、换号之后晚到的响应一律丢弃。
    if (epoch !== sessionEpoch || user?.id !== userId) return;
    wrap.replaceChildren(element("span", "已记录，谢谢", "ai-feedback-done"));
  }

  yesBtn.addEventListener("click", () => void vote(true));
  noBtn.addEventListener("click", () => void vote(false));
  wrap.append(label, yesBtn, noBtn);
  return wrap;
}
