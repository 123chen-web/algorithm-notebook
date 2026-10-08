# 支付配置离线检查

`deploy/bin/check_payments.py` 只用 Python 标准库，检查本站现有支付宝当面付、
微信 Native 配置的基本外形。它不会创建订单、发送支付请求、执行退款、发邮件、
查询 DNS 或联网；不导入 `payments / db / dotenv`，不初始化支付 SDK，
也不会寻找、读取或修改仓库 `.env`。输出只有固定字段名、通过与否和固定说明，
不显示商户号、地址、密钥或原始异常。

## 使用

默认只检查当前进程里已经提供的、所选渠道的固定支付环境变量：

```sh
python -B deploy/bin/check_payments.py --channel alipay
python -B deploy/bin/check_payments.py --channel wechat
python -B deploy/bin/check_payments.py --channel all
```

`all` 是默认值，要求两种渠道都完整。尚未向当前进程提供支付环境变量时会报告缺失，
不会自动加载 `.env` 或把缺失项当作正式配置。请用现有安全的运维方式提供环境，
不要把密钥直接写成命令行参数或贴进聊天记录。本轮没有读取真实支付配置。

也可明确指定一个**本地 `.json` 文件**：

```sh
python -B deploy/bin/check_payments.py --channel alipay --config-json /本地安全路径/payment-check.json
```

只读取指定文件，不合并当前进程环境。文件必须是 UTF-8 JSON（允许 UTF-8 BOM），
最多 64 KiB，顶层是对象，字段值全部是字符串。重复字段、未知字段、布尔值、
非标准数值或无效 Unicode 都会拒绝；不接受 `.env`、UNC 网络路径、符号链接或目录联接。
应使用本地文件系统，读取后自行保管或删除临时配置文件；工具不写文件或建立目录。

以下只有占位内容，**预期检查不通过**，不能直接用于真实收款：

```json
{
  "PAYMENTS_MOCK_ENABLED": "0",
  "ALIPAY_SANDBOX": "0",
  "ALIPAY_APP_ID": "YOUR_APP_ID",
  "ALIPAY_SELLER_ID": "YOUR_SELLER_ID",
  "ALIPAY_PRIVATE_KEY": "YOUR_PRIVATE_PEM",
  "ALIPAY_PUBLIC_KEY": "YOUR_ALIPAY_PUBLIC_PEM",
  "ALIPAY_NOTIFY_URL": "https://notebook.example.com/api/payments/alipay/callback"
}
```

## 检查范围

- 两种渠道都要求明确 `PAYMENTS_MOCK_ENABLED="0"`。
  支付宝还要求明确 `ALIPAY_SANDBOX="0"` 或 `"1"`，不把缺失值默认为正式。
  这只是环境标记格式检查，通过不表示正式收款资格已经确认。
  本站微信配置没有沙箱字段；检查器不会编造或启用微信沙箱。
- 支付宝必需字段：`ALIPAY_APP_ID / ALIPAY_SELLER_ID / ALIPAY_PRIVATE_KEY /
  ALIPAY_PUBLIC_KEY / ALIPAY_NOTIFY_URL / ALIPAY_SANDBOX`。
- 微信必需字段：`WECHAT_APP_ID / WECHAT_MCH_ID / WECHAT_CERT_SERIAL_NO /
  WECHAT_PRIVATE_KEY / WECHAT_PUBLIC_KEY / WECHAT_PUBLIC_KEY_ID /
  WECHAT_API_V3_KEY / WECHAT_NOTIFY_URL`。
- 商户标识检查非空、明显占位内容及 ASCII 字母数字下划线的基本外形；
  不验证标识属于真实商户。私钥、公钥只检查匹配的 PEM 首尾标记与 Base64 外形，
  支持真实换行或字面量 `\n`；不解析 RSA、不验证位数或密钥相互匹配。
  微信 APIv3 密钥只检查 32 个可见 ASCII 字符。
- 回调地址必须是 HTTPS，路径精确为 `/api/payments/alipay/callback` 或
  `/api/payments/wechat/callback`，不能带用户名、密码、查询或片段。
  拒绝回环、私网、文档示例 IP 和明显保留/示例域名。
  所有检查都是本地文本解析，不解析 DNS，不证明地址可达、证书有效或回调能通过验签。
- 通过 `importlib.metadata` 查看**当前 Python 环境**中 `python-alipay-sdk`、
  `wechatpayv3` 的安装元数据，不导入这些 SDK，也不自动安装任何依赖。
  元数据存在不证明包能正常导入或交易功能可用；应使用项目已有环境运行。
  JSON 还允许现有 `PAYMENTS_MOCK_SECRET` 字段，但正式检查不使用或输出该值。

全部离线检查通过退出 `0`；缺失、格式不合格或不能读取指定文件退出 `2`。
工具不会自动修复配置，也不能证明商户资质、签名匹配、公网回调或真实付款成功。

## 与锁定金额的关系

本站正式订单已经由服务端保存套餐价格快照，并验证支付回调金额。
要使用这条路径，仍需站长拥有对应商户账号、支付产品权限与正确的实际配置。
没有这些条件，离线检查通过也不能保证真实付款能够锁定金额。
个人通用收款码的 App 输入金额不能由网页锁定，仍须按
[手动收款流程](manual-payment.md) 核实实际到账；本工具不会替代核账。

本轮交付仅包含工具、文档与使用虚构数据的离线测试。
未读取真实 `.env`、未安装 SDK、未创建或退款真实订单、未联系任何支付平台。
