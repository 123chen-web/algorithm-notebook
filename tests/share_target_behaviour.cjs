"use strict";

/* 手机分享直录（static/share-target.js + capture.js 的 stashShared）行为测试：
   Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

function makeEnv({ search = "", hash = "", storageBroken = false } = {}) {
  const store = new Map();
  const replaced = [];
  const sessionStorage = storageBroken
    ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } }
    : {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: (key) => store.delete(key),
    };
  const location = { origin: "https://oy.example.com", pathname: "/", search, hash };
  const history = {
    replaceState(_s, _t, url) {
      replaced.push(url);
      const q = url.indexOf("?");
      const h = url.indexOf("#");
      location.search = q >= 0 ? url.slice(q, h >= 0 ? h : undefined) : "";
      location.hash = h >= 0 ? url.slice(h) : "";
    },
  };
  const env = load(["capture.js", "share-target.js"], {
    extra: { URL, URLSearchParams, sessionStorage, location, history, navigator: {} },
  });
  return { ...env, store, replaced, location };
}

const plain = (value) => JSON.parse(JSON.stringify(value));
const parse = (search) => plain(makeEnv().window.ShareTarget.parseShare(search));

test("parseShare: url 参数优先，标题来自 title", () => {
  const got = parse("?title=Two%20Sum&text=ignored&url=https%3A%2F%2Fleetcode.cn%2Fproblems%2Ftwo-sum%2F");
  assert.deepEqual(got, { url: "https://leetcode.cn/problems/two-sum/", title: "Two Sum" });
});

test("parseShare: 安卓常把链接放进 text，没有 url 参数也能取到", () => {
  const got = parse("?text=" + encodeURIComponent("https://leetcode.cn/problems/two-sum/description/"));
  assert.equal(got.url, "https://leetcode.cn/problems/two-sum/description/");
  assert.equal(got.title, "");
});

test("parseShare: text 里“标题 链接”：链接取出来，剩下的短文字当标题；末尾标点不进链接", () => {
  const got = parse("?text=" + encodeURIComponent("两数之和 https://leetcode.cn/problems/two-sum/。"));
  assert.equal(got.url, "https://leetcode.cn/problems/two-sum/");
  assert.equal(got.title, "两数之和");
});

test("parseShare: 多个链接取第一个", () => {
  const got = parse("?text=" + encodeURIComponent("a https://a.example/x then https://b.example/y"));
  assert.equal(got.url, "https://a.example/x");
});

test("parseShare: 垃圾、javascript:、data:、ftp: 一律忽略", () => {
  assert.equal(parse(""), null);
  assert.equal(parse("?foo=bar"), null);
  assert.equal(parse("?text=just%20some%20words"), null);
  assert.equal(parse("?url=javascript%3Aalert(1)"), null);
  assert.equal(parse("?text=" + encodeURIComponent("javascript:alert(1) data:text/html,x ftp://x.example/a")), null);
  assert.equal(parse("?url=" + encodeURIComponent("https://user:pw@evil.example/")), null);
  assert.equal(parse("?url=" + encodeURIComponent("https://x.example/" + "a".repeat(2500))), null);
});

test("parseShare: url 参数不合法时回退到 text 里的合法链接", () => {
  const got = parse("?url=javascript%3Aalert(1)&text=" + encodeURIComponent("see https://ok.example/p"));
  assert.equal(got.url, "https://ok.example/p");
});

test("intake: 已识别的刷题站链接被暂存，地址栏里的 title/text/url 被清掉，刷新不会重复触发", () => {
  const env = makeEnv({ search: "?title=%E4%B8%A4%E6%95%B0%E4%B9%8B%E5%92%8C&url=https%3A%2F%2Fleetcode.cn%2Fproblems%2Ftwo-sum%2F%3Futm_source%3Dx", hash: "#/app" });
  assert.equal(env.window.ShareTarget.intake(), true);
  assert.deepEqual(env.replaced, ["/#/app"]);
  assert.equal(env.window.Capture.hasPending(), true);
  const taken = plain(env.window.Capture.take());
  assert.equal(taken.parsed.url, "https://leetcode.cn/problems/two-sum/");
  assert.equal(taken.title, "两数之和");
  assert.equal(taken.shared, true);
  assert.equal(env.window.Capture.take(), null);
  assert.equal(env.window.ShareTarget.intake(), false, "查询串已清空，不会再触发");
});

test("intake: 不认识的站点只要是 http(s) 也带入（只填来源链接），其他查询参数保留", () => {
  const env = makeEnv({ search: "?email_token=abc&text=" + encodeURIComponent("https://blog.example.org/post?id=3") });
  assert.equal(env.window.ShareTarget.intake(), true);
  assert.deepEqual(env.replaced, ["/?email_token=abc"]);
  const taken = plain(env.window.Capture.take());
  assert.equal(taken.parsed.url, "https://blog.example.org/post?id=3");
  assert.equal(taken.parsed.zone, "算法");
});

test("intake: 登录前存进 sessionStorage，页面重新加载后仍能取到；过期后丢弃", () => {
  const first = makeEnv({ search: "?url=https%3A%2F%2Fblog.example.org%2Fa" });
  first.window.ShareTarget.intake();
  assert.equal(first.store.size, 1);
  const raw = [...first.store.values()][0];
  const second = makeEnv();
  second.store.set([...first.store.keys()][0], raw);
  assert.equal(second.window.Capture.hasPending(), true);
  assert.equal(second.window.Capture.hasPending(Date.now() + 31 * 60 * 1000), false);
});

test("intake: 链接无效时只清地址栏，不暂存；存储被禁用也不抛错", () => {
  const bad = makeEnv({ search: "?url=javascript%3Aalert(1)&title=x" });
  assert.equal(bad.window.ShareTarget.intake(), false);
  assert.deepEqual(bad.replaced, ["/"]);
  assert.equal(bad.window.Capture.hasPending(), false);
  const blocked = makeEnv({ search: "?url=https%3A%2F%2Fblog.example.org%2Fa", storageBroken: true });
  assert.equal(blocked.window.ShareTarget.intake(), true);
  assert.equal(blocked.window.Capture.hasPending(), true, "内存里仍有一份");
});

test("fillForm: 分享来的链接填进来源链接字段，题名只填空着的", () => {
  const env = makeEnv({ search: "?title=%E5%8D%9A%E5%AE%A2&url=https%3A%2F%2Fblog.example.org%2Fa" });
  env.window.ShareTarget.intake();
  const record = env.window.Capture.take();
  const form = env.document.createElement("form");
  const title = env.document.createElement("input");
  title.setAttribute("name", "title");
  const source = env.document.createElement("input");
  source.setAttribute("name", "source_url");
  form.append(title, source);
  env.window.Capture.fillForm(form, record.parsed, record.title);
  assert.equal(source.value, "https://blog.example.org/a");
  assert.equal(title.value, "博客");
});
