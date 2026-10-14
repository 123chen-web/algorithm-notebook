/* 内容脚本 v2：Shadow DOM 隔离的浮层按钮 + 极简表单。
* - 所有样式封在 closed shadow root 里，不污染原站；按钮可拖到屏幕边缘收起。
* - 用户可控文本一律用 textContent，不用 innerHTML 拼接。
* - 网络请求只走 background service worker；内容脚本只做提取与展示。
* - 读取页面发生在用户点击按钮之后（隐私要求）。
*/
"use strict";

(function () {
const HOST_ID = "ouye-v2-host";
let shadow = null;
let ui = null; // {btn, tab, panel, toast}
let collapsed = false;
let currentExtract = null;

const T = (k) => globalThis.OY_T(k);

function h(tag, attrs, text) {
const e = document.createElement(tag);
if (attrs) {
for (const k of Object.keys(attrs)) {
if (k === "style") e.style.cssText = attrs[k];
else if (k.startsWith("on") && typeof attrs[k] === "function")
e.addEventListener(k.slice(2), attrs[k]);
else e.setAttribute(k, attrs[k]);
}
}
if (text!== undefined && text!== null) e.textContent = text;
return e;
}

const CSS = `
:host { all: initial;}
.oy-btn {
position: fixed; right: 18px; bottom: 84px; z-index: 2147483647;
font: 14px/1.4 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
background: #c0392b; color: #fff; border: none; border-radius: 22px;
padding: 10px 18px; cursor: grab; user-select: none;
box-shadow: 0 2px 14px rgba(0,0,0,.28); white-space: nowrap;
}
.oy-btn:active { cursor: grabbing;}
.oy-btn.dragging { opacity:.85;}
.oy-tab {
position: fixed; z-index: 2147483647; top: 40%;
font: 13px/1.4 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
background: #c0392b; color: #fff; border: none; cursor: pointer;
padding: 12px 6px; writing-mode: vertical-rl; letter-spacing: 2px;
box-shadow: 0 2px 14px rgba(0,0,0,.28); user-select: none;
}
.oy-tab.left { left: 0; border-radius: 0 10px 10px 0;}
.oy-tab.right { right: 0; border-radius: 10px 0 0 10px;}
.oy-panel {
position: fixed; right: 18px; bottom: 84px; z-index: 2147483648;
width: 340px; max-width: calc(100vw - 36px);
font: 14px/1.5 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
background: #fff; color: #222; border-radius: 12px;
box-shadow: 0 8px 32px rgba(0,0,0,.3); padding: 16px; display: none;
}
.oy-panel.open { display: block;}
.oy-panel h3 { margin: 0 0 10px; font-size: 15px;}
.oy-panel label { display: block; font-size: 12px; color: #666; margin: 8px 0 4px;}
.oy-panel input[type=text],.oy-panel textarea {
width: 100%; box-sizing: border-box; font: inherit; font-size: 13px;
border: 1px solid #ddd; border-radius: 8px; padding: 8px; resize: vertical;
background: #fff; color: #222;
}
.oy-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 6px;}
.oy-chip {
font-size: 12px; border: 1px solid #e0c9c4; background: #fdf3f1; color: #a93226;
border-radius: 14px; padding: 3px 10px; cursor: pointer;
}
.oy-row { display: flex; gap: 8px; margin-top: 12px; justify-content: flex-end;}
.oy-primary {
background: #c0392b; color: #fff; border: none; border-radius: 8px;
padding: 8px 18px; font-size: 14px; cursor: pointer;
}
.oy-primary:disabled { background: #bbb; cursor: wait;}
.oy-ghost {
background: #f4f4f4; color: #333; border: none; border-radius: 8px;
padding: 8px 14px; font-size: 14px; cursor: pointer;
}
.oy-note { font-size: 12px; color: #888; margin-top: 8px;}
.oy-err { font-size: 12px; color: #c0392b; margin-top: 8px;}
.oy-toast {
position: fixed; left: 50%; bottom: 36px; transform: translateX(-50%);
z-index: 2147483649; background: rgba(20,20,20,.88); color: #fff;
font: 14px/1.5 -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
padding: 10px 20px; border-radius: 8px; max-width: 82vw; text-align: center;
display: none;
}
.oy-toast.show { display: block;}
.oy-toast a { color: #ffd27d;}
.oy-src { font-size: 12px; color: #888; margin-top: 2px;}
@media (prefers-color-scheme: dark) {
.oy-panel { background: #1e1e1e; color: #e8e8e8;}
.oy-panel label { color: #aaa;}
.oy-panel input[type=text],.oy-panel textarea {
background: #2a2a2a; color: #e8e8e8; border-color: #444;
}
.oy-ghost { background: #333; color: #e8e8e8;}
.oy-note { color: #999;}
.oy-chip { background: #3a2422; border-color: #6b3a35; color: #f0b9b2;}
}
`;

function ensureUI() {
if (ui) return ui;
const host = document.createElement("div");
host.id = HOST_ID;
document.documentElement.appendChild(host);
shadow = host.attachShadow({ mode: "closed"});
const style = document.createElement("style");
style.textContent = CSS;
shadow.appendChild(style);

const btn = h("button", { class: "oy-btn", title: T("collapseTitle")}, T("floatBtn"));
const tab = h(
"button",
{ class: "oy-tab right", style: "display:none", title: T("floatBtnTitle")},
T("floatBtn")
);
const panel = h("div", { class: "oy-panel", role: "dialog", "aria-label": T("formTitle")});
const toast = h("div", { class: "oy-toast"});
shadow.append(btn, tab, panel, toast);
ui = { btn, tab, panel, toast, host};
wireDrag();
return ui;
}

/* ---- 拖拽：移动阈值内算点击；松手时贴近左右边缘则收起 ---- */
function wireDrag() {
const { btn, tab} = ui;
let sx = 0, sy = 0, ox = 0, oy = 0, dragging = false, moved = 0;

btn.addEventListener("pointerdown", (e) => {
dragging = true;
moved = 0;
sx = e.clientX;
sy = e.clientY;
const r = btn.getBoundingClientRect();
ox = r.left;
oy = r.top;
btn.classList.add("dragging");
btn.setPointerCapture(e.pointerId);
});
btn.addEventListener("pointermove", (e) => {
if (!dragging) return;
const dx = e.clientX - sx, dy = e.clientY - sy;
moved = Math.max(moved, Math.abs(dx) + Math.abs(dy));
if (moved > 6) {
btn.style.left = Math.min(Math.max(ox + dx, 0), window.innerWidth - 60) + "px";
btn.style.top = Math.min(Math.max(oy + dy, 0), window.innerHeight - 40) + "px";
btn.style.right = "auto";
btn.style.bottom = "auto";
}
});
btn.addEventListener("pointerup", (e) => {
dragging = false;
btn.classList.remove("dragging");
if (moved <= 6) {
openForm();
return;
}
const r = btn.getBoundingClientRect();
const cx = r.left + r.width / 2;
if (cx < 90 || cx > window.innerWidth - 90) {
setCollapsed(true, cx < window.innerWidth / 2? "left": "right");
} else {
btn.style.left = "";
btn.style.top = "";
btn.style.right = "18px";
btn.style.bottom = "84px";
}
});
tab.addEventListener("click", () => setCollapsed(false));
}

function setCollapsed(on, side) {
collapsed = on;
const { btn, tab} = ensureUI();
btn.style.display = on? "none": "";
tab.style.display = on? "": "none";
tab.className = "oy-tab " + (side === "left"? "left": "right");
try {
chrome.storage.local.set({ ouyeBtnCollapsed: on, ouyeBtnSide: side || "right"});
} catch (e) {}
}

/* ---- toast（纯文本，不拼接 HTML） ---- */
let toastTimer = 0;
function toast(text, ms) {
const { toast: el} = ensureUI();
el.textContent = "";
el.appendChild(document.createTextNode(text));
el.classList.add("show");
clearTimeout(toastTimer);
toastTimer = setTimeout(() => el.classList.remove("show"), ms || 4000);
}

function toastWithLink(text, linkText, href) {
const { toast: el} = ensureUI();
el.textContent = "";
el.appendChild(document.createTextNode(text + " "));
const a = document.createElement("a");
a.textContent = linkText;
a.href = href;
a.target = "_blank";
a.rel = "noopener";
el.appendChild(a);
el.classList.add("show");
clearTimeout(toastTimer);
toastTimer = setTimeout(() => el.classList.remove("show"), 8000);
}

function sendMsg(msg) {
return new Promise((resolve) => {
try {
chrome.runtime.sendMessage(msg, (resp) => resolve(resp || null));
} catch (e) {
resolve(null);
}
});
}

/* ---- 表单 ---- */
function clearPanel() {
const { panel} = ensureUI();
panel.textContent = "";
}

function openForm() {
const { panel} = ensureUI();
clearPanel();
panel.classList.add("open");

const extracted = AdapterRegistry.extractProblem(location.href, document);
if (!extracted.ok) {
panel.appendChild(h("h3", null, T("formTitle")));
panel.appendChild(h("div", { class: "oy-err"}, T("extractFail")));
panel.appendChild(
h("div", { class: "oy-row"}, null)
);
const row = panel.lastChild;
row.appendChild(h("button", { class: "oy-ghost", onclick: closeForm}, T("cancel")));
return;
}
currentExtract = extracted.data;
buildForm(panel, extracted.data);
}

function closeForm() {
const { panel} = ensureUI();
panel.classList.remove("open");
clearPanel();
currentExtract = null;
}

function srcName(source) {
return (globalThis.OY.SOURCE_NAMES && OY.SOURCE_NAMES[source]) || source;
}

function buildForm(panel, data) {
panel.appendChild(h("h3", null, T("formTitle")));
const meta = h(
"div",
{ class: "oy-src"},
srcName(data.source) +
(data.difficulty? " · " + data.difficulty: "") +
(data.degraded && data.degraded.length? "（部分字段未识别，可手填）": "")
);
panel.appendChild(meta);

panel.appendChild(h("label", null, T("labelTitle")));
const titleInput = h("input", { type: "text", maxlength: "200"});
titleInput.value = data.title || "";
panel.appendChild(titleInput);

const needsContent =!(data.content && data.content.trim());
let contentInput = null;
if (needsContent) {
panel.appendChild(h("div", { class: "oy-note"}, T("needManualContent")));
panel.appendChild(h("label", null, "题面"));
contentInput = h("textarea", { rows: "4", placeholder: "粘贴题面（可选）"});
panel.appendChild(contentInput);
}

// 后端暂不支持的来源：拦截导入，给复制文本
if (!globalThis.OY.isSourceSupported(data.source)) {
panel.appendChild(h("div", { class: "oy-err"}, T("unsupportedSource")));
panel.appendChild(h("div", { class: "oy-note"}, T("unsupportedSourceHint")));
const row = h("div", { class: "oy-row"});
const copyBtn = h("button", { class: "oy-primary"}, T("copyInfo"));
copyBtn.addEventListener("click", () => {
const text =
"\n来源：" + srcName(data.source) + "\n链接：" + data.url +
(data.difficulty? "\n难度：" + data.difficulty: "") +
(data.tags && data.tags.length? "\n标签：" + data.tags.join("、"): "") +
(data.content? "\n\n" + data.content.slice(0, 2000): "");
copyText(text).then((ok) => toast(ok? T("copied"): text.slice(0, 60)));
});
row.appendChild(copyBtn);
row.appendChild(h("button", { class: "oy-ghost", onclick: closeForm}, T("cancel")));
panel.appendChild(row);
return;
}

panel.appendChild(h("label", null, T("labelReason")));
const reasonInput = h("textarea", { rows: "2", placeholder: T("reasonPlaceholder"), maxlength: "500"});
panel.appendChild(reasonInput);
panel.appendChild(h("label", null, T("labelChips")));
const chips = h("div", { class: "oy-chips"});
for (const c of T("chips")) {
const chip = h("button", { type: "button", class: "oy-chip"}, c);
chip.addEventListener("click", () => {
const cur = reasonInput.value.trim();
reasonInput.value = cur? cur + "；" + c: c;
reasonInput.focus();
});
chips.appendChild(chip);
}
panel.appendChild(chips);

const err = h("div", { class: "oy-err", style: "display:none"});
panel.appendChild(err);

const row = h("div", { class: "oy-row"});
const submit = h("button", { class: "oy-primary"}, T("submit"));
const cancel = h("button", { class: "oy-ghost"}, T("cancel"));
cancel.addEventListener("click", closeForm);
submit.addEventListener("click", () =>
doSubmit(submit, err, {
title: titleInput.value,
content: contentInput? contentInput.value: data.content || "",
reason: reasonInput.value,
difficulty: data.difficulty || "",
})
);
row.append(submit, cancel);
panel.appendChild(row);
}

function copyText(text) {
return new Promise((resolve) => {
try {
if (navigator.clipboard && navigator.clipboard.writeText) {
navigator.clipboard.writeText(text).then(() => resolve(true), () => resolve(false));
} else {
resolve(false);
}
} catch (e) {
resolve(false);
}
});
}

async function doSubmit(btn, errEl, form) {
const v = globalThis.OY.validateImportForm(form);
if (!v.ok) {
errEl.style.display = "";
errEl.textContent = "请检查：标题不能为空且不超过 200 字。";
return;
}
const data = Object.assign({}, currentExtract, {
title: v.title,
content: v.content,
difficulty: v.difficulty,
});
btn.disabled = true;
errEl.style.display = "none";

// 重复检测（本地记录）
const dup = await sendMsg({
type: "ouye-check-dup",
source: data.source,
source_id: data.source_id,
});
if (dup && dup.duplicate) {
btn.disabled = false;
showDupDialog(dup.duplicate, data, v.reason);
return;
}

const resp = await sendMsg({ type: "ouye-import-v2", data: Object.assign({}, data, { pending_reason: v.reason || "" })});
btn.disabled = false;
if (resp && resp.ok) {
await sendMsg({
type: "ouye-save-record",
record: globalThis.OY.makeRecord(data, {
problemId: resp.id,
pendingReason: v.reason,
}),
});
closeForm();
if (v.reason) {
toastWithLink(T("importOk") + "。" + T("reasonSavedLocal"), T("openOuye"), resp.homeUrl);
} else {
toastWithLink(T("importOk"), T("openOuye"), resp.homeUrl);
}
} else {
errEl.style.display = "";
const e = (resp && resp.error) || "UNKNOWN";
errEl.textContent =
e === "NO_TOKEN"? T("noToken")
: e === "NO_BASE_URL"? T("noBaseUrl")
: e === "BAD_TOKEN"? T("badToken")
: e === "SOURCE_UNSUPPORTED"? T("unsupportedSource")
: T("importFail") + "：" + ((resp && resp.message) || e);
}
}

function showDupDialog(record, data, reason) {
const { panel} = ensureUI();
clearPanel();
panel.classList.add("open");
panel.appendChild(h("h3", null, T("dupTitle")));
const when = (record.importedAt || "").slice(0, 10);
panel.appendChild(h("div", { class: "oy-note"}, T("dupHint") + (when? "（" + when + "收录）": "")));
if (reason) {
panel.appendChild(h("div", { class: "oy-note"}, "这道题已经记过了，可以把这次的错因追加到那道题里。"));
const appendBtn = h("button", { class: "oy-primary"}, "追加这条错因");
appendBtn.addEventListener("click", async () => {
appendBtn.disabled = true;
const done = await sendMsg({ type: "ouye-append-reason", problemId: record.problemId, reason});
if (done && done.ok) { closeForm(); toast("错因已追加到欧叶OY"); }
else { appendBtn.disabled = false; toast("追加失败，请稍后再试"); }
});
panel.appendChild(appendBtn);
}
const row = h("div", { class: "oy-row"});
const openBtn = h("button", { class: "oy-primary"}, T("dupOpen"));
openBtn.addEventListener("click", async () => {
const s = await sendMsg({ type: "ouye-get-settings"});
const base = (s && s.baseUrl) || globalThis.OY.DEFAULT_BASE_URL;
window.open(base, "_blank", "noopener");
closeForm();
});
const forceBtn = h("button", { class: "oy-ghost"}, T("dupForce"));
forceBtn.addEventListener("click", async () => {
// 用户明确要求仍要新建：走一遍导入（会产生重复题，后端无去重）
const resp = await sendMsg({ type: "ouye-import-v2", data: Object.assign({}, data, { pending_reason: reason || "" })});
if (resp && resp.ok) {
await sendMsg({
type: "ouye-save-record",
record: globalThis.OY.makeRecord(data, { problemId: resp.id, pendingReason: reason}),
force: true,
});
closeForm();
toastWithLink(T("importOk"), T("openOuye"), resp.homeUrl);
} else {
toast(T("importFail"));
}
});
const cancelBtn = h("button", { class: "oy-ghost"}, T("dupCancel"));
cancelBtn.addEventListener("click", closeForm);
row.append(openBtn, forceBtn, cancelBtn);
panel.appendChild(row);
}

/* ---- SPA 路由监听：切题不刷新时重挂按钮 ---- */
function watchSpa() {
let lastUrl = location.href;
const check = () => {
if (location.href!== lastUrl) {
lastUrl = location.href;
closeForm();
const { btn} = ensureUI();
if (AdapterRegistry.detect(location.href)) {
btn.style.display = collapsed? "none": "";
setTimeout(() => {}, 0);
} else {
btn.style.display = "none";
}
}
};
try {
new MutationObserver(check).observe(document.documentElement, {
childList: true,
subtree: true,
});
} catch (e) {}
setInterval(check, 2500);
}

/* ---- 消息：popup 的后备通道 ---- */
try {
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
if (!msg) return false;
if (msg.type === "ouye-extract-quick") {
sendResponse(AdapterRegistry.extractProblem(location.href, document));
return true;
}
if (msg.type === "ouye-open-form") {
ensureUI();
openForm();
sendResponse({ ok: true});
return true;
}
return false;
});
} catch (e) {}

/* ---- 启动 ---- */
if (AdapterRegistry.detect(location.href)) {
ensureUI();
try {
chrome.storage.local.get(["ouyeBtnCollapsed", "ouyeBtnSide"], (s) => {
if (s && s.ouyeBtnCollapsed) setCollapsed(true, s.ouyeBtnSide || "right");
});
} catch (e) {}
setTimeout(() => {
if (!collapsed) ui.btn.style.display = "";
}, 1000);
watchSpa();
}
})();
