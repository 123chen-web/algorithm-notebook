"""一键自测发信配置：读取 .env 里的 SMTP_* 设置，分步测试并计时。

    python check_mail.py --to 你的邮箱@example.com

步骤：DNS 解析 → TCP 连接 → TLS 握手 → 登录 → 发送测试邮件。
哪一步失败就停在哪一步，并用中文说明最可能的原因。不加 --to 时只测到登录，
不发信。全程不会打印密码（报错信息里偶然出现的密码也会被替换成 ***）。
"""
import argparse
import smtplib
import socket
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

from dotenv import load_dotenv

import mailer
from legal import PRODUCT_NAME

ROOT = Path(__file__).resolve().parent
TOTAL_STEPS = 5

CN_HINT = "国内建议改用 smtp.qq.com / smtp.163.com 的 465 端口（SMTP_SECURITY=ssl）"


class StepFailed(Exception):
    """一个自测步骤失败；reasons 是给用户看的中文原因列表。"""

    def __init__(self, detail, reasons):
        super().__init__(detail)
        self.detail = detail
        self.reasons = reasons


def _resolve(settings):
    try:
        infos = socket.getaddrinfo(settings.host, settings.port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise StepFailed(f"无法解析域名：{exc}", [
            f"SMTP_HOST 拼写错误（当前是 {settings.host}）",
            "本机网络或 DNS 不可用，或域名被拦截",
            CN_HINT,
        ]) from exc
    addresses = sorted({info[4][0] for info in infos})
    return f"{settings.host} → {', '.join(addresses[:3])}"


def _tcp(settings):
    try:
        sock = socket.create_connection((settings.host, settings.port), settings.timeout)
    except TimeoutError as exc:
        raise StepFailed(f"连接超时（{settings.timeout:g} 秒内没有响应）", [
            "连接超时：很可能是网络被拦截（防火墙 / 公司或校园网 / 国外邮箱在国内被限速）",
            "端口写错，或服务商没有开放这个端口",
            CN_HINT,
        ]) from exc
    except ConnectionRefusedError as exc:
        raise StepFailed("连接被拒绝", [
            f"{settings.host} 没有在 {settings.port} 端口提供 SMTP 服务",
            "端口与加密方式不对应：465 配 SMTP_SECURITY=ssl，587 配 starttls",
        ]) from exc
    except OSError as exc:
        raise StepFailed(f"连接失败：{exc}", [
            "本机没有网络，或出站连接被防火墙 / 代理拦截",
            CN_HINT,
        ]) from exc
    sock.close()
    return f"{settings.host}:{settings.port} 可以连通"


def _tls(settings):
    """返回 (已建立加密的 SMTP 客户端, 说明)。ssl 模式连接即握手；starttls 先问候再升级。"""
    context = ssl.create_default_context()
    client = None
    try:
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(
                settings.host, settings.port, timeout=settings.timeout, context=context
            )
        else:
            client = smtplib.SMTP(settings.host, settings.port, timeout=settings.timeout)
            if settings.security == "starttls":
                client.starttls(context=context)
    except ssl.SSLCertVerificationError as exc:
        _close(client)
        raise StepFailed(f"证书校验失败：{exc}", [
            "服务器证书不是受信任机构签发的，或域名与证书不符（SMTP_HOST 是否写成了 IP / 别名？）",
            "本机系统时间不对（证书会被判为过期）",
            "网络中间人 / 公司代理在替换证书",
        ]) from exc
    except (ssl.SSLError, smtplib.SMTPServerDisconnected) as exc:
        _close(client)
        raise StepFailed(f"TLS 握手失败：{exc}", [
            "端口与加密方式不匹配：465 必须配 SMTP_SECURITY=ssl，587 才配 starttls",
            "服务器在握手时就断开了连接，常见于网络拦截",
            CN_HINT,
        ]) from exc
    except smtplib.SMTPNotSupportedError as exc:
        _close(client)
        raise StepFailed(f"服务器不支持 STARTTLS：{exc}", [
            "这个端口不是 STARTTLS 端口；试试 SMTP_SECURITY=ssl 和 465 端口",
        ]) from exc
    except TimeoutError as exc:
        _close(client)
        raise StepFailed(f"握手超时（{settings.timeout:g} 秒）", [
            "网络很慢或被拦截：连得上端口但服务器没有应答",
            CN_HINT,
        ]) from exc
    except (smtplib.SMTPException, OSError) as exc:
        _close(client)
        raise StepFailed(f"握手失败：{exc}", [
            "服务器没有按 SMTP 协议应答，检查 SMTP_PORT 是否是 SMTP 端口",
            CN_HINT,
        ]) from exc
    if settings.security == "none":
        return client, "SMTP_SECURITY=none，不加密（只应该用于本机调试）"
    sock = client.sock
    version = sock.version() if hasattr(sock, "version") else "TLS"
    return client, f"{version} 加密通道已建立"


def _login(settings, client):
    if not settings.username:
        return "未设置 SMTP_USERNAME，跳过登录（服务器必须允许匿名发信）"
    try:
        client.login(settings.username, settings.password)
    except smtplib.SMTPAuthenticationError as exc:
        raise StepFailed(f"认证失败：{exc}", [
            "QQ / 163 邮箱必须用「授权码」，不能用邮箱登录密码；Gmail 要用「应用专用密码」",
            "SMTP_USERNAME 必须是完整的邮箱地址",
            "邮箱后台没有开启 SMTP 服务，或授权码已被重置",
        ]) from exc
    except smtplib.SMTPNotSupportedError as exc:
        raise StepFailed(f"服务器不支持登录：{exc}", [
            "服务器没有提供 AUTH：可能没有先完成加密（SMTP_SECURITY=none 时很多服务商会拒绝登录）",
        ]) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise StepFailed(f"登录出错：{exc}", [
            "服务器在登录中途断开或返回了异常应答，稍后重试；反复出现可能是登录频率被限制",
        ]) from exc
    return f"账号 {settings.username} 登录成功"


def _send(settings, client, to_address):
    message = EmailMessage()
    message["Subject"] = f"{PRODUCT_NAME}：邮件配置自测"
    message["From"] = settings.sender
    message["To"] = to_address
    message.set_content(
        f"这是一封由 check_mail.py 发出的测试邮件（{datetime.now():%Y-%m-%d %H:%M:%S}）。\n"
        "收到它说明发信配置可用，可以直接忽略。"
    )
    try:
        client.send_message(message)
    except (smtplib.SMTPRecipientsRefused, smtplib.SMTPSenderRefused,
            smtplib.SMTPDataError) as exc:
        raise StepFailed(f"被服务器拒收：{exc}", [
            f"发件人 SMTP_FROM（当前是 {settings.sender or '空'}）必须和登录邮箱一致",
            "收件地址不存在或拼写错误",
            "被判为垃圾邮件或触发了发信频率限制；稍等几分钟再试",
        ]) from exc
    except (smtplib.SMTPException, OSError) as exc:
        raise StepFailed(f"发送出错：{exc}", [
            "连接在发送途中断开，或服务器返回了异常应答；稍后重试",
        ]) from exc
    return f"已发往 {to_address}，请到收件箱和垃圾邮件箱查看"


def _close(client):
    if client is None:
        return
    try:
        client.close()
    except Exception:
        pass


def _scrub(text, secret):
    return text.replace(secret, "***") if secret else text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--to", help="收测试邮件的邮箱；不填则只测到登录，不发信")
    parser.add_argument(
        "--env-file", default=str(ROOT / ".env"),
        help="要读取的 .env 路径（默认项目根目录的 .env；已在系统环境里设置的变量优先）",
    )
    args = parser.parse_args(argv)

    load_dotenv(args.env_file)
    try:
        settings = mailer.smtp_settings()
    except mailer.MailConfigError as exc:
        print(f"配置有误：{exc}")
        print("请先在 .env 里填好 SMTP_HOST 等设置，示例见 docs/operations/mail.md。")
        return 1

    print("发信配置（不显示密码）：")
    print(f"  服务器   {settings.host}:{settings.port}")
    print(f"  加密方式 {settings.security}")
    print(f"  超时     {settings.timeout:g} 秒")
    print(f"  账号     {settings.username or '（未设置）'}")
    print(f"  密码     {'已设置' if settings.password else '未设置'}")
    print(f"  发件人   {settings.sender or '（未设置）'}")
    print()

    client = None
    context = {}

    def step(number, title, action):
        started = time.monotonic()
        print(f"[{number}/{TOTAL_STEPS}] {title} ...", end=" ", flush=True)
        try:
            outcome = action()
        except StepFailed as exc:
            elapsed = time.monotonic() - started
            print(f"失败（{elapsed:.2f} 秒）")
            print(f"    {_scrub(exc.detail, settings.password)}")
            print("    可能的原因：")
            for reason in exc.reasons:
                print(f"      - {reason}")
            return False
        elapsed = time.monotonic() - started
        print(f"成功（{elapsed:.2f} 秒）{_scrub(outcome, settings.password)}")
        return True

    def tls_action():
        context["client"], note = _tls(settings)
        return note

    try:
        if not step(1, "DNS 解析", lambda: _resolve(settings)):
            return 1
        if not step(2, "TCP 连接", lambda: _tcp(settings)):
            return 1
        if not step(3, "TLS 握手", tls_action):
            return 1
        client = context["client"]
        if not step(4, "登录", lambda: _login(settings, client)):
            return 1
        if args.to:
            if not step(5, "发送测试邮件", lambda: _send(settings, client, args.to)):
                return 1
        else:
            print(f"[5/{TOTAL_STEPS}] 发送测试邮件 ... 跳过（没有 --to；加上 --to 你的邮箱 才会真的发信）")
    finally:
        if client is not None:
            try:
                client.quit()
            except Exception:
                pass
            _close(client)

    print()
    print("全部通过。" if args.to else "到登录为止都正常。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
