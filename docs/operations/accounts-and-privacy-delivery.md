# D-后端交付与验证记录

本次工作保留了已有产品改名，未修改 README，未提交、推送或切换分支。分支仍为 `main`，HEAD 为 `03293b1`。工作区同时有任务 C 与 D-前端的改动，下文只记录本次后端工作。

## 实现对应关系

| 要求 | 文件与主要函数 | 结果 |
| --- | --- | --- |
| 迁移 4 | `db.py::_apply_accounts_and_privacy` | 增加 `deleted_at`、`is_admin`、`terms_accepted_at`、`terms_version`；不自行提交或执行 `executescript` |
| 管理员身份 | `main.py::current_user`、`bootstrap_admin`、`register`；`admin_tool.py` | 身份来自数据库；无有效管理员时注册或启动引导；改名不失权；工具支持 list/grant/revoke 和最后管理员保护 |
| 用户名 | `db.py::normalize_username`；`main.py::check_username_available`、`register`、`update_username`、`login` | NFKC、去空白、小写、规范化后长度检查；保留名返回 400；登录兼容旧全角名字及规范化后长度膨胀的旧名字 |
| 新密码 | `main.py::check_new_password`、`password_matches`、`Registration`、`ResetPassword` | 8–128 位、129 项弱口令表、重复字符和账号身份信息保护；旧 6–7 位密码仍可登录；畸形存储值返回 False |
| 改密码 | `main.py::change_password` | 按用户限流；当前密码错返回 400；哈希计算在写事务外；更新密码、撤销其他会话、清重置令牌同事务完成 |
| 退出其他设备 | `main.py::revoke_sessions`、`revoke_other_sessions` | 保留 Cookie 对应的当前会话；体验账号也可使用 |
| 注销 | `main.py::delete_account`、`delete_account_data`、`require_deletable_account` | 体验账号/管理员拒绝；有其他成员的小组点名返回 409；单事务删除学习数据、退出小组、匿名成本记录和脱敏用户；保留论坛、举报、订单与套餐字段；提交后删除头像并清 Cookie |
| 注销账号过滤 | `main.py::login`、`current_user`、`forgot_password`、`reset_password`、`ai_quota`、`leaderboard`、`group_members_by_id`、`list_groups`、`admin_dashboard` 等 | 已注销账号不能登录或找回密码；榜单、成员和成员数、后台用户与有效订阅统计不含已注销账号；论坛及论坛举报作者继续显示占位名 |
| 邮箱验证 | `main.py::EmailUpdate`、`update_email` | 必须提供当前密码；错误返回 400；原有格式校验和重复邮箱行为保留 |
| 条款页面与注册同意 | `legal.py`、`static/legal.css`、`main.py::terms`、`privacy`、`register`、`.env.example` | 两页公开中文 HTML、运营者字段转义、无内联脚本样式、沿用安全头、5 分钟缓存；注册记录 UTC 同意时间和版本；体验账号不记录 |
| 其它注册调用 | `sample_world.py::Builder.register`、测试辅助与直接注册请求 | 增加 `accept_terms: true`；README 无直接注册 curl 例子需要改，本次没有编辑 README |
| 清理及提醒 | `send_reminders.py`、`cleanup_trial_accounts.py` | 排除已注销用户 |
| 单一产品常量 | `legal.py::PRODUCT_NAME`；`main.py`、`payment_channels.py`、`share_card.py`、`send_reminders.py` | Python 运行时代码中的产品名只定义一次，各展示位置引用常量；保留原有改名后的文案 |
| 注销与在途请求 | `main.py` 的头像、题目、小组、社区新建及分析保存路径；`ai_limits.py::track_call`、`clusters.py::create_clusters` | 写锁内复查账号，防止注销后重新写回个人数据；注销后完成的 AI 成本记录不再关联用户 |

首次追加迁移 4 时迁移 3 尚不存在，按要求直接在末尾追加 4。任务 C 后来加入迁移 3；最终列表为 **1、2、3、4**，严格递增。`review_mistake` 的变更属于任务 C，本任务没有编辑该函数。

`payment_channels.py`、`share_card.py` 的额外修改仅为引用产品常量；`ai_limits.py`、`clusters.py` 的最小联动用于保证在途 AI 不破坏注销的数据处理结果。没有新增依赖。

## 接口细化

