"use strict";

/* 收录（粘贴题目链接 / 书签小工具）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

const ORIGIN = "https://oy.example.com";

function makeEnv({ hash = "", storageBroken = false, clipboard } = {}) {
  const store = new Map();
  const replaced = [];
  const sessionStorage = storageBroken
    ? { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); }, removeItem() { throw new Error("blocked"); } }
    : {
      getItem: (key) => (store.has(key) ? store.get(key) : null),
      setItem: (key, value) => store.set(key, String(value)),
      removeItem: (key) => store.delete(key),
    };
  const location = { origin: ORIGIN, pathname: "/", search: "", hash };
  const history = { replaceState(_s, _t, url) { replaced.push(url); location.hash = url.slice(url.indexOf("#")); } };
  const env = load(["capture.js"], { extra: { URL, sessionStorage, location, history, navigator: { clipboard } } });
  return { ...env, store, replaced, location };
}

const cases = (env) => env.window.Capture;

/* ---------------- parse ---------------- */
test("parse: LeetCode (cn / com / www / 大小写 / 末尾斜杠 / 跟踪参数)", () => {
  const { parse } = cases(makeEnv());
  const result = parse("https://leetcode.cn/problems/two-sum/description/?utm_source=x&envType=daily");
  assert.equal(result.title, "LeetCode · Two Sum");
  assert.equal(result.id, "two-sum");
  assert.equal(result.zone, "算法");
  assert.equal(result.source, "leetcode");
  assert.equal(result.url, "https://leetcode.cn/problems/two-sum/description/?envType=daily");
  assert.equal(parse("HTTPS://WWW.LeetCode.com/problems/Longest-Substring-Without-Repeating-Characters/").title,
    "LeetCode · Longest Substring Without Repeating Characters");
  assert.equal(parse("http://leetcode.com/problems/two-sum").url, "http://leetcode.com/problems/two-sum");
  assert.equal(parse("https://leetcode.cn/problems/"), null);
  assert.equal(parse("https://leetcode.cn/contest/weekly-contest-1/"), null);
});

test("parse: 洛谷、牛客、Codeforces、AtCoder", () => {
  const { parse } = cases(makeEnv());
  assert.equal(parse("https://www.luogu.com.cn/problem/P1001?spm=1&from=x").title, "洛谷 · P1001");
  assert.equal(parse("https://www.luogu.com.cn/problem/P1001?spm=1&from=x").url, "https://www.luogu.com.cn/problem/P1001");
  assert.equal(parse("https://luogu.com.cn/problem/cf1a").title, "洛谷 · CF1A");
  assert.equal(parse("https://www.nowcoder.com/practice/abc123DEF").title, "牛客 · 题目");
  assert.equal(parse("https://nowcoder.com/questionTerminal/abc123/").id, "abc123");
  assert.equal(parse("https://codeforces.com/problemset/problem/1/a").title, "Codeforces · 1A");
  assert.equal(parse("https://codeforces.com/contest/1234/problem/B1").title, "Codeforces · 1234B1");
  assert.equal(parse("https://www.codeforces.com/gym/102/problem/c").title, "Codeforces · 102C");
  assert.equal(parse("https://atcoder.jp/contests/abc001/tasks/abc001_a").title, "AtCoder · abc001_a");
  assert.equal(parse("https://atcoder.jp/contests/abc001/"), null);
});

test("parse: 首尾空白与中文标点会被去掉", () => {
  const { parse } = cases(makeEnv());
  assert.equal(parse("  “https://leetcode.cn/problems/two-sum/”。 \n").id, "two-sum");
  assert.equal(parse("（https://leetcode.cn/problems/two-sum），").id, "two-sum");
});

test("parse: 拒绝 javascript: / data: / 超长 / 账号密码 / 未知主机 / 空串", () => {
  const { parse } = cases(makeEnv());
  for (const bad of [
    "javascript:alert(1)", "JavaScript://leetcode.cn/problems/two-sum/%0aalert(1)",
    "data:text/html,<script>1</script>", "ftp://leetcode.cn/problems/two-sum",
    "https://user:pw@leetcode.cn/problems/two-sum/", "https://user@leetcode.cn/problems/two-sum/",
    "https://evil.com/problems/two-sum/", "https://leetcode.cn.evil.com/problems/two-sum/",
    "https://notleetcode.cn/problems/two-sum/", "https://leetcode.cn:8443/problems/two-sum/",
    "", "   ", "两数之和", "https://leetcode.cn/problems/two sum/", null, undefined, 42,
    `https://leetcode.cn/problems/${"a".repeat(2001)}/`,
  ]) assert.equal(parse(bad), null, String(bad).slice(0, 50));
});

