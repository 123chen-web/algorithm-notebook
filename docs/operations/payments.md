
## 支付与本地联调

各通道当前状态——**线上实际在用的只有手动收款**，两个自动支付通道均未联调：

| 通道 | 状态 | 说明 |
| --- | --- | --- |
| 手动收款（个人收款码 + 站长确认） | ⚠️ 过渡方案 | 当前唯一在用的收款方式；2022-03-01 起"具有明显经营活动特征"的用户不得用个人收款码收经营款，建议升级为个人经营收款码/商户码 |
| 支付宝（当面付） | ⏳ 待商户号联调 | 代码已接好，需商户号开通后完成沙箱/实网联调 |
| 微信支付（Native） | ⏳ 待商户号小额联调 | 代码已接好；微信无沙箱，只能用真实商户号小额联调 |

自动支付 fail-closed：任一自动通道在无有效商户配置时，所有入口（下单/退款/回调）
直接返回 `503 {"detail": "支付通道未配置，请使用手动收款"}`，不会创建订单、不会调用支付 SDK。

本地 Mock 模式下 `alipay` 和 `wechat` 都由 `MockChannel` 模拟；返回的
`mock://` 二维码地址仅占位，不能扫码付款。默认关闭 Mock；在本地 `.env` 设置：

```dotenv
PAYMENTS_MOCK_ENABLED=1
PAYMENTS_MOCK_SECRET=填写独立随机密钥
```

可用 `python -c "import secrets; print(secrets.token_hex(32))"` 生成密钥。
密钥只用于服务端和本地联调脚本，不发送给浏览器。正式部署保持 Mock 关闭。

以下浏览器及 Mock 接口均需已有的 Session Cookie；POST 还需
`X-CSRF-Protection: 1`。真实支付宝、微信支付通知入口分别见对应小节。

| 接口 | 请求或返回 |
| --- | --- |
| `GET /api/plans` | `{"plans": [...]}`，只包含启用套餐 |
| `POST /api/orders` | 请求 `{"plan_id": 1, "channel": "alipay"}`；返回 `order` 和 `payment` |
| `GET /api/orders` | `{"orders": [...]}`，自己的全部订单，按创建时间倒序，包含 `plan_name` |
| `GET /api/orders/{order_id}` | `{"order": {...}}`，只能查询自己的订单 |
| `POST /api/orders/{order_id}/refund` | 无需正文；退自己的已支付订单，返回 `{"order": {...}}` |
| `POST /api/payments/mock/{channel}/callback` | 原始 JSON 正文加 `X-Mock-Signature`；只能处理自己的订单 |

套餐由管理员直接维护数据库；本阶段没有面向普通用户的套餐编辑接口。
常规周期或额度变化时新建套餐、停用旧套餐，保留原记录；本次默认套餐提额
提供下方的显式一次性更新工具。订单以落库时的价格
作为金额快照，不接受前端自行指定金额，也不会因套餐停用而拒绝已有订单
的有效支付回调。

Mock 回调正文包含以下字段，`amount_cents` 必须与订单金额相同：

```json
{
  "order_id": "下单接口返回的订单号",
  "channel": "alipay",
  "provider_trade_no": "本渠道内唯一的模拟交易号",
  "amount_cents": 990,
  "status": "paid"
}
```

用 `MockChannel(channel, secret).sign_callback(raw_body)` 得到签名头，提交时
必须使用签名时完全相同的原始字节。`verify_callback` 对签名、结构和金额
类型做校验，业务层再核对订单渠道、金额、交易号和归属。

支付回调允许 `pending → paid / failed / closed`；同一终态的相同回调幂等，
不允许通过支付回调在终态之间互相转换。自助退款单独允许 `paid → refunded`。
只有首次转为 `paid` 时更新用户订阅，订单与订阅在同一事务提交：当前
套餐未过期则从 `plan_expires_at` 顺延本次套餐的 `period_days`，允许
提前续费不浪费剩余时长；无套餐或已过期则从支付时刻重新起算，不倒扣
已经过去的时间。不同套餐之间切换按上述规则直接顺延或重新起算，V1
不做按剩余天数折算价格的处理。
渠道发起支付若报错，会返回包含 `order_id` 的 502；订单保留 `pending`，
先轮询该订单，避免渠道实际已受理但客户端重复下单。

### 自助退款

