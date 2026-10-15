/* notes-pane.js —— 笔记页“列表 + 编辑区”布局的小工具：模板、目录、列表预览文字、手机上编辑区的开合。
 *
 * 对外：window.NotesPane = { TEMPLATES, templateText, headings, previewText, setMode, setOpen, renderToc }。
 * 纯函数（headings / previewText / templateText）不碰 DOM，Node 测试直接调用；
 * 编辑器本身、自动保存仍由 notes.js + notes-history.js 负责。用户文字一律 textContent。
 */
(function () {
  "use strict";

  // 模板只给结构和提示语，不替用户写内容（产品底线：AI / 系统都不生成标准答案）。
  const TEMPLATES = [
    { key: "blank", label: "空白笔记", lines: [] },
    {
      key: "pattern", label: "套路笔记", lines: [
        "# 套路：",
        "",
        "> 一句话讲给新手：",
        "",
        "## 触发信号（看到什么就该想到它）",
        "- ",
        "",
        "## 核心思路",
        "",
        "## 代码模板",
        "```python",
        "# 在这里写模板代码（空代码块编辑器读不进去，所以留一行注释）",
        "```",
        "",
        "## 易错点",
        "- ",
        "",
        "## 例题",
        "- [[题:]]",
      ],
    },
    {
      key: "review", label: "错题复盘", lines: [
        "# 复盘：",
        "",
        "## 题目",
        "[[题:]]",
        "",
        "## 我当时怎么想的",
        "",
        "## 错在哪",
        "",
        "## 最小反例（能让错误代码出错的最小输入）",
        "",
        "## 这次改对的关键",
      ],
    },
    {
      key: "compare", label: "解法对比", lines: [
        "## 思路",
        "",
        "## 复杂度（可选）",
        "",
        "## 适用场景",
        "",
        "## 与其他解法对比",
        "对比 [[暴力解法]] 与 [[二分查找]] 的取舍。",
      ],
    },
    {
      key: "weekly", label: "周总结", lines: [
        "# 本周总结",
        "",
        "## 这周最大的收获",
        "",
        "## 反复犯的错",
        "",
        "## 下周要练的套路",
      ],
    },
  ];

  function templateText(key) {
    const found = TEMPLATES.find((item) => item.key === key);
    return found ? found.lines.join("\n") : "";
  }

  // 可插入的“积木”：提示框（7 种颜色）、折叠答案、荧光笔。插入的是 Markdown，阅读视图里会排好版。
  const BLOCKS = [
    { key: "tip", label: "💡 提示", text: "> [!提示]\n> " },
    { key: "warn", label: "⚠️ 注意", text: "> [!注意]\n> " },
    { key: "danger", label: "🚩 易错", text: "> [!易错]\n> " },
    { key: "example", label: "🧪 例子", text: "> [!例子]\n> " },
    { key: "ok", label: "✅ 结论", text: "> [!结论]\n> " },
    { key: "def", label: "📘 定义", text: "> [!定义]\n> " },
    { key: "answer", label: "🤔 折叠答案", text: "> [!答案]- 先想一想再点开\n> " },
    { key: "hl", label: "🖍 荧光笔", text: "==要划重点的话==" },
  ];

  /** 把“粘贴进来的纯文本”整理成正经格式：下划线标题、一、二、章节、转义的 * 列表、“答案：”折叠。
      代码块里不动；返回整理后的文本（没有可整理的就原样返回）。 */
  function tidyMarkdown(source) {
    const lines = String(source ?? "").replace(/\r\n?/g, "\n").split("\n");
    const out = [];
    let fence = "";
    for (let i = 0; i < lines.length; i += 1) {
      const line = lines[i];
      const marker = /^\s*(```|~~~)/.exec(line);
      if (marker) {
        if (!fence) fence = marker[1];
        else if (marker[1] === fence) fence = "";
        out.push(line);
        continue;
      }
      if (fence) { out.push(line); continue; }
      const next = lines[i + 1];
      if (line.trim() && !/^\s*(#|>|[-*+]\s|\d+[.)]\s)/.test(line) && next !== undefined && /^\s{0,3}(={3,}|-{3,})\s*$/.test(next)) {
        out.push(`${/=/.test(next) ? "#" : "##"} ${line.trim()}`);
        i += 1;
        continue;
      }
      if (/^\s{0,3}={3,}\s*$/.test(line)) continue;
      let m;
      if ((m = /^(\s*)\\([*+-])\s+(.*)$/.exec(line))) { out.push(`${m[1]}- ${m[3]}`); continue; }
      if ((m = /^\s*([一二三四五六七八九十]+、\s*[^。！？!?]{1,30})$/.exec(line))) { out.push(`## ${m[1].trim()}`); continue; }
      if ((m = /^\s*(（[一二三四五六七八九十]+）\s*[^。！？!?]{1,30})$/.exec(line))) { out.push(`### ${m[1].trim()}`); continue; }
      if ((m = /^\s*(答案|参考答案|答)[:：]\s*(.+)$/.exec(line))) { out.push(`> [!答案]- ${m[1]}`, `> ${m[2]}`); continue; }
      out.push(line);
    }
    return out.join("\n").replace(/\n{3,}/g, "\n\n");
  }

  /** 正文里的 #/##/### 标题（跳过代码块），用来生成目录。 */
  function headings(markdown) {
    const result = [];
    let fence = "";
    for (const line of String(markdown ?? "").split("\n")) {
      const marker = /^\s*(```|~~~)/.exec(line);
      if (marker) {
        if (!fence) fence = marker[1];
        else if (marker[1] === fence) fence = "";
        continue;
      }
      if (fence) continue;
      const match = /^(#{1,3})\s+(.+?)\s*#*\s*$/.exec(line);
      if (match) result.push({ level: match[1].length, text: cleanInline(match[2]) });
    }
    return result.filter((item) => item.text);
  }

  function cleanInline(text) {
    return String(text ?? "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/\[\[(?:题:|笔记:)?([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_all, target, alias) => alias || target)
      .replace(/[*`$_~]/g, "")
      .trim();
  }

  /** 列表里的两行预览：去掉标题行、代码块、Markdown 符号，压成一行纯文本。 */
  function previewText(note, max = 90) {
    const lines = String(note?.content ?? "").split("\n");
    const out = [];
    let fence = false;
    let skippedTitle = Boolean(String(note?.title ?? "").trim());
    for (const raw of lines) {
      if (/^\s*(```|~~~)/.test(raw)) { fence = !fence; continue; }
      if (fence) continue;
      const line = raw.trim();
      if (!line || /^(={3,}|-{3,})$/.test(line)) continue;
      if (!skippedTitle) { skippedTitle = true; continue; } // 没有单独标题时第一行就是列表里的标题
      const text = cleanInline(line.replace(/^#{1,6}\s+/, "").replace(/^(?:[-*+>]|\d+[.)]|- \[[ xX]\])\s+/, ""));
      if (text) out.push(text);
      if (out.join(" ").length >= max) break;
    }
    const joined = out.join(" ");
    return joined.length > max ? `${joined.slice(0, max)}…` : joined;
  }

  /** 右侧编辑区显示“新笔记”还是“某一篇”。 */
  function setMode(mode, label) {
    const pane = document.getElementById("notes-pane");
    if (!pane) return;
    pane.dataset.mode = mode;
    const composer = document.getElementById("notes-composer");
    const note = document.getElementById("notes-pane-note");
    if (composer) composer.hidden = mode !== "new";
    if (note) note.hidden = mode !== "note";
    const text = document.getElementById("notes-pane-label");
    if (text) text.textContent = label || (mode === "new" ? "新笔记" : "");
  }

  /** 手机上编辑区盖住列表；桌面三栏时这个标记不影响布局（见 notes.css）。 */
  function setOpen(open) {
    const layout = document.getElementById("notes-layout");
    if (layout) layout.dataset.paneOpen = open ? "true" : "false";
  }

  /** 目录：标题少于 3 个就不显示；点一项滚到编辑区里对应的第 n 个标题。 */
  function renderToc(container, markdown, scrollRoot) {
    if (!container) return;
    const items = headings(markdown);
    container.replaceChildren();
    container.hidden = items.length < 3;
    if (container.hidden) return;
    const fold = document.createElement("details");
    fold.className = "notes-toc";
    const summary = document.createElement("summary");
    summary.textContent = `目录（${items.length}）`;
    const list = document.createElement("ol");
    items.forEach((item, index) => {
      const entry = document.createElement("li");
      entry.className = `notes-toc-l${item.level}`;
      const jump = document.createElement("button");
      jump.type = "button";
      jump.textContent = item.text;
      jump.addEventListener("click", () => {
        const nodes = scrollRoot ? scrollRoot.querySelectorAll("h1, h2, h3") : [];
        const target = nodes[index];
        if (target && typeof target.scrollIntoView === "function") target.scrollIntoView({ block: "start", behavior: "smooth" });
        fold.open = false;
      });
      entry.append(jump);
      list.append(entry);
    });
    fold.append(summary, list);
    container.append(fold);
  }

  /* ---------- 阅读视图的小工具：代码块复制、遮住看、导出 ---------- */

  /** 每个代码块加一条头：语言名 + “复制”。复制走剪贴板 API，失败时提示手动选中。 */
  function enhanceCode(root, notify) {
    if (!root) return 0;
    let count = 0;
    for (const pre of [...root.querySelectorAll("pre")]) {
      if (pre.parentNode && pre.parentNode.classList && pre.parentNode.classList.contains("nm-code")) continue;
      const code = pre.querySelector("code");
      if (!code) continue;
      const lang = (/language-([\w+#-]+)/.exec(code.className || "") || [])[1] || "代码";
      const wrap = document.createElement("div");
      wrap.className = "nm-code";
      const bar = document.createElement("div");
      bar.className = "nm-code-bar";
      const label = document.createElement("span");
      label.className = "nm-code-lang";
      label.textContent = lang;
      const copy = document.createElement("button");
      copy.type = "button";
      copy.className = "nm-code-copy";
      copy.textContent = "复制";
      copy.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(code.textContent || "");
          copy.textContent = "已复制";
          window.setTimeout(() => { copy.textContent = "复制"; }, 1500);
        } catch (_error) {
          notify?.("浏览器没让复制，请手动选中代码复制。");
        }
      });
      bar.append(label, copy);
      pre.replaceWith(wrap);
      wrap.append(bar, pre);
      count += 1;
    }
    return count;
  }

  /** 遮住看：只露出标题，正文每一块先模糊，点一下这一块就显示。可选的看法，不打分、不记录。 */
  function setCovered(root, on) {
    if (!root) return;
    root.classList.toggle("is-covered", Boolean(on));
    for (const block of [...(root.children || [])]) block.classList.remove("nm-uncovered");
    if (!root.dataset.coverBound) {
      root.dataset.coverBound = "1";
      root.addEventListener("click", (event) => {
        if (!root.classList.contains("is-covered")) return;
        let block = event.target;
        while (block && block.parentNode !== root) block = block.parentNode;
        if (!block || /^H[1-3]$/.test(block.tagName) || block.classList.contains("nm-uncovered")) return;
        event.preventDefault();
        event.stopPropagation();
        block.classList.add("nm-uncovered");
      }, true);
    }
  }

  /** 把一篇笔记存成 .md 文件（文件名用标题，去掉不能当文件名的字符）。 */
  function downloadMarkdown(note, title) {
    const name = `${String(title || "笔记").replace(/[\/:*?"<>|\n\r]+/g, " ").trim().slice(0, 60) || "笔记"}.md`;
    const head = note.title ? `# ${note.title}\n\n` : "";
    const tags = (note.tags || []).length ? `\n\n标签：${note.tags.map((tag) => `#${tag}`).join(" ")}\n` : "\n";
    const blob = new Blob([`${head}${note.content || ""}${tags}`], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = name;
    link.hidden = true;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return name;
  }

  window.NotesPane = Object.freeze({
    TEMPLATES, BLOCKS, templateText, tidyMarkdown, headings, previewText, setMode, setOpen, renderToc,
    enhanceCode, setCovered, downloadMarkdown,
  });
})();
