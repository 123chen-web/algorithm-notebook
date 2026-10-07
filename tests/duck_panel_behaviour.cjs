"use strict";

/* “讲给小黄鸭听”面板的异步行为测试（Node 内置 node:test + tests/js_harness.cjs 的假浏览器）。
   重点覆盖字符串断言测不到的东西：迟到响应（登出换号 / 关闭面板 / 切换错题 / 重新开始）、
   输入法组合输入、发送中防重、12 轮封顶、429 额度锁定、错误重试不丢字。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick, FakeEvent } = require("./js_harness.cjs");

const unhandled = [];
process.on("unhandledRejection", (error) => unhandled.push(error));
test.afterEach(() => {
  assert.deepEqual(unhandled.splice(0).map(String), [], "no unhandled promise rejection");
});

// 脚本在独立的 vm 环境里跑，数组的原型和这里不是同一个：比较前先转成普通 JSON。
const plain = (value) => JSON.parse(JSON.stringify(value));
const MISTAKE = { id: 7, title: "二分查找：循环条件写错" };
const reply = (text, extra = {}) => ({ reply: text, turns_used: 1, ai_remaining: 9, ...extra });

/** 与 app.js 的 api() 行为一致：失败时抛出带 status / message 的 Error。 */
function makeApi(env) {
  return (path, options) => env.window.fetch(path, options).then(async (response) => {
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(String(data.detail || "请求失败，请稍后重试"));
      error.status = response.status;
      throw error;
    }
    return data;
  });
}

function setup({ mistake = MISTAKE, user = { id: 7 } } = {}) {
  const env = load(["duck-panel.js"]);
  const session = { epoch: 1, user };
  env.window.DuckPanel.configure({
    api: makeApi(env),
    getUser: () => session.user,
    getEpoch: () => session.epoch,
  });
  const container = env.document.createElement("div");
  env.document.body.append(container);
  const mounted = env.window.DuckPanel.mount(container, mistake);
  return { env, session, container, mounted };
}

const $ = (env, selector) => env.document.querySelector(selector);
const $$ = (env, selector) => env.document.querySelectorAll(selector);

function type(env, text) {
  const input = $(env, ".duck-input");
  input.value = text;
  input.dispatchEvent(new FakeEvent("input"));
  return input;
}
function pressEnter(env, props = {}) {
  $(env, ".duck-input").dispatchEvent(new FakeEvent("keydown", { props: { key: "Enter", ...props } }));
}
function bodyOf(call) {
  return JSON.parse(call.init.body);
}
/** 发一条并等到回复落盘，返回这一轮的气泡对。 */
async function exchange(env, text, replyText) {
  type(env, text);
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls.at(-1), 200, reply(replyText));
  await tick();
}

/* ---------------- 初始渲染 ---------------- */
test("mount 渲染面板：标题、只存内存的说明、错题名", () => {
  const { env, container, mounted } = setup();
  assert.equal(mounted, true);
  assert.equal($(env, ".duck-title").textContent, "讲给小黄鸭听");
  assert.match($(env, ".duck-note").textContent, /刷新或离开就没了/);
  assert.match($(env, ".duck-note").textContent, /二分查找/);
  assert.equal(container.querySelectorAll(".duck-panel").length, 1);
});

test("初始轮次提示是 第 1 / 12 轮", () => {
  const { env } = setup();
  assert.equal($(env, ".duck-round").textContent, "第 1 / 12 轮");
});

test("初始字数提示 还可输入 600 字，输入后减少", () => {
  const { env } = setup();
  assert.equal($(env, ".duck-count").textContent, "还可输入 600 字");
  type(env, "我觉得问题出在边界上");
  assert.equal($(env, ".duck-count").textContent, `还可输入 ${600 - 10} 字`);
});

test("输入框有 maxlength=600、aria-label 与占位提示", () => {
  const { env } = setup();
  const input = $(env, ".duck-input");
  assert.equal(input.maxLength, 600);
  assert.equal(input.getAttribute("aria-label"), "讲给小黄鸭听的内容");
});

test("空输入与纯空格都发不出去", async () => {
  const { env } = setup();
  assert.equal($(env, ".duck-send").disabled, true, "空输入时发送键禁用");
  type(env, "   ");
  assert.equal($(env, ".duck-send").disabled, true, "纯空格时发送键禁用");
  pressEnter(env);
  await tick();
  assert.equal(env.calls.length, 0, "Enter 也不会发出请求");
  assert.match($(env, ".duck-status").textContent, /先写点什么/);
});

