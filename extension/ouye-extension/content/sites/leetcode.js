/* 力扣适配器：leetcode.cn/problems/* 与 leetcode.com/problems/*。
 *
 * 选择器维护（最后核对：2026-10-08，静态推断，未在真实页面验证）：
 *  只改 SELECTORS，不要改下面的提取逻辑。
 *  力扣是 SPA（切题不刷新），document.title 是最稳的兜底：
 *  中文站形如 "1. 两数之和 - 力扣（LeetCode）"，英文站形如 "1. Two Sum - LeetCode"。
 */
"use strict";

(function registerLeetcode() {
  const SELECTORS = {
    title: ['[data-cy="question-title"]', ".text-title-large", "[class*='question-title']"],
    content: [
      '[data-track-load="description_content"]',
      '[data-cy="question-description"]',
      ".question-content",
      "[class*='question-content']",
    ],
    // 难度胶囊（中英文都认）
    difficulty: [
      ".text-difficulty-easy",
      ".text-difficulty-medium",
      ".text-difficulty-hard",
      "[class*='difficulty']",
    ],
    tags: 'a[href*="/tag/"]',
    titleSuffixes: [" - 力扣（LeetCode）", " - LeetCode", " - 力扣"],
    // 题号前缀："1. 两数之和" -> "两数之和"（source_id 用 URL slug，不用这个）
    titleNumberPrefix: /^\d+\.\s*/,
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
      if (t.endsWith(s)) {
        t = t.slice(0, -s.length);
        break;
      }
    }
    return t.replace(SELECTORS.titleNumberPrefix, "").trim() || t.trim();
  }

  function normalizeDifficulty(raw) {
    if (/简单|easy/i.test(raw)) return "简单";
    if (/中等|medium/i.test(raw)) return "中等";
    if (/困难|hard/i.test(raw)) return "困难";
    return "";
  }

  AdapterRegistry.register({
    id: "leetcode",
    source: "leetcode", // detect 时再按 hostname 细分为 leetcode / leetcode-cn
    SELECTORS,

    detect(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isLc =
        host === "leetcode.com" ||
        host.endsWith(".leetcode.com") ||
        host === "leetcode.cn" ||
        host.endsWith(".leetcode.cn");
      return isLc && /^\/problems\/[^/]+/.test(path);
    },

    sourceName(loc) {
      const host = (loc.hostname || "").toLowerCase();
      return host === "leetcode.cn" || host.endsWith(".leetcode.cn")
        ? "leetcode-cn"
        : "leetcode";
    },

    sourceId(loc) {
      const m = /^\/problems\/([^/]+)/.exec(loc.pathname || "");
      return m ? m[1] : "";
    },

    extract(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      const source = this.sourceName(loc);
      let title = firstText(doc, SELECTORS.title) || titleFromDocTitle(doc);
      if (!title) degraded.push("title");
      const content = firstText(doc, SELECTORS.content);
      if (!content) degraded.push("content");
      const difficulty = normalizeDifficulty(firstText(doc, SELECTORS.difficulty));
      let tags = [];
      try {
        tags = Array.from(doc.querySelectorAll(SELECTORS.tags))
          .map((a) => textOf(a))
          .filter(Boolean)
          .slice(0, 20);
      } catch (e) {
        tags = [];
      }
      let language = "";
      try {
        const btn = doc.querySelector('button[aria-label*="language" i]');
        language = textOf(btn);
      } catch (e) {}
      return {
        title,
        source,
        source_id: this.sourceId(loc),
        url,
        difficulty,
        tags,
        zone_hint: "算法",
        my_status: null, // 账号信息不读取
        content,
        language,
        degraded,
      };
    },

    /* ---- 结果页：/problems/{slug}/submissions/{sid}/（力扣中国站为主）
     * 力扣是单页应用：提交后结果异步出现，调用方需用 MutationObserver
     * 等 verdict 节点出现后再 extractResult（见 content/result-nudge.js）。
     * 选择器维护（最后核对：2026-10-14，静态推断，未在真实页面验证）：
     *  只改 RESULT_SELECTORS，不要改下面的提取逻辑。
     */
    detectResult(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isLc =
        host === "leetcode.com" ||
        host.endsWith(".leetcode.com") ||
        host === "leetcode.cn" ||
        host.endsWith(".leetcode.cn");
      return isLc && /^\/problems\/[^/]+\/submissions\/\d+/.test(path);
    },

    resultSubmissionId(loc) {
      const m = /^\/problems\/[^/]+\/submissions\/(\d+)/.exec(loc.pathname || "");
      return m ? m[1] : "";
    },

    /** SPA：结果是否已渲染（判题文案节点出现）。 */
    resultReady(doc) {
      try {
        const el = doc.querySelector("[data-e2e-locator='submission-result'], .submission-result");
        if (el && el.textContent && el.textContent.trim()) return true;
        // 兜底：页面出现"解答错误/通过"等关键词
        const body = doc.body ? doc.body.textContent : "";
        return /解答错误|超出时间限制|超出内存限制|运行出错|编译出错|通过/.test(body || "");
      } catch (e) {
        return false;
      }
    },

    extractResult(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      const RESULT_SELECTORS = {
        verdict: ["[data-e2e-locator='submission-result']", ".submission-result", "[class*='result']"],
        title: ["a[href*='/problems/']"],
        problemLink: ["a[href*='/problems/']"],
        code: [".monaco-editor", "[class*='monaco']", "pre.code"],
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
      let title = firstText(RESULT_SELECTORS.title);
      if (!title) {
        const m = /^\/problems\/([^/]+)/.exec(loc.pathname || "");
        title = m ? m[1] : "";
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
