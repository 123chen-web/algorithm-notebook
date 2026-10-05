# GOAL 卡片（window.GoalCard）接线说明

新增文件（保持相同相对路径放进仓库即可）：

- `static/goal-card.js` — 部件本体
- `static/goal-card.css` — 样式
- `tests/goal_card_behaviour.cjs` — Node 假浏览器行为测试（37 用例）
- `tests/test_goal_card_behaviour.py` — pytest 包装（通过数下限 37）
- `tests/test_goal_card_assets.py` — 静态契约

## 1. static/index.html 需要加的标签

样式表：放在 `rank.css`（第 37 行附近）之后：

```html
<link rel="stylesheet" href="/static/goal-card.css?v=1">
```

脚本：放在 `plan.js`（第 65 行附近）之后、`app.js` 之前（部件必须早于 app.js 加载）：

```html
<script defer src="/static/goal-card.js?v=1"></script>
```

挂载容器：放在 `#home-page` 里 `.ov-tiles` 结束标签之后、`<section id="home-trend">` 之前：

```html
<div id="goal-card"></div>
```

（`GoalCard.mount()` 会把 `<section class="gc-card">` 填进这个 div；容器本身不需要任何属性。）

## 2. static/app.js 的最少改动（3 处，可直接粘贴）

**(a) 登出清理**——在 `window.Rank?.reset();`（约 521 行，`signedOut` 的清理段）旁边加：

```js
window.GoalCard?.reset();
```

**(b) 配置**——在 `window.RankAdmin?.configure(hooks);`（约 4731 行）之后加：

```js
window.GoalCard?.configure({ api, getUser: () => user, getEpoch: () => sessionEpoch });
```

**(c) 总览渲染后挂载**——在派发 `app:home-rendered` 的 `document.dispatchEvent(...)`（约 974–976 行）之后加：

```js
window.GoalCard?.mount(document.querySelector("#goal-card"));
```

`app.js` 改动后请把 `index.html` 里 `app.js?v=72` 的版本号 +1。

## 3. 文字色 / 底色令牌配对清单（请加进 tests/test_contrast_tokens.py）

```python
GOAL_TEXT_PAIRS = (
    ("--ink", "--surface"),            # 卡片正文、目标名、倒计时、进度百分比、确认框文字
    ("--ink-2", "--surface"),          # 状态行、每日建议、表单标签、空状态引导
    ("--azurite", "--surface"),        # GOAL 等宽小标签
    ("--success-ink", "--surface"),    # “✓ 已达标”
    ("--danger", "--surface"),         # “○ 还差 N 条”
    ("--error-ink", "--error-surface"),# 加载失败 / 表单校验 / 422 文案
    ("--ink", "--paper-2"),            # 表单输入框文字（输入框底色 --paper-2）
    ("--ink", "--danger-soft"),        # 结束目标确认框文字（确认框底色 --danger-soft）
)
```

这 8 对全部已经出现在现有 ONBOARDING / THREAD / ADMIN_METRICS 矩阵里，加入后即可直接通过。
装饰用途（无文字压在上面，无需进矩阵）：`--azurite` 信号线与进度条填充、`--line` 边框、`--paper-2` 进度条轨道。
`test_goal_card_assets.py` 里的 `test_css_text_colors_are_checked_token_pairs` 已经按这份清单核对样式表。

## 4. 依赖的 api() 行为

- 成功：返回解析后的 JSON（`{"goal": ...}`）。
- 失败：抛出 `Error`，`error.message` 是扁平化后的 `detail` 字符串（422 的 `{"detail": str}` 因此能原样显示），`error.status` 是 HTTP 状态码。
- 401 由 api() 自己触发 `signedOut()`；部件不处理 401。
- 部件只通过 `configure({api})` 拿到的 `api` 发请求，不直接 `fetch`。

## 5. 风险最高的三处

1. **后端尚未实现**：`/api/goal` 上线前，卡片会停在错误态（一行提示 + 「重试」），不影响总览其他区块。想先合并前端，可以先只加文件和标签、暂不写第 2(c) 步的 mount。
2. **挂载时机**：`app:home-rendered` 只在总览成功渲染后派发；若该分支没走到（例如总览接口失败），卡片不会挂载也不发请求。接线位置务必与 dispatch 同分支。
3. **`status: "empty"` 的语义**：按「目标存在、但今天没有安排任务」实现（每日行显示「今天没有安排任务。」，倒计时照常）。如果后端语义不同，只需改 `helpers.dailyText` 与对应测试。

## 假设（不确定处的保守选择）

1. 进度条表达的是**今日进度**（`done_today / daily_target`）：接口没有给总量完成数，`aria-valuemax=daily_target`、`aria-valuenow=min(done_today, daily_target)`；`daily_target=0` 时退化为 0–100 区间。
2. `status` 为 `today / passed / empty` 时只改倒计时与每日行的文案，布局不变。
3. 没有改 `index.html` / `app.js` / `test_contrast_tokens.py`；接线按上面步骤由项目方执行。
4. 名称字数按码点计（😀 算 1 字，与 rank-admin 一致）；输入框 `maxlength=120` 只是物理上限，真正校验在 `formProblem()`。
5. 登出 / 换号时的 `reset()` 由宿主调用（与 `window.Rank` 相同模式）；部件自身还按 `getEpoch() + getUser().id + 请求序号` 三层丢弃迟到响应。

## 验证结果（副本内真实输出）

- `node --test tests/goal_card_behaviour.cjs` → `ℹ pass 37` / `ℹ fail 0`
- `node --test tests/*_behaviour.cjs` → `ℹ tests 640` / `ℹ pass 640` / `ℹ fail 0`
- `pytest tests/test_goal_card_assets.py tests/test_goal_card_behaviour.py` → `11 passed`
- 全量 `pytest -q -p no:cacheprovider`（127 个文件，分 8 批跑完）→ 合计 `3965 passed, 2 failed`；仅有的 2 个失败是 `tests/test_avatar_contrast.py`，在原项目里只读复跑同样失败（本机 node stdin 环境所致），与本次新增文件无关。
- 变异检查 3 处（删 epoch 守卫 / 删请求序号守卫 / 删保存的 busy 占位）各自让行为测试变红（36 pass / 1 fail），还原后 37 pass / 0 fail。