/* ---------------- 键盘与输入法 ---------------- */
test("Enter 发送，请求 POST 到 /api/mistakes/{id}/duck", async () => {
  const { env } = setup();
  type(env, "我先讲一遍思路");
  pressEnter(env);
  await tick();
  assert.equal(env.calls.length, 1);
  assert.equal(env.calls[0].url, "/api/mistakes/7/duck");
  assert.equal(env.calls[0].init.method, "POST");
  assert.deepEqual(bodyOf(env.calls[0]), { turns: [{ role: "user", text: "我先讲一遍思路" }], finish: false });
});

test("Shift+Enter 是换行，不发送", async () => {
  const { env } = setup();
  type(env, "第一行");
  pressEnter(env, { shiftKey: true });
  await tick();
  assert.equal(env.calls.length, 0);
});

test("输入法组合输入期间（isComposing）Enter 不发送", async () => {
  const { env } = setup();
  type(env, "拼音候选中");
  pressEnter(env, { isComposing: true });
  await tick();
  assert.equal(env.calls.length, 0);
});

test("compositionstart 之后、compositionend 之前的 Enter 不发送，结束后恢复", async () => {
  const { env } = setup();
  const input = type(env, "组合输入");
  input.dispatchEvent(new FakeEvent("compositionstart"));
  pressEnter(env);
  await tick();
  assert.equal(env.calls.length, 0, "组合输入期间不发送");
  input.dispatchEvent(new FakeEvent("compositionend"));
  pressEnter(env);
  await tick();
  assert.equal(env.calls.length, 1, "组合输入结束后 Enter 恢复发送");
});

/* ---------------- 发送中与成功 ---------------- */
test("发送中：输入框和发送键禁用，状态显示 小黄鸭在想…，不会重复发请求", async () => {
  const { env } = setup();
  type(env, "进行中的一句");
  $(env, ".duck-send").click();
  await tick();
  assert.equal(env.calls.length, 1);
  assert.equal($(env, ".duck-input").disabled, true);
  assert.equal($(env, ".duck-send").disabled, true);
  assert.equal($(env, ".duck-status").textContent, "小黄鸭在想…");
  $(env, ".duck-send").click();
  pressEnter(env);
  await tick();
  assert.equal(env.calls.length, 1, "发送中不会再发第二次");
});

test("成功：出现 我 / 小黄鸭 两个气泡，文字原样，说话人有文字标注", async () => {
  const { env } = setup();
  await exchange(env, "我当时的想法", "你漏掉了哪个边界？");
  const bubbles = $$(env, ".duck-bubble");
  assert.equal(bubbles.length, 2);
  assert.equal(bubbles[0].querySelector(".duck-speaker").textContent, "我");
  assert.equal(bubbles[0].querySelector(".duck-text").textContent, "我当时的想法");
  assert.equal(bubbles[1].querySelector(".duck-speaker").textContent, "小黄鸭");
  assert.equal(bubbles[1].querySelector(".duck-text").textContent, "你漏掉了哪个边界？");
});

test("成功后 aria-live 区域播报小黄鸭的新回复", async () => {
  const { env } = setup();
  await exchange(env, "讲一句", "追问一句？");
  const status = $(env, ".duck-status");
  assert.equal(status.getAttribute("aria-live"), "polite");
  assert.equal(status.textContent, "小黄鸭：追问一句？");
});

test("成功后输入框清空、轮次前进到 第 2 / 12 轮", async () => {
  const { env } = setup();
  await exchange(env, "讲一句", "追问");
  assert.equal($(env, ".duck-input").value, "");
  assert.equal($(env, ".duck-round").textContent, "第 2 / 12 轮");
  assert.equal($(env, ".duck-send").disabled, true, "输入已清空，发送键回到禁用");
});

test("第二轮请求带上此前完整 turns：user 与 duck 严格交替、以 user 开头", async () => {
  const { env } = setup();
  await exchange(env, "第一句", "第一答");
  type(env, "第二句");
  $(env, ".duck-send").click();
  await tick();
  assert.deepEqual(bodyOf(env.calls.at(-1)).turns, [
    { role: "user", text: "第一句" },
    { role: "duck", text: "第一答" },
    { role: "user", text: "第二句" },
  ]);
});

