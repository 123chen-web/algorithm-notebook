# Python / C++ 独立运行服务交付方案

这套文件供站长**以后手动安装**，当前没有执行安装、拉取镜像、申请证书、启动服务或改网站 `.env`。
网站仍默认关闭运行功能。默认自测只向 `127.0.0.1` 的临时假服务发送 HTTP，假服务不会执行代码。

## 运行边界与主机选择

必须使用与欧叶网站分开的 Linux VM，不能把此 Compose 拼进网站 Compose，也不能复用网站的
数据库、SMTP/AI/支付密钥、网站目录或 Docker socket。官方 Judge0 的 server/worker 使用
`privileged`，容器本身不能充当可信的宿主机隔离边界；独立 VM、主机防火墙与更新是必需条件。
不支持把它直接放进当前 Windows 网站进程执行，也不提供公开评测接口作为替代。

这套启动覆写针对官方 **1.13.1（2024-04-18）**，其发布说明指出修复了
1.13.0 及更早版本中的三个严重漏洞。这只证明该版本包含这些已公布修复，**不证明 2026 年的
镜像、内核、Isolate、编译器与第三方组件都安全**。安装时重新核对官方安全公告和镜像内容，
填写自己审核得到的真实 digest。当前模板留空，未编造 digest，也不使用 `latest`。

