/* 手机“分享到欧叶”（Web Share Target，GET /?title=&text=&url=）。
   parseShare 是纯函数：从查询串里取出第一个 http(s) 链接和标题，其他一律忽略。
   intake 只在启动时调用：把链接交给 Capture 暂存（登录前存 sessionStorage，登录后再带入新增记录），
   并立刻把这三个参数从地址栏去掉，刷新不会重复触发。不发任何请求，不碰 innerHTML。 */
(() => {
  "use strict";

  const MAX_URL = 2000;
  const MAX_TITLE = 200;
  const MAX_QUERY = 8000;
  const SHARE_KEYS = ["title", "text", "url"];
  const LINK = /https?:\/\/[^\s<>"'`]+/gi;
  const TAIL = /[.,;:!?)）。，；、！？」』》】"'”’]+$/;

  /** 文本里第一个合法的 http(s) 链接；没有返回 ""。 */
  function firstLink(value) {
    if (typeof value !== "string" || !value) return "";
    for (const match of value.slice(0, MAX_URL * 2).matchAll(LINK)) {
      const candidate = match[0].replace(TAIL, "");
      if (!candidate || candidate.length > MAX_URL) continue;
      try {
        const url = new window.URL(candidate);
        if (url.protocol !== "http:" && url.protocol !== "https:") continue;
        if (url.username || url.password) continue;
        return url.href;
      } catch {
        // 不是合法链接，看下一个。
      }
    }
    return "";
  }

  function cleanText(value) {
    return String(value ?? "").replace(/[\u0000-\u001f\u007f]/g, " ").replace(/\s+/g, " ").trim();
  }

  /** 解析 location.search；没有可用的 http(s) 链接返回 null，否则 { url, title }。 */
  function parseShare(search) {
    if (typeof search !== "string" || !search || search.length > MAX_QUERY) return null;
    const params = new window.URLSearchParams(search);
    const text = params.get("text") || "";
    const url = firstLink(params.get("url") || "") || firstLink(text);
    if (!url) return null;
    let title = cleanText(params.get("title"));
    if (!title) {
      // 有些 App 把“标题 链接”都塞进 text：去掉链接后剩下的短文字当标题。
      const rest = cleanText(text.replace(LINK, " "));
      if (rest && rest.length <= 100) title = rest;
    }
    return { url, title: Array.from(title).slice(0, MAX_TITLE).join("") };
  }

  function hasShareParams(search) {
    const params = new window.URLSearchParams(search || "");
    return SHARE_KEYS.some((key) => params.has(key));
  }

  /** 启动时调用：收下分享的链接并清掉地址栏里的 title/text/url。返回是否收下了有效链接。 */
  function intake() {
    const location = window.location;
    const search = location?.search || "";
    if (!hasShareParams(search)) return false;
    const shared = parseShare(search);
    try {
      const params = new window.URLSearchParams(search);
      for (const key of SHARE_KEYS) params.delete(key);
      const rest = params.toString();
      window.history.replaceState(null, "", `${location.pathname}${rest ? `?${rest}` : ""}${location.hash || ""}`);
    } catch {
      // 地址栏清理失败不影响带入。
    }
    if (!shared || !window.Capture?.stashShared) return false;
    return window.Capture.stashShared(shared.url, shared.title);
  }

  window.ShareTarget = { parseShare, intake };
})();
