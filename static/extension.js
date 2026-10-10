"use strict";

(() => {
  const button = document.getElementById("ext-make-token");
  const box = document.getElementById("ext-token-box");
  const input = document.getElementById("ext-token");
  const copy = document.getElementById("ext-copy");
  const status = document.getElementById("ext-status");
  if (!button || !box || !input || !copy || !status) return;

  function say(text) {
    status.textContent = text;
  }

  button.addEventListener("click", async () => {
    button.disabled = true;
    say("正在生成…");
    try {
      const response = await fetch("/api/users/api-token", {
        method: "POST",
        credentials: "same-origin",
        headers: { "X-CSRF-Protection": "1" },
      });
      if (response.status === 401) {
        say("请先登录欧叶OY（在首页登录后再回到这里）。");
        return;
      }
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.token) {
        say("生成失败，请稍后再试。");
        return;
      }
      input.value = data.token;
      box.hidden = false;
      input.focus();
      input.select();
      say("已生成。请复制后粘贴到扩展的「选项」里；这把密钥只显示这一次。");
    } catch {
      say("网络出错，请稍后再试。");
    } finally {
      button.disabled = false;
    }
  });

  copy.addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText(input.value);
      say("已复制。");
    } catch {
      input.focus();
      input.select();
      say("请按 Ctrl+C 复制选中的密钥。");
    }
  });
})();
