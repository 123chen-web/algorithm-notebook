"use strict";

/* draw-font-shim.js 行为测试：FontFace 构造器包裹后，跨源候选被剔除、
 * 同源/相对/data 候选保留，且构造结果仍来自原生 FontFace。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const SHIM = fs.readFileSync(
  path.join(__dirname, "..", "static", "draw-font-shim.js"),
  "utf8"
);

function loadShim() {
  const calls = [];
  function NativeFontFace(family, src, descriptors) {
    calls.push({ family, src, descriptors });
    this.family = family;
    this.src = src;
  }
  const sandbox = {
    window: {
      location: { origin: "http://127.0.0.1:8123" },
      FontFace: NativeFontFace,
    },
  };
  sandbox.window.window = sandbox.window;
  vm.createContext(sandbox);
  vm.runInContext(SHIM, sandbox, { filename: "draw-font-shim.js" });
  return { calls, window: sandbox.window, NativeFontFace };
}

test("包裹后构造器仍产出原生 FontFace 实例", () => {
  const env = loadShim();
  const face = new env.window.FontFace("X", "url(http://127.0.0.1:8123/a.woff2)", {});
  assert.ok(face instanceof env.NativeFontFace);
  assert.equal(face.family, "X");
});

test("同源 + esm 双候选：esm 跨源候选被剔除，只留同源", () => {
  const env = loadShim();
  new env.window.FontFace(
    "Xiaolai",
    "url(http://127.0.0.1:8123/static/draw/fonts/X/a.woff2) format('woff2'), "
      + "url(https://esm.sh/@excalidraw/excalidraw@0.18.1/dist/prod/fonts/X/a.woff2) format('woff2')",
    {}
  );
  const src = env.calls[0].src;
  assert.ok(src.includes("127.0.0.1:8123"), src);
  assert.ok(!src.includes("esm.sh"), src);
});

test("相对路径、data:、blob: 候选保留；任意 http(s) 跨源剔除", () => {
  const env = loadShim();
  const fn = env.window.__drawFontShim.sameOriginOnly;
  const origin = "http://127.0.0.1:8123";
  assert.ok(fn("url(./fonts/a.woff2) format('woff2')", origin).includes("./fonts/a.woff2"));
  assert.ok(fn("url(data:font/woff2;base64,AAAA) format('woff2')", origin).includes("data:"));
  assert.ok(fn("url(blob:http://127.0.0.1:8123/x) format('woff2')", origin).includes("blob:"));
  const mixed = fn(
    "url(http://127.0.0.1:8123/a.woff2), url(https://fonts.gstatic.com/x.woff2)",
    origin
  );
  assert.ok(mixed.includes("127.0.0.1"));
  assert.ok(!mixed.includes("gstatic"));
});

test("幂等：同一上下文重复执行脚本不会二次包裹", () => {
  const env = loadShim();
  const first = env.window.FontFace;
  const sandbox = vm.createContext({ window: env.window });
  vm.runInContext(SHIM, sandbox, { filename: "draw-font-shim.js" });
  assert.equal(env.window.FontFace, first);
  // 仍能正常构造
  const face = new env.window.FontFace("Y", "url(http://127.0.0.1:8123/b.woff2)");
  assert.ok(face instanceof env.NativeFontFace);
  assert.equal(env.calls.length, 1);
});
