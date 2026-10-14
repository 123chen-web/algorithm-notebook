/* 适配器注册表：统一 detect / extract 入口。
 * 每个 sites/*.js 调用 AdapterRegistry.register(adapter) 注册自己。
 * extract 绝不抛异常：单个适配器出错时降级为字段全空的结果。
 */
"use strict";

globalThis.AdapterRegistry = {
  _list: [],

  register(adapter) {
    if (adapter && typeof adapter.detect === "function") {
      this._list.push(adapter);
    }
  },

  /** 按 URL 找到适配器；没有返回 null。 */
  detect(url) {
    let loc;
    try {
      loc = new URL(url);
    } catch (e) {
      return null;
    }
    for (const a of this._list) {
      try {
        if (a.detect(loc)) return a;
      } catch (e) {
        /* 单个 detect 出错不影响其他 */
      }
    }
    return null;
  },

  /**
   * 提取当前页题目信息。
   * 返回 {ok:true, adapter, data} 或 {ok:false, error}。
   * data: {title, source, source_id, url, difficulty, tags, zone_hint,
   *        my_status, content, language?, degraded[]}
   * error: NOT_PROBLEM_PAGE | EXTRACT_FAILED
   */
  extractProblem(url, doc) {
    const adapter = this.detect(url);
    if (!adapter) return { ok: false, error: "NOT_PROBLEM_PAGE" };
    let data;
    try {
      data = adapter.extract(doc || (typeof document !== "undefined" ? document : null), url);
    } catch (e) {
      return { ok: false, error: "EXTRACT_FAILED", adapter: adapter.id };
    }
    if (!data || typeof data !== "object") {
      return { ok: false, error: "EXTRACT_FAILED", adapter: adapter.id };
    }
    // 题面超长保护：后端上限 100000，客户端留余量
    const LIMIT = 95000;
    if (data.content && data.content.length > LIMIT) {
      data.content = data.content.slice(0, LIMIT) + "\n……（题面过长，已截断）";
      data.truncated = true;
    }
    if (!data.title) {
      return { ok: false, error: "EXTRACT_FAILED", adapter: adapter.id, data };
    }
    return { ok: true, adapter: adapter.id, data };
  },

  /**
   * 结果页：按 URL 找到支持结果页的适配器；没有返回 null。
   * 适配器需实现 detectResult(loc) 与 extractResult(doc, url)。
   */
  detectResult(url) {
    let loc;
    try {
      loc = new URL(url);
    } catch (e) {
      return null;
    }
    for (const a of this._list) {
      try {
        if (typeof a.detectResult === "function" && a.detectResult(loc)) return a;
      } catch (e) {
        /* 单个 detect 出错不影响其他 */
      }
    }
    return null;
  },

  /**
   * 提取结果页信息。
   * 返回 {ok:true, adapter, data} 或 {ok:false, error}。
   * data: {verdictRaw, verdictCode ("WA"|"TLE"|"MLE"|"RE"|"CE"|null|undefined),
   *        title, problemUrl, submissionId, code, language, degraded[]}
   * verdictCode 为 null = 通过（不应弹提示）；undefined = 未知（不应弹提示）。
   * error: NOT_RESULT_PAGE | EXTRACT_FAILED
   */
  extractResult(url, doc) {
    const adapter = this.detectResult(url);
    if (!adapter) return { ok: false, error: "NOT_RESULT_PAGE" };
    let data;
    try {
      data = adapter.extractResult(doc || (typeof document !== "undefined" ? document : null), url);
    } catch (e) {
      return { ok: false, error: "EXTRACT_FAILED", adapter: adapter.id };
    }
    if (!data || typeof data !== "object") {
      return { ok: false, error: "EXTRACT_FAILED", adapter: adapter.id };
    }
    // 判题映射：原文 → code（表驱动，见 lib/verdict.js）。
    if (typeof globalThis.OY !== "undefined" && typeof globalThis.OY.mapVerdict === "function") {
      data.verdictCode = globalThis.OY.mapVerdict(adapter.id, data.verdictRaw);
    }
    // 代码超长保护：与后端 submitted_code 上限对齐，客户端先截断。
    const CODE_LIMIT = 20000;
    if (data.code && data.code.length > CODE_LIMIT) {
      data.code = data.code.slice(0, CODE_LIMIT);
      data.codeTruncated = true;
    }
    return { ok: true, adapter: adapter.id, data };
  },
};
