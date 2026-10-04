"""找回密码邮件：接口不被 SMTP 拖慢、失败不外泄、mailer 的加密方式与错误分类、check_mail.py 自测。

全部用假对象，不联网、不发信。
"""
import asyncio
import json
import logging
import os
import smtplib
import socket
import ssl
import threading
from types import SimpleNamespace

import pytest

import check_mail
import mailer
import main
from db import connect
from test_app import client, register  # noqa: F401  (client 是 fixture)

SECRET_PASSWORD = "s3cret-pw-9X-zz"


@pytest.fixture(autouse=True)
def smtp_env(monkeypatch):
    """每个测试从干净的 SMTP 环境开始；load_dotenv 写入的变量也不会漏到别的测试。"""
    monkeypatch.setattr(os, "environ", os.environ.copy())
    for name in tuple(os.environ):
        if name.startswith("SMTP_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setenv("SMTP_USERNAME", "sender@example.test")
    monkeypatch.setenv("SMTP_PASSWORD", SECRET_PASSWORD)


def fake_module(real, **overrides):
    """复制一个模块的公开属性，换掉其中几个；这样不会改到全局的 smtplib / socket。"""
    namespace = SimpleNamespace(**{name: getattr(real, name) for name in dir(real)})
    for name, value in overrides.items():
        setattr(namespace, name, value)
    return namespace


class FakeSock:
    def version(self):
        return "TLSv1.3"


def make_smtplib(**errors):
    """假的 smtplib：记录每一步调用；errors 里指定某一步抛什么异常。"""
    calls = []

    def maybe_raise(step):
        if step in errors:
            raise errors[step]

    class FakeSMTP:
        kind = "SMTP"

        def __init__(self, host, port=0, timeout=None, context=None):
            self.sock = FakeSock()
            calls.append((self.kind, host, port, timeout, context))
            maybe_raise("connect")

        def starttls(self, context=None):
            calls.append(("starttls", context))
            maybe_raise("starttls")

        def login(self, username, password):
            calls.append(("login", username, password))
            maybe_raise("login")

        def send_message(self, message):
            calls.append(("send", message["To"], message["From"], message["Subject"]))
            maybe_raise("send")
            return {}

        def quit(self):
            calls.append(("quit",))
            maybe_raise("quit")

        def close(self):
            calls.append(("close",))

    class FakeSMTPSSL(FakeSMTP):
        kind = "SMTP_SSL"

    return fake_module(smtplib, SMTP=FakeSMTP, SMTP_SSL=FakeSMTPSSL), calls


# ---------------------------------------------------------------- 接口：不阻塞、不泄露


def post_forgot_via_asgi(email, on_response_sent):
    """直接调用 ASGI 应用：在响应的最后一段发出的那一刻回调，之后继续等后台任务跑完。

    TestClient 会等后台任务结束才把响应交给测试，看不出"响应有没有先发出"，所以这里自己驱动。
    """
    body = json.dumps({"email": email}).encode()
    sent_messages = []
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": "POST", "scheme": "http", "path": "/api/auth/forgot-password",
        "raw_path": b"/api/auth/forgot-password", "query_string": b"", "root_path": "",
        "headers": [
            (b"host", b"testserver"), (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()), (b"x-csrf-protection", b"1"),
        ],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }
    state = {"delivered": False}

    async def receive():
        if state["delivered"]:
            await asyncio.sleep(3600)
        state["delivered"] = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        sent_messages.append(message)
        if message["type"] == "http.response.body" and not message.get("more_body"):
            on_response_sent()

    asyncio.run(main.app(scope, receive, send))
    start = next(m for m in sent_messages if m["type"] == "http.response.start")
    payload = b"".join(m.get("body", b"") for m in sent_messages if m["type"] == "http.response.body")
    return start["status"], payload


