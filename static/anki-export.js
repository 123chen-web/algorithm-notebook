"use strict";
/* 导出到 Anki（账号菜单内）：范围下拉 + 分区下拉 + 下载按钮 + 三步导入说明。
 *
 * 下载沿用浏览器原生 <a download>：先 fetch 拿到 text/plain 文本（这样 413/429
 * 等错误能在页面上提示），再用 Blob URL 交给浏览器保存。GET 请求只带会话
 * cookie 与同源 CSRF 头，不写任何数据。
 *
 * 迟到响应守卫与其他小部件一致：epoch 在登出/换账号时加一，clickTicket 每次
 * 点击加一；响应回来时对不上就丢弃，绝不触发下载或改写状态。
 * 用户可控文字（分区名、服务器错误信息）一律 textContent，不拼 HTML。
 */
(() => {
  const $ = (selector) => document.querySelector(selector);

  let epoch = 0;
  let clickTicket = 0;
  let zones = [];

  function statusNode() {
    return $("#anki-status");
  }

  function setStatus(text, isError = false) {
    const node = statusNode();
    if (!node) return;
    node.textContent = text;
    node.classList.toggle("is-error", Boolean(isError && text));
  }

  function syncZoneVisibility() {
    const scope = $("#anki-scope");
    const zoneSelect = $("#anki-zone");
    if (!scope || !zoneSelect) return;
    zoneSelect.hidden = scope.value !== "zone";
  }

  function setZones(nextZones) {
    zones = Array.isArray(nextZones) ? [...nextZones] : [];
    const zoneSelect = $("#anki-zone");
    if (!zoneSelect) return;
    zoneSelect.replaceChildren();
    for (const zone of zones) {
      const option = document.createElement("option");
      option.value = zone;
      option.textContent = zone;
      zoneSelect.append(option);
    }
    syncZoneVisibility();
  }

  function filenameFromDisposition(disposition) {
    const match = /filename="?([^"]+)"?/.exec(String(disposition || ""));
    return match ? match[1] : "oy-anki.txt";
  }

  function saveTextFile(text, filename) {
    const blob = new window.Blob([text], { type: "text/plain;charset=utf-8" });
    const url = window.URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.setAttribute("download", filename);
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    window.URL.revokeObjectURL(url);
  }

  async function errorDetail(response) {
    try {
      const data = await response.json();
      if (data && typeof data.detail === "string" && data.detail) return data.detail;
    } catch {
      // 错误响应不是 JSON 时退回通用提示。
    }
    return "导出失败，请稍后重试。";
  }

  async function download() {
    const scope = $("#anki-scope");
    const zoneSelect = $("#anki-zone");
    const button = $("#anki-download");
    if (!scope || !zoneSelect || !button) return;

    const scopeValue = scope.value || "all";
    let zoneValue = "";
    if (scopeValue === "zone") {
      zoneValue = zoneSelect.value;
      if (!zoneValue) {
        setStatus("请先选择要导出的分区。", true);
        return;
      }
    }

    const startedIn = epoch;
    const ticket = ++clickTicket;
    button.disabled = true;
    setStatus("正在生成 Anki 文件…");

    let endpoint = `/api/export/anki?scope=${encodeURIComponent(scopeValue)}`;
    if (scopeValue === "zone") {
      endpoint += `&zone=${encodeURIComponent(zoneValue)}`;
    }

    try {
      const response = await fetch(endpoint, {
        credentials: "same-origin",
        headers: { "X-CSRF-Protection": "1" },
      });
      if (startedIn !== epoch || ticket !== clickTicket) return;
      if (!response.ok) {
        setStatus(await errorDetail(response), true);
        return;
      }
      const text = await response.text();
      if (startedIn !== epoch || ticket !== clickTicket) return;
      const disposition = response.headers
        ? response.headers.get("Content-Disposition")
        : "";
      saveTextFile(text, filenameFromDisposition(disposition));
      setStatus("已开始下载，按下面三步把文件导入 Anki。");
    } catch {
      if (startedIn !== epoch || ticket !== clickTicket) return;
      setStatus("网络出错，文件没有导出，请再试一次。", true);
    } finally {
      if (startedIn === epoch && ticket === clickTicket) {
        button.disabled = false;
      }
    }
  }

  function reset() {
    // 登出或换账号：让所有在途响应失效，下拉与状态回到初始值。
    epoch += 1;
    clickTicket += 1;
    const button = $("#anki-download");
    if (button) button.disabled = false;
    const scope = $("#anki-scope");
    if (scope) scope.value = "all";
    const zoneSelect = $("#anki-zone");
    if (zoneSelect) zoneSelect.hidden = true;
    setStatus("");
  }

  // defer 脚本执行时 DOM 已就绪；假浏览器测试环境同样如此。
  syncZoneVisibility();
  $("#anki-scope")?.addEventListener("change", syncZoneVisibility);
  $("#anki-download")?.addEventListener("click", () => {
    download();
  });

  window.AnkiExport = { setZones, reset };
})();
