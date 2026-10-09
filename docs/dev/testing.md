
## 自动化测试

Windows：

```powershell
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q
```

macOS / Linux：

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider -q
```

先安装开发依赖：Windows 用 `.venv\Scripts\python.exe -m pip install -r requirements-dev.txt`，macOS / Linux 用 `.venv/bin/python -m pip install -r requirements-dev.txt`。

测试使用临时数据库，AI 调用被模拟，不会产生 API 费用。
不要把测试临时目录或缓存目录放进仓库；系统临时目录遇到权限错误时应停止，
报告具体错误后再决定如何处理。页面静态测试不需要临时数据库。


## 本机样本环境

想用一个“什么都有”的账号来试功能，而不是在自己的真实数据里乱点，可以启动样本环境：

```powershell
.venv\Scripts\python.exe sample_world.py
```

第一次运行会在 `data/sample.db` 里建一份独立的假数据，然后在 http://127.0.0.1:8001 启动；
**不会碰** `data/notebook.db`。里面有：

- 主测试号 `样本同学`：30 多条错题（各分区都有，12 天连续打卡，还有一个很久没新增的分区），
  加入 4 个小组（金榜冲刺队 Lv.6、周末算法共学、新手村，以及已满员的满分俱乐部 Lv.8），
  还空着 1 个小组名额；讨论区发过帖，带一份示例分析报告。
- 新人号 `样本新人`（只有 4 条错题、没有小组，用来试“数据不足”和加入被拒）、管理员
  `样本管理员`（举报队列里有一条示例举报），以及二十来个陪衬成员，全部共用同一个密码。
- 账号、密码和可用来测试加入的邀请码写在 `data/sample-account.txt`；`data/` 已被 Git
  忽略，密码不会进入仓库。
- 邮件和支付在这个环境里一直是关闭的；AI 默认也是关闭的（点“分析”会提示未配置，不会产生
  费用）。想试真实的 AI 分析，加 `--with-ai` 启动：它会使用 `.env` 里的 `OPENAI_API_KEY`
  （以及 `OPENAI_MODEL`、`OPENAI_BASE_URL`），点“分析”是真实调用，会产生费用。
- 请用 `127.0.0.1` 而不是 `localhost` 打开：浏览器按主机名保存 Cookie，用 `localhost`
  会和 8000 端口的真实应用互相顶掉登录。

`--reset` 丢掉样本数据并重建（日期会重新按“今天”计算，密码不变）；`--build-only` 只建不启动；
`--with-ai` 见上。重建前要先关掉正在运行的样本服务器。

