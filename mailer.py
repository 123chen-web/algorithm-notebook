"""发送邮件（密码找回、每日复习提醒）。

用标准库 smtplib，不引入新依赖。默认按 Gmail 的 SMTP 参数
（smtp.gmail.com:587 + STARTTLS + 应用专用密码）配置，但换成
其他标准 SMTP 服务商（QQ / 163 / Resend 的 SMTP 接口等）只需要改 .env
里的几个 SMTP_* 变量，这里的代码不用动：

- SMTP_SECURITY：starttls（默认，587）、ssl（465，直接 TLS）、none（不加密，
  只用于本机调试用的 SMTP 服务）。
- SMTP_PORT：不填时 ssl 用 465，其余用 587。
- SMTP_TIMEOUT：每一步网络操作（连接、握手、登录、发送）的超时秒数，默认 15。

失败时抛出 MailError 的子类，区分"连不上 / 认证失败 / 被拒收 / 配置有误"，
调用方记日志即可；异常信息里不会出现密码。
"""
import logging
import os
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

logger = logging.getLogger("algorithm_notebook")

SECURITY_MODES = ("starttls", "ssl", "none")
DEFAULT_TIMEOUT_SECONDS = 15.0


class MailError(Exception):
    """发信失败的基类；kind 是 connect / auth / refused / config 之一。"""

    kind = "other"


class MailConfigError(MailError, RuntimeError):
    kind = "config"


class MailConnectError(MailError):
    kind = "connect"


class MailAuthError(MailError):
    kind = "auth"


class MailRejectedError(MailError):
    kind = "refused"


@dataclass(frozen=True)
class SmtpSettings:
    host: str
    port: int
    security: str
    timeout: float
    username: str
    password: str
    sender: str


def smtp_configured():
    return bool(os.getenv("SMTP_HOST", "").strip())


def smtp_settings():
    """读取并校验 SMTP_* 环境变量；缺失或取值不合法时抛 MailConfigError。"""
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise MailConfigError("SMTP 尚未配置（缺少 SMTP_HOST）")

    security = (os.getenv("SMTP_SECURITY", "").strip() or "starttls").lower()
    if security not in SECURITY_MODES:
        raise MailConfigError(
            f"SMTP_SECURITY 只能是 {' / '.join(SECURITY_MODES)}，当前是 {security!r}"
        )

    raw_port = os.getenv("SMTP_PORT", "").strip()
    try:
        port = int(raw_port) if raw_port else (465 if security == "ssl" else 587)
    except ValueError:
        raise MailConfigError(f"SMTP_PORT 不是有效的端口号：{raw_port!r}") from None
    if not 0 < port < 65536:
        raise MailConfigError(f"SMTP_PORT 超出范围：{port}")

    raw_timeout = os.getenv("SMTP_TIMEOUT", "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_TIMEOUT_SECONDS
    except ValueError:
        raise MailConfigError(f"SMTP_TIMEOUT 不是有效的秒数：{raw_timeout!r}") from None
    if not timeout > 0:
        raise MailConfigError(f"SMTP_TIMEOUT 必须大于 0：{raw_timeout!r}")

    username = os.getenv("SMTP_USERNAME", "").strip()
    return SmtpSettings(
        host=host,
        port=port,
        security=security,
        timeout=timeout,
        username=username,
        password=os.getenv("SMTP_PASSWORD", "").strip(),
        sender=os.getenv("SMTP_FROM", "").strip() or username,
    )


def open_connection(settings):
    """按 SMTP_SECURITY 建立连接（连接、TLS 握手都在这里）。"""
    # 显式用带证书校验的默认上下文：smtplib 不传 context 时默认不校验证书。
    context = ssl.create_default_context()
    if settings.security == "ssl":
        return smtplib.SMTP_SSL(
            settings.host, settings.port, timeout=settings.timeout, context=context
        )
    client = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
    if settings.security == "starttls":
        try:
            client.starttls(context=context)
        except BaseException:
            client.close()
            raise
    return client


def _where(settings):
    return f"{settings.host}:{settings.port}（{settings.security}）"


def _classify(exc, phase, settings):
    """把 smtplib / socket / ssl 的各种异常归成四类，给出人能看懂的原因。"""
    where = _where(settings)
    # SMTPException 本身继承自 OSError，所以必须先判断 smtplib 的异常。
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return MailAuthError(f"登录 {where} 失败：账号或密码（授权码）不被接受：{exc}")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return MailConnectError(
            f"{where} 不支持所需的功能（{exc}）；检查 SMTP_SECURITY 与端口是否匹配"
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        # 它的 str() 带完整收件地址；日志里只留服务器返回的状态码和说明。
        replies = "; ".join(
            f"{code} {text.decode(errors='replace') if isinstance(text, bytes) else text}"
            for code, text in exc.recipients.values()
        )
        return MailRejectedError(f"{where} 拒收了收件人：{replies}")
    if isinstance(exc, (smtplib.SMTPSenderRefused,
                        smtplib.SMTPDataError, smtplib.SMTPHeloError)):
        return MailRejectedError(f"{where} 拒收了这封邮件：{exc}")
    if isinstance(exc, smtplib.SMTPConnectError):
        return MailConnectError(f"{where} 拒绝了连接：{exc}")
    if isinstance(exc, smtplib.SMTPServerDisconnected):
        return MailConnectError(f"{where} 在{_PHASE_LABELS[phase]}时断开了连接：{exc}")
    if isinstance(exc, smtplib.SMTPResponseException) and phase == "auth":
        return MailAuthError(f"登录 {where} 失败：{exc}")
    if isinstance(exc, smtplib.SMTPException):
        return MailRejectedError(f"{where} 在{_PHASE_LABELS[phase]}时返回错误：{exc}")
    if isinstance(exc, TimeoutError):
        return MailConnectError(f"连接 {where} 超时（{settings.timeout:g} 秒内没有响应）")
    if isinstance(exc, ssl.SSLError):
        return MailConnectError(f"与 {where} 的 TLS 握手失败：{exc}")
    if isinstance(exc, OSError):
        return MailConnectError(f"无法连接 {where}：{exc}")
    return exc


_PHASE_LABELS = {"connect": "建立连接", "auth": "登录", "send": "发送"}


def send_email(to_address, subject, body):
    """同步发送一封纯文本邮件；失败时抛出 MailError 子类，调用方决定怎么处理。

    这个函数会阻塞到发送结束，不要在请求线程里直接调用（见 main.py 的后台任务）。
    """
    settings = smtp_settings()

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = settings.sender
    message["To"] = to_address
    message.set_content(body)

    phase = "connect"
    client = None
    try:
        client = open_connection(settings)
        phase = "auth"
        if settings.username:
            client.login(settings.username, settings.password)
        phase = "send"
        client.send_message(message)
    except MailError:
        raise
    except Exception as exc:
        translated = _classify(exc, phase, settings)
        if translated is exc:
            raise
        raise translated from exc
    finally:
        if client is not None:
            # 信已经交给服务器：QUIT 的任何失败（QQ / 163 常见）都不能算发送失败，
            # 否则调用方会以为没发出去而重发。
            try:
                client.quit()
            except Exception:
                pass
            finally:
                client.close()
