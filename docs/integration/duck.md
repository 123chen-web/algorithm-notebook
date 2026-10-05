# DUCK 面板（讲给小黄鸭听）接入说明

交付物（相对路径与项目一致）：

- `static/duck-panel.js` — `window.DuckPanel`，公开接口 `configure({api, getUser, getEpoch})` / `mount(container, mistake)` / `unmount()` / `reset()`
- `static/duck-panel.css` — 面板样式，只用主题令牌
- `tests/duck_panel_behaviour.cjs` — Node 假浏览器行为测试，45 个用例
- `tests/test_duck_panel_behaviour.py` — pytest 包装（通过数下限 45）
- `tests/test_duck_panel_assets.py` — 静态契约（10 个用例）

## 1. static/index.html 需要的标签

样式表加在第 37 行 `rank.css` 之后（顺序无关，保持分组即可）：

```html
<link rel="stylesheet" href="/static/duck-panel.css?v=1">
```

脚本加在 `app.js` 之前（第 68 行之前），因为 `app.js` 里要调用 `window.DuckPanel?.…`：

```html
<script defer src="/static/duck-panel.js?v=1"></script>
```

同时把 `app.js` 的版本号 +1（副本中是 `?v=72` → `?v=73`；原项目在我复制后已改到 73，合并时按当时实际版本再 +1）。

## 2. static/app.js 的最少改动（可直接粘贴）

三处，共四行：

```js
// (a) 登录后配置（放在 configureRank() 旁边，或任何一个登录后执行的 configure 里）：
window.DuckPanel?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });

// (b) renderDetail(item) 里，root.append(...) 之后任意位置（建议放在“删除”按钮之前）：
const duckHost = element("div", "", "duck-panel-host");
root.append(duckHost);
window.DuckPanel?.mount(duckHost, item);

// (c) 登出清理（signedOut 附近，与 window.Rank?.reset() 同一处）：
window.DuckPanel?.reset();
```

不用再手动 unmount：`mount()` 自己会先卸掉旧面板，`reset()` 内含 `unmount()`；
迟到响应由 面板代次 + epoch + 账号 id + 错题 id + 请求序号 五重守卫丢弃。

## 3. 文字色 / 底色令牌配对清单

以下配对全部已经在 `tests/test_contrast_tokens.py` 现有矩阵里逐主题检查过（ink/ink-2/muted × paper/paper-2/surface 的基础矩阵，外加 RANK 的 `--soft` 对与 ONBOARDING 的 `--error-*` / `--success-*` 对），**不需要**新增配对；列出仅供你核对（我没有改动 test_contrast_tokens.py）：

| 用处 | 文字色 | 底色 |
| --- | --- | --- |
| 面板正文 / 气泡文字 | `--ink` | `--surface` |
| 轮次、说明、状态、快捷键提示 | `--ink-2` | `--surface` |
| 剩余字数 | `--muted` | `--surface` |
| 用户气泡 | `--ink` | `--soft` |
| 用户气泡的“我”标注 | `--ink-2` | `--soft` |
| 小黄鸭气泡 | `--ink` | `--paper-2` |
| 小黄鸭气泡的“小黄鸭”标注 | `--ink-2` | `--paper-2` |
| 错误条 | `--error-ink` | `--error-surface` |
| 总结区块 | `--success-ink` | `--success-soft` |

按钮沿用全局 `button` / `button.primary` 样式，本组件不另写颜色。

## 4. 依赖的 api() 行为

- `api(path, { method: "POST", body: JSON.stringify(...) })`，返回解析后的 JSON；
- 失败时抛出 `Error`，带 `.status`（429/503/422/502 分支全靠它）和 `.message`（来自响应 `detail`，数组/对象已被 app.js 拍平成字符串）；
- 401 由 app.js 自己处理登出，组件不处理；
- 组件自己不碰 fetch、不写 localStorage（对话只存内存，刷新即丢，界面上已写明）。

## 5. 后端契约（对接用）

`POST /api/mistakes/{id}/duck`，请求体 `{"turns": [{"role": "user"|"duck", "text": str}], "finish": bool}`；
`turns` 是完整对话（含当前这句用户发言，user/duck 严格交替、以 user 开头，≤ 6 轮用户发言——与 `duck_prompt.check_turns` 一致）。
200 → `{"reply": str, "turns_used": int, "ai_remaining": int}`；429 今日额度用完、503 AI 未配置、422 参数错、502 这次没答好（前端给重试）。
目前 `main.py` 里还没有这个路由（只有 `duck_prompt.py` 纯函数），需要后端同事补上。

## 6. 风险最高的三处

1. **挂载时机**：`renderDetail` 每次重画都会重建 `#detail` 的内容。如果接入时不走 `mount()` 而是只放一次容器，旧面板会被 `replaceChildren()` 摘掉但状态还在——务必每次 `renderDetail` 都新建宿主 div 并调 `mount()`（重复挂载是安全的，会先卸载旧的）。
2. **429 锁定是本次挂载级的**：额度用完后面板锁定到下次挂载（额度说明可见，重新开始不会解锁）。如果后端想要“换个错题再试试”的语义，需要明确额度到底是按账号还是按错题——我按服务端按天按账号理解（保守）。
3. **总结请求的 turns 末尾是 duck**：`finish=true` 时不追加用户发言，后端 `build_messages` 会自己补总结指令；若后端改成“finish 也要带一条用户发言”，前端 `payloadTurns(turns, "")` 的行为要跟着改（有行为测试钉住现状）。

## 假设（不确定处均按最保守方案）

1. 后端路由 `POST /api/mistakes/{id}/duck` 尚未存在，按任务书契约对接，`duck_prompt.py` 的校验规则视为权威（turns 以 user 开头交替、可含当前发言、≤6 轮）。
2. “第 N / 6 轮”的 N 指正在进行的这一轮（未发言时是 1，讲满后定格 6 并提示去总结）。
3. 发送失败时用户文字留在输入框里（不清空），“重试”按当前框内文字重发；429 视为当日额度耗尽，锁定到下次挂载。
4. “结束并总结”至少讲过 1 轮才可用；总结后对话保留只读，只能“重新开始”。
5. 组件不持久化任何东西（无 localStorage），`reset()` 只需作废在飞请求并摘掉面板（组件没有计时器和缓存）。