def test_forgot_response_does_not_wait_for_a_hanging_smtp(client, monkeypatch):
    register(client, "alice")
    release = threading.Event()
    state = {"finished": False, "entered": threading.Event()}

    def hanging_send_email(to, subject, body):
        state["entered"].set()
        release.wait(timeout=3)  # 假装 SMTP 卡住；若接口同步调用，响应会被拖到这里结束之后
        state["finished"] = True

    monkeypatch.setattr(mailer, "send_email", hanging_send_email)
    seen = {}

    def response_sent():
        seen["send_finished_when_response_went_out"] = state["finished"]
        release.set()

    status, payload = post_forgot_via_asgi("alice@example.com", response_sent)
    assert status == 200 and json.loads(payload) == {"ok": True}
    assert seen["send_finished_when_response_went_out"] is False
    # 后台任务确实被执行了，而不是被丢掉。
    assert state["entered"].is_set() and state["finished"] is True


def test_forgot_response_is_identical_whether_or_not_the_account_exists(client, monkeypatch):
    register(client, "alice")
    sent = []
    monkeypatch.setattr(mailer, "send_email", lambda to, subject, body: sent.append(to))
    known = client.post("/api/auth/forgot-password", json={"email": "alice@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email": "nobody@example.com"})
    assert (known.status_code, known.content) == (unknown.status_code, unknown.content)
    assert known.headers["content-length"] == unknown.headers["content-length"]
    assert known.json() == {"ok": True}
    assert sent == ["alice@example.com"]  # 只有存在的账号真的发了信


@pytest.mark.parametrize("error", [
    mailer.MailConnectError("无法连接 smtp.example.test:587（starttls）：timed out"),
    mailer.MailAuthError("登录失败"),
    RuntimeError("意料之外的错误"),
])
def test_forgot_send_failure_keeps_the_response_and_is_logged_without_secrets(
    client, monkeypatch, caplog, error
):
    register(client, "alice")
    bodies = []

    def failing_send_email(to, subject, body):
        bodies.append(body)
        raise error

    monkeypatch.setattr(mailer, "send_email", failing_send_email)
    with caplog.at_level(logging.ERROR, logger="algorithm_notebook"):
        response = client.post("/api/auth/forgot-password", json={"email": "alice@example.com"})
    assert response.status_code == 200 and response.json() == {"ok": True}
    assert str(error) not in response.text

    records = [r for r in caplog.records if "发送密码重置邮件失败" in r.getMessage()]
    assert len(records) == 1 and records[0].levelno == logging.ERROR
    assert records[0].exc_info is not None, "failure reason (traceback) is in the log"
    token = bodies[0].split("reset_token=")[1].split()[0]
    assert token and token not in caplog.text
    assert SECRET_PASSWORD not in caplog.text
    assert "alice@example.com" not in caplog.text, "recipient is masked"
    assert "a***@example.com" in caplog.text
    # token 已经落库：发信失败不回滚它。
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM password_resets").fetchone()[0] == 1


# ---------------------------------------------------------------- mailer：加密方式与参数


def send_with(monkeypatch, **errors):
    fake, calls = make_smtplib(**errors)
    monkeypatch.setattr(mailer, "smtplib", fake)
    return calls


def test_default_security_is_starttls_on_587_with_15_second_timeout(monkeypatch):
    calls = send_with(monkeypatch)
    mailer.send_email("to@example.test", "主题", "正文")
    assert calls[0][:4] == ("SMTP", "smtp.example.test", 587, 15.0)
    assert [c[0] for c in calls] == ["SMTP", "starttls", "login", "send", "quit", "close"]
    assert calls[2][1:] == ("sender@example.test", SECRET_PASSWORD)
    assert calls[3][1:] == ("to@example.test", "sender@example.test", "主题")


def test_ssl_security_uses_smtp_ssl_on_465_and_never_starttls(monkeypatch):
    monkeypatch.setenv("SMTP_SECURITY", "ssl")
    calls = send_with(monkeypatch)
    mailer.send_email("to@example.test", "s", "b")
    assert calls[0][:4] == ("SMTP_SSL", "smtp.example.test", 465, 15.0)
    assert "starttls" not in [c[0] for c in calls]
    assert "login" in [c[0] for c in calls]


