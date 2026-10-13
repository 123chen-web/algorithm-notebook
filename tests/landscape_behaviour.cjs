"use strict";

/* 总览页「成长山水」（static/landscape.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   覆盖字符串断言测不到的东西：哈希稳定性、阶段阈值、新元素只播一次动画、
   晚到响应丢弃、接口失败占位、键盘可聚焦与 aria-label、减少动画。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

const CATALOG = Array.from({ length: 150 }, (_, index) => ({ id: `p${index}`, title: `套路${index}` }));

function setup({ user = { id: 7 }, epoch = 1, extra = {} } = {}) {
  const env = load(["landscape.js"], { extra });
  const state = { user, epoch };
  const hooks = { getUser: () => state.user, getEpoch: () => state.epoch };
  // 假 fetch 的响应对象只有 json()；api() 里失败要抛错，这里照着宿主 app.js 的样子包一层。
  hooks.api = async (path, options = {}) => {
    const response = await env.window.fetch(path, options);
    const data = await response.json();
    if (!response.ok) {
      const error = new Error(data.detail || "请求失败，请稍后重试");
      error.status = response.status;
      throw error;
    }
    return data;
  };
  env.window.Landscape.configure(hooks);
  const container = env.document.createElement("div");
  env.document.body.append(container);
  return { env, state, container };
}

const q = (container, selector) => container.querySelector(selector);
const qa = (container, selector) => container.querySelectorAll(selector);

/** mount 并按顺序回应 /api/roadmap 与套路清单两个请求（清单只在首次请求）。 */
async function mountWith(ctx, progress, { roadmapStatus = 200, catalogStatus = 200 } = {}) {
  const start = ctx.env.calls.length;
  const promise = ctx.env.window.Landscape.mount(ctx.container);
  await tick();
  const fresh = ctx.env.calls.slice(start);
  const roadmapCall = fresh.find((call) => call.url === "/api/roadmap");
  const catalogCall = fresh.find((call) => call.url !== "/api/roadmap");
  assert.ok(roadmapCall, "发起了 /api/roadmap 请求");
  ctx.env.respond(roadmapCall, roadmapStatus, { progress });
  if (catalogCall) ctx.env.respond(catalogCall, catalogStatus, CATALOG);
  await promise;
  await tick();
  await tick();
}

const mastered = (ids) => Object.fromEntries(ids.map((id) => [id, "mastered"]));

test("空状态：只有一座淡淡的远山和那句引导文案", async () => {
  const ctx = setup();
  await mountWith(ctx, {});
  assert.equal(qa(ctx.container, ".ls-el").length, 0);
  assert.ok(q(ctx.container, ".ls-m1"), "淡淡的远山一直在");
  assert.equal(q(ctx.container, ".ls-m2"), null);
  assert.equal(q(ctx.container, ".ls-sun"), null);
  assert.match(q(ctx.container, ".ls-caption").textContent, /学会第一个套路/);
});

test("哈希稳定：同一个套路两次挂载落到同一个槽位", async () => {
  const ctx = setup();
  const progress = mastered(["p3", "p42", "p137"]);
  await mountWith(ctx, progress);
  const first = new Map(
    qa(ctx.container, ".ls-el").map((el) => [el.getAttribute("data-slot"), el.getAttribute("aria-label")]),
  );
  ctx.env.window.Landscape.reset();
  const second = setup();
  // 同一个 env 才能复用 localStorage；这里只验证槽位映射稳定，用新 env 也行。
  await mountWith(second, progress);
  const again = new Map(
    qa(second.container, ".ls-el").map((el) => [el.getAttribute("data-slot"), el.getAttribute("aria-label")]),
  );
  assert.deepEqual([...again.entries()], [...first.entries()]);
});

