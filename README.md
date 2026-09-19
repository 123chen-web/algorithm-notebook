# 算法错题本 V1

FastAPI + SQLite + 原生网页。

支持邀请码注册、独立用户数据、题目记录、按易错点复习、AI 变体题、
手动练习结果。不会执行用户代码。

## 环境

Python 3.11 或更高版本。

## Windows 启动

在项目目录中执行：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`：

- 将 `INVITE_CODE` 从 `change-me` 改为自己的内测邀请码。
- 填写 `OPENAI_API_KEY`，启用 AI 生成功能。
- 默认模型是 `gpt-4.1-mini`，可通过 `OPENAI_MODEL` 修改。

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

1. 使用 `.env` 中的邀请码注册账号。
2. 新增一道题，填写代码、思路和两条易错点。
3. 两条易错点分别出现在“今日复习”中。
4. 给第一条易错点评分，它从今日列表移除，第二条仍保留。
5. 在“全部记录”中查看第一条，确认显示下次日期及复习历史。
6. 点击“生成一道变体题”。
7. 自行解题，保存结果、代码和复盘。
8. 刷新页面，确认变体及练习结果仍然存在。
9. 注册第二个账号，确认看不到第一个账号的记录。

AI 使用真实 OpenAI API，会产生 API 使用费用。
不配置 API Key 时，其余功能仍然可以使用。

## 自动化测试

Windows：

```powershell
.venv\Scripts\python.exe -m pytest -q
```

macOS / Linux：

```bash
.venv/bin/python -m pytest -q
```

测试使用临时数据库，AI 调用被模拟，不会产生 API 费用。

## 调度规则

- 每条易错点独立保存调度状态。
- 新增易错点当天到期，方便立即开始复习。
- 今日列表包含 `due_date <= 用户当地日期` 的全部记录。
- 前端四档评分对应 0、3、4、5。
- 评分小于 3：连续成功次数归零，间隔一天。
- 成功复习：前两次间隔一天、六天，以后使用旧间隔乘旧易度，向上取整。
- 每次评分后按 SM-2 公式更新易度，最低为 1.3。
- 下一次日期从实际评分当天计算。
- 简化版不进行当天重复练习，每条易错点每天最多评分一次。
- 尚未到期的记录可以查看、生成变体，但不能提前评分。
- 变体练习结果不会自动修改易错点的复习日期。
- 每道变体保存一份可更新的练习结果，V1 不保存多次作答历史。

## 数据结构

- users：账号、密码哈希、时区。
- sessions：会话令牌哈希、到期时间。
- problems：题目名、代码、思路。
- mistakes：易错点、SM-2 状态、下次日期、版本。
- reviews：每次复习评分及下次日期。
- variants：AI 题目及手动练习结果。
- ai_usage：每个用户每日 AI 请求次数。

时间戳使用 UTC；复习日期使用用户注册时选择的时区。

## AI 行为

密钥仅存在服务端环境变量中。

生成时会发送当前题目名、语言、代码、思路和选中的易错点。
题目保存到 SQLite；输出只作为文本显示。

每个用户默认每天最多尝试生成 10 次。
调用失败也消耗次数，服务端不自动重试。

V1 没有后台生成队列。如果生成期间服务进程退出，可能需要重新生成，
之前发出的 API 请求仍可能产生费用。

## 部署说明

这是面向少量邀请用户的 V1。

部署使用持久化磁盘保存 SQLite 文件，先采用单实例服务。
对外访问配置 HTTPS，并设置 `COOKIE_SECURE=1`，启动时去掉 `--reload`。
前端和 API 保持同源；不要为当前 Cookie 认证随意开放跨域。

备份时停止服务后复制整个 `data` 目录，
或使用 SQLite 自带的在线备份接口。

不要提交 `.env` 或用户数据库到 GitHub。

V1 暂不包含记录编辑删除、密码找回、邮件推送、支付、
公开注册防滥用、多实例部署和数据库版本迁移。
