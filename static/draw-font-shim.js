"use strict";

/* draw-font-shim.js —— 自托管 Excalidraw 同源化前置脚本。
 *
 * 由 sw.js 在返回 /static/draw/draw.html 时注入到 <head>（classic 脚本，
 * 保证先于 ES module bundle 执行）；dist 目录本身保持原样、不做任何改动。
 *
 * 背景：Excalidraw bundle 构造手绘字体 FontFace 时，src 永远带两个候选：
 *   url(<本地 EXCALIDRAW_ASSET_PATH>/fonts/...woff2) 与一个跨源 CDN 兜底。
 * 浏览器会并发尝试候选：跨源候选被 CSP 瞬时否决，冷启动时还会把在连接队列里
 * 排队的本地候选一并挤掉，导致手绘字体整族加载失败。自托管构建已自带全部
 * 字体文件，跨源兜底没有存在意义，这里在 FontFace 构造前把所有跨源候选
 * 剔除，只保留 data:/blob:/相对路径/同源候选——画板生命周期内不再产生任何
 * 跨源字体请求。只包裹 FontFace 构造器，不改任何其它行为。
 */
(function () {
  var NativeFontFace = window.FontFace;
  if (typeof NativeFontFace !== "function" || NativeFontFace.__drawShimWrapped) return;

  /** 保留同源（及 data:/blob:/相对）url() 候选，剔除 http(s) 跨源候选。纯函数，便于单测。 */
  function sameOriginOnly(src, origin) {
    if (typeof src !== "string") return src;
    var kept = src.split(",").map(function (part) { return part.trim(); }).filter(function (part) {
      var match = /url\(\s*(['"]?)([^'")]+)\1\s*\)/.exec(part);
      if (!match) return true; // 不含 url() 的片段（理论上没有），保守保留
      var url = match[2];
      if (url.indexOf("data:") === 0 || url.indexOf("blob:") === 0) return true;
      if (url.indexOf("http://") !== 0 && url.indexOf("https://") !== 0) return true; // 相对路径
      return url.indexOf(origin) === 0;
    });
    return kept.join(", ");
  }

  function WrappedFontFace(family, src, descriptors) {
    return new NativeFontFace(family, sameOriginOnly(src, window.location.origin), descriptors);
  }
  WrappedFontFace.prototype = NativeFontFace.prototype;
  WrappedFontFace.__drawShimWrapped = true;
  WrappedFontFace.sameOriginOnly = sameOriginOnly;
  window.FontFace = WrappedFontFace;
  window.__drawFontShim = { sameOriginOnly: sameOriginOnly };
})();