- 注册采用 `accept_terms: bool = False`，在处理函数里拒绝缺失/false：HTTP 400，`detail` 为“请先阅读并同意服务条款和隐私政策”。这是需求允许的两个方案之一。
- 当前密码不正确均返回 HTTP 400，避免前端的 401 全页登出行为。
- `register` 仍返回原来的 `id`、`username`，`login` 仍返回 `{ok: true}`。
- 与固定保留名重合的配置管理员名字，在首个管理员注册引导时允许；普通用户不能使用它。已有管理员可使用配置名改名，其他固定保留名仍按通用规则保护。
- 两个法律页面使用 `Cache-Control: public, max-age=300`；静态样式版本读取首页，`legal.css?v=1`。
- 管理后台用户总数、正式/体验人数、新用户数、活跃用户数与有效订阅人数排除已注销用户；历史付费订单和匿名调用成本继续保留。项目没有独立管理员用户列表路由。

## 新增测试

| 文件 | 覆盖 |
| --- | --- |
| `tests/test_account_security.py` | 121 个展开用例：三处密码策略、用户名规范化/保留/旧数据、管理员引导与改名、会话撤销、注销逐表删除和保留、409 无修改、触发器中途失败回滚、头像并发注销、已注销账号过滤、邮件/清理脚本过滤、邮箱密码、条款同意与页面安全 |
| `tests/test_account_inflight.py` | 6 个展开用例：注销后在途成本匿名化、正常成本仍关联用户、聚类与薄弱点分析不能回写注销账号、首次写入前注销保护 |
| `tests/test_admin_tool.py` | 22 个展开用例：list/grant/revoke、最后管理员及 --force、注销用户与未知用户名拒绝、规范化与旧名字查找 |
| `tests/test_legal_assets.py` | 8 个展开用例：完整页面、资源版本、运营者转义、内容要点、CSP 所需无内联结构、令牌配色与移动留白 |
| `tests/test_migrations.py` | 本任务追加 3 个用例：旧库升级数据不变、四列默认值、迁移 4 失败完整回滚 |

## 旧测试改动逐处说明

| 文件与用例 | 最小修改原因 |
| --- | --- |
| `tests/test_app.py::register` | 辅助注册请求补 `accept_terms: true`；原有 `a-test-password-123` 已合规，保留 |
| `test_register_is_rate_limited` 两个直接请求 | 补同意字段，使测试继续检验邀请码/限流 |
| `test_register_validates_and_dedupes_email` 两个直接请求 | 补同意字段，使测试继续检验邮箱校验/去重 |
| `test_forgot_and_reset_password_flow` | 短新密码由 Pydantic 422 改为密码策略 400，并断言中文提示 |
| `test_update_email_for_existing_account` 的成功及无效邮箱请求 | 补必填当前密码；仍分别检验规范化与原有 422 格式校验 |
| `test_update_email_rejects_duplicate` | 补当前密码；仍检验重复邮箱 409 |
| 旧 `test_renaming_away_from_admin_username_loses_admin_immediately` | 改名为 `test_renaming_away_from_admin_username_keeps_admin_and_reserves_name`；确认改名成功后仍持权，并拒绝别人注册或改名成引导名 |
| `tests/test_moderation.py::become_admin`、`tests/test_avatars.py::become_admin` | 原辅助函数只在已注册后设置环境变量；新模型必须显式写 `is_admin=1`，保留这些测试对内容/头像审核的原覆盖 |
| `tests/test_forum_threads.py::test_edit_reply_in_deleted_post_does_not_read_hidden_reference` | 管理员测试名字从固定保留名 `moderator` 改成 `moderator_user`；继续使用注册引导 |
| `tests/test_db.py::test_init_migrates_old_users_and_preserves_data` | `SELECT *` 整行预期字典补四个新列默认值；原字段预期不变 |
| `tests/test_group_levels.py::test_group_points_queries_batch_groups_and_keep_membership_windows` | 简化内存 users 表补 `deleted_at` 与 NULL 值，适配成员过滤 SQL；原积分/查询数量断言保留 |
| `tests/test_group_activity.py::test_shared_member_activity_streams_once_and_keeps_each_groups_daily_window` | 同上，仅补内存 schema 的新字段 |
| `tests/test_migrations.py` 中任务 C 的升级断言 | 升级后的版本由 `==3` 改为 `==db.SCHEMA_VERSION`；故意构造版本 2 的断言保留 |

已有测试不需要把合规注册密码替换成别的值。`test_app.py` 的 SM-2 易度预期改变属于任务 C；`tests/test_export.py`、`tests/test_share_card.py` 已存在的产品名预期改动也没有回退。

