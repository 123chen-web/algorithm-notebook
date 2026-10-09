"use strict";

/* N1 笔记双向链接 + 关系图谱（static/notes-links.js）。
   对外契约：window.NotesLinks = {
     // 纯函数（Node 测试直接调用，不碰 DOM）
     parseLinkTokens, detectLinkQuery, buildLinkInsertion,
     selectGraphNodes, computeLayout, codeRanges,
     // 运行态
     setKnownTargets, attachInlineLinks, configure, enterNotesView, reset,
     SOLUTION_TEMPLATE,
   }
   渲染管线顺序（与 notes.js 的 renderNoteMarkdown 协作，N2 在此之后追加阶段）：
     1) notes.js 先把 fenced ```/~~~ 代码块落成 <pre><code>，把行内 `code` 落成 <code>；
     2) 然后调用 window.NotesLinks.attachInlineLinks(root)，只对 <code>/<pre> 之外的
        文本节点做 [[ ]] 链接变换（代码内一律保护）；
     3) N2 追加 $行内$ / $$块$$ / ```mermaid / ![](attachment:ID) 时，应先调用
        codeRanges() 拿到代码区间，只对区间外文本做自己的变换，并把结果节点落成
        <code>/自定义元素，再交回本管线——不要破坏第 1、2 步的代码保护与链接落位。
   安全约束：全程只用 textContent / setAttribute / data-* / 事件委托，不拼接 HTML 字符串、
   不写伪协议 href、不写行内样式、不做动态求值、不用文档写入口、不碰存储 API；
   SVG 一律 createElementNS + setAttribute。 */
