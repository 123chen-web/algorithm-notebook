"use strict";

/* 记笔记页面（Memos 式）：顶部 composer、搜索框、标签筛选行、时间线列表、"加载更多"分页。
   对外契约：window.Notes = { 纯函数…, configure(hooks), load(), loadMore(), openWithProblem(problemId, problemTitle), reset(), snapshot() }。
   纯函数不碰 DOM，Node 测试（tests/notes_behaviour.cjs）直接调用；控制器只通过 hooks 打交道：
     api(path, options)   带 CSRF 的请求（失败时抛出带中文 message 的错误）
     getUser()            当前用户
     getEpoch()           登录代次
     getView()            当前页面
     confirm(message)     删除确认框
     notify(text)         顶部提示条（app.js 的 message）
   所有异步响应都带"票据"（本控制器的代数 + 登录代次 + 用户 id），登出再登录之后迟到的响应一律丢弃。
   后端不返回标签聚合，所以标签筛选行从"当前已加载的笔记"里收集标签（见 collectTags 注释）。 */
(() => {
  const PAGE_SIZE = 20;
  const PROBLEMS_LIMIT = 30;
  const SEARCH_DELAY = 250;
  const TITLE_FALLBACK_LEN = 30;
  const TAG_MAX = 10;
  // 与后端 routers/notes.py 的 NOTE_CONTENT_MAX 保持一致（Pydantic max_length）。
  // 前端先拦一道，给出中文提示，避免超长提交后才收到 422。
  const NOTE_CONTENT_MAX = 20000;
  // 提示框类型：中英文都认，归到 7 种颜色。
  const CALLOUT_KINDS = {
    提示: "tip", tip: "tip", note: "tip", info: "tip", hint: "tip", 笔记: "tip",
    注意: "warn", 警告: "warn", warning: "warn", caution: "warn", attention: "warn",
    易错: "danger", 错误: "danger", 坑: "danger", danger: "danger", bug: "danger", error: "danger", failure: "danger",
    例子: "example", 例题: "example", 示例: "example", example: "example",
    结论: "ok", 总结: "ok", 要点: "ok", summary: "ok", abstract: "ok", tldr: "ok", success: "ok",
    定义: "def", 概念: "def", 定理: "def", quote: "def", definition: "def",
    答案: "answer", 自测: "answer", 思考: "answer", question: "answer", answer: "answer", faq: "answer",
  };
  const CALLOUT_LABELS = { tip: "提示", warn: "注意", danger: "易错", example: "例子", ok: "结论", def: "定义", answer: "答案（想好了再点开）" };
  const CALLOUT_ICONS = { tip: "💡", warn: "⚠️", danger: "🚩", example: "🧪", ok: "✅", def: "📘", answer: "🤔" };

  let hooks = null;
  let generation = 0;
  let mounted = false;
  let searchTimer = null;
  let loading = false;
  let listRequest = 0;

  const notes = [];
  let total = 0;
  let hasMore = false;
  let query = "";
  let activeTag = "";
  let problemFilter = null; // { id, title } 或 null
  let problems = [];
  let problemsLoaded = false;
  let pendingPrefill = null; // { id, title }：从题目详情"记笔记"跳过来时预填关联题目

  const $ = (selector) => document.querySelector(selector);

  /* ───────────── 纯函数 ───────────── */

  /** 先转义再渲染是安全底线：所有 HTML 特殊字符在这里变成实体，后续只拼接我们自己生成的标签。 */
  function escapeHtml(text) {
    return String(text ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");
  }

  /** 标签输入按逗号（半角/全角）、顿号、空白分隔，去重保序，最多 TAG_MAX 个。 */
  function parseTags(raw) {
    const seen = new Set();
    for (const part of String(raw ?? "").split(/[\s,，、]+/)) {
      const tag = part.trim();
      if (!tag || seen.has(tag)) continue;
      seen.add(tag);
      if (seen.size >= TAG_MAX) break;
    }
    return [...seen];
  }

  // 标题里可能带行内排版（加粗、链接、互链、图片占位），卡片标题只保留可读文字。
  function cleanHeadingText(text) {
    return String(text ?? "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/\[\[(?:题:|笔记:)?([^\]]+)\]\]/g, "$1")
      .replace(/^(?:[-*>]|\d+[.)])\s+/, "")
      .replace(/[*`$]/g, "")
      .trim();
  }

  /** 笔记显示标题：有 title 用 title，否则取 content 首个非空行前 30 字。 */
  function noteTitle(note) {
    const title = String(note?.title ?? "").trim();
    if (title) return title;
    const firstLine = String(note?.content ?? "").split("\n").map((line) => line.trim()).find((line) => line) || "";
    // 没有单独标题时取第一行；去掉 # 标题符号、列表/引用符号和加粗符号，别把排版符号当标题显示。
    const cleaned = cleanHeadingText(firstLine.replace(/^#{1,6}\s*/, ""));
    return (cleaned || firstLine).slice(0, TITLE_FALLBACK_LEN);
  }

  // 标题取自正文第一行的“# 小标题”时，卡片标题已经显示了它，正文里不再重复一遍。
  function bodyWithoutDerivedTitle(note) {
    const content = String(note?.content ?? "");
    if (String(note?.title ?? "").trim()) return content;
    const lines = content.split("\n");
    const first = lines.findIndex((line) => line.trim());
    if (first !== -1 && /^#{1,3}\s+\S/.test(lines[first])) lines.splice(first, 1);
    return lines.join("\n");
  }

  /** N5：卡片标题的显示策略——只有用户显式写了标题，或正文首行本身是 ATX 标题块时，
      卡片头部才显示标题；正文开头当标题的旧做法不再重复显示（避免“标题=正文第一句”）。
      返回应显示的标题文本；不需要标题时返回 null。纯函数，Node 测试直接调用。 */
  function cardHeading(note) {
    const explicit = String(note?.title ?? "").trim();
    if (explicit) return explicit;
    const content = String(note?.content ?? "");
    const firstLine = content.split("\n").map((line) => line.trim()).find((line) => line) || "";
    const heading = /^#{1,3}\s+(.+)$/.exec(firstLine);
    if (!heading) return null;
    // 与 noteTitle 一致地去掉行内排版符号，避免把链接 / ** / ` / $ 显示进卡片标题。
    const cleaned = cleanHeadingText(heading[1]);
    return cleaned || null;
  }

  /** 列表查询串：只带非默认值；limit 钳在 1..100。 */
  function buildNotesQuery({ q = "", tag = "", problemId = "", limit = PAGE_SIZE, offset = 0 } = {}) {
    const params = new URLSearchParams();
    if (q.trim()) params.set("q", q.trim());
    if (tag) params.set("tag", tag);
    if (problemId !== "" && problemId !== null && problemId !== undefined) params.set("problem_id", String(problemId));
    const safeLimit = Math.min(100, Math.max(1, Number(limit) || PAGE_SIZE));
    if (safeLimit !== PAGE_SIZE) params.set("limit", String(safeLimit));
    if (offset > 0) params.set("offset", String(offset));
    const queryString = params.toString();
    return queryString ? `/api/notes?${queryString}` : "/api/notes";
  }

  // Markdown 子集直接构建 DOM。文本不经过 HTML 解析，也不使用占位符。
  // options.drawingIds：当前用户拥有的画板 id 集合；![标题](drawing:ID) 仅在
  // ID 属于本人时渲染成缩略图按钮，其余一律按纯文本显示（由 draw-host.js 委托打开）。
  function renderNoteMarkdown(source, options = {}) {
    source = String(source ?? "");
    // N2：把 mermaid 围栏 / 块级公式 / 行内公式先换成私有区占位符，再走行解析；
    // 代码围栏与行内代码里的 $ / [[ ]] 由 NotesLinks.codeRanges 保护，不被替换。
    if (window.NotesRich && typeof window.NotesRich.prepareSource === "function") {
      source = window.NotesRich.prepareSource(source);
    }
    const root = document.createDocumentFragment();
    function drawingRefNode(alt, target) {
      const idMatch = /^drawing:(\d+)$/.exec(String(target));
      const id = idMatch ? Number(idMatch[1]) : NaN;
      const owned = idMatch && Number.isInteger(id) && id > 0
        && options.drawingIds instanceof Set && options.drawingIds.has(id);
      if (!owned) return document.createTextNode(`![${alt}](${target})`);
      const caption = alt || "画板";
      const ref = document.createElement("button");
      ref.type = "button";
      ref.className = "notes-drawing-ref";
      ref.setAttribute("data-drawing-id", String(id));
      const img = document.createElement("img");
      img.className = "notes-drawing-thumb";
      img.setAttribute("src", `/api/drawings/${id}/thumb`);
      img.alt = caption;
      img.loading = "lazy";
      const title = document.createElement("span");
      title.className = "notes-drawing-ref-title";
      title.textContent = caption;
      ref.append(img, title);
      return ref;
    }
    function isHttpUrl(url) {
      try {
        return /^https?:\/\//i.test(url) && ["http:", "https:"].includes(new URL(url).protocol);
      } catch {
        return false;
      }
    }

    // 逐字符扫描行内语法：行内代码 → 图片 → 显式链接 → 粗/斜/删除线 → 反斜杠转义。
    // 与旧实现一致：非 http(s) 链接（javascript:/data:/vbscript: 等）与未命中的写法一律落纯文本。
    function inline(parent, text) {
      const s = String(text ?? "");
      let i = 0;
      let buf = "";
      const flush = () => { if (buf) { parent.append(document.createTextNode(buf)); buf = ""; } };
      const push = (node) => { flush(); parent.append(node); };
      while (i < s.length) {
        const rest = s.slice(i);
        let m;
        if ((m = /^`([^`\n]+)`/.exec(rest))) {
          push(h("code", m[1]));
        } else if ((m = /^!\[([^\]]*)\]\(([^)\s]*)\)/.exec(rest))) {
          let node;
          if (/^drawing:\d+$/.test(m[2])) {
            node = drawingRefNode(m[1], m[2]);
          } else {
            // 图片先落 data-* 占位 span，是否真渲染成 <img> 由 NotesRich 决定（仅本人附件）。
            node = document.createElement("span");
            node.setAttribute("data-nr-img", "1");
            node.setAttribute("data-alt", m[1]);
            node.setAttribute("data-src", m[2]);
          }
          push(node);
        } else if ((m = /^\[([^\]]+)\]\(([^)\s]*)\)/.exec(rest))) {
          if (isHttpUrl(m[2])) {
            const a = h("a", m[1]);
            a.setAttribute("href", m[2]);
            a.setAttribute("target", "_blank");
            a.setAttribute("rel", "noopener");
            push(a);
          } else {
            buf += m[0]; // 非 http(s) 链接按纯文本显示
          }
        } else if ((m = /^==([^=\n]+)==/.exec(rest))) {
          const mark = h("mark", null, "nm-hl");
          inline(mark, m[1]);
          push(mark);
        } else if ((m = /^\*\*([^*]+)\*\*/.exec(rest))) {
          push(h("strong", m[1]));
        } else if ((m = /^\*([^*\n]+)\*/.exec(rest))) {
          push(h("em", m[1]));
        } else if ((m = /^~~([^~\n]+)~~/.exec(rest))) {
          push(h("del", m[1]));
        } else if ((m = /^\\([\\`*_{}[\]()#+\-.!~>])/.exec(rest))) {
          buf += m[1]; // 编辑器会对行首符号做反斜杠转义，展示时还原成普通字符
        } else {
          buf += s[i];
          i += 1;
          continue;
        }
        i += m[0].length;
      }
      flush();
    }

    // GFM 表格：表头行 + 分隔行（|:--|--:|）+ 数据行。单元格用 textContent 填，绝不拼 HTML。
    function splitTableRow(line) {
      let s = String(line).trim();
      if (s.startsWith("|")) s = s.slice(1);
      if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
      return s.split(/(?<!\\)\|/).map((cell) => cell.replace(/\\\|/g, "|").trim());
    }

    function isTableStart(lines, i) {
      if (i + 1 >= lines.length) return false;
      const header = lines[i];
      const sep = lines[i + 1];
      if (!header.includes("|")) return false;
      if (!/^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/.test(sep)) return false;
      const heads = splitTableRow(header);
      const marks = splitTableRow(sep);
      return heads.length > 0 && heads.length === marks.length;
    }

    function buildTable(lines, i) {
      const headers = splitTableRow(lines[i]);
      const rows = [];
      let j = i + 2;
      while (j < lines.length && lines[j].includes("|") && lines[j].trim()) {
        const cells = splitTableRow(lines[j]);
        if (cells.length !== headers.length) break;
        rows.push(cells);
        j += 1;
      }
      const wrap = h("div", null, "notes-table-wrap");
      const table = h("table", null, "notes-rich-table");
      const thead = document.createElement("thead");
      const headRow = h("tr");
      for (const text of headers) { const th = h("th"); th.textContent = text; headRow.append(th); }
      thead.append(headRow);
      const tbody = document.createElement("tbody");
      for (const cells of rows) {
        const tr = h("tr");
        for (const text of cells) { const td = h("td"); td.textContent = text; tr.append(td); }
        tbody.append(tr);
      }
      table.append(thead, tbody);
      wrap.append(table);
      return { node: wrap, next: j };
    }

    // 列表项：任务清单项 - [ ] / - [x]，无序列表 -/*/+，有序列表 1. / 1)；记录缩进层级。
    function matchListItem(line) {
      let m;
      if ((m = /^(\s*)([-*+])\s+\[([ xX])\]\s+(.*)$/.exec(line))) {
        return { indent: m[1].length, marker: "task", checked: m[3].toLowerCase() === "x", content: m[4] };
      }
      if ((m = /^(\s*)\\?([-*+])\s+(.*)$/.exec(line))) {
        return { indent: m[1].length, marker: "ul", content: m[3] };
      }
      if ((m = /^(\s*)\d+[.)]\s+(.*)$/.exec(line))) {
        return { indent: m[1].length, marker: "ol", content: m[2] };
      }
      return null;
    }

    // 递归构建（可嵌套）列表；同层标记变化时结束，交由外层另起一个列表。
    function buildList(lines, i) {
      const first = matchListItem(lines[i]);
      const tag = first.marker === "ol" ? "ol" : "ul";
      const list = h(tag);
      if (first.marker === "task") list.className = "notes-task-list";
      const baseIndent = first.indent;
      let li = null;
      while (i < lines.length) {
        const cur = matchListItem(lines[i]);
        if (!cur || cur.indent < baseIndent) break;
        if (cur.indent > baseIndent) {
          if (!li) break;
          const sub = buildList(lines, i); // 更深缩进 = 嵌套到上一个 li 里
          li.append(sub.node);
          i = sub.next;
          continue;
        }
        const curTag = cur.marker === "ol" ? "ol" : "ul";
        if (curTag !== tag) break; // 有序/无序切换：另起列表
        if (cur.marker === "task") {
          li = h("li", null, "notes-task-item");
          const box = document.createElement("input");
          box.type = "checkbox";
          box.disabled = true;
          box.checked = cur.checked;
          box.setAttribute("aria-readonly", "true");
          box.tabIndex = -1;
          const text = h("span", null, "notes-task-text");
          inline(text, cur.content);
          li.append(box, text);
        } else {
          li = h("li");
          inline(li, cur.content);
        }
        list.append(li);
        i += 1;
      }
      return { node: list, next: i };
    }

    // 提示框：引用块第一行是 [!类型] 标题（与 Obsidian 写法一致，中英文类型名都认）。
    // [!类型]- 默认折起，[!类型]+ 默认展开；“答案/自测”类默认折起。
    function buildCallout(quoted) {
      // 所见即所得编辑器保存时会把 [ ] 转义成 \[ \]，这里两种写法都认。
      const first = String(quoted[0] || "").trim().replace(/\\([[\]])/g, "$1");
      const head = /^\[!([^\]\s]{1,12})\]([+-]?)\s*(.*)$/.exec(first);
      if (!head) return null;
      const kind = CALLOUT_KINDS[head[1].toLowerCase()] || "tip";
      const label = head[3] || CALLOUT_LABELS[kind] || head[1];
      const foldable = head[2] === "-" || head[2] === "+" || kind === "answer";
      const box = h(foldable ? "details" : "div", null, `nm-callout nm-callout-${kind}`);
      if (foldable && head[2] === "+") box.open = true;
      const title = h(foldable ? "summary" : "p", null, "nm-callout-title");
      title.append(h("span", CALLOUT_ICONS[kind] || "💡", "nm-callout-icon"));
      const titleText = h("span");
      inline(titleText, label);
      title.append(titleText);
      const body = h("div", null, "nm-callout-body");
      body.append(parseBlocks(quoted.slice(1)));
      box.append(title, body);
      return box;
    }

    // 块级解析：围栏代码 → 分割线 → 引用 → 表格 → 列表 → 标题/段落。
    function parseBlocks(textLines) {
      const fragment = document.createDocumentFragment();
      let index = 0;
      while (index < textLines.length) {
        const line = textLines[index];
        const fence = /^(```|~~~)(.*)$/.exec(line);
        if (fence) {
          const body = [];
          index += 1;
          while (index < textLines.length && !textLines[index].startsWith(fence[1])) {
            body.push(textLines[index]);
            index += 1;
          }
          if (index < textLines.length) index += 1; // 收尾围栏
          const pre = h("pre");
          const code = h("code", body.join("\n"));
          const lang = fence[2].trim();
          if (lang) code.className = `language-${lang}`;
          pre.append(code);
          fragment.append(pre);
          continue;
        }
        if (/^\s{0,3}(?:[-*_]\s*){3,}$/.test(line)) {
          fragment.append(document.createElement("hr"));
          index += 1;
          continue;
        }
        if (/^\s{0,3}>/.test(line)) {
          const quoted = [];
          while (index < textLines.length && /^\s{0,3}>/.test(textLines[index])) {
            quoted.push(textLines[index].replace(/^\s{0,3}>\s?/, ""));
            index += 1;
          }
          const callout = buildCallout(quoted);
          if (callout) {
            fragment.append(callout);
            continue;
          }
          const bq = h("blockquote");
          if (quoted.length === 1) inline(bq, quoted[0]);
          else bq.replaceChildren(parseBlocks(quoted));
          fragment.append(bq);
          continue;
        }
        if (isTableStart(textLines, index)) {
          const built = buildTable(textLines, index);
          fragment.append(built.node);
          index = built.next;
          continue;
        }
        if (matchListItem(line)) {
          const built = buildList(textLines, index);
          fragment.append(built.node);
          index = built.next;
          continue;
        }
        if (!line.trim()) { index += 1; continue; }
        const underline = index + 1 < textLines.length ? /^\s{0,3}(={3,}|-{3,})\s*$/.exec(textLines[index + 1]) : null;
        if (underline) {
          // 纯文本里常见的“标题下面一行 ====”写法（Markdown 叫 setext 标题）。
          const block = h(underline[1][0] === "=" ? "h2" : "h3");
          inline(block, line.trim());
          fragment.append(block);
          index += 2;
          continue;
        }
        if (/^\s{0,3}={3,}\s*$/.test(line)) {
          fragment.append(document.createElement("hr"));
          index += 1;
          continue;
        }
        const answer = /^\s*(答案|参考答案|答)[:：]\s*(.+)$/.exec(line);
        if (answer) {
          // “答案：…”默认折起，先自己想，想好了再点开（不打分、不记录，只是帮你忍住不偷看）。
          const fold = h("details", null, "nm-answer");
          fold.append(h("summary", `${answer[1]}（想好了再点开）`));
          const body = h("p");
          inline(body, answer[2]);
          fold.append(body);
          fragment.append(fold);
          index += 1;
          continue;
        }
        const cnHeading = /^\s*([一二三四五六七八九十]+、|（[一二三四五六七八九十]+）)\s*([^。！？!?]{1,30})$/.exec(line);
        const heading = line.match(/^(#{1,3})\s+(.*)$/);
        let block;
        if (heading) {
          block = h(`h${heading[1].length}`);
          inline(block, heading[2]);
        } else if (cnHeading) {
          block = h(cnHeading[1].endsWith("、") ? "h2" : "h3", null, "nm-cn-heading");
          inline(block, line.trim());
        } else {
          block = h("p");
          if (/^\s*自测[:：]/.test(line)) block.className = "nm-selftest";
          inline(block, line);
        }
        fragment.append(block);
        index += 1;
      }
      return fragment;
    }

    const lines = String(source ?? "").split("\n");
    root.append(parseBlocks(lines));
    // N1：代码块/行内代码已落成 <pre><code>/<code>，再对剩余文本节点做 [[ ]] 链接落位。
    // 注：仅在 notes-links.js 已加载时生效；Node 测试只加载 notes.js 时为 no-op。
    if (window.NotesLinks && typeof window.NotesLinks.attachInlineLinks === "function") {
      window.NotesLinks.attachInlineLinks(root);
    }
    // N2：attachment 图片落位 + 公式 / mermaid 占位渲染（按需懒加载 vendor）。
    if (window.NotesRich && typeof window.NotesRich.attachRich === "function") {
      window.NotesRich.attachRich(root);
    }
    return root;
  }

  /* 后端列表接口不返回标签聚合：这里从当前已加载的笔记里收集（按出现次数降序，
     次数相同按标签名升序），只够做"当前页/已加载范围"的筛选提示，这是取舍后的简化实现。 */
  function collectTags(loaded) {
    const counts = new Map();
    for (const note of loaded || []) {
      for (const tag of note?.tags || []) {
        const name = String(tag).trim();
        if (!name) continue;
        counts.set(name, (counts.get(name) || 0) + 1);
      }
    }
    return [...counts.entries()]
      .map(([tag, count]) => ({ tag, count }))
      .sort((a, b) => b.count - a.count || (a.tag < b.tag ? -1 : a.tag > b.tag ? 1 : 0));
  }

  /** 刚刚 / N 分钟前 / N 小时前 / N 天前（不足 30 天）/ 否则 YYYY-MM-DD；非法输入返回空串。 */
  function noteTime(value, nowMs = Date.now()) {
    const then = value === null || value === undefined || value === "" ? NaN : Date.parse(String(value));
    if (!Number.isFinite(then) || !Number.isFinite(nowMs)) return "";
    const seconds = Math.floor((nowMs - then) / 1000);
    if (seconds < 60) return "刚刚";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} 分钟前`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时前`;
    if (seconds < 30 * 86400) return `${Math.floor(seconds / 86400)} 天前`;
    const date = new Date(then);
    const pad = (n) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  }

  /* ───────────── 控制器 ───────────── */

  function configure(next) {
    hooks = next;
  }

  const alive = (token) => token.generation === generation
    && (!hooks?.getEpoch || hooks.getEpoch() === token.epoch)
    && (!hooks?.getUser || hooks.getUser()?.id === token.userId);
  const takeToken = () => ({
    generation,
    epoch: hooks?.getEpoch ? hooks.getEpoch() : null,
    userId: hooks?.getUser ? hooks.getUser()?.id : null,
  });

  function h(tag, text, className) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  function composerNotice(text) {
    const notice = $("#notes-composer-notice");
    if (!notice) return;
    notice.hidden = !text;
    notice.textContent = text || "";
  }

  /* N5：新建区正文可能是 OYEditor 所见即所得，也可能降级为 #notes-content textarea；
     保存 / 清空 / 预填 / 模板插入都走这一层，两条路径行为一致。 */
  function composerEditor() {
    const ed = window.NotesEditor;
    return ed && typeof ed.editorActive === "function" && ed.editorActive() ? ed : null;
  }

  function getComposerContent() {
    const ed = composerEditor();
    if (ed) return ed.getComposerMarkdown();
    return String($("#notes-content")?.value ?? "");
  }

  function setComposerContent(markdown, focus) {
    const text = String(markdown ?? "");
    const ed = composerEditor();
    if (ed) ed.setComposerMarkdown(text);
    else { const ta = $("#notes-content"); if (ta) ta.value = text; }
    if (focus) focusComposer();
  }

  // 对外的“聚焦新建区”：右边正开着某一篇时先收起它（会先保存），再切到新建区。
  function focusComposer() {
    if (paneState) {
      void showComposer({ focus: true });
      return;
    }
    window.NotesPane?.setMode("new");
    window.NotesPane?.setOpen(true);
    focusComposerEditor();
  }

  function focusComposerEditor() {
    const ed = composerEditor();
    if (ed && typeof ed.focusComposer === "function") ed.focusComposer();
    else $("#notes-content")?.focus();
  }

  function insertComposerTemplate(markdown) {
    const text = String(markdown ?? "");
    const ed = composerEditor();
    if (ed && typeof ed.insertComposerTemplate === "function") ed.insertComposerTemplate(text);
    else if (window.NotesRich && typeof window.NotesRich.insertAtCursor === "function") {
      const ta = $("#notes-content");
      if (ta) window.NotesRich.insertAtCursor(ta, "", "", text);
    }
  }

  function sortNotes() {
    notes.sort((a, b) => {
      const pin = Number(Boolean(b.pinned)) - Number(Boolean(a.pinned));
      if (pin) return pin;
      return String(b.updated_at || "").localeCompare(String(a.updated_at || ""));
    });
  }

  function renderTagRow() {
    const row = $("#notes-tag-row");
    if (!row) return;
    row.replaceChildren();
    const all = h("button", "全部", "notes-chip");
    all.type = "button";
    all.setAttribute("aria-pressed", activeTag === "" ? "true" : "false");
    if (activeTag === "") all.classList.add("is-active");
    all.addEventListener("click", () => setTagFilter(""));
    row.append(all);
    for (const { tag, count } of collectTags(notes)) {
      const chip = h("button", `${tag} ${count}`, "notes-chip");
      chip.type = "button";
      chip.dataset.tag = tag;
      chip.setAttribute("aria-pressed", activeTag === tag ? "true" : "false");
      if (activeTag === tag) chip.classList.add("is-active");
      chip.addEventListener("click", () => setTagFilter(activeTag === tag ? "" : tag));
      row.append(chip);
    }
  }

  function setTagFilter(tag) {
    activeTag = tag;
    void reloadList();
  }

  function setProblemFilter(id, title) {
    problemFilter = id ? { id, title: title || "" } : null;
    void reloadList();
  }

  // 列表里一篇一行：标题、两行预览、时间和标签；点一下在右边打开（直接可编辑）。
  function renderNoteCard(note) {
    const card = h("article", null, "notes-card");
    card.setAttribute("data-note-id", String(note.id));
    if (note.pinned) card.classList.add("is-pinned");
    const open = h("button", null, "notes-card-open");
    open.type = "button";
    const title = h("span", null, "notes-card-title");
    if (note.pinned) {
      const pin = h("span", "📌", "notes-pin");
      pin.setAttribute("aria-label", "已置顶");
      title.append(pin);
    }
    const pane = window.NotesPane;
    const strong = h("strong", noteTitle(note) || "（无标题）");
    if (query.trim() && pane) pane.markInto(strong, strong.textContent, query);
    title.append(strong);
    const preview = h("span", pane ? pane.previewText(note) : "", "notes-card-preview");
    // 搜索时预览换成“命中的那一句”，关键词用荧光色标出来；只命中标题时仍显示普通预览。
    const snippet = query.trim() && pane ? pane.searchSnippet(note, query) : null;
    if (snippet) {
      const hit = h("mark", snippet.hit, "notes-search-hit");
      preview.replaceChildren(document.createTextNode(snippet.before), hit, document.createTextNode(snippet.after));
      preview.classList.add("is-snippet");
    }
    const foot = h("span", null, "notes-card-foot");
    foot.append(h("time", noteTime(note.updated_at || note.created_at), "notes-time"));
    for (const tag of (note.tags || []).slice(0, 3)) foot.append(h("span", `#${tag}`, "notes-card-tag"));
    if (note.problem_id) foot.append(h("span", `📝 ${note.problem_title || `题目 ${note.problem_id}`}`, "notes-card-problem"));
    open.append(title, preview, foot);
    open.addEventListener("click", () => void openNote(note));
    if (currentNoteId === note.id) {
      card.classList.add("is-current");
      open.setAttribute("aria-current", "true");
    }
    card.append(open);
    return card;
  }

  function renderList() {
    const list = $("#notes-list");
    const status = $("#notes-status");
    const more = $("#notes-more");
    if (!list) return;
    list.replaceChildren();
    for (const note of notes) list.append(renderNoteCard(note));
    const filtersOn = query.trim() || activeTag || problemFilter;
    if (status) {
      status.hidden = false;
      if (!notes.length) {
        status.textContent = filtersOn ? "没有符合筛选的笔记，换个条件试试。" : "还没有笔记。点“＋ 新建”选个模板，或直接在新建区写第一条。";
      } else {
        status.textContent = `共 ${total} 条${filtersOn ? "（已筛选）" : ""}`;
      }
    }
    if (more) more.hidden = !hasMore;
    renderTagRow();
  }

  async function reloadList() {
    if (!hooks?.api) return;
    const token = takeToken();
    const request = ++listRequest;
    loading = true;
    // 列表还空着（刚进笔记页）时，慢的话先放骨架；筛选/搜索时保留旧列表，不闪。
    const listEl = $("#notes-list");
    const skeleton = listEl && !listEl.childNodes.length ? window.Feel?.skeletonCards?.(listEl, 5) : null;
    try {
      const data = await hooks.api(buildNotesQuery({
        q: query, tag: activeTag, problemId: problemFilter?.id ?? "", offset: 0,
      }));
      skeleton?.cancel();
      if (!alive(token) || request !== listRequest) return;
      notes.length = 0;
      notes.push(...(data?.notes || []));
      total = Number(data?.total) || 0;
      hasMore = notes.length < total;
      if (window.NotesRich && typeof window.NotesRich.configure === "function"
          && Array.isArray(data?.attachment_ids)) {
        window.NotesRich.configure({ ownedAttachmentIds: data.attachment_ids });
      }
      sortNotes();
      renderList();
    } catch (error) {
      const shown = skeleton?.cancel();
      if (!alive(token) || request !== listRequest) return;
      if (shown) listEl.replaceChildren();
      const status = $("#notes-status");
      if (status) { status.hidden = false; status.textContent = error?.message || "加载笔记失败，请稍后重试。"; }
    } finally {
      if (alive(token) && request === listRequest) loading = false;
    }
  }

  async function loadMore() {
    if (!hooks?.api || loading || !hasMore) return;
    const token = takeToken();
    const request = ++listRequest;
    loading = true;
    try {
      const data = await hooks.api(buildNotesQuery({
        q: query, tag: activeTag, problemId: problemFilter?.id ?? "", offset: notes.length,
      }));
      if (!alive(token) || request !== listRequest) return;
      notes.push(...(data?.notes || []));
      total = Number(data?.total) || 0;
      hasMore = notes.length < total;
      if (window.NotesRich && typeof window.NotesRich.configure === "function"
          && Array.isArray(data?.attachment_ids)) {
        window.NotesRich.configure({ ownedAttachmentIds: data.attachment_ids });
      }
      sortNotes();
      renderList();
    } catch (error) {
      if (!alive(token) || request !== listRequest) return;
      hooks?.notify?.(error?.message || "加载更多失败，请稍后重试。");
    } finally {
      if (alive(token) && request === listRequest) loading = false;
    }
  }

  function fillProblemOptions() {
    const select = $("#notes-problem");
    if (!select) return;
    const current = select.value;
    select.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "不关联";
    select.append(none);
    for (const problem of problems) {
      const option = document.createElement("option");
      option.value = String(problem.id);
      option.textContent = problem.title || `题目 ${problem.id}`;
      select.append(option);
    }
    // 预填的题目可能不在最近 30 条里：补一个选项，保证 openWithProblem 的值能选中。
    if (pendingPrefill && !problems.some((problem) => String(problem.id) === String(pendingPrefill.id))) {
      const option = document.createElement("option");
      option.value = String(pendingPrefill.id);
      option.textContent = pendingPrefill.title || `题目 ${pendingPrefill.id}`;
      select.append(option);
    }
    select.value = current || (pendingPrefill ? String(pendingPrefill.id) : "");
  }

  async function ensureProblems(token) {
    if (problemsLoaded || !hooks?.api) return;
    try {
      const data = await hooks.api(`/api/problems?limit=${PROBLEMS_LIMIT}`);
      if (!alive(token)) return;
      problems = data?.problems || [];
      problemsLoaded = true;
      fillProblemOptions();
    } catch {
      // 题目下拉加载失败不阻塞笔记列表；用户仍可写不关联题目的笔记。
    }
  }

  function applyPrefill() {
    if (!pendingPrefill) return;
    if (paneState) {
      void showComposer().then((ok) => { if (ok) applyPrefill(); });
      return;
    }
    window.NotesPane?.setMode("new");
    window.NotesPane?.setOpen(true);
    fillProblemOptions();
    const select = $("#notes-problem");
    if (select) select.value = String(pendingPrefill.id);
    // N1：新建笔记正文预填 [[题:题目标题]]，直接建立题目双向链接（编辑器或 textarea 同路）。
    if (!getComposerContent().trim()) {
      if (pendingPrefill.content) setComposerContent(pendingPrefill.content);
      else if (pendingPrefill.title) setComposerContent(`[[题:${pendingPrefill.title}]]`);
    }
    if (hooks?.getView?.() === "notes") focusComposer();
    pendingPrefill = null;
  }

  /** 从题目详情"记笔记"进入：先切到笔记页（showView 是 async，load 可能还没跑完），
      只把意图记在 pendingPrefill 里；已经 load 完就直接预填，否则 load() 结尾消费，避免竞态。 */
  function openWithProblem(problemId, problemTitle, extra = {}) {
    if (!problemId) return;
    pendingPrefill = { id: problemId, title: problemTitle || "", ...(extra.content ? { content: String(extra.content) } : {}) };
    if (problemsLoaded) applyPrefill();
  }

  // 新建区的本机草稿：输入后 0.6 秒存一次，保存成功 / 清空时删掉；下次进笔记页时自动放回去。
  let draftTimer = 0;
  function saveComposerDraft() {
    window.clearTimeout(draftTimer);
    draftTimer = window.setTimeout(() => {
      const store = window.NotesHistory?.drafts;
      if (!store) return;
      const content = getComposerContent();
      if (!String(content).trim()) store.clear("composer");
      else store.write("composer", { content, tags: $("#notes-tags-input")?.value || "", problem: $("#notes-problem")?.value || "" });
    }, 600);
  }

  function restoreComposerDraft() {
    const draft = window.NotesHistory?.drafts.read("composer");
    if (!draft || typeof draft.content !== "string" || !draft.content.trim()) return;
    if (String(getComposerContent()).trim()) return;
    setComposerContent(draft.content);
    const tags = $("#notes-tags-input");
    if (tags && !tags.value) tags.value = draft.tags || "";
    const select = $("#notes-problem");
    if (select && draft.problem && [...select.options].some((option) => option.value === draft.problem)) select.value = draft.problem;
    composerNotice(`已放回上次（${window.NotesHistory.when(draft.at)}）没保存的草稿。`);
  }

  function clearComposer() {
    window.clearTimeout(draftTimer);
    window.NotesHistory?.drafts.clear("composer");
    setComposerContent("");
    const tags = $("#notes-tags-input");
    const select = $("#notes-problem");
    if (tags) tags.value = "";
    if (select) select.value = "";
    composerNotice("");
  }

  async function saveComposer() {
    if (!hooks?.api) return;
    const token = takeToken();
    const rawContent = getComposerContent();
    const content = String(rawContent ?? "").trim();
    if (!content) {
      composerNotice("至少写点什么");
      focusComposer();
      return;
    }
    if (rawContent.length > NOTE_CONTENT_MAX) {
      composerNotice(`正文不能超过 ${NOTE_CONTENT_MAX} 字，请精简后再保存。`);
      focusComposer();
      return;
    }
    const tags = parseTags($("#notes-tags-input")?.value);
    const problemId = $("#notes-problem")?.value || "";
    const draft = { content: rawContent, tags: $("#notes-tags-input")?.value, problem: $("#notes-problem")?.value };
    const saveButton = $("#notes-save");
    if (saveButton) saveButton.disabled = true;
    try {
      const body = { content };
      if (tags.length) body.tags = tags;
      if (problemId) body.problem_id = Number(problemId) || problemId;
      const created = await hooks.api("/api/notes", { method: "POST", body: JSON.stringify(body) });
      if (!alive(token)) return;
      // 保存成功：清空筛选保证新笔记可见，插到列表顶部，不整页刷新。
      const filtered = query.trim() || activeTag || problemFilter;
      listRequest += 1;
      query = "";
      activeTag = "";
      problemFilter = null;
      const search = $("#notes-search");
      if (search) search.value = "";
      notes.unshift(created);
      total += 1;
      hasMore = notes.length < total;
      sortNotes();
      // 只有保存期间用户没有改动草稿才清空；失败 / 改过都保留内容。
      const untouched = getComposerContent() === draft.content
          && $("#notes-tags-input")?.value === draft.tags
          && $("#notes-problem")?.value === draft.problem;
      if (untouched) clearComposer();
      if (filtered) await reloadList();
      else renderList();
      if (!alive(token)) return;
      hooks?.notify?.("笔记已保存，之后的修改会自动保存。");
      // 新建区没被继续改动时，直接在右边打开刚建好的这篇，接着写就是自动保存。
      if (untouched) {
        const fresh = notes.find((item) => item.id === created.id) || created;
        await openNote(fresh);
      }
    } catch (error) {
      if (!alive(token)) return;
      composerNotice(error?.message || "保存失败，请稍后重试。");
    } finally {
      if (alive(token) && saveButton) saveButton.disabled = false;
    }
  }

  async function togglePin(note) {
    if (!hooks?.api) return;
    const token = takeToken();
    try {
      const updated = await hooks.api(`/api/notes/${note.id}`, {
        method: "PUT", body: JSON.stringify({ pinned: !note.pinned }),
      });
      if (!alive(token)) return;
      Object.assign(note, updated);
      // 手感层：置顶后卡片 FLIP 挪位；reduced-motion 下 flip 直接执行 mutate。
      const notesListEl = $("#notes-list");
      const mutate = () => { sortNotes(); renderList(); };
      if (window.Feel?.flip) window.Feel.flip(notesListEl, mutate);
      else mutate();
    } catch (error) {
      if (!alive(token)) return;
      hooks?.notify?.(error?.message || "操作失败，请稍后重试。");
    }
  }

  async function removeNote(note) {
    if (!hooks?.api) return;
    const token = takeToken();
    const ok = hooks.confirm ? hooks.confirm("删除这条笔记？删除后可以在「最近删除」里恢复。") : true;
    if (!ok) return;
    try {
      await hooks.api(`/api/notes/${note.id}`, { method: "DELETE" });
      if (!alive(token)) return;
      if (paneState && paneState.note.id === note.id) {
        await closeNote({ force: true });
        window.NotesPane?.setOpen(false);
      }
      const index = notes.findIndex((item) => item.id === note.id);
      if (index >= 0) notes.splice(index, 1);
      total = Math.max(0, total - 1);
      renderList();
      hooks?.notify?.("笔记已删除，可以在「最近删除」里恢复。");
    } catch (error) {
      if (!alive(token)) return;
      hooks?.notify?.(error?.message || "删除失败，请稍后重试。");
    }
  }

  /* ───────────── 右侧编辑区：选中的笔记直接可编辑 ───────────── */
  // paneState：当前在右侧打开的那篇 { note, flush, dirty, teardown, saver, refreshHead }；没有打开时为 null。
  let paneState = null;
  let currentNoteId = null;

  function markSelected() {
    for (const card of document.querySelectorAll("#notes-list .notes-card")) {
      const on = currentNoteId !== null && card.getAttribute("data-note-id") === String(currentNoteId);
      card.classList.toggle("is-current", on);
      const open = card.querySelector(".notes-card-open");
      if (!open) continue;
      if (on) open.setAttribute("aria-current", "true");
      else open.removeAttribute("aria-current");
    }
  }

  /** 关掉右侧正在编辑的那篇：先把没保存的改动发出去；发不出去时问一句（改动已在本机）。
      返回 false 表示用户选择留下。force=true 时不保存直接收起（登出、删除之后）。 */
  async function closeNote({ force = false } = {}) {
    const state = paneState;
    if (!state) return true;
    if (!force) {
      const ok = await state.flush();
      if (paneState !== state) return true; // 保存期间已被别的操作收起
      if (!ok && state.dirty()) {
        const leave = hooks?.confirm
          ? hooks.confirm("这篇还没保存上（看编辑区下方的提示）。改动已经存在这台设备上，下次打开会提示恢复。仍然离开吗？")
          : false;
        if (!leave) return false;
      }
    }
    state.teardown();
    return true;
  }

  /** 右侧切回“新笔记”。手机上同时把编辑区盖到列表上（open=true）或收回列表（open=false）。 */
  async function showComposer({ focus = false, open = true } = {}) {
    if (!(await closeNote())) return false;
    window.NotesPane?.setMode("new");
    window.NotesPane?.setOpen(open);
    if (focus) focusComposerEditor();
    return true;
  }

  function startNewFromTemplate(key) {
    const text = window.NotesPane ? window.NotesPane.templateText(key) : "";
    void showComposer().then((ok) => {
      if (!ok) return;
      const menu = $("#notes-new-menu");
      if (menu) menu.open = false;
      if (text) {
        if (String(getComposerContent()).trim()) insertComposerTemplate(text);
        else setComposerContent(text);
      }
      focusComposerEditor();
    });
  }

  /** 打开一篇：先显示排好版的阅读视图（提示框、荧光笔、折叠答案、串联），点“编辑”或双击正文进入编辑。 */
  async function openNote(note) {
    if (paneState && paneState.note.id === note.id && paneState.mode === "read") {
      window.NotesPane?.setOpen(true);
      return;
    }
    if (!(await closeNote())) return;
    const host = $("#notes-pane-note");
    if (!host) return;
    currentNoteId = note.id;
    markSelected();
    window.NotesPane?.setMode("note", noteTitle(note) || "笔记");
    window.NotesPane?.setOpen(true);

    const view = h("article", null, "notes-reader");
    const head = h("div", null, "notes-pane-head");
    head.append(h("h3", noteTitle(note) || "（无标题）", "notes-reader-title"));
    const tools = h("div", null, "notes-pane-tools");
    const editButton = h("button", "编辑", "notes-action primary");
    editButton.type = "button";
    editButton.setAttribute("aria-label", "编辑这条笔记");
    const pinButton = h("button", note.pinned ? "取消置顶" : "置顶", "notes-action");
    pinButton.type = "button";
    const historyButton = h("button", "历史", "notes-action");
    historyButton.type = "button";
    historyButton.setAttribute("aria-label", "查看这条笔记的历史版本");
    historyButton.hidden = !window.NotesHistory;
    const deleteButton = h("button", "删除", "notes-action is-danger");
    deleteButton.type = "button";
    deleteButton.setAttribute("aria-label", "删除这条笔记");
    // 粘贴进来的纯文本（==== 下划线、一、二、、\* 列表、答案：）一键整理成正经格式；改动进历史，能撤回。
    const tidied = window.NotesPane ? window.NotesPane.tidyMarkdown(note.content || "") : note.content;
    const tidyButton = h("button", "整理格式", "notes-action");
    tidyButton.type = "button";
    tidyButton.title = "把粘贴进来的纯文本整理成标题、列表、折叠答案（原样子会存进历史）";
    tidyButton.hidden = !tidied || tidied === note.content;
    // 遮住看：只露标题，正文点一下才显示（可选的看法，不打分、不记录）。
    const coverButton = h("button", "遮住看", "notes-action");
    coverButton.type = "button";
    coverButton.setAttribute("aria-pressed", "false");
    coverButton.title = "先遮住正文，自己回想，点哪一块显示哪一块";
    const exportButton = h("button", "导出", "notes-action");
    exportButton.type = "button";
    exportButton.title = "把这一篇存成 .md 文件";
    // 不常用的收进“更多”，顶上只留最常用的三个。
    const more = h("details", null, "notes-more-menu");
    more.append(h("summary", "更多", "notes-action"));
    const moreList = h("div", null, "notes-more-list");
    moreList.append(pinButton, historyButton, exportButton, deleteButton);
    more.append(moreList);
    tools.append(editButton, tidyButton, coverButton, more);
    head.append(tools);

    const meta = h("div", null, "notes-pane-meta");
    for (const tag of note.tags || []) {
      const chip = h("button", `#${tag}`, "notes-chip notes-chip-sm");
      chip.type = "button";
      chip.dataset.tag = tag;
      chip.setAttribute("aria-label", `按标签 ${tag} 筛选`);
      chip.addEventListener("click", () => setTagFilter(tag));
      meta.append(chip);
    }
    if (note.problem_id) {
      const problemChip = h("button", `📝 ${note.problem_title || `题目 ${note.problem_id}`}`, "notes-chip notes-chip-sm notes-problem-chip");
      problemChip.type = "button";
      problemChip.setAttribute("aria-label", `只看关联「${note.problem_title || note.problem_id}」的笔记`);
      problemChip.addEventListener("click", () => setProblemFilter(note.problem_id, note.problem_title));
      meta.append(problemChip);
    }
    meta.append(h("time", `更新于 ${noteTime(note.updated_at || note.created_at)}`, "notes-time"));
    const toc = h("div", null, "notes-pane-toc");
    const body = h("div", null, "notes-body notes-rich notes-reader-body");
    const drawingIds = hooks?.getDrawingIds ? hooks.getDrawingIds() : null;
    body.append(renderNoteMarkdown(bodyWithoutDerivedTitle(note), { drawingIds }));
    body.title = "双击进入编辑";
    const connect = h("div", null, "nc-panel");
    connect.hidden = true;
    const related = h("div", null, "nl-related");
    related.setAttribute("data-nl-related", "");
    view.append(head, meta, toc, body, connect, related);
    host.replaceChildren(view);
    window.NotesPane?.renderToc(toc, note.content || "", body);
    window.NotesPane?.enhanceCode(body, hooks?.notify);
    window.NotesLinks?.renderRelated?.(view, note.id);
    const paneEl = $("#notes-pane");
    if (paneEl) paneEl.scrollTop = 0;
    // 从搜索结果打开：滚到正文里第一个命中处并标出来。
    if (query.trim()) window.NotesPane?.revealMatch?.(body, query);

    const state = {
      note,
      mode: "read",
      flush: async () => true,
      dirty: () => false,
      teardown: () => {
        if (paneState !== state) return;
        paneState = null;
        currentNoteId = null;
        host.replaceChildren();
        markSelected();
        window.NotesPane?.setMode("new");
      },
      refreshHead: () => { pinButton.textContent = note.pinned ? "取消置顶" : "置顶"; },
    };
    paneState = state;

    // 串联：提到的笔记 / 套路加下划线，下面列出相关错题、套路、笔记（取不到就不显示）。
    if (window.NotesConnect) {
      void window.NotesConnect.load(note.id).then((data) => {
        if (paneState !== state || !data) return;
        window.NotesConnect.decorate(body, data);
        window.NotesConnect.renderPanel(connect, data);
      });
    }

    editButton.addEventListener("click", () => void editNote(note));
    coverButton.addEventListener("click", () => {
      const on = coverButton.getAttribute("aria-pressed") !== "true";
      coverButton.setAttribute("aria-pressed", on ? "true" : "false");
      coverButton.textContent = on ? "全部显示" : "遮住看";
      window.NotesPane?.setCovered(body, on);
    });
    exportButton.addEventListener("click", () => {
      more.open = false;
      const name = window.NotesPane?.downloadMarkdown(note, noteTitle(note));
      if (name) hooks?.notify?.(`已导出「${name}」。`);
    });
    tidyButton.addEventListener("click", async () => {
      tidyButton.disabled = true;
      try {
        const updated = await hooks.api(`/api/notes/${note.id}`, {
          method: "PUT",
          body: JSON.stringify({ content: tidied, base_updated_at: note.updated_at, snapshot: true }),
        });
        if (paneState !== state) return;
        Object.assign(note, updated);
        state.teardown();
        refreshAfterClose();
        await openNote(note);
        hooks?.notify?.("格式整理好了；原来的样子在“历史”里。");
      } catch (error) {
        tidyButton.disabled = false;
        hooks?.notify?.(error?.message || "整理失败，请稍后重试。");
      }
    });
    body.addEventListener("dblclick", (event) => {
      if (event.target.closest && event.target.closest("button, a, summary, details")) return;
      if (body.classList.contains("is-covered")) return; // 遮住看时双击是在掀开，不进编辑
      void editNote(note);
    });
    pinButton.addEventListener("click", async () => {
      await togglePin(note);
      state.refreshHead();
    });
    historyButton.addEventListener("click", () => {
      window.NotesHistory?.openHistory(note, {
        onRestored: (updated) => {
          Object.assign(note, updated);
          state.teardown();
          refreshAfterRestore(note);
        },
      });
    });
    deleteButton.addEventListener("click", () => void removeNote(note));
  }

  /** 从别的页面用模板新建（如学习路线的“用套路笔记模板写一篇”）；replacements 把模板里的整行替换掉。 */
  async function newFromTemplate(key, replacements = {}) {
    const base = window.NotesPane ? window.NotesPane.templateText(key) : "";
    const text = base.split("\n").map((line) => (Object.hasOwn(replacements, line) ? replacements[line] : line)).join("\n");
    if (!(await showComposer())) return;
    if (String(getComposerContent()).trim()) insertComposerTemplate(text);
    else setComposerContent(text);
    focusComposerEditor();
  }

  /** 用一段现成的 Markdown 开一篇新笔记草稿（比如错因标签下的“汇成笔记”）：只放进新建区，用户写完自己保存。 */
  async function newFromText(markdown) {
    const text = String(markdown ?? "");
    if (!(await showComposer())) return false;
    if (String(getComposerContent()).trim()) insertComposerTemplate(text);
    else setComposerContent(text);
    focusComposerEditor();
    return true;
  }

  /** 按 id 打开（从“串联”面板、预览卡跳过来）：列表里有就用列表里的，没有就单独取一次。 */
  async function openNoteById(id) {
    let note = notes.find((item) => item.id === id);
    if (!note && hooks?.api) {
      try { note = await hooks.api(`/api/notes/${id}`); } catch (_error) { note = null; }
    }
    if (note) await openNote(note);
  }

  async function editNote(note) {
    if (paneState && paneState.note.id === note.id && paneState.mode === "edit") {
      window.NotesPane?.setOpen(true);
      return;
    }
    if (!(await closeNote())) return;
    const host = $("#notes-pane-note");
    if (!host) return;
    const owner = hooks?.getUser ? hooks.getUser()?.id : null;
    const epoch = hooks?.getEpoch ? hooks.getEpoch() : null;
    currentNoteId = note.id;
    markSelected();
    window.NotesPane?.setMode("note", noteTitle(note) || "笔记");
    window.NotesPane?.setOpen(true);

    const editor = h("div", null, "notes-editor");
    const head = h("div", null, "notes-pane-head");
    const titleInput = h("input", null, "notes-edit-title");
    titleInput.type = "text";
    titleInput.maxLength = 200;
    titleInput.value = note.title || "";
    titleInput.placeholder = "标题（可选，空则取正文首行）";
    titleInput.setAttribute("aria-label", "笔记标题");
    const tools = h("div", null, "notes-pane-tools");
    const pinButton = h("button", note.pinned ? "取消置顶" : "置顶", "notes-action");
    pinButton.type = "button";
    const historyButton = h("button", "历史", "notes-action");
    historyButton.type = "button";
    historyButton.setAttribute("aria-label", "查看这条笔记的历史版本");
    historyButton.hidden = !window.NotesHistory;
    const deleteButton = h("button", "删除", "notes-action is-danger");
    deleteButton.type = "button";
    deleteButton.setAttribute("aria-label", "删除这条笔记");
    tools.append(pinButton, historyButton, deleteButton);
    head.append(titleInput, tools);

    const meta = h("div", null, "notes-pane-meta");
    const tagInput = h("input", null, "notes-edit-tags");
    tagInput.type = "text";
    tagInput.value = (note.tags || []).join(" ");
    tagInput.placeholder = "标签，用逗号或空格分隔";
    tagInput.setAttribute("aria-label", "笔记标签");
    meta.append(tagInput);
    if (note.problem_id) {
      // 本应用没有独立的题目详情路由，关联题目用“只看关联这题的笔记”代替跳转。
      const problemChip = h("button", `📝 ${note.problem_title || `题目 ${note.problem_id}`}`, "notes-chip notes-chip-sm notes-problem-chip");
      problemChip.type = "button";
      problemChip.setAttribute("aria-label", `只看关联「${note.problem_title || note.problem_id}」的笔记`);
      problemChip.addEventListener("click", () => setProblemFilter(note.problem_id, note.problem_title));
      meta.append(problemChip);
    }
    const toc = h("div", null, "notes-pane-toc");

    // N5：正文优先挂所见即所得编辑器；OYEditor 缺失或初始化抛错时回退到 textarea。
    const bodyHost = h("div", null, "notes-edit-body");
    let editCtl = null;
    let area = null;
    try {
      if (window.NotesEditor && typeof window.NotesEditor.mountEdit === "function"
          && !(typeof window.NotesEditor.isFallback === "function" && window.NotesEditor.isFallback())) {
        editCtl = window.NotesEditor.mountEdit(bodyHost, note.content || "");
      }
    } catch (_error) {
      editCtl = null;
    }
    if (!editCtl) {
      area = h("textarea", null, "notes-edit-content");
      area.rows = 12;
      area.maxLength = NOTE_CONTENT_MAX;
      area.value = note.content || "";
      area.setAttribute("aria-label", "笔记正文");
      bodyHost.append(area);
    }

    const blocks = h("div", null, "notes-block-row");
    blocks.setAttribute("role", "group");
    blocks.setAttribute("aria-label", "插入积木");
    blocks.append(h("span", "插入：", "notes-template-label"));
    for (const block of window.NotesPane ? window.NotesPane.BLOCKS : []) {
      const add = h("button", block.label, "notes-chip notes-chip-sm");
      add.type = "button";
      add.dataset.block = block.key;
      add.addEventListener("click", () => {
        const current = String(readBody() ?? "").replace(/\s+$/, "");
        const next = current ? `${current}\n\n${block.text}` : block.text;
        if (editCtl && typeof editCtl.setMarkdown === "function") editCtl.setMarkdown(next);
        else if (area) area.value = next;
        if (editCtl && typeof editCtl.focus === "function") editCtl.focus();
        else area?.focus();
        state?.saver?.schedule();
      });
      blocks.append(add);
    }
    // 看效果：编辑器（外部打包，没有源码）里荧光笔、提示框、折叠答案只显示成 Markdown 原文；
    // 打开后在正文下面实时显示排好版的样子，边写边看。只是看，不改内容、不存状态。
    const previewToggle = h("button", "看效果", "notes-chip notes-chip-sm notes-preview-toggle");
    previewToggle.type = "button";
    previewToggle.setAttribute("aria-pressed", "false");
    previewToggle.title = "在下面实时显示排好版的样子（荧光笔、提示框、折叠答案）";
    blocks.append(previewToggle);
    const preview = h("div", null, "notes-body notes-rich notes-edit-preview");
    preview.hidden = true;
    preview.setAttribute("aria-label", "排版效果预览");
    let previewTimer = 0;
    let previewShown = null;
    const paintPreview = () => {
      const text = String(readBody() ?? "");
      if (text === previewShown) return;
      previewShown = text;
      const drawingIds = hooks?.getDrawingIds ? hooks.getDrawingIds() : null;
      preview.replaceChildren(renderNoteMarkdown(bodyWithoutDerivedTitle({ ...note, title: titleInput.value, content: text }), { drawingIds }));
      window.NotesPane?.enhanceCode(preview, hooks?.notify);
    };
    previewToggle.addEventListener("click", () => {
      const on = preview.hidden;
      preview.hidden = !on;
      previewToggle.setAttribute("aria-pressed", on ? "true" : "false");
      previewToggle.textContent = on ? "收起效果" : "看效果";
      if (previewTimer) window.clearInterval(previewTimer);
      previewTimer = 0;
      if (!on) return;
      previewShown = null;
      paintPreview();
      previewTimer = window.setInterval(() => { if (paneState === state) paintPreview(); }, 700);
    });
    const row = h("div", null, "notes-editor-row");
    const done = h("button", "完成", "notes-action primary");
    done.type = "button";
    done.title = "保存并收起（Ctrl/⌘ + S 随时保存）";
    const discard = h("button", "放弃本次修改", "notes-action");
    discard.type = "button";
    const saveBar = h("p", null, "notes-save-bar");
    const error = h("p", "", "notes-notice");
    error.hidden = true;
    error.setAttribute("role", "status");
    row.append(done, discard, saveBar);
    // N1：关联区（出链 + 反向链接），由 NotesLinks 异步填充。
    const related = h("div", null, "nl-related");
    related.setAttribute("data-nl-related", "");
    editor.append(head, meta, toc, blocks, bodyHost, preview, row, error, related);
    host.replaceChildren(editor);
    window.NotesLinks?.renderRelated?.(editor, note.id);
    window.NotesPane?.renderToc(toc, note.content || "", bodyHost);
    // 打开时不抢焦点（会把整页滚走）；编辑区从顶部开始显示。
    const paneEl = $("#notes-pane");
    if (paneEl) paneEl.scrollTop = 0;

    const readBody = () => (editCtl ? editCtl.getMarkdown() : area.value);
    const readAll = () => ({ title: titleInput.value, content: String(readBody() ?? ""), tags: parseTags(tagInput.value) });
    let watcher = 0;
    let state = null;
    const current = () => paneState === state && state !== null
      && (hooks?.getUser ? hooks.getUser()?.id === owner : true)
      && (hooks?.getEpoch ? hooks.getEpoch() === epoch : true);
    const refreshList = () => {
      if (!current()) return;
      sortNotes();
      renderList();
      window.NotesPane?.setMode("note", noteTitle(note) || "笔记");
    };

    // 停手 1.5 秒自动保存（带 base_updated_at，别处改过会停下来问）；没有 NotesHistory 时退回手动保存。
    const saver = window.NotesHistory?.autosave({
      note, read: readAll, bar: saveBar,
      onSaved: (updated) => {
        if (!current()) return;
        Object.assign(note, updated);
        window.NotesPane?.renderToc(toc, updated.content || "", bodyHost);
        refreshList();
      },
      onTheirs: (latest) => {
        if (!current()) return;
        Object.assign(note, latest);
        state.teardown();
        void openNote(note);
      },
    });
    saver?.acceptLoaded?.();

    state = {
      note,
      mode: "edit",
      saver,
      flush: async () => (saver ? saver.flush() : readBody() === note.content),
      dirty: () => (saver ? saver.dirty() : readBody() !== note.content),
      teardown: () => {
        if (watcher) window.clearInterval(watcher);
        if (previewTimer) window.clearInterval(previewTimer);
        saver?.stop();
        if (editCtl && typeof editCtl.destroy === "function") {
          try { editCtl.destroy(); } catch { /* 卸载失败不影响收起 */ }
        }
        editCtl = null;
        if (paneState === state) {
          paneState = null;
          currentNoteId = null;
          host.replaceChildren();
          markSelected();
          window.NotesPane?.setMode("new");
        }
      },
      refreshHead: () => { pinButton.textContent = note.pinned ? "取消置顶" : "置顶"; },
    };
    paneState = state;

    if (saver) {
      const offer = saver.pendingDraft();
      if (offer) {
        error.hidden = false;
        error.replaceChildren(
          document.createTextNode(`这台设备上有一份（${window.NotesHistory.when(offer.at)}）没保存上的修改。`),
        );
        const restore = h("button", "恢复它", "notes-action");
        restore.type = "button";
        restore.addEventListener("click", () => {
          titleInput.value = offer.title || "";
          tagInput.value = (offer.tags || []).join(" ");
          if (editCtl && typeof editCtl.setMarkdown === "function") editCtl.setMarkdown(offer.content);
          else if (area) area.value = offer.content;
          error.hidden = true;
          saver.schedule();
        });
        const drop = h("button", "不要了", "notes-action");
        drop.type = "button";
        drop.addEventListener("click", () => { saver.discardDraft(); error.hidden = true; });
        error.append(restore, drop);
      }
      const nudge = () => saver.schedule();
      for (const type of ["input", "keyup", "paste", "drop", "change", "click"]) editor.addEventListener(type, nudge);
      // 工具栏/斜杠菜单可能挂在编辑区外面：每 4 秒再对比一次内容，漏掉的改动也会被保存。
      watcher = window.setInterval(() => { if (paneState === state) saver.schedule(); }, 4000);
      editor.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === "s") {
          event.preventDefault();
          void saver.flush();
        }
      });
    }

    pinButton.addEventListener("click", async () => {
      await state.flush();
      await togglePin(note);
      saver?.rebase?.(note.updated_at);
      state.refreshHead();
    });
    historyButton.addEventListener("click", async () => {
      await state.flush();
      window.NotesHistory?.openHistory(note, {
        onRestored: (updated) => {
          Object.assign(note, updated);
          state.teardown();
          refreshAfterRestore(note);
        },
      });
    });
    deleteButton.addEventListener("click", () => void removeNote(note));

    discard.addEventListener("click", async () => {
      const changed = saver ? (saver.everSaved || saver.dirty()) : readBody() !== note.content;
      if (changed && !(hooks?.confirm ? hooks.confirm("放弃这次打开编辑以来的所有修改，回到打开时的样子？（改过的内容会存进历史版本）") : true)) return;
      discard.disabled = true;
      try {
        const restored = saver ? await saver.revert() : null;
        if (!current()) return;
        if (restored) Object.assign(note, restored);
        state.teardown();
        refreshList();
        void openNote(note);
      } catch (requestError) {
        discard.disabled = false;
        error.hidden = false;
        error.textContent = requestError?.message || "没能撤回，请稍后重试。";
      }
    });

    done.addEventListener("click", async () => {
      const raw = readBody();
      if (!String(raw ?? "").trim()) {
        error.hidden = false;
        error.textContent = "至少写点什么";
        return;
      }
      if (String(raw ?? "").length > NOTE_CONTENT_MAX) {
        error.hidden = false;
        error.textContent = `正文不能超过 ${NOTE_CONTENT_MAX} 字，请精简后再保存。`;
        return;
      }
      done.disabled = true;
      if (saver) {
        if (!(await closeNote())) {
          done.disabled = false;
          return;
        }
        refreshAfterClose();
        window.NotesConnect?.forget?.(note.id);
        void openNote(note);
        return;
      }
      try {
        const updated = await hooks.api(`/api/notes/${note.id}`, {
          method: "PUT",
          body: JSON.stringify({ title: titleInput.value.trim(), content: raw, tags: parseTags(tagInput.value) }),
        });
        if (!current()) return;
        Object.assign(note, updated);
        state.teardown();
        refreshAfterClose();
        void openNote(note);
        hooks?.notify?.("笔记已更新。");
      } catch (requestError) {
        if (!current()) return;
        error.hidden = false;
        error.textContent = requestError?.message || "保存失败，请稍后重试。";
        done.disabled = false;
      }
    });
  }

  function refreshAfterClose() {
    sortNotes();
    renderList();
  }

  function refreshAfterRestore(note) {
    sortNotes();
    renderList();
    void openNote(note);
  }

  /** 从别的笔记的 [[链接]] 跳过来：按标题搜，正好有这一篇就直接在右边打开。 */
  async function openByTitle(title) {
    const search = $("#notes-search");
    if (search) search.value = title;
    query = String(title ?? "");
    await reloadList();
    const hit = notes.find((note) => noteTitle(note) === title);
    if (hit) await openNote(hit);
  }

  function bindDom() {
    const saveButton = $("#notes-save");
    if (saveButton && !saveButton.dataset.notesBound) {
      saveButton.dataset.notesBound = "1";
      saveButton.addEventListener("click", () => void saveComposer());
    }
    const search = $("#notes-search");
    if (search && !search.dataset.notesBound) {
      search.dataset.notesBound = "1";
      search.addEventListener("input", () => {
        if (searchTimer) window.clearTimeout(searchTimer);
        searchTimer = window.setTimeout(() => {
          query = search.value;
          void reloadList();
        }, SEARCH_DELAY);
      });
    }
    const more = $("#notes-more");
    if (more && !more.dataset.notesBound) {
      more.dataset.notesBound = "1";
      more.addEventListener("click", () => void loadMore());
    }
    const composer = $("#notes-composer");
    if (composer && !composer.dataset.notesDraftBound) {
      composer.dataset.notesDraftBound = "1";
      for (const type of ["input", "keyup", "paste", "drop", "change"]) composer.addEventListener(type, saveComposerDraft);
      composer.addEventListener("keydown", (event) => {
        if ((event.ctrlKey || event.metaKey) && !event.altKey && String(event.key).toLowerCase() === "s") {
          event.preventDefault();
          void saveComposer();
        }
      });
    }
    const newOptions = $("#notes-new-options");
    if (newOptions && !newOptions.dataset.notesBound && window.NotesPane) {
      newOptions.dataset.notesBound = "1";
      for (const template of window.NotesPane.TEMPLATES) {
        const option = h("button", template.label, "notes-new-option");
        option.type = "button";
        option.dataset.template = template.key;
        option.addEventListener("click", () => startNewFromTemplate(template.key));
        newOptions.append(option);
      }
    }
    const templateRow = $("#notes-template-row");
    if (templateRow && !templateRow.dataset.notesBound && window.NotesPane) {
      templateRow.dataset.notesBound = "1";
      templateRow.append(h("span", "用模板开头：", "notes-template-label"));
      for (const template of window.NotesPane.TEMPLATES.filter((item) => item.key !== "blank")) {
        const option = h("button", template.label, "notes-chip notes-chip-sm");
        option.type = "button";
        option.dataset.template = template.key;
        option.addEventListener("click", () => startNewFromTemplate(template.key));
        templateRow.append(option);
      }
    }
    const back = $("#notes-pane-back");
    if (back && !back.dataset.notesBound) {
      back.dataset.notesBound = "1";
      back.addEventListener("click", () => void showComposer({ open: false }));
    }
    const trash = $("#notes-trash");
    if (trash && !trash.dataset.notesBound) {
      trash.dataset.notesBound = "1";
      trash.hidden = !window.NotesHistory;
      trash.addEventListener("click", () => window.NotesHistory?.openTrash({
        onRestored: () => void reloadList(),
      }));
    }
    mounted = true;
  }

  /** 进入笔记页：拉题目下拉 + 第一页列表；消费 openWithProblem 留下的预填。 */
  async function load() {
    generation += 1;
    const token = takeToken();
    bindDom();
    await ensureProblems(token);
    if (!alive(token)) return;
    await reloadList();
    if (!alive(token)) return;
    if (pendingPrefill) applyPrefill();
    else restoreComposerDraft();
  }

  /** 登出 / 换账号：丢掉所有数据、作废在途请求。 */
  function reset() {
    if (paneState) paneState.teardown();
    paneState = null;
    currentNoteId = null;
    window.NotesPane?.setMode("new");
    window.NotesPane?.setOpen(false);
    generation += 1;
    listRequest += 1;
    const saveButton = $("#notes-save");
    if (saveButton) saveButton.disabled = false;
    if (searchTimer) window.clearTimeout(searchTimer);
    searchTimer = null;
    loading = false;
    notes.length = 0;
    total = 0;
    hasMore = false;
    query = "";
    activeTag = "";
    problemFilter = null;
    problems = [];
    problemsLoaded = false;
    pendingPrefill = null;
    const search = $("#notes-search");
    if (search) search.value = "";
    $("#notes-list")?.replaceChildren();
    const status = $("#notes-status");
    if (status) status.hidden = true;
    const more = $("#notes-more");
    if (more) more.hidden = true;
    $("#notes-tag-row")?.replaceChildren();
    clearComposer();
  }

  function snapshot() {
    return {
      count: notes.length, total, hasMore, query, activeTag,
      problemFilter: problemFilter ? { ...problemFilter } : null,
      pendingPrefill: pendingPrefill ? { ...pendingPrefill } : null,
      problemsLoaded, loading,
    };
  }

  window.Notes = Object.freeze({
    escapeHtml, parseTags, noteTitle, cardHeading, buildNotesQuery, renderNoteMarkdown, collectTags, noteTime,
    configure, load, loadMore, openWithProblem, reset, snapshot,
    openNote, openNoteById, editNote, openByTitle, showComposer, newFromTemplate, newFromText, currentNoteId: () => currentNoteId,
    NOTE_CONTENT_MAX,
    getComposerContent, setComposerContent, focusComposer, insertComposerTemplate,
    generation: () => generation,
  });
})();