## 实际验证结果

所有执行都使用 `python -B`；pytest 带 `-p no:cacheprovider`。设置 `PYTHON_DOTENV_DISABLED=1`，防止既有自动加载逻辑读取 `.env`；没有调用真实 AI、邮件或支付。

- 9 项通过：`tests/test_legal_assets.py` 全部 8 项，以及迁移版本严格递增的纯函数用例。
- 17 项通过：管理员工具的用户名规范化/边界 8 项、畸形哈希/合法哈希 7 项、两个现有内存小组积分 SQL 回归。
- 1 项通过：新密码 128/129 位边界及弱口令表数量。
- 共 **27 个无需系统临时目录的 pytest 用例实际通过**；两条既有弃用警告来自 Starlette/httpx 与 AnyIO，未增加依赖处理它们。
- 用实际 `main.app` 的 TestClient、不开启数据库 lifespan，分别请求 `/terms`、`/privacy`：200、安全头、5 分钟缓存、无 Cookie、无内联样式检查通过。该检查没有访问数据库。
- 五个相关测试文件收集成功，共 172 项；收集不等于执行通过。
- 源码语法、路由注册和 `git diff --check` 检查通过。没有跑整个测试集，也没有进行真实浏览器视觉验证。

首次尝试命令：

```powershell
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/test_migrations.py -x
```

在第一项 setup 即失败，输出 `1 error in 0.43s`。原样报错：

```text
PermissionError: [WinError 5] 拒绝访问。: 'C:\\Users\\chenjin\\AppData\\Local\\Temp\\pytest-of-chenjin'
```

因此停止依赖系统临时数据库/头像目录的测试，没有更改临时路径，也没有创建项目内替代临时/缓存目录。上述 API、磁盘迁移与 CLI 数据库行为均待本机运行，不能宣称已通过。

一次辅助连接替换验证接线失误，误连默认数据库，报 `sqlite3.OperationalError: no such column: is_admin`。既有 `db.connect` 在异常路径回滚并关闭连接，未提交删除或脱敏，未对默认库执行 `init_db`，未输出用户数据；此方式已停止，内存替换结果不计入正式通过数。

建议在系统临时目录可用的正常终端分批运行以下相关文件；仍不要设置仓库内 basetemp 或启用 pytest 缓存：

```powershell
$env:PYTHON_DOTENV_DISABLED = '1'
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/test_account_security.py tests/test_account_inflight.py tests/test_admin_tool.py tests/test_legal_assets.py tests/test_migrations.py tests/test_db.py
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/test_app.py tests/test_groups.py tests/test_group_levels.py tests/test_group_activity.py tests/test_moderation.py tests/test_avatars.py tests/test_forum_threads.py
.venv\Scripts\python.exe -B -m pytest -p no:cacheprovider -q tests/test_export.py tests/test_share_card.py tests/test_payment_channels.py tests/test_payments.py tests/test_payments_api.py tests/test_send_reminders.py tests/test_cleanup_trial_accounts.py tests/test_weakness_insights.py tests/test_clusters.py tests/test_ai_calls.py
```

## 建议由维护者合并到 README 的文字

### 账号与隐私

账号页支持修改密码、退出其他设备和注销账号；修改邮箱需验证当前密码。新设置的密码须为 8–128 位，并通过常见弱口令和账号信息检查，历史短密码仍可登录。注册前须阅读并同意[服务条款](/terms)和[隐私政策](/privacy)。注销会删除个人学习数据，论坛内容以“已注销用户”保留，订单按财务要求保留。管理员权限由数据库字段保存，改名不会失权，可通过 `python admin_tool.py list/grant/revoke` 管理。升级和环境变量、注销数据处理及上线前法律审阅清单见[账号与隐私运维说明](docs/operations/accounts-and-privacy.md)。

## 上线前需要维护者确认

- 填写真实 `LEGAL_OPERATOR_NAME` 与 `LEGAL_CONTACT_EMAIL`。
- 请法律人士审阅跨境传输、个人信息保护法告知义务、未成年人、AI 生成内容标识、争议解决条款、数据与财务记录保存期限；页面没有声称已通过法律审核。
- 核对实际备份频率与保留配置。页面按默认约 14 份滚动覆盖说明，不能替代对实际部署的确认。
- 在正常终端完成待跑的 API/迁移回归后再部署；本次不把测试收集或辅助内存检查当作正式验证。
