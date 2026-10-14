/* 洛谷适配器：luogu.com.cn/problem/*（含比赛内、题单内的题目页，URL 同为 /problem/题号）。
 *
 * 选择器维护（最后核对：2026-10-08，静态推断，未在真实页面验证）：
 *  只改 SELECTORS，不要改下面的提取逻辑。
 *  洛谷前端是 SPA，题目页主体异步渲染；document.title 是最稳的兜底。
 */
"use strict";

(function registerLuogu() {
  const SELECTORS = {
    // 标题候选：页面主标题 → h1 → document.title 兜底
    title: ["h1", ".problem-title", "[class*='problem-title']"],
    // 题面候选
    content: [".problem-content", "[class*='problem-content']", "article"],
    // 难度：洛谷用文字标识（入门/普及-/提高-/省选-/NOI/）
    difficulty: [".difficulty", "[class*='difficulty']"],
    // 标签链接
    tags: "a[href*='/tag']",
    // document.title 后缀（去掉站点名）
    titleSuffixes: [" - 洛谷", " - 洛谷 | 计算机科学教育新生态", "| 洛谷"],
    // 难度关键词 → 归一化
    difficultyWords: ["入门", "普及", "提高", "省选", "NOI", "IOI"],
  };

  function textOf(el) {
    return (el && el.textContent ? el.textContent : "").trim();
  }

  function firstText(doc, selectors) {
    for (const sel of selectors) {
      let el = null;
      try {
        el = doc.querySelector(sel);
      } catch (e) {
        /* 非法选择器就跳过 */
      }
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
    id: "luogu",
    source: "luogu",
    SELECTORS,

    detect(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isLuogu = host === "luogu.com.cn" || host.endsWith(".luogu.com.cn");
      return isLuogu && /^\/problem\/[A-Za-z0-9]+/.test(path);
    },

    sourceId(loc) {
      const m = /^\/problem\/([A-Za-z0-9]+)/.exec(loc.pathname || "");
      return m ? m[1].toUpperCase() : "";
    },

    extract(doc, url) {
      const degraded = [];
      let title = firstText(doc, SELECTORS.title) || titleFromDocTitle(doc);
      if (!title) degraded.push("title");
      const content = firstText(doc, SELECTORS.content);
      if (!content) degraded.push("content");
      const diffRaw = firstText(doc, SELECTORS.difficulty);
      let difficulty = "";
      for (const w of SELECTORS.difficultyWords) {
        if (diffRaw.indexOf(w) !== -1) {
          difficulty = w;
          break;
        }
      }
      let tags = [];
      try {
        tags = Array.from(doc.querySelectorAll(SELECTORS.tags))
          .map((a) => textOf(a))
          .filter(Boolean)
          .slice(0, 20);
      } catch (e) {
        tags = [];
      }
      // my_status：涉及用户账号信息，按隐私要求不读取，恒为 null。
      return {
        title,
        source: "luogu",
        source_id: this.sourceId({ pathname: new URL(url).pathname }),
        url,
        difficulty,
        tags,
        zone_hint: "算法",
        my_status: null,
        content,
        degraded,
      };
    },

    /* ---- 结果页：/record/{recordId}（洛谷评测记录页，SPA 异步渲染）
     * 选择器维护（最后核对：2026-10-14，静态推断，未在真实页面验证）：
     *  只改 RESULT_SELECTORS，不要改下面的提取逻辑。
     */
    detectResult(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isLg = host === "luogu.com.cn" || host.endsWith(".luogu.com.cn");
      return isLg && /^\/record\/\d+/.test(path);
    },

    resultSubmissionId(loc) {
      const m = /^\/record\/(\d+)/.exec(loc.pathname || "");
      return m ? m[1] : "";
    },

    /** SPA：评测状态是否已渲染。 */
    resultReady(doc) {
      try {
        const body = doc.body ? doc.body.textContent : "";
        return /Accepted|Wrong Answer|Time Limit Exceeded|Memory Limit Exceeded|Runtime Error|Compile Error/i.test(
          body || ""
        );
      } catch (e) {
        return false;
      }
    },

    extractResult(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      const RESULT_SELECTORS = {
        // 评测状态，如 "Wrong Answer"
        verdict: [".record-status", "[class*='status']", ".status"],
        title: ["a[href*='/problem/']", "h1"],
        problemLink: ["a[href*='/problem/']"],
        // 提交代码
        code: [".record-code", "pre.code", "[class*='code']"],
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
        let t = (doc && doc.title) || "";
        const i = t.indexOf(" - 洛谷");
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
