/* F1 题目导入前端模块（新增文件；只读 #problem-form 表单字段，不修改 HTML）。
*
* 建题表单（static/index.html #problem-form）字段：
* 题目名 input[name="title"]
* 分区 select#problem-zone
* 编程语言 input[name="language"]
* 当时的代码 textarea[name="code"]
* 当时的思路 textarea[name="thinking"]
* 表单没有难度/标签字段，预填里的 difficulty/tags/source_url 作为参考备注
* 写入「当时的思路」，用户可自行删改。
*/
(function () {
"use strict";

var MAX_IMAGE_BYTES = 5 * 1024 * 1024;

function form() {
return document.getElementById("problem-form");
}

function field(name) {
var f = form();
return f? f.querySelector('[name="' + name + '"]'): null;
}

var hooks = null;
var generation = 0;
function configure(options) { hooks = options; }
function reset() { generation += 1; }
function ticket() { return { generation, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id }; }
function alive(t) { return t.generation === generation && t.epoch === hooks.getEpoch() && t.userId === hooks.getUser()?.id; }
async function request(path, options) {
var t = ticket();
try { var data = await hooks.api(path, options); return alive(t) ? data : null; }
catch (error) { if (!alive(t)) return null; throw error; }
}
async function fetchPrefill(url) {
return request("/api/problems/fetch-from-url", { method: "POST", body: JSON.stringify({ url }) });
}
async function parseScreenshot(file) {
if (!file) throw new Error("请选择图片文件");
if (!/^image\//.test(file.type || "")) throw new Error("请上传图片文件");
if (file.size > MAX_IMAGE_BYTES) throw new Error("图片不能超过 5MB");
var data = new FormData(); data.append("image", file);
return request("/api/problems/parse-screenshot", { method: "POST", body: data });
}

// 把预填写入建题表单（不提交），用户确认后再点「保存这条记录」。
function fillProblemForm(prefill) {
prefill = prefill || {};
var titleInput = field("title");
if (titleInput && prefill.title) titleInput.value = prefill.title;

var notes = [];
if (prefill.difficulty) notes.push("难度：" + prefill.difficulty);
if (prefill.tags && prefill.tags.length) notes.push("标签：" + prefill.tags.join("、"));
if (prefill.source_url) notes.push("来源：" + prefill.source_url);

if (prefill.description) {
var codeInput = field("code");
// description 放进代码区只是占位：用户粘贴自己的代码后自行替换。
if (codeInput &&!codeInput.value.trim()) codeInput.value = prefill.description;
else if (codeInput) notes.unshift("题目描述见上方代码区（请替换为自己的代码）");
}
if (notes.length) {
var thinkingInput = field("thinking");
if (thinkingInput) {
var prefix = "" + notes.join("；") + "\n";
if (!thinkingInput.value.trim()) thinkingInput.value = prefix;
else thinkingInput.value = prefix + thinkingInput.value;
}
}
}

window.ImportProblem = {
configure, reset,
fetchPrefill: fetchPrefill,
parseScreenshot: parseScreenshot,
fillProblemForm: fillProblemForm,
};
})();