test("用户文字里的标记字符按原文显示，不被当成 HTML", async () => {
  const { env } = setup();
  await exchange(env, "<b>left <= right</b>", "好问题");
  const bubble = $(env, ".duck-user .duck-text");
  assert.equal(bubble.textContent, "<b>left <= right</b>");
  assert.equal(bubble.querySelector("b"), null, "没有真的产生 <b> 元素");
});

test("服务端回复里的标记字符同样原样显示", async () => {
  const { env } = setup();
  await exchange(env, "讲", "是不是 <i>边界</i> 问题？");
  const bubble = $(env, ".duck-duck .duck-text");
  assert.equal(bubble.textContent, "是不是 <i>边界</i> 问题？");
  assert.equal(bubble.querySelector("i"), null);
});

test("超过 600 字的输入按 600 字截断后发送", async () => {
  const { env } = setup();
  type(env, "长".repeat(700));
  $(env, ".duck-send").click();
  await tick();
  assert.equal(bodyOf(env.calls[0]).turns[0].text.length, 600);
});

/* ---------------- 错误与重试 ---------------- */
test("502：显示没答好可重试，输入框内容不丢", async () => {
  const { env } = setup();
  type(env, "这句话不能丢");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[0], 502, { detail: "bad gateway" });
  await tick();
  assert.equal($(env, ".duck-error-text").textContent, "小黄鸭这次没答好，可以再试一次。");
  assert.equal($(env, ".duck-retry").hidden, false);
  assert.equal($(env, ".duck-input").value, "这句话不能丢");
  assert.equal($$(env, ".duck-bubble").length, 0, "失败的回合不进对话");
});

test("点重试用原内容再发一次，成功后气泡补上", async () => {
  const { env } = setup();
  type(env, "原内容");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[0], 502, {});
  await tick();
  $(env, ".duck-retry").click();
  await tick();
  assert.equal(env.calls.length, 2);
  assert.deepEqual(bodyOf(env.calls[1]).turns, [{ role: "user", text: "原内容" }]);
  env.respond(env.calls[1], 200, reply("这次答好了？"));
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 2);
  assert.equal($(env, ".duck-error").hidden, true, "成功后错误条收起");
});

test("502 之后可以直接编辑再发，不必点重试", async () => {
  const { env } = setup();
  type(env, "第一版");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[0], 502, {});
  await tick();
  type(env, "改过的第二版");
  $(env, ".duck-send").click();
  await tick();
  assert.equal(bodyOf(env.calls[1]).turns[0].text, "改过的第二版");
});

test("429：显示额度用完说明，输入与发送、总结都禁用，不给重试", async () => {
  const { env } = setup();
  type(env, "还想讲");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[0], 429, { detail: "今日 AI 额度已用完" });
  await tick();
  assert.match($(env, ".duck-error-text").textContent, /额度用完/);
  assert.equal($(env, ".duck-retry").hidden, true);
  assert.equal($(env, ".duck-input").disabled, true);
  assert.equal($(env, ".duck-send").disabled, true);
  assert.equal($(env, ".duck-finish").disabled, true);
  assert.equal($(env, ".duck-restart").disabled, false, "重新开始始终可用");
});

test("429 之后重新开始：对话清空，但额度锁定保留", async () => {
  const { env } = setup();
  await exchange(env, "讲过一句", "答一句");
  type(env, "再来");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[1], 429, { detail: "额度" });
  await tick();
  $(env, ".duck-restart").click();
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 0);
  assert.equal($(env, ".duck-round").textContent, "第 1 / 12 轮");
  assert.equal($(env, ".duck-input").disabled, true, "额度是服务端按天算的，清空对话不恢复");
  type(env, "再试");
  $(env, ".duck-send").click();
  await tick();
  assert.equal(env.calls.length, 2, "锁定期间不再发请求");
});

test("503：显示服务端给的说明，可重试", async () => {
  const { env } = setup();
  type(env, "讲");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[0], 503, { detail: "AI 未配置" });
  await tick();
  assert.equal($(env, ".duck-error-text").textContent, "AI 未配置");
  assert.equal($(env, ".duck-retry").hidden, false);
  assert.equal($(env, ".duck-input").disabled, false, "503 不锁面板");
});

