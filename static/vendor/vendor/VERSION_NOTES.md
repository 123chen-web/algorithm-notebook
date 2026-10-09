# 笔记富文本第三方前端库（vendor）版本与来源说明

本目录为「N2：笔记公式 / 流程图 / 图片」随附的**本地**第三方前端库，全部从
随任务提供的离线素材复制，**未联网下载**。仅在笔记正文出现对应语法时懒加载，
普通笔记不产生任何额外请求。

## 来源

- 素材包：`muse-draw.zip`（解包于 `muse-draw/`），其中
  `muse-draw/vendor/vditor/VERSION.txt` 记载：Vditor 4.0.0（npm 包
  vditor@4.0.0，GitHub release tag v4.0.0，发布于 2026-08-30），
  vendor 化日期 2026-10-08。
- 下列两个库是 Vditor dist 内自带的运行时文件，本项目**不引入 Vditor 编辑器**
  （笔记编辑框继续使用原生 textarea），只取这两个独立浏览器库：

| 库 | 版本（从压缩包内提取） | 本项目路径 | 文件 |
|---|---|---|---|
| KaTeX | 0.16.9（katex.min.js 内 `version:"0.16.9"`） | static/vendor/katex/ | katex.min.js（270.5 KB）、katex.min.css（22.7 KB）、fonts/ 下 20 个 woff2（仅 woff2，不含 ttf/woff） |
| Mermaid | 11.16.1（mermaid.min.js 内 `version:"11.16.1"`；包内另有 3.4.0 为其打包依赖版本，非 Mermaid 版本） | static/vendor/mermaid/ | mermaid.min.js（3482.5 KB） |

## 许可证

- KaTeX：MIT License，Copyright (c) 2015 Khan Academy，见 katex/LICENSE.txt。
- Mermaid：MIT License，Copyright (c) 2014 Knut Sveidqvist，见 mermaid/LICENSE.txt。
- Vditor 本身亦为 MIT（素材包 vendor/vditor/LICENSE），本项目不复制、不加载
  Vditor 代码，仅取用上述两个同为 MIT 的独立库文件。
- 注意：Vditor dist 的 katex/mermaid 目录未随附独立 LICENSE 文件，上述
  LICENSE.txt 按 MIT 标准文本补录，版权人依各上游官方 LICENSE 填写；
  上线前应对照上游仓库 LICENSE 复核（链接见各 LICENSE.txt 末尾说明）。

## 部署与安全约束

- 全部文件由同源 `/static/vendor/...` 提供，适配现有严格 CSP
  （script-src 'self'；style-src 'self'；font 走 default-src 'self'），
  **不得**为此放宽任何 CSP 指令。
- 懒加载：仅当笔记正文含 `$...$`/`$$...$$` 时才动态插入 katex 的 css/js；
  含 ```` ```mermaid ```` 围栏时才加载 mermaid；两者皆无时不加载。
- KaTeX 渲染参数固定 `throwOnError: false`、`trust: false`、`output` 用
  默认 html；渲染产物经 DOMParser + document.importNode/replaceChildren 落位，
  禁止 innerHTML。
- Mermaid 初始化固定 `securityLevel: "strict"`、startOnLoad false、禁止任何
  点击/交互回调；若在当前 strict CSP（无 style-src 'unsafe-inline'、无
  unsafe-eval）下实测无法渲染，则**降级**为显示原始 mermaid 代码块并提示
  "流程图无法渲染"，在 NOTES_RICH_REPORT.md 中如实说明，绝不放宽 CSP。
- 字体仅保留 woff2（KaTeX css 中 woff2 排在 src 首位，现代浏览器均命中），
  ttf/woff/mhchem 等其余文件不复制。
- 本目录体积约 3.9 MB（其中 mermaid.min.js 约 3.4 MB），不计入 N2 的
  format-patch 补丁集，单独以 vendor zip 交付。
