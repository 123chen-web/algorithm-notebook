# SEC 第二轮续做报告 · 2026-10-05

代码已在原有未提交改动上续完；数据库集成验证未完成，不能据此宣称可上线。没有提交、推送、切分支，没有连接 `data/notebook.db`，没有在项目内建立临时或缓存目录。

## 起点、继承与本次补做

开始时 `main` 的实际 HEAD 为 `d134e81`，已经含复习手感迁移 10。工作区已有 SEC 后端、前端、测试和文档的未提交改动；先检查了 `git status`、`git diff`，保留这些可用部分，没有套用旧补丁。既有 `.codex/` 和 `docs/reports/rvf-review-feel-2026-10-04.md` 未改动。

结束前 HEAD 被本次操作之外的提交推进到 `965ab25`（新增 duck prompt 和 offline queue 五个独立文件）。检查 `git diff d134e81..HEAD --name-only` 后确认这些文件与 SEC 改动不重叠，没有改动它们。本次没有执行任何提交命令。

继承部分包括：共享当前密码限流、改邮箱清除找回令牌、改密码轮换会话、写锁内会话复查、移除注册和启动补权、用户名不可见字符检查、精确登录查找、CLI 检查及 ID 授权、注销后的支付/导入防线、头像访问检查、匿名名后缀，以及多数数据库用例。

本次实际补做：

1. 修复已复现的 3 个 Node 失败：对话框增加 `sessionEpoch` 守卫，绑定邮箱的失败响应也检查发起账号与 epoch；同一用户重新登录后也不能被旧注销响应影响。
2. 在认证请求结束后统一释放登出、关闭和取消按钮的锁；补齐退出其他设备、注销两种请求未决及失败后的行为测试。
3. 登录模型保留原始名字，避免 Pydantic 在精确查找之前删除存量用户名末尾的控制字符；注册和改名仍严格拒绝不可见字符。
4. CLI 授权/撤权要求精确用户名或 ID，拒绝只有规范化键相同的名字；歧义名字仍必须用 ID。补充纯函数和数据库断言。
5. 改密码轮换会话时禁止 `set_session` 顺便清理其他用户的过期会话；增加其他用户含过期令牌也保持原样的断言。
6. 重置密码把空 token、空密码交由业务校验返回错误码，并把恰好到期的 token 判为失效；补空值和到期边界用例。
7. 增加精确登录冲突、邮箱事务回滚、存量管理员保留、残留头像文件标志等数据库回归；加强样本世界仅有指定管理员的断言。
8. 增加不依赖临时目录的限流、写前会话复查单元测试，执行 A/D/E 变异检查；更新静态版本及测试通过数下限。
9. 核实迁移 4 的实际内容，修正文档里不存在的一次性回填承诺，记录规格与基线的差异。

## A–L 对照

