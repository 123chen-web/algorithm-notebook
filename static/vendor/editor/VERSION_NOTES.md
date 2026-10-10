# OY Editor（欧叶OY 所见即所得编辑器）接入说明

本目录是第三方离线编辑器产物，**只放构建产物，不含源码、不含 node_modules**。
由另一套工程（Tiptap 3 + esbuild，包名 `oy-editor-build`）打包后交付，
随本仓库自托管，配合站点 `Content-Security-Policy: default-src 'self';
script-src 'self'; style-src 'self'` 使用，不引用任何 CDN。

## 文件清单

| 文件 | 说明 |
| --- | --- |
| `oy-editor.js` | esbuild 单文件 IIFE 产物，加载后挂载全局 `window.OYEditor`（约 815 KB，gzip 约 190 KB）。 |
| `oy-editor.css` | 编辑器主题，全部颜色/尺寸走宿主 CSS 变量；所有选择器以 `.oy-editor` 开头。 |
| `LICENSE` | 编辑器封装层的 MIT 许可证。 |
| `NOTICE.md` | 打包进 `oy-editor.js` 的第三方依赖及其许可证清单与全文。 |

- 上游版本：`OYEditor.version === "0.1.0"`。
- 构建时间：2026-10（随 N5 任务接入）。
- 引入方式（`static/index.html`，遵循站内 `?v=N` 版本号规则）：
  - CSS：`<link rel="stylesheet" href="/static/vendor/editor/oy-editor.css?v=2">`，放在笔记相关样式附近；
  - JS：`<script defer src="/static/vendor/editor/oy-editor.js?v=1"></script>`，放在 `notes.js` 之前。
- Service Worker（`static/sw.js`）由 `pwa-register.js` 自动收集带 `?v=` 的同源
  stylesheet/script 做离线预缓存，本目录文件无需在 `sw.js` 里手工登记。

## 对外 API（与 `README_EDITOR.md` 一致）

```js
const inst = OYEditor.create(element, {
  markdown, placeholder, readOnly, maxLength,
  onChange(markdown), onOverLimit(length, maxLength),
  uploadImage(file) -> Promise<{ src, alt }>,
  resolveImageSrc(src) -> string,
  suggestLinks(query) -> Promise<Array<{ label, kind }>>,   // kind: "note" | "problem"
  onLinkClick({ label, kind }),
  onRequestDrawing(),
  onRequestProblemPicker(),
});
// 实例：getMarkdown / setMarkdown / focus / blur / destroy /
//       insertDrawing({id,title}) / insertImage({src,alt}) /
//       insertWikilink({label,kind,alias}) / uploadImageFile(file) / isEmpty
```

存储层仍保存 Markdown 文本（`notes.content` 字段不变），保存时取 `getMarkdown()`。

## 内容能力

标题 H1–H3、粗体、斜体、删除线、行内代码、代码块（12 种语言语法高亮）、
引用、无序/有序/待办列表、分割线、GFM 表格、行内/整行 TeX 公式（KaTeX MathML）、
图片（粘贴/拖拽/斜杠菜单上传）、笔记/题目互链 `[[…]]`、画板引用 `drawing:ID`、
斜杠命令菜单、选中浮动工具栏。

## CSP 与安全

- Tiptap `injectCSS:false`，产物不创建 `<style>`、不 `document.write`、不加载字体；
- 菜单/弹层定位全部用 CSSOM（`element.style.*`），无行内 `style=` 属性、无 HTML 字符串拼接；
- 图片走宿主回调（`uploadImage`/`resolveImageSrc`），不直连任意外部源；
- KaTeX 以 `output:"mathml"` 打包进产物（公式不需要额外字体/样式请求）；
- 产物内 KaTeX 节点视图会把 KaTeX 生成的 MathML 写入节点（受控的库输出，非用户文本拼接）。

## 宿主需要提供的 CSS 变量

`oy-editor.css` 不定义 `:root` 默认值，全部变量由宿主 `static/style.css` 提供，
包括：`--paper --paper-2 --surface --ink --ink-2 --muted --accent --line
--line-strong --soft --selection --field-surface --code-surface --code-ink
--azurite --gamboge --danger-soft --success-ink --group-ink --menu-shadow-rgb
--radius --radius-sm --radius-xs --space-1…8 --text-xs…base --motion-fast
--motion-enter --motion-ease`。

其中 `--danger-ink` 编辑器用于公式错误/链接错误文字，宿主原令牌集未单列，
已在 `static/style.css` 用既有令牌补别名 `--danger-ink: var(--danger);`，
未引入新的颜色字面量。

## 降级

宿主在 `window.OYEditor` 缺失或 `OYEditor.create` 抛错时，自动回退到原有
`textarea` 纯文本输入并给出中文提示，保证笔记始终可写；逻辑见 `static/notes-editor.js`。

## 对分发副本的本地补丁（升级上游时需重新评估）

1. `oy-editor.css` 的 `.oy-editor .ProseMirror` 规则补了一行 `white-space: pre-wrap;`。
   ProseMirror 官方要求编辑区使用 pre-wrap 以正确保留连续空格与代码空白；上游产物
   遗漏该声明时，控制台会出现 “ProseMirror expects the CSS white-space property to
   be 'pre-wrap'” 警告，且连续空格显示不正确。仅改分发 CSS，未改 `oy-editor.js`；
   若日后重新打包/升级编辑器，请在上游工程确认该声明后移除本补丁。
2. `oy-editor.js` 修复“整行公式”在空段落上插入失效的问题。上游 `insertMathBlock`
   在当前段落为空时执行 `setNode('mathBlock', …)`，但 mathBlock 是 atom 块而非
   textblock，Tiptap 会告警 `Currently "setNode()" only supports text block nodes.`
   且不插入任何节点（斜杠“整行公式”在空行上 100% 复现失效）。分发副本把该分支改为
   `insertContentAt({from, to}, {type:'mathBlock', …})` 整体替换空段落，效果与
   非空段落分支一致。对应上游源码
   `tools/editor-build/src/editor.js` 的 `insertMathBlock`，建议上游工程按同样方式
   修复后重新打包、再移除本补丁。两处补丁均有静态契约测试守护
   （`tests/test_notes_editor_assets.py`）。
3. `oy-editor.css` 待办列表选择器适配 Tiptap 3 的实际 DOM。上游 CSS 写的是
   `li[data-type="taskItem"]`，但 Tiptap 3 的 TaskItem 实际渲染为
   `<li data-checked="…">`（`data-type` 只保留在 `ul[data-type="taskList"]` 上），
   导致 flex 布局与 18px 勾选框尺寸全部不生效，勾选框被宿主表单的
   `input { width:100% }` 规则拉满、错位到行尾。分发副本把四条规则改挂到
   `ul[data-type="taskList"] > li …`，兼容两种标记。
4. `oy-editor.js` 修复 GFM 表格序列化丢失单元格内容的问题。上游 `markdown.js`
   表格行序列化直接对 `cell.content` 调用行内序列化器，而 Tiptap 单元格的内容是
   段落块数组（`cell.content = [{type:'paragraph', content:[…行内…]}]`），行内
   序列化器遇到块节点一律输出空串，保存的 Markdown 表格因此所有单元格都是空的
   （编辑器里看得见、保存后内容消失）。分发副本改为先展开单元格内的段落块、再做
   行内序列化，多个块用 `<br>` 连接。对应上游源码
   `tools/editor-build/src/markdown.js` 的 table 分支（`fmtRow`），建议上游工程
   同步修复后重新打包、再移除本补丁。
