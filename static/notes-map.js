"use strict";

/* 笔记思维导图（static/notes-map.js）：把一篇笔记的标题 / 小标题 / 列表画成一张可收起展开的导图，
   再加一枝“关联”（提到的笔记、相关错题、相关套路）。只读，不新增任务，不改笔记内容。
   对外契约：window.NotesMap = {
     // 纯函数（Node 测试直接调用，不碰 DOM）
     cleanInline, textWidth, fitLabel, parseOutline, withConnections, layout, defaultCollapsed,
     // 运行态
     configure, build,
   }
   - parseOutline(markdown, title)：# 标题 → 层级；列表按缩进嵌套；> [!易错] 这类提示框成一个小节点；
     代码块、表格、图片跳过；没有标题也没有列表时，取每段第一句。最多 MAX_NODES 个节点。
   - layout(root, collapsed)：从左到右的树形布局，返回 { nodes, edges, width, height }。
   - build(host, options)：在 host 里画出工具栏和 SVG，返回 { setConnections, expandAll, collapseTo, destroy }。
   安全约束：文字一律 textContent；SVG 用 createElementNS + setAttribute；颜色全部走样式表里的主题令牌，
   不写行内 style、不拼 HTML 字符串。 */
(() => {
  const MAX_NODES = 220;
  const MAX_DEPTH = 7;
  const BRANCH_COLORS = 6;
  const LABEL_MAX_PX = 250;
  const NODE_H = 30;
  const ROOT_H = 40;
  const GAP_X = 46;
  const GAP_Y = 10;
  const PAD = 22;
  const AUTO_COLLAPSE_ABOVE = 60;
  const GROUP_LIMIT = 8;
  const SVG_NS = "http://www.w3.org/2000/svg";

  let hooks = {};
  function configure(next) {
    hooks = next || {};
  }

  /* ───────────── 纯函数 ───────────── */

  function cleanInline(text) {
    return String(text ?? "")
      .replace(/<[^>]+>/g, "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/\[\[(?:题:|笔记:)?([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_all, target, alias) => alias || target)
      .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
      .replace(/==([^=]+)==/g, "$1")
      .replace(/\$\$?([^$]+)\$\$?/g, "$1")
      .replace(/(\*\*|__|~~|`)/g, "")
      .replace(/(^|\s)\*([^*\s][^*]*)\*/g, "$1$2")
      .replace(/\s+/g, " ")
      .trim();
  }

  // 全角字（中日韩、全角标点）按字号算一个字宽，其余按 0.58 个字宽估算；SVG 里没法排版，只能先量好。
  function charWidth(ch, fontPx) {
    const code = ch.codePointAt(0);
    return code >= 0x2e80 || (code >= 0xff00 && code <= 0xffef) ? fontPx : fontPx * 0.58;
  }

  function textWidth(text, fontPx = 13) {
    let width = 0;
    for (const ch of String(text ?? "")) width += charWidth(ch, fontPx);
    return width;
  }

  function fitLabel(text, maxPx = LABEL_MAX_PX, fontPx = 13) {
    const value = String(text ?? "");
    if (textWidth(value, fontPx) <= maxPx) return value;
    let out = "";
    let width = fontPx; // 给末尾的省略号留位置
    for (const ch of value) {
      const next = charWidth(ch, fontPx);
      if (width + next > maxPx) break;
      out += ch;
      width += next;
    }
    return `${out.trimEnd()}…`;
  }

  function leadingWidth(whitespace) {
    let width = 0;
    for (const ch of whitespace) width += ch === "\t" ? 4 : 1;
    return width;
  }

  /** 笔记正文 → 树。节点：{ text, full, kind, level?, headingIndex?, children }。 */
  function parseOutline(markdown, title) {
    const root = {
      text: cleanInline(title) || "笔记",
      full: String(title ?? "").trim() || "笔记",
      kind: "root",
      depth: 0,
      children: [],
    };
    let count = 0;
    let truncated = false;
    let headingIndex = -1;
    const headings = [root];
    let list = [];
    const paragraphs = [];
    let fence = "";

    const add = (parent, node) => {
      if (count >= MAX_NODES) {
        truncated = true;
        return null;
      }
      count += 1;
      node.depth = (parent.depth || 0) + 1;
      node.children = [];
      parent.children.push(node);
      return node;
    };

    for (const raw of String(markdown ?? "").replace(/\r\n?/g, "\n").split("\n")) {
      const fenceMatch = /^\s*(```|~~~)/.exec(raw);
      if (fenceMatch) {
        if (!fence) fence = fenceMatch[1];
        else if (fenceMatch[1] === fence) fence = "";
        continue;
      }
      if (fence || !raw.trim()) continue;

      const heading = /^(#{1,6})\s+(.+?)\s*#*\s*$/.exec(raw);
      if (heading) {
        headingIndex += 1;
        const text = cleanInline(heading[2]);
        if (!text) continue;
        const level = heading[1].length;
        while (headings.length > 1 && headings[headings.length - 1].level >= level) headings.pop();
        const node = add(headings[headings.length - 1], {
          text, full: text, kind: "heading", level, headingIndex,
        });
        if (node) headings.push(node);
        list = [];
        continue;
      }

      const callout = /^\s*>\s*\[!([^\]\s]+)\][+-]?\s*(.*)$/.exec(raw);
      if (callout) {
        const detail = cleanInline(callout[2]);
        const text = detail ? `${callout[1]}：${detail}` : callout[1];
        add(headings[headings.length - 1], { text, full: text, kind: "callout" });
        list = [];
        continue;
      }
      if (/^\s*>/.test(raw) || /^\s*\|/.test(raw) || /^\s*(={3,}|-{3,}|\*{3,})\s*$/.test(raw)) continue;

      const item = /^(\s*)(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?(.+)$/.exec(raw);
      if (item) {
        const text = cleanInline(item[2]);
        if (!text) continue;
        const indent = leadingWidth(item[1]);
        while (list.length && list[list.length - 1].indent >= indent) list.pop();
        let parent = list.length ? list[list.length - 1].node : headings[headings.length - 1];
        while (parent.depth >= MAX_DEPTH && parent.parent) parent = parent.parent; // 太深就挂到上一层，不再往下长
        const node = add(parent, { text, full: text, kind: "item" });
        if (node) {
          node.parent = parent;
          list.push({ indent, node });
        }
        continue;
      }

      if (/^\s/.test(raw)) continue; // 缩进的续行，属于上一条
      list = [];
      const first = cleanInline(raw).split(/[。！？!?；;]/)[0].trim();
      if (first) paragraphs.push(first);
    }

    if (!root.children.length) {
      for (const text of paragraphs.slice(0, 12)) add(root, { text, full: text, kind: "para" });
    }
    // 去掉只为回溯父节点留的引用，避免树里出现环（JSON、深拷贝都不安全）。
    (function strip(node) {
      delete node.parent;
      node.children.forEach(strip);
    }(root));
    root.truncated = truncated;
    return root;
  }

  /** 在根节点下加一枝“关联”：提到的笔记 / 被提到 / 相关错题 / 相关套路 / 同标签笔记。没有数据就原样返回。 */
  function withConnections(root, data) {
    if (!data) return root;
    const groups = [
      ["相关错题", data.mistakes, (item) => ({ type: "mistake", id: item.id }), (item) => item.title],
      ["相关套路", data.patterns, (item) => ({ type: "pattern", pattern: item }), (item) => item.title],
      ["这篇提到的笔记", data.note_mentions, (item) => ({ type: "note", id: item.id }), (item) => item.title],
      ["提到这篇的笔记", data.mentioned_by, (item) => ({ type: "note", id: item.id }), (item) => item.title],
      ["同标签的笔记", data.same_tag, (item) => ({ type: "note", id: item.id }), (item) => item.title],
    ].filter(([, list]) => Array.isArray(list) && list.length);
    if (!groups.length) return root;
    const branch = { text: "关联", full: "关联", kind: "assoc", depth: 1, children: [] };
    for (const [label, list, action, title] of groups) {
      const group = { text: label, full: label, kind: "assoc-group", depth: 2, children: [] };
      for (const item of list.slice(0, GROUP_LIMIT)) {
        const text = cleanInline(title(item)) || "（无标题）";
        group.children.push({ text, full: text, kind: "assoc-item", depth: 3, children: [], action: action(item) });
      }
      branch.children.push(group);
    }
    return { ...root, children: [...root.children, branch] };
  }

  function countNodes(node) {
    return 1 + node.children.reduce((sum, child) => sum + countNodes(child), 0);
  }

  function collapseFrom(root, depth) {
    const collapsed = new Set();
    (function walk(node, id, level) {
      if (level >= depth && node.children.length) collapsed.add(id);
      node.children.forEach((child, index) => walk(child, `${id}.${index}`, level + 1));
    }(root, "0", 0));
    return collapsed;
  }

  function visibleCount(node, id, collapsed) {
    if (collapsed.has(id)) return 1;
    return 1 + node.children.reduce((sum, child, index) => sum + visibleCount(child, `${id}.${index}`, collapsed), 0);
  }

  /** 节点不多就全部展开；很多时从深到浅逐层收起，直到画面里的节点不超过 AUTO_COLLAPSE_ABOVE 个。返回要收起的节点 id 集合。 */
  function defaultCollapsed(root) {
    if (countNodes(root) <= AUTO_COLLAPSE_ABOVE) return new Set();
    let collapsed = new Set();
    for (const depth of [3, 2, 1]) {
      collapsed = collapseFrom(root, depth);
      if (visibleCount(root, "0", collapsed) <= AUTO_COLLAPSE_ABOVE) break;
    }
    return collapsed;
  }

  /** 从左到右的树形布局。同一深度的节点对齐成一列；父节点落在子节点的正中。 */
  function layout(root, collapsed = new Set()) {
    const nodes = [];
    const edges = [];
    const columns = [];

    (function measure(node, id, depth, branch) {
      node.id = id;
      node.depthIndex = depth;
      node.branch = branch;
      const fontPx = depth === 0 ? 16 : 13;
      const hasKids = node.children.length > 0;
      node.visible = !collapsed.has(id) ? node.children : [];
      node.label = fitLabel(node.text, depth === 0 ? 220 : LABEL_MAX_PX, fontPx);
      const jump = node.kind === "heading" ? 18 : 0;
      const chevron = hasKids ? 20 : 0;
      node.w = Math.max(depth === 0 ? 84 : 48, Math.ceil(textWidth(node.label, fontPx)) + (depth === 0 ? 32 : 24) + jump + chevron);
      node.h = depth === 0 ? ROOT_H : NODE_H;
      columns[depth] = Math.max(columns[depth] || 0, node.w);
      node.visible.forEach((child, index) => measure(
        child, `${id}.${index}`, depth + 1, depth === 0 ? index % BRANCH_COLORS : branch,
      ));
    }(root, "0", 0, -1));

    let cursor = PAD;
    (function place(node) {
      if (!node.visible.length) {
        node.cy = cursor + node.h / 2;
        cursor += node.h + GAP_Y;
        return;
      }
      node.visible.forEach(place);
      node.cy = (node.visible[0].cy + node.visible[node.visible.length - 1].cy) / 2;
    }(root));

    const left = [PAD];
    for (let depth = 1; depth < columns.length; depth += 1) left[depth] = left[depth - 1] + columns[depth - 1] + GAP_X;

    (function collect(node, parent) {
      node.x = left[node.depthIndex];
      node.y = node.cy - node.h / 2;
      nodes.push(node);
      if (parent) {
        edges.push({
          id: node.id, branch: node.branch,
          x1: parent.x + parent.w, y1: parent.cy, x2: node.x, y2: node.cy,
        });
      }
      node.visible.forEach((child) => collect(child, node));
    }(root, null));

    const width = left[columns.length - 1] + columns[columns.length - 1] + PAD;
    const height = Math.max(cursor - GAP_Y + PAD, ROOT_H + PAD * 2);
    return { nodes, edges, width, height };
  }

  /* ───────────── 运行态 ───────────── */

  function svgEl(tag, attrs = {}) {
    const element = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) element.setAttribute(key, String(value));
    return element;
  }

  function htmlEl(tag, text, className) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined && text !== null) element.textContent = text;
    return element;
  }

  function curve(edge) {
    const mid = (edge.x1 + edge.x2) / 2;
    return `M ${edge.x1} ${edge.y1} C ${mid} ${edge.y1}, ${mid} ${edge.y2}, ${edge.x2} ${edge.y2}`;
  }

  function runAction(action) {
    if (!action) return;
    if (action.type === "note") hooks.openNote?.(action.id);
    else if (action.type === "mistake") hooks.openMistake?.(action.id);
    else if (action.type === "pattern") hooks.openPattern?.(action.pattern);
  }

  /** options: { markdown, title, connections, onJump(headingIndex) } */
  function build(host, options = {}) {
    let tree = parseOutline(options.markdown, options.title);
    const baseTree = tree;
    let collapsed = defaultCollapsed(tree);
    let byId = new Map();

    const bar = htmlEl("div", null, "nm-bar");
    const expandAll = htmlEl("button", "全部展开", "nm-tool");
    const collapseAll = htmlEl("button", "只看第一层", "nm-tool");
    const fit = htmlEl("button", "适合宽度", "nm-tool");
    fit.setAttribute("aria-pressed", "false");
    for (const button of [expandAll, collapseAll, fit]) button.type = "button";
    const hint = htmlEl("p", "点一下分支收起或展开；点标题旁的 ↗ 跳到正文。", "nm-hint");
    bar.append(expandAll, collapseAll, fit, hint);
    const scroller = htmlEl("div", null, "nm-scroll");
    const empty = htmlEl("p", "这篇笔记还没有标题或列表。加几个小标题（# 开头）或列表，导图就会自己长出来。", "nm-empty");
    host.replaceChildren(bar, scroller);
    host.classList.add("nm-host");

    function ids(node, id = "0", out = []) {
      out.push([id, node]);
      node.children.forEach((child, index) => ids(child, `${id}.${index}`, out));
      return out;
    }

    function draw() {
      const result = layout(tree, collapsed);
      byId = new Map(ids(tree));
      const svg = svgEl("svg", {
        class: "nm-svg", viewBox: `0 0 ${result.width} ${result.height}`,
        width: result.width, height: result.height, role: "group",
        "aria-label": `「${tree.text}」的思维导图`,
      });
      const edgeLayer = svgEl("g", { class: "nm-edges" });
      for (const edge of result.edges) edgeLayer.append(svgEl("path", { class: `nm-edge nm-b${edge.branch}`, d: curve(edge) }));
      const nodeLayer = svgEl("g", { class: "nm-nodes" });
      for (const node of result.nodes) nodeLayer.append(drawNode(node));
      svg.append(edgeLayer, nodeLayer);
      scroller.replaceChildren(tree.children.length ? svg : empty);
      expandAll.disabled = collapseAll.disabled = fit.disabled = !tree.children.length;
    }

    function drawNode(node) {
      const hasKids = node.children.length > 0;
      const isCollapsed = collapsed.has(node.id);
      const group = svgEl("g", {
        class: `nm-node nm-b${node.branch < 0 ? "r" : node.branch} nm-${node.kind}${isCollapsed ? " is-collapsed" : ""}`,
        "data-id": node.id,
        transform: `translate(${node.x} ${node.y})`,
        tabindex: 0,
        role: "button",
        "aria-label": hasKids ? `${node.full}，${isCollapsed ? "已收起" : "已展开"}，按回车切换` : node.full,
      });
      if (hasKids) group.setAttribute("aria-expanded", String(!isCollapsed));
      const title = svgEl("title");
      title.textContent = node.full;
      group.append(title);
      group.append(svgEl("rect", { class: "nm-box", width: node.w, height: node.h, rx: node.depthIndex === 0 ? 12 : 9 }));
      const label = svgEl("text", {
        class: "nm-text", x: node.depthIndex === 0 ? 16 : 12, y: node.h / 2, "dominant-baseline": "central",
      });
      label.textContent = node.label;
      group.append(label);
      let right = node.w;
      if (hasKids) {
        right -= 18;
        const mark = svgEl("text", { class: "nm-fold", x: right + 9, y: node.h / 2, "text-anchor": "middle", "dominant-baseline": "central" });
        mark.textContent = isCollapsed ? `+${countNodes(node) - 1}` : "–";
        group.append(mark);
      }
      if (node.kind === "heading" && Number.isInteger(node.headingIndex)) {
        right -= 16;
        const jump = svgEl("g", {
          class: "nm-jump", "data-jump": node.headingIndex, tabindex: 0, role: "button", "aria-label": `跳到正文：${node.full}`,
        });
        jump.append(svgEl("rect", { x: right, y: 4, width: 16, height: node.h - 8, rx: 4, class: "nm-jump-hit" }));
        const arrow = svgEl("text", { x: right + 8, y: node.h / 2, "text-anchor": "middle", "dominant-baseline": "central" });
        arrow.textContent = "↗";
        jump.append(arrow);
        group.append(jump);
      }
      return group;
    }

    function activate(target) {
      const jump = target.closest(".nm-jump");
      if (jump) {
        options.onJump?.(Number(jump.getAttribute("data-jump")));
        return;
      }
      const group = target.closest(".nm-node");
      if (!group) return;
      const node = byId.get(group.getAttribute("data-id"));
      if (!node) return;
      if (node.action) {
        runAction(node.action);
      } else if (node.children.length) {
        const id = group.getAttribute("data-id");
        if (collapsed.has(id)) collapsed.delete(id);
        else collapsed.add(id);
        draw();
        scroller.querySelector(`.nm-node[data-id="${id}"]`)?.focus({ preventScroll: true });
      } else if (node.kind === "heading" && Number.isInteger(node.headingIndex)) {
        options.onJump?.(node.headingIndex);
      }
    }

    scroller.addEventListener("click", (event) => activate(event.target));
    scroller.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      if (!event.target.closest?.(".nm-node, .nm-jump")) return;
      event.preventDefault();
      activate(event.target);
    });
    fit.addEventListener("click", () => {
      const on = !scroller.classList.contains("is-fit");
      scroller.classList.toggle("is-fit", on);
      fit.setAttribute("aria-pressed", String(on));
      fit.textContent = on ? "原大小" : "适合宽度";
    });
    expandAll.addEventListener("click", () => { collapsed = new Set(); draw(); });
    collapseAll.addEventListener("click", () => {
      collapsed = new Set();
      for (const [id, node] of ids(tree)) if (id.split(".").length === 2 && node.children.length) collapsed.add(id);
      draw();
    });

    draw();
    return {
      /** 关联数据到了就多画一枝；没有数据时保持原样。 */
      setConnections(data) {
        tree = withConnections(baseTree, data);
        collapsed = defaultCollapsed(tree);
        draw();
      },
      destroy() {
        host.classList.remove("nm-host");
        host.replaceChildren();
      },
    };
  }

  window.NotesMap = Object.freeze({
    MAX_NODES, cleanInline, textWidth, fitLabel, parseOutline, withConnections, layout, defaultCollapsed,
    configure, build,
  });
})();