- [官方版本和已公布修复](https://github.com/judge0/judge0/releases/tag/v1.13.1)
- [官方 Docker 镜像标签](https://hub.docker.com/r/judge0/judge0/tags)
- [官方 Compose 的 privileged 要求](https://github.com/judge0/judge0/blob/v1.13.1/docker-compose.yml)
- [官方配置字段](https://github.com/judge0/judge0/blob/v1.13.1/judge0.conf)
- [官方 API、语言和状态码](https://ce.judge0.com/)

官方部署说明要求 Linux，并说明 Ubuntu 22.04 / cgroup v1 的主机准备；这可能涉及重启和 GRUB，
必须由站长在独立 VM 上自行评估和完成。这次没有修改任何主机的内核或启动配置。

## HTTPS 连通方式

网站服务器 → `https://RUNNER_DOMAIN` → 独立 VM 的 Caddy → 内部网络 `server:2358` → worker。

`server` 裸 API 的宿主机端口仅绑定 `127.0.0.1:2358`。Caddy 在独立 VM 的 443 端口终止 TLS，
只转发一个明确的网站出口 IP 发来的 GET/POST submissions 请求。它按连接的 `remote_ip`
判断，不信任客户端伪造的转发头；Judge0 另外核对 `X-Auth-Token`。没有使用外部 CDN 或其他
代理；若已有代理，必须另行设计可信源地址验证，不能扩大成任意 IP。

默认方案使用公网 HTTPS：独立域名的 DNS 指向 runner VM；网站固定公网出口 IP 填入
`OY_APP_EGRESS_IP`。VM 防火墙只开放所需 443/TCP，裸 API、数据库、Redis 不对外开放。
Caddy 可使用 TLS-ALPN-01，证书挑战须能连到 443；业务 HTTP 请求另外严格限制到网站 IP，
业务 matcher 不影响 TLS 握手挑战，不要以关闭业务源 IP 检查解决证书问题。
若使用同一私网/VPN，也支持一个固定 RFC1918/ULA 网站地址；必须已有通路和正确 DNS/证书，
地址检查不会验证 VPN 连通性。拒绝 192.0.2/198.51.100/203.0.113/2001:db8 等文档地址。
`RUNNER_DOMAIN` 必须有实际可用证书；站长自己的私网 CA 则需把受信任 CA 安装到网站容器。

对 VM 配置出站限制，不允许访问网站数据服务、云元数据接口或管理网络。Compose 的 sandbox
网络设为 internal，程序默认和最大权限均禁止网络，但这些设置不能替代 VM 边界与实测。

- [Caddy 源 IP matcher](https://caddyserver.com/docs/caddyfile/matchers#remote-ip)
- [Caddy 自动 HTTPS / TLS-ALPN](https://caddyserver.com/docs/automatic-https)

## 配置和手动安装步骤

1. 在独立 VM 的 `/srv/oy-runner` 放置本目录文件。复制 `judge0.conf.example` 为 `judge0.conf`、
   `images.env.example` 为 `images.env`，权限设置为仅站长可读。这个路径不是网站目录。
2. 用 Python `secrets.token_urlsafe(32)` 分别生成三个互不相同的随机值，填写 AUTHN_TOKEN、
   REDIS_PASSWORD、POSTGRES_PASSWORD。不要复制网站已有密钥，也不要把输出贴进报告或聊天。
3. 审核官方 Judge0、PostgreSQL、Redis、Caddy 镜像，填写数字版本及对应的真实 SHA-256。
   PostgreSQL 模板的数据路径按 16/17 的官方镜像布局设计；选用其他主版本需审核其数据目录
   和升级规则。不要把空 digest 或假 digest 当成完成配置。
4. 填写自己的 RUNNER_DOMAIN 和一个 OY_APP_EGRESS_IP。只接受普通 KEY=value，无 shell 展开。
5. 使用以下纯离线检查。它不加载网站 `.env`，不连接任何服务，不校验镜像内容、DNS 或证书：

```sh
python3 -B /path/to/notebook/deploy/bin/check-runner.py \
  --conf /srv/oy-runner/judge0.conf --images /srv/oy-runner/images.env
```

未填模板预期退出码 1、passed=false；填写审核值后，语法与固定限制应 passed=true。
然后由站长在独立 VM 进行 Compose 语法检查，必须 `--quiet`，避免输出解析后的密码：

```sh
cd /srv/oy-runner
docker compose --env-file images.env --env-file judge0.conf -f compose.yaml config --quiet
```

以下为未来安装命令，**本次未执行**。它们会下载镜像、创建数据库并实际运行服务。
站长先完成 VM、更新、防火墙、证书、镜像审查与资源容量准备，再手动执行：

```sh
docker compose --env-file images.env --env-file judge0.conf -f compose.yaml pull
docker compose --env-file images.env --env-file judge0.conf -f compose.yaml up -d db redis
docker compose --env-file images.env --env-file judge0.conf -f compose.yaml up -d server workers gateway
```

db/redis 初始化尚未结束时，server/worker 可能重启；由站长等待健康后再做实测。
站长最后在网站的正式配置中填写 `CODE_RUNNER_URL=https://自己的运行域名`，
`CODE_RUNNER_TOKEN=独立AUTHN_TOKEN`，并按网站自己的发布流程更新配置。
不把 runner 的 PostgreSQL/Redis 密码复制到网站，不在浏览器填写或显示 token。

## 可执行自测和验收范围

本机默认模式不需要 Docker，不新增 Python 依赖，不创建目录/数据库，不读取 `.env`：

```sh
python3 -B deploy/bin/runner-selftest.py
```

它只监听 `127.0.0.1` 的随机空闲端口，临时生成认证 token，用网站实际适配器走 POST →
排队 → GET 轮询 → Base64 输出。四个固定用例是 Python 相加、C++ 相加、编译失败、超时；
返回的是**假的预设结果**，源码仅做固定字符串比对，绝不编译、执行或调用 shell。
退出时关闭本机端口，JSON 标明 mode=mock、executed_source=false。

只有站长已经接好自己管理的服务，才在隔离的维护环境显式运行下列命令。命令只读取进程中
已有的 CODE_RUNNER_URL/TOKEN，不读取配置文件；会向该服务提交两个最小真实相加样例：

```sh
python3 -B deploy/bin/runner-selftest.py --live --confirm-isolated-service
```

`--live` 单独使用会拒绝提交；确认标志表示站长已核实服务与网站数据、密钥和宿主机隔离。
该声明仍不由脚本自动证明。本次未运行 --live。两条相加用例通过仍不能证明沙箱安全。
站长还需实际验证：语言 ID 71
确为 Python、54 确为 C++；编译错误和无限循环会按限制返回；文件、内存和进程超额被拒绝；
程序不能联网、读取网站目录或云元数据；其他 IP 和错误 token 不能提交；排队容量达到上限
时返回拒绝；日志和监控不记录源码或认证 token。

## 源码保留和停止方式

Judge0 会把源代码/输入/输出写入独立 PostgreSQL。没有承诺默认自动清理；站长须选择并安装
24 小时保留政策，对版本 1.13.1 的 submissions 表可按以下 SQL 清理已完成任务，再将其
安排为 runner VM 的每日任务。必须先确认新版本表结构；不在网站 SQLite 运行这条 SQL。

```sql
DELETE FROM submissions
WHERE status_id >= 3
  AND finished_at < CURRENT_TIMESTAMP - INTERVAL '24 hours';
```

排队中或没有 finished_at 的异常任务不由这条 SQL 删除，需单独监控并处理，避免永久保留。
独立 runner 数据库不进入网站备份；如必须备份，也须同样限制源码保留期限。
停止服务用 `docker compose ... down`（沿用上述两个 env-file 与 compose 文件参数），
不要附带删除数据卷的参数。网站取消 CODE_RUNNER 配置后即恢复明确的未配置提示。

此交付完成的是安装文件、离线检查、HTTP 协议假服务和显式真实烟雾测试入口；
真实 VM、镜像审查、DNS/TLS、防火墙、沙箱强度、容量与清理的现场验收仍需要站长准备环境。

## 启动日志覆写

官方 1.13.1 的 server/workers 会把全部导出环境通过 tee 打印到标准输出；生产 Rails
默认 debug 日志还可能保存源码、输入和 SQL 参数。本模板实际挂载 safe-server.sh、
safe-workers.sh 和 oy_logging.rb，保留官方 load-config、迁移/播种、Rails 与 Resque
启动机制，同时先创建 root-only 的 0600 environment 文件，再丢弃 tee 的标准输出。
官方 Docker entrypoint 的 cron 启动保留；没有把原始密码输出写进 Docker 日志。

覆写关闭 Rails/ActiveRecord/Resque 应用日志，过滤源码、输入、输出、认证参数作为补充；
同时丢弃 Rails 和 worker 子进程原始 stdout/stderr，只留下不含提交内容的通用启动提示。
单设 WARN 或过滤参数无法保证任意 SQL/异常信息安全，所以这里不保留详细应用诊断。
代价是故障信息减少：站长可核对离线配置、容器状态、资源/队列和返回状态，不能临时打开
原始 debug/环境输出来排查生产源码。这次未运行 Bash/Ruby 镜像，挂载行为和真实日志仍须
在独立 VM 上核验。升级 Judge0 时必须重新审核这些启动路径和覆写兼容性。

- [官方 server 启动脚本](https://github.com/judge0/judge0/blob/v1.13.1/scripts/server)
- [官方 workers 启动脚本](https://github.com/judge0/judge0/blob/v1.13.1/scripts/workers)
- [官方生产日志配置](https://github.com/judge0/judge0/blob/v1.13.1/config/environments/production.rb)
