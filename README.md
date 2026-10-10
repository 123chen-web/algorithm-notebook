# 欧叶OY

> 把每一次出错，变成下一次的把握。

一个给自学算法、准备面试的人用的**错题本**：把做错的题连同当时的思路和错因
记下来，系统按遗忘规律安排复习，AI 帮你讲清楚、找出总在哪里栽跟头。

**线上地址：<https://ouyeoy.com>**（邀请码注册，也可以点“免注册体验”先逛一圈）。
原名“算法错题本”。

## 能做什么

- **记录**：三种方式——速记（只写题名，错因以后补）、一句话（写下“我以为…其实…”）、
  完整记录；每道题可拆成多条易错点，分别复习。也可以用[浏览器扩展](docs/features/plugin-api.md)
  在洛谷、力扣、牛客、Codeforces、AtCoder 的题目页一键收录。
- **复习**：同一道题只显示一张卡，卡片背景按复习完成度从左向右涂色；先自己回忆、再看错因、
  再打分；每档评分都会预览下次间隔，支持撤销、推迟、暂停和每日上限；专注模式一次只看一张卡。
- **笔记**：所见即所得编辑器——输入 `/` 选标题、列表、待办、代码、公式、表格，选中文字加粗或加链接，
  粘贴图片；用 `[[ ]]` 链接另一篇笔记或一道错题，并能看到笔记关系图；自带画板（Excalidraw，自托管）。
  另有[算法套路库](static/patterns.html)：41 个常见套路的触发信号、模板代码、常见坑和例题。
- **AI 辅助**：橡皮鸭讲题（你讲、它追问，有独立的每日次数）、变体题、薄弱点分析、拍照识别手写题、
  AI 讨论要点；额度规则见[AI 额度与计费](static/ai-billing.html)。
- **看清自己**：总览与趋势、掌握度、“我的三大典型失误”和考前一页纸、目标计划卡、
  草稿演算区、每日推荐题、榜单与昨日之星。
- **一起学**：学习小组（每周小目标、组内今日动态）、讨论区（采纳、有用、AI 要点）、
  榜单（可填榜单显示名，也可匿名）。
- **随身带**：手机“添加到主屏幕”像 App 一样用，断网也能评分，联网后自动补交。
- **提醒与导出**：每日邮件或微信提醒；整本 JSON 导出、导出到 Anki。
- **收款**：手动收款（个人收款码 + 站长确认）和兑换码；支付宝、微信支付的代码已接好。

新朋友可以先看[新手指南](static/help.html)。

## 技术与部署

FastAPI + SQLite + 原生 JavaScript（没有构建步骤）。AI 走“OpenAI 兼容”接口，
线上使用 DeepSeek。Web 进程不执行用户代码；可选的独立运行服务支持 Python、C++，
未配置时保持关闭，详见 [代码运行服务](docs/operations/code-runner.md)。用 Docker + Caddy 部署，自动 HTTPS，
每天自动备份，带宕机告警和公开状态页。

- 部署与升级：[docs/operations/deploy.md](docs/operations/deploy.md)
- 零基础上线手册（买服务器到上线）：[deploy/README-零基础上线手册.md](deploy/README-零基础上线手册.md)
- 备份与恢复：[docs/operations/backup-and-restore.md](docs/operations/backup-and-restore.md)
- 宕机告警与状态页：[docs/operations/watchdog.md](docs/operations/watchdog.md)
- 手动收款：[docs/operations/manual-payment.md](docs/operations/manual-payment.md)
- 账号与隐私：[docs/operations/accounts-and-privacy.md](docs/operations/accounts-and-privacy.md)

下面的内容是开发与运行说明。

## 环境

Python 3.11 或更高版本。

## Windows 启动

在项目目录中执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

只想运行网站用 `requirements.txt`；要跑自动化测试、依赖审计，改用 `requirements-dev.txt`（它包含 `requirements.txt` 并额外装 pytest、pip-audit）。

编辑 `.env`：

- 将 `INVITE_CODE` 从 `change-me` 改为自己的内测邀请码。
- 填写 `OPENAI_API_KEY`，启用 AI 生成功能。变量名沿用“OpenAI 兼容接口”的叫法，实际填的是所用服务商的 Key；我们线上用的是 **DeepSeek**（国内可直接访问，不需要翻墙），此时同时设置 `OPENAI_BASE_URL=https://api.deepseek.com`，并把 `OPENAI_MODEL` 改成 DeepSeek 当前的模型名。
- 这两项留空时才会请求 OpenAI 官方（默认模型 `gpt-4.1-mini`）。
- 填写 `SMTP_*`，启用找回密码和每日邮件提醒，见[密码找回与邮件提醒](docs/features/reminders.md)。

然后启动：

```powershell
.venv\Scripts\python.exe -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

## macOS / Linux 启动

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
```

编辑 `.env` 后执行：

```bash
.venv/bin/python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

浏览器打开 http://127.0.0.1:8000 。

SQLite 文件和表结构会在第一次启动时自动创建。

## 验证完整闭环

1. 使用 `.env` 中的邀请码注册账号（需要填写用户名、密码和邮箱）。
2. 新增一道题，填写代码、思路和两条易错点。
3. 两条易错点分别出现在“今日复习”中。
4. 给第一条易错点评分，它从今日列表移除，第二条仍保留。
5. 在“全部记录”中查看第一条，确认显示下次日期及复习历史。
6. 点击“诊断错因并出一道新题”。
7. 自行解题，保存结果、代码和复盘。
8. 刷新页面，确认变体及练习结果仍然存在。
9. 注册第二个账号，确认看不到第一个账号的记录。

AI 调用第三方模型服务（我们线上使用 DeepSeek），会产生 API 使用费用。
不配置 API Key 时，其余功能仍然可以使用。

## 详细文档

- 学习：[调度与五档评分](docs/features/scheduling.md)、[撤销与暂停](docs/features/review-feel.md)、[私人笔记](docs/features/notes.md)、[成长功能](docs/features/growth.md)。
- AI：[功能与额度](docs/features/ai.md)、[AI 额度与计费](static/ai-billing.html)。
- 社区：[讨论区](docs/features/forum.md)、[学习小组](docs/features/groups.md)、[榜单](docs/features/leaderboard.md)。
- 数据：[模型与完整迁移表](docs/dev/data-model.md)、[数据导出](docs/features/export.md)、[Markdown/JSON 导入](docs/features/import-notes.md)。
- 开发：[测试与样本环境](docs/dev/testing.md)、[界面与原创场景](docs/features/ui-style.md)、[移动与离线](docs/features/mobile.md)。
- 账号：[账号安全](docs/features/account-security.md)、[个人资料与榜单显示名](docs/features/profile.md)、[头像](docs/features/avatar.md)、[体验账号](docs/operations/trial-accounts.md)、[提醒](docs/features/reminders.md)。
- 操作：[支付](docs/operations/payments.md)、[扩展 API](docs/features/plugin-api.md)、[推荐定时任务](docs/operations/recommend.md)、[离线支付检查](docs/operations/payment-config-check.md)。
- 其他：[徽章](docs/features/achievements.md)、[目标卡](docs/features/goal-card.md)、[小黄鸭](docs/features/duck.md)、[草稿](docs/features/scratch.md)、[专注与考前复盘](docs/features/focus-mode.md)。

迁移当前到 **72**；1–22 的已发布迁移保持原样。编号有意留有空缺（23/24/28 加固与提醒、40–41 小组、50–52 反馈批、70–72 笔记），不要为了填空重排。新增功能见上述文档，真实 AI、SMTP、支付、独立运行服务与服务器任务仍需站长另行配置和验收。
