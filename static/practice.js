"use strict";

/* 「现在就练 5 条」：薄弱点分析页与错因专题页共用的取题规则和按钮。
   对外契约：window.PracticeNow = { LIMIT, pickDueIds, fill, slot, setDue, reset }。
   只走现有入口：FocusReview.start({ ids })；没有到期的就禁用按钮并给出"去看看这一块的记录"（records:filter）。
   所有文字一律 textContent。 */
(() => {
  const LIMIT = 5;
  const DAY = /^\d{4}-\d{2}-\d{2}$/;

  let epoch = 0; // 只在登出/换账号时加一，用来丢弃旧账号留下的按钮和到期清单
  let due = null; // { items, today }：GET /api/mistakes?due_only=true 的结果；null 表示还没读到
  const slots = new Set();

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  /** 从 items 里挑最多 limit 条"今天已到期"的易错点 id，按到期日升序（同日按 id）。
      zone：只要这个分区；ids：只要这些 id（两者都给时取交集）；today 缺省则信任调用方已经只传了到期项。
      条目的 id 字段兼容 mistake_id（专题成员）与 id（/api/mistakes）。 */
  function pickDueIds(items, { zone, ids, today, limit = LIMIT } = {}) {
    if (!Array.isArray(items)) return [];
    const wanted = ids === undefined ? null : new Set(ids);
    const seen = new Set();
    const picked = [];
    for (const item of items) {
      if (!item || typeof item !== "object") continue;
      const id = item.mistake_id ?? item.id;
      if (!Number.isInteger(id) || seen.has(id)) continue;
      if (zone !== undefined && item.zone !== zone) continue;
      if (wanted && !wanted.has(id)) continue;
      if (typeof item.due_date !== "string" || !DAY.test(item.due_date)) continue;
      if (today !== undefined && item.due_date > today) continue;
      seen.add(id);
      picked.push({ id, due: item.due_date });
    }
    picked.sort((a, b) => (a.due < b.due ? -1 : a.due > b.due ? 1 : a.id - b.id));
    return picked.slice(0, Math.max(0, limit)).map((entry) => entry.id);
  }

  function canStart() {
    return typeof window.FocusReview?.start === "function";
  }

  /** 把按钮画进 box。spec：ids（已挑好的）、label（有 id 时的按钮文字）、emptyText、
      link（{ zone } 时在禁用态旁给出"去看看这一块的记录"）、guard()（点击时仍返回 true 才开始）、
      buttonClass（调用方自己的样式类）。 */
  function fill(box, { ids, label, emptyText, link, guard, buttonClass } = {}) {
    box.replaceChildren();
    if (!canStart()) {
      box.hidden = true;
      return;
    }
    box.hidden = false;
    const list = Array.isArray(ids) ? ids.slice(0, LIMIT) : [];
    if (list.length) {
      const button = node("button", `practice-now-button${buttonClass ? ` ${buttonClass}` : ""}`, label || `现在就练 ${list.length} 条`);
      button.type = "button";
      button.addEventListener("click", () => {
        if (guard && !guard()) return;
        if (canStart()) window.FocusReview.start({ ids: list.slice() });
      });
      box.append(button);
      return;
    }
    const button = node("button", `practice-now-button is-empty${buttonClass ? ` ${buttonClass}` : ""}`, label || "现在就练 5 条");
    button.type = "button";
    button.disabled = true;
    const note = node("span", "practice-now-note", emptyText || "这一块今天没有要复习的");
    box.append(button, note);
    if (link) {
      const view = node("button", "practice-now-link", "去看看这一块的记录");
      view.type = "button";
      view.addEventListener("click", () => {
        if (guard && !guard()) return;
        document.dispatchEvent(new CustomEvent("records:filter", { detail: { zone: link.zone || "" } }));
      });
      box.append(view);
    }
  }

  function refill(entry) {
    const { box, spec } = entry;
    if (entry.epoch !== epoch || !due) return;
    fill(box, {
      ids: pickDueIds(due.items, { zone: spec.zone, ids: spec.ids, today: due.today }),
      emptyText: spec.emptyText,
      link: spec.link,
      guard: () => entry.epoch === epoch,
    });
  }

  /** 先建一个空位；到期清单读到后（setDue）才填，没读到就一直不显示（拿不到数据不假装有按钮）。 */
  function slot(spec = {}) {
    const box = node("div", "practice-now");
    box.hidden = true;
    for (const old of [...slots]) if (old.epoch !== epoch || old.box.isConnected === false) slots.delete(old);
    const entry = { box, spec, epoch };
    slots.add(entry);
    refill(entry);
    return box;
  }

  function setDue(items, today) {
    if (!Array.isArray(items) || typeof today !== "string" || !DAY.test(today)) return;
    due = { items, today };
    for (const entry of [...slots]) refill(entry);
  }

  function reset() {
    epoch += 1;
    due = null;
    slots.clear();
  }

  window.PracticeNow = { LIMIT, pickDueIds, fill, slot, setDue, reset };
})();
