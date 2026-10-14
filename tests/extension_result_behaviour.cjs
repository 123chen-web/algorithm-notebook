/* 扩展结果页适配器行为测试：用最小 HTML 夹具 + 假 DOM 跑各站点适配器。
 * 断言：识别出 verdict code、题名、代码；AC 不弹；去重键；请求体字段与截断。
 *
 * 假 DOM 只实现适配器用到的子集：querySelector（#id / .class / tag /
 * [attr*='v'] / 组合与逗号分隔）、textContent、href、title、body。
 */
"use strict";

const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const EXT = path.join(__dirname, "..", "extension", "ouye-extension");
const FIX = path.join(__dirname, "fixtures", "extension");

/* ---------- 最小假 DOM ---------- */

function decodeEntities(s) {
  return s
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">")
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, '"');
}

function parseAttrs(str) {
  const attrs = {};
  const re = /([a-zA-Z_:][a-zA-Z0-9_:.-]*)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+)))?/g;
  let m;
  while ((m = re.exec(str))) {
    attrs[m[1].toLowerCase()] = m[2] !== undefined ? m[2] : m[3] !== undefined ? m[3] : m[4] !== undefined ? m[4] : "";
  }
  return attrs;
}

class FakeEl {
  constructor(tag, attrs, parent) {
    this.tagName = tag.toUpperCase();
    this.attrs = attrs || {};
    this.parent = parent || null;
    this.children = [];
    this._text = "";
  }
  get id() {
    return this.attrs.id || "";
  }
  get className() {
    return this.attrs.class || "";
  }
  get href() {
    return this.attrs.href || "";
  }
  get textContent() {
    let out = this._text;
    for (const c of this.children) out += c.textContent;
    return out;
  }
  getAttribute(n) {
    return this.attrs[String(n).toLowerCase()];
  }
  matches(sel) {
    return matchSel(this, sel.trim());
  }
  querySelector(sel) {
    for (const part of sel.split(",")) {
      const found = findFirst(this, part.trim());
      if (found) return found;
    }
    return null;
  }
  // 后代选择器："A B" = 在匹配 A 的子树里找匹配 B 的。
  querySelectorDeep(sel) {
    return this.querySelector(sel);
  }
  querySelectorAll() {
    return [];
  }
  cloneNode() {
    return this;
  }
}

