"use strict";

/* 打卡墨点（static/inkdot.js）的行为测试：Node 内置测试运行器 + tests/js_harness.cjs 的假浏览器。
   覆盖：档位大小/浓淡随天数、中断淡化不归零、墨滴只播一次、里程碑印、空状态、aria。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

function setup() {
  const env = load(["inkdot.js"]);
  const mount = env.document.createElement("div");
  mount.id = "streak-ink";
  mount.setAttribute("role", "img");
  env.document.body.append(mount);
  return { env, mount };
}

const data = (over = {}) => ({
  streak_days: 0,
  last7: [false, false, false, false, false, false, false],
  today: "2026-10-12",
  ...over,
});
const q = (mount, selector) => mount.querySelector(selector);
const qa = (mount, selector) => mount.querySelectorAll(selector);
const coreR = (mount) => Number(q(mount, ".inkdot-core").getAttribute("r"));
const tierCore = (tier) => Number(([10, 20, 28, 36, 44][tier] * 0.66).toFixed(1));

test("inkdot: 21 天是第 3 档（r=28），墨团位置稳定", () => {
  const { env, mount } = setup();
  env.window.InkDot.render(data({ streak_days: 21, last7: [true, true, true, true, true, true, true] }), "u1");
  const svg = q(mount, "svg.inkdot-svg");
  assert.equal(svg.dataset.tier, "2");
  assert.equal(coreR(mount), tierCore(2));
  assert.equal(qa(mount, ".inkdot-edge").length, 4 + 2 * 3); // 档位越高毛边越多
  const first = q(mount, ".inkdot-blob").getAttribute("d");
  env.window.InkDot.render(data({ streak_days: 21, last7: [true, true, true, true, true, true, true] }), "u1");
  assert.equal(q(mount, ".inkdot-blob").getAttribute("d"), first, "同一天数画出的墨团不变");
  assert.equal(mount.getAttribute("aria-label"), "连续打卡墨点：连续 21 天，墨点第3阶");
});

test("inkdot: 档位随天数长大、毛边变多", () => {
  const { env, mount } = setup();
  env.window.InkDot.render(data({ streak_days: 3 }), "u2");
  assert.equal(coreR(mount), tierCore(1));
  assert.equal(qa(mount, ".inkdot-edge").length, 7);
  env.window.InkDot.render(data({ streak_days: 120 }), "u2");
  assert.equal(coreR(mount), tierCore(4));
  assert.equal(qa(mount, ".inkdot-edge").length, 16);
});

test("inkdot: 从没打过卡是空轮廓 + 邀请文案", () => {
  const { env, mount } = setup();
  env.window.InkDot.render(data({ streak_days: 0 }), "newbie");
  assert.ok(q(mount, ".inkdot-empty"), "空轮廓");
  assert.equal(q(mount, ".inkdot-blob"), null);
  assert.equal(q(mount, ".inkdot-caption").textContent, "今天完成一次复习，这里会落下第一滴墨");
  assert.equal(q(mount, ".inkdot-seal"), null, "没有里程碑印");
});

test("inkdot: 中断不归零——墨色变淡、缩小一档、文案温和", () => {
  const { env, mount } = setup();
  env.window.InkDot.render(data({ streak_days: 35, last7: [true, true, true, true, true, true, true] }), "u3");
  assert.equal(q(mount, "svg.inkdot-svg").dataset.tier, "3");
  env.window.InkDot.render(data({ streak_days: 0 }), "u3"); // 断了
  const svg = q(mount, "svg.inkdot-svg");
  assert.ok(svg.classList.contains("is-broken"));
  assert.equal(svg.dataset.tier, "2", "从 3 档缩到 2 档，而不是消失");
  assert.equal(q(mount, ".inkdot-caption").textContent, "墨还在，再点一下就回来了");
  assert.equal(mount.getAttribute("aria-label"), "连续打卡墨点：连续 0 天，已中断，墨色变淡");
});

test("inkdot: 今天第一次完成复习才落墨滴，且只播一次", () => {
  const { env, mount } = setup();
  const done = [true, true, true, true, true, true, true];
  const notYet = [true, true, true, true, true, true, false];
  env.window.InkDot.render(data({ streak_days: 4, last7: notYet }), "u4");
  assert.equal(q(mount, ".inkdot-drop-layer"), null, "今天还没复习，不落墨滴");
  env.window.InkDot.render(data({ streak_days: 5, last7: done }), "u4");
  const layer = q(mount, ".inkdot-drop-layer");
  assert.ok(layer, "第一次完成复习：墨滴落下");
  assert.equal(qa(mount, ".inkdot-ripple").length, 3, "三圈涟漪");
  assert.equal(env.window.localStorage.getItem("oy-inkdot:drop:u4:2026-10-12"), "1");
  env.window.InkDot.render(data({ streak_days: 5, last7: done }), "u4");
  assert.equal(q(mount, ".inkdot-drop-layer"), null, "同一天不再重播");
});

test("inkdot: 里程碑 7/30/100 天盖小印", () => {
  const { env, mount } = setup();
  env.window.InkDot.render(data({ streak_days: 6 }), "u5");
  assert.equal(q(mount, ".inkdot-seal"), null, "6 天没有印");
  env.window.InkDot.render(data({ streak_days: 7 }), "u5");
  assert.equal(q(mount, ".inkdot-seal").textContent, "七日");
  assert.equal(q(mount, ".inkdot-caption").textContent, "七日之印 · 墨色渐浓");
  env.window.InkDot.render(data({ streak_days: 100 }), "u5");
  assert.equal(q(mount, ".inkdot-seal").textContent, "百日");
  assert.equal(q(mount, ".inkdot-caption").textContent, "百日之印 · 墨点成海");
});

test("inkdot: reset 清空挂载点", () => {
  const { env, mount } = setup();
  env.window.InkDot.render(data({ streak_days: 10 }), "u6");
  assert.ok(q(mount, "svg"));
  env.window.InkDot.reset();
  assert.equal(q(mount, "svg"), null);
});
