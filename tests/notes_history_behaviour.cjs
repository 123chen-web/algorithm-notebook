"use strict";

/* 笔记“不丢字”层（static/notes-history.js）与画板图片外存（static/draw-host.js）的行为测试。
   覆盖：自动保存带 base_updated_at 并滚动更新、保存成功清本机草稿、断网时保留草稿并给重试、
   409 冲突后“保留我的”（读最新 → 带 snapshot 覆盖）与“看最新的”、空正文不保存、
   本机存储抛异常时静默降级、只在版本对得上时提示恢复草稿；
   画板图片：可上传的图片只传一次并换成 attachment:ID、SVG 和上传失败照旧内嵌、
   打开时把 attachment:ID 还原成 dataURL、取不到的图片保留引用。 */
const assert = require("node:assert/strict");
const test = require("node:test");
const { load, tick } = require("./js_harness.cjs");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function setup({ storage } = {}) {
  const env = load(["notes-history.js"], storage ? { extra: { localStorage: storage } } : {});
  const calls = [];
  const api = (path, options = {}) => {
    const call = { path, method: options.method || "GET", body: options.body ? JSON.parse(options.body) : null, ...deferred() };
    calls.push(call);
    return call.promise;
  };
  const reply = async (call, data) => { call.resolve(data); await tick(); await tick(); };
  const fail = async (call, status, message) => {
    call.reject(Object.assign(new Error(message), { status }));
    await tick(); await tick();
  };
  const NotesHistory = env.window.NotesHistory;
  NotesHistory.configure({ api, getUser: () => ({ id: 7 }), notify() {}, confirm: () => true });
  return { env, calls, reply, fail, NotesHistory };
}

function editor(env, NotesHistory, overrides = {}) {
  const note = { id: 3, title: "单调栈", content: "第一版", tags: ["栈"], updated_at: "2026-10-14T08:00:00+00:00", ...overrides };
  const value = { title: note.title, content: note.content, tags: [...note.tags] };
  const bar = env.document.createElement("p");
  const seen = { saved: [], theirs: [] };
  const saver = NotesHistory.autosave({
    note, read: () => ({ ...value, tags: [...value.tags] }), bar,
    onSaved: (updated) => seen.saved.push(updated), onTheirs: (latest) => seen.theirs.push(latest),
  });
  return { note, value, bar, saver, seen };
}

test("autosave: PUT carries base_updated_at, rolls it forward and clears the local draft", async () => {
  const { env, calls, reply, NotesHistory } = setup();
  const { value, bar, saver, seen } = editor(env, NotesHistory);
  value.content = "第二版";
  value.title = "  单调栈  ";
  saver.schedule();
  assert.ok(env.window.localStorage.getItem("oy:notes:7:edit:3"), "改动先存一份在本机");
  const first = saver.flush();
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "PUT");
  assert.deepEqual(calls[0].body, {
    title: "单调栈", content: "第二版", tags: ["栈"], base_updated_at: "2026-10-14T08:00:00+00:00",
  });
  await reply(calls[0], { id: 3, content: "第二版", updated_at: "2026-10-14T08:01:00+00:00" });
  assert.equal(await first, true);
  assert.equal(bar.dataset.state, "saved");
  assert.match(bar.textContent, /已自动保存/);
  assert.equal(seen.saved.length, 1);
  assert.equal(env.window.localStorage.getItem("oy:notes:7:edit:3"), null);
  assert.equal(await saver.flush(), true, "没有新改动就不再发请求");
  assert.equal(calls.length, 1);
  value.content = "第三版";
  void saver.flush();
  assert.equal(calls[1].body.base_updated_at, "2026-10-14T08:01:00+00:00");
  saver.stop();
});

test("autosave: a failed save keeps the local draft and offers a retry", async () => {
  const { env, calls, fail, NotesHistory } = setup();
  const { value, bar, saver } = editor(env, NotesHistory);
  value.content = "断网时写的";
  saver.schedule();
  const saving = saver.flush();
  await fail(calls[0], 0, "Failed to fetch");
  assert.equal(await saving, false);
  assert.equal(bar.dataset.state, "error");
  assert.match(bar.textContent, /存在这台设备上/);
  assert.ok(bar.querySelector(".notes-save-retry"), "给一个现在重试的按钮");
  assert.match(env.window.localStorage.getItem("oy:notes:7:edit:3"), /断网时写的/);
  saver.stop();
});

test("autosave: an empty body is never sent", async () => {
  const { env, calls, NotesHistory } = setup();
  const { value, bar, saver } = editor(env, NotesHistory);
  value.content = "   ";
  assert.equal(await saver.flush(), false);
  assert.equal(calls.length, 0);
  assert.equal(bar.dataset.state, "error");
  saver.stop();
});

test("conflict: keep mine re-reads the latest version and overwrites with a forced history snapshot", async () => {
  const { env, calls, reply, fail, NotesHistory } = setup();
  const { value, bar, saver, seen } = editor(env, NotesHistory);
  value.content = "我这边的";
  const saving = saver.flush();
  await fail(calls[0], 409, "这条笔记在别处改过了");
  assert.equal(await saving, false);
  assert.equal(bar.dataset.state, "conflict");
  assert.equal(saver.conflict, true);
  saver.schedule();
  assert.equal(await saver.flush(), false, "冲突没解决前不再自动保存");
  assert.equal(calls.length, 1);
  bar.querySelector(".notes-save-mine").click();
  await tick();
  assert.equal(calls[1].method, "GET");
  assert.equal(calls[1].path, "/api/notes/3");
  await reply(calls[1], { id: 3, content: "别处的", updated_at: "2026-10-14T08:05:00+00:00" });
  assert.equal(calls[2].method, "PUT");
  assert.equal(calls[2].body.snapshot, true);
  assert.equal(calls[2].body.base_updated_at, "2026-10-14T08:05:00+00:00");
  assert.equal(calls[2].body.content, "我这边的");
  await reply(calls[2], { id: 3, content: "我这边的", updated_at: "2026-10-14T08:06:00+00:00" });
  assert.equal(bar.dataset.state, "saved");
  assert.equal(seen.saved.length, 1);
  saver.stop();
});

