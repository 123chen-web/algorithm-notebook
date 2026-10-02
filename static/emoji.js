"use strict";

/* 表情选择面板：给文本框"插入表情"（评论、回复、发帖、编辑都用同一个面板）。
   对外契约：window.EmojiPicker = { attach(textarea, { container }) → trigger 按钮, close() }。
   同一时间只会打开一个面板；表情按钮用方向键漫游，Esc 关闭并把焦点还给文本框。
   表情只是普通文字（可能带 U+FE0F / U+200D），后端原样保存，不做任何过滤。 */
(() => {
  const RECENT_KEY = "algorithm-notebook-recent-emoji";
  const RECENT_LIMIT = 16;
  const GROUPS = [
    { id: "common", label: "常用", items: [
      ["👍", "赞"], ["👏", "鼓掌"], ["🙏", "拜托"], ["💪", "加油"], ["🎉", "庆祝"], ["🔥", "火"],
      ["✅", "搞定"], ["❤️", "红心"], ["😂", "笑哭"], ["🤔", "思考"], ["😭", "大哭"], ["👀", "围观"],
    ] },
    { id: "face", label: "表情", items: [
      ["😀", "开心"], ["😁", "露齿笑"], ["🤣", "笑倒"], ["😊", "微笑"], ["😍", "花痴"], ["😘", "飞吻"],
      ["😎", "墨镜"], ["🤓", "学霸"], ["🤩", "惊艳"], ["🥳", "派对"], ["😅", "汗颜"], ["😢", "流泪"],
      ["😤", "不服气"], ["😡", "生气"], ["🤯", "脑洞炸裂"], ["😱", "惊恐"], ["😴", "困了"], ["🙄", "白眼"],
      ["😬", "尴尬"], ["😇", "天使"], ["🙃", "倒脸"], ["😏", "得意"], ["🤗", "拥抱"], ["🤭", "偷笑"],
      ["🤫", "嘘"], ["🥱", "哈欠"], ["🤒", "生病"], ["😷", "口罩"],
    ] },
    { id: "hand", label: "手势", items: [
      ["👎", "踩"], ["🙌", "欢呼"], ["👌", "好的"], ["✌️", "耶"], ["🤞", "祈祷"], ["🤝", "握手"],
      ["👋", "挥手"], ["✍️", "写字"],
    ] },
    { id: "study", label: "学习", items: [
      ["📚", "书本"], ["📖", "阅读"], ["✏️", "铅笔"], ["📝", "笔记"], ["💡", "灵感"], ["🧠", "大脑"],
      ["🎯", "命中"], ["⏰", "闹钟"], ["🔍", "放大镜"], ["🧩", "拼图"], ["💻", "电脑"], ["🐛", "bug"],
      ["🔧", "扳手"], ["📌", "图钉"], ["📈", "上升"], ["🏆", "奖杯"], ["⭐", "星星"], ["✨", "闪光"],
      ["🚀", "火箭"], ["💯", "满分"],
    ] },
    { id: "mood", label: "心情", items: [
      ["🎊", "彩球"], ["🥇", "金牌"], ["💙", "蓝心"], ["💚", "绿心"], ["💛", "黄心"], ["🧡", "橙心"],
      ["💜", "紫心"], ["☕", "咖啡"], ["🍵", "热茶"], ["🌱", "萌芽"], ["🌈", "彩虹"], ["🍀", "四叶草"],
    ] },
  ];
  const NAMES = new Map(GROUPS.flatMap((group) => group.items));

  let popover = null;
  let status = null;
  let active = null; // { textarea, trigger, selection }

  function node(tag, className, text) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (text !== undefined) item.textContent = text;
    return item;
  }

  /* ---------- 最近使用 ---------- */
  function readRecent() {
    try {
      const saved = JSON.parse(window.localStorage.getItem(RECENT_KEY) || "[]");
      return Array.isArray(saved) ? saved.filter((item) => NAMES.has(item)).slice(0, RECENT_LIMIT) : [];
    } catch {
      return [];
    }
  }
  function remember(emoji) {
    try {
      const next = [emoji, ...readRecent().filter((item) => item !== emoji)].slice(0, RECENT_LIMIT);
      window.localStorage.setItem(RECENT_KEY, JSON.stringify(next));
    } catch {
      // 隐私模式等拿不到存储时，只是少了"最近使用"，不影响插入。
    }
  }

  /* ---------- 插入 ---------- */
  function insert(textarea, text, selection) {
    const start = Math.min(selection?.start ?? textarea.selectionStart ?? textarea.value.length, textarea.value.length);
    const end = Math.min(selection?.end ?? textarea.selectionEnd ?? start, textarea.value.length);
    const limit = textarea.maxLength > 0 ? textarea.maxLength : Infinity;
    if (textarea.value.length - (end - start) + text.length > limit) return false;
    textarea.setRangeText(text, start, end, "end");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    return true;
  }

  /* ---------- 面板 ---------- */
  function buildPopover() {
    popover = node("div", "emoji-popover");
    popover.id = "emoji-popover";
    popover.setAttribute("role", "dialog");
    popover.setAttribute("aria-label", "选择表情");
    popover.hidden = true;
    status = node("p", "emoji-sr-only");
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    popover.append(status);
    popover.addEventListener("mousedown", (event) => {
      // 点面板时别让文本框失去焦点（手机上也不会收起键盘）。
      if (event.target.closest(".emoji-item")) event.preventDefault();
    });
    popover.addEventListener("click", (event) => {
      const button = event.target.closest(".emoji-item");
      if (!button || !active) return;
      const emoji = button.dataset.emoji;
      if (insert(active.textarea, emoji, active.selection)) {
        remember(emoji);
        status.textContent = `已插入${NAMES.get(emoji)}`;
        active.selection = null;
      } else {
        status.textContent = "已到字数上限，放不下这个表情。";
      }
    });
    popover.addEventListener("keydown", onKeydown);
    document.body.append(popover);
  }

  function fillPopover() {
    const sections = [];
    const recent = readRecent();
    const groups = recent.length
      ? [{ id: "recent", label: "最近使用", items: recent.map((item) => [item, NAMES.get(item)]) }, ...GROUPS]
      : GROUPS;
    for (const group of groups) {
      const section = node("section", "emoji-group");
      section.setAttribute("aria-label", group.label);
      section.append(node("h3", "emoji-group-title", group.label));
      const grid = node("div", "emoji-grid");
      for (const [emoji, name] of group.items) {
        const button = node("button", "emoji-item", emoji);
        button.type = "button";
        button.dataset.emoji = emoji;
        button.title = name;
        button.setAttribute("aria-label", name);
        button.tabIndex = -1;
        grid.append(button);
      }
      section.append(grid);
      sections.push(section);
    }
    popover.replaceChildren(status, ...sections);
    status.textContent = "";
    const first = popover.querySelector(".emoji-item");
    if (first) first.tabIndex = 0;
  }

  function items() {
    return [...popover.querySelectorAll(".emoji-item")];
  }
  function focusItem(button) {
    for (const item of items()) item.tabIndex = item === button ? 0 : -1;
    button.focus({ preventScroll: false });
  }

  function onKeydown(event) {
    if (event.key === "Escape" || event.key === "Tab") {
      event.preventDefault();
      event.stopPropagation();
      close({ restore: true });
      return;
    }
    const current = event.target.closest?.(".emoji-item");
    if (!current) return;
    const all = items();
    const index = all.indexOf(current);
    let target = null;
    if (event.key === "ArrowRight") target = all[index + 1];
    else if (event.key === "ArrowLeft") target = all[index - 1];
    else if (event.key === "Home") target = all[0];
    else if (event.key === "End") target = all[all.length - 1];
    else if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      // 各分组长度不同，按实际位置找上一行/下一行里最近的那个。
      const box = current.getBoundingClientRect();
      const down = event.key === "ArrowDown";
      let best = Infinity;
      for (const item of all) {
        const other = item.getBoundingClientRect();
        const vertical = down ? other.top - box.bottom + 1 : box.top - other.bottom + 1;
        if (vertical < 0) continue;
        const horizontal = Math.abs((other.left + other.width / 2) - (box.left + box.width / 2));
        const score = vertical * 1000 + horizontal;
        if (score < best) {
          best = score;
          target = item;
        }
      }
    } else {
      return;
    }
    event.preventDefault();
    if (target) focusItem(target);
  }

  function place() {
    if (!active || !popover || popover.hidden) return;
    const anchor = active.trigger.getBoundingClientRect();
    const margin = 8;
    const width = popover.offsetWidth;
    const height = popover.offsetHeight;
    const roomAbove = anchor.top - margin;
    const roomBelow = window.innerHeight - anchor.bottom - margin;
    const above = roomAbove >= height || roomAbove > roomBelow;
    const top = above ? Math.max(margin, anchor.top - height - margin) : Math.min(window.innerHeight - height - margin, anchor.bottom + margin);
    const left = Math.max(margin, Math.min(window.innerWidth - width - margin, anchor.left));
    popover.style.top = `${Math.max(margin, top)}px`;
    popover.style.left = `${left}px`;
  }

  function open(textarea, trigger) {
    if (!popover) buildPopover();
    if (active && active.trigger !== trigger) close({ restore: false });
    active = { textarea, trigger, selection: { start: textarea.selectionStart, end: textarea.selectionEnd } };
    fillPopover();
    popover.hidden = false;
    trigger.setAttribute("aria-expanded", "true");
    place();
    const first = popover.querySelector(".emoji-item");
    if (first) first.focus({ preventScroll: true });
  }

  function close({ restore = false } = {}) {
    if (!popover || popover.hidden) return;
    popover.hidden = true;
    const previous = active;
    active = null;
    if (previous) {
      previous.trigger.setAttribute("aria-expanded", "false");
      if (restore && previous.textarea.isConnected) previous.textarea.focus({ preventScroll: true });
    }
  }

  document.addEventListener("pointerdown", (event) => {
    if (!active || !popover || popover.hidden) return;
    if (popover.contains(event.target) || active.trigger.contains(event.target)) return;
    close();
  });
  document.addEventListener("app:view-changed", () => close());
  window.addEventListener("resize", () => place());
  window.addEventListener("scroll", () => {
    if (active && !active.trigger.isConnected) close();
    else place();
  }, { passive: true });

  /* ---------- 挂到文本框上 ---------- */
  function attach(textarea, { container = null } = {}) {
    if (!textarea || textarea.dataset.emojiAttached === "1") return null;
    textarea.dataset.emojiAttached = "1";
    const trigger = node("button", "emoji-trigger");
    trigger.type = "button";
    trigger.setAttribute("aria-haspopup", "dialog");
    trigger.setAttribute("aria-expanded", "false");
    trigger.setAttribute("aria-controls", "emoji-popover");
    trigger.setAttribute("aria-label", "插入表情");
    trigger.append(node("span", "emoji-glyph", "😊"), node("span", "emoji-label", "表情"));
    // 记住最后一次选区：点按钮后文本框会失焦，但选区仍要插在原来的位置。
    const saveSelection = () => {
      if (active && active.textarea === textarea) active.selection = { start: textarea.selectionStart, end: textarea.selectionEnd };
    };
    textarea.addEventListener("blur", saveSelection);
    trigger.addEventListener("click", () => {
      if (active && active.trigger === trigger && !popover.hidden) close({ restore: true });
      else open(textarea, trigger);
    });
    textarea.form?.addEventListener("submit", () => close());
    if (container) {
      container.prepend(trigger);
    } else {
      const bar = node("div", "emoji-bar");
      bar.append(trigger);
      const anchor = textarea.closest("label") || textarea;
      anchor.after(bar);
    }
    return trigger;
  }

  window.EmojiPicker = { attach, close };
})();