function matchSel(el, sel) {
  // 支持：#id / .class / tag / [a*='v'] / tag.cls / tag[attr*='v'] / .c1.c2
  let rest = sel;
  let tag = null;
  const tagM = /^[a-zA-Z][a-zA-Z0-9]*/.exec(rest);
  if (tagM) {
    tag = tagM[0].toUpperCase();
    rest = rest.slice(tagM[0].length);
  }
  if (tag && el.tagName !== tag) return false;
  const re = /([#.])([a-zA-Z0-9_-]+)|\[([a-zA-Z0-9-]+)(\*=|=)'([^']*)'\]/g;
  let m;
  let ok = true;
  let matched = false;
  while ((m = re.exec(rest))) {
    matched = true;
    if (m[1] === "#") {
      if (el.id !== m[2]) ok = false;
    } else if (m[1] === ".") {
      if (!el.className.split(/\s+/).includes(m[2])) ok = false;
    } else if (m[3]) {
      const v = el.getAttribute(m[3]) || "";
      if (m[4] === "*=") {
        if (!v.includes(m[5])) ok = false;
      } else {
        if (v !== m[5]) ok = false;
      }
    }
  }
  if (!matched && !tag) return false;
  return ok;
}

function findFirst(root, sel) {
  // 后代选择器："A B" 先找 A，再在 A 的子树里找 B。
  const spaceIdx = sel.search(/\s+(?![^[]*\])/);
  if (spaceIdx !== -1) {
    const ancestorSel = sel.slice(0, spaceIdx).trim();
    const descSel = sel.slice(spaceIdx).trim();
    const ancestors = findAll(root, ancestorSel);
    for (const anc of ancestors) {
      const found = findFirst(anc, descSel);
      if (found) return found;
    }
    return null;
  }
  const stack = [...root.children];
  while (stack.length) {
    const el = stack.shift();
    if (el.matches(sel)) return el;
    stack.unshift(...el.children);
  }
  return null;
}

function findAll(root, sel) {
  const out = [];
  const stack = [...root.children];
  while (stack.length) {
    const el = stack.shift();
    if (el.matches(sel)) out.push(el);
    stack.unshift(...el.children);
  }
  return out;
}

function parseHTML(html) {
  const root = new FakeEl("ROOT", {});
  const body = new FakeEl("BODY", {});
  root.children.push(body);
  body.parent = root;
  const stack = [body];
  // 标题
  const titleM = /<title>([\s\S]*?)<\/title>/i.exec(html);
  const docTitle = titleM ? decodeEntities(titleM[1]).trim() : "";
  const tagRe = /<\/?([a-zA-Z][a-zA-Z0-9]*)\b([^>]*)>|([^<]+)/g;
  let m;
  while ((m = tagRe.exec(html))) {
    if (m[3] !== undefined) {
      const t = decodeEntities(m[3]);
      if (t.trim()) stack[stack.length - 1]._text += t;
    } else {
      const isClose = m[0].startsWith("</");
      const tag = m[1].toLowerCase();
      if (isClose) {
        while (stack.length > 1 && stack[stack.length - 1].tagName !== tag.toUpperCase()) stack.pop();
        if (stack.length > 1) stack.pop();
      } else if (!/^(meta|link|br|hr|img|input)$/.test(tag)) {
        const el = new FakeEl(tag, parseAttrs(m[2] || ""), stack[stack.length - 1]);
        stack[stack.length - 1].children.push(el);
        if (!m[0].endsWith("/>")) stack.push(el);
      }
    }
  }
  return {
    title: docTitle,
    body,
    querySelector(sel) {
      return body.querySelector(sel);
    },
    querySelectorAll() {
      return [];
    },
  };
}

/* ---------- 加载扩展脚本 ---------- */

function loadExtension() {
  const sandbox = {
    console,
    URL,
    RegExp,
    globalThis: null,
  };
  sandbox.globalThis = sandbox;
  vm.createContext(sandbox);
  const files = [
    "lib/api.js",
    "lib/dedup.js",
    "lib/validate.js",
    "lib/verdict.js",
    "content/adapters.js",
    "content/sites/luogu.js",
    "content/sites/leetcode.js",
    "content/sites/nowcoder.js",
    "content/sites/codeforces.js",
    "content/sites/atcoder.js",
  ];
  for (const f of files) {
    const code = fs.readFileSync(path.join(EXT, f), "utf8");
    vm.runInContext(code, sandbox, { filename: f });
  }
  return sandbox;
}

function fixture(name) {
  return parseHTML(fs.readFileSync(path.join(FIX, name), "utf8"));
}

/* ---------- 测试 ---------- */

test("verdict 映射表：各站点原文 → code，AC → null，未知 → undefined", () => {
  const sb = loadExtension();
  const OY = sb.OY;
  assert.equal(OY.mapVerdict("atcoder", "WA"), "WA");
  assert.equal(OY.mapVerdict("atcoder", "TLE"), "TLE");
  assert.equal(OY.mapVerdict("atcoder", "AC"), null);
  assert.equal(OY.mapVerdict("codeforces", "Wrong answer on test 12"), "WA");
  assert.equal(OY.mapVerdict("codeforces", "Time limit exceeded on test 3"), "TLE");
  assert.equal(OY.mapVerdict("codeforces", "Accepted"), null);
  assert.equal(OY.mapVerdict("leetcode-cn", "解答错误"), "WA");
  assert.equal(OY.mapVerdict("leetcode-cn", "超出时间限制"), "TLE");
  assert.equal(OY.mapVerdict("luogu", "Wrong Answer"), "WA");
  assert.equal(OY.mapVerdict("luogu", "Accepted"), null);
  assert.equal(OY.mapVerdict("nowcoder", "答案错误"), "WA");
  assert.equal(OY.mapVerdict("atcoder", "???"), undefined);
  assert.equal(OY.isFailedVerdict("WA"), true);
  assert.equal(OY.isFailedVerdict("TLE"), true);
  assert.equal(OY.isFailedVerdict(null), false);
  assert.equal(OY.isFailedVerdict(undefined), false);
});

test("AtCoder 结果页：识别 WA + 题名 + 代码 + 提交号", () => {
  const sb = loadExtension();
  const url = "https://atcoder.jp/contests/abc214/submissions/12345678";
  const doc = fixture("atcoder-result.html");
  assert.ok(sb.AdapterRegistry.detectResult(url), "应识别为结果页");
  const r = sb.AdapterRegistry.extractResult(url, doc);
  assert.ok(r.ok);
  assert.equal(r.data.verdictCode, "WA");
  assert.ok(r.data.title.includes("Welcome to AtCoder"));
  assert.equal(r.data.submissionId, "12345678");
  assert.ok(r.data.code.includes('print(n + 1)'));
});

test("AtCoder AC 页：verdictCode 为 null（不弹）", () => {
  const sb = loadExtension();
  const url = "https://atcoder.jp/contests/abc214/submissions/12345679";
  const doc = fixture("atcoder-result-ac.html");
  const r = sb.AdapterRegistry.extractResult(url, doc);
  assert.ok(r.ok);
  assert.equal(r.data.verdictCode, null);
  assert.equal(sb.OY.isFailedVerdict(r.data.verdictCode), false);
});

test("Codeforces 结果页：识别 Wrong answer → WA", () => {
  const sb = loadExtension();
  const url = "https://codeforces.com/contest/1/submission/123456789";
  const doc = fixture("codeforces-result.html");
  assert.ok(sb.AdapterRegistry.detectResult(url));
  const r = sb.AdapterRegistry.extractResult(url, doc);
  assert.ok(r.ok);
  assert.equal(r.data.verdictCode, "WA");
  assert.equal(r.data.submissionId, "123456789");
  assert.ok(r.data.code.includes("bits/stdc++.h"));
});

test("力扣中国结果页：识别 解答错误 → WA（SPA 结构）", () => {
  const sb = loadExtension();
  const url = "https://leetcode.cn/problems/two-sum/submissions/123456789/";
  const doc = fixture("leetcode-result.html");
  assert.ok(sb.AdapterRegistry.detectResult(url));
  const r = sb.AdapterRegistry.extractResult(url, doc);
  assert.ok(r.ok);
  assert.equal(r.data.verdictCode, "WA");
  assert.equal(r.data.submissionId, "123456789");
});

test("洛谷结果页：识别 Wrong Answer → WA", () => {
  const sb = loadExtension();
  const url = "https://www.luogu.com.cn/record/98765432";
  const doc = fixture("luogu-result.html");
  assert.ok(sb.AdapterRegistry.detectResult(url));
  const r = sb.AdapterRegistry.extractResult(url, doc);
  assert.ok(r.ok);
  assert.equal(r.data.verdictCode, "WA");
  assert.equal(r.data.submissionId, "98765432");
  assert.ok(r.data.code.includes("scanf"));
});

test("牛客结果页（尽力而为）：识别 答案错误 → WA，degraded 标记", () => {
  const sb = loadExtension();
  const url = "https://ac.nowcoder.com/acm/contest/123/submission/456";
  const doc = fixture("nowcoder-result.html");
  assert.ok(sb.AdapterRegistry.detectResult(url));
  const r = sb.AdapterRegistry.extractResult(url, doc);
  assert.ok(r.ok);
  assert.equal(r.data.verdictCode, "WA");
  assert.ok(r.data.degraded.includes("nowcoder-best-effort"));
});

test("非结果页：题目页不被误判为结果页", () => {
  const sb = loadExtension();
  assert.equal(sb.AdapterRegistry.detectResult("https://atcoder.jp/contests/abc214/tasks/abc214_a"), null);
  assert.equal(sb.AdapterRegistry.detectResult("https://codeforces.com/problemset/problem/1/A"), null);
  assert.equal(sb.AdapterRegistry.detectResult("https://leetcode.cn/problems/two-sum/"), null);
});

test("去重：同一次提交只弹一次；今天不再提醒按天+题目", () => {
  const sb = loadExtension();
  const OY = sb.OY;
  let prompted = {};
  assert.equal(OY.hasPromptedResult(prompted, "atcoder", "123"), false);
  prompted = OY.markResultPrompted(prompted, "atcoder", "123");
  assert.equal(OY.hasPromptedResult(prompted, "atcoder", "123"), true);
  assert.equal(OY.hasPromptedResult(prompted, "atcoder", "124"), false);

  let muted = {};
  assert.equal(OY.isResultMutedToday(muted, "luogu", "P1001", "2026-10-14"), false);
  muted = OY.muteResultToday(muted, "luogu", "P1001", "2026-10-14");
  assert.equal(OY.isResultMutedToday(muted, "luogu", "P1001", "2026-10-14"), true);
  assert.equal(OY.isResultMutedToday(muted, "luogu", "P1001", "2026-10-15"), false);
});

test("请求体：verdict + submitted_code 进 body；超长截断并标记", () => {
  const sb = loadExtension();
  const OY = sb.OY;
  const req = OY.buildImportRequest({
    baseUrl: "https://ouyeoy.com",
    token: "oyt_test",
    data: {
      source: "codeforces",
      url: "https://codeforces.com/contest/1/problem/A",
      title: "A. Theatre Square",
      content: "题面",
      verdict: "WA",
      submitted_code: "x".repeat(25000),
      language: "GNU C++17",
    },
  });
  assert.equal(req.body.verdict, "WA");
  assert.equal(req.body.submitted_code.length, 20000);
  assert.equal(req.codeTruncated, true);
  // token 只在头里，不在 URL 里
  assert.ok(!req.url.includes("oyt_test"));
  assert.equal(req.headers["X-CSRF-Protection"], "1");
});

test("请求体：无 verdict/code 时不带多余字段", () => {
  const sb = loadExtension();
  const req = sb.OY.buildImportRequest({
    baseUrl: "https://ouyeoy.com",
    token: "oyt_test",
    data: { source: "luogu", url: "https://www.luogu.com.cn/problem/P1001", title: "P1001", content: "题面" },
  });
  assert.ok(!("verdict" in req.body));
  assert.ok(!("submitted_code" in req.body));
  assert.equal(req.codeTruncated, false);
});
