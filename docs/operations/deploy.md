# 部署与升级

当前按 SQLite 单实例、单 worker 部署。运行时依赖在 `requirements.txt`，开发、测试和依赖审计使用 `requirements-dev.txt`。Docker 镜像只安装运行时依赖，包含 Python 应用、`static/` 和分享卡片字体所在的 `assets/`。

## Docker 快速开始

安装 Docker Engine 和 Docker Compose 插件，在项目目录执行：

```bash
cp .env.example .env
mkdir -p data
```

编辑 `.env`：将 `INVITE_CODE` 改为自己的邀请码；按需配置 `OPENAI_*`、`SMTP_*` 和支付配置。上线时将 `PUBLIC_BASE_URL` 改成真实 HTTPS 地址并设置 `COOKIE_SECURE=1`；本地 HTTP 检查使用 `COOKIE_SECURE=0`。Compose 要求 `.env` 已存在，密钥由 `env_file` 在运行时注入，不会复制进镜像。

容器使用非 root 用户，固定 UID/GID 为 `10001:10001`。`./data` 绑定挂载到 `/app/data`，挂载后会使用宿主机目录的权限，镜像内的 `chown` 不会修改宿主机目录。Linux 上新建的目录需要允许该用户写入，例如：

```bash
sudo chown -R 10001:10001 ./data
docker compose up -d --build
curl -fsS http://127.0.0.1:8000/healthz
```

`chown` 示例适用于本项目数据目录；已有数据应先备份，并确认共享目录的其他程序仍有需要的访问权限。Windows/macOS Docker Desktop 的绑定挂载权限由宿主系统管理，不需要照搬 Linux 的 `chown`。

浏览器访问 `http://127.0.0.1:8000`。Compose 默认只把端口发布在本机（`127.0.0.1:8000:8000`）；对外服务请放在 HTTPS 反向代理之后，只在确实要让局域网其他机器直接访问时才改成 `8000:8000`。`/healthz` 无需登录，检查 SQLite 可读及可取得写事务，成功返回 `{"status":"ok","schema_version":15}`（数字为当前数据库版本，以 `db.py` 的 `SCHEMA_VERSION` 为准），失败返回 HTTP 503 和 `{"status":"error"}`。响应使用 `Cache-Control: no-store`。Docker 与 Compose 的健康检查用 Python 标准库访问该地址，镜像无需安装 curl。

也可直接使用镜像并在运行时传入环境文件：

```bash
docker build -t algorithm-notebook:local .
docker run -d --name algorithm-notebook -p 127.0.0.1:8000:8000 --env-file .env -v "$(pwd)/data:/app/data" algorithm-notebook:local
```

## 升级流程

1. 在更新程序前，用当前版本的 `backup.py` 生成备份，并按 [备份与恢复说明](backup-and-restore.md) 检查备份结果。容器部署可执行 `docker compose exec app python backup.py --output-dir data/backups --keep 14`，默认输出保存在挂载的 `./data/backups`，再用 `docker compose exec app python backup.py verify data/backups/生成的归档文件名.tar.gz` 校验。
2. 拉取新代码，先读此次迁移说明；保留 `.env` 和 `./data`。
3. 执行 `docker compose up -d --build`。启动时 `init_db()` 自动按 `PRAGMA user_version` 顺序执行未完成的迁移。
4. 执行 `curl -fsS http://127.0.0.1:8000/healthz`，确认 `status` 为 `ok` 且 `schema_version` 为新程序支持的版本；必要时查看 `docker compose logs app`。

