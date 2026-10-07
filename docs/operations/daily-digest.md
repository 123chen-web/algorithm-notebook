# 每日简报（digest.py）运维说明

`digest.py` 是独立脚本（不进 web 进程），每天给**已开启微信提醒的管理员**
（`users.is_admin=1`、`deleted_at IS NULL`、且 `user_push.enabled=1`）推送一条微信简报，
内容包括：

- 该用户当地日期的今日待复习条数、其中已逾期条数；
- arXiv 最新论文精选：`cs.DS`、`cs.AI` 各 3 篇（官方 Atom API）；
- Hacker News 热榜前 5（官方 Firebase API），无外链的条目用 HN 讨论页链接；
- 每条资讯一句 40 字以内的中文 AI 摘要（摘要服务不可用时自动降级为只显示原标题）。

数据只来自 arXiv 与 Hacker News 的官方接口，不抓取任何网页 HTML。

## 手动运行

在项目根目录（容器内为 `/app` 一类的工作目录）：

```bash
# 只打印会发给谁、各有几条待复习；不发送、不写库、不联网、不调用 AI
python digest.py --dry-run

# 正式发送
python digest.py

# 只给某个管理员发送（仍需满足管理员且开启微信提醒的条件）
python digest.py --username alice
```

脚本复用 `.env` 里的既有配置：

- 微信推送：用户在账号设置里配置的 Server酱 / PushPlus 渠道与密钥（`user_push` 表）；
- AI 摘要：`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`，
  读取方式与 `ai.py` 一致。未配置 `OPENAI_API_KEY` 时跳过 AI，简报照发（只有原标题）。

## 配置 cron（生产环境）

每天服务器时间 08:05 运行一次（示例与 `deploy/docker-compose.prod.yml` 对应）：

```cron
5 8 * * * cd /srv/algorithm-notebook && docker compose -f deploy/docker-compose.prod.yml exec -T app python digest.py >> /var/log/notebook-digest.log 2>&1
```

非容器部署直接用虚拟环境的 Python，例如：

```cron
5 8 * * * cd /srv/algorithm-notebook && /srv/algorithm-notebook/.venv/bin/python digest.py >> /var/log/notebook-digest.log 2>&1
```

## 去重与缓存

- **同一天不重复发送**：发送成功后在 `app_settings` 表写入
  `digest_last_sent:<user_id> = <用户当地日期 YYYY-MM-DD>`；
  发送失败不记录，下次运行会自动补发。不需要新增数据库迁移。
- **每日缓存**：抓到的条目和中文摘要按 UTC 日期存到
  `data/digest/YYYY-MM-DD.json`（`data` 目录跟随 `DATABASE_PATH` 解析）。
  同一天重复运行直接读缓存，不再联网、不再调用 AI。
- 微信推送连续失败 5 次会自动关闭该用户的微信开关（复用提醒脚本的失败计数逻辑，
  与 `docs/operations/wechat-push.md` 一致），用户重新填写密钥后手动开启即可恢复。

## 成本估算

- 每天每个运行实例只产生 **1 次** AI 调用（全部条目合并在一次请求里，
  论文 6 篇 + 新闻 5 条，摘要各截断到 600 字，输出很短）。
- 按主流 OpenAI 兼容模型（如 `gpt-4.1-mini` / DeepSeek 同类价位）估算，
  每天约合人民币 **几分钱**，一个月通常不超过 1 元；网络请求本身免费。
- AI 调用会以 `feature='digest'`、`user_id=NULL` 记入 `ai_calls` 表，
  可在管理后台或数据库里核对实际 token 消耗。

## 故障表现

- arXiv 某个分类、Hacker News 或 AI 摘要任一环节失败，只跳过该环节，
  其余内容和待复习情况照常推送；脚本不会因为单个来源失败而中断。
- 网络请求统一走官方 HTTPS 接口，拒绝任何重定向；超时 10 秒，超过 1 MB 的
  响应会被拒绝（最多多读 1 字节判定超限），文本按严格 UTF-8 解码。
  XML 解析前拒绝 NUL 字符、`<!DOCTYPE` 与 `<!ENTITY`，防止编码绕过。User-Agent 为
  `oy-digest/1 (+https://ouyeoy.com)`；两次 arXiv 请求之间至少间隔 3 秒。
- 日志只打印来源名与失败类别（如 `URLError`、`ParseError`），不会打印密钥。

## 怎么关闭

- **临时停发**：注释或删除上面的 cron 行即可（`crontab -e`）。
- **只给部分管理员发**：关闭对应用户账号设置里的「微信提醒」开关，
  或取消其管理员身份；也可以把 cron 命令固定为
  `python digest.py --username <管理员用户名>`。
- **停掉 AI 摘要但保留简报**：在 `.env` 里移除（或置空）`OPENAI_API_KEY` 后重启容器，
  简报仍会发送，只是资讯没有中文摘要。
- 缓存文件可以随时删除；删掉后当天的下一次运行会重新抓取并重新生成摘要。