/* ---------------- 标题清洗 ---------------- */
test("cleanTitle: 去站点后缀、折叠空白、截到 200 字符", () => {
  const { cleanTitle } = cases(makeEnv());
  assert.equal(cleanTitle("1. 两数之和 - 力扣（LeetCode）"), "1. 两数之和");
  assert.equal(cleanTitle("Two Sum - LeetCode"), "Two Sum");
  assert.equal(cleanTitle("P1001 A+B Problem | 洛谷"), "P1001 A+B Problem");
  assert.equal(cleanTitle("P1001 A+B Problem - 洛谷 | 计算机科学教育新生态"), "P1001 A+B Problem");
  assert.equal(cleanTitle("  A \n\t  B   "), "A B");
  assert.equal(Array.from(cleanTitle("字".repeat(500))).length, 200);
  assert.equal(cleanTitle(null), "");
});

/* ---------------- 书签脚本 ---------------- */
test("bookmarklet: 只做 window.open，origin 被编码进去，没有 cookie / 请求", () => {
  const { bookmarkletCode, bookmarkletHref } = cases(makeEnv());
  const code = bookmarkletCode(ORIGIN);
  assert.ok(code.includes(`window.open('${ORIGIN}/#/app?new=1&u='+encodeURIComponent(location.href)+'&t='+encodeURIComponent(document.title),'_blank','noopener')`));
  assert.doesNotMatch(code, /cookie|fetch|XMLHttpRequest|sendBeacon|localStorage|document\.(write|createElement|body)/i);
  assert.equal(code.includes("\n"), false);
  const href = bookmarkletHref(ORIGIN);
  assert.ok(href.startsWith("javascript:"));
  assert.equal(href.includes('"'), false);
  assert.equal(bookmarkletCode("javascript:alert(1)"), "");
  assert.equal(bookmarkletCode("https://a.com'+alert(1)+'"), "");
  assert.equal(bookmarkletHref(""), "");
  // 带端口的 origin 也能用，单引号 / 反斜杠不会逃出字符串（被校验直接拒绝）
  assert.ok(bookmarkletCode("http://localhost:8000").includes("http://localhost:8000/#/app"));
  // 实际执行生成的脚本，确认它真的只打开一个带参数的新窗口
  const opened = [];
  const run = new Function("window", "location", "document", "encodeURIComponent", `${code}`);
  run({ open: (...args) => opened.push(args) }, { href: "https://leetcode.cn/problems/two-sum/" }, { title: "两数之和 - 力扣（LeetCode）" }, encodeURIComponent);
  assert.deepEqual(opened, [[`${ORIGIN}/#/app?new=1&u=${encodeURIComponent("https://leetcode.cn/problems/two-sum/")}&t=${encodeURIComponent("两数之和 - 力扣（LeetCode）")}`, "_blank", "noopener"]]);
});

/* ---------------- 哈希接收 ---------------- */
const hashFor = (u, t) => `#/app?new=1&u=${encodeURIComponent(u)}&t=${encodeURIComponent(t)}`;

test("hash: 合法参数被收下，地址栏立刻去参数，take 只能取一次", () => {
  const env = makeEnv({ hash: hashFor("https://leetcode.cn/problems/two-sum/?utm_source=a", "两数之和 - 力扣（LeetCode）") });
  const { Capture } = env.window;
  assert.equal(Capture.intake(), true);
  assert.deepEqual(env.replaced, ["/#/app"]);
  assert.equal(env.location.hash, "#/app");
  assert.equal(Capture.hasPending(), true);
  const taken = Capture.take();
  assert.equal(taken.title, "两数之和");
  assert.equal(taken.parsed.url, "https://leetcode.cn/problems/two-sum/");
  assert.equal(Capture.take(), null);
  assert.equal(env.store.size, 0);
  assert.equal(Capture.intake(), false, "地址栏已清理，重复调用没有新预填");
});