def test_none_security_uses_plain_smtp_without_starttls(monkeypatch):
    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setenv("SMTP_PORT", "25")
    calls = send_with(monkeypatch)
    mailer.send_email("to@example.test", "s", "b")
    assert calls[0][:4] == ("SMTP", "smtp.example.test", 25, 15.0)
    assert "starttls" not in [c[0] for c in calls]


def test_security_value_is_case_insensitive_and_unknown_value_is_rejected(monkeypatch):
    monkeypatch.setenv("SMTP_SECURITY", " SSL ")
    assert mailer.smtp_settings().security == "ssl"
    monkeypatch.setenv("SMTP_SECURITY", "tls")
    calls = send_with(monkeypatch)
    with pytest.raises(mailer.MailConfigError, match="SMTP_SECURITY"):
        mailer.send_email("to@example.test", "s", "b")
    assert calls == [], "nothing was connected with a bad configuration"


def test_explicit_port_and_timeout_are_used_and_certificates_are_verified(monkeypatch):
    monkeypatch.setenv("SMTP_SECURITY", "ssl")
    monkeypatch.setenv("SMTP_PORT", "2465")
    monkeypatch.setenv("SMTP_TIMEOUT", "4.5")
    calls = send_with(monkeypatch)
    mailer.send_email("to@example.test", "s", "b")
    kind, host, port, timeout, context = calls[0]
    assert (port, timeout) == (2465, 4.5)
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname is True


def test_starttls_gets_a_verifying_context(monkeypatch):
    calls = send_with(monkeypatch)
    mailer.send_email("to@example.test", "s", "b")
    context = next(c for c in calls if c[0] == "starttls")[1]
    assert context.verify_mode == ssl.CERT_REQUIRED


