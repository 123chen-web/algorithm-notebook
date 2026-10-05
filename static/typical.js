"use strict";

/* 错因专题页顶部的"我的三大典型失误"卡与"考前一页纸"打印视图。
   请求走宿主注入的 hooks.api()；每个响应都按登录代次 / 用户 id / 当前视图 / 请求序号校验，
   晚到的响应直接丢弃；所有来自服务器的文字都用 textContent，不拼 HTML。 */
(() => {
  const $ = (selector) => document.querySelector(selector);
  const card = $("#typical-card");
  const guide = $("#typical-guide");
  const statusLine = $("#typical-status");
  const list = $("#typical-list");
  const printOpen = $("#typical-print-open");
  const sheetWrap = $("#typical-sheet-wrap");
  const sheet = $("#typical-sheet");
  const sheetPrint = $("#typical-sheet-print");
  const sheetClose = $("#typical-sheet-close");
  if (!card || !list || !sheet) return;

  let hooks = null;
  let generation = 0;
  let sequence = 0;
  let lastReport = null;

  function configure(next) {
    hooks = next;
  }

  function reset() {
    generation += 1;
    sequence = 0;
    lastReport = null;
    card.hidden = true;
    guide.hidden = true;
    statusLine.textContent = "";
    list.replaceChildren();
    printOpen.hidden = true;
    sheetWrap.hidden = true;
    sheet.replaceChildren();
    document.documentElement.classList.remove("typical-sheet-open");
  }

  function current(ticket) {
    const user = hooks?.getUser();
    return Boolean(hooks && user)
      && ticket.epoch === hooks.getEpoch()
      && ticket.userId === user.id
      && ticket.generation === generation
      && ticket.sequence === sequence
      && hooks.getView() === "clusters";
  }

  /** ISO 时间（服务端存 UTC）只取日期部分，按"9 月 15 日"呈现，避免时区换算出错。 */
  function formatDate(value) {
    const match = typeof value === "string" && value.match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (!match) return "";
    return `${Number(match[2])} 月 ${Number(match[3])} 日`;
  }

  function text(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    node.textContent = value;
    return node;
  }

  function rowFor(item) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "typical-row";
    row.dataset.tag = item.key;
    row.setAttribute("aria-label", `查看带「${item.name}」标签的记录`);
    const head = document.createElement("span");
    head.className = "typical-row-head";
    head.append(
      text("span", "typical-row-name", item.name),
      text("span", "typical-row-count", `${Number(item.count) || 0} 条`),
    );
    row.append(head);
    const when = formatDate(item.last_wrong_at);
    if (when) row.append(text("span", "typical-row-meta", `最近出错 ${when}`));
    if (item.hint) row.append(text("span", "typical-row-hint", item.hint));
    row.addEventListener("click", () => {
      document.dispatchEvent(new CustomEvent("records:filter", { detail: { tag: item.key } }));
    });
    return row;
  }

  function render(report) {
    list.replaceChildren();
    const items = Array.isArray(report?.items) ? report.items : [];
    if (!report?.enough) {
      guide.hidden = false;
      guide.textContent = "再记录几条易错点、复习几次并给它们贴上错因标签，这里会自动整理出你最该补的三类失误。";
      statusLine.textContent = "";
      printOpen.hidden = true;
      return;
    }
    guide.hidden = true;
    if (!items.length) {
      statusLine.textContent = "暂时没有反复出错的类型，保持这个状态！";
      printOpen.hidden = true;
      return;
    }
    statusLine.textContent = "";
    for (const item of items) list.append(rowFor(item));
    printOpen.hidden = false;
  }

  async function mount(container) {
    const root = container || card;
    if (!root || root !== card) return;
    const user = hooks?.getUser();
    if (!hooks || !user || hooks.getView() !== "clusters") {
      card.hidden = true;
      return;
    }
    generation += 1;
    const ticket = { generation, sequence: ++sequence, epoch: hooks.getEpoch(), userId: user.id };
    card.hidden = false;
    let report;
    try {
      report = await hooks.api("/api/stats/typical");
    } catch {
      if (current(ticket)) card.hidden = true;
      return;
    }
    if (!current(ticket)) return;
    lastReport = report;
    render(report);
  }

  /* ---------- 考前一页纸 ---------- */

  function buildSheet(report) {
    sheet.replaceChildren();
    sheet.append(text("h2", "typical-sheet-title", "考前一页纸 · 我的三大典型失误"));
    sheet.append(text("p", "typical-sheet-sub", "考前只看这一页：先认失误类型，再看代表错题，最后逐条自查。"));
    for (const item of report.items) {
      const section = document.createElement("section");
      section.className = "typical-sheet-category";
      const heading = document.createElement("h3");
      heading.append(
        text("span", "typical-sheet-name", item.name),
        text("span", "typical-sheet-count", `${Number(item.count) || 0} 条`),
      );
      section.append(heading);
      if (item.hint) section.append(text("p", "typical-sheet-hint", item.hint));
      const examples = document.createElement("ol");
      for (const record of (item.ids || []).slice(0, 2)) {
        const li = document.createElement("li");
        li.className = "typical-sheet-example";
        li.append(text("span", "typical-sheet-q", record.title || "未命名题目"));
        const why = (record.description || "").trim();
        if (why) li.append(text("span", "typical-sheet-why", why));
        examples.append(li);
      }
      section.append(examples);
      sheet.append(section);
    }
    const checklist = document.createElement("section");
    checklist.className = "typical-sheet-checklist";
    checklist.append(text("h3", "", "考前自查清单"));
    const ul = document.createElement("ul");
    for (const line of report.checklist || []) {
      ul.append(text("li", "typical-sheet-check", `☐ ${line}`));
    }
    checklist.append(ul);
    sheet.append(checklist);
  }

  function openSheet() {
    const items = Array.isArray(lastReport?.items) ? lastReport.items : [];
    if (!lastReport?.enough || !items.length) return;
    buildSheet(lastReport);
    sheetWrap.hidden = false;
    document.documentElement.classList.add("typical-sheet-open");
    sheetWrap.scrollIntoView({ block: "start" });
  }

  function closeSheet() {
    sheetWrap.hidden = true;
    document.documentElement.classList.remove("typical-sheet-open");
  }

  printOpen.addEventListener("click", openSheet);
  sheetClose.addEventListener("click", closeSheet);
  sheetPrint.addEventListener("click", () => {
    if (typeof hooks?.print === "function") hooks.print();
    else if (typeof window.print === "function") window.print();
  });

  // 记录有增删改时（新增 / 复习 / 编辑标签），在专题页上自动刷新。
  document.addEventListener("app:data-changed", () => {
    const user = hooks?.getUser();
    if (hooks && user && hooks.getView() === "clusters") mount(card);
  });

  window.Typical = { configure, mount, reset };
})();
