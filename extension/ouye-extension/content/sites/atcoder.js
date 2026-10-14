/* AtCoder 适配器：atcoder.jp/contests/{contest}/tasks/{task_id}。
 *
 * 选择器维护（最后核对：2026-10-08，静态推断，未在真实页面验证）：
 *  只改 SELECTORS，不要改下面的提取逻辑。
 *  AtCoder 题目页不展示难度与标签，这两项恒为空/空数组。
 *  注意：后端 import-from-extension 的 source 白名单暂不含 atcoder，
 *  扩展侧会拦截导入并提示（见 EXT_REPORT 的后端规格建议）。
 */
"use strict";

(function registerAtcoder() {
  const SELECTORS = {
    statement: "#task-statement",
    title: "#task-statement span.h2, span.h2",
    titleSuffixes: [" - AtCoder"],
  };

  function textOf(el) {
    return (el && el.textContent ? el.textContent : "").trim();
  }

  AdapterRegistry.register({
    id: "atcoder",
    source: "atcoder",
    SELECTORS,

    detect(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isAc = host === "atcoder.jp" || host.endsWith(".atcoder.jp");
      return isAc && /^\/contests\/[^/]+\/tasks\/[^/]+/.test(path);
    },

    sourceId(loc) {
      const m = /^\/contests\/[^/]+\/tasks\/([^/?#]+)/.exec(loc.pathname || "");
      return m ? m[1] : "";
    },

    extract(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      let title = "";
      try {
        const h = doc.querySelector(SELECTORS.title);
        title = textOf(h);
      } catch (e) {}
      if (!title) {
        let t = (doc && doc.title) || "";
        for (const s of SELECTORS.titleSuffixes) {
          if (t.endsWith(s)) {
            t = t.slice(0, -s.length);
            break;
          }
        }
        title = t.trim();
      }
      if (!title) degraded.push("title");
      let content = "";
      try {
        const st = doc.querySelector(SELECTORS.statement);
        content = textOf(st);
      } catch (e) {}
      if (!content) degraded.push("content");
      return {
        title,
        source: "atcoder",
        source_id: this.sourceId(loc),
        url,
        difficulty: "", // AtCoder 题目页不展示难度
        tags: [], // AtCoder 题目页无公开标签
        zone_hint: "算法",
        my_status: null, // 账号信息不读取
        content,
        degraded,
      };
    },

    /* ---- 结果页：/contests/{contest}/submissions/{submissionId} ----
     * 选择器维护（最后核对：2026-10-14，静态推断，未在真实页面验证）：
     *  只改 RESULT_SELECTORS，不要改下面的提取逻辑。
     */
    detectResult(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isAc = host === "atcoder.jp" || host.endsWith(".atcoder.jp");
      return isAc && /^\/contests\/[^/]+\/submissions\/\d+/.test(path);
    },

    resultSubmissionId(loc) {
      const m = /^\/contests\/[^/]+\/submissions\/(\d+)/.exec(loc.pathname || "");
      return m ? m[1] : "";
    },

    extractResult(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      const RESULT_SELECTORS = {
        // 判题状态：提交详情页的状态单元格
        verdict: ["#submission-detail .verdict", ".verdict", "td.verdict"],
        // 题名：面包屑或标题中的题目链接
        title: ["#submission-detail a[href*='/tasks/']", ".contest-title"],
        // 题目链接
        problemLink: ["#submission-detail a[href*='/tasks/']"],
        // 提交代码
        code: ["#submission-code", ".source-code", "pre.source-code"],
        // 语言
        language: ["#submission-detail .language", ".language"],
      };
      function firstText(sels) {
        for (const s of sels) {
          let el = null;
          try {
            el = doc.querySelector(s);
          } catch (e) {}
          const t = el && el.textContent ? el.textContent.trim() : "";
          if (t) return t;
        }
        return "";
      }
      function firstHref(sels) {
        for (const s of sels) {
          let el = null;
          try {
            el = doc.querySelector(s);
          } catch (e) {}
          if (el && el.href) return el.href;
        }
        return "";
      }
      const verdictRaw = firstText(RESULT_SELECTORS.verdict);
      let title = firstText(RESULT_SELECTORS.title);
      if (!title) {
        let t = (doc && doc.title) || "";
        const i = t.indexOf(" - AtCoder");
        title = (i !== -1 ? t.slice(0, i) : t).trim();
      }
      if (!title) degraded.push("title");
      let code = firstText(RESULT_SELECTORS.code);
      if (!code) degraded.push("code");
      return {
        verdictRaw,
        title,
        problemUrl: firstHref(RESULT_SELECTORS.problemLink),
        submissionId: this.resultSubmissionId(loc),
        code,
        language: firstText(RESULT_SELECTORS.language),
        degraded,
      };
    },
  });
})();
