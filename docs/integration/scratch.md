# 草稿演算区（window.Scratch）接入说明

新增文件：

- `static/scratch.js` — 部件本体（`window.Scratch`）
- `static/scratch.css` — 样式（只用主题令牌）
- `tests/scratch_behaviour.cjs` — Node 假浏览器行为测试（57 个用例）
- `tests/test_scratch_behaviour.py` — pytest 包装（通过数下限 57）
- `tests/test_scratch_assets.py` — 静态契约测试

## 1. static/index.html 需要加的标签

样式（放在 `rank.css` 之后即可）：

```html
<link rel="stylesheet" href="/static/scratch.css?v=1">
```

脚本（顺序有要求：`diff-lines.js` 与 `trace-table.js` 必须在 `scratch.js` 之前，
`scratch.js` 必须在 `app.js` 之前）：

```html
<script defer src="/static/diff-lines.js?v=1"></script>
<script defer src="/static/trace-table.js?v=1"></script>
<script defer src="/static/scratch.js?v=1"></script>
<script defer src="/static/app.js?v=73"></script>
```

注意：

- `diff-lines.js` 目前**没有**被 index.html 引用（此前只在 Node 测试里用），需要新加。
- `trace-table.js` 由后端 / 另一位前端同学提供，接口为 `window.TraceTable`
  （`create/addRow/removeRow/addCol/removeCol/setCell/setHeader/pasteGrid/validate/toJSON`）。
  即使它没加载，`scratch.js` 也不会报错：演算表标签会显示“演算表暂不可用”。
  所以 `trace-table.js` 的 script 标签可以后补。

## 2. static/app.js 需要的最少改动

### 2.1 配置（放在其它部件 configure 附近，例如 `Rank.configure` 一带）

```js
window.Scratch?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
```

### 2.2 挂载：在 `renderDetail(item)` 里挂到错题详情页

在 `renderDetail` 末尾（`root.append(original);` 之后、`state.answers.push(original);` 一带，
只要是在 `root` 已经装好详情内容之后）加：

```js
const scratchHost = element("div", "", "scratch-host");
root.append(scratchHost);
window.Scratch?.mount(scratchHost, item);
```

### 2.3 换题 / 清空详情时卸载：在 `rvfClearDetail()` 里加一行

```js
function rvfClearDetail() {
  window.Scratch?.unmount(); // 切换或关闭详情时立即保存并卸载草稿面板
  rvfDetailState?.menu?.destroy?.();
  rvfDetailGeneration += 1;
  rvfDetailState = null;
}
```

（`renderDetail`、`clearDetail`、`signedOut` 都会经过 `rvfClearDetail`，所以只需改这一处。）

### 2.4 登出时复位：在 `signedOut()` 里（`window.Rank?.reset();` 附近）加

```js
window.Scratch?.reset();
```

`reset()` 会清掉防抖计时器、`visibilitychange` 监听器、DOM，并把内部代次 +1，
使所有未完成请求的迟到响应作废。

## 3. 后端接口约定（前端已按此实现）

- `GET /api/mistakes/{id}/scratch` →
  `{"version": int, "code": str, "fixed": str, "table": {cols, rows} | null, "updated_at": str | null}`；
  从未保存过时 `version` 0、空字符串、`table` 为 `null`。
- `PUT /api/mistakes/{id}/scratch`，请求体
  `{"version": 当前版本, "code": str, "fixed": str, "table": {cols,rows} | null}` →
  200 `{"version": 新版本, "updated_at": str}`；版本冲突 409
  `{"detail": str, "current": 同 GET 结构}`；超限 413；校验失败 422。
- 前端另有本地大小上限：`code.length + fixed.length + JSON.stringify(table).length ≤ 40000`，
  超出时回退本次输入并提示。

## 4. 对 `api()` 行为的依赖（已读 static/app.js 的实现确认）

- `api(url, options)` 自带 `Content-Type: application/json` 与 `X-CSRF-Protection: 1` 头，
  所以 PUT 直接传 `body: JSON.stringify(...)`，不再设置任何头。
