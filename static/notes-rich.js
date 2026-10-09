/* N2 笔记富渲染：$行内公式$、$$块级公式$$、```mermaid``` 流程图、
   ![alt](attachment:ID) 图片附件；与 N1 的 [[ ]] 链接管线组合。

   管线接缝（必须与 notes.js / notes-links.js 保持一致）：
   ① notes.js renderNoteMarkdown 开头调用 window.NotesRich.prepareSource(source)
      把 mermaid 围栏 / 块级公式 / 行内公式 换成私有区占位符（占位符里没有反引号、
      方括号、星号，不会被 notes.js 的 inline() 二次拆开）；
   ② notes.js 照常把 ```/~~~ 围栏落成 <pre><code>、行内 `code` 落成 <code>；
   ③ window.NotesLinks.attachInlineLinks(root) 只对代码外文本落位 [[ ]]；
   ④ renderNoteMarkdown 结尾调用 window.NotesRich.attachRich(root)：
      解析 attachment 占位 span 成 <img>（仅本人附件 id），再把公式/mermaid 占位
      替换为 KaTeX / Mermaid 渲染产物（DOMParser + importNode 落位，不拼 HTML 字符串）。

   安全契约与 N1 一致：只用 textContent、setAttribute、data-*、事件委托与 DOM 工厂；
   vendor（KaTeX / Mermaid）按需动态注入，断网后由 sw.js 运行时缓存兜底。 */