test("422：显示参数错误说明，可重试，内容不丢", async () => {
  const { env } = setup();
  type(env, "内容还在");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[0], 422, { detail: "turns 不合法" });
  await tick();
  assert.equal($(env, ".duck-error-text").textContent, "turns 不合法");
  assert.equal($(env, ".duck-retry").hidden, false);
  assert.equal($(env, ".duck-input").value, "内容还在");
});

test("回复是空字符串：按 502 处理，可重试且不加气泡", async () => {
  const { env } = setup();
  type(env, "讲");
  $(env, ".duck-send").click();
  await tick();
  env.respond(env.calls[0], 200, reply("   "));
  await tick();
  assert.equal($(env, ".duck-error-text").textContent, "小黄鸭这次没答好，可以再试一次。");
  assert.equal($$(env, ".duck-bubble").length, 0);
});

test("网络异常（reject）也走可重试路径", async () => {
  const { env } = setup();
  type(env, "讲");
  $(env, ".duck-send").click();
  await tick();
  env.calls[0].reject(new Error("network down"));
  await tick();
  assert.equal($(env, ".duck-error").hidden, false);
  assert.equal($(env, ".duck-retry").hidden, false);
  assert.equal($(env, ".duck-send").disabled, false, "失败后输入框里还有字，可以再发");
});

/* ---------------- 12 轮封顶与总结 ---------------- */
async function fillRounds(env, count) {
  for (let index = 0; index < count; index += 1) {
    await exchange(env, `第${index + 1}句`, `第${index + 1}答`);
  }
}

test("seven rounds remain usable within the twelve-round limit", async () => {
  const { env } = setup();
  await fillRounds(env, 7);
  assert.equal($(env, ".duck-round").textContent, "第 8 / 12 轮");
  assert.equal($(env, ".duck-input").disabled, false);
  type(env, "第八句");
  assert.equal($(env, ".duck-send").disabled, false);
  assert.equal($$(env, ".duck-bubble").length, 14);
});

test("讲满 12 轮后：输入与发送禁用，只能点 结束并总结，轮次定格 第 12 / 12 轮", async () => {
  const { env } = setup();
  await fillRounds(env, 12);
  assert.match($(env, ".duck-round").textContent, /^第 12 \/ 12 轮/);
  assert.equal($(env, ".duck-input").disabled, true);
  assert.equal($(env, ".duck-send").disabled, true);
  assert.equal($(env, ".duck-finish").disabled, false);
  assert.equal($$(env, ".duck-bubble").length, 24);
});

test("没讲过一轮时 结束并总结 不可用", () => {
  const { env } = setup();
  assert.equal($(env, ".duck-finish").disabled, true);
});

test("结束并总结：请求 finish=true、turns 是完整对话，回复进独立总结区块", async () => {
  const { env } = setup();
  await fillRounds(env, 2);
  $(env, ".duck-finish").click();
  await tick();
  const body = bodyOf(env.calls.at(-1));
  assert.equal(body.finish, true);
  assert.equal(body.turns.length, 4, "总结请求带上全部 2 轮对话");
  assert.equal(body.turns.at(-1).role, "duck", "总结请求没有伪造一条用户发言");
  assert.equal($(env, ".duck-status").textContent, "小黄鸭在整理总结…");
  env.respond(env.calls.at(-1), 200, reply("你讲清了循环不变量，边界还没说清。"));
  await tick();
  const summary = $(env, ".duck-summary");
  assert.equal(summary.hidden, false);
  assert.equal($(env, ".duck-summary-text").textContent, "你讲清了循环不变量，边界还没说清。");
  assert.match($(env, ".duck-status").textContent, /总结/);
  assert.equal($$(env, ".duck-bubble").length, 4, "对话区原样保留");
});

test("总结之后：输入、发送、总结禁用，重新开始可用", async () => {
  const { env } = setup();
  await fillRounds(env, 1);
  $(env, ".duck-finish").click();
  await tick();
  env.respond(env.calls.at(-1), 200, reply("总结语"));
  await tick();
  assert.equal($(env, ".duck-input").disabled, true);
  assert.equal($(env, ".duck-send").disabled, true);
  assert.equal($(env, ".duck-finish").disabled, true);
  assert.equal($(env, ".duck-restart").disabled, false);
});