| 编号 | 实现与正反面测试 | 本机验证状态 |
| --- | --- | --- |
| A | 改密码、注销、改邮箱均使用 `verify_current_password`；用户 5 次失败/IP 20 次尝试，15 分钟窗口，先限流后哈希，成功清用户桶；同用户验证串行避免成功清零与在途失败交错。数据库测试覆盖共享入口、窗口、IP 跨用户、成功清零和不变的数据快照；单元测试用调用计数证明超限不算哈希。 | 单元及变异通过；数据库未运行 |
| B | 邮箱更新与删除该用户全部找回密码令牌在同一事务；错误密码、重复邮箱不改令牌，新增删除令牌失败时邮箱回滚用例，其他用户令牌保留。 | 数据库未运行 |
| C | 同一事务修改密码、踢其他设备、删旧当前令牌、生成新令牌并下发 Cookie，清找回令牌；其他用户的有效和过期会话均不动。失败时检查密码哈希、全部会话及令牌快照不变。 | 数据库及 C 变异未运行 |
| D | 三个写接口在写锁内复查 token 属主、有效期、注销及封禁状态；交错撤销/令牌换属主返回 401。注销成功不下发 `Set-Cookie`。前端锁登出、切换账号和关闭入口；强制 reset 仍保持未决锁，响应结束才放开，迟到成功与失败不改新账号。 | Node、模拟边界单元及 D 变异通过；SQLite 交错事务未运行 |
| E | 新注册/改名在 trim 前拒绝 Unicode Cc/Cf，再做 NFKC/小写/trim；保留名使用同一比较键。登录不套用新建名字拒绝规则，允许存量不可见字符名字精确登录。 | 纯函数及 E 变异通过；注册/登录数据库用例未运行 |
| F | 400 顶层含 `code: weak_password` 或 `invalid_token`，中文 detail 保持；前端只在后者清 token。新增空值、到期边界与同链接重试测试，迟到响应有 epoch/token 守卫。 | 前端通过；后端数据库未运行 |
| G | `signedOut` 和 `enterApp` 清邮箱表单及密码；邮箱请求成功与失败均检查账号 ID/epoch。 | Node 正反面通过 |
| H | 注册只创建普通用户，移除启动按名字补权；已有 `is_admin=1` 保留，样本管理员在隔离样本世界按 ID 用 SQL 明确授予。管理员由 CLI 管理。发布的迁移未改。 | CLI 纯函数通过；注册、管理员保留和样本运行未验证；迁移 4 差异见下文 |
| I | `check-usernames` 用 SQLite `mode=ro`，不初始化/迁移，报告有效账号规范化冲突、不可见字符、注销保留前缀占用；登录先精确，否则仅在规范化候选唯一时匹配。CLI 歧义列候选、按 ID 操作。 | CLI 纯函数通过；只读输出、登录优先级、ID 授权数据库测试未运行 |
| J | 创建订单写锁内拒绝注销账号，每个导入文件的写事务内再查注销；支付回调/退款财务状态照常落库，已注销用户权益跳过并记日志。正常账户支付/退款既有用例保留。 | 数据库未运行 |
| K | 头像读取及 `/api/me` 的 `has_avatar` 检查作者未注销；注销提交后 unlink 失败只记日志，残留文件也不能经头像接口读到。 | 数据库/文件集成未运行 |
| L | 匿名名被占用时从 `-2` 找第一个空闲值；测试无冲突、一个冲突、连续多个冲突以及其他用户行仍存在。 | 数据库未运行 |

## 规格差异与保留范围

- **迁移 4 差异**：`d134e81`/`965ab25` 的 `_apply_accounts_and_privacy` 只有 4 条添加字段语句，没有说明中提到的 `ADMIN_USERNAME` 一次性回填；HEAD 中 `db.py` 也没有其他管理员回填。按“不改已发布迁移、不新增 SEC 迁移、不再启动补权”保留现状，没有伪造或补入回填。已存在管理员字段不变；旧站点没有管理员时，需要运维确认 ID 后运行 CLI。已向用户提出此基线差异，文档明确写出实际行为。
- **验证限制**：系统 pytest 临时目录权限失败，停止数据库验证；未使用仓库 basetemp、内存 SQLite 或其他路径绕过。A/D 的补充单元测试模拟数据库 I/O，不冒充 SQLite 集成结果；C 的旧令牌失效变异仍待隔离副本执行。
- **头像范围**：只修改 SEC 负责的头像接口和本人 `has_avatar` 计算，没有改论坛 `comment_response`、`list_posts`、讨论区、管理后台指标或小组展示函数。即使论坛旧标志仍提示有头像，注销作者的实际头像读取会被拒绝；论坛标志未在本轮改动。
- **样式验证**：没有新增 CSS、DOM 元素或文字/底色配对，已有 ink/qixi 对比度测试通过。本轮未启动服务器做 320/360/390 实机浏览器验证，因为不能使用默认真实数据库，也没有可用的数据库测试临时目录；Node 使用假 DOM。
- 不改 `elapsed_days` 算法和其语义测试；其含义仍为评分前间隔加实际逾期天数，即距上次复习的天数。`test_review_feel_static.py` 仅同步 app.js 资源版本。
- 被封禁用户仍不能自助注销，保持封禁即时生效；其他 Unicode 同形字不在本轮范围。

## 改动文件

以下列的是最终未提交 SEC 差异；“继承”指本次开始时已在工作区，“补做”指这次增加或修正。

