"use strict";

/* 代码只经宿主 api() 送到站长配置的独立服务；编辑内容仅属于当前详情。 */
(() => {
  const LANGUAGES = ["Python", "C++"];
  const STATUS = { completed: "程序运行结束", time_limit: "运行超时", compile_error: "编译失败", runtime_error: "运行出错" };
  const PLACEHOLDER = "（速记：代码待补）";
  let hooks = null, generation = 0, root = null, host = null;

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = String(text);
    return element;
  }
  function button(text, className) {
    const element = node("button", className, text);
    element.type = "button";
    return element;
  }
  function field(text, control) {
    const label = node("label", "cr-field", text);
    label.append(control);
    return label;
  }
  function unmount() {
    generation += 1;
    if (root) {
      for (const input of root.querySelectorAll("textarea, input")) input.value = "";
      for (const output of root.querySelectorAll("pre")) output.textContent = "";
      root.replaceChildren(); root.remove();
    }
    host?.replaceChildren(); root = host = null;
  }
  function mount(container, item, { offline = false } = {}) {
    unmount();
    if (!hooks?.getUser() || !Number.isSafeInteger(item?.id) || item.id <= 0) return null;
    host = container;
    const panel = node("section", "cr-panel"); root = panel; container.append(panel);
    const ticket = { generation, epoch: hooks.getEpoch(), user: hooks.getUser().id, view: hooks.getView(),
      selection: hooks.getSelectionGeneration?.(), detail: hooks.getDetailGeneration?.() };
    const current = () => root === panel && panel.isConnected && ticket.generation === generation
      && Boolean(hooks.getUser()) && ticket.user === hooks.getUser().id && ticket.epoch === hooks.getEpoch()
      && ticket.view === hooks.getView() && ["today", "all"].includes(hooks.getView())
      && ticket.selection === hooks.getSelectionGeneration?.() && ticket.detail === hooks.getDetailGeneration?.();
    let capabilities = null, busy = false, loading = false, request = 0;
    const title = node("h3", "cr-title", "运行代码");
    const help = node("p", "cr-help", "仅支持 Python / C++。代码和标准输入会发送到站长配置的独立运行服务；不会自动判题，不消耗 AI 额度，也不会保存这里的修改。");
    const status = node("p", "cr-status", offline ? "离线时不能运行代码。" : "正在读取独立运行服务配置…");
    status.setAttribute("role", "status"); status.setAttribute("aria-live", "polite");
    const editor = node("details", "cr-editor");
    editor.append(node("summary", "cr-summary", "展开运行面板"));
    const form = node("form", "cr-form");
    const language = node("select", "cr-language"); language.required = true;
    const blank = node("option", "", "请选择 Python 或 C++"); blank.value = ""; language.append(blank);
    for (const value of LANGUAGES) {
      const option = node("option", "", value); option.value = value; language.append(option);
    }
    language.value = LANGUAGES.includes(item.language) ? item.language : "";
    const code = node("textarea", "cr-code"); code.maxLength = 40000; code.rows = 8; code.required = true; code.spellcheck = false;
    code.value = typeof item.code === "string" && item.code.trim() !== PLACEHOLDER ? item.code : "";
    code.placeholder = "填写这一轮要运行的代码，修改不会覆盖原记录。";
    const stdin = node("textarea", "cr-stdin"); stdin.maxLength = 10000; stdin.rows = 3; stdin.spellcheck = false;
    stdin.placeholder = "例如：5\n1 2 3 4 5";
    const submit = button("运行这一轮", "cr-run"); submit.type = "submit"; submit.disabled = true;
    const retry = button("重新读取配置", "cr-retry"); retry.hidden = true;
    const limits = node("p", "cr-help", "每轮限 CPU 2 秒、内存 128 MB。结果只用于观察程序输出，请自行对照题目的预期答案。");
    const results = node("div", "cr-results");
    form.append(field("运行语言", language), field("这一轮的代码", code), field("标准输入（可选）", stdin), submit, limits, results);
    editor.append(form); panel.append(title, help, status, retry, editor);

    const allowed = () => !offline && capabilities?.configured === true && capabilities.allowed === true && !hooks.getUser()?.is_trial;
    function readyMessage() {
      if (offline) return "离线时不能运行代码。";
      if (!capabilities) return "独立运行服务配置未读取成功。";
      if (!capabilities.allowed || hooks.getUser()?.is_trial) return "运行代码仅限普通账号使用。";
      if (!capabilities.configured) return "需要站长配置独立运行服务后，才能运行代码。";
      return "独立运行服务已就绪，展开面板后可运行这一轮代码。";
    }
    function controls() {
      submit.dataset.blocked = busy || !allowed() ? "1" : "0";
      submit.disabled = submit.dataset.blocked === "1";
      language.disabled = code.disabled = stdin.disabled = busy;
      retry.dataset.blocked = busy || loading ? "1" : "0";
      retry.disabled = retry.dataset.blocked === "1";
      form.setAttribute("aria-busy", String(busy));
    }
    async function loadCapabilities() {
      if (!current() || offline || loading || busy) return;
      loading = true; retry.hidden = true; controls();
      try {
        const data = await hooks.api("/api/code-runner");
        if (!current()) return;
        if (!data || typeof data.configured !== "boolean" || typeof data.allowed !== "boolean"
          || !Array.isArray(data.languages) || !data.languages.length || data.languages.some((value) => !LANGUAGES.includes(value))) {
          throw new Error("独立运行服务返回了无效配置。");
        }
        capabilities = data;
        for (const option of language.querySelectorAll("option")) option.disabled = Boolean(option.value) && !data.languages.includes(option.value);
        if (language.value && !data.languages.includes(language.value)) language.value = "";
        status.textContent = readyMessage();
      } catch (error) {
        if (!current()) return;
        capabilities = null; status.textContent = error.message || "读取独立运行服务配置失败。"; retry.hidden = false;
      } finally {
        if (current()) { loading = false; controls(); }
      }
    }
    async function run() {
      if (!current() || busy || !allowed() || !editor.open || panel.closest("[hidden]")) return;
      if (!capabilities.languages.includes(language.value)) { status.textContent = "请选择支持的 Python 或 C++。"; language.focus(); return; }
      if (!code.value.trim() || code.value.trim() === PLACEHOLDER || code.value.length > 40000) {
        status.textContent = "请填写要运行的代码，最多 40000 个字符。"; code.focus(); return;
      }
      if (stdin.value.length > 10000) { status.textContent = "标准输入最多 10000 个字符。"; stdin.focus(); return; }
      const sequence = ++request;
      const payload = { language: language.value, code: code.value, stdin: stdin.value };
      busy = true; results.replaceChildren(); status.textContent = "正在独立服务中运行…"; controls();
      try {
        const data = await hooks.api(`/api/mistakes/${item.id}/run`, { method: "POST", body: JSON.stringify(payload) });
        if (!current() || sequence !== request) return;
        if (!data || !Object.hasOwn(STATUS, data.status) || typeof data.truncated !== "boolean"
          || ["stdout", "stderr", "compile_output"].some((key) => typeof data[key] !== "string")) {
          throw new Error("独立运行服务返回了无效结果。");
        }
        status.textContent = STATUS[data.status];
        let truncated = data.truncated;
        for (const [key, label] of [["stdout", "标准输出"], ["stderr", "标准错误"], ["compile_output", "编译信息"]]) {
          truncated ||= data[key].length > 8000;
          results.append(node("h4", "cr-result-title", label), node("pre", "cr-output", data[key].slice(0, 8000) || "（无输出）"));
        }
        if (truncated) results.append(node("p", "cr-help", "输出较长，已截断显示。"));
      } catch (error) {
        if (current() && sequence === request) status.textContent = error.message || "运行失败，请稍后重试。";
      } finally {
        if (current() && sequence === request) { busy = false; controls(); }
      }
    }
    form.addEventListener("submit", (event) => { event.preventDefault(); void run(); });
    retry.addEventListener("click", () => { void loadCapabilities(); });
    editor.addEventListener("toggle", () => {
      if (!current() || editor.open) return;
      request += 1; busy = false; results.replaceChildren();
      if (capabilities) status.textContent = readyMessage();
      controls();
    });
    controls(); if (!offline) void loadCapabilities();
    return panel;
  }
  window.CodeRunner = { configure(options) { unmount(); hooks = options; }, mount, unmount, reset: unmount };
})();