test("hash: 非法 / 重复 / 缺失参数一律忽略但仍清地址栏", () => {
  for (const hash of [
    "#/app?new=1&u=javascript%3Aalert(1)&t=x",
    "#/app?new=1&u=https%3A%2F%2Fevil.com%2Fproblems%2Fx",
    "#/app?new=1&t=只有标题",
    "#/app?new=2&u=https%3A%2F%2Fleetcode.cn%2Fproblems%2Ftwo-sum%2F",
    `#/app?new=1&u=${encodeURIComponent("https://leetcode.cn/problems/a/")}&u=${encodeURIComponent("https://leetcode.cn/problems/b/")}`,
    "#/app?new=1&new=1&u=" + encodeURIComponent("https://leetcode.cn/problems/a/"),
    "#/app?%E0%A4%A",
  ]) {
    const env = makeEnv({ hash });
    assert.equal(env.window.Capture.intake(), false, hash);
    assert.deepEqual(env.replaced, ["/#/app"], hash);
    assert.equal(env.window.Capture.hasPending(), false, hash);
  }
  const plain = makeEnv({ hash: "#/app" });
  assert.equal(plain.window.Capture.intake(), false);
  assert.deepEqual(plain.replaced, [], "不带参数的 #/app 不碰地址栏");
});

test("hash: 未登录时存进 sessionStorage，登录后取出；登出清除；过期忽略", () => {
  const env = makeEnv({ hash: hashFor("https://www.luogu.com.cn/problem/P1001", "P1001 A+B Problem - 洛谷") });
  const { Capture } = env.window;
  Capture.intake();
  assert.equal(env.store.size, 1);
  const saved = JSON.parse([...env.store.values()][0]);
  assert.equal(saved.url, "https://www.luogu.com.cn/problem/P1001");

  // 模拟页面刷新：新的脚本环境共用同一份 sessionStorage
  const sessionStorage = { getItem: (k) => env.store.get(k) ?? null, setItem() {}, removeItem: (k) => env.store.delete(k) };
  const reloaded = load(["capture.js"], { extra: { URL, sessionStorage, location: { origin: ORIGIN, pathname: "/", search: "", hash: "#/welcome" }, history: { replaceState() {} } } });
  const got = reloaded.window.Capture.take();
  assert.equal(got.title, "P1001 A+B Problem");
  assert.equal(env.store.size, 0, "取出后清除");

  Capture.intake();
  Capture.reset(); // 登出
  assert.equal(Capture.take(), null);
  assert.equal(env.store.size, 0);

  const later = makeEnv({ hash: hashFor("https://leetcode.cn/problems/two-sum/", "t") });
  later.window.Capture.intake();
  assert.equal(later.window.Capture.take(Date.now() + 31 * 60 * 1000), null, "超过 30 分钟视为过期");
});

test("hash: 被篡改的 sessionStorage 内容不会被采信", () => {
  const env = makeEnv();
  env.store.set("oy-capture-pending", JSON.stringify({ url: "javascript:alert(1)", title: "x", at: Date.now() }));
  assert.equal(env.window.Capture.take(), null);
  env.store.set("oy-capture-pending", "{not json");
  assert.equal(env.window.Capture.take(), null);
});

test("storage: sessionStorage 抛异常时不崩，内存里的预填仍可用", () => {
  const env = makeEnv({ hash: hashFor("https://leetcode.cn/problems/two-sum/", "两数之和"), storageBroken: true });
  const { Capture } = env.window;
  assert.doesNotThrow(() => Capture.intake());
  assert.equal(Capture.take().parsed.id, "two-sum");
  assert.doesNotThrow(() => Capture.reset());
  const none = load(["capture.js"], { extra: { URL, location: { origin: ORIGIN, pathname: "/", search: "", hash: "" }, history: { replaceState() { throw new Error("no"); } } } });
  assert.equal(none.window.Capture.take(), null, "没有 sessionStorage 也不崩");
  assert.doesNotThrow(() => none.window.Capture.intake());
});