| 文件 | 一句话说明 |
| --- | --- |
| `main.py` | 继承 A–D/F/H/K/L 认证加固，补做原始登录输入、空重置参数/到期边界、轮换时不清理其他用户过期会话。 |
| `db.py` | 继承比较键与 Cc/Cf 拒绝；本次未改 SCHEMA、版本或任何迁移。 |
| `admin_tool.py` | 继承只读检查、歧义拒绝和 `--id`，本次补精确用户名要求。 |
| `payments.py` | 继承注销账号下单检查、回调及退款只跳过权益并记日志，本次保留。 |
| `import_notes.py` | 继承目标账号注销检查和逐文件写前复查，本次保留。 |
| `sample_world.py` | 继承注册后按 ID 明确授予样本管理员，本次保留。 |
| `.env.example` | 补正管理员配置与实际迁移差异的说明。 |
| `docs/operations/accounts-and-privacy.md` | 继承第二轮运维说明，补精确 CLI 名字要求及实际迁移 4 没有回填的事实。 |
| `static/account.js` | 继承未决认证锁，补 sessionEpoch 守卫和统一解锁关闭入口。 |
| `static/app.js` | 继承错误码、表单清空和认证入口锁，补邮箱失败回调的账号守卫。 |
| `static/index.html` | 将本次起始的 app.js v71/account.js v2 递增为 v72/v3（HEAD 原值是 v70/v1）。 |
| `tests/account_behaviour.cjs` | 继承未决锁和迟到响应测试，补退出其他设备与注销的未决锁/失败解锁测试。 |
| `tests/sec_auth_behaviour.cjs` | 继承新文件的 reset/email/认证入口行为测试，本次复现并修复其中失败，不改其断言。 |
| `tests/test_account_behaviour.py` | 通过数下限加强为 35，并保留终端/TAP 两种格式识别。 |
| `tests/test_sec_auth_behaviour.py` | 继承 Node 包装器，通过数下限加强为 20。 |
| `tests/test_account_security.py` | 继承第二轮语义更新、改密码失败数据快照和法律页非永久缓存断言。 |
| `tests/test_sec_round2.py` | 继承数据库边界用例，补纯单元、精确名字冲突、空 reset/到期、回滚、其他用户过期会话、残留头像及管理员保留测试。 |
| `tests/test_admin_tool.py` | 继承 ID/歧义/只读测试，补精确名字正反面及纯函数复现。 |
| `tests/test_import_notes.py` | 继承注销前及逐文件交错注销的拒绝导入测试。 |
| `tests/test_payments.py` | 继承注销后下单、回调入账和在途退款权益不变测试。 |
| `tests/test_migrations.py` | 本次新增已升级库管理员字段在重复初始化及配置变化后保留的断言，版本仍为既有的 10。 |
| `tests/test_sample_world.py` | 本次加强样本管理员字段及只有指定样本管理员的断言。 |
| `tests/test_account_ui_assets.py` | 本次将 app.js/account.js 版本下限加强为 72/3。 |
| `tests/test_forum_page_assets.py` | 仅同步 app.js 的明确版本断言为 72。 |
| `tests/test_intro_film_assets.py` | 仅同步 app.js 的加载次数及依赖顺序中的版本为 72。 |
| `tests/test_onboarding_assets.py` | 仅同步 app.js 的版本断言为 72。 |
| `tests/test_review_feel_static.py` | 仅同步 app.js 的资源版本及顺序断言为 72。 |
| 本报告 | 新增完整续做记录和验证边界。 |

## 新增名字与对外契约

- 没有新增 HTTP 路由、DOM id 或 CSS 类；复用已有表单和账号对话框。
- 新增 Python 辅助函数：`db.sec_username_key`、`db.sec_has_invisible_username`、`main.sec_has_avatar`、`main.sec_verify_password_attempt`、`main.sec_recheck_session`；`verify_current_password` 增加 `request` 参数，`set_session` 增加 `sec_cleanup_expired` 可选参数。
- 新增 CLI：`python admin_tool.py check-usernames`，grant/revoke 的 `--id` 参数；这些是工具能力，并非本次对真实用户进行过授权。
- 新增前端对外函数：`window.Account.isPending()`；辅助名字 `secClearEmailForm`、`secRenderSessionLock`、`secOwnerEpoch`。
- 既有接口新增行为：reset-password 的 `code`；password 成功轮换 Cookie；delete-account 成功不返回 `Set-Cookie`；改邮箱成功失效找回令牌。`api()` 把后端 `code` 放到 Error 对象上。

## 旧测试改动原因