(() => {
  "use strict";

  // ── 占位符协议：私有使用区字符，绝不与正文混淆 ──
  const BLOCK_OPEN = String.fromCharCode(0xE010);
  const BLOCK_CLOSE = String.fromCharCode(0xE011);
  const INLINE_OPEN = String.fromCharCode(0xE012);
  const INLINE_CLOSE = String.fromCharCode(0xE013);
  const BLOCK_RE = new RegExp("^" + BLOCK_OPEN + "(\\d+)" + BLOCK_CLOSE + "$");
  const INLINE_RE = new RegExp(INLINE_OPEN + "(\\d+)" + INLINE_CLOSE, "g");

  // 占位内容注册表：prepareSource 写入，attachRich 消费。
  const registry = [];
  // 本人附件 id 集合（configure 注入；列表接口回传的全部本人附件 id）。
  let ownedIds = new Set();

  const PUB = {};

  // ── 第一段：mermaid 围栏识别（其余围栏保持原样交给 notes.js 代码块）──
  function splitMermaidFences(text) {
    const lines = String(text).split("\n");
    const out = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (line.startsWith("```")) {
        const info = line.slice(3).trim();
        let j = i + 1;
        const body = [];
        while (j < lines.length && !lines[j].startsWith("```")) {
          body.push(lines[j]);
          j += 1;
        }
        const closed = j < lines.length;
        if (closed) j += 1;
        if (info === "mermaid") {
          const id = registry.length;
          registry.push({ kind: "block-mermaid", code: body.join("\n") });
          out.push(BLOCK_OPEN + id + BLOCK_CLOSE);
        } else {
          out.push(line, ...body);
          if (closed) out.push("```");
        }
        i = j;
      } else {
        out.push(line);
        i += 1;
      }
    }
    return out.join("\n");
  }

  // ── 非代码段内的公式替换（块级 $$...$$ 先于行内 $...$）──
  function transformTextSegment(segment) {
    let next = segment.replace(/\$\$([\s\S]+?)\$\$/g, (_match, inner) => {
      const id = registry.length;
      registry.push({ kind: "block-math", code: inner.trim() });
      return "\n" + BLOCK_OPEN + id + BLOCK_CLOSE + "\n";
    });
    next = next.replace(/\$([^\$\n]+?)\$/g, (_match, inner) => {
      const id = registry.length;
      registry.push({ kind: "inline-math", code: inner });
      return INLINE_OPEN + id + INLINE_CLOSE;
    });
    return next;
  }

  // 纯函数：源码预处理。测试直接调用本函数断言分段是否正确。
  function prepareSource(text) {
    registry.length = 0;
    const work = splitMermaidFences(String(text ?? ""));
    let ranges = [];
    if (window.NotesLinks && typeof window.NotesLinks.codeRanges === "function") {
      ranges = window.NotesLinks.codeRanges(work);
    }
    const pieces = [];
    let pos = 0;
    for (const [cs, ce] of ranges) {
      if (cs > pos) pieces.push(work.slice(pos, cs));
      pieces.push({ __code: work.slice(cs, ce) });
      pos = ce;
    }
    if (pos < work.length) pieces.push(work.slice(pos));
    return pieces
      .map((piece) => (piece && piece.__code !== undefined ? piece.__code : transformTextSegment(piece)))
      .join("");
  }

  // ── attachRich：attachment 图片 + 公式 / mermaid 占位落位 ──
  function resolveImages(root) {
    if (!root || typeof root.querySelectorAll !== "function") return;
    const holders = root.querySelectorAll("[data-nr-img]");
    Array.from(holders).forEach((holder) => {
      const src = holder.getAttribute("data-src") || "";
      const alt = holder.getAttribute("data-alt") || "";
      const match = /^attachment:(\d+)$/.exec(src.trim());
      if (match && ownedIds.has(Number(match[1]))) {
        const img = document.createElement("img");
        img.setAttribute("src", "/api/notes/attachments/" + match[1]);
        img.setAttribute("alt", alt);
        img.setAttribute("loading", "lazy");
        img.setAttribute("class", "nr-image");
        holder.replaceWith(img);
      } else {
        // 外链 / data: / 伪协议 / 他人附件 id 一律按纯文本显示，不发请求。
        holder.replaceWith(document.createTextNode("![" + alt + "](" + src + ")"));
      }
    });
  }

  function collectBlocks(root, sink) {
    const kids = root.childNodes || [];
    for (const child of Array.from(kids)) {
      if (child.nodeType !== 1) continue;
      const sub = child.childNodes || [];
      const alone = sub.length === 1 && sub[0].nodeType === 3 && BLOCK_RE.test(sub[0].textContent || "");
      if (alone) sink(child, sub[0].textContent);
      else collectBlocks(child, sink);
    }
  }

  function expandInlinePlaceholders(root, sink) {
    const pending = [];
    const walk = (node) => {
      const kids = node.childNodes || [];
      for (const child of Array.from(kids)) {
        if (child.nodeType === 3) {
          if (INLINE_RE.test(child.textContent || "")) pending.push(child);
        } else if (child.nodeType === 1) {
          walk(child);
        }
      }
    };
    walk(root);
    pending.forEach((textNode) => {
      const parts = [];
      let last = 0;
      INLINE_RE.lastIndex = 0;
      const content = textNode.textContent || "";
      for (const match of content.matchAll(INLINE_RE)) {
        if (match.index > last) parts.push(document.createTextNode(content.slice(last, match.index)));
        const box = buildInlineMathBox(Number(match[1]));
        sink.push(box);
        parts.push(box);
        last = match.index + match[0].length;
      }
      if (last < content.length) parts.push(document.createTextNode(content.slice(last)));
      textNode.replaceWith(...parts);
    });
  }

  function buildFallback(code, label) {
    const pre = document.createElement("pre");
    pre.setAttribute("class", "nr-fallback");
    const codeEl = document.createElement("code");
    codeEl.textContent = code;
    const msg = document.createElement("span");
    msg.textContent = label + "无法渲染";
    pre.appendChild(codeEl);
    pre.appendChild(msg);
    return pre;
  }

  function buildInlineMathBox(id) {
    const box = document.createElement("span");
    box.setAttribute("class", "nr-inline-math");
    box.__nrCode = registry[id] ? registry[id].code : "";
    box.__nrDisplay = false;
    return box;
  }

  // vendor 按需注入：只有真出现公式 / mermaid 时才加载。
  let katexPromise = null;
  let mermaidPromise = null;

  function injectStylesheet(href) {
    return new Promise((resolve, reject) => {
      const link = document.createElement("link");
      link.setAttribute("rel", "stylesheet");
      link.setAttribute("href", href);
      link.onload = () => resolve();
      link.onerror = () => reject(new Error("样式加载失败"));
      document.head.appendChild(link);
    });
  }

  function injectScript(src) {
    return new Promise((resolve, reject) => {
      const script = document.createElement("script");
      script.setAttribute("src", src);
      script.onload = () => resolve();
      script.onerror = () => reject(new Error("脚本加载失败"));
      document.head.appendChild(script);
    });
  }

  function ensureKatex() {
    if (window.katex) return Promise.resolve();
    if (katexPromise) return katexPromise;
    katexPromise = (async () => {
      await injectStylesheet("/static/vendor/katex/katex.min.css?v=1");
      await injectScript("/static/vendor/katex/katex.min.js?v=1");
    })();
    return katexPromise;
  }

  function ensureMermaid() {
    if (window.mermaid) return Promise.resolve();
    if (mermaidPromise) return mermaidPromise;
    mermaidPromise = (async () => {
      await injectScript("/static/vendor/mermaid/mermaid.min.js?v=1");
      window.mermaid.initialize({ startOnLoad: false, securityLevel: "strict" });
    })();
    return mermaidPromise;
  }

  function nodeFromHtmlString(html) {
    const doc = new DOMParser().parseFromString("<div>" + html + "</div>", "text/html");
    return document.importNode(doc.body.firstChild, true);
  }

  function fillMathBox(box) {
    if (!window.katex) return;
    try {
      const html = window.katex.renderToString(box.__nrCode, {
        throwOnError: false,
        trust: false,
        displayMode: Boolean(box.__nrDisplay),
      });
      const node = nodeFromHtmlString(html);
      if (box.__nrDisplay) node.setAttribute("class", "nr-block-math");
      box.replaceWith(node);
    } catch (_err) {
      const fallback = buildFallback(box.__nrCode, "公式");
      box.replaceWith(fallback);
    }
  }

  async function fillMermaidBox(box) {
    if (!window.mermaid) return;
    try {
      const id = "nr-mm-" + box.__nrId;
      const rendered = await window.mermaid.render(id, box.__nrCode);
      const doc = new DOMParser().parseFromString(rendered.svg, "image/svg+xml");
      const node = document.importNode(doc.documentElement, true);
      const wrap = document.createElement("div");
      wrap.setAttribute("class", "nr-mermaid");
      wrap.appendChild(node);
      box.replaceWith(wrap);
    } catch (_err) {
      const fallback = buildFallback(box.__nrCode, "图表");
      box.replaceWith(fallback);
    }
  }

  function attachRich(root) {
    resolveImages(root);

    const mathBoxes = [];
    const mermaidBoxes = [];

    collectBlocks(root, (boxEl, text) => {
      const id = Number(BLOCK_RE.exec(text)[1]);
      const entry = registry[id];
      if (!entry) return;
      if (entry.kind === "block-math") {
        const holder = document.createElement("div");
        holder.__nrCode = entry.code;
        holder.__nrDisplay = true;
        boxEl.replaceWith(holder);
        mathBoxes.push(holder);
      } else if (entry.kind === "block-mermaid") {
        const holder = document.createElement("div");
        holder.__nrCode = entry.code;
        holder.__nrId = id;
        boxEl.replaceWith(holder);
        mermaidBoxes.push(holder);
      }
    });

    const inlineBoxes = [];
    expandInlinePlaceholders(root, inlineBoxes);

    // 实测结论：strict CSP（style-src 'self'、无 unsafe-inline）下 Mermaid 产物 SVG
    // 大量内联样式被浏览器拦截，节点填充/文字颜色全失，图不可读；按任务约定绝不放宽 CSP，
    // 因此 mermaid 一律降级为代码块 + “图表无法渲染”提示。
    mermaidBoxes.forEach((box) => box.replaceWith(buildFallback(box.__nrCode, "图表")));

    if (mathBoxes.length || inlineBoxes.length) {
      ensureKatex()
        .then(() => {
          mathBoxes.forEach(fillMathBox);
          inlineBoxes.forEach(fillMathBox);
        })
        .catch(() => {
          mathBoxes.concat(inlineBoxes).forEach((box) => box.replaceWith(buildFallback(box.__nrCode, "公式")));
        });
    }
  }

  // ── 编辑器接线：工具栏 / 实时预览 / 粘贴拖拽上传 / 灯箱 ──
  const MERMAID_TEMPLATE = [
    "```mermaid",
    "flowchart TD",
    "  A[开始] --> B{条件成立?}",
    "  B -- 是 --> C[执行处理]",
    "  B -- 否 --> D[跳过]",
    "  C --> D",
    "```",
    "",
  ].join("\n");

  const TABLE_TEMPLATE = [
    "",
    "| 列一 | 列二 | 列三 |",
    "| --- | --- | --- |",
    "| 内容 | 内容 | 内容 |",
    "",
  ].join("\n");

  function insertAtCursor(ta, before, after, placeholder) {
    const start = ta.selectionStart !== undefined ? ta.selectionStart : ta.value.length;
    const end = ta.selectionEnd !== undefined ? ta.selectionEnd : ta.value.length;
    const selected = ta.value.slice(start, end) || placeholder || "";
    ta.setRangeText(before + selected + after, start, end, "end");
    ta.dispatchEvent(new Event("input", { bubbles: true }));
  }

  const TOOLS = [
    { label: "B", aria: "加粗", before: "**", after: "**", placeholder: "加粗文字" },
    { label: "I", aria: "斜体", before: "*", after: "*", placeholder: "斜体文字" },
    { label: "</>", aria: "行内代码", before: "`", after: "`", placeholder: "code" },
    { label: "{}", aria: "插入代码块", before: "\n```\n", after: "\n```\n", placeholder: "代码" },
    { label: "∑", aria: "插入行内公式", before: "$", after: "$", placeholder: "x^2" },
    { label: "M", aria: "插入 mermaid 流程图模板", block: MERMAID_TEMPLATE },
    { label: "图", aria: "插入图片附件", file: true },
    { label: "表", aria: "插入表格模板", block: TABLE_TEMPLATE },
  ];

  function uploadFile(file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/notes/attachments");
      xhr.setRequestHeader("X-CSRF-Protection", "1");
      xhr.upload.onprogress = (event) => {
        if (event.lengthComputable && onProgress) {
          onProgress(event.loaded / event.total);
        }
      };
      xhr.onload = () => {
        try {
          const data = JSON.parse(xhr.responseText);
          if (xhr.status >= 200 && xhr.status < 300) resolve(data);
          else reject(new Error(data.detail || "图片上传失败"));
        } catch (_err) {
          reject(new Error("图片上传失败"));
        }
      };
      xhr.onerror = () => reject(new Error("网络错误，图片上传失败"));
      const form = new FormData();
      form.append("file", file);
      xhr.send(form);
    });
  }

  function setStatus(statusEl, text) {
    statusEl.textContent = text;
  }

  async function uploadAndInsert(ta, statusEl, file) {
    setStatus(statusEl, "正在上传图片…");
    try {
      const data = await uploadFile(file, (ratio) => {
        setStatus(statusEl, "正在上传图片 " + Math.round(ratio * 100) + "%");
      });
      ownedIds.add(Number(data.id));
      insertAtCursor(ta, "", "", data.markdown + "\n");
      setStatus(statusEl, "");
    } catch (err) {
      setStatus(statusEl, err.message || "图片上传失败");
    }
  }

  function buildToolbar(bar, ta, statusEl) {
    TOOLS.forEach((tool) => {
      const button = document.createElement("button");
      button.setAttribute("type", "button");
      button.setAttribute("aria-label", tool.aria);
      button.textContent = tool.label;
      button.addEventListener("click", () => {
        ta.focus();
        if (tool.file) {
          pickImageFile(ta, statusEl);
        } else if (tool.block) {
          insertAtCursor(ta, "", "", tool.block);
        } else {
          insertAtCursor(ta, tool.before, tool.after, tool.placeholder);
        }
      });
      bar.appendChild(button);
    });
  }

  let fileInput = null;
  function pickImageFile(ta, statusEl) {
    if (!fileInput) {
      fileInput = document.createElement("input");
      fileInput.setAttribute("type", "file");
      fileInput.setAttribute("accept", "image/*");
      fileInput.addEventListener("change", () => {
        const files = Array.from(fileInput.files || []);
        files.forEach((file) => uploadAndInsert(ta, statusEl, file));
        fileInput.value = "";
      });
    }
    fileInput.click();
  }

  function initComposer() {
    const ta = document.getElementById("notes-content");
    if (!ta || ta.dataset.nrRichInit === "1") return;
    ta.dataset.nrRichInit = "1";

    const bar = document.createElement("div");
    bar.setAttribute("class", "nr-toolbar");
    const status = document.createElement("div");
    status.setAttribute("class", "nr-uploading");
    status.setAttribute("aria-live", "polite");

    const editor = document.createElement("div");
    editor.setAttribute("class", "nr-editor");
    const preview = document.createElement("div");
    preview.setAttribute("class", "nr-preview");
    preview.setAttribute("id", "notes-preview");

    ta.parentNode.insertBefore(bar, ta);
    ta.parentNode.insertBefore(editor, ta);
    editor.appendChild(ta);
    editor.appendChild(preview);
    editor.parentNode.insertBefore(status, editor.nextSibling);

    buildToolbar(bar, ta, status);

    let timer = null;
    ta.addEventListener("input", () => {
      if (timer) clearTimeout(timer);
      timer = setTimeout(() => {
        const root = window.Notes.renderNoteMarkdown(ta.value);
        preview.replaceChildren(root);
      }, 300);
    });

    ta.addEventListener("paste", (event) => {
      const files = event.clipboardData && event.clipboardData.files
        ? Array.from(event.clipboardData.files)
        : [];
      const images = files.filter((file) => /^image\//.test(file.type));
      if (!images.length) return;
      event.preventDefault();
      images.forEach((file) => uploadAndInsert(ta, status, file));
    });

    ta.addEventListener("dragover", (event) => {
      event.preventDefault();
    });
    ta.addEventListener("drop", (event) => {
      const files = event.dataTransfer && event.dataTransfer.files
        ? Array.from(event.dataTransfer.files)
        : [];
      const images = files.filter((file) => /^image\//.test(file.type));
      if (!images.length) return;
      event.preventDefault();
      images.forEach((file) => uploadAndInsert(ta, status, file));
    });
  }

  // ── 灯箱：点击笔记内附件图片打开大图，Esc 关闭并归还焦点 ──
  let lightbox = null;
  let lightboxReturnFocus = null;

  function closeLightbox() {
    if (!lightbox) return;
    lightbox.remove();
    lightbox = null;
    if (lightboxReturnFocus && typeof lightboxReturnFocus.focus === "function") {
      lightboxReturnFocus.focus();
    }
    lightboxReturnFocus = null;
  }

  function openLightbox(src, alt) {
    closeLightbox();
    lightboxReturnFocus = document.activeElement;
    lightbox = document.createElement("div");
    lightbox.setAttribute("class", "nr-lightbox");
    lightbox.setAttribute("role", "dialog");
    lightbox.setAttribute("aria-label", "图片预览");
    const img = document.createElement("img");
    img.setAttribute("src", src);
    img.setAttribute("alt", alt);
    const close = document.createElement("button");
    close.setAttribute("type", "button");
    close.setAttribute("aria-label", "关闭图片预览");
    close.textContent = "关闭";
    close.addEventListener("click", closeLightbox);
    lightbox.addEventListener("click", (event) => {
      if (event.target === lightbox) closeLightbox();
    });
    lightbox.appendChild(img);
    lightbox.appendChild(close);
    document.body.appendChild(lightbox);
    close.focus();
    document.addEventListener("keydown", function onKey(event) {
      if (event.key === "Escape") {
        closeLightbox();
        document.removeEventListener("keydown", onKey);
      }
    });
  }

  function onDocumentClick(event) {
    const target = event.target;
    if (!target || target.tagName !== "IMG") return;
    if (!target.classList || !target.classList.contains("nr-image")) return;
    openLightbox(target.getAttribute("src"), target.getAttribute("alt") || "");
  }

  // ── 导出 ──
  PUB.prepareSource = prepareSource;
  PUB.attachRich = attachRich;
  PUB.configure = (options) => {
    if (options && options.ownedAttachmentIds) {
      ownedIds = new Set(options.ownedAttachmentIds);
    }
  };
  PUB.insertAtCursor = insertAtCursor;
  PUB.initComposer = initComposer;
  PUB.knownRegistry = () => registry.slice();
  PUB.MERMAID_TEMPLATE = MERMAID_TEMPLATE;

  if (typeof window !== "undefined") {
    window.NotesRich = PUB;
    document.addEventListener("click", onDocumentClick);
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initComposer);
    } else {
      initComposer();
    }
  }
})();