登录后在“我的套餐 → 我的订单”查看全部订单（包括停用套餐的历史订单），
对 `paid` 订单点击“申请退款”，二次确认后立即发起该订单的全额退款。
不需要人工审核，不支持部分退款，退款原因固定为“用户自助申请全额退款”。
退款接口沿用登录和 CSRF 校验：不存在或不属于自己的订单返回 404，
非 `paid` 状态（包括已退款）返回 409，渠道未配置返回 503。

渠道确认退款成功后，在同一个 SQLite 写事务中将订单改为 `refunded`、
写入 UTC `refunded_at`，并清空用户的 `plan_id` 和 `plan_expires_at`。
`paid_at` 保留作历史记录；AI 当天已经使用的次数不退回、不重置，
后续请求按免费额度计算。新库直接建列，旧库启动时按 `ORDER_COLUMN_MIGRATIONS`
检查并补上 `refunded_at`，重复初始化不会重复迁移。

**V1 的已知简化：退款会清空当前整体套餐，不追踪被退订单贡献的时长。**
例如先买 A 又续费 B，随后只退 A 的款，也会清空当前套餐的全部剩余时长，
B 订单仍保留已支付状态。退款金额取该订单保存的整数分金额，不按剩余天数折算，
不补偿退款当天已经使用的 AI 次数。这与 V1 续费价格不按剩余天数折算的规则一致。

支付宝退款使用现有 SDK 的同步 `api_alipay_trade_refund`，不新增退款回调。
每笔订单固定使用 `refund-{订单号}` 作为 `out_request_no`，并发请求和重试
都复用它，避免重复出款。金额用整数除法和余数转为两位小数字符串，避免浮点误差。
直接响应须核对业务成功码、订单号、支付宝交易号及全额退款金额；
`fund_change=N` 表示本次没有新增资金变化，可能是同号重试，不单独视为失败。