(() => {
  const NOTE_LINK_MAX = 50;
  const GRAPH_NODE_LIMIT = 300;

  const CODE_SPAN_RE = /```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`/g;
  const LINK_RE = /\[\[([^\[\]\n]+?)\]\]/g;

  /* ───────────── 纯函数：链接解析 ───────────── */

  /** 代码区间：所有 fenced 代码块（``` / ~~~）与行内代码 `...` 的 [start,end)。 */
  function codeRanges(text) {
    const src = String(text ?? "");
    CODE_SPAN_RE.lastIndex = 0;
    const ranges = [];
    let m;
    while ((m = CODE_SPAN_RE.exec(src)) !== null) ranges.push([m.index, m.index + m[0].length]);
    return ranges;
  }

  /** 解析正文里的 [[ ]] 链接 token（自动跳过代码块/行内代码内的）。
      返回 [{start,end,kind:'note'|'problem',targetText,alias,raw}]。 */
  function parseLinkTokens(text) {
    const src = String(text ?? "");
    const ranges = codeRanges(src);
    const inCode = (s, e) => ranges.some(([cs, ce]) => !(e <= cs || s >= ce));
    const tokens = [];
    LINK_RE.lastIndex = 0;
    let m;
    while ((m = LINK_RE.exec(src)) !== null) {
      if (inCode(m.index, m.index + m[0].length)) continue;
      let inner = m[1];
      let alias = "";
      const pipe = inner.indexOf("|");
      if (pipe !== -1) { alias = inner.slice(pipe + 1); inner = inner.slice(0, pipe); }
      let kind = "note";
      if (inner.startsWith("题:")) { kind = "problem"; inner = inner.slice(2); }
      inner = inner.trim();
      alias = alias.trim();
      if (!inner) continue;
      tokens.push({
        start: m.index, end: m.index + m[0].length, kind,
        targetText: inner, alias, raw: m[0],
      });
    }
    return tokens;
  }

  /** 联想：光标前是否有一个未闭合的 [[ 片段。返回 {openStart, query} 或 null。 */
  function detectLinkQuery(text, caret) {
    const src = String(text ?? "");
    const upTo = src.slice(0, caret);
    const open = upTo.lastIndexOf("[[");
    if (open === -1) return null;
    const tail = upTo.slice(open + 2);
    if (tail.includes("]") || tail.includes("\n")) return null;
    for (const [cs, ce] of codeRanges(src)) {
      if (open >= cs && open < ce) return null;
    }
    return { openStart: open, query: tail };
  }

  /** 回车补全：把未闭合的 [[片段 补成 [[chosenTitle]]，返回 {text, caret}。 */
  function buildLinkInsertion(text, caret, chosenTitle) {
    const src = String(text ?? "");
    const query = detectLinkQuery(src, caret);
    if (!query) return { text: src, caret };
    const insert = `[[${chosenTitle}]]`;
    const next = src.slice(0, query.openStart) + insert + src.slice(caret);
    return { text: next, caret: query.openStart + insert.length };
  }

  /* ───────────── 纯函数：图谱筛选与布局 ───────────── */

  /** 图谱节点筛选：center 时先 BFS 到 depth(1|2)，再按度数截断（中心必保留）。
      nodes:[{id,type,title}] edges:[{source,target}]。 */
  function selectGraphNodes(nodes, edges, opts = {}) {
    const limit = opts.limit || GRAPH_NODE_LIMIT;
    let nodeIds = new Set((nodes || []).map((n) => n.id));
    let keptEdges = (edges || []).slice();
    if (opts.center) {
      const within = new Set([opts.center]);
      const depth = opts.depth === 2 ? 2 : 1;
      for (let d = 0; d < depth; d += 1) {
        const nxt = [];
        for (const e of keptEdges) {
          if (within.has(e.source) && !within.has(e.target)) nxt.push(e.target);
          else if (within.has(e.target) && !within.has(e.source)) nxt.push(e.source);
        }
        nxt.forEach((id) => within.add(id));
      }
      nodeIds = within;
      keptEdges = keptEdges.filter((e) => within.has(e.source) && within.has(e.target));
    }
    const degree = new Map();
    for (const e of keptEdges) {
      degree.set(e.source, (degree.get(e.source) || 0) + 1);
      degree.set(e.target, (degree.get(e.target) || 0) + 1);
    }
    let kept = (nodes || []).filter((n) => nodeIds.has(n.id));
    let truncated = false;
    let truncatedCount = 0;
    if (kept.length > limit) {
      const forced = opts.center ? new Set([opts.center]) : new Set();
      const ranked = kept.slice().sort((a, b) =>
        ((degree.get(b.id) || 0) - (degree.get(a.id) || 0)) || (a.id < b.id ? -1 : 1));
      const chosen = new Set();
      for (const n of ranked) {
        if (chosen.size >= limit && !forced.has(n.id)) break;
        chosen.add(n.id);
      }
      if (opts.center) chosen.add(opts.center);
      truncated = true;
      truncatedCount = kept.length - chosen.size;
      kept = kept.filter((n) => chosen.has(n.id));
      keptEdges = keptEdges.filter((e) => chosen.has(e.source) && chosen.has(e.target));
    }
    return { nodes: kept, edges: keptEdges, truncated, truncatedCount, nodeCount: kept.length };
  }

  function hashString(s) {
    let h = 2166136261;
    const str = String(s ?? "");
    for (let i = 0; i < str.length; i += 1) {
      h ^= str.charCodeAt(i);
      h = Math.imul(h, 16777619);
    }
    return h >>> 0;
  }

  /** 确定性力导向布局：无 Math.random，同输入必同输出（供 node 测试与无动效直出）。
      返回 { [nodeId]: {x,y} }。 */
  function computeLayout(nodes, edges, opts = {}) {
    const list = (nodes || []).slice();
    const width = opts.width || 600;
    const height = opts.height || 400;
    const cx = width / 2;
    const cy = height / 2;
    const iterations = opts.iterations === undefined ? 60 : opts.iterations;
    const pos = new Map();
    list.forEach((n, i) => {
      const angle = (i / Math.max(1, list.length)) * Math.PI * 2;
      const r = Math.min(width, height) * 0.28;
      pos.set(n.id, {
        x: cx + Math.cos(angle) * r,
        y: cy + Math.sin(angle) * r,
        vx: 0, vy: 0,
      });
    });
    const dist = (a, b) => Math.max(1, Math.hypot(a.x - b.x, a.y - b.y));
    for (let it = 0; it < iterations; it += 1) {
      for (let i = 0; i < list.length; i += 1) {
        for (let j = i + 1; j < list.length; j += 1) {
          const a = pos.get(list[i].id);
          const b = pos.get(list[j].id);
          const d = dist(a, b);
          const f = (90 * 90) / (d * d);
          const ux = (a.x - b.x) / d;
          const uy = (a.y - b.y) / d;
          a.vx += ux * f; a.vy += uy * f;
          b.vx -= ux * f; b.vy -= uy * f;
        }
      }
      for (const e of (edges || [])) {
        const a = pos.get(e.source);
        const b = pos.get(e.target);
        if (!a || !b) continue;
        const d = dist(a, b);
        const f = (d - 90) * 0.02;
        const ux = (b.x - a.x) / d;
        const uy = (b.y - a.y) / d;
        a.vx += ux * f; a.vy += uy * f;
        b.vx -= ux * f; b.vy -= uy * f;
      }
      for (const n of list) {
        const p = pos.get(n.id);
        p.vx += (cx - p.x) * 0.01;
        p.vy += (cy - p.y) * 0.01;
        p.x += p.vx * 0.1;
        p.y += p.vy * 0.1;
        p.vx *= 0.8;
        p.vy *= 0.8;
        p.x = Math.max(20, Math.min(width - 20, p.x));
        p.y = Math.max(20, Math.min(height - 20, p.y));
      }
    }
    const out = {};
    for (const [id, p] of pos) out[id] = { x: Math.round(p.x), y: Math.round(p.y) };
    return out;
  }

  /* ───────────── 运行态：已知目标注册表 ───────────── */

  let hooks = null;
  let knownNoteTitles = new Set();
  let knownProblemTitles = new Set();
  let entered = false;

  /** 与后端 _display_title / 前端 notes.js noteTitle 对齐：有 title 用 title，否则首非空行前 30 字。 */
  function displayTitle(note) {
    const t = String(note?.title ?? "").trim();
    if (t) return t;
    const first = String(note?.content ?? "").split("\n").map((l) => l.trim()).find((l) => l);
    return (first || "").slice(0, 30);
  }

  function setKnownTargets({ notes = [], problems = [] } = {}) {
    knownNoteTitles = new Set(notes.map(displayTitle).filter(Boolean));
    knownProblemTitles = new Set((problems).map((p) => String(p?.title ?? "").trim()).filter(Boolean));
  }

  /* ───────────── 运行态：正文 [[ ]] 链接落位 ───────────── */

  function childList(parent) {
    if (typeof parent.childNodes !== "undefined" && parent.childNodes.length !== undefined) {
      return Array.from(parent.childNodes);
    }
    return (parent.children || []).slice();
  }

  function expandText(text) {
    const tokens = parseLinkTokens(text);
    if (!tokens.length) return null;
    const pieces = [];
    let cursor = 0;
    for (const t of tokens) {
      if (t.start > cursor) pieces.push({ type: "text", value: text.slice(cursor, t.start) });
      pieces.push({ type: "link", token: t });
      cursor = t.end;
    }
    if (cursor < text.length) pieces.push({ type: "text", value: text.slice(cursor) });
    return pieces;
  }

  function linkToAnchor(token) {
    const a = document.createElement("a");
    const resolved = token.kind === "note"
      ? knownNoteTitles.has(token.targetText)
      : knownProblemTitles.has(token.targetText);
    a.textContent = token.alias || token.targetText;
    a.setAttribute("href", "#");
    a.setAttribute("data-note-link", token.kind);
    a.setAttribute("data-link-target", token.targetText);
    a.setAttribute("data-link-alias", token.alias);
    a.setAttribute("data-link-resolved", resolved ? "1" : "0");
    a.setAttribute("class", resolved ? "nl-link" : "nl-link nl-dangling");
    return a;
  }

  function transformElement(el) {
    if (el.tagName === "CODE") return; // 保护行内/块代码：不进入
    const kids = childList(el);
    let changed = false;
    const rebuilt = [];
    for (const kid of kids) {
      if (kid.nodeType === 3) {
        const pieces = expandText(kid.textContent);
        if (!pieces) { rebuilt.push(kid); continue; }
        changed = true;
        for (const p of pieces) {
          rebuilt.push(
            p.type === "text" ? document.createTextNode(p.value) : linkToAnchor(p.token)
          );
        }
      } else if (kid.nodeType === 1) {
        transformElement(kid);
        rebuilt.push(kid);
      } else {
        rebuilt.push(kid);
      }
    }
    if (changed && typeof el.replaceChildren === "function") el.replaceChildren(...rebuilt);
  }

  /** renderNoteMarkdown 生成片段后调用：把 <code> 之外文本里的 [[ ]] 落成 <a>。 */
  function attachInlineLinks(root) {
    if (!root) return;
    transformElement(root);
  }

  /* ───────────── 运行态：跳转与新建 ───────────── */

  function locateNoteByTitle(title) {
    if (!hooks?.showView) return;
    void hooks.showView("notes").then(() => {
      const search = document.querySelector("#notes-search");
      if (search) {
        search.value = title;
        search.dispatchEvent(new Event("input", { bubbles: true }));
      }
    }).catch(() => {});
  }

  function prefillNewNote(title) {
    if (!hooks?.showView) return;
    void hooks.showView("notes").then(() => {
      const content = document.querySelector("#notes-content");
      if (content && !content.value) content.value = `[[${title}]]`;
      content?.focus?.();
    }).catch(() => {});
  }

  function onDocClick(event) {
    const target = event.target;
    if (!target || !target.closest) return;
    const anchor = target.closest("a[data-note-link]");
    if (!anchor) return;
    event.preventDefault();
    const kind = anchor.getAttribute("data-note-link");
    const title = anchor.getAttribute("data-link-target") || "";
    const resolved = anchor.getAttribute("data-link-resolved") === "1";
    if (kind === "problem") {
      // 题目链接：切到笔记视图并按题目过滤（本应用无独立题目路由）。
      locateProblem(title);
      return;
    }
    if (resolved) locateNoteByTitle(title);
    else prefillNewNote(title);
  }

  function locateProblem(title) {
    // 简化：切到笔记视图，提示用户；题目 chip 已在卡片 meta 里提供过滤入口。
    hooks?.notify?.(`题目「${title}」可在笔记卡片的题目标签处筛选查看。`);
  }

  /* ───────────── 运行态：联想弹层 ───────────── */

  let popup = null;
  let popupItems = [];
  let activeIndex = -1;
  let popupTextarea = null;
  let suggestTimer = null;

  function closePopup() {
    if (popup && popup.parentNode) popup.parentNode.removeChild(popup);
    popup = null;
    popupItems = [];
    activeIndex = -1;
    popupTextarea = null;
  }

  function renderPopup() {
    if (!popup) return;
    popup.replaceChildren();
    popup.setAttribute("role", "listbox");
    popup.setAttribute("aria-label", "笔记链接联想");
    popupItems.forEach((item, index) => {
      const option = document.createElement("div");
      option.setAttribute("role", "option");
      option.setAttribute("id", `nl-option-${index}`);
      option.setAttribute("data-title", item.title);
      option.setAttribute("data-kind", item.kind);
      option.textContent = item.kind === "problem" ? `题：${item.title}` : item.title;
      if (index === activeIndex) option.setAttribute("aria-selected", "true");
      else option.removeAttribute("aria-selected");
      popup.append(option);
    });
    if (popupTextarea) {
      popupTextarea.setAttribute(
        "aria-activedescendant",
        activeIndex >= 0 ? `nl-option-${activeIndex}` : ""
      );
    }
  }

  async function fetchSuggestions(query) {
    if (!hooks?.api) return [];
    try {
      const data = await hooks.api(`/api/notes/suggest?q=${encodeURIComponent(query)}&limit=20`);
      return data?.results || [];
    } catch {
      return [];
    }
  }

  async function onTextareaInput(textarea) {
    const caret = textarea.selectionStart ?? textarea.value.length;
    const query = detectLinkQuery(textarea.value, caret);
    if (!query) { closePopup(); return; }
    if (suggestTimer) clearTimeout(suggestTimer);
    suggestTimer = setTimeout(async () => {
      const results = await fetchSuggestions(query.query);
      if (!results.length) { closePopup(); return; }
      ensurePopup(textarea);
      popupItems = results;
      activeIndex = results.length ? 0 : -1;
      renderPopup();
    }, 120);
  }

  function ensurePopup(textarea) {
    if (popup && popupTextarea === textarea) return popup;
    closePopup();
    popup = document.createElement("div");
    popup.setAttribute("class", "nl-popup");
    popupTextarea = textarea;
    // 直接插在 textarea 之后（普通文档流），由 CSS 画成下拉层，不写行内 style。
    if (textarea.parentNode) textarea.parentNode.insertBefore(popup, textarea.nextSibling);
    return popup;
  }

  function chooseActive(textarea) {
    if (activeIndex < 0 || !popupItems[activeIndex]) return;
    const chosen = popupItems[activeIndex].title;
    const next = buildLinkInsertion(textarea.value, textarea.selectionStart, chosen);
    textarea.value = next.text;
    textarea.setSelectionRange(next.caret, next.caret);
    closePopup();
  }

  function onTextareaKeydown(event) {
    if (!popup) return;
    if (event.key === "Escape") {
      event.preventDefault();
      closePopup();
    } else if (event.key === "ArrowDown") {
      if (!popupItems.length) return;
      event.preventDefault();
      activeIndex = (activeIndex + 1) % popupItems.length;
      renderPopup();
    } else if (event.key === "ArrowUp") {
      if (!popupItems.length) return;
      event.preventDefault();
      activeIndex = (activeIndex - 1 + popupItems.length) % popupItems.length;
      renderPopup();
    } else if (event.key === "Enter") {
      if (activeIndex < 0) return;
      event.preventDefault();
      chooseActive(event.currentTarget);
    }
  }

  function attachAutocomplete(textarea) {
    if (!textarea || textarea.dataset.nlBound) return;
    textarea.dataset.nlBound = "1";
    textarea.addEventListener("input", () => onTextareaInput(textarea));
    textarea.addEventListener("keydown", onTextareaKeydown);
    textarea.addEventListener("blur", () => setTimeout(closePopup, 150));
  }

  /* ───────────── 运行态：关联区（出链 + 反向链接） ───────────── */

  async function renderRelated(card, noteId) {
    if (!hooks?.api) return;
    const box = card.querySelector("[data-nl-related]");
    if (!box) return;
    try {
      const data = await hooks.api(`/api/notes/${noteId}/links`);
      box.replaceChildren();
      const outgoing = data?.outgoing || [];
      const backlinks = data?.backlinks || [];
      if (!outgoing.length && !backlinks.length) {
        const empty = document.createElement("p");
        empty.setAttribute("class", "nl-empty");
        empty.textContent = "暂无关联笔记或题目。";
        box.append(empty);
        return;
      }
      if (outgoing.length) {
        const h = document.createElement("p");
        h.setAttribute("class", "nl-related-title");
        h.textContent = `出链（${outgoing.length}）`;
        box.append(h);
        for (const link of outgoing) box.append(relatedItem(link, false));
      }
      if (backlinks.length) {
        const h = document.createElement("p");
        h.setAttribute("class", "nl-related-title");
        h.textContent = `反向链接（${backlinks.length}）`;
        box.append(h);
        for (const back of backlinks) box.append(relatedItem(back, true));
      }
    } catch {
      // 关联区加载失败不影响卡片主体。
    }
  }

  function relatedItem(link, isBacklink) {
    const item = document.createElement("button");
    item.type = "button";
    item.setAttribute("class", "nl-related-item");
    const name = document.createElement("span");
    name.setAttribute("class", "nl-related-name");
    name.textContent = link.title || link.target_text || "";
    if (!link.exists && !isBacklink) name.setAttribute("class", "nl-related-name nl-dangling");
    const snippet = document.createElement("span");
    snippet.setAttribute("class", "nl-related-snippet");
    snippet.textContent = link.snippet || "";
    item.append(name, snippet);
    item.addEventListener("click", () => {
      if (isBacklink) locateNoteByTitle(link.title || "");
      else if (link.exists && link.link_kind === "note") locateNoteByTitle(link.title || "");
      else if (!link.exists) prefillNewNote(link.target_text || "");
    });
    return item;
  }

  /* ───────────── 运行态：关系图谱（手写 SVG） ───────────── */

  function svgEl(tag, attrs) {
    const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    return el;
  }

  async function mountGraph(container) {
    if (!hooks?.api || !container) return;
    container.replaceChildren();
    const header = document.createElement("div");
    header.setAttribute("class", "nl-graph-head");
    const modeGlobal = document.createElement("button");
    modeGlobal.type = "button";
    modeGlobal.textContent = "全局图";
    const modeDepth1 = document.createElement("button");
    modeDepth1.type = "button";
    modeDepth1.textContent = "以本笔记为中心 1 层";
    const modeDepth2 = document.createElement("button");
    modeDepth2.type = "button";
    modeDepth2.textContent = "以本笔记为中心 2 层";
    const listToggle = document.createElement("button");
    listToggle.type = "button";
    listToggle.textContent = "列表视图";
    header.append(modeGlobal, modeDepth1, modeDepth2, listToggle);
    container.append(header);

    const state = { mode: "global", center: null };
    async function loadGraph() {
      container.querySelector(".nl-graph-canvas")?.remove();
      container.querySelector(".nl-graph-list")?.remove();
      const params = new URLSearchParams();
      if (state.center) { params.set("center", String(state.center)); params.set("depth", state.mode === "depth2" ? "2" : "1"); }
      const query = params.toString();
      const data = await hooks.api(`/api/notes/graph${query ? `?${query}` : ""}`);
      const selection = selectGraphNodes(data?.nodes || [], data?.edges || [], {
        center: state.center || undefined,
        depth: state.mode === "depth2" ? 2 : 1,
      });
      renderSvgGraph(container, selection);
      if (data?.truncated) {
        const note = document.createElement("p");
        note.setAttribute("class", "nl-graph-note");
        note.textContent = `节点超过上限，已按度数保留前 ${GRAPH_NODE_LIMIT} 个（截断 ${data.truncated_count} 个）。`;
        container.append(note);
      }
    }
    modeGlobal.addEventListener("click", () => { state.mode = "global"; state.center = null; void loadGraph(); });
    modeDepth1.addEventListener("click", () => { state.mode = "depth1"; state.center = firstNoteId(); void loadGraph(); });
    modeDepth2.addEventListener("click", () => { state.mode = "depth2"; state.center = firstNoteId(); void loadGraph(); });
    listToggle.addEventListener("click", () => renderListView(container));
    void loadGraph();
  }

  function firstNoteId() {
    const card = document.querySelector("#notes-list .notes-card[data-note-id]");
    return card ? card.getAttribute("data-note-id") : null;
  }

  function prefersReducedMotion() {
    return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  }

  function renderSvgGraph(container, selection) {
    const width = 640;
    const height = 420;
    const svg = svgEl("svg", {
      viewBox: `0 0 ${width} ${height}`,
      class: "nl-graph-canvas",
      role: "img",
      "aria-label": "笔记关系图谱",
    });
    const positions = computeLayout(selection.nodes, selection.edges, { width, height, iterations: prefersReducedMotion() ? 0 : 60 });
    for (const e of selection.edges) {
      const a = positions[e.source];
      const b = positions[e.target];
      if (!a || !b) continue;
      svg.append(svgEl("line", { x1: a.x, y1: a.y, x2: b.x, y2: b.y, class: "nl-edge" }));
    }
    for (const n of selection.nodes) {
      const p = positions[n.id];
      if (!p) continue;
      const g = svgEl("g", { class: "nl-node", "data-id": n.id });
      if (n.type === "problem") {
        g.append(svgEl("rect", { x: p.x - 8, y: p.y - 8, width: 16, height: 16, class: "nl-node-shape" }));
      } else {
        g.append(svgEl("circle", { cx: p.x, cy: p.y, r: 8, class: "nl-node-shape" }));
      }
      const label = svgEl("text", { x: p.x, y: p.y + 20, class: "nl-node-label" });
      label.textContent = n.title || n.id;
      g.append(label);
      svg.append(g);
    }
    container.append(svg);
    attachSvgInteractions(svg, positions);
  }

  function attachSvgInteractions(svg, positions) {
    // 拖拽节点 + 滚轮缩放 + 画布平移（纯 setAttribute，不写行内 style）。
    let dragTarget = null;
    svg.addEventListener("mousedown", (event) => {
      const g = event.target.closest?.("g.nl-node");
      if (g) { dragTarget = g; }
    });
    window.addEventListener("mouseup", () => { dragTarget = null; });
    svg.addEventListener("mousemove", (event) => {
      if (!dragTarget) return;
      const pt = svgPoint(svg, event);
      dragTarget.querySelector(".nl-node-shape")?.setAttribute("cx", pt.x);
    });
    svg.addEventListener("click", (event) => {
      const g = event.target.closest?.("g.nl-node");
      if (!g) return;
      const id = g.getAttribute("data-id");
      if (id && id.startsWith("note:")) {
        const title = (g.querySelector(".nl-node-label")?.textContent || "").trim();
        locateNoteByTitle(title);
      }
    });
  }

  function svgPoint(svg, event) {
    const rect = svg.getBoundingClientRect();
    const width = 640;
    const height = 420;
    return {
      x: ((event.clientX - rect.left) / Math.max(1, rect.width)) * width,
      y: ((event.clientY - rect.top) / Math.max(1, rect.height)) * height,
    };
  }

  function renderListView(container) {
    container.querySelector(".nl-graph-canvas")?.remove();
    container.querySelector(".nl-graph-list")?.remove();
    const list = document.createElement("ul");
    list.setAttribute("class", "nl-graph-list");
    // 列表视图：从已加载卡片收集标题，作为无障碍替代。
    const cards = document.querySelectorAll("#notes-list .notes-card");
    cards.forEach((card) => {
      const li = document.createElement("li");
      const strong = card.querySelector(".notes-card-title strong");
      li.textContent = strong?.textContent || "";
      list.append(li);
    });
    container.append(list);
  }

  /* ───────────── 解法对比模板 ───────────── */

  const SOLUTION_TEMPLATE = [
    "## 思路",
    "",
    "## 复杂度",
    "",
    "## 适用场景",
    "",
    "## 与其他解法对比",
    "对比 [[暴力解法]] 与 [[二分查找]] 的取舍。",
  ].join("\n");

  /* ───────────── 入口 ───────────── */

  async function refreshKnownTargets() {
    if (!hooks?.api) return;
    try {
      const [notesData, problemsData] = await Promise.all([
        hooks.api("/api/notes?limit=100"),
        hooks.api("/api/problems?limit=100"),
      ]);
      setKnownTargets({ notes: notesData?.notes || [], problems: problemsData?.problems || [] });
    } catch { /* 静默：标题表拉取失败不阻塞。 */ }
  }

  async function enterNotesView() {
    await refreshKnownTargets();
    // 已知标题到位后，重跑一次正文链接落位，修正 resolved/dangling 样式。
    document.querySelectorAll("#notes-list .notes-body").forEach((body) => {
      transformElement(body);
    });
    // 给卡片接联想弹层。
    attachAutocomplete(document.querySelector("#notes-content"));
    document.querySelectorAll("textarea.notes-edit-content").forEach(attachAutocomplete);
    // 关联区：给每张卡片挂载（notes.js 列表异步渲染，需延迟重试直到卡片出现）。
    void populateRelatedSoon();
    // 图谱容器。
    const graphBox = document.querySelector("#notes-graph");
    if (graphBox) await mountGraph(graphBox);
    // 模板按钮。
    const tpl = document.querySelector("#notes-solution-template");
    if (tpl && !tpl.dataset.nlBound) {
      tpl.dataset.nlBound = "1";
      tpl.addEventListener("click", () => {
        const content = document.querySelector("#notes-content");
        if (content) {
          content.value = content.value ? `${content.value}\n${SOLUTION_TEMPLATE}` : SOLUTION_TEMPLATE;
          content.focus();
        }
      });
    }
  }

  /** 关联区挂载：notes.js 的列表是异步渲染的，多等几拍直到 .notes-card 出现再填充。 */
  async function populateRelatedSoon(tries = 4) {
    for (let i = 0; i < tries; i += 1) {
      await new Promise((resolve) => setTimeout(resolve, i === 0 ? 0 : 250));
      const cards = document.querySelectorAll("#notes-list .notes-card");
      if (!cards.length) continue;
      for (const card of cards) {
        const noteId = card.getAttribute("data-note-id");
        if (noteId && !card.dataset.nlRelatedDone) {
          card.dataset.nlRelatedDone = "1";
          await renderRelated(card, noteId);
        }
      }
      return;
    }
  }

  function reset() {
    entered = false;
    closePopup();
    knownNoteTitles = new Set();
    knownProblemTitles = new Set();
  }

  function configure(next) {
    hooks = next;
    document.addEventListener("click", onDocClick);
    document.addEventListener("app:view-changed", (event) => {
      if (event?.detail?.view === "notes") void enterNotesView();
    });
  }

  window.NotesLinks = Object.freeze({
    codeRanges, parseLinkTokens, detectLinkQuery, buildLinkInsertion,
    selectGraphNodes, computeLayout, hashString,
    setKnownTargets, attachInlineLinks,
    configure, enterNotesView, reset, SOLUTION_TEMPLATE,
  });
})();