@pytest.mark.parametrize("name, value", [
    ("SMTP_PORT", "abc"), ("SMTP_PORT", "0"), ("SMTP_PORT", "70000"),
    ("SMTP_TIMEOUT", "abc"), ("SMTP_TIMEOUT", "0"), ("SMTP_TIMEOUT", "-3"),
])
def test_bad_numbers_are_config_errors(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(mailer.MailConfigError, match=name):
        mailer.smtp_settings()


def test_missing_host_is_a_config_error_and_blank_port_falls_back(monkeypatch):
    monkeypatch.setenv("SMTP_PORT", "")
    monkeypatch.setenv("SMTP_TIMEOUT", "")
    settings = mailer.smtp_settings()
    assert (settings.port, settings.timeout, settings.security) == (587, 15.0, "starttls")
    monkeypatch.setenv("SMTP_HOST", "  ")
    with pytest.raises(mailer.MailConfigError, match="SMTP_HOST"):
        mailer.send_email("to@example.test", "s", "b")
    assert mailer.smtp_configured() is False


def test_username_is_optional_and_from_defaults_to_username(monkeypatch):
    monkeypatch.delenv("SMTP_USERNAME")
    calls = send_with(monkeypatch)
    mailer.send_email("to@example.test", "s", "b")
    assert "login" not in [c[0] for c in calls]
    monkeypatch.setenv("SMTP_USERNAME", "login@example.test")
    monkeypatch.setenv("SMTP_FROM", "noreply@example.test")
    calls = send_with(monkeypatch)
    mailer.send_email("to@example.test", "s", "b")
    assert next(c for c in calls if c[0] == "send")[2] == "noreply@example.test"


# ---------------------------------------------------------------- mailer：错误分类


@pytest.mark.parametrize("step, error, expected, fragment", [
    ("connect", TimeoutError("timed out"), mailer.MailConnectError, "超时"),
    ("connect", ConnectionRefusedError("refused"), mailer.MailConnectError, "无法连接"),
    ("connect", socket.gaierror(-2, "Name or service not known"), mailer.MailConnectError, "无法连接"),
    ("connect", ssl.SSLError("wrong version number"), mailer.MailConnectError, "TLS 握手失败"),
    ("connect", smtplib.SMTPServerDisconnected("closed"), mailer.MailConnectError, "断开"),
    ("starttls", smtplib.SMTPNotSupportedError("STARTTLS extension not supported"),
     mailer.MailConnectError, "SMTP_SECURITY"),
    ("login", smtplib.SMTPAuthenticationError(535, b"bad credentials"), mailer.MailAuthError, "登录"),
    ("login", smtplib.SMTPNotSupportedError("SMTP AUTH extension not supported"),
     mailer.MailConnectError, "SMTP_SECURITY"),
    ("send", smtplib.SMTPRecipientsRefused({"to@example.test": (550, b"no such user")}),
     mailer.MailRejectedError, "拒收"),
    ("send", smtplib.SMTPSenderRefused(553, b"Mailbox name not allowed", "sender@example.test"),
     mailer.MailRejectedError, "拒收"),
    ("send", smtplib.SMTPDataError(554, b"spam"), mailer.MailRejectedError, "拒收"),
])
def test_failures_are_classified_and_never_contain_the_password(monkeypatch, step, error, expected, fragment):
    send_with(monkeypatch, **{step: error})
    with pytest.raises(expected) as caught:
        mailer.send_email("to@example.test", "s", "b")
    assert fragment in str(caught.value)
    assert "smtp.example.test:587" in str(caught.value)
    assert SECRET_PASSWORD not in str(caught.value)
    assert caught.value.__cause__ is error


def test_refused_recipient_error_does_not_carry_the_address(monkeypatch):
    send_with(monkeypatch, send=smtplib.SMTPRecipientsRefused({"to@example.test": (550, b"no such user")}))
    with pytest.raises(mailer.MailRejectedError) as caught:
        mailer.send_email("to@example.test", "s", "b")
    assert "550 no such user" in str(caught.value) and "to@example.test" not in str(caught.value)


def test_error_kinds_are_distinct():
    assert {c.kind for c in (
        mailer.MailConnectError, mailer.MailAuthError, mailer.MailRejectedError, mailer.MailConfigError
    )} == {"connect", "auth", "refused", "config"}


def test_a_failing_quit_after_a_successful_send_is_not_a_failure(monkeypatch):
    calls = send_with(monkeypatch, quit=smtplib.SMTPResponseException(-1, b"\x00\x00\x00"))
    mailer.send_email("to@example.test", "s", "b")  # 不抛异常：信已经发出，重试会造成重复邮件
    assert [c[0] for c in calls][-2:] == ["quit", "close"]


def test_connection_is_closed_when_sending_fails(monkeypatch):
    calls = send_with(monkeypatch, login=smtplib.SMTPAuthenticationError(535, b"no"))
    with pytest.raises(mailer.MailAuthError):
        mailer.send_email("to@example.test", "s", "b")
    assert calls[-1] == ("close",)


def test_starttls_failure_closes_the_half_open_connection(monkeypatch):
    calls = send_with(monkeypatch, starttls=ssl.SSLError("handshake"))
    with pytest.raises(mailer.MailConnectError):
        mailer.send_email("to@example.test", "s", "b")
    assert [c[0] for c in calls].count("close") >= 1


# ---------------------------------------------------------------- check_mail.py


def fake_socket(resolve=None, connect=None):
    """假的 socket：getaddrinfo / create_connection 默认成功，也可以指定抛什么。"""
    log = []

    class Conn:
        def close(self):
            log.append("tcp-close")

    def getaddrinfo(host, port, type=0):
        log.append("dns")
        if isinstance(resolve, Exception):
            raise resolve
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("203.0.113.7", port))]

    def create_connection(address, timeout=None):
        log.append("tcp")
        if isinstance(connect, Exception):
            raise connect
        return Conn()

    return fake_module(socket, getaddrinfo=getaddrinfo, create_connection=create_connection), log