/* ---------------- 页面：预填规则 ---------------- */
function buildPage(env, { zones = ["数学", "算法", "英语"], thinking = "", title = "", zone } = {}) {
  const doc = env.document;
  const el = (tag, props = {}) => Object.assign(doc.createElement(tag), props);
  const page = el("section");
  page.id = "new-page";
  const box = el("section", { id: "capture-box" });
  const input = el("input", { id: "capture-url" });
  const go = el("button", { id: "capture-go" });
  const status = el("p", { id: "capture-status" });
  const banner = el("p", { id: "capture-banner" });
  banner.hidden = true;
  const details = el("details", { id: "capture-bookmarklet-box" });
  const link = el("a", { id: "capture-bookmarklet" });
  const copy = el("button", { id: "capture-copy" });
  const copyStatus = el("p", { id: "capture-copy-status" });
  const code = el("textarea", { id: "capture-code" });
  code.hidden = true;
  details.append(link, copy, copyStatus, code);
  box.append(input, go, status, banner, details);

  const form = el("form", { id: "problem-form" });
  const titleInput = el("input", { value: title });
  titleInput.setAttribute("name", "title");
  const select = el("select");
  select.setAttribute("name", "zone");
  for (const name of zones) select.append(el("option", { value: name }));
  select.value = zone ?? zones[0];
  const fold = el("section", { className: "form-fold" });
  const toggle = el("button", { className: "form-fold-toggle" });
  toggle.setAttribute("aria-expanded", "false");
  toggle.addEventListener("click", () => toggle.setAttribute("aria-expanded", "true"));
  const textarea = el("textarea", { value: thinking });
  textarea.setAttribute("name", "thinking");
  fold.append(toggle, textarea);
  form.append(titleInput, select, fold);
  page.append(box, form);
  doc.body.append(page);
  const changes = [];
  select.addEventListener("change", () => changes.push(select.value));
  const capture = env.window.Capture.mount(page);
  return { page, input, go, status, banner, link, copy, copyStatus, code, form, titleInput, select, textarea, toggle, changes, capture };
}

test("预填：只填空字段，分区只在默认值时改，思路最前面加链接行，焦点落在思路并播报", () => {
  const env = makeEnv();
  const ui = buildPage(env);
  ui.input.value = "https://leetcode.cn/problems/two-sum/?utm_source=x";
  ui.go.click();
  assert.equal(ui.titleInput.value, "LeetCode · Two Sum");
  assert.equal(ui.select.value, "算法");
  assert.deepEqual(ui.changes, ["算法"], "改分区会触发 change，应用才会切换字段模式");
  assert.equal(ui.textarea.value, "题目链接：https://leetcode.cn/problems/two-sum/\n");
  assert.equal(ui.toggle.getAttribute("aria-expanded"), "true", "折叠的思路区被展开");
  assert.equal(env.document.activeElement, ui.textarea);
  assert.equal(ui.status.textContent, "已从链接带入题名和链接，请补充你的思路");
  assert.equal(ui.banner.hidden, true);
});

test("预填：不覆盖用户已写的题名 / 分区 / 思路，不重复加链接", () => {
  const env = makeEnv();
  const ui = buildPage(env, { title: "我自己的题名", thinking: "我先想到了双指针", zone: "英语" });
  ui.input.value = "https://leetcode.cn/problems/two-sum/";
  ui.go.click();
  assert.equal(ui.titleInput.value, "我自己的题名");
  assert.equal(ui.select.value, "英语");
  assert.equal(ui.textarea.value, "我先想到了双指针");
  assert.deepEqual(ui.changes, []);

  const again = buildPage(makeEnv());
  again.input.value = "https://leetcode.cn/problems/two-sum/";
  again.go.click();
  again.go.click();
  assert.equal(again.textarea.value.match(/题目链接：/g).length, 1, "连点两次也只有一行链接");
});

test("预填：认不出的链接给出提示且不动表单；回车和粘贴也会触发识别", async () => {
  const env = makeEnv();
  const ui = buildPage(env);
  ui.input.value = "https://example.com/x";
  ui.go.click();
  assert.equal(ui.status.textContent, "没认出这个链接，可以直接手填");
  assert.equal(ui.titleInput.value, "");
  assert.equal(ui.textarea.value, "");

  ui.input.value = "https://www.luogu.com.cn/problem/P1001";
  ui.input.dispatchEvent(new env.context.Event("keydown", { props: { key: "Enter" } }));
  assert.equal(ui.titleInput.value, "洛谷 · P1001");

  const pasted = buildPage(makeEnv());
  pasted.input.value = "https://atcoder.jp/contests/abc001/tasks/abc001_a";
  pasted.input.dispatchEvent(new env.context.Event("paste"));
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(pasted.titleInput.value, "AtCoder · abc001_a");
});