1. `test_account_security.py` 的公开注册、启动补权、无有效管理员分支：原先期待按名字提权，改为拒绝保留名/普通用户/不自动提权，符合 H；管理员改名与注销测试改为显式 SQL 设置管理员身份，避免沿用已删除的引导路径。
2. 该文件的改密码成功断言：原来的 Cookie 不变、旧令牌仍在断言改为新 Cookie 不等于旧值，旧 token 401，数据库只剩新令牌和无关用户令牌，符合 C。
3. 改密码错误用例：新增密码哈希、会话和找回令牌的完整快照不变、Cookie 不变及无 `Set-Cookie` 断言；这是加强失败路径，未放宽判断。
4. 敏感操作限流错误文案：改为需求指定的“尝试次数过多，请 15 分钟后再试”，次数仍为 5，符合 A。
5. 注销成功 Cookie：从期待清空 Cookie 改为响应无 `Set-Cookie`、浏览器仍保留已失效旧 Cookie，库内会话删除仍检查，符合 D。
6. 法律页缓存：原缓存存在检查保留，新增拒绝 `immutable`，`max-age`/`s-maxage` 不得超过 3600 秒，防止永久缓存；服务现值 300 秒不变。
7. `test_admin_tool.py`：原规范化拼写授权测试改为拒绝非精确拼写、确认未提权，再用精确名字成功；旧全角名字使用其精确拼写，撤权也使用精确名字，符合 H/I。规范化器自身的正反面断言保留。
8. `account_behaviour.cjs`：原允许未决请求关闭/重开对话框的情景，按 D 改为入口保持锁；保留 forced reset、换用户、同用户新会话的迟到响应不改 UI 断言。新增 revoke/delete 两分支锁测试。
9. 两个 Node 包装器下限分别提高到 35/20，未放宽通过数或失败检查。
10. 四个其他功能静态文件只替换 app.js 版本常量，原因是同一共享资源改版；未改这些功能的语义断言。账号静态版本下限也提高到 72/3。
11. `test_migrations.py` 只增加已有管理员保留测试，旧迁移用例和版本断言未改；`test_sample_world.py` 只增加角色身份与唯一指定样本管理员的断言。
12. 支付/导入测试是在既有测试上新增注销边界用例，原正常支付、退款、导入的测试不变。

## 实际执行的命令与结果

