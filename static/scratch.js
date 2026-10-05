"use strict";

/* 错题详情页的「草稿演算区」：代码草稿 / 对比 / 演算表三个标签 + 自动保存。
   对外契约：window.Scratch = { configure({ api, getUser, getEpoch }), mount(container, mistake), unmount(), reset() }。
   - 请求全部走宿主注入的 api()（自带 CSRF 头；失败抛出带 status/message 的错误；
     真实的 app.js api() 不在错误对象上挂响应体，所以 409 后需要重新 GET 拿服务器版本，
     但若宿主在 error.current / error.body.current 上给了，优先使用）。
   - 自动保存：idle / dirty / saving / saved / error / conflict；编辑后防抖 800ms；
     保存中又有编辑，请求返回后立刻再存一次；切换标签、卸载、页面隐藏时立即保存。
   - 迟到响应守卫：getEpoch()、getUser().id、mistake.id、内部代次一起校验，登出 /
     换号 / 换题 / 卸载之后返回的响应一律丢弃。
   - 所有来自用户或服务器的文字只用 textContent；行对比用 window.DiffLines；
     演算表用 window.TraceTable（不存在时该标签显示“演算表暂不可用”，不报错）。 */
(() => {
  const MAX_LINES = 2000; // 代码草稿 / 修正代码各自的行数上限
  const SIZE_LIMIT = 40000; // code + fixed + JSON(table) 合计字符上限
  const TABLE_MAX_COLS = 20;
  const TABLE_MAX_ROWS = 60;
  const DEFAULT_COLS = ["步骤", "变量", "值"];
  const DEFAULT_ROWS = [["1", "", ""]];

  let hooks = null;
  let generation = 0; // reset / unmount 时加一，丢弃一切迟到响应
  let root = null; // 当前挂载的面板根节点
  let host = null;
  let mistakeId = null;
  let activeTab = "code";
  let state = "idle"; // idle | dirty | saving | saved | error | conflict
  let version = 0;
  let savedAt = "";
  let saveTimer = 0;
  let savingNow = false;
  let pendingWhileSaving = false;
  let lastGood = { code: "", fixed: "", table: null };
  let conflictCurrent = null;
  let tableModel = null;
  let tableCells = []; // [r][c] -> input
  let tableHeaders = []; // [c] -> input

  /* ---------- 小工具 ---------- */
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  function button(label, className, onClick, ariaLabel) {
    const node = el("button", className, label);
    node.type = "button";
    node.setAttribute("aria-label", ariaLabel || label);
    node.addEventListener("click", onClick);
    return node;
  }
  const countLines = (text) => (text === "" ? 1 : text.split("\n").length);
  const trace = () => (typeof window !== "undefined" ? window.TraceTable : null) || null;
  const debounceMs = () => Math.max(0, Number(window.Scratch?.debounceMs ?? 800) || 0);

  /* ---------- 迟到响应守卫 ---------- */
  function ticket() {
    return {
      generation,
      epoch: hooks?.getEpoch ? hooks.getEpoch() : null,
      userId: hooks?.getUser ? hooks.getUser()?.id ?? null : null,
      mistake: mistakeId,
    };
  }
  function alive(mark) {
    return Boolean(root && mark && mark.generation === generation && mark.mistake === mistakeId
      && (hooks?.getEpoch ? hooks.getEpoch() : null) === mark.epoch
      && (hooks?.getUser ? hooks.getUser()?.id ?? null : null) === mark.userId);
  }

  /* ---------- 内容快照与大小上限 ---------- */
  function snapshot() {
    return {
      code: editors.code ? editors.code.area.value : lastGood.code,
      fixed: editors.fixed ? editors.fixed.area.value : lastGood.fixed,
      table: tableModel && trace() ? trace().toJSON(tableModel) : null,
    };
  }
  function serializedSize(value) {
    return value.code.length + value.fixed.length
      + (value.table ? JSON.stringify(value.table).length : 0);
  }

  /* ---------- 状态栏 ---------- */
  let statusBar = null;
  let sizeNote = null;

  function updateStatus() {
    if (!root || !statusBar) return;
    root.dataset.state = state;
    statusBar.replaceChildren();
    if (state === "idle") {
      statusBar.append(el("span", "sc-status-text", "草稿会在你停笔后自动保存。"));
    } else if (state === "dirty") {
      statusBar.append(el("span", "sc-status-text", "有未保存的修改…"));
    } else if (state === "saving") {
      statusBar.append(el("span", "sc-status-text", "正在保存…"));
    } else if (state === "saved") {
      statusBar.append(el("span", "sc-status-text is-saved", savedAt ? `已保存 ${savedAt}` : "已保存。"));
    } else if (state === "error") {
      const wrap = el("span", "sc-status-text is-error");
      wrap.setAttribute("role", "alert");
      wrap.append(button("保存失败，点此重试", "sc-retry", () => flushNow(), "重试保存草稿"));
      statusBar.append(wrap);
    } else if (state === "conflict") {
      const wrap = el("span", "sc-conflict");
      wrap.setAttribute("role", "alert");
      wrap.append(
        el("span", "sc-conflict-text", "另一个窗口修改过这份草稿。"),
        button("用服务器上的版本", "sc-conflict-use-server", adoptServerVersion, "放弃我的修改，用服务器上的版本"),
        button("保留我的并覆盖", "sc-conflict-keep-mine", overwriteWithMine, "保留我的修改，覆盖服务器上的版本"),
      );
      statusBar.append(wrap);
    }
  }

  /* ---------- 自动保存状态机 ---------- */
  function markDirty() {
    if (!root) return;
    if (state === "conflict") return; // 冲突未解决前不再自动提交
    state = "dirty";
    updateStatus();
    window.clearTimeout(saveTimer);
    saveTimer = window.setTimeout(() => {
      saveTimer = 0;
      flush();
    }, debounceMs());
  }

  /** 立即保存：切换标签 / 卸载 / 页面隐藏时调用（取消防抖）。 */
  function flushNow() {
    window.clearTimeout(saveTimer);
    saveTimer = 0;
    flush();
  }

  function flush() {
    if (!root || state === "conflict") return;
    if (state !== "dirty" && state !== "error" && !pendingWhileSaving) return;
    if (savingNow) {
      pendingWhileSaving = true;
      return;
    }
    doSave();
  }

  async function doSave() {
    const mark = ticket();
    const body = { version, code: lastGood.code, fixed: lastGood.fixed, table: lastGood.table };
    savingNow = true;
    pendingWhileSaving = false;
    state = "saving";
    updateStatus();
    try {
      const result = await hooks.api(`/api/mistakes/${mistakeId}/scratch`, {
        method: "PUT",
        body: JSON.stringify(body),
      });
      if (!alive(mark)) return; // 迟到响应：登出 / 换号 / 换题 / 卸载之后返回的一律丢弃
      savingNow = false;
      version = Number(result.version) || version + 1;
      savedAt = typeof result.updated_at === "string" ? result.updated_at : "";
      if (pendingWhileSaving) {
        pendingWhileSaving = false;
        doSave(); // 保存中又有编辑：立刻再存一次（内容已合并为最后一次）
        return;
      }
      state = "saved";
      updateStatus();
    } catch (error) {
      if (!alive(mark)) return;
      savingNow = false;
      pendingWhileSaving = false;
      if (error?.status === 409) {
        await enterConflict(error, mark);
        return;
      }
      state = "error";
      updateStatus();
    }
  }

  /* ---------- 409 冲突 ---------- */
  async function conflictServerCopy(error, mark) {
    // 宿主若在错误对象上给了 current 就直接用；真实的 app.js api() 不给，重新 GET。
    const direct = error && (error.current || (error.body && error.body.current));
    if (direct && typeof direct === "object") return direct;
    const fresh = await hooks.api(`/api/mistakes/${mistakeId}/scratch`);
    if (!alive(mark)) return null;
    return fresh;
  }

  async function enterConflict(error, mark) {
    try {
      const current = await conflictServerCopy(error, mark);
      if (!alive(mark)) return;
      conflictCurrent = current;
      state = "conflict";
      updateStatus();
    } catch (fetchError) {
      if (!alive(mark)) return;
      state = "error";
      updateStatus();
    }
  }

  function adoptServerVersion() {
    if (!root || state !== "conflict" || !conflictCurrent) return;
    const current = conflictCurrent;
    conflictCurrent = null;
    version = Number(current.version) || 0;
    setEditorValue(editors.code, typeof current.code === "string" ? current.code : "");
    setEditorValue(editors.fixed, typeof current.fixed === "string" ? current.fixed : "");
    if (trace()) {
      tableModel = current.table ? trace().validate(current.table) : null;
      if (!tableModel) tableModel = trace().create(DEFAULT_COLS, DEFAULT_ROWS);
      renderTable();
    }
    lastGood = snapshot();
    savedAt = typeof current.updated_at === "string" ? current.updated_at : "";
    state = "saved";
    updateStatus();
    renderDiff();
  }

  function overwriteWithMine() {
    if (!root || state !== "conflict" || !conflictCurrent) return;
    version = Number(conflictCurrent.version) || version; // 用冲突响应里的最新版本再提交
    conflictCurrent = null;
    state = "dirty";
    flush();
  }

  /* ---------- 带行号的编辑区 ---------- */
  const editors = { code: null, fixed: null };

  function refreshGutter(editor) {
    const lines = countLines(editor.area.value);
    if (lines === editor.gutterLines) return;
    editor.gutterLines = lines;
    const numbers = [];
    for (let index = 1; index <= lines; index += 1) numbers.push(String(index));
    editor.gutter.textContent = numbers.join("\n");
  }

  function syncEscHint(editor) {
    editor.escHint.hidden = !editor.escMode;
  }

  function setEditorValue(editor, value) {
    if (!editor) return;
    editor.area.value = value;
    editor.accepted = value;
    editor.limitNote.hidden = true;
    refreshGutter(editor);
  }

  function createEditor(key, label) {
    const editor = { key, escMode: false, accepted: "", gutterLines: 0 };
    const wrap = el("div", "sc-editor");
    const gutter = el("div", "sc-gutter");
    gutter.setAttribute("aria-hidden", "true");
    const area = document.createElement("textarea");
    area.className = "sc-text";
    area.setAttribute("wrap", "off"); // 长行在编辑区内部横向滚动，页面不出现横向滚动条
    area.setAttribute("spellcheck", "false");
    area.setAttribute("aria-label", label);
    area.setAttribute("autocapitalize", "off");
    editor.gutter = gutter;
    editor.area = area;

    const limitNote = el("p", "sc-note is-error", `最多 ${MAX_LINES} 行，超出的部分没有输入。`);
    limitNote.hidden = true;
    limitNote.setAttribute("role", "alert");
    editor.limitNote = limitNote;

    const escHint = el("p", "sc-note", "已切换：按 Tab 移出编辑框；再按 Esc 恢复 Tab 缩进。");
    escHint.hidden = true;
    editor.escHint = escHint;

    area.addEventListener("scroll", () => {
      gutter.scrollTop = area.scrollTop; // 行号列与文本滚动同步
    });
    area.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        editor.escMode = !editor.escMode; // Esc 之后 Tab 恢复为离开编辑框
        syncEscHint(editor);
        event.preventDefault();
        return;
      }
      if (event.key === "Tab" && !editor.escMode) {
        event.preventDefault();
        const start = area.selectionStart ?? area.value.length;
        const end = area.selectionEnd ?? start;
        area.value = `${area.value.slice(0, start)}    ${area.value.slice(end)}`;
        area.setSelectionRange(start + 4, start + 4);
        handleAreaInput(editor);
      }
    });
    area.addEventListener("blur", () => {
      if (editor.escMode) {
        editor.escMode = false;
        syncEscHint(editor);
      }
    });
    area.addEventListener("input", () => handleAreaInput(editor));

    wrap.append(gutter, area);
    const block = el("div", "sc-editor-block");
    block.append(wrap, escHint, limitNote);
    editor.root = block;
    refreshGutter(editor);
    return editor;
  }

  function handleAreaInput(editor) {
    if (countLines(editor.area.value) > MAX_LINES) {
      editor.area.value = editor.accepted; // 超过 2000 行：阻止输入并提示
      editor.limitNote.hidden = false;
      refreshGutter(editor);
      return;
    }
    editor.limitNote.hidden = true;
    editor.accepted = editor.area.value;
    refreshGutter(editor);
    const proposed = snapshot();
    if (serializedSize(proposed) > SIZE_LIMIT) {
      setEditorValue(editor, lastGood[editor.key]); // 合计超过大小上限：回退这次输入
      sizeNote.hidden = false;
      return;
    }
    sizeNote.hidden = true;
    lastGood = proposed;
    renderDiff(); // 两侧任一编辑都刷新对比
    markDirty();
  }

  /* ---------- 标签页 ---------- */
  const TABS = [
    { key: "code", label: "代码草稿" },
    { key: "diff", label: "对比" },
    { key: "table", label: "演算表" },
  ];
  const tabButtons = new Map();
  const tabPanels = new Map();

  function selectTab(key, { focus = false, save = true } = {}) {
    if (!tabButtons.has(key)) return;
    activeTab = key;
    for (const tab of TABS) {
      const isActive = tab.key === key;
      const tabButton = tabButtons.get(tab.key);
      tabButton.setAttribute("aria-selected", String(isActive));
      tabButton.tabIndex = isActive ? 0 : -1;
      tabPanels.get(tab.key).hidden = !isActive;
    }
    if (focus) tabButtons.get(key).focus();
    if (save) flushNow(); // 切换标签时立即保存未保存的内容
  }

  function onTablistKeydown(event) {
    const keys = ["ArrowLeft", "ArrowRight", "Home", "End"];
    if (!keys.includes(event.key)) return;
    event.preventDefault();
    const index = TABS.findIndex((tab) => tab.key === activeTab);
    let next = index;
    if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
    else if (event.key === "ArrowLeft") next = (index + TABS.length - 1) % TABS.length;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = TABS.length - 1;
    selectTab(TABS[next].key, { focus: true });
  }

  /* ---------- 对比标签 ---------- */
  let diffSummary = null;
  let diffList = null;
  let diffTooLarge = null;

  function renderDiff() {
    if (!diffList || !window.DiffLines) return;
    const result = window.DiffLines.diffLines(
      editors.code ? editors.code.area.value : "",
      editors.fixed ? editors.fixed.area.value : "",
    );
    diffList.replaceChildren();
    if (result.tooLarge) {
      diffSummary.textContent = "";
      diffTooLarge.hidden = false;
      return;
    }
    diffTooLarge.hidden = true;
    const stats = window.DiffLines.summarize(result);
    diffSummary.textContent = `+${stats.added} −${stats.removed}`;
    const SIGNS = { added: "+", removed: "−", same: " " };
    const NAMES = { added: "新增", removed: "删除", same: "相同" };
    for (const row of result) {
      const line = el("div", `sc-diff-row is-${row.type}`);
      line.setAttribute(
        "aria-label",
        `${NAMES[row.type]}：${row.oldLine == null ? "" : `旧第 ${row.oldLine} 行 `}${row.newLine == null ? "" : `新第 ${row.newLine} 行`}`.trim(),
      );
      const sign = el("span", "sc-diff-sign", SIGNS[row.type]);
      sign.setAttribute("aria-hidden", "true");
      line.append(
        el("span", "sc-diff-old", row.oldLine == null ? "" : String(row.oldLine)),
        el("span", "sc-diff-new", row.newLine == null ? "" : String(row.newLine)),
        sign,
        el("span", "sc-diff-text", row.text),
      );
      diffList.append(line);
    }
  }

  /* ---------- 演算表标签 ---------- */
  let tableWrap = null;
  let tableNote = null;
  let pasteNote = null;
  let tableButtons = null;

  function tableData() {
    return tableModel && trace() ? trace().toJSON(tableModel) : { cols: [], rows: [] };
  }

  /** 纯函数给出的新模型先过大小上限，过了才提交。 */
  function commitTableModel(nextModel) {
    if (!nextModel) return false;
    const proposed = {
      code: editors.code ? editors.code.area.value : "",
      fixed: editors.fixed ? editors.fixed.area.value : "",
      table: trace().toJSON(nextModel),
    };
    if (serializedSize(proposed) > SIZE_LIMIT) {
      sizeNote.hidden = false;
      renderTable(); // 恢复显示为旧模型
      return false;
    }
    sizeNote.hidden = true;
    tableModel = nextModel;
    lastGood = snapshot();
    markDirty();
    return true;
  }

  function updateTableButtons() {
    if (!tableButtons) return;
    const data = tableData();
    const setState = (node, disabled, note) => {
      node.disabled = disabled;
      node.setAttribute("aria-disabled", String(disabled));
      node.title = disabled ? note : "";
    };
    setState(tableButtons.addRow, data.rows.length >= TABLE_MAX_ROWS, `最多 ${TABLE_MAX_ROWS} 行`);
    setState(tableButtons.removeRow, data.rows.length <= 1, "至少保留 1 行");
    setState(tableButtons.addCol, data.cols.length >= TABLE_MAX_COLS, `最多 ${TABLE_MAX_COLS} 列`);
    setState(tableButtons.removeCol, data.cols.length <= 1, "至少保留 1 列");
    tableNote.textContent = data.rows.length >= TABLE_MAX_ROWS
      ? `已到上限：最多 ${TABLE_MAX_ROWS} 行。`
      : data.cols.length >= TABLE_MAX_COLS
        ? `已到上限：最多 ${TABLE_MAX_COLS} 列。`
        : `方向键在单元格间移动，Enter 向下。${data.rows.length} 行 × ${data.cols.length} 列。`;
  }

  function focusCell(row, col) {
    const target = tableCells[row] && tableCells[row][col];
    if (target) target.focus();
  }

  function onCellKeydown(event, row, col) {
    const data = tableData();
    let nextRow = row;
    let nextCol = col;
    if (event.key === "ArrowRight") nextCol = col + 1;
    else if (event.key === "ArrowLeft") nextCol = col - 1;
    else if (event.key === "ArrowUp") nextRow = row - 1;
    else if (event.key === "ArrowDown" || event.key === "Enter") nextRow = row + 1;
    else return;
    if (nextRow < 0 || nextRow >= data.rows.length || nextCol < 0 || nextCol >= data.cols.length) return;
    event.preventDefault();
    focusCell(nextRow, nextCol);
  }

  function onCellPaste(event, row, col) {
    const text = event.clipboardData ? event.clipboardData.getData("text") : "";
    if (!text || (!text.includes("\n") && !text.includes("\t") && !text.includes("\r"))) return;
    event.preventDefault(); // 多行文本走 pasteGrid，拆进网格
    const result = trace().pasteGrid(tableModel, row, col, text);
    if (!result || !result.model) return;
    if (commitTableModel(result.model)) {
      pasteNote.hidden = !result.truncated;
      renderTable();
      focusCell(row, col);
    }
  }

  function renderTable() {
    if (!tableWrap || !trace() || !tableModel) return;
    const data = tableData();
    tableCells = [];
    tableHeaders = [];
    const table = el("table", "sc-grid");
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    data.cols.forEach((name, col) => {
      const th = document.createElement("th");
      th.scope = "col";
      const input = document.createElement("input");
      input.className = "sc-grid-head";
      input.value = String(name ?? "");
      input.setAttribute("aria-label", `第 ${col + 1} 列列名`);
      input.addEventListener("input", () => {
        const next = trace().setHeader(tableModel, col, input.value);
        if (commitTableModel(next)) pasteNote.hidden = true;
        else input.value = String(tableData().cols[col] ?? "");
      });
      tableHeaders[col] = input;
      th.append(input);
      headRow.append(th);
    });
    head.append(headRow);
    const body = document.createElement("tbody");
    data.rows.forEach((cells, row) => {
      const tr = document.createElement("tr");
      tableCells[row] = [];
      data.cols.forEach((_name, col) => {
        const td = document.createElement("td");
        const input = document.createElement("input");
        input.className = "sc-grid-cell";
        input.value = String(cells[col] ?? "");
        input.setAttribute("aria-label", `第 ${row + 1} 行第 ${col + 1} 列`);
        input.addEventListener("input", () => {
          const next = trace().setCell(tableModel, row, col, input.value);
          if (!commitTableModel(next)) input.value = String(tableData().rows[row][col] ?? "");
        });
        input.addEventListener("keydown", (event) => onCellKeydown(event, row, col));
        input.addEventListener("paste", (event) => onCellPaste(event, row, col));
        tableCells[row][col] = input;
        td.append(input);
        tr.append(td);
      });
      body.append(tr);
    });
    table.append(head, body);
    tableWrap.replaceChildren(table);
    updateTableButtons();
  }

  function buildTablePanel(panel) {
    if (!trace()) {
      panel.append(el("p", "sc-note", "演算表暂不可用。"));
      return;
    }
    const toolbar = el("div", "sc-grid-tools");
    tableButtons = {
      addRow: button("加行", "sc-tool", () => {
        if (commitTableModel(trace().addRow(tableModel, tableData().rows.length))) renderTable();
      }, "在末尾增加一行"),
      removeRow: button("删行", "sc-tool", () => {
        if (commitTableModel(trace().removeRow(tableModel, tableData().rows.length - 1))) renderTable();
      }, "删除最后一行"),
      addCol: button("加列", "sc-tool", () => {
        if (commitTableModel(trace().addCol(tableModel, tableData().cols.length))) renderTable();
      }, "在末尾增加一列"),
      removeCol: button("删列", "sc-tool", () => {
        if (commitTableModel(trace().removeCol(tableModel, tableData().cols.length - 1))) renderTable();
      }, "删除最后一列"),
    };
    toolbar.append(tableButtons.addRow, tableButtons.removeRow, tableButtons.addCol, tableButtons.removeCol);
    tableNote = el("p", "sc-note", "");
    pasteNote = el("p", "sc-note", "内容太多，粘贴时只放进了能放得下的部分。");
    pasteNote.hidden = true;
    tableWrap = el("div", "sc-grid-wrap");
    panel.append(toolbar, tableNote, pasteNote, tableWrap);
  }

  /* ---------- 组装面板 ---------- */
  function build() {
    root = el("section", "sc-panel");
    root.dataset.state = state;
    const tablist = el("div", "sc-tabs");
    tablist.setAttribute("role", "tablist");
    tablist.setAttribute("aria-label", "草稿演算区");
    tablist.addEventListener("keydown", onTablistKeydown);
    const panels = el("div", "sc-panels");
    for (const tab of TABS) {
      const tabButton = button(tab.label, "sc-tab", () => selectTab(tab.key), `${tab.label}标签`);
      tabButton.id = `sc-tab-${tab.key}`;
      tabButton.setAttribute("role", "tab");
      tabButton.setAttribute("aria-controls", `sc-panel-${tab.key}`);
      tabButtons.set(tab.key, tabButton);
      tablist.append(tabButton);
      const panel = el("div", "sc-page");
      panel.id = `sc-panel-${tab.key}`;
      panel.setAttribute("role", "tabpanel");
      panel.setAttribute("aria-labelledby", tabButton.id);
      tabPanels.set(tab.key, panel);
      panels.append(panel);
    }
    statusBar = el("p", "sc-status");
    statusBar.setAttribute("role", "status");
    sizeNote = el("p", "sc-note is-error", `内容合计最多 ${SIZE_LIMIT} 字符，超出的输入没有生效。`);
    sizeNote.hidden = true;
    sizeNote.setAttribute("role", "alert");

    // 代码草稿
    editors.code = createEditor("code", "代码草稿");
    tabPanels.get("code").append(editors.code.root);

    // 对比：错误代码（代码草稿） vs 修正代码（可编辑）
    diffSummary = el("p", "sc-diff-summary", "");
    diffTooLarge = el("p", "sc-note", "代码太长了，超过对比能处理的范围，暂时没有显示差异。");
    diffTooLarge.hidden = true;
    diffList = el("div", "sc-diff-list");
    diffList.setAttribute("role", "table");
    diffList.setAttribute("aria-label", "错误代码与修正代码的逐行对比");
    editors.fixed = createEditor("fixed", "修正代码");
    tabPanels.get("diff").append(
      diffSummary, diffTooLarge, diffList,
      el("h4", "sc-sub", "修正代码（可编辑）"), editors.fixed.root,
    );

    // 演算表
    buildTablePanel(tabPanels.get("table"));

    root.append(tablist, statusBar, sizeNote, panels);
    selectTab("code", { save: false });
    updateStatus();
  }

  /* ---------- 加载 ---------- */
  async function load(mark) {
    try {
      const data = await hooks.api(`/api/mistakes/${mistakeId}/scratch`);
      if (!alive(mark)) return;
      version = Number(data.version) || 0;
      setEditorValue(editors.code, typeof data.code === "string" ? data.code : "");
      setEditorValue(editors.fixed, typeof data.fixed === "string" ? data.fixed : "");
      if (trace()) {
        tableModel = data.table ? trace().validate(data.table) : null;
        if (!tableModel) tableModel = trace().create(DEFAULT_COLS, DEFAULT_ROWS);
        renderTable();
      }
      savedAt = typeof data.updated_at === "string" ? data.updated_at : "";
      lastGood = snapshot();
      state = version > 0 && savedAt ? "saved" : "idle";
      updateStatus();
      renderDiff();
    } catch (error) {
      if (!alive(mark)) return;
      statusBar.replaceChildren();
      const wrap = el("span", "sc-status-text is-error");
      wrap.setAttribute("role", "alert");
      wrap.append(
        el("span", "", `草稿加载失败：${error?.message || "请稍后重试"} `),
        button("重试", "sc-retry", () => load(ticket()), "重新加载草稿"),
      );
      statusBar.append(wrap);
    }
  }

  /* ---------- 页面隐藏时立即保存 ---------- */
  function onVisibilityChange() {
    if (document.hidden) flushNow();
  }

  /* ---------- 对外接口 ---------- */
  function mount(container, mistake) {
    if (!hooks?.api || !container || !mistake || mistake.id === undefined) return;
    if (root) unmount();
    host = container;
    mistakeId = mistake.id;
    state = "idle";
    version = 0;
    savedAt = "";
    conflictCurrent = null;
    tableModel = null;
    pendingWhileSaving = false;
    savingNow = false;
    lastGood = { code: "", fixed: "", table: null };
    tabButtons.clear();
    tabPanels.clear();
    build();
    host.append(root);
    document.addEventListener("visibilitychange", onVisibilityChange);
    load(ticket());
  }

  function unmount() {
    if (!root) return;
    flushNow(); // 关闭面板时立即保存未保存的内容（请求照常发出，响应会被丢弃）
    generation += 1;
    window.clearTimeout(saveTimer);
    saveTimer = 0;
    document.removeEventListener("visibilitychange", onVisibilityChange);
    root.remove();
    root = null;
    host = null;
    mistakeId = null;
    statusBar = null;
    sizeNote = null;
    diffSummary = null;
    diffList = null;
    diffTooLarge = null;
    tableWrap = null;
    tableNote = null;
    pasteNote = null;
    tableButtons = null;
    tableCells = [];
    tableHeaders = [];
    editors.code = null;
    editors.fixed = null;
    tableModel = null;
    tabButtons.clear();
    tabPanels.clear();
    savingNow = false;
    pendingWhileSaving = false;
    state = "idle";
  }

  function reset() {
    unmount(); // 清掉计时器、监听器、DOM；generation +1 使未完成请求的影响全部作废
    conflictCurrent = null;
    version = 0;
    savedAt = "";
    lastGood = { code: "", fixed: "", table: null };
  }

  window.Scratch = {
    configure(options) {
      hooks = options || null;
    },
    mount,
    unmount,
    reset,
    flush: flushNow,
    debounceMs: 800,
    state: () => state,
    limits: { maxLines: MAX_LINES, size: SIZE_LIMIT, tableCols: TABLE_MAX_COLS, tableRows: TABLE_MAX_ROWS },
  };
})();
