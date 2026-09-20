"""发送邮件（密码找回、每日复习提醒）。

用标准库 smtplib，不引入新依赖。默认按 Gmail 的 SMTP 参数
（smtp.gmail.com:587 + STARTTLS + 应用专用密码）配置，但换成
其他标准 SMTP 服务商（包括 Resend 的 SMTP 接口）只需要改 .env
里的几个 SMTP_* 变量，这里的代码不用动。
"""
import os
import smtplib
from email.message import EmailMessage


def smtp_configured():
    return bool(os.getenv("SMTP_HOST", "").strip())


def send_email(to_address, subject, body):
    """同步发送一封纯文本邮件；失败时抛出异常，调用方决定怎么处理。"""
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        raise RuntimeError("SMTP 尚未配置（缺少 SMTP_HOST）")

    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    sender = os.getenv("SMTP_FROM", "").strip() or username

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = to_address
    message.set_content(body)

    with smtplib.SMTP(host, port, timeout=10) as client:
        client.starttls()
        if username:
            client.login(username, password)
        client.send_message(message)
