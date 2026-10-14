/* Codeforces 适配器：
 * codeforces.com/problemset/problem/{contestId}/{index}
 * codeforces.com/contest/{contestId}/problem/{index}
 *
 * 选择器维护（最后核对：2026-10-08，静态推断，未在真实页面验证）：
 *  只改 SELECTORS，不要改下面的提取逻辑。
 *  CF 题目页不直接展示难度（rating 在题库列表页），difficulty 恒为空；
 *  tags（.tag-box）仅在用户已通过该题或特定条件下可见，拿不到就降级。
 */
"use strict";

(function registerCodeforces() {
  const SELECTORS = {
    statement: "#problem-statement",
    titleInHeader: ".header .title",
    tags: ".tag-box",
    titleSuffixes: [" - Codeforces"],
  };

  function textOf(el) {
    return (el && el.textContent ? el.textContent : "").trim();
  }

  AdapterRegistry.register({
    id: "codeforces",
    source: "codeforces",
    SELECTORS,

    detect(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isCf = host === "codeforces.com" || host.endsWith(".codeforces.com");
      if (!isCf) return false;
      return (
        /^\/problemset\/problem\/\d+\/[A-Za-z0-9]+/.test(path) ||
        /^\/contest\/\d+\/problem\/[A-Za-z0-9]+/.test(path)
      );
    },

    sourceId(loc) {
      const path = loc.pathname || "";
      let m =
        /^\/problemset\/problem\/(\d+)\/([A-Za-z0-9]+)/.exec(path) ||
        /^\/contest\/(\d+)\/problem\/([A-Za-z0-9]+)/.exec(path);
      return m ? m[1] + m[2].toUpperCase() : "";
    },

    extract(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      let title = "";
      let content = "";
      let ps = null;
      try {
        ps = doc.querySelector(SELECTORS.statement);
      } catch (e) {
        ps = null;
      }
      if (ps) {
        try {
          title = textOf(ps.querySelector(SELECTORS.titleInHeader));
          const clone = ps.cloneNode(true);
          const header = clone.querySelector(".header");
          if (header && header.remove) header.remove();
          content = textOf(clone);
        } catch (e) {
          /* 降级到 document.title */
        }
      }
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
      if (!content) degraded.push("content");
      let tags = [];
      try {
        tags = Array.from(doc.querySelectorAll(SELECTORS.tags))
          .map((a) => textOf(a).replace(/^#/, ""))
          .filter(Boolean)
          .slice(0, 20);
      } catch (e) {
        tags = [];
      }
      if (!tags.length) degraded.push("tags");
      return {
        title,
        source: "codeforces",
        source_id: this.sourceId(loc),
        url,
        difficulty: "", // CF 题目页不展示难度
        tags,
        zone_hint: "算法",
        my_status: null, // 账号信息不读取
        content,
        degraded,
      };
    },

    /* ---- 结果页：/contest/{id}/submission/{sid} 与 /problemset/submission/{id}/{sid} ----
     * 选择器维护（最后核对：2026-10-14，静态推断，未在真实页面验证）：
     *  只改 RESULT_SELECTORS，不要改下面的提取逻辑。
     */
    detectResult(loc) {
      const host = (loc.hostname || "").toLowerCase();
      const path = loc.pathname || "";
      const isCf = host === "codeforces.com" || host.endsWith(".codeforces.com");
      if (!isCf) return false;
      return (
        /^\/contest\/\d+\/submission\/\d+/.test(path) ||
        /^\/problemset\/submission\/\d+\/\d+/.test(path)
      );
    },

    resultSubmissionId(loc) {
      const m =
        /^\/contest\/\d+\/submission\/(\d+)/.exec(loc.pathname || "") ||
        /^\/problemset\/submission\/\d+\/(\d+)/.exec(loc.pathname || "");
      return m ? m[1] : "";
    },

    extractResult(doc, url) {
      const degraded = [];
      const loc = new URL(url);
      const RESULT_SELECTORS = {
        // 判题结果文本，如 "Wrong answer on test 1"
        verdict: [".verdict-format-text", ".verdict-accepted", "[class*='verdict']"],
        // 题名：提交页的问题链接
        title: ["a[href*='/problem/']", ".title"],
        problemLink: ["a[href*='/problemset/problem/']", "a[href*='/contest/'][href*='/problem/']"],
        // 提交代码
        code: ["#program-source-text", ".program-source", "pre[id*='program-source']"],
        language: [".language", "[class*='language']"],
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
        const i = t.indexOf(" - Codeforces");
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
