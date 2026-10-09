"use strict";

/* 列表只按 problem_id 合并展示，不修改评分/离线队列的逐条结构。
 * progress 只接受后端的 5% 档位。旧离线缓存没有元数据时显示未知，绝不推算。
 * 成熟口径：未暂停易错点 min(1, interval_days/21)，未复习为0；整题平均并四舍五入到5%。 */
(() => {
  function node(tag, className, text) {
    const el = document.createElement(tag);
    if (className) el.className = className;
    if (text !== undefined) el.textContent = text;
    return el;
  }

  function progress(value) {
    return Number.isInteger(value) && value >= 0 && value <= 100 && value % 5 === 0 ? value : null;
  }

  function uniqueTags(tags) {
    const seen = new Set();
    return tags.filter(tag => {
      if (typeof tag !== "string") return false;
      const key = tag.replace(/[A-Z]/g, letter => letter.toLowerCase());
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function group(items, today) {
    const groups = new Map();
    for (const item of items) {
      const key = item.problem_id ?? item.mistake_id ?? item.id ?? item;
      if (!groups.has(key)) groups.set(key, { problemId: key, item, members: [] });
      groups.get(key).members.push(item);
    }
    return Array.from(groups.values(), entry => {
      const { item, members } = entry;
      const metadata = members.find(member => Array.isArray(member.problem_tags));
      return { ...entry, tags: uniqueTags(metadata ? metadata.problem_tags : members.flatMap(m => m.tags || [])),
        progress: progress(item.progress),
        dueCount: Number.isInteger(item.problem_due_count) ? item.problem_due_count
          : members.filter(m => !m.suspended_at && (!today || m.due_date <= today)).length };
    });
  }

  function card(entry, { onOpen, selected = false } = {}) {
    const { item, members } = entry;
    const root = node("section", "problem-record-card problem-progress-card");
    root.dataset.problemId = String(entry.problemId);
    if (entry.progress !== null) root.dataset.progress = String(entry.progress);
    const open = node("button", "record-button problem-card-open");
    open.type = "button";
    open.dataset.id = String(item.mistake_id ?? item.id);
    open.classList.toggle("selected", selected);
    open.setAttribute("aria-pressed", String(selected));
    const heading = node("h3", "", item.title);
    const total = item.problem_mistakes?.length || members.length;
    const summary = `待复习 ${entry.dueCount} 条 · 共 ${total} 条易错点`;
    open.append(heading, node("span", "problem-card-zone", item.zone), node("small", "problem-card-summary", summary));
    if (members.every(m => m.suspended_at)) open.append(node("span", "review-suspended", "已暂停"));
    if (members.some(m => m.pending_reason)) open.append(node("span", "muted", "错因待补"));
    open.addEventListener("click", () => onOpen?.(item));
    const label = entry.progress === null ? "完成度待联网更新" : `复习完成度 ${entry.progress}%`;
    const meter = node("span", "problem-progress-label", entry.progress === null ? "—" : `${entry.progress}%`);
    meter.setAttribute("role", "img");
    meter.setAttribute("aria-label", label);
    open.append(meter);
    root.append(open);
    if (entry.tags.length) {
      const chips = node("div", "problem-card-tags");
      chips.setAttribute("aria-label", "这道题的全部标签");
      const tags = entry.tags.map((tag, index) => {
        const chip = node("span", "problem-chip", tag);
        chip.hidden = index >= 5;
        chips.append(chip);
        return chip;
      });
      if (tags.length > 5) {
        const more = node("button", "problem-tags-more", `+${tags.length - 5}`);
        more.type = "button";
        more.setAttribute("aria-expanded", "false");
        more.setAttribute("aria-label", `展开其余 ${tags.length - 5} 个标签`);
        let expanded = false;
        more.addEventListener("click", () => {
          expanded = !expanded;
          tags.forEach((tag, index) => { tag.hidden = !expanded && index >= 5; });
          more.textContent = expanded ? "收起" : `+${tags.length - 5}`;
          more.setAttribute("aria-expanded", String(expanded));
          more.setAttribute("aria-label", expanded ? "收起标签" : `展开其余 ${tags.length - 5} 个标签`);
        });
        chips.append(more);
      }
      root.append(chips);
    }
    return root;
  }

  function position(item, queue) {
    const members = queue.filter(m => (m.problem_id ?? m.id) === (item.problem_id ?? item.id));
    const index = members.findIndex(m => m.id === item.id);
    return index < 0 ? "" : `这道题 第 ${index + 1}/${members.length} 条`;
  }

  // 切换发生在逐条详情里；暂停、推迟、撤销和评分仍使用选中易错点的原入口。
  function navigation(item, onOpen) {
    const members = item.problem_mistakes || [];
    const root = node("div", "problem-mistake-navigation");
    if (!members.length) return root;
    const due = members.filter(m => !m.suspended_at && m.due_date <= item.today);
    const shown = due.some(m => m.id === item.id) ? due : members;
    root.append(node("p", "problem-position", item.problem_position || position(item, shown.map(m => ({ ...m, problem_id: item.problem_id })))));
    if (members.length > 1) {
      const label = node("label", "", "切换这道题的易错点");
      const select = node("select");
      for (const [index, member] of members.entries()) {
        const option = node("option", "", `第 ${index + 1} 条${member.suspended_at ? " · 已暂停" : ""}`);
        option.value = String(member.id);
        select.append(option);
      }
      select.value = String(item.id);
      select.addEventListener("change", () => onOpen(Number(select.value)));
      label.append(select);
      root.append(label);
    }
    return root;
  }

  window.ProblemCards = { group, card, progress, position, navigation };
})();