test("conflict: see latest hands the server copy back and stops saving", async () => {
  const { env, calls, reply, fail, NotesHistory } = setup();
  const { value, bar, saver, seen } = editor(env, NotesHistory);
  value.content = "我这边的";
  saver.schedule();
  void saver.flush();
  await fail(calls[0], 409, "冲突");
  bar.querySelector(".notes-save-theirs").click();
  await tick();
  await reply(calls[1], { id: 3, content: "别处的", updated_at: "2026-10-14T08:05:00+00:00" });
  assert.deepEqual(seen.theirs.map((note) => note.content), ["别处的"]);
  assert.equal(env.window.localStorage.getItem("oy:notes:7:edit:3"), null);
  assert.equal(NotesHistory.hasUnsaved(), false, "停下的编辑器不再拦着关页面");
});

test("drafts: a draft is offered only for the same server version, and storage errors are swallowed", () => {
  const { env, NotesHistory } = setup();
  NotesHistory.drafts.write("edit:3", { title: "单调栈", content: "没保存上的", tags: ["栈"], base: "2026-10-14T08:00:00+00:00" });
  const same = editor(env, NotesHistory);
  assert.equal(same.saver.pendingDraft().content, "没保存上的");
  same.saver.stop();
  const moved = editor(env, NotesHistory, { updated_at: "2026-10-14T09:00:00+00:00" });
  assert.equal(moved.saver.pendingDraft(), null, "服务器之后又改过，旧草稿不再提示");
  moved.saver.stop();

  const broken = {
    getItem() { throw new Error("SecurityError"); },
    setItem() { throw new Error("QuotaExceeded"); },
    removeItem() { throw new Error("SecurityError"); },
  };
  const { NotesHistory: quiet } = setup({ storage: broken });
  assert.doesNotThrow(() => quiet.drafts.write("composer", { content: "x" }));
  assert.equal(quiet.drafts.read("composer"), null);
  assert.doesNotThrow(() => quiet.drafts.clear("composer"));
});

/* ---------- 画板图片 ---------- */
function drawHost() {
  const env = load(["draw-host.js"], { extra: { location: { origin: "http://local.test" } } });
  return env.window.DrawHost;
}

const PNG = "data:image/png;base64,iVBORw0KGgo=";
const SVG = "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=";

test("drawing images: uploadable images are stored once as attachments, others stay inline", async () => {
  const DrawHost = drawHost();
  const uploads = [];
  const upload = async (dataUrl) => {
    uploads.push(dataUrl);
    if (dataUrl.includes("broken")) throw new Error("图片太大");
    return 40 + uploads.length;
  };
  const scene = {
    elements: [{ id: "e1", type: "image", fileId: "f1" }],
    files: {
      f1: { id: "f1", mimeType: "image/png", dataURL: PNG },
      f2: { id: "f2", mimeType: "image/svg+xml", dataURL: SVG },
      f3: { id: "f3", mimeType: "image/jpeg", dataURL: "data:image/jpeg;base64,broken" },
    },
  };
  const stored = await DrawHost.externalizeFiles(scene, upload);
  assert.equal(stored.files.f1.dataURL, "attachment:41");
  assert.equal(stored.files.f1.mimeType, "image/png");
  assert.equal(stored.files.f2.dataURL, SVG, "SVG 不走附件");
  assert.equal(stored.files.f3.dataURL, "data:image/jpeg;base64,broken", "传不上就照旧内嵌");
  assert.equal(scene.files.f1.dataURL, PNG, "原场景对象不改");
  const again = await DrawHost.externalizeFiles(scene, upload);
  assert.equal(again.files.f1.dataURL, "attachment:41");
  assert.equal(uploads.filter((item) => item === PNG).length, 1, "同一张图只传一次");
});

test("drawing images: attachment refs are turned back into data URLs when a drawing opens", async () => {
  const DrawHost = drawHost();
  const fetched = [];
  const fetchImage = async (id) => {
    fetched.push(id);
    if (id === 99) throw new Error("图片取不到");
    return `data:image/png;base64,IMG${id}`;
  };
  const scene = {
    elements: [],
    files: {
      f1: { id: "f1", mimeType: "image/png", dataURL: "attachment:12" },
      f2: { id: "f2", mimeType: "image/png", dataURL: "attachment:99" },
      f3: { id: "f3", mimeType: "image/svg+xml", dataURL: SVG },
    },
  };
  const loaded = await DrawHost.hydrateFiles(scene, fetchImage);
  assert.equal(loaded.files.f1.dataURL, "data:image/png;base64,IMG12");
  assert.equal(loaded.files.f2.dataURL, "attachment:99", "取不到的图片保留引用，之后保存不会把它弄丢");
  assert.equal(loaded.files.f3.dataURL, SVG);
  assert.deepEqual(fetched.sort(), [12, 99]);
  assert.equal(await DrawHost.hydrateFiles(null, fetchImage), null);
  // 打开时取回过的图片，保存时不再重复上传。
  const stored = await DrawHost.externalizeFiles(loaded, async () => { throw new Error("不该再上传"); });
  assert.equal(stored.files.f1.dataURL, "attachment:12");
});