test("书签预填：清洗后的网页标题优先，顶部提示，永不自动提交", () => {
  const env = makeEnv();
  const ui = buildPage(env);
  let submitted = 0;
  ui.form.addEventListener("submit", () => { submitted += 1; });
  const record = env.window.Capture.parseHash(hashFor("https://leetcode.cn/problems/two-sum/", "1. 两数之和 - 力扣（LeetCode）"));
  env.window.Capture.applyIntake(record);
  assert.equal(ui.titleInput.value, "1. 两数之和");
  assert.equal(ui.banner.hidden, false);
  assert.equal(ui.banner.textContent, "已从书签带入，请确认后保存");
  assert.equal(env.document.activeElement, ui.textarea);
  assert.equal(submitted, 0);
  ui.form.dispatchEvent(new env.context.Event("reset"));
  assert.equal(ui.banner.hidden, true, "表单重置 / 保存后提示消失");
});

test("书签区：点击只提示拖拽；复制走 clipboard，失败退回选中文字", async () => {
  const written = [];
  const env = makeEnv({ clipboard: { writeText: async (text) => { written.push(text); } } });
  const ui = buildPage(env);
  assert.ok(ui.link.getAttribute("href").startsWith("javascript:"));
  assert.ok(ui.link.getAttribute("href").includes(encodeURIComponent("x").length ? "window.open" : ""));
  const click = new env.context.CustomEvent("click", { bubbles: true });
  ui.link.dispatchEvent(click);
  assert.equal(click.defaultPrevented, true);
  assert.equal(ui.copyStatus.textContent, "请把它拖到书签栏，而不是点击");

  ui.copy.click();
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(written.length, 1);
  assert.ok(written[0].startsWith("javascript:(function(){window.open('"));
  assert.equal(ui.copyStatus.textContent, "书签代码已复制");

  const failing = makeEnv({ clipboard: { writeText: async () => { throw new Error("denied"); } } });
  const ui2 = buildPage(failing);
  ui2.copy.click();
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(ui2.code.hidden, false);
  assert.equal(failing.document.activeElement, ui2.code);
  assert.equal(ui2.code.selectionEnd, ui2.code.value.length);

  const none = buildPage(makeEnv());
  none.copy.click();
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.equal(none.code.hidden, false, "没有 navigator.clipboard 也不崩");
});

/* ---------------- 详情页链接渲染 ---------------- */
test("详情页：只把“题目链接：”行里的 http(s) 链接渲染成带 rel 的 <a>，其余是纯文本", () => {
  const { Capture } = makeEnv().window;
  const p = Capture.renderThinking("题目链接：https://leetcode.cn/problems/two-sum/\n我的思路 https://example.com/不会变链接\n<b>x</b>");
  assert.equal(p.className, "multiline");
  const links = p.querySelectorAll("a");
  assert.equal(links.length, 1);
  assert.equal(links[0].getAttribute("href"), "https://leetcode.cn/problems/two-sum/");
  assert.equal(links[0].getAttribute("rel"), "noopener noreferrer");
  assert.equal(links[0].getAttribute("target"), "_blank");
  assert.equal(links[0].textContent, "https://leetcode.cn/problems/two-sum/");
  assert.equal(p.textContent, "题目链接：https://leetcode.cn/problems/two-sum/\n我的思路 https://example.com/不会变链接\n<b>x</b>");
  assert.equal(p.querySelectorAll("b").length, 0);
});

test("详情页：javascript: / data: / 带账号密码的链接不会变成 <a>，句末标点不进 href", () => {
  const { Capture } = makeEnv().window;
  for (const text of [
    "题目链接：javascript:alert(1)",
    "题目链接：data:text/html,x",
    "题目链接：https://user:pw@evil.com/x",
    "题目链接：ftp://example.com/x",
    "没有前缀 https://example.com",
  ]) assert.equal(Capture.renderThinking(text).querySelectorAll("a").length, 0, text);
  const link = Capture.renderThinking("题目链接：https://example.com/a。").querySelectorAll("a")[0];
  assert.equal(link.getAttribute("href"), "https://example.com/a");
  assert.equal(Capture.renderThinking("").textContent, "");
  assert.equal(Capture.renderThinking(null).textContent, "");
});
