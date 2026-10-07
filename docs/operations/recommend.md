# 个性化推荐题（Codeforces）

总览页的「今日推荐题」卡：按用户未掌握的错题标签，从 Codeforces 官方公开接口
拉回来的题库缓存里每天挑 1 道难度合适的题，只给题名、难度、标签和原站链接，
**不保存、不展示题面**。

## 题库怎么来

`cf_problems.py`（仓库根目录）用标准库 `urllib` 请求一次官方接口
`https://codeforces.com/api/problemset.problems`，只保留字段
`contestId / index / name / rating / tags`，原子写入 `data/cf_problems.json`
（先写临时文件再 `os.replace`）。

- 超时 20 秒，响应体上限 15 MB，User-Agent `oy-recommend/1 (+https://ouyeoy.com)`，
  每次刷新距离上次至少间隔 2 秒（官方限制）。
- 同一缓存目录的刷新进程共用 `cf_problems.lock` 文件锁和请求时间；失败请求也计入
  2 秒间隔。缓存写入使用独立暂存文件并在锁内替换，失败后清除暂存文件。
- 刷新失败（网络错、HTTP 非 200、JSON 损坏、响应体超限）时**保留旧缓存**，
  退出码非 0，方便 cron 告警。
- 缓存超过 14 天只在推荐日志里提醒一行，不影响推荐（题库本来就更新得慢）。

### 服务器每天自动刷新（仅提供脚本，不会自动安装）

在服务器现有仓库中，先检查计划：

```sh
python3 deploy/bin/refresh-recommend.py --dry-run
```

它不会读密钥、修改文件、执行 Docker 或联网。确认后可手动运行同一脚本
（去掉 `--dry-run`）刷新缓存。脚本使用生产 Compose 文件与根目录已有 `.env`，
不修改 `.env`，也不发送邮件、微信消息或调用 AI。失败返回非零退出码并保留旧缓存。
同一缓存目录的并发刷新由 `cf_problems.py` 的进程文件锁协调。

由站长在服务器执行 `crontab -e` 添加以下一行；不要把它交给 Web 进程调度：

```cron
# 每天 04:00（宿主机时区）刷新；路径按服务器实际仓库目录修改
0 4 * * * cd /srv/algorithm-notebook && /usr/bin/python3 deploy/bin/refresh-recommend.py >> /srv/algorithm-notebook/data/cf-refresh.log 2>&1
```

也可以在宿主机直接跑（`python` 换成项目 venv 的 python）：

```sh
cd /path/to/app && /path/to/venv/bin/python cf_problems.py refresh
```

缓存文件不存在或损坏时，接口返回空列表 + 提示「推荐题库还没准备好」，
不会报 500——部署后记得先手动跑一次 `refresh`。

## 选题逻辑（recommend.py）

1. **标签映射**：用户错题标签是中文，`recommend.py` 顶部的 `TAG_MAP`
   是显式字典（如 `BFS/DFS/搜索 → graphs, dfs and similar`），映射不到的直接忽略。
   想加新映射只改这个字典，不用动别处。
2. **弱点分**：每个 Codeforces 标签统计该用户「未掌握」的错题条数
   （`未掌握`口径复用 `stats`/`mastery` 的 `retention_on < 0.7`，不要自创），
   取分最高的前 2 个 Codeforces 标签。
3. **难度带**：默认 rating 800–1200；用户有 ≥10 次复习记录**且**近 30 天复习的
   平均掌握度 ≥0.8 时，上限提到 1400。没有 rating 的题不选。
   近 30 天含用户本地的今天；只对这段时间复习过的易错点计算当前保持率均值，
   历史复习或从未复习的错题不混进近期均值。
4. **去重**：`problem_recommendations` 表里该用户推荐过的题以后不再推荐；
   当天重复请求返回同一道（按用户本地日期判断）。此前已保存的当天多题记录保留，
   不删旧推荐；新的一天按 1 道生成。
5. 优先从弱点分最高的标签池选题；该池为空时顺延到下一个标签。

每道题带一条推荐理由，只含用户自己的标签名和数字，
如「你在 BFS 上有 3 条未掌握的错题」。

## 接口

- `GET /api/recommend`（登录用户，体验账号也可看；每用户每分钟 30 次）→
  `{ items: [{ id, contest_id, idx, name, rating, tags, url, reason, state }], hint }`。
  `url` 固定拼成 `https://codeforces.com/problemset/problem/{contestId}/{index}`。
  第一次返回的题写入表（`state=new`）。
- `POST /api/recommend/{contest_id}/{idx}`，body `{ state: "done" | "dismissed" }`，
  只能改自己的记录，不存在返回 404。写操作走全局 CSRF 中间件
  （`X-CSRF-Protection: 1`）。

## 数据来源与版权

- 数据来源：Codeforces 官方公开 API `problemset.problems`。
- 只缓存题目元信息（题号、题名、难度分、标签），**不保存题面、不保存题解**；
  前端「去做题」链接跳转 Codeforces 原站（`target=_blank rel="noopener noreferrer"`），
  卡片下方注明「题目来自 Codeforces，点击跳转原站；欧叶OY 不保存题面。」
- 不要抓取洛谷或任何没有官方接口的网站。
