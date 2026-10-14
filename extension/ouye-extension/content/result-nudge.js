/* 结果页一键录入：在 OJ 提交结果页检测到失败判定时，右下角弹一个不打扰的小条。
 *
 * 隐私：只在本地读 DOM；只有用户点"记下"才向欧叶OY发一次请求；绝不自动上传。
 * 去重：同一次提交只弹一次（键 = source::submissionId）；"今天不再提醒这道题"
 *       按天 + 题目静音。两份名单都存在 chrome.storage.local。
 * 开关：用户可在扩展设置里关掉"结果页提示"（默认开）。
 */
"use strict";

(function () {
  const HOST_ID = "ouye-result-nudge-host";
  let shown = false;

  function sendMsg(msg) {
    return new Promise((resolve) => {
      try {
        chrome.runtime.sendMessage(msg, (resp) => resolve(resp || null));
      } catch (e) {
        resolve(null);
      }
    });
  }

  function h(tag, attrs, text) {
    const e = document.createElement(tag);
    if (attrs) {
      for (const k of Object.keys(attrs)) {
        if (k === "style") e.style.cssText = attrs[k];
        else if (k.startsWith("on") && typeof attrs[k] === "function") e.addEventListener(k.slice(2), attrs[k]);
        else e.setAttribute(k, attrs[k]);
      }
    }
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  }

  const CSS = `
.oy-nudge {
  position: fixed; right: 18px; bottom: 24px; z-index: 2147483647;
  font: 14px/1.5 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  background: #fff; color: #222; border-radius: 12px;
  box-shadow: 0 8px 32px rgba(0,0,0,.28); padding: 12px 14px;
  max-width: 340px; border: 1px solid #eee;
}
.oy-nudge p { margin: 0 0 10px; }
.oy-nudge .oy-verdict { font-weight: 700; color: #c0392b; }
.oy-nudge .oy-row { display: flex; gap: 8px; justify-content: flex-end; }
.oy-nudge .oy-primary {
  background: #c0392b; color: #fff; border: none; border-radius: 8px;
  padding: 6px 16px; font-size: 13px; cursor: pointer;
}
.oy-nudge .oy-primary:disabled { background: #bbb; cursor: wait; }
.oy-nudge .oy-ghost {
  background: #f4f4f4; color: #333; border: none; border-radius: 8px;
  padding: 6px 12px; font-size: 13px; cursor: pointer;
}
.oy-nudge .oy-tip { font-size: 12px; color: #888; margin-top: 8px; }
`;

  function ensureHost() {
    let host = document.getElementById(HOST_ID);
    if (host) return host;
    host = document.createElement("div");
    host.id = HOST_ID;
    const shadow = host.attachShadow({ mode: "closed" });
    const style = document.createElement("style");
    style.textContent = CSS;
    shadow.appendChild(style);
    document.documentElement.appendChild(host);
    host._shadow = shadow;
    return host;
  }

  async function getSettings() {
    const s = await sendMsg({ type: "ouye-get-settings-full" });
    if (s && typeof s === "object") return s;
    // 降级：直接读 storage（与 background 共用同一份）。
    try {
      return await chrome.storage.local.get(["baseUrl", "token", "resultNudgeEnabled"]);
    } catch (e) {
      return {};
    }
  }

  async function shouldShow(source, submissionId, problemKey) {
    const store = await chrome.storage.local.get(["resultPrompted", "resultMuted"]);
    const OY = globalThis.OY;
    if (OY.hasPromptedResult(store.resultPrompted, source, submissionId)) return false;
    if (OY.isResultMutedToday(store.resultMuted, source, problemKey, OY.todayStr())) return false;
    return true;
  }

  async function markPrompted(source, submissionId) {
    const store = await chrome.storage.local.get(["resultPrompted"]);
    const next = globalThis.OY.markResultPrompted(store.resultPrompted, source, submissionId);
    await chrome.storage.local.set({ resultPrompted: next });
  }

  async function muteToday(source, problemKey) {
    const store = await chrome.storage.local.get(["resultMuted"]);
    const next = globalThis.OY.muteResultToday(store.resultMuted, source, problemKey, globalThis.OY.todayStr());
    await chrome.storage.local.set({ resultMuted: next });
  }

  function showNudge(result, adapter) {
    if (shown) return;
    shown = true;
    const OY = globalThis.OY;
    const data = result.data;
    const verdictName = (OY.VERDICT_NAMES && OY.VERDICT_NAMES[data.verdictCode]) || data.verdictCode;
    const host = ensureHost();
    const shadow = host._shadow;

    const box = h("div", { class: "oy-nudge", role: "status" });
    const p = h("p");
    // 用户文本一律 textContent，不拼 innerHTML。
    p.appendChild(document.createTextNode("这次是 "));
    const v = h("span", { class: "oy-verdict" }, verdictName);
    p.appendChild(v);
    p.appendChild(document.createTextNode("，记一笔？"));
    box.appendChild(p);

    const row = h("div", { class: "oy-row" });
    const saveBtn = h("button", { class: "oy-primary", type: "button" }, "记下");
    const muteBtn = h("button", { class: "oy-ghost", type: "button" }, "今天不再提醒这道题");
    row.appendChild(muteBtn);
    row.appendChild(saveBtn);
    box.appendChild(row);
    const tip = h("div", { class: "oy-tip" }, "只在你点“记下”时上传一次，不会自动同步。");
    box.appendChild(tip);
    shadow.appendChild(box);

    const close = () => {
      if (box.parentNode) box.parentNode.removeChild(box);
      shown = false;
    };

    muteBtn.addEventListener("click", async () => {
      const problemKey = data.problemUrl || data.title || "";
      await muteToday(adapter.source, problemKey);
      await markPrompted(adapter.source, data.submissionId);
      close();
    });

    saveBtn.addEventListener("click", async () => {
      saveBtn.disabled = true;
      const importData = {
        source: adapter.source,
        url: data.problemUrl || location.href,
        title: data.title || "（无题名）",
        content: "（从提交结果页一键录入，题面未抓取）",
        verdict: data.verdictCode,
        submitted_code: data.code || "",
        language: data.language || "",
      };
      const resp = await sendMsg({ type: "ouye-import-v2", data: importData });
      await markPrompted(adapter.source, data.submissionId);
      if (resp && resp.ok) {
        // 记入本地收录记录，便题目页去重。
        await sendMsg({
          type: "ouye-save-record",
          record: OY.makeRecord(
            { title: importData.title, source: importData.source, source_id: "", url: importData.url },
            { problemId: resp.id }
          ),
        });
        tip.textContent = "已记下！" + (resp.codeTruncated ? "（代码过长已截断）" : "");
        setTimeout(close, 1800);
      } else {
        tip.textContent = "记下失败：" + ((resp && (resp.message || resp.error)) || "未知错误");
        saveBtn.disabled = false;
      }
    });
  }

  /** 尝试提取并决定是否弹提示。返回 true 表示已处理（弹过或明确不弹）。 */
  async function tryNudge() {
    if (shown) return true;
    const url = location.href;
    const adapter = AdapterRegistry.detectResult(url);
    if (!adapter) return false;

    const settings = await getSettings();
    if (settings.resultNudgeEnabled === false) return true; // 用户关掉了

    // SPA（如力扣/洛谷）：结果没渲染出来就等，不硬弹。
    if (typeof adapter.resultReady === "function") {
      try {
        if (!adapter.resultReady(document)) return false;
      } catch (e) {
        return false;
      }
    }

    const result = AdapterRegistry.extractResult(url, document);
    if (!result.ok) return true;
    const data = result.data;
    // 只对失败判定弹：通过（null）与未知（undefined）都不弹。
    if (!globalThis.OY.isFailedVerdict(data.verdictCode)) return true;
    if (!data.submissionId) return true; // 没有提交号就无法去重，不弹

    const problemKey = data.problemUrl || data.title || "";
    if (!(await shouldShow(adapter.source, data.submissionId, problemKey))) return true;

    showNudge(result, adapter);
    return true;
  }

  /** SPA 结果延迟出现：MutationObserver + 轮询兜底。 */
  function watchResult() {
    let done = false;
    let polls = 0;
    const attempt = async () => {
      if (done) return;
      try {
        const handled = await tryNudge();
        if (handled) {
          done = true;
          return;
        }
      } catch (e) {}
      polls += 1;
      if (polls > 40) done = true; // 约 60 秒后放弃
    };
    try {
      const mo = new MutationObserver(() => {
        attempt();
      });
      mo.observe(document.documentElement, { childList: true, subtree: true });
      // 停 90 秒后断开，避免常驻观察者。
      setTimeout(() => {
        try {
          mo.disconnect();
        } catch (e) {}
        done = true;
      }, 90000);
    } catch (e) {}
    const timer = setInterval(() => {
      attempt();
      if (done) clearInterval(timer);
    }, 1500);
    attempt();
  }

  // 结果页才启动；题目页的逻辑仍在 inject.js。
  try {
    if (AdapterRegistry.detectResult(location.href)) {
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", watchResult);
      } else {
        watchResult();
      }
    }
  } catch (e) {}
})();
