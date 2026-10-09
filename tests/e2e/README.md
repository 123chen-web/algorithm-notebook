# E2E 冒烟测试（真浏览器）

用 Playwright（Python）驱动真实 Chromium，走完整 UI 流程的冒烟测试。
只新增文件，不改业务代码；默认 `pytest` 不会收集这些测试。

## 安装

```bash
pip install playwright pytest
playwright install chromium
```

## 启动被测服务（隔离数据库）

```bash
# Linux / macOS
DATABASE_PATH=/tmp/e2e-test/notebook.db INVITE_CODE=<你的邀请码> TRUST_PROXY=1 \
  python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

注意：
- `DATABASE_PATH` 指向临时目录，测试用的库与开发库完全隔离。
- 邮件（`SMTP_*`）、AI（`OPENAI_API_KEY`）、支付不要配置，保持关闭。
- `INVITE_CODE` 必须设置（不能是 `change-me`），测试用 `E2E_INVITE_CODE`
  环境变量告诉 pytest 同一个值（默认 `e2e-invite-2026`）。
- `TRUST_PROXY=1`：注册接口按 IP 限流（5 次/15 分钟），而每个测试都用独立
  账号。conftest 给每个测试分配不同的 `X-Forwarded-For`，模拟来自不同 IP
  的真实用户；服务端需信任该头才生效。**仅用于本机隔离测试**，生产环境
  端口不对公网开放时才可如此配置（见 main.py `client_ip` 注释）。

## 运行

```bash
E2E_INVITE_CODE=<你的邀请码> pytest tests/e2e --base-url http://127.0.0.1:8000
```

- 默认 `pytest`（不带 `tests/e2e`）不会运行这些测试：测试文件命名为
  `check_*.py`，`conftest.py` 只在显式指定 `tests/e2e` 时才收集它们。
- 失败时自动截图到 `tests/e2e/artifacts/`（已加入 `.gitignore`）。
- 每个测试自动检查：页面控制台无 error 日志、无外部域名请求。

## 覆盖的流程

| 文件 | 流程 |
|---|---|
| `check_auth.py` | 邀请码注册 → 登录 → 退出 → 再登录 |
| `check_mistakes.py` | 完整方式记一道错题 + 速记再记一道，列表确认 |
| `check_review.py` | 今日复习评分一道，计数变化 |
| `check_mobile.py` | 375×812 下关键步骤，无横向滚动、按钮可点 |
| `check_groups.py` | 建小组 → 邀请码加入 → 非成员 404 |
| `check_forum.py` | 发帖 → 评论 → 回复 |
| `check_account.py` | 数据导出下载 → 注销 → 注销后无法登录 |

## 选择器策略与未来维护

- 优先用稳定 id（`#auth-register-tab`、`#register-form`、`#problem-save` 等），
  其次用 `data-view` / `data-quality` 等语义属性，最后才是文本匹配。
- 基线换成合并后的最终版时，如下选择器最可能需要改：
  - 导航：`button.nav-item[data-view="..."]`（视图增减/改名）
  - 评分按钮：`.review-grade[data-quality="..."]`（评分档位变化）
  - 小组：`#groups-create-form`、`#groups-invite-code`（小组功能在 D 任务后大改）
  - 讨论区：`#forum-new-post-btn`、`#forum-comment-send`
  - 注销：`#account-delete`、确认按钮文案"永久注销账号"
- 已知容忍：未登录时 SPA 会请求 `/api/me` 返回 401，Chromium 会记一条
  console error，已列入 `BENIGN_CONSOLE_PATTERNS`（见 conftest.py），
  其他 error 一律判失败。
- 另一已知容忍：`static/review-extras.js` 的 `available()` 用 GET 探测
  `/api/mistakes/{id}/snooze|suspend|review/undo` 等写路由（靠 405/404
  判断功能是否发布），会产生 405 的 console error。这是**已发现的问题**
  （见交付报告"发现的问题清单"），测试里暂时放行，修复后应删掉该放行。
