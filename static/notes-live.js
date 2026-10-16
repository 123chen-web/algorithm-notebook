"use strict";

/* 编辑时也看得见积木：给所见即所得编辑器挂一个只管“显示”的 ProseMirror 插件。
   - ==重点== 显示成荧光笔（两头的 == 变淡）；> [!易错] 这类引用块显示成对应颜色的提示框（[!类型] 变淡）。
   - 只加装饰（Decoration），不改文档、不改保存出来的 Markdown；撤销、复制、导出都不受影响。
   - 编辑器是外部打包的（static/vendor/editor/，没有源码），实例上有 get editor() 返回 Tiptap 编辑器。
     ProseMirror 的 Plugin / Decoration / DecorationSet 没有导出，就从编辑器现有插件身上取它们的构造函数；
     取不到就什么也不做，编辑器照常可用。
   对外：window.NotesLive = { attach(editor) → 是否挂上, highlightRanges(text, codeMask), calloutHead(text), calloutKind(type) }。 */
(() => {
  // 和阅读视图（notes.js 的 CALLOUT_KINDS）同一套类型名；阅读视图里有就以它为准。
  const KINDS = {
    提示: "tip", tip: "tip", note: "tip", info: "tip", hint: "tip", 笔记: "tip",
    注意: "warn", 警告: "warn", warning: "warn", caution: "warn", attention: "warn",
    易错: "danger", 错误: "danger", 坑: "danger", danger: "danger", bug: "danger", error: "danger", failure: "danger",
    例子: "example", 例题: "example", 示例: "example", example: "example",
    结论: "ok", 总结: "ok", 要点: "ok", summary: "ok", abstract: "ok", tldr: "ok", success: "ok",
    定义: "def", 概念: "def", 定理: "def", quote: "def", definition: "def",
    答案: "answer", 自测: "answer", 思考: "answer", question: "answer", answer: "answer", faq: "answer",
  };
  const calloutKind = (type) => KINDS[String(type || "").toLowerCase()] || "tip";

  /** 一段文字里所有 ==x== 的 [起, 止)（含两头的 ==）；codeMask[i] 为真表示第 i 个字符在行内代码里，跳过。 */
  function highlightRanges(text, codeMask = null) {
    const ranges = [];
    const pattern = /==(?=[^=\s])([^=\n]*?[^=\s])==/g;
    for (let match = pattern.exec(text); match; match = pattern.exec(text)) {
      const from = match.index;
      const to = from + match[0].length;
      let inCode = false;
      if (codeMask) for (let i = from; i < to && !inCode; i += 1) inCode = Boolean(codeMask[i]);
      if (!inCode) ranges.push([from, to]);
    }
    return ranges;
  }

  /** 引用块第一行的 [!类型]（编辑器保存时会转义成 \[!类型\]，两种都认）：返回 { kind, length } 或 null。 */
  function calloutHead(text) {
    const match = /^\s*\\?\[!([^\]\\\s]{1,12})\\?\]([+-]?)/.exec(String(text || ""));
    return match ? { kind: calloutKind(match[1]), length: match[0].length } : null;
  }

  /** 从编辑器现有插件身上找 Plugin / Decoration / DecorationSet；找不到返回 null。 */
  function proseMirrorClasses(editor) {
    const state = editor?.state;
    const Plugin = state?.plugins?.[0]?.constructor;
    if (!state || typeof Plugin !== "function") return null;
    const probe = (probeState) => {
      for (const plugin of probeState.plugins) {
        const decorations = plugin?.props?.decorations;
        if (typeof decorations !== "function") continue;
        let set = null;
        try { set = decorations.call(plugin, probeState); } catch { set = null; }
        const found = set && typeof set.find === "function" ? set.find() : [];
        const Decoration = found[0]?.constructor;
        if (Decoration && typeof Decoration.inline === "function" && typeof Decoration.node === "function"
            && typeof set.constructor?.create === "function") {
          return { Plugin, Decoration, DecorationSet: set.constructor };
        }
      }
      return null;
    };
    let classes = probe(state);
    if (!classes) {
      // 当前文档里没有任何装饰时，用一份只有空段落的临时文档引出“占位提示”插件的装饰。
      try {
        const doc = editor.schema.topNodeType.createAndFill();
        classes = probe(state.constructor.create({ doc, plugins: state.plugins }));
      } catch { classes = null; }
    }
    return classes;
  }

  /** 文本块的内容拼成字符串（非文字的行内节点用占位符），下标 = 块内位置偏移；同时记下哪些字符在行内代码里。 */
  function blockText(node) {
    let text = "";
    const codeMask = [];
    node.forEach((child) => {
      if (child.isText) {
        const inCode = (child.marks || []).some((mark) => mark.type?.spec?.code || mark.type?.name === "code");
        text += child.text;
        for (let i = 0; i < child.text.length; i += 1) codeMask.push(inCode);
      } else {
        text += "￼".repeat(child.nodeSize);
        for (let i = 0; i < child.nodeSize; i += 1) codeMask.push(true);
      }
    });
    return { text, codeMask };
  }

  function buildDecorations(doc, { Decoration, DecorationSet }) {
    const decorations = [];
    doc.descendants((node, pos) => {
      if (node.type?.spec?.code) return false; // 代码块里不管
      if (node.type?.name === "blockquote") {
        const first = node.firstChild;
        const head = first && first.isTextblock ? calloutHead(blockText(first).text) : null;
        if (head) {
          decorations.push(Decoration.node(pos, pos + node.nodeSize, { class: `oy-live-callout oy-live-callout-${head.kind}` }));
          const start = pos + 2; // 引用块开始 + 第一个段落开始
          decorations.push(Decoration.inline(start, start + head.length, { class: "oy-live-marker" }));
        }
        return true;
      }
      if (!node.isTextblock) return true;
      const { text, codeMask } = blockText(node);
      const start = pos + 1;
      for (const [from, to] of highlightRanges(text, codeMask)) {
        decorations.push(Decoration.inline(start + from, start + to, { class: "oy-live-hl" }));
        decorations.push(Decoration.inline(start + from, start + from + 2, { class: "oy-live-marker" }));
        decorations.push(Decoration.inline(start + to - 2, start + to, { class: "oy-live-marker" }));
      }
      return false;
    });
    return DecorationSet.create(doc, decorations);
  }

  function attach(editor) {
    if (!editor || editor.isDestroyed || editor.__oyLive) return Boolean(editor?.__oyLive);
    if (typeof editor.registerPlugin !== "function") return false;
    const classes = proseMirrorClasses(editor);
    if (!classes) return false;
    let lastDoc = null;
    let lastSet = null;
    const plugin = new classes.Plugin({
      props: {
        decorations(state) {
          if (state.doc !== lastDoc) {
            lastDoc = state.doc;
            try { lastSet = buildDecorations(state.doc, classes); } catch { lastSet = classes.DecorationSet.empty; }
          }
          return lastSet;
        },
      },
    });
    try {
      editor.registerPlugin(plugin);
    } catch {
      return false;
    }
    editor.__oyLive = true;
    return true;
  }

  window.NotesLive = Object.freeze({ attach, highlightRanges, calloutHead, calloutKind });
})();
