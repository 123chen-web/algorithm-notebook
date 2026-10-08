"use strict";

/* F4 费曼模式：错题详情页里的“讲一遍”面板。
   对外契约：window.Explain = { configure({ api, getUser, getEpoch }), mount(container, mistake), unmount(), reset() }。
   - 请求只走宿主传入的 api()：POST /api/mistakes/{id}/explain，
     请求体 { explanation }，
     成功 200 { score, missing_points, follow_up, mastered, explanation_id, ai_remaining }；
     422 空讲解，429 每题每日 3 次或今日 AI 额度用完，503 AI 未配置，502 评分失败（可重试）；
   - 每个响应都按 登录代次(epoch) + 面板代次(generation) + 错题 id + 请求序号 校验，
     登出换号、关闭面板、切换错题之后才回来的响应一律丢弃；
   - 讲解原文只存在前端内存，刷新或离开页面即丢；
   - 一切来自用户或服务器的文字都用 textContent；组件自己不直接发请求、不写本地存储。
   接线方式（由宿主在 app.js 里调用，仿照 DuckPanel）：
     window.Explain?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
     window.Explain?.mount(explainHost, item);
   index.html 需新增：<script defer src="/static/explain.js?v=1"></script> */
(() => {
  const MAX_CHARS = 4000; // 输入上限与后端的 ExplainInput 一致
  const THINKING = "正在评分…";

  let hooks = null;
  let generation = 0; // mount / unmount / reset 都会加一：旧面板的迟到响应全部作废
  let sendSeq = 0;    // 请求序号：只允许最后一次发出的请求写界面
  let panel = null;   // 当前面板状态

  /* ---------- 小工具 ---------- */
  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }
  function button(label, className) {
    const item = node("button", className, label);
    item.type = "button";
    return item;
  }
  function clearElement(target) {
    while (target.firstChild) target.removeChild(target.firstChild);
  }

  /* ---------- 纯函数（挂在 Explain.helpers 上供测试） ---------- */
  /** api 错误 → 界面说明；返回 { text, retryable }。 */
  function failureInfo(error) {
    const status = error && typeof error.status === "number" ? error.status : 0;
    const detail = (error && error.message ? String(error.message) : "").trim();
    if (status === 422) return { text: "讲解太短了，先写下你的思路再评分。", retryable: true };
    if (status === 429) {
      return {
        text: detail.includes("AI 生成")
          ? "今天的 AI 额度用完了，明天再来讲解吧。"
          : "这道题今天的评分次数用完了，明天再来。",
        retryable: false,
      };
    }
    if (status === 503) return { text: "AI 服务还没配好，暂时评不了分。", retryable: false };
    if (status === 502) return { text: "这次没评好，可以再试一次。", retryable: true };
    return { text: detail || "请求失败，请稍后再试。", retryable: true };
  }

  /** 分数文案：>=80 视为讲清楚了。 */
  function scoreText(score, mastered) {
    return `${score} 分${mastered ? " · 讲清楚了！" : " · 还没完全讲清楚"}`;
  }

  function remainingText(length) {
    return `还可输入 ${Math.max(0, MAX_CHARS - length)} 字`;
  }

  /* ---------- 面板 ---------- */
  function configure(next) {
    hooks = next;
  }

  function reset() {
    generation += 1;
    panel = null;
  }

  function unmount() {
    reset();
  }

  function mount(container, mistake) {
    generation += 1;
    const myGeneration = generation;
    clearElement(container);
    panel = { container, mistakeId: mistake.id, generation: myGeneration };

    const section = node("section", "explain-panel");
    const title = node("h3", "section-label", "讲一遍（费曼模式）");
    const hint = node("p", "muted", "用自己的话把这道题讲一遍，AI 会对照思路笔记给你打分并指出漏点。每题每天可评 3 次。");
    const textWrap = node("div", "explain-input-wrap");
    const textarea = node("textarea", "explain-input");
    textarea.placeholder = "比如：这道题的关键是…我当时错在…";
    textarea.maxLength = MAX_CHARS;
    textarea.rows = 6;
    const counter = node("p", "muted explain-counter", remainingText(0));
    const submit = button("让 AI 评分", "primary");
    const result = node("div", "explain-result");
    result.hidden = true;
    const err = node("p", "error explain-error");
    err.hidden = true;

    textarea.addEventListener("input", () => {
      counter.textContent = remainingText(textarea.value.length);
    });

    const renderResult = (data) => {
      clearElement(result);
      const scoreLine = node("p", "explain-score", scoreText(data.score, data.mastered));
      result.append(scoreLine);
      if (Array.isArray(data.missing_points) && data.missing_points.length > 0) {
        result.append(node("p", "explain-label", "漏掉或讲错的关键点："));
        const list = node("ul", "explain-missing");
        data.missing_points.forEach((point) => {
          list.append(node("li", "", point));
        });
        result.append(list);
      } else {
        result.append(node("p", "explain-label", "全部关键点都讲到了。"));
      }
      if (data.follow_up) {
        result.append(node("p", "explain-label", "追问："));
        result.append(node("p", "explain-followup", data.follow_up));
      }
      result.hidden = false;
    };

    submit.addEventListener("click", async () => {
      if (!hooks) return;
      const explanation = textarea.value.trim();
      if (!explanation) {
        err.textContent = "先写下你的讲解再评分。";
        err.hidden = false;
        return;
      }
      err.hidden = true;
      result.hidden = true;
      submit.disabled = true;
      const waiting = node("p", "muted", THINKING);
      result.append(waiting);
      result.hidden = false;
      sendSeq += 1;
      const mySeq = sendSeq;
      const epoch = hooks.getEpoch ? hooks.getEpoch() : 0;
      try {
        const data = await hooks.api(`/api/mistakes/${panel.mistakeId}/explain`, {
          method: "POST",
          body: JSON.stringify({ explanation }),
        });
        // 迟到响应 / 切换错题 / 登出换号 / 面板重挂：直接丢弃。
        if (
          !panel ||
          panel.generation !== myGeneration ||
          mySeq !== sendSeq ||
          (hooks.getEpoch ? hooks.getEpoch() !== epoch : false)
        ) {
          return;
        }
        clearElement(result);
        renderResult(data);
      } catch (error) {
        if (
          !panel ||
          panel.generation !== myGeneration ||
          mySeq !== sendSeq ||
          (hooks.getEpoch ? hooks.getEpoch() !== epoch : false)
        ) {
          return;
        }
        clearElement(result);
        result.hidden = true;
        const info = failureInfo(error);
        err.textContent = info.text;
        err.hidden = false;
        submit.disabled = !info.retryable;
      } finally {
        if (panel && panel.generation === myGeneration && mySeq === sendSeq) {
          submit.disabled = false;
        }
      }
    });

    textWrap.append(textarea);
    section.append(title, hint, textWrap, counter, submit, result, err);
    container.append(section);
  }

  window.Explain = { configure, mount, unmount, reset, helpers: { failureInfo, scoreText, remainingText } };
})();
