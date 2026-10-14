/* Service worker v2：所有发往欧叶OY后端的网络请求 + 本地收录记录。
 * token 只存在 chrome.storage.local，不进 URL、不写日志。
 */
"use strict";

importScripts("i18n/messages.js", "lib/api.js", "lib/dedup.js");

const T = (k) => globalThis.OY_T(k);

async function getSettings() {
  const s = await chrome.storage.local.get(["baseUrl", "token"]);
  return {
    baseUrl: (s.baseUrl || OY.DEFAULT_BASE_URL).trim(),
    token: (s.token || "").trim(),
  };
}

async function ensurePermission(origin) {
  const has = await chrome.permissions.contains({ origins: [origin + "/*"] });
  if (has) return true;
  try {
    return await chrome.permissions.request({ origins: [origin + "/*"] });
  } catch (e) {
    return false;
  }
}

function baseOf(settings) {
  const b = OY.normalizeBaseUrl(settings.baseUrl);
  return b;
}

/* ---- 导入 ---- */
async function doImport(data) {
  const settings = await getSettings();
  const base = baseOf(settings);
  if (!base) return { ok: false, error: "NO_BASE_URL" };
  if (!settings.token) return { ok: false, error: "NO_TOKEN" };
  if (!OY.isSourceSupported(data.source)) {
    return { ok: false, error: "SOURCE_UNSUPPORTED" };
  }
  if (!(await ensurePermission(base))) {
    return { ok: false, error: "PERMISSION_DENIED", message: "没有该地址的访问授权" };
  }
  let req;
  try {
    req = OY.buildImportRequest({ baseUrl: base, token: settings.token, data });
  } catch (e) {
    return { ok: false, error: "BAD_REQUEST", message: String((e && e.message) || e) };
  }
  let resp;
  try {
    resp = await fetch(req.url, {
      method: "POST",
      headers: req.headers,
      body: JSON.stringify(req.body),
    });
  } catch (e) {
    return { ok: false, error: "NETWORK_ERROR", message: "连不上后端：" + base };
  }
  if (resp.status === 401) {
    return { ok: false, error: "BAD_TOKEN", message: T("badToken") };
  }
  let body = null;
  try {
    body = await resp.json();
  } catch (e) {}
  if (!resp.ok) {
    const detail =
      body && (body.detail || body.message)
        ? typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail)
        : "HTTP " + resp.status;
    return { ok: false, error: "SERVER_REJECTED", message: detail };
  }
  return { ok: true, id: body && body.id, homeUrl: base };
}

/* ---- 本地收录记录 ---- */
async function getRecords() {
  const s = await chrome.storage.local.get(["ouyeRecords"]);
  return s.ouyeRecords || {};
}

async function checkDup(source, source_id) {
  const records = await getRecords();
  const duplicate = OY.checkDuplicate(records, source, source_id);
  return { duplicate: duplicate || null };
}

async function saveRecord(record, force) {
  const records = await getRecords();
  const key = OY.recordKey(record.source, record.source_id);
  if (!force && records[key]) return { ok: false, error: "DUPLICATE" };
  records[key] = record;
  await chrome.storage.local.set({ ouyeRecords: records });
  return { ok: true };
}

/* ---- 连接状态：GET /api/me（带 token；该接口不泄露 token） ---- */
async function checkConnection() {
  const settings = await getSettings();
  const base = baseOf(settings);
  if (!base) return { status: "no-config" };
  if (!settings.token) return { status: "no-token", baseUrl: base };
  if (!(await chrome.permissions.contains({ origins: [base + "/*"] }))) {
    return { status: "no-permission", baseUrl: base };
  }
  let req;
  try {
    req = OY.buildDueCountRequest({ baseUrl: base, token: settings.token });
  } catch (e) {
    return { status: "bad-config", baseUrl: base };
  }
  try {
    const resp = await fetch(req.url, { headers: req.headers });
    if (resp.status === 401 || resp.status === 403) {
      return { status: "bad-token", baseUrl: base };
    }
    if (!resp.ok) return { status: "unreachable", baseUrl: base, http: resp.status };
    return { status: "ok", baseUrl: base };
  } catch (e) {
    return { status: "unreachable", baseUrl: base };
  }
}

async function getDueCount() {
  const settings = await getSettings();
  const base = baseOf(settings);
  if (!base || !settings.token) return { ok: false };
  try {
    const req = OY.buildDueCountRequest({ baseUrl: base, token: settings.token });
    const resp = await fetch(req.url, { headers: req.headers });
    if (!resp.ok) return { ok: false };
    const body = await resp.json();
    return { ok: true, count: Number(body.due_count) || 0 };
  } catch (e) {
    return { ok: false };
  }
}

async function appendReason(problemId, reason) {
  const settings = await getSettings();
  const base = baseOf(settings);
  if (!base) return { ok: false, error: "NO_BASE_URL" };
  if (!settings.token) return { ok: false, error: "NO_TOKEN" };
  let req;
  try {
    req = OY.buildAppendReasonRequest({ baseUrl: base, token: settings.token, problemId, reason });
  } catch (e) {
    return { ok: false, error: "BAD_REQUEST", message: String((e && e.message) || e) };
  }
  try {
    const resp = await fetch(req.url, { method: "POST", headers: req.headers, body: JSON.stringify(req.body) });
    if (resp.status === 401) return { ok: false, error: "BAD_TOKEN" };
    if (resp.status === 404) return { ok: false, error: "NOT_FOUND" };
    return resp.ok ? { ok: true } : { ok: false, error: "HTTP_" + resp.status };
  } catch (e) {
    return { ok: false, error: "NETWORK_ERROR" };
  }
}

async function getStats() {
  const records = await getRecords();
  const sum = OY.summarizeRecords(records);
  return sum;
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (!msg || !msg.type) return false;
  const done = (p) => p.then(sendResponse, (e) => sendResponse({ ok: false, error: String(e && e.message || e) }));
  switch (msg.type) {
    case "ouye-due-count":
      done(getDueCount());
      return true;
    case "ouye-append-reason":
      done(appendReason(msg.problemId, msg.reason));
      return true;
    case "ouye-import-v2":
      done(doImport(msg.data));
      return true;
    case "ouye-check-dup":
      done(checkDup(msg.source, msg.source_id));
      return true;
    case "ouye-save-record":
      done(saveRecord(msg.record, msg.force));
      return true;
    case "ouye-get-settings":
      done(getSettings().then((s) => ({ baseUrl: s.baseUrl, hasToken: !!s.token })));
      return true;
    case "ouye-get-settings-full":
      done(
        chrome.storage.local
          .get(["baseUrl", "token", "resultNudgeEnabled"])
          .then((s) => ({
            baseUrl: (s.baseUrl || OY.DEFAULT_BASE_URL).trim(),
            hasToken: !!s.token,
            // 结果页提示开关：默认开，只有显式设为 false 才关。
            resultNudgeEnabled: s.resultNudgeEnabled !== false,
          }))
      );
      return true;
    case "ouye-connection":
      done(checkConnection());
      return true;
    case "ouye-stats":
      done(getStats());
      return true;
    default:
      return false;
  }
});
