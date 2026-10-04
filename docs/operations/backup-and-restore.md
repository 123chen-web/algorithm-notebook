# 备份与恢复

数据库保存账号、密码哈希、错题、复习记录和订单；头像保存在数据库外的目录。只复制 `.db` 会漏掉头像，直接复制正在写入的 SQLite 文件也可能漏掉 WAL 中的数据。`backup.py` 使用 SQLite 在线备份接口取得一致的数据库快照，应用可以继续写入。

## 创建与校验

在仓库根目录运行：

```powershell
.venv\Scripts\python.exe backup.py
.venv\Scripts\python.exe backup.py --output-dir D:\secure-backups\algorithm-notebook --keep 14
.venv\Scripts\python.exe backup.py verify D:\secure-backups\algorithm-notebook\backup-20261003-020000.tar.gz
```

Linux 使用 `.venv/bin/python`。源路径与应用一致：`DATABASE_PATH` 默认 `data/notebook.db`，`AVATAR_DIR` 默认 `data/avatars`。支持 `~`，相对路径以脚本所在的仓库根目录为基准，与运行命令的当前目录无关。

输出目录默认 `data/backups`，可用 `BACKUP_DIR` 覆盖；默认保留最近 14 份，可用 `BACKUP_KEEP` 覆盖，`0` 表示全部保留。命令行 `--output-dir`、`--keep` 优先。脚本只读取进程环境变量，不自动加载 `.env`；自定义数据路径时，必须把应用使用的相同变量传给脚本或调度器。

归档使用 UTC 文件名 `backup-YYYYMMDD-HHMMSS.tar.gz`，同秒重名增加数字后缀。成功后按归档修改时间保留最近 N 份，只删除输出目录中更旧的 `backup-*.tar.gz` 普通文件；刚生成的文件、目录、链接及其他文件保留。比本次归档更新的已有文件也保留。输出目录不能放在头像目录内部。请避免同时运行多个备份任务。

归档包含：

- `notebook.db`：完整数据库快照，包含订单等所有表，并通过 `PRAGMA integrity_check`。
- `avatars/`：头像文件，缺失或空目录也可以备份。头像目录内的符号链接、特殊文件会被拒绝。
- `MANIFEST.json`：格式版本、UTC 创建时间、数据库 SHA-256、字节数、schema 版本、核心表行数和头像文件数。

归档不含 `.env` 中的密钥、源代码、运行环境及日志。密钥需要另行妥善保存，恢复服务时仍需原来的配置。归档含密码哈希和个人数据，请存放在受控目录，限制访问并考虑加密；脚本尽量设置归档权限为 `0600`，Windows 的访问控制仍需管理员自行配置。不要把归档提交到 Git 或放在可公开下载的位置。

脚本不负责上传。应把成功的归档复制到另一台机器或受访问控制的对象存储，避免机器损坏时数据和本机备份一起丢失。保留策略只作用于本机输出目录，异地副本需另设生命周期。

数据库快照是一致的；头像文件是随后逐个读取，数据库与头像不能形成跨文件事务。若备份期间发生头像上传或删除，可能需要重试或在停写窗口备份。

## 定时执行

以下例子每天本地时间 02:00 备份；归档文件名仍使用 UTC。先手动运行一次，确认执行账号有源数据读取权和输出目录写入权。自定义 `DATABASE_PATH`、`AVATAR_DIR` 时，把相同值加入任务环境；示例使用默认源路径。

### Windows 任务计划程序

在终端创建每日任务：

```powershell
schtasks /Create /TN "AlgorithmNotebookBackup" /SC DAILY /ST 02:00 /TR 'C:\dev\algorithm-notebook\.venv\Scripts\python.exe C:\dev\algorithm-notebook\backup.py --output-dir D:\secure-backups\algorithm-notebook --keep 14' /F
```

在任务计划程序中选择合适的运行账号，并按需要启用“无论用户是否登录都要运行”。检查任务的“上次运行结果”；路径含空格时，应通过任务计划程序分别填写程序路径和参数，避免命令行引号歧义。

### Linux cron

通过 `crontab -e` 添加：

```cron
0 2 * * * /srv/algorithm-notebook/.venv/bin/python /srv/algorithm-notebook/backup.py --output-dir /srv/secure-backups/algorithm-notebook --keep 14 >> /var/log/algorithm-notebook-backup.log 2>&1
```

提前创建备份目录和日志文件，并赋予任务账号所需权限；监控非零退出码与最近归档时间。

### Linux systemd timer

创建 `/etc/systemd/system/algorithm-notebook-backup.service`：

```ini
[Unit]
Description=欧叶OY备份

[Service]
Type=oneshot
User=algorithm-notebook
WorkingDirectory=/srv/algorithm-notebook
ExecStart=/srv/algorithm-notebook/.venv/bin/python /srv/algorithm-notebook/backup.py --output-dir /srv/secure-backups/algorithm-notebook --keep 14
```

创建 `/etc/systemd/system/algorithm-notebook-backup.timer`：

```ini
[Unit]
Description=每天备份欧叶OY

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

根据部署修改运行账号与路径；自定义源目录时在 service 中增加 `Environment=DATABASE_PATH=...` 和 `Environment=AVATAR_DIR=...`。启用并检查：

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now algorithm-notebook-backup.timer
systemctl list-timers algorithm-notebook-backup.timer
journalctl -u algorithm-notebook-backup.service
```

### Docker Compose

在 Compose 项目目录运行，或由宿主机 cron/任务计划程序定时调用：

```sh
docker compose exec -T app python backup.py --keep 14
```

容器镜像必须包含 `backup.py`，备份目录必须落在持久化卷或宿主机挂载目录中。用 `--output-dir` 指定容器内路径时，也要配置对应挂载；容器临时文件系统不适合作为唯一备份位置。使用容器中与应用一致的 `DATABASE_PATH` 和 `AVATAR_DIR`，并检查命令退出码。

## 恢复与演练

1. 停止服务及其他写数据库的进程，保留现有数据副本。
2. 恢复到独立的临时目录。恢复命令会先完整校验归档；默认拒绝目标中已有的 `notebook.db` 或 `avatars/`。`--force` 仅覆盖指定恢复目录的数据，不能绕过与应用数据路径重合的保护。

   ```powershell
   .venv\Scripts\python.exe backup.py restore D:\secure-backups\algorithm-notebook\backup-20261003-020000.tar.gz --into D:\restore-drill\algorithm-notebook
   ```

3. 再运行 `backup.py verify` 校验同一份归档；它核对大小、SHA-256、完整性、schema 和核心表行数，并拒绝路径穿越、符号链接和硬链接。
4. 在服务停止的前提下，手动用恢复出的 `notebook.db` 与完整 `avatars/` 目录替换 `data/notebook.db`、`data/avatars/`。自定义源路径时替换实际配置的路径。不要混用旧头像目录或旧的 `notebook.db-wal`、`notebook.db-shm`、`notebook.db-journal`；把它们与原库一起移到保留副本中，再放入恢复文件。
5. 确认运行账号可读写数据、`.env` 等运行配置已另行恢复，启动服务。
6. 访问 `/healthz`，再登录检查代表性错题、复习记录、订单和头像。

建议每季度做一次恢复演练，使用隔离的服务实例与独立目录，记录备份时间、恢复耗时和检查结果。校验通过只说明归档与快照一致，不能代替业务数据检查；尤其应确认订单记录与实际支付情况。演练通过后按数据保护要求清理演练副本。
