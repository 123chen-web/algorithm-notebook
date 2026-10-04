"use strict";

/* 管理后台“今日一条”：发布、编辑、停用 / 启用、查看历史。
   写请求走宿主传入的 api()（自带 CSRF 头）；响应按登录代次 / 页面 / 请求序号隔离；
   管理员输入的文字一律 textContent，链接只在通过 http(s) 校验后才渲染成可点击的 <a>。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const root = $("#rank-admin-card");
  if (!root) return;

  const MAX_TEXT = 80;
  const MAX_LINK = 500;
  const STATUS_NAMES = { active: "生效中", scheduled: "未开始", expired: "已过期", disabled: "已停用" };

  let hooks = null;
  let generation = 0;
  let listSequence = 0;
  let editingId = null;
  let pending = false;
  let items = [];
  let today = "";

  /* ---------- 纯函数（也挂在 window.RankAdmin.helpers 上供测试） ---------- */

  const countChars = (value) => Array.from(String(value ?? "")).length;

  /** 与服务端 rank_notice.clean_link 同一套规则的前端预检；服务端才是最终裁判。 */
  function linkProblem(value) {
    const link = String(value ?? "").trim();
    if (!link) return "";
    if (link.length > MAX_LINK) return `链接最多 ${MAX_LINK} 个字符`;
    if (/[\s\\]/.test(link)) return "链接不能包含空白或反斜杠";
    let url;
    try {
      url = new window.URL(link);
    } catch {
      return "链接格式不正确";
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return "链接必须以 http:// 或 https:// 开头";
    if (url.username || url.password) return "链接不能带账号或密码";
    return "";
  }

  function formProblem({ text, link, start, end }) {
    const chars = countChars(text.trim());
    if (!chars) return "请填写一句话";
    if (chars > MAX_TEXT) return `一句话最多 ${MAX_TEXT} 个字`;
    const linkError = linkProblem(link);
    if (linkError) return linkError;
    if (!start || !end) return "请选择开始和结束日期";
    if (end < start) return "结束日期不能早于开始日期";
    return "";
  }

  function safeHref(value) {
    return value && !linkProblem(value) ? new window.URL(value.trim()).href : null;
  }

  /* ---------- 加载与隔离 ---------- */

  function ticket() {
    listSequence += 1;
    return { generation, sequence: listSequence, epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
  }

  function current(request) {
    const user = hooks?.getUser();
    return Boolean(hooks && user && user.is_admin && request.generation === generation
      && request.sequence === listSequence && request.epoch === hooks.getEpoch()
      && request.userId === user.id && hooks.getView() === "admin");
  }

  function node(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value !== undefined) item.textContent = value;
    return item;
  }

  function renderList() {
    const list = $("#rank-admin-list");
    list.replaceChildren();
    for (const item of items) {
      const row = node("li", "rank-admin-item");
      row.dataset.status = item.status;
      const head = node("p", "rank-admin-item-head");
      head.append(node("span", "rank-admin-badge", STATUS_NAMES[item.status] || item.status),
        node("span", "rank-admin-dates-text", item.start_date === item.end_date ? item.start_date : `${item.start_date} 至 ${item.end_date}`));
      const body = node("p", "rank-admin-item-text", item.text);
      row.append(head, body);
      const href = safeHref(item.link);
      if (href) {
        const link = node("a", "rank-admin-item-link", href);
        link.setAttribute("href", href);
        link.setAttribute("rel", "noopener noreferrer");
        link.setAttribute("target", "_blank");
        row.append(link);
      }
      const actions = node("div", "rank-admin-item-actions");
      const edit = node("button", "", "编辑");
      edit.type = "button";
      edit.addEventListener("click", () => startEdit(item.id));
      const toggle = node("button", "", item.is_active ? "停用" : "启用");
      toggle.type = "button";
      toggle.addEventListener("click", () => toggleActive(item.id));
      actions.append(edit, toggle);
      row.append(actions);
      list.append(row);
    }
    $("#rank-admin-status").textContent = items.length ? "" : "还没有任何记录。";
  }

  function formValues() {
    return {
      text: $("#rank-admin-text").value,
      link: $("#rank-admin-link").value,
      start: $("#rank-admin-start").value,
      end: $("#rank-admin-end").value,
      active: $("#rank-admin-active").checked,
    };
  }

  function syncCount() {
    const chars = countChars($("#rank-admin-text").value.trim());
    const counter = $("#rank-admin-count");
    counter.textContent = `${chars} / ${MAX_TEXT}`;
    counter.dataset.over = chars > MAX_TEXT ? "true" : "false";
  }

  function fillForm(item) {
    $("#rank-admin-text").value = item ? item.text : "";
    $("#rank-admin-link").value = item?.link || "";
    $("#rank-admin-start").value = item ? item.start_date : today;
    $("#rank-admin-end").value = item ? item.end_date : today;
    $("#rank-admin-active").checked = item ? item.is_active : true;
    $("#rank-admin-save").textContent = item ? "保存修改" : "发布";
    $("#rank-admin-cancel").hidden = !item;
    syncCount();
  }

  function startEdit(id) {
    const item = items.find((entry) => entry.id === id);
    if (!item || pending) return;
    editingId = id;
    fillForm(item);
    $("#rank-admin-form-status").textContent = "正在编辑一条已有记录。";
    $("#rank-admin-text").focus();
  }

  function stopEdit() {
    editingId = null;
    fillForm(null);
    $("#rank-admin-form-status").textContent = "";
  }

  function setPending(value) {
    pending = value;
    $("#rank-admin-save").disabled = value;
    $("#rank-admin-cancel").disabled = value;
    $("#rank-admin-form").setAttribute("aria-busy", value ? "true" : "false");
  }

  async function load() {
    generation += 1;
    if (!hooks || !hooks.getUser()?.is_admin) {
      clear();
      return false;
    }
    const request = ticket();
    $("#rank-admin-status").textContent = "正在加载…";
    try {
      const data = await hooks.api("/api/admin/daily-notices");
      if (!current(request)) return false;
      items = data.notices;
      const firstLoad = !today;
      today = data.today;
      renderList();
      if (firstLoad && editingId === null) fillForm(null);
      return true;
    } catch (error) {
      if (current(request)) $("#rank-admin-status").textContent = error?.message || "加载失败，请重试。";
      return false;
    }
  }

  async function write(method, path, body, doneText) {
    const status = $("#rank-admin-form-status");
    // 写请求发出之后，只要还是同一位管理员、同一次登录，就该把表单状态收尾，不管中途切没切过页面。
    const request = { epoch: hooks.getEpoch(), userId: hooks.getUser()?.id };
    const same = () => request.epoch === hooks.getEpoch() && hooks.getUser()?.id === request.userId
      && Boolean(hooks.getUser()?.is_admin);
    let saved = false;
    setPending(true);
    status.textContent = "正在保存…";
    try {
      await hooks.api(path, { method, body: JSON.stringify(body) });
      if (!same()) return false;
      stopEdit();
      status.textContent = doneText;
      saved = true;
    } catch (error) {
      if (same()) status.textContent = error?.message || "保存失败，请重试。";
    } finally {
      if (same()) setPending(false);
    }
    if (saved) await load();
    return saved;
  }

  function bodyFrom(values) {
    return {
      text: values.text.trim(),
      link: values.link.trim() || null,
      start_date: values.start,
      end_date: values.end,
      is_active: values.active,
    };
  }

  async function submit(event) {
    event.preventDefault();
    if (pending || !hooks?.getUser()?.is_admin) return;
    const values = formValues();
    const problem = formProblem(values);
    if (problem) {
      $("#rank-admin-form-status").textContent = problem;
      return;
    }
    if (editingId === null) await write("POST", "/api/admin/daily-notices", bodyFrom(values), "已发布。");
    else await write("PUT", `/api/admin/daily-notices/${editingId}`, bodyFrom(values), "已保存修改。");
  }

  async function toggleActive(id) {
    const item = items.find((entry) => entry.id === id);
    if (!item || pending || !hooks?.getUser()?.is_admin) return;
    const body = {
      text: item.text, link: item.link, start_date: item.start_date, end_date: item.end_date,
      is_active: !item.is_active,
    };
    await write("PUT", `/api/admin/daily-notices/${id}`, body, item.is_active ? "已停用。" : "已启用。");
  }

  function clear() {
    items = [];
    today = "";
    editingId = null;
    pending = false;
    $("#rank-admin-list").replaceChildren();
    $("#rank-admin-status").textContent = "";
    $("#rank-admin-form-status").textContent = "";
    $("#rank-admin-save").disabled = false;
    $("#rank-admin-cancel").disabled = false;
    $("#rank-admin-form").setAttribute("aria-busy", "false");
    fillForm(null);
  }

  function reset() {
    generation += 1;
    listSequence += 1;
    clear();
  }

  $("#rank-admin-form").addEventListener("submit", submit);
  $("#rank-admin-cancel").addEventListener("click", () => { if (!pending) stopEdit(); });
  $("#rank-admin-text").addEventListener("input", syncCount);
  document.addEventListener("app:view-changed", () => { generation += 1; });
  syncCount();

  window.RankAdmin = {
    configure: (value) => { hooks = value; },
    load,
    reset,
    helpers: { countChars, linkProblem, formProblem, safeHref },
  };
})();