def run_check(monkeypatch, capsys, tmp_path, argv=("--to", "me@example.test"),
              dns_error=None, tcp_error=None, **smtp_errors):
    fake_sock, socket_log = fake_socket(dns_error, tcp_error)
    fake_smtp, smtp_calls = make_smtplib(**smtp_errors)
    monkeypatch.setattr(check_mail, "socket", fake_sock)
    monkeypatch.setattr(check_mail, "smtplib", fake_smtp)
    code = check_mail.main([*argv, "--env-file", str(tmp_path / "missing.env")])
    return code, capsys.readouterr().out, socket_log, smtp_calls


def test_check_mail_success_runs_all_five_steps_timed(monkeypatch, capsys, tmp_path):
    code, out, socket_log, calls = run_check(monkeypatch, capsys, tmp_path)
    assert code == 0
    for step in ("[1/5] DNS 解析", "[2/5] TCP 连接", "[3/5] TLS 握手", "[4/5] 登录", "[5/5] 发送测试邮件"):
        line = next(l for l in out.splitlines() if l.startswith(step))
        assert "成功" in line and line.count("秒") == 1, line
    assert "203.0.113.7" in out and "TLSv1.3" in out and "全部通过" in out
    assert socket_log == ["dns", "tcp", "tcp-close"]
    assert ("send", "me@example.test", "sender@example.test", "欧叶OY：邮件配置自测") in calls
    assert SECRET_PASSWORD not in out
    assert "密码     已设置" in out


def test_check_mail_without_to_stops_after_login(monkeypatch, capsys, tmp_path):
    code, out, _, calls = run_check(monkeypatch, capsys, tmp_path, argv=())
    assert code == 0 and "跳过" in out and "到登录为止都正常" in out
    assert "login" in [c[0] for c in calls] and "send" not in [c[0] for c in calls]


@pytest.mark.parametrize("security, expected_kind, starttls", [
    ("ssl", "SMTP_SSL", False), ("starttls", "SMTP", True), ("none", "SMTP", False),
])
def test_check_mail_follows_smtp_security(monkeypatch, capsys, tmp_path, security, expected_kind, starttls):
    monkeypatch.setenv("SMTP_SECURITY", security)
    code, out, _, calls = run_check(monkeypatch, capsys, tmp_path)
    assert code == 0
    assert calls[0][0] == expected_kind
    assert ("starttls" in [c[0] for c in calls]) is starttls
    assert ("不加密" in out) is (security == "none")


def test_check_mail_dns_failure(monkeypatch, capsys, tmp_path):
    code, out, socket_log, calls = run_check(
        monkeypatch, capsys, tmp_path, dns_error=socket.gaierror(-2, "Name or service not known")
    )
    assert code == 1
    assert "[1/5] DNS 解析 ... 失败" in out and "无法解析域名" in out and "SMTP_HOST" in out
    assert "[2/5]" not in out and socket_log == ["dns"] and calls == []


def test_check_mail_tcp_timeout_points_to_domestic_providers(monkeypatch, capsys, tmp_path):
    code, out, _, calls = run_check(monkeypatch, capsys, tmp_path, tcp_error=TimeoutError("timed out"))
    assert code == 1
    assert "[2/5] TCP 连接 ... 失败" in out
    assert "连接超时：很可能是网络被拦截" in out and "smtp.qq.com" in out and "465" in out
    assert "[3/5]" not in out and calls == []


def test_check_mail_tcp_refused(monkeypatch, capsys, tmp_path):
    code, out, _, _ = run_check(monkeypatch, capsys, tmp_path, tcp_error=ConnectionRefusedError())
    assert code == 1 and "连接被拒绝" in out and "SMTP_SECURITY=ssl" in out