数据库目前的最新版本以 `db.py` 的 `SCHEMA_VERSION` 为准（本次为 52，50 为榜单显示名与公开设置，51 为小黄鸭额度，52 为手填实付金额；各版本含义见
[README 的数据结构](../../README.md#数据结构)）。版本 1 是兼容所有历史 `user_version=0`
数据库的幂等基线。包括基线在内，每个迁移均在独立事务内执行，失败时该迁移的变更和版本号一起回滚；已提交的早期迁移保留。正常完整的数据库不重复执行已完成的迁移；为保留旧版本的幂等初始化行为，版本号已为 1 或更高但缺少冻结基线的表、迁移列或索引时，会在独立事务内补齐基线，并保留原版本号。

如果数据库版本比程序支持的版本新，程序会拒绝启动。升级失败时先修复原因，再启动以继续迁移；需要回退时使用对应版本的备份，不能直接用旧程序打开已经迁移的新数据库。

## 环境变量

完整说明及示例见 [`.env.example`](../../.env.example)，部署时通过环境变量或 Compose 的 `env_file` 配置。

| 配置 | 用途 |
| --- | --- |
| `INVITE_CODE` | 注册邀请码 |
| `ADMIN_USERNAME` | 保留名；**不授予管理员权限**，管理员身份用 `admin_tool.py grant/revoke` 管理 |
| `DATABASE_PATH` | SQLite 路径，默认 `data/notebook.db`；容器内必须放在 `/app/data` 持久化目录 |
| `AVATAR_DIR` | 头像目录，默认 `data/avatars`；`backup.py` 归档包含它 |
| `PAY_QR_DIR` | 手动收款二维码目录，默认 `data/pay-qr`；**不在** `backup.py` 归档内，需单独备份 |
| `BACKUP_DIR`、`BACKUP_KEEP` | `backup.py` 的输出目录与保留份数，默认 `data/backups` / 14 |
| `SAMPLE_WORLD_DIR` | `sample_world.py` 的数据目录，默认项目内 `data` |
| `COOKIE_SECURE`、`PUBLIC_BASE_URL` | HTTPS Cookie、邮件链接所用地址 |
| `OPENAI_API_KEY`、`OPENAI_MODEL`、`OPENAI_BASE_URL` | AI 服务密钥、模型、兼容服务地址；密钥为空时 AI 不可用 |
| `DUCK_DAILY_LIMIT` | 小黄鸭独立每日次数，所有账号默认 10 次；0 关闭，非法值回落到 10；每次回复及总结各扣 1 次，502/503/504 退还，429 并发不扣 |
| `AI_DAILY_LIMIT`、`TRIAL_AI_DAILY_LIMIT` | 默认值与优先级见[AI 额度与计费](../../static/ai-billing.html) |
| `AI_MAX_CONCURRENCY` | 每个进程同时进行中的 AI 调用上限，默认 6 |
| `SMTP_HOST`、`SMTP_PORT`、`SMTP_SECURITY`、`SMTP_TIMEOUT`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_FROM` | 找回密码和复习提醒发信；配置与自测见 [发信配置与自测](mail.md) |
| `PAYMENTS_MOCK_*`、`ALIPAY_*`、`WECHAT_*` | 本地支付模拟及支付渠道配置，详见示例中的逐项说明 |

AI 并发上限、扣额、失败退还与重试规则统一见[AI 额度与计费](../../static/ai-billing.html)。

## 反向代理

对外服务配置 HTTPS，并保持前端和 API 同源。账户接口的内存限流按套接字 IP 计数；如果应用看到的都是代理地址，用户会共用一个桶。使用反向代理时需要确认实际取得的客户端 IP，并考虑共享限流存储；参见 [README 的部署说明](../../README.md#部署说明)。不要只增加 worker 数量来解决线程池或限流问题。

## 新增数据库迁移

`db.py` 的 `SCHEMA` 及版本 1 的列补齐逻辑是冻结的历史基线。新表、新列和新索引通过新迁移加入，不修改已经发布的迁移。

新增一个接收 `conn` 的函数，用 `conn.execute(...)` 执行 SQL，并在 `MIGRATIONS` 末尾追加 `(下一个版本号, 名称, 函数)`。版本号严格递增且不得重复，`SCHEMA_VERSION = MIGRATIONS[-1][0]` 自动取末尾版本。迁移只执行数据库变更，不自行 `commit()`、`rollback()`，也不要使用会隐式提交事务的 `executescript()`。`init_db()` 统一执行 `BEGIN IMMEDIATE`，应用迁移后更新 `user_version` 并提交；异常则回滚并继续抛出。冻结基线使用 `sqlite3.complete_statement()` 拆分原 `SCHEMA` 并逐条执行，避免隐式提交，因此版本 1 也能完整回滚。

新增迁移应补充 `tests/test_migrations.py`：覆盖全新库、旧库数据保留、升级幂等、失败回滚及新版数据库拒绝被旧程序打开。迁移前后均应验证现有业务查询与数据库测试。

## AI 成本与并发

`ai_calls` 按调用记录用户 ID、功能、实际模型、服务商返回的 prompt/completion token 数、成功状态、简短错误类别、耗时和 UTC 时间。删除账号会将记账记录的 `user_id` 置空，保留成本记录；表内不存提示词、模型回复或用户材料。未提供有效 token 用量时记为 NULL，不能当作零成本。记账写入失败只记录日志，不改变原请求的结果。

以下 SQL 只读，按 UTC 日期及功能汇总最近 7 天的调用和已知 token 用量；可在 SQLite 客户端中对持久化数据库执行：

```sql
SELECT
    substr(created_at, 1, 10) AS utc_day,
    feature,
    COUNT(*) AS calls,
    SUM(ok) AS successful_calls,
    COUNT(prompt_tokens) AS calls_with_prompt_usage,
    COUNT(completion_tokens) AS calls_with_completion_usage,
    SUM(prompt_tokens) AS prompt_tokens,
    SUM(completion_tokens) AS completion_tokens
FROM ai_calls
WHERE created_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-7 days')
GROUP BY utc_day, feature
ORDER BY utc_day DESC, feature;
```

用量不完整时，汇总 token 只包含已知部分；需要估算费用时还应按 `model` 分组并使用服务商对应的定价。AI 失败会记录失败成本，用户额度结算见[AI 额度与计费](../../static/ai-billing.html)；并发忙碌、额度用尽、未配置密钥、输入不足等没有实际调用的分支不会产生记账。

## CI 与本地检查

`.github/workflows/ci.yml` 在 `main` 的 push、pull request 及手动触发时运行。同一分支的新运行取消旧运行，权限仅为 `contents: read`。

- `test`：Ubuntu、Python 3.13、Node 22，安装开发依赖，检查 Python/JS 语法并执行 pytest，超时 30 分钟。
- `audit`：使用 `pip-audit` 检查运行时依赖；失败只提供提示。
- `docker`：构建镜像，以测试邀请码启动容器，最多等待 30 秒检查 `/healthz`；失败打印容器日志并退出失败。

在 Linux/macOS 的项目目录，激活虚拟环境后可复现测试与审计步骤：

```bash
python -m pip install -r requirements-dev.txt
python -m compileall -q -x "(\.venv|node_modules|\.git)" .
for f in static/*.js tests/*.cjs; do
  node --check "$f"
done
python -m pytest -p no:cacheprovider -q
pip-audit -r requirements.txt
```

Windows 使用 `.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q`，并用 PowerShell 的 `Get-ChildItem static/*.js, tests/*.cjs | ForEach-Object { node --check $_.FullName }` 检查 JavaScript。pytest 不使用仓库内临时目录或 `.pytest_cache`；系统默认临时目录遇到权限错误应立即停止并报告，不改用仓库内的替代目录。

容器检查可复现为：

```bash
docker build -t algorithm-notebook:ci .
docker run -d --name nb -p 8000:8000 -e INVITE_CODE=ci-invite -e COOKIE_SECURE=0 algorithm-notebook:ci
if timeout 30s bash -c '
  until curl -fsS --connect-timeout 1 --max-time 2 http://127.0.0.1:8000/healthz; do
    sleep 1
  done
'; then
  echo 'healthz ok'
else
  docker logs nb
  exit 1
fi
docker rm -f nb
```

镜像及 GitHub Actions 需在具备 Linux/Docker 的环境实测，本地静态检查不能替代真实构建、容器启动和 CI 结果。

## 定时任务

Docker 部署的每日推荐抓取使用 [recommend-cron.md](recommend-cron.md) 中的幂等安装脚本，
默认 dry-run；显式 `--apply` 后每天服务器时间 03:20 在容器内执行刷新，失败保留旧缓存。

应用不在 Web 进程内自行调度提醒、体验账号清理或备份。由宿主机 cron/任务计划程序触发，Docker 部署可使用：

```bash
docker compose exec -T app python send_reminders.py
docker compose exec -T app python cleanup_trial_accounts.py
docker compose exec -T app python backup.py --output-dir data/backups --keep 14
```

提醒需要已配置 SMTP；可先运行 `send_reminders.py --dry-run`。清理默认删除创建超过 7 天的体验账号，可先运行 `cleanup_trial_accounts.py --dry-run`；备份参数和保留策略参见 [备份与恢复说明](backup-and-restore.md)。

例如每天凌晨 3 点清理体验账号，cron 中先进入项目目录以找到 Compose 配置：

```cron
0 3 * * * cd /srv/algorithm-notebook && docker compose exec -T app python cleanup_trial_accounts.py
```

提醒和备份使用相同方式设置合适的周期；备份输出放在持久化挂载目录中，并定期复制到另一台机器或独立存储。

## 部署说明

这是面向少量邀请用户的 V1。

部署使用持久化磁盘保存 SQLite 文件，先采用单实例服务。
对外访问配置 HTTPS，并设置 `COOKIE_SECURE=1`，启动时去掉 `--reload`。
前端和 API 保持同源；不要为当前 Cookie 认证随意开放跨域。

登录、注册中的 PBKDF2 密码计算以及 AI 生成目前都在同步 `def` 端点中
执行，会占用 Starlette / AnyIO 的线程池令牌。默认容量是 40 个令牌，
同步依赖等操作也共享这份容量；AI 等待网络返回时仍占用令牌，饱和后
新的同步任务会排队，因此高并发下登录和生成请求可能相互影响。
这一限制见 [Starlette 官方线程池说明](https://www.starlette.io/threadpool/)。
AI 并发、计次和重试规则统一见[AI 额度与计费](../../static/ai-billing.html)，调用成本汇总方法见 [部署与升级](../../docs/operations/deploy.md)。
当前仍按 SQLite 单实例、单 worker 部署。负载增长后可评估多 worker
或把密码计算、AI 生成隔离执行；届时需同时处理 SQLite 写入竞争及
下述内存限流的共享问题，不能只增加 worker 数量。

### 备份与恢复

使用 `python backup.py` 在线备份 SQLite 数据库与头像（服务不用停），默认保存在 `data/backups/`，保留最近 14 份，可用 `--output-dir`、`--keep` 调整；`python backup.py verify 归档.tar.gz` 校验。恢复时先停止服务，再用 `python backup.py restore 归档.tar.gz --into 独立目录` 还原并校验，手动替换数据库与头像后启动服务、访问 `/healthz`。归档含密码哈希和个人数据，不含 `.env` 里的密钥，请安全保存并另存异地副本。定时任务示例和季度恢复演练见 [备份与恢复](../../docs/operations/backup-and-restore.md)。

### Docker、健康检查、升级与 CI

仓库带有 `Dockerfile` 和 `docker-compose.yml`（容器内用非 root 用户，数据放在挂载的 `./data`）；`GET /healthz` 无需登录，检查数据库可读写并返回 `schema_version`，失败返回 503。数据库用 `PRAGMA user_version` 记录版本，启动时按顺序自动执行未完成的迁移，每个迁移独立提交、失败回滚；数据库版本比程序新时程序会拒绝启动。GitHub Actions（`.github/workflows/ci.yml`）在每次推送到 `main` 和每个 pull request 时跑语法检查、全部测试，并构建镜像检查 `/healthz`，另有一个只作提示的依赖安全审计。详细步骤见 [部署与升级](../../docs/operations/deploy.md)。

不要提交 `.env` 或用户数据库到 GitHub。

题目和易错点支持编辑、删除。删除题目会级联删除它名下的
全部易错点、复习记录和变体题，不可恢复；删除单条易错点
不影响同一道题的其他记录。

注册、登录、忘记密码、重置密码、体验账号创建接口都按客户端 IP 做了
基础防刷：默认 15 分钟内最多 5 次注册尝试、10 次登录尝试、5 次忘记
密码请求、10 次重置密码尝试、每小时 3 次体验账号创建，超过返回 429。
计数只存在单进程内存里，重启即清零；部署多实例或反向代理之后需要
改成共享存储，并确认拿到的是真实客户端 IP。

支付宝接口尚需商户账号开通后完成沙箱/实网联调；微信支付没有沙箱，
只能用真实商户号完成小额联调。V1 不包含多实例部署。