test("阶段阈值：10/40 远山添层，135 迎来日出", async () => {
  const ctx = setup();
  const ids = (n) => Array.from({ length: n }, (_, i) => `p${i}`);
  await mountWith(ctx, mastered(ids(9)));
  assert.equal(q(ctx.container, ".ls-m2"), null);
  await mountWith(ctx, mastered(ids(10)));
  assert.ok(q(ctx.container, ".ls-m2"), "10 个：远山多一层");
  assert.equal(q(ctx.container, ".ls-m3"), null);
  assert.match(q(ctx.container, ".ls-caption").textContent, /已掌握 10 \/ 150 · 再学 30 个，远山再添一层/);
  await mountWith(ctx, mastered(ids(40)));
  assert.ok(q(ctx.container, ".ls-m3"), "40 个：远山再添一层");
  assert.equal(q(ctx.container, ".ls-sun"), null);
  await mountWith(ctx, mastered(ids(135)));
  assert.ok(q(ctx.container, ".ls-sun"), "135 个：日出");
  assert.match(q(ctx.container, ".ls-caption").textContent, /山水已满/);
});

test("新元素动画只播一次：第二次挂载不再长", async () => {
  const ctx = setup();
  await mountWith(ctx, mastered(["p3"]));
  assert.equal(qa(ctx.container, ".ls-el.ls-grow").length, 1, "第一次：播墨晕开");
  ctx.env.window.Landscape.reset();
  const start = ctx.env.calls.length;
  const promise = ctx.env.window.Landscape.mount(ctx.container);
  await tick();
  const fresh = ctx.env.calls.slice(start);
  const roadmapCall = fresh.find((call) => call.url === "/api/roadmap");
  ctx.env.respond(roadmapCall, 200, { progress: mastered(["p3"]) });
  await promise;
  await tick();
  await tick();
  assert.equal(qa(ctx.container, ".ls-el").length, 1, "元素还在");
  assert.equal(qa(ctx.container, ".ls-el.ls-grow").length, 0, "第二次：不再播");
});

test("晚到响应丢弃：reset 之后回来的数据不渲染", async () => {
  const ctx = setup();
  const promise = ctx.env.window.Landscape.mount(ctx.container);
  await tick();
  ctx.env.window.Landscape.reset();
  const [roadmapCall, catalogCall] = ctx.env.calls;
  ctx.env.respond(roadmapCall, 200, { progress: mastered(["p3"]) });
  if (catalogCall) ctx.env.respond(catalogCall, 200, CATALOG);
  await promise;
  await tick();
  assert.equal(ctx.container.childElementCount, 0);
});

test("接口失败：不阻塞，留一句温和的话", async () => {
  const ctx = setup();
  await mountWith(ctx, {}, { roadmapStatus: 500 });
  assert.ok(q(ctx.container, ".ls-m1"), "占位山水还在");
  assert.match(q(ctx.container, ".ls-caption").textContent, /暂时读不到学习进度/);
});

test("无障碍：生长元素可聚焦，aria-label 是一句批注", async () => {
  const ctx = setup();
  await mountWith(ctx, mastered(["p3"]));
  const el = q(ctx.container, ".ls-el");
  assert.equal(el.getAttribute("tabindex"), "0");
  assert.match(el.getAttribute("aria-label"), /因为你掌握了「套路3」/);
  assert.equal(el.getAttribute("role"), "img");
});

test("常驻微动画载体：有掌握时雾、飞鸟、水波都在（只动 transform/opacity）", async () => {
  const ctx = setup();
  await mountWith(ctx, mastered(["p3", "p42"]));
  assert.ok(q(ctx.container, ".ls-mist"), "薄雾在");
  assert.ok(q(ctx.container, ".ls-fly"), "偶尔掠过的飞鸟在");
  assert.ok(q(ctx.container, ".ls-water"), "水波在");
  assert.equal(q(ctx.container, ".ls-fly").getAttribute("aria-hidden"), "true", "装饰元素对读屏隐藏");
});

test("减少动画：新元素也不播生长动画", async () => {
  const ctx = setup({
    extra: { matchMedia: () => ({ matches: true, addEventListener() {} }) },
  });
  await mountWith(ctx, mastered(["p3"]));
  assert.equal(qa(ctx.container, ".ls-el").length, 1, "元素照常出现");
  assert.equal(qa(ctx.container, ".ls-el.ls-grow").length, 0, "但不播动画");
});
