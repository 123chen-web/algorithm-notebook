/* 前端错误上报：把用户遇到的 JavaScript 报错（脱敏后）发给站长，只进服务器日志。
   只带：报错信息、文件名（不含域名和查询串）、行列、堆栈（去掉域名和查询串）、
   当前页面路由名（不含 id）、浏览器 UA、app.js 版本。不带任何用户输入内容。
   本脚本必须是页面里第一个脚本；任何环节失败都静默吞掉，绝不制造新的错误。 */
(function () {
  "use strict";
  if (typeof window === "undefined" || (window.ErrorReport && window.ErrorReport.installed)) return;

  var ENDPOINT = "/api/client-errors";
  var MAX_REPORTS = 5;
  var MIN_GAP_MS = 1000;
  var EXTENSION_RE = /(?:chrome|moz|safari|safari-web)-extension:\/\//i;

  function clip(value, max) {
    var text = String(value == null ? "" : value);
    return text.length > max ? text.slice(0, max) : text;
  }

  function stripUrls(text) {
    return String(text)
      .replace(/https?:\/\/[^\/\s)]+/gi, "") // 绝对地址的域名
      .replace(/\?[^\s):]*/g, "") // 查询串（含 ?v=103）
      .replace(/#[^\s):]*/g, ""); // 片段
  }

  function basename(value) {
    var text = stripUrls(String(value || ""));
    var last = text.split("/").pop() || "";
    return clip(last.replace(/[^\w.\-]/g, ""), 100);
  }

  function routeName(hash) {
    var match = /^#\/?([A-Za-z][A-Za-z0-9_-]{0,29})/.exec(String(hash || ""));
    return match ? "#/" + match[1] : "";
  }

  function buildVersion(doc) {
    try {
      var scripts = doc.querySelectorAll("script");
      for (var i = 0; i < scripts.length; i++) {
        var src = (scripts[i].getAttribute && scripts[i].getAttribute("src")) || scripts[i].src || "";
        var match = /\/static\/app\.js\?v=([0-9A-Za-z._-]{1,20})/.exec(src);
        if (match) return match[1];
      }
    } catch (error) { /* 取不到就不带 */ }
    return "";
  }

  /* 把一个 error / unhandledrejection 的原始信息整理成上报体；该忽略的返回 null。 */
  function build(input, env) {
    try {
      input = input || {};
      env = env || {};
      var message = clip(input.message, 300);
      var rawStack = String(input.stack == null ? "" : input.stack);
      var rawSource = String(input.source == null ? "" : input.source);
      if (EXTENSION_RE.test(rawStack) || EXTENSION_RE.test(rawSource)) return null;
      if (!message) return null;
      // 跨域脚本的报错浏览器只给 "Script error."，没有任何可用信息
      if (/^script error\.?$/i.test(message) && !rawSource && !input.line) return null;
      // 浏览器自己的无害提示，不是代码错误
      if (/^ResizeObserver loop/i.test(message)) return null;
      var stack = clip(stripUrls(rawStack), 1500);
      var source = basename(rawSource);
      if (!source) {
        var frame = /([\w.\-]+\.js)(?::\d+){0,2}/.exec(stripUrls(rawStack));
        if (frame) source = clip(frame[1], 100);
      }
      var report = {
        message: message,
        source: source,
        line: Math.max(0, Math.min(10000000, parseInt(input.line, 10) || 0)),
        col: Math.max(0, Math.min(10000000, parseInt(input.col, 10) || 0)),
        stack: stack,
        route: routeName(env.hash),
        ua: clip(env.ua, 200),
      };
      var version = env.build || "";
      if (version) report.build = clip(version, 20);
      return report;
    } catch (error) {
      return null;
    }
  }

  function newState() { return { sent: 0, last: -Infinity, seen: {} }; }

  /* 去重 + 每页最多 5 条 + 每秒最多 1 条。允许发送时登记并返回 true。 */
  function shouldSend(state, report, now) {
    if (!state || !report) return false;
    if (state.sent >= MAX_REPORTS) return false;
    var key = [report.message, report.source, report.line, report.col].join("|");
    if (state.seen[key]) return false;
    if (now - state.last < MIN_GAP_MS) return false;
    state.seen[key] = true;
    state.sent += 1;
    state.last = now;
    return true;
  }

  var state = newState();

  function send(report) {
    try {
      var result = window.fetch(ENDPOINT, {
        method: "POST",
        keepalive: true,
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRF-Protection": "1" },
        body: JSON.stringify(report),
      });
      if (result && typeof result.catch === "function") result.catch(function () {});
    } catch (error) { /* 上报失败不能再惹新错误 */ }
  }

  function handle(input) {
    try {
      var loc = window.location || {};
      var nav = window.navigator || {};
      var report = build(input, {
        hash: loc.hash,
        ua: nav.userAgent,
        build: buildVersion(window.document),
      });
      if (!report || !shouldSend(state, report, Date.now())) return;
      send(report);
    } catch (error) { /* 同上 */ }
  }

  function onError(event) {
    try {
      var err = event && event.error;
      handle({
        message: event && event.message,
        source: event && event.filename,
        line: event && event.lineno,
        col: event && event.colno,
        stack: err && err.stack,
      });
    } catch (error) { /* 同上 */ }
  }

  function onRejection(event) {
    try {
      var reason = event && event.reason;
      var message = "";
      var stack = "";
      if (reason && typeof reason === "object") {
        message = reason.message || "";
        stack = reason.stack || "";
      } else if (reason != null) {
        message = String(reason);
      }
      handle({ message: "Unhandled rejection: " + (message || "(no message)"), stack: stack });
    } catch (error) { /* 同上 */ }
  }

  window.ErrorReport = { build: build, shouldSend: shouldSend, newState: newState, installed: true };
  try {
    window.addEventListener("error", onError);
    window.addEventListener("unhandledrejection", onRejection);
  } catch (error) { /* 同上 */ }
})();
