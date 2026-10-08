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

### 服务器每天自动刷新（提供配置与脚本，由站长手动启用）

在服务器现有仓库中，先检查计划：

```sh
python3 deploy/bin/refresh-recommend.py --dry-run
```

它不会读密钥、修改文件、执行 Docker 或联网。确认后可手动运行同一脚本
（去掉 `--dry-run`）刷新缓存，再将一道官方练习链接写入榜单的「今日一条」。
脚本使用生产 Compose 文件与根目录已有 `.env`，
不修改 `.env`，也不发送邮件、微信消息或调用 AI。失败返回非零退出码并保留旧缓存。
同一缓存目录的并发刷新由 `cf_problems.py` 的进程文件锁协调。

「今日一条」使用已验证的题库元信息，选取难度 800–1200 的一道题，
只存题名与官方链接，不抓题面、不用 AI。同一北京时间自然日只写一次，
已有管理员内容（包括主动停用的内容）时保留原记录。缓存缺失或没有合格题目时
不写记录，任务返回非零，下次可以重试。手动覆盖仍通过管理后台完成。
独立虚拟环境部署可在刷新后运行 `python daily_notice.py`；
`python daily_notice.py --dry-run` 只打印计划，不读取缓存或数据库。

仓库另外提供两个 systemd 配置和一个标准库管理工具：

- `deploy/systemd/algorithm-notebook-recommend.service.in`：单次任务，调用现有刷新脚本，
  宿主机服务超时设为 5 分钟；输出进 journal，不在仓库中新建日志或临时目录。
- `deploy/systemd/algorithm-notebook-recommend.timer`：每天 **04:00 Asia/Taipei** 触发，
  不依赖宿主机当前时区，调度精度设为 1 秒。主机关机或停用期间错过触发时，
  重新启用后补一次，不逐日回放。服务仍在运行时不会另起同名实例。
- `deploy/bin/recommend-schedule.py`：只管理以上两个固定任务名；
  `install / verify / enable / disable / run` **全部默认预览**，
  只有显式添加 `--apply` 才会执行 Linux 上的相应操作。Windows 也可预览。

当前交付仅包含代码、模板和离线测试；尚未在任何服务器安装、启用或抓取。
未来要启用时，由站长在现有的 **Linux systemd + Docker Compose** 主机上按以下顺序执行。
仓库路径如果不是 `/srv/algorithm-notebook`，给每一步加同一个 `--repo /实际路径`。
路径必须是规范的 Linux 绝对路径，不支持空格、符号链接或 systemd 特殊字符。

1. 先在本机或服务器预览。以下命令不会写文件、读取 `.env`、调用 systemd/Docker 或联网：

   ```sh
   python3 deploy/bin/recommend-schedule.py install
   python3 deploy/bin/recommend-schedule.py enable
   ```

2. 站长先检查是否曾配置旧的推荐刷新 cron 或其他计时任务。
   如有，手工移除同一推荐任务的旧调度，避免两个调度器重复抓取。
   管理工具不会读取或修改任何人的 crontab。

3. 确认现有网站、Docker、生产 Compose 文件及 `.env` 已配置好。
   实际操作要求 root；仓库目录及其祖先、任务入口和 Compose 文件必须由 root 所有，
   不可允许组或其他用户写入，不接受符号链接。工具不会改变这些权限，也不会读取或修改 `.env`。
   两个目标 unit 的同名外来文件、运行时覆盖文件或额外配置目录
   （包括名称前缀和通用 service/timer drop-in）会让操作直接失败，留给站长手工审阅。

4. 安装配置，再核查；这两步不会主动启用或开始抓取：

   ```sh
   sudo python3 deploy/bin/recommend-schedule.py install --apply
   sudo python3 deploy/bin/recommend-schedule.py verify --apply
   ```

   `install` 先检查两个目标，再分别以临时文件 + `fsync` + 原子替换写入
   `/etc/systemd/system/`，仅执行 `daemon-reload`。它可更新自身安装的原样任务，
   不能只凭头部标记接管被修改的文件。两个文件分别原子写入，并非跨文件事务；
   写入或重载失败时返回非零，请先核查，不要继续启用。
   已经启用的旧任务不会因为重复安装自动停用；要调整现有部署时，先按下方命令停用。
   `verify` 检查配置语法、日历和实际加载来源，显示启停状态、运行结果与下次触发时间；
   不开始任务。首次安装、尚未运行时没有成功执行记录是正常的。
   启用、停用或手工运行前也会只读核查 systemd 当前加载来源、额外配置及重载需求，
   避免操作尚未重载的外来旧任务或同路径旧配置。

5. 确认核查结果后，站长明确启用每天运行：

   ```sh
   sudo python3 deploy/bin/recommend-schedule.py enable --apply
   ```

   **启用可能因 Persistent 立即补跑一次，并发生官方题库请求和今日一条发布。**
   如希望立即做一次真实刷新，也可单独执行：

   ```sh
   sudo python3 deploy/bin/recommend-schedule.py run --apply
   ```

   两者都不调用 AI、SMTP、微信推送；只刷新 Codeforces 官方题库元信息，成功后发布一道链接。
   `run` 只提交单次任务请求，不等待抓取完成；命令成功只表示 systemd 接收了请求。
   真正执行结果需通过 `verify` 的 `Result / ExecMainStatus` 及 journal 核查。
   刷新失败保留旧缓存，且不会发布新的今日一条，服务结果为失败，日志保留在 journal。
   宿主服务超时不证明容器里的 Docker exec 作业已经取消；发生超时后，
   请站长检查容器与 journal 确认没有残留作业，再手工重试。
   正常运行的同名 systemd 服务不会重叠；容器内刷新另有缓存文件锁。
   次日仍会按计划运行；需要当天重试时由站长手工 `run`。

停用后续计时，并检查状态与日志：

```sh
sudo python3 deploy/bin/recommend-schedule.py disable --apply
sudo python3 deploy/bin/recommend-schedule.py verify --apply
sudo journalctl -u algorithm-notebook-recommend.service -n 50 --no-pager
```

`disable` 只停用这一个 timer，不删除文件或影响其他任务，也不打断已开始的单次刷新。
再次 `enable` 仍可能因错过触发而补一次。重复启用、停用与安装可安全重用。
以上服务器实际操作均由用户自行执行；离线测试不能证明目标主机的时区数据库、
Docker 状态、官方网络可达性或任务真实成功，需要安装后的 `verify` 和 journal 确认。
systemd 行为依据官方 [timer 说明](https://github.com/systemd/systemd/blob/main/man/systemd.timer.xml)
与 [时间表达式说明](https://github.com/systemd/systemd/blob/main/man/systemd.time.xml)。

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
