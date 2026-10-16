"use strict";

/* 一道题的“变强轨迹”（static/growth-track.js）：复习记录按时间排好、算出每次定下的间隔、一句话总结。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load } = require("./js_harness.cjs");

const plain = (value) => JSON.parse(JSON.stringify(value));
const track = () => load(["growth-track.js"]).window.GrowthTrack;

// 复习时间用中午 12 点（UTC），不管跑测试的机器在哪个时区，本地日期都不变。
const REVIEWS = [
  { id: 3, quality: 4, reviewed_at: "2026-10-01T12:00:00+00:00", next_due_date: "2026-10-31" },
  { id: 1, quality: 2, reviewed_at: "2026-09-01T12:00:00+00:00", next_due_date: "2026-09-04" },
  { id: 2, quality: 3, reviewed_at: "2026-09-04T12:00:00+00:00", next_due_date: "2026-09-12" },
];

test("steps：按时间从早到晚排，评分换成文字和颜色档，间隔 = 下次日期 - 复习那天", () => {
  const steps = plain(track().steps(REVIEWS));
  assert.deepEqual(steps.map((step) => [step.label, step.tone, step.gap]), [
    ["答错了", "low", 3], ["很吃力", "mid", 8], ["记得", "good", 30],
  ]);
  assert.equal(steps[0].day, "2026-09-01");
  assert.deepEqual(plain(track().steps([{ quality: 4, reviewed_at: "2026-09-01T12:00:00+00:00", next_due_date: "坏日期" }]))[0].gap, null);
  assert.deepEqual(plain(track().steps(null)), []);
});

test("summary：两次以上说“从…到…、间隔从…拉长到…”；没变长就只说现在隔几天；一次只说复习过一次", () => {
  const api = track();
  assert.equal(api.summary(api.steps(REVIEWS)), "复习了 3 次，从「答错了」到「记得」，间隔从 3 天拉长到 30 天。");
  const flat = api.steps([
    { quality: 4, reviewed_at: "2026-09-01T12:00:00+00:00", next_due_date: "2026-09-06" },
    { quality: 4, reviewed_at: "2026-09-06T12:00:00+00:00", next_due_date: "2026-09-08" },
  ]);
  assert.equal(api.summary(flat), "复习了 2 次，现在隔 2 天复习一次。");
  assert.equal(api.summary(api.steps(REVIEWS.slice(1, 2))), "复习过 1 次：答错了，定在 3 天后再来。");
  assert.equal(api.summary([]), "");
});

test("render：没复习过不显示；有记录时每次一个点，最新的在最后", () => {
  const env = load(["growth-track.js"]);
  const api = env.window.GrowthTrack;
  assert.equal(api.render({ reviews: [] }), null);
  const box = api.render({ reviews: REVIEWS });
  env.document.body.append(box);
  const steps = box.querySelectorAll(".growth-step");
  assert.equal(steps.length, 3);
  assert.ok(steps[2].classList.contains("growth-good"));
  assert.match(steps[0].textContent, /第 1 次 · 答错了/);
  assert.match(steps[0].textContent, /隔 3 天/);
  assert.match(box.querySelector(".growth-track-summary").textContent, /拉长到 30 天/);
});