已核对本仓库 `.venv` 中 SDK 3.4.0 源码：同步验签后返回内层
`alipay_trade_refund_response` / `alipay_trade_fastpay_refund_query_response` 字典，
无需再次解包。业务字段定义对照支付宝官方 SDK：
[退款响应](https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/response/AlipayTradeRefundResponse.java)
中的 `refund_fee` 是累计成功退款金额字符串；
[查询响应](https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/response/AlipayTradeFastpayRefundQueryResponse.java)
中的 `refund_amount` 是本次请求金额字符串，`refund_status` 表示退款结果；
[退款请求](https://github.com/alipay/alipay-sdk-java-all/blob/master/v2/src/main/java/com/alipay/api/domain/AlipayTradeRefundModel.java)
明确同一个 `out_request_no` 的重试只退款一次。

若退款请求异常或响应不能确认成功，适配器再调用
`api_alipay_trade_fastpay_refund_query`，核对请求号、订单号、交易号、金额以及
`refund_status=REFUND_SUCCESS`。仍无法确认时返回含 `order_id` 的 502，
保留 `paid` 和套餐，不假定退款成功或失败。可等待至少 10 秒后重新申请同一笔退款；
支付宝查询结果可能有延迟，同号重试可以核实并补齐本地状态。
渠道已退款但本地提交失败时也通过相同方式恢复。
这一阶段没有后台自动对账任务：结果一直未知且用户没有重试时，
渠道与本地状态可能暂时不一致；普通订单查询只读本地状态。

渠道网络调用不占用 SQLite 写锁。本地用 `WHERE status = 'paid'` 和
`cursor.rowcount` 保证只成功迁移一次；并发请求中的后完成者返回 409，
不会再次清空用户之后新购买的套餐。订单变更与套餐收回要么一起提交，要么一起回滚。
Mock 退款仅同步模拟成功，不调用外部渠道，也不需要退款签名。

### 套餐额度

每日上限与套餐优先级统一见[AI 额度与计费](../../static/ai-billing.html)。

`GET /api/me` 会返回 `plan_name`、`plan_active`、`ai_daily_limit`、
`ai_daily_used`、`ai_daily_remaining` 等字段，“我的套餐”展示这些信息并在退款后刷新。

#### 创建套餐

V1 没有套餐管理后台，`plans` 表的行目前只能手动插入或者用独立脚本
`seed_plans.py` 创建：

默认套餐的价格、额度及倍数说明见[AI 额度与计费](../../static/ai-billing.html)。

```bash
.venv/Scripts/python.exe seed_plans.py           # 按名字幂等插入默认的两档套餐
.venv/Scripts/python.exe seed_plans.py --dry-run # 只打印会新增什么，不写入
```

普通 seed 不会覆盖已存在套餐。已上线数据库提额时，先备份、预览，再显式执行：

```bash
docker compose -f deploy/docker-compose.prod.yml exec -T app python seed_plans.py --update-limits
docker compose -f deploy/docker-compose.prod.yml exec -T app python seed_plans.py --update-limits --apply
```

只更新名字精确为「标准版」「进阶版」的 `ai_daily_limit` 至默认值（见[AI 额度与计费](../../static/ai-billing.html)）；同名
多行会逐行列出并更新，缺失套餐跳过。不改价格、周期、购买/启用开关，不新增
套餐，不动站长专属等其他套餐。默认预览不写库也不做结构迁移；正式更新在
同一个事务内完成，失败整体回滚，重复执行无额外变更。现有有效订阅也会按
新额度计算，已用次数保持不变。环境变量默认值见[AI 额度与计费](../../static/ai-billing.html)；修改配置后按部署流程重建 app 容器使环境变量生效。

默认插入的套餐 `purchasable = 0`：会出现在 `GET /api/plans` 和"我的套餐"页里，
但下单接口会拒绝（403），不会触发支付宝/微信真实扣款。确认价格无误后运行
`seed_plans.py --enable-purchase` 把它们打开（只改现有记录，不会新增套餐）。
`purchasable` 和 `is_active`（停用套餐，见上）是两个独立开关：前者控制能不能
下单，后者控制展示与下单一起关闭。

### 支付宝当面付

使用第三方 [python-alipay-sdk](https://github.com/fzlee/alipay) 3.4.0，
其 `api_alipay_trade_precreate` 支持生成二维码、请求 RSA2 签名和同步响应
验签，`verify` 支持异步通知验签；这里固定已核对的版本。
仅接入 RSA2 公钥模式，暂不接证书模式或支付主动查单补偿；
自助退款及退款结果查询见上节。微信支付见下节。

安装 `requirements.txt` 后，关闭 Mock，并按 [.env.example](../../.env.example)
填写 `ALIPAY_APP_ID`、`ALIPAY_PRIVATE_KEY`（应用私钥）、
`ALIPAY_PUBLIC_KEY`（支付宝公钥）、`ALIPAY_SELLER_ID`（收款商户 PID）和
`ALIPAY_NOTIFY_URL`。五项均必填；配置缺失或无效时下单返回 503。
密钥支持完整多行 PEM 或单引号包裹的单行 PEM（用字面量 `\n` 表示换行）。
`ALIPAY_SANDBOX=1` 使用支付宝沙箱，默认 `0` 使用正式网关；两套账户、
APPID 和密钥不能混用。申请应用、签约当面付和取密钥的位置见配置文件注释。

下单仍为 `POST /api/orders`，`channel` 填 `alipay`。服务端调用
`alipay.trade.precreate`，返回：

```json
{"provider": "alipay", "qr_code_url": "https://qr.alipay.com/...", "redirect_url": null}
```

这里的 `qr_code_url` 是需要编码成二维码的内容，不是二维码图片。
“我的套餐”展示支付链接和支付文本，并轮询自己的订单状态。

`ALIPAY_NOTIFY_URL` 指向公开的 `POST /api/payments/alipay/callback`，
无需 Session Cookie 或 CSRF 头；仅此 POST 路径豁免浏览器 CSRF 检查。
通知必须是 UTF-8 表单编码，适配器验 RSA2 签名、APPID、商户 PID，
业务层继续核对订单金额、渠道和交易号。事务提交后返回纯文本 `success`；
验签或业务处理失败返回非 2xx，不确认付款，以便支付宝重试。
开启 Mock 时该公开通知入口关闭，Mock 回调仍需登录。

`TRADE_SUCCESS` 和 `TRADE_FINISHED` 均视为 `paid`，重复通知不会再次续期；
`TRADE_CLOSED` 映射 `closed`。已付款订单收到全额退款导致的 CLOSED 通知时，
支付回调状态机会拒绝 `paid/refunded → closed`；退款与订阅撤销由上节同步流程完成。
等待付款和未知状态不作为支付成功处理。

本地测试用临时生成的两对 RSA 密钥模拟应用与支付宝，真实执行 SDK 验签；
预下单、退款与退款查询的网络请求被替换，不需要真实商户账号，也不访问支付宝服务器。

### 微信支付 Native 支付

使用第三方 [wechatpayv3](https://github.com/minibear2021/wechatpayv3) 2.0.4
（微信支付没有官方 Python SDK，这是社区维护、生态里最主流的 APIv3 实现，
这里固定已核对源码的版本）。只接入 Native 支付（扫码），不接 JSAPI、
H5、小程序支付，因此不需要微信 OAuth、不需要拿用户 openid、
前端不需要任何微信 JS SDK。

初始化使用**微信支付平台公钥模式**，不是默认的平台证书模式：不需要指定
`cert_dir`、SDK 不会向本地目录自动下载或轮换微信支付平台证书，纯本地
初始化，不产生任何进程外状态。已核对本仓库 `.venv` 中 SDK 2.0.4 源码：
公钥模式下 `WeChatPay.__init__` 不会调用证书下载逻辑，同步请求和回调验签
命中 `public_key_id` 时也都走本地公钥验签，不发起额外网络请求。

**微信支付 APIv3 没有沙箱环境**，不像支付宝那样能用沙箱账户联调；本地
真实联调只能用真实商户号做小额（如 0.01 元）交易，测试前请知悉。

安装 `requirements.txt` 后，关闭 Mock，并按 [.env.example](../../.env.example)
填写 `WECHAT_APP_ID`、`WECHAT_MCH_ID`、`WECHAT_CERT_SERIAL_NO`、
`WECHAT_PRIVATE_KEY`（商户 API 证书私钥）、`WECHAT_PUBLIC_KEY`
（微信支付平台公钥，不是商户自己的公钥）、`WECHAT_PUBLIC_KEY_ID`、
`WECHAT_API_V3_KEY` 和 `WECHAT_NOTIFY_URL`。八项均必填；配置缺失、
无效或密钥不足 2048 位时下单返回 503。密钥支持完整多行 PEM 或单引号
包裹的单行 PEM（用字面量 `\n` 表示换行）。各项去哪个后台菜单获取见
配置文件注释。

下单仍为 `POST /api/orders`，`channel` 填 `wechat`。服务端调用 Native
统一下单接口，返回：

```json
{"provider": "wechat", "qr_code_url": "weixin://wxpay/bizpayurl?pr=...", "redirect_url": null}
```

`qr_code_url` 同样是需要编码成二维码的内容，不是二维码图片，和支付宝
共用同一套前端展示与轮询逻辑。金额直接用整数分传给微信（`amount.total`
本身就是分），不像支付宝需要换算成带小数的元字符串。

`WECHAT_NOTIFY_URL` 指向公开的 `POST /api/payments/wechat/callback`，
无需 Session Cookie 或 CSRF 头；仅此 POST 路径豁免浏览器 CSRF 检查。
微信只在支付成功时推送这个通知（没有"关闭"事件的异步通知），适配器验
AEAD_AES_256_GCM 回调签名与解密、核对 APPID、商户号，业务层继续核对
订单金额、渠道和交易号。事务提交后按微信的约定返回 `{"code": "SUCCESS"}`；
验签或业务处理失败返回非 2xx，以便微信重试。这个响应格式和支付宝的纯
文本 `success`/`failure` 不同，两者互不通用。开启 Mock 时该公开通知
入口关闭，Mock 回调仍需登录。

微信支付退款使用同步的申请退款接口，不新增退款回调；同一订单固定使用
`refund-{订单号}` 作为 `out_refund_no`。退款可能异步处理（`PROCESSING`），
适配器在非 `SUCCESS` 时自动调用退款查询接口兜底确认，仍无法确认时返回
含 `order_id` 的 502，行为与支付宝退款一致（保留 `paid` 和套餐，不假定
退款成功或失败，可稍后重试）。

本地测试用临时生成的 RSA 密钥模拟商户与微信支付平台，独立实现
AEAD_AES_256_GCM 加密和 RSA 签名（不复用 SDK 自身的验签逻辑），真实执行
SDK 的解密和验签；预下单、退款与退款查询的网络请求被替换，不需要真实
商户账号，也不访问微信支付服务器。

### 手动收款与兑换码

没有商户号、接不了支付宝/微信支付时，可以用站长自己的个人收款码收款，
流程是半自动的：用户在套餐页扫码付款后登记"我已付款"（选择套餐、填写
付款备注），站长在收款 App 里核对到账后，在管理后台点"确认收款"，系统
自动按所选套餐开通。站长也可以直接批量生成兑换码发给用户，用户在套餐页
兑换开通。

收款二维码在管理后台"手动收款设置"里上传，文件存在 `PAY_QR_DIR`
（默认 `data/pay-qr`）；`backup.py` 的归档不含这个目录，备份策略里要
单独处理。完整流程、命令行（`admin_tool.py pending-claims` /
`confirm-claim` / `reject-claim` / `make-codes`）和对账建议见
[手动收款](../../docs/operations/manual-payment.md)。