test("总结失败（502）：对话不丢，可重试总结", async () => {
  const { env } = setup();
  await fillRounds(env, 1);
  $(env, ".duck-finish").click();
  await tick();
  env.respond(env.calls.at(-1), 502, {});
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 2, "对话还在");
  assert.equal($(env, ".duck-summary").hidden, true);
  assert.equal($(env, ".duck-retry").hidden, false);
  $(env, ".duck-retry").click();
  await tick();
  assert.equal(bodyOf(env.calls.at(-1)).finish, true, "重试仍然发 finish");
  env.respond(env.calls.at(-1), 200, reply("总结来了"));
  await tick();
  assert.equal($(env, ".duck-summary").hidden, false);
});

test("总结时遇到 429：显示额度说明并锁定", async () => {
  const { env } = setup();
  await fillRounds(env, 1);
  $(env, ".duck-finish").click();
  await tick();
  env.respond(env.calls.at(-1), 429, { detail: "额度" });
  await tick();
  assert.match($(env, ".duck-error-text").textContent, /额度用完/);
  assert.equal($(env, ".duck-finish").disabled, true);
});

/* ---------------- 重新开始 ---------------- */
test("重新开始：清空气泡与总结、轮次回 第 1 / 12 轮、输入恢复", async () => {
  const { env } = setup();
  await fillRounds(env, 2);
  $(env, ".duck-restart").click();
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 0);
  assert.equal($(env, ".duck-summary").hidden, true);
  assert.equal($(env, ".duck-round").textContent, "第 1 / 12 轮");
  assert.equal($(env, ".duck-input").disabled, false);
  type(env, "重新开始的一句");
  $(env, ".duck-send").click();
  await tick();
  assert.deepEqual(bodyOf(env.calls.at(-1)).turns, [{ role: "user", text: "重新开始的一句" }],
    "新对话不携带旧 turns");
});

test("发送中点重新开始：在飞请求的迟到响应被丢弃，界面已复位", async () => {
  const { env } = setup();
  type(env, "会被丢掉的一句");
  $(env, ".duck-send").click();
  await tick();
  $(env, ".duck-restart").click();
  await tick();
  env.respond(env.calls[0], 200, reply("迟到的回答"));
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 0, "迟到响应不写入");
  assert.equal($(env, ".duck-input").disabled, false, "发送中标记已复位");
  type(env, "新的");
  $(env, ".duck-send").click();
  await tick();
  assert.equal(env.calls.length, 2, "面板可以继续正常发");
});

/* ---------------- 迟到响应守卫 ---------------- */
test("unmount 之后才回来的响应被丢弃", async () => {
  const { env, container } = setup();
  type(env, "讲");
  $(env, ".duck-send").click();
  await tick();
  env.window.DuckPanel.unmount();
  env.respond(env.calls[0], 200, reply("迟到的"));
  await tick();
  assert.equal(container.querySelector(".duck-panel"), null, "面板已摘掉");
  assert.equal(env.document.querySelector(".duck-log"), null);
});

test("reset 之后才回来的响应被丢弃（登出场景）", async () => {
  const { env } = setup();
  type(env, "讲");
  $(env, ".duck-send").click();
  await tick();
  env.window.DuckPanel.reset();
  env.respond(env.calls[0], 200, reply("上一个账号的回复"));
  await tick();
  assert.equal(env.document.querySelector(".duck-panel"), null);
});

test("登出再登录别的账号：旧账号的迟到响应不会写进新账号的面板", async () => {
  const { env, session, container } = setup();
  type(env, "A 的错题讲解");
  $(env, ".duck-send").click();
  await tick();

  session.epoch += 1; // 登出
  session.user = { id: 8 }; // 换成 B
  env.window.DuckPanel.reset();
  env.window.DuckPanel.mount(container, { id: 9, title: "B 的错题" });
  env.respond(env.calls[0], 200, reply("给 A 的回答"));
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 0, "A 的回复进不了 B 的面板");

  type(env, "B 自己讲");
  $(env, ".duck-send").click();
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/mistakes/9/duck");
  env.respond(env.calls.at(-1), 200, reply("给 B 的回答"));
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 2);
  assert.match($(env, ".duck-log").textContent, /给 B 的回答/);
  assert.equal($(env, ".duck-log").textContent.includes("给 A 的回答"), false);
});