- 失败时抛出 `Error`，带 `status`（HTTP 状态码）、`code`（响应体里的 code）、
  `message`（取自响应体 `detail`，数组 / 对象会被拍平成字符串）。
- **真实实现不会把响应体挂到错误对象上**（没有 `error.body` / `error.current`）。
  因此 409 的处理是：先看 `error.current ?? error.body?.current`（若宿主以后扩展了就直接用），
  否则重新 `GET /api/mistakes/{id}/scratch` 拿服务器当前版本，再展示冲突选择。
  行为测试对两条路径都有覆盖。
- 401 时 `api()` 自己调 `signedOut()`，后者会调用 `Scratch.reset()`（见 2.4），
  部件不需要单独处理 401。

## 5. 令牌配对清单（文字色 / 底色）

`scratch.css` 用到的全部“文字色压在底色上”的配对，均已存在于
`tests/test_contrast_tokens.py` 已检查的配对集合中（两个主题都 ≥ 4.5:1）：

| 文字色 | 底色 | 用途 |
| --- | --- | --- |
| `--ink` | `--surface` | 面板正文、编辑区文字、对比行文字、表格输入 |
| `--ink-2` | `--surface` | 状态栏、说明文字、对比行号 |
| `--ink-2` | `--paper-2` | 行号列、表头输入、工具按钮文字 |
| `--ink` | `--paper-2` | 工具按钮、选中标签页 |
| `--azurite` | `--surface` | “已保存”状态文字、选中标签下划线（信号线） |
| `--danger` | `--surface` | 行数 / 大小上限提示 |
| `--danger` | `--danger-soft` | 保存失败重试、冲突提示条、删除行符号 |
| `--ink` | `--danger-soft` | 删除行文字、冲突条按钮文字 |
| `--ink` | `--success-soft` | 新增行文字 |
| `--success-ink` | `--success-soft` | 新增行符号 |
| `--ink` | `--soft` | 悬停态（仅 hover: hover + pointer: fine） |

对比行除颜色外一律带 `+` / `−` / 空格符号，色盲友好。

## 6. 风险最高的三处

1. **`window.TraceTable` 的具体模型形状是假设的**（另一位同学实现）。
   假设：`create(cols, rows)` 的入参与 `toJSON(model)` 的产出同构，都是
   `{cols: string[], rows: string[][]}`，`validate()` 吃同一个形状。
   如果实际接口是“列数 / 行数”而不是数组，`buildTablePanel` / 加载 / 冲突采纳三处要跟着改。
2. **409 响应体拿不到的问题**：`app.js` 的 `api()` 目前会把 409 的响应体
   （含 `current`）丢掉，前端只能再发一次 GET。如果两个窗口高频互写，
   会多一次往返；并且 GET 与“保留我的并覆盖”的 PUT 之间仍存在一个很小的竞态窗口
   （第三次冲突会再次进入 conflict 流程，行为正确但用户要多点一次）。
3. **长文本性能**：2000 行的对比会渲染约 2000 行 × 4 个节点，低端手机上切到
   “对比”标签可能有一下可感知的卡顿；编辑“代码草稿”时每次击键都会重算
   LCS（O(n·m) 的 Uint32Array DP），2000×2000 行级别下输入会有负担。
   目前没有做防抖或增量计算，如实际使用可感知再优化。

## 7. 假设（按最保守方案）

1. `TraceTable.create(cols, rows)` 的入参是字符串数组形状（见风险 1）。
2. 大小上限按 `code.length + fixed.length + JSON.stringify(table).length` 计算
   （不含 PUT 包装字段本身的引号等开销）。
3. 项目没有 `--mono` 字体令牌，等宽字体沿用 `style.css` 里 `.code` 的
   `"Cascadia Code", Consolas, Monaco, monospace` 字体栈。
4. 标签切换采用自动换页（方向键移动即选中），这是站点移动端最保守的做法。
5. 卸载 / 页面隐藏时的“立即保存”是 fire-and-forget：请求照常发出，
   响应按迟到守卫丢弃，不阻塞卸载。