@pytest.mark.parametrize("error, fragment", [
    (ssl.SSLError("WRONG_VERSION_NUMBER"), "465 必须配 SMTP_SECURITY=ssl"),
    (ssl.SSLCertVerificationError("certificate verify failed"), "证书校验失败"),
    (smtplib.SMTPServerDisconnected("Connection unexpectedly closed"), "TLS 握手失败"),
    (TimeoutError("handshake timed out"), "握手超时"),
])
def test_check_mail_tls_failures(monkeypatch, capsys, tmp_path, error, fragment):
    code, out, _, calls = run_check(monkeypatch, capsys, tmp_path, connect=error)
    assert code == 1
    assert "[3/5] TLS 握手 ... 失败" in out and fragment in out
    assert "[4/5]" not in out
    assert "login" not in [c[0] for c in calls]


def test_check_mail_closes_the_half_open_client_when_starttls_fails(monkeypatch, capsys, tmp_path):
    code, out, _, calls = run_check(monkeypatch, capsys, tmp_path, starttls=ssl.SSLError("handshake"))
    assert code == 1 and "TLS 握手失败" in out
    assert calls[-1] == ("close",)


def test_check_mail_starttls_not_supported(monkeypatch, capsys, tmp_path):
    code, out, _, _ = run_check(
        monkeypatch, capsys, tmp_path, starttls=smtplib.SMTPNotSupportedError("STARTTLS extension not supported")
    )
    assert code == 1 and "服务器不支持 STARTTLS" in out and "SMTP_SECURITY=ssl" in out


def test_check_mail_login_failure_hints_at_the_auth_code_and_hides_the_password(monkeypatch, capsys, tmp_path):
    # 有的服务器会在报错里回显收到的内容：脚本不能因此把密码打到屏幕上。
    error = smtplib.SMTPAuthenticationError(535, f"Login fail for {SECRET_PASSWORD}".encode())
    code, out, _, calls = run_check(monkeypatch, capsys, tmp_path, login=error)
    assert code == 1
    assert "[4/5] 登录 ... 失败" in out and "授权码" in out and "应用专用密码" in out
    assert SECRET_PASSWORD not in out and "***" in out
    assert "send" not in [c[0] for c in calls]


@pytest.mark.parametrize("error", [
    smtplib.SMTPRecipientsRefused({"me@example.test": (550, b"no such user")}),
    smtplib.SMTPSenderRefused(553, b"Mailbox name not allowed", "sender@example.test"),
])
def test_check_mail_send_rejection(monkeypatch, capsys, tmp_path, error):
    code, out, _, _ = run_check(monkeypatch, capsys, tmp_path, send=error)
    assert code == 1
    assert "[5/5] 发送测试邮件 ... 失败" in out and "被服务器拒收" in out and "SMTP_FROM" in out


def test_check_mail_reports_bad_configuration_without_connecting(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("SMTP_SECURITY", "tls")
    code, out, socket_log, calls = run_check(monkeypatch, capsys, tmp_path)
    assert code == 1 and "配置有误" in out and "SMTP_SECURITY" in out
    assert socket_log == [] and calls == []


def test_check_mail_reads_the_env_file(monkeypatch, capsys, tmp_path):
    for name in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD"):
        monkeypatch.delenv(name)
    env_file = tmp_path / "test.env"
    env_file.write_text(
        "SMTP_HOST=smtp.qq.com\nSMTP_PORT=465\nSMTP_SECURITY=ssl\n"
        f"SMTP_USERNAME=sender@qq.com\nSMTP_PASSWORD={SECRET_PASSWORD}\n",
        encoding="utf-8",
    )
    fake_sock, _ = fake_socket()
    fake_smtp, calls = make_smtplib()
    monkeypatch.setattr(check_mail, "socket", fake_sock)
    monkeypatch.setattr(check_mail, "smtplib", fake_smtp)
    assert check_mail.main(["--to", "me@example.test", "--env-file", str(env_file)]) == 0
    out = capsys.readouterr().out
    assert calls[0][:3] == ("SMTP_SSL", "smtp.qq.com", 465)
    assert ("login", "sender@qq.com", SECRET_PASSWORD) in calls
    assert SECRET_PASSWORD not in out