test("切换错题之后才回来的响应被丢弃，新错题从第 1 轮开始", async () => {
  const { env, container } = setup();
  type(env, "给旧题讲");
  $(env, ".duck-send").click();
  await tick();
  env.window.DuckPanel.mount(container, { id: 42, title: "另一道题" });
  env.respond(env.calls[0], 200, reply("旧题的迟到回答"));
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 0);
  assert.equal($(env, ".duck-round").textContent, "第 1 / 12 轮");
  type(env, "给新题讲");
  $(env, ".duck-send").click();
  await tick();
  assert.equal(env.calls.at(-1).url, "/api/mistakes/42/duck");
});

test("同一个账号登出再登录（只有 epoch 变了）：迟到响应同样丢弃", async () => {
  const { env, session } = setup();
  type(env, "旧会话的一句");
  $(env, ".duck-send").click();
  await tick();
  session.epoch += 1; // 重新登录同一账号，宿主还没重置面板
  env.respond(env.calls[0], 200, reply("旧会话的迟到回答"));
  await tick();
  assert.equal($$(env, ".duck-bubble").length, 0, "epoch 变了，迟到响应不能写入");
});

test("重复挂载同一个容器只留一个面板", () => {
  const { env, container } = setup();
  env.window.DuckPanel.mount(container, MISTAKE);
  env.window.DuckPanel.mount(container, MISTAKE);
  assert.equal(container.querySelectorAll(".duck-panel").length, 1);
});

test("mount 缺少错题或容器时返回 false，不产生面板", () => {
  const env = load(["duck-panel.js"]);
  env.window.DuckPanel.configure({ api: makeApi(env), getUser: () => ({ id: 1 }), getEpoch: () => 1 });
  const container = env.document.createElement("div");
  env.document.body.append(container);
  assert.equal(env.window.DuckPanel.mount(container, null), false);
  assert.equal(env.window.DuckPanel.mount(null, MISTAKE), false);
  assert.equal(container.querySelector(".duck-panel"), null);
});

test("未登录时点发送：提示先登录，不发请求", async () => {
  const { env, session } = setup();
  session.user = null;
  type(env, "讲");
  $(env, ".duck-send").click();
  await tick();
  assert.equal(env.calls.length, 0);
  assert.match($(env, ".duck-error-text").textContent, /请先登录/);
});

/* ---------------- 纯函数 ---------------- */
test("helpers.roundText：从 1 数到 12 并定格", () => {
  const { roundText } = load(["duck-panel.js"]).window.DuckPanel.helpers;
  assert.equal(roundText([]), "第 1 / 12 轮");
  assert.equal(roundText([{ role: "user", text: "u" }, { role: "duck", text: "d" }]), "第 2 / 12 轮");
  const full = [];
  for (let index = 0; index < 12; index += 1) full.push({ role: "user", text: "u" }, { role: "duck", text: "d" });
  assert.match(roundText(full), /^第 12 \/ 12 轮/);
});

test("helpers.payloadTurns：复制历史并追加当前发言，不改原数组", () => {
  const { payloadTurns } = load(["duck-panel.js"]).window.DuckPanel.helpers;
  const turns = [{ role: "user", text: "a" }, { role: "duck", text: "b" }];
  const sent = payloadTurns(turns, "c");
  assert.deepEqual(plain(sent), [{ role: "user", text: "a" }, { role: "duck", text: "b" }, { role: "user", text: "c" }]);
  assert.equal(turns.length, 2, "原数组没有被改动");
  assert.deepEqual(plain(payloadTurns(turns, "")), plain(turns), "总结请求不追加发言");
});

test("helpers.failureInfo：429 锁额度、502 可重试、未知错误给通用文案", () => {
  const { failureInfo } = load(["duck-panel.js"]).window.DuckPanel.helpers;
  assert.deepEqual(plain(failureInfo({ status: 429, message: "x" })),
    { text: "今天的 AI 额度用完了，明天再来找小黄鸭讲吧。", quota: true, retry: false });
  assert.equal(failureInfo({ status: 502 }).retry, true);
  assert.equal(failureInfo({ status: 503, message: "" }).text.length > 0, true);
  assert.equal(failureInfo({ status: 500, message: "服务错误" }).text, "服务错误");
  assert.equal(failureInfo(null).retry, true);
});