所有 pytest 命令都先设置了以下环境；数据库文件名每次用新的 UUID，且无测试使用默认真实库。

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:DATABASE_PATH=Join-Path ([System.IO.Path]::GetTempPath()) ('sec-verify-'+[guid]::NewGuid().ToString()+'.db')
```

1. `git status --short`、`git diff --stat`、`git log -1 --oneline`、分区 `git diff`：完成起始与结束检查。
2. `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -q -x tests/test_sec_round2.py tests/test_account_security.py tests/test_admin_tool.py tests/test_payments.py tests/test_import_notes.py`：首个用例夹具报错，`1 error`，即停止数据库测试。
3. `node --test tests/account_behaviour.cjs tests/sec_auth_behaviour.cjs`：修复前 `50 passed, 3 failed`，修复后 `53 passed, 0 failed`；后续又补 2 个锁测试，通过两个 pytest 包装器验证 35+20 项。
4. 精确 CLI 和原始登录模型两个纯用例：修复前 `2 failed`；修复后连同 9 个规范化控制字符用例 `11 passed`。
5. 新增的限流/会话复查纯单元：`3 passed`。
6. 静态命令如下；初次未加 `-k`，意外包含 onboarding 文件内一个 API 用例，该用例在相同临时目录错误处失败，结果为 `666 passed, 1 error`；随后仅排除这个数据库用例，结果 `666 passed, 1 deselected, 2 warnings`。没有修改或跳过它的测试代码。

```powershell
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -q tests/test_account_ui_assets.py tests/test_forum_page_assets.py tests/test_intro_film_assets.py tests/test_onboarding_assets.py tests/test_review_feel_static.py tests/test_contrast_tokens.py tests/test_account_behaviour.py tests/test_sec_auth_behaviour.py -k 'not test_the_four_sample_records_are_accepted_by_the_real_api_and_removable'
```

7. `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -q tests/test_account_behaviour.py tests/test_sec_auth_behaviour.py`：`2 passed`，包装器确认 35+20 项 Node 用例且 `fail 0`。
8. `node --test tests/widget_behaviour.cjs tests/forgot_behaviour.cjs tests/account_behaviour.cjs tests/sec_auth_behaviour.cjs`：`70 passed, 0 failed`，覆盖关联的小部件、忘记密码及本轮行为。
9. 最终纯函数检查如下：`32 passed, 2 warnings`。

```powershell
.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider -q tests/test_sec_round2.py::test_a_shared_guard_counts_failures_before_hash_without_database tests/test_sec_round2.py::test_d_revoke_route_rechecks_session_before_writes_without_database tests/test_sec_round2.py::test_e_normalizer_rejects_controls_even_before_trimming tests/test_sec_round2.py::test_i_login_input_preserves_legacy_exact_name_before_lookup tests/test_admin_tool.py::test_normalize_username tests/test_admin_tool.py::test_normalize_username_rejects_empty_and_long_values tests/test_admin_tool.py::test_cli_read_only_check_dispatch_never_initializes_database tests/test_admin_tool.py::test_cli_lookup_ambiguity_with_stub_connection tests/test_admin_tool.py::test_cli_lookup_requires_exact_name_with_stub_connection tests/test_account_security.py::test_password_matches_rejects_malformed_stored_hash tests/test_account_security.py::test_password_matches_accepts_valid_hash_and_rejects_placeholder tests/test_account_security.py::test_new_password_length_boundaries_and_common_list_size
```

10. `.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider --collect-only -q tests/test_sec_round2.py tests/test_account_security.py tests/test_admin_tool.py tests/test_payments.py tests/test_import_notes.py tests/test_migrations.py tests/test_sample_world.py`：`302 tests collected`，仅验证收集，没有执行数据库/样本用例。
11. `node --check static/app.js`、`node --check static/account.js`：均 exit 0；用 Python `compile(Path(...).read_text(), filename, 'exec')` 检查全部 20 个修改/新增 Python 文件，全部通过，没有写字节码或临时脚本。
12. `git diff --check`：exit 0，仅 Git 的既有 LF/CRLF 提示；未发现空白错误。
13. 用 AST 对比 `d134e81:main.py`/`db.py` 与当前文件：14 个变化的既有 main.py 函数全部属于 SEC 认证/头像区域；论坛、复习、统计、后台指标函数及所有已发布迁移函数保持一致。

### 失败原样摘录

数据库首次失败及后续混合静态套件中的同类错误：

```text
root = WindowsPath('C:/Users/chenjin/AppData/Local/Temp/pytest-of-chenjin')
prefix = 'pytest-'

    def find_prefixed(root: Path, prefix: str) -> Iterator[os.DirEntry[str]]:
        """Find all elements in root that begin with the prefix, case-insensitive."""
        l_prefix = prefix.lower()
>       for x in os.scandir(root):
                 ^^^^^^^^^^^^^^^^
E       PermissionError: [WinError 5] 拒绝访问。: 'C:\\Users\\chenjin\\AppData\\Local\\Temp\\pytest-of-chenjin'

.venv\Lib\site-packages\_pytest\pathlib.py:175: PermissionError
ERROR tests/test_sec_round2.py::test_a_shared_failure_limit_checks_before_password_hash[email]
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!
2 warnings, 1 error in 2.97s
```

```text
ERROR tests/test_onboarding_assets.py::test_the_four_sample_records_are_accepted_by_the_real_api_and_removable
666 passed, 2 warnings, 1 error in 5.67s
```

前端首次复现：

```text
✖ guards: same user new session ignores a late delete 200 response (32.0925ms)
✖ guards: same user new session ignores a late delete 409 response (14.8228ms)
✖ email: failure arriving after switching accounts does not show the old error (34.0981ms)
ℹ tests 53
ℹ pass 50
ℹ fail 3
```

新增纯用例修复前：

```text
E       Failed: DID NOT RAISE ValueError
tests\test_admin_tool.py:203: Failed
E       AssertionError: assert 'legacy' == 'legacy\t'
tests\test_sec_round2.py:263: AssertionError
2 failed, 2 warnings in 2.45s
```

变异脚本初次准备失败（未改文件，随后修正字符串定位方式并执行成功）：

```text
Traceback (most recent call last):
  File "<stdin>", line 17, in <module>
AssertionError
```

只读定位中的 Windows 文件通配错误，随后改用 `rg ... tests -g '*.py'` 定位；不涉及数据库或文件修改：

```text
rg: tests/test_sec*: 文件名、目录名或卷标语法不正确。 (os error 123)
rg: tests/test_sample_world*: 文件名、目录名或卷标语法不正确。 (os error 123)
rg: tests/test*: IO error for operation on tests/test*: 文件名、目录名或卷标语法不正确。 (os error 123)
```

一次上下文补丁定位未命中，重新读取当前 login/change_password 后按实际上下文修正；失败的补丁没有写入文件：

```text
apply_patch verification failed: Failed to find expected lines in C:\dev\algorithm-notebook\main.py:
    with connect(write=True) as conn:
        set_session(conn, user["id"], response, sec_cleanup_expired=False)
