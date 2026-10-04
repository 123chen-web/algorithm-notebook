"use strict";

/* 把 stdin 里的 JSON 用例数组交给 static/capture.js 的 Capture.parse，结果 JSON 写到 stdout。
   只给 tests/test_hot_problems.py 做“服务端解析与前端等价”的对照，不是 node --test 的测试文件。 */
const fs = require("node:fs");
const { load } = require("./js_harness.cjs");

const cases = JSON.parse(fs.readFileSync(0, "utf8"));
const env = load(["capture.js"], { extra: { URL, sessionStorage: undefined, location: { origin: "https://oy.example.com", pathname: "/", search: "", hash: "" }, history: {}, navigator: {} } });
process.stdout.write(JSON.stringify(cases.map((text) => env.window.Capture.parse(text))));
