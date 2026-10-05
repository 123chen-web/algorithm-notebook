/* 导出到 Anki 小部件的行为测试（Node 假浏览器，不需要真实浏览器）。
   覆盖：范围下拉、分区下拉联动、下载请求、文件名与 Blob 下载、
   413/429 等错误只写 textContent、迟到响应（重复点击/登出后）不落地。 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const SCRIPT = "anki-export.js";
const flush = async (rounds = 5) => {
  for (let i = 0; i < rounds; i += 1) await tick();
};

function blobEnv() {
  const created = [];
  const downloads = [];
  const extra = {
    Blob: class FakeBlob {
      constructor(parts, options) {
        this.parts = parts;
        this.options = options;
        this.size = String(parts && parts[0] ? parts[0] : "").length;
      }
    },
    URL: {
      createObjectURL(blob) {
        created.push(blob);
        return "blob:fake-anki";
      },
      revokeObjectURL() {},
    },
  };
  const env = load([SCRIPT], { extra });
  env.document.body.addEventListener("click", (event) => {
    const target = event.target;
    if (target && target.attributes && target.attributes.download) {
      downloads.push({
        filename: target.attributes.download,
        href: target.href,
      });
    }
  });
  return { env, created, downloads };
}

function okResponse(text, filename) {
  return {
    ok: true,
    status: 200,
    text: async () => text,
    headers: {
      get(name) {
        return String(name).toLowerCase() === "content-disposition"
          ? `attachment; filename="${filename}"`
          : null;
      },
    },
  };
}

function setScope(env, value) {
  const select = env.document.querySelector("#anki-scope");
  select.value = value;
  select.dispatchEvent(new FakeEvent("change", { bubbles: true }));
}

test("初始化：分区下拉默认隐藏，状态为空，对外接口就位", () => {
  const { env } = blobEnv();
  assert.equal(env.document.querySelector("#anki-zone").hidden, true);
  assert.equal(env.document.querySelector("#anki-status").textContent, "");
  assert.equal(typeof env.window.AnkiExport.setZones, "function");
  assert.equal(typeof env.window.AnkiExport.reset, "function");
});

test("setZones 用 option 填充分区，选项文字走 textContent", () => {
  const { env } = blobEnv();
  env.window.AnkiExport.setZones(["算法", "高等数学", "前端"]);
  const zone = env.document.querySelector("#anki-zone");
  assert.equal(zone.children.length, 3);
  assert.equal(zone.children[1].textContent, "高等数学");
  assert.equal(zone.children[1].value, "高等数学");
});

test("范围切到 zone 才显示分区下拉，切回其他范围重新隐藏", () => {
  const { env } = blobEnv();
  env.window.AnkiExport.setZones(["算法"]);
  setScope(env, "zone");
  assert.equal(env.document.querySelector("#anki-zone").hidden, false);
  setScope(env, "all");
  assert.equal(env.document.querySelector("#anki-zone").hidden, true);
});

test("下载全部：请求带 scope 与同源凭据，成功后用服务器文件名触发 Blob 下载", async () => {
  const { env, created, downloads } = blobEnv();
  setScope(env, "all");
  env.document.querySelector("#anki-download").click();
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, "/api/export/anki?scope=all");
  assert.equal(env.calls[0].init.credentials, "same-origin");
  assert.equal(env.calls[0].init.headers["X-CSRF-Protection"], "1");
  assert.match(env.document.querySelector("#anki-status").textContent, /正在生成/);

  env.calls[0].resolve(okResponse("#separator:tab\n卡片正文", "oy-anki-20260919.txt"));
  await flush();

  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].filename, "oy-anki-20260919.txt");
  assert.equal(downloads[0].href, "blob:fake-anki");
  assert.equal(created.length, 1);
  assert.equal(created[0].parts[0], "#separator:tab\n卡片正文");
  assert.match(env.document.querySelector("#anki-status").textContent, /下载/);
  assert.equal(env.document.querySelector("#anki-download").disabled, false);
});

test("按分区下载：URL 带 zone，中文分区名做 URL 编码", async () => {
  const { env, downloads } = blobEnv();
  env.window.AnkiExport.setZones(["算法", "高等数学"]);
  setScope(env, "zone");
  env.document.querySelector("#anki-zone").value = "高等数学";
  env.document.querySelector("#anki-download").click();
  assert.equal(
    env.calls[0].url,
    "/api/export/anki?scope=zone&zone=" + encodeURIComponent("高等数学"),
  );
  env.calls[0].resolve(okResponse("卡片", "oy-anki-20260919.txt"));
  await flush();
  assert.equal(downloads.length, 1);
});

test("选了按分区但分区还没就绪：提示选择分区，不发请求", () => {
  const { env } = blobEnv();
  setScope(env, "zone");
  env.document.querySelector("#anki-download").click();
  assert.equal(env.calls.length, 0);
  assert.match(env.document.querySelector("#anki-status").textContent, /分区/);
});

test("迟到响应守卫：后点的请求先生效，先点的慢响应回来不重复下载", async () => {
  const { env, downloads } = blobEnv();
  setScope(env, "all");
  env.document.querySelector("#anki-download").click();
  setScope(env, "weak");
  env.document.querySelector("#anki-download").click();
  assert.equal(env.calls.length, 2);

  env.calls[1].resolve(okResponse("薄弱卡片", "oy-anki-weak.txt"));
  await flush();
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].filename, "oy-anki-weak.txt");

  // 第一个请求（all）这时才回来：不能再触发下载，也不能覆盖提示。
  env.calls[0].resolve(okResponse("全部卡片", "oy-anki-all.txt"));
  await flush();
  assert.equal(downloads.length, 1);
  assert.equal(downloads[0].filename, "oy-anki-weak.txt");
});

test("登出/换账号后回来的响应：不下载、不写状态、不恢复按钮", async () => {
  const { env, downloads } = blobEnv();
  setScope(env, "all");
  env.document.querySelector("#anki-download").click();
  env.window.AnkiExport.reset();
  assert.equal(env.document.querySelector("#anki-status").textContent, "");

  env.calls[0].resolve(okResponse("迟到的卡片", "oy-anki-late.txt"));
  await flush();
  assert.equal(downloads.length, 0);
  assert.equal(env.document.querySelector("#anki-status").textContent, "");
});

test("413/429/403：服务器 detail 原样写进状态，按钮恢复，不下载", async () => {
  const cases = [
    [413, "记录超过 5000 条，请按分区导出"],
    [429, "导出过于频繁，请一小时后再试"],
    [403, "体验账号不能导出到 Anki，请先注册正式账号"],
  ];
  for (const [status, detail] of cases) {
    const { env, downloads } = blobEnv();
    setScope(env, "all");
    env.document.querySelector("#anki-download").click();
    env.respond(env.calls[0], status, { detail });
    await flush();
    assert.equal(env.document.querySelector("#anki-status").textContent, detail);
    assert.equal(env.document.querySelector("#anki-download").disabled, false);
    assert.equal(downloads.length, 0);
  }
});

test("网络异常：提示网络出错，按钮恢复", async () => {
  const { env, downloads } = blobEnv();
  setScope(env, "all");
  env.document.querySelector("#anki-download").click();
  env.calls[0].reject(new Error("network down"));
  await flush();
  assert.match(env.document.querySelector("#anki-status").textContent, /网络/);
  assert.equal(env.document.querySelector("#anki-download").disabled, false);
  assert.equal(downloads.length, 0);
});

test("reset 后范围与分区下拉复位，可重新下载", async () => {
  const { env, downloads } = blobEnv();
  env.window.AnkiExport.setZones(["算法"]);
  setScope(env, "zone");
  env.window.AnkiExport.reset();
  assert.equal(env.document.querySelector("#anki-scope").value, "all");
  assert.equal(env.document.querySelector("#anki-zone").hidden, true);
  env.document.querySelector("#anki-download").click();
  assert.equal(env.calls[0].url, "/api/export/anki?scope=all");
  env.calls[0].resolve(okResponse("卡片", "oy-anki.txt"));
  await flush();
  assert.equal(downloads.length, 1);
});