```

## 变异检查

选定 A/C/D/E 四处；仅在 Python 进程内用原函数源码构造变异，finally 恢复原函数，没有临时修改仓库代码或写临时文件。

| 变异 | 测试结果 | 恢复后 |
| --- | --- | --- |
| A：把用户限流移到 `password_matches` 后面 | 超限仍算哈希，断言 `matches.call_count == 5` 实际 6，`1 failed`，变异被抓到。 | 通过 |
| D：移除 revoke 写锁内的 `sec_recheck_session` | 已撤销请求不再抛 401，`DID NOT RAISE HTTPException`；`1 failed, 1 passed`，变异被抓到。 | 通过 |
| E：类别检查只保留 Cc，漏掉 Cf | 6 个零宽/方向控制样本不再拒绝，`6 failed, 3 passed`，变异被抓到。 | 通过 |
| C：旧当前令牌未删除 | 需要真实 SQLite 结果，因临时目录权限已停止而未执行；对应数据库测试已包含旧 Cookie 401、新 Cookie 有效及其他用户完整快照。 | 待隔离副本 |

三处变异的 12 个选定用例恢复后：`12 passed`。A/D 单元模拟数据库边界，不替代事务级正反面验证。

## 风险最高的 3 处

1. **会话轮换及写锁复查**：优先在隔离副本跑 C/D 的交错撤销、换属主、旧 Cookie 401、新 Cookie 有效；数据库测试仍未运行，尤其要确认密码变化与令牌替换的同事务回滚。
2. **存量身份与旧站点管理员升级**：核对 `check-usernames` 输出和全角/精确账号各自登录；确认已有真实管理员字段不变。当前基线没有迁移 4 回填，旧站点不能依赖环境变量重建管理员，授权须运维确认 ID。
3. **注销后的财务及文件边界**：验证回调仍记录交易号/状态，退款仍入账但权益不变，导入写前拒绝，头像 unlink 失败不回滚注销且之后读头像 404；这些依赖真实数据库/文件行为，尚未跑通。

## 建议加入 README 的文字（未改 README）

建议替换目前“ADMIN_USERNAME 指定唯一管理员”的过时段落，并增加以下说明：

> 管理员身份由数据库 `users.is_admin` 保存，支持多名管理员，改名不会失去权限。公开注册永远创建普通用户，应用启动不按名字授予权限；`ADMIN_USERNAME` 仅作为保留名配置。用 `python admin_tool.py list` 和只读的 `check-usernames` 核对存量账号，再用 `grant --id <ID>` / `revoke --id <ID>` 明确管理。精确用户名存在规范化歧义时也必须按 ID 操作。当前已发布的迁移 4 不含按名字回填；已有管理员身份保持不变，没有管理员的旧站点需运维显式授权。
>
> 改密码、改邮箱和注销共享当前密码验证限流：用户 15 分钟内最多 5 次失败，IP 15 分钟内最多 20 次尝试，验证成功清用户失败计数；现有限流是进程内的，多进程不共享额度。改密码会轮换当前设备会话并退出其他设备；改邮箱会同时作废既有找回密码链接。有效重置链接配弱密码可以用同一链接重试。
>
> 注销保留脱敏用户行和财务/论坛记录，撤销全部会话；财务回调与退款继续记录，但已注销账号不再变更套餐权益。头像文件删除失败只记日志，接口依旧拒绝读取。被封禁用户不能自助注销，遵循封禁即时生效。
>
> 复习历史 `elapsed_days` 是距上次复习的天数，包含评分前间隔与实际逾期天数，并非只记录逾期天数。
>
> 自动化验证必须显式把 `DATABASE_PATH` 指向系统临时目录的新文件，禁止连接真实 `data/notebook.db`；默认临时目录权限失败时停止数据库测试，不用项目内临时/缓存目录绕过。

隔离副本中建议执行本文第 2 项加上 `tests/test_migrations.py tests/test_sample_world.py`，然后运行完整套件；本报告只记录已经实际取得的结果，不把收集成功或模拟测试当作数据库通过。
