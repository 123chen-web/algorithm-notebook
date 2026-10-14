/* 牛客适配器：ac.nowcoder.com/acm/problem/*（主），
 * 兼顾旧版 www.nowcoder.com/practice/* 与 questionTerminal/*。
 *
 * 选择器维护（最后核对：2026-10-08，静态推断，未在真实页面验证）：
 *  只改 SELECTORS，不要改下面的提取逻辑。
 */
"use strict";

(function registerNowcoder() {
  const SELECTORS = {
    title: [".subject-title", ".question-title", "h1"],
    content: [".subject-describe", ".question-describe", ".nk-content", "article"],
    difficulty: ["[class*='difficulty']", ".level"],
    tags: ".subject-tags a, .tag-item",
    titleSuffixes: ["_牛客网", "-牛客网", " - 牛客"],
  };

  function textOf(el) {
    return (el && el.textContent ? el.textContent : "").trim();
  }

  function firstText(doc, selectors) {
    for (const sel of selectors) {
      let el = null;
      try {
        el = doc.querySelector(sel);
      } catch (e) {}
      const t = textOf(el);
      if (t) return t;
    }
    return "";
  }

  function titleFromDocTitle(doc) {
    let t = (doc && doc.title) || "";
    for (const s of SELECTORS.titleSuffixes) {
      const i = t.indexOf(s);
      if (i !== -1) {
        t = t.slice(0, i);
        break;
      }
    }
    return t.trim();
  }

  AdapterRegistry.register({
    id: "nowcoder",
    source: "nowcoder",
    SELECTORS,

    detect(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isNc = host === "nowcoder.com" || host.endsWith(".nowcoder.com");
      if (!isNc) return false;
      return (
        /^\/acm\/problem\/\d+/.test(path) ||
        /^\/practice\/\d+/.test(path) ||
        /^\/questionTerminal\/[^/]+/.test(path)
      );
    },

    sourceId(loc) {
      const path = loc.pathname || "";
      let m = /^\/acm\/problem\/(\d+)/.exec(path) || /^\/practice\/(\d+)/.exec(path);
      if (m) return m[1];
      m = /^\/questionTerminal\/([^/]+)/.exec(path);
      return m ? m[1] : "";
    },

    extract(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      let title = firstText(doc, SELECTORS.title) || titleFromDocTitle(doc);
      if (!title) degraded.push("title");
      const content = firstText(doc, SELECTORS.content);
      if (!content) degraded.push("content");
      const difficulty = firstText(doc, SELECTORS.difficulty);
      let tags = [];
      try {
        tags = Array.from(doc.querySelectorAll(SELECTORS.tags))
          .map((a) => textOf(a))
          .filter(Boolean)
          .slice(0, 20);
      } catch (e) {
        tags = [];
      }
      return {
        title,
        source: "nowcoder",
        source_id: this.sourceId(loc),
        url,
        difficulty,
        tags,
        zone_hint: "算法",
        my_status: null, // 账号信息不读取
        content,
        degraded,
      };
    },

    /* ---- 结果页（尽力而为）：ac.nowcoder.com 练习提交记录页
     * 牛客提交记录页结构不稳定，这里只做 URL 识别 + 通用提取；
     * 拿不准的字段会进 degraded，调用方据此降级。详见 REPORT。
     * 选择器维护（最后核对：2026-10-14，静态推断，未在真实页面验证）。
     */
    detectResult(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isNc = host === "nowcoder.com" || host.endsWith(".nowcoder.com");
      if (!isNc) return false;
      return /\/acm\/(contest|problem)\/.*(submission|status)/.test(path);
    },

    resultSubmissionId(loc) {
      const m = /submission\/(\d+)/.exec(loc.pathname || "") || /[?&]id=(\d+)/.exec(loc.search || "");
      return m ? m[1] : "";
    },

    extractResult(doc, url) {
      const degraded = ["nowcoder-best-effort"];
      const loc = new URL(url);
      const RESULT_SELECTORS = {
        verdict: [".result-status", "[class*='status']", ".status"],
        title: [".subject-title", ".question-title", "h1"],
        problemLink: ["a[href*='/acm/problem/']"],
        code: ["pre.code", ".code-view", "[class*='code']"],
        language: ["[class*='language']"],
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
      const title = firstText(RESULT_SELECTORS.title) || "";
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
