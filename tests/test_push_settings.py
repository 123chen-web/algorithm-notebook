"""微信提醒（user_push）接口测试。

覆盖：
- GET 默认值、PUT 保存 / 保持密钥 / 切换渠道、密钥格式校验、严格请求体；
- 登录、CSRF、体验账号（403）、封禁账号（401）；
- 密钥任何响应都不回原文（只回尾号 4 位），日志里也不出现；
- 测试消息的结果分类中文映射、每用户每小时 5 次限流；
- 注销账号清理 user_push。
所有网络调用都用假的 HTTP 客户端（替换 push_channels._default_post），不联网。
"""
import logging

import pytest
from fastapi.testclient import TestClient

import main
import push_channels
from db import connect
from test_app import client, register  # noqa: F401  (client 是 pytest fixture)

SC_KEY = "SCT" + "a1B2" * 10          # 合法 Server酱 SendKey，尾号 a1B2
PP_KEY = "abcdef0123456789" * 2       # 合法 PushPlus token，尾号 6789
BAD_SC_KEY = "has-空格-and-too-long" + "x" * 80
BAD_PP_KEY = "g" * 32                 # 32 位但含非十六进制字符

PUSH_URL = "/api/me/push"
TEST_URL = "/api/me/push/test"


def save(client, channel="serverchan", secret=SC_KEY, enabled=True):
    return client.put(
        PUSH_URL,
        json={"channel": channel, "secret": secret, "enabled": enabled},
    )


@pytest.fixture
def fake_post(monkeypatch):
    calls = []

    def make(status=200, text='{"code":0,"message":"success"}', raising=None):
        def post(url, data, timeout):
            calls.append({"url": url, "data": data, "timeout": timeout})
            if raising is not None:
                raise raising
            return status, text
        monkeypatch.setattr(push_channels, "_default_post", post)
        return calls

    # 默认就给一个成功的假客户端，避免漏配时真的联网。
    make()
    return make


def set_trial(username="alice", value=1):
    with connect(write=True) as conn:
        conn.execute(
            f"UPDATE users SET is_trial = {int(value)} WHERE username = ?",
            (username,),
        )


def set_banned(username="alice"):
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE users SET is_banned = 1 WHERE username = ?", (username,)
        )


def push_row(username="alice"):
    with connect() as conn:
        return conn.execute(
            "SELECT up.* FROM user_push up JOIN users u ON u.id = up.user_id "
            "WHERE u.username = ?",
            (username,),
        ).fetchone()


# ==================== 默认状态与权限 ====================

def test_get_default_when_not_configured(client):
    register(client)
    response = client.get(PUSH_URL)
    assert response.status_code == 200
    assert response.json() == {
        "channel": None,
        "enabled": False,
        "configured": False,
        "tail": "",
        "fail_count": 0,
    }


def test_requires_login(client):
    anon = TestClient(main.app, headers={"X-CSRF-Protection": "1"})
    assert anon.get(PUSH_URL).status_code == 401
    assert anon.put(PUSH_URL, json={}).status_code == 401
    assert anon.post(TEST_URL).status_code == 401


def test_put_requires_csrf_header(client):
    register(client)
    response = client.put(
        PUSH_URL,
        json={"channel": "serverchan", "secret": SC_KEY, "enabled": True},
        headers={"X-CSRF-Protection": ""},
    )
    assert response.status_code == 403


def test_trial_account_rejected(client):
    register(client)
    set_trial()
    assert client.get(PUSH_URL).status_code == 403
    assert save(client).status_code == 403
    assert client.post(TEST_URL).status_code == 403


def test_banned_account_rejected(client, fake_post):
    register(client)
    save(client)
    set_banned()
    assert client.get(PUSH_URL).status_code == 401
    assert save(client, secret=None).status_code == 401
    assert client.post(TEST_URL).status_code == 401


# ==================== 保存与校验 ====================

def test_put_then_get_reports_tail_only(client):
    register(client)
    response = save(client)
    assert response.status_code == 200
    body = response.json()
    assert body == {
        "channel": "serverchan",
        "enabled": True,
        "configured": True,
        "tail": "a1B2",
        "fail_count": 0,
    }
    # 任何响应字段里都不能出现密钥原文。
    assert SC_KEY not in response.text

    got = client.get(PUSH_URL)
    assert got.status_code == 200
    assert got.json()["tail"] == "a1B2"
    assert SC_KEY not in got.text

    row = push_row()
    assert row["secret"] == SC_KEY  # 服务端库里保存的是原文，仅用于发送
    assert row["enabled"] == 1
    assert row["fail_count"] == 0
    assert row["last_ok_at"] is None


def test_put_pushplus_channel_and_tail(client):
    register(client)
    response = save(client, channel="pushplus", secret=PP_KEY)
    assert response.status_code == 200
    assert response.json()["channel"] == "pushplus"
    assert response.json()["tail"] == "6789"
    assert PP_KEY not in response.text


@pytest.mark.parametrize(
    "channel,secret",
    [
        ("serverchan", "short"),
        ("serverchan", BAD_SC_KEY),
        ("pushplus", BAD_PP_KEY),
        ("pushplus", "z" * 32),
        ("serverchan", "中文密钥中文密钥abcd"),
    ],
    ids=["sc-too-short", "sc-bad-char-long", "pp-non-hex-g",
         "pp-non-hex-z", "sc-non-ascii"],
)
def test_invalid_secret_rejected_422(client, channel, secret):
    register(client)
    response = save(client, channel=channel, secret=secret)
    assert response.status_code == 422
    assert push_row() is None
    assert secret not in response.text  # 报错信息不能回显密钥


def test_unknown_channel_rejected_422(client):
    register(client)
    response = save(client, channel="wechat", secret=SC_KEY)
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [
        {"channel": "serverchan", "enabled": True},                    # 缺 secret
        {"channel": "serverchan", "secret": SC_KEY},                   # 缺 enabled
        {"secret": SC_KEY, "enabled": True},                           # 缺 channel
        {"channel": "serverchan", "secret": SC_KEY, "enabled": "yes"}, # enabled 非布尔
        {"channel": "serverchan", "secret": 123, "enabled": True},     # secret 非字符串
        {"channel": "serverchan", "secret": SC_KEY, "enabled": True, "extra": 1},
    ],
    ids=["missing-secret", "missing-enabled", "missing-channel",
         "enabled-not-bool", "secret-not-string", "extra-field"],
)
def test_bad_request_shape_rejected_422(client, payload):
    register(client)
    response = client.put(PUSH_URL, json=payload)
    assert response.status_code == 422


def test_first_save_without_secret_is_rejected(client):
    register(client)
    response = client.put(
        PUSH_URL, json={"channel": "serverchan", "secret": None, "enabled": True}
    )
    assert response.status_code == 422
    assert push_row() is None


def test_blank_secret_keeps_existing_secret(client):
    register(client)
    assert save(client).status_code == 200
    # 再次保存（只改开关），secret 传空字符串 / null 都表示保持不变。
    for blank in ("", None):
        response = client.put(
            PUSH_URL,
            json={"channel": "serverchan", "secret": blank, "enabled": False},
        )
        assert response.status_code == 200
        assert response.json()["configured"] is True
        assert response.json()["tail"] == "a1B2"
        assert response.json()["enabled"] is False
        assert push_row()["secret"] == SC_KEY


def test_switching_channel_requires_new_secret(client):
    register(client)
    assert save(client, channel="serverchan", secret=SC_KEY).status_code == 200
    # Server酱的 key 不符合 PushPlus 规则，切渠道却不带新密钥必须拒绝。
    response = client.put(
        PUSH_URL,
        json={"channel": "pushplus", "secret": None, "enabled": True},
    )
    assert response.status_code == 422
    row = push_row()
    assert row["channel"] == "serverchan"

    # 带上合法新密钥后切换成功，fail_count 清零。
    response = save(client, channel="pushplus", secret=PP_KEY)
    assert response.status_code == 200
    assert push_row()["channel"] == "pushplus"
    assert push_row()["secret"] == PP_KEY


def test_saving_new_secret_resets_fail_count(client, fake_post):
    register(client)
    save(client)
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE user_push SET fail_count = 4 WHERE user_id = "
            "(SELECT id FROM users WHERE username='alice')"
        )
    response = save(client, secret=SC_KEY)
    assert response.status_code == 200
    assert response.json()["fail_count"] == 0


# ==================== 测试消息 ====================

def test_test_message_requires_configuration(client, fake_post):
    register(client)
    response = client.post(TEST_URL)
    assert response.status_code == 400


@pytest.mark.parametrize(
    "setup,expected_word",
    [
        (dict(status=200, text='{"code":0,"message":"done"}'), "已发送"),
        (dict(status=200, text='{"code":40001,"message":"bad credentials, key invalid"}'),
         "认证"),
        (dict(status=200, text='{"code":1,"message":"接口调用太频繁"}'), "限流"),
        (dict(status=500, text="gateway error"), "服务商"),
        (dict(raising=OSError("unreachable https://sctapi.ftqq.com/x.send")), "网络"),
    ],
    ids=["ok", "auth-by-body", "rate-by-body", "provider-500", "network-exception"],
)
def test_test_message_maps_kinds_to_chinese(client, fake_post, setup, expected_word):
    register(client)
    save(client)
    fake_post(**setup)
    response = client.post(TEST_URL)
    assert response.status_code == 200
    body = response.json()
    assert expected_word in body["message"]
    assert SC_KEY not in response.text


def test_test_message_http_status_auth_and_rate_limit(client, fake_post):
    register(client)
    save(client)
    fake_post(status=401, text="unauthorized")
    body = client.post(TEST_URL).json()
    assert "认证" in body["message"]
    fake_post(status=429, text="too many requests")
    body = client.post(TEST_URL).json()
    assert "限流" in body["message"]


def test_test_message_rate_limited_to_five_per_hour(client, fake_post):
    register(client)
    save(client)
    for _ in range(5):
        assert client.post(TEST_URL).status_code == 200
    response = client.post(TEST_URL)
    assert response.status_code == 429
    # 限流提示不能泄露密钥。
    assert SC_KEY not in response.text


def test_test_message_does_not_change_fail_count(client, fake_post):
    register(client)
    save(client)
    fake_post(status=500, text="error")
    for _ in range(6):
        client.post(TEST_URL)  # 前 5 次返回 200，第 6 次被我们自己限流
    row = push_row()
    assert row["fail_count"] == 0
    assert row["enabled"] == 1
    assert row["last_ok_at"] is None


def test_secret_never_appears_in_logs(client, fake_post, caplog):
    register(client)
    save(client)
    # 服务商返回认证失败与网络异常两条路径都会写 warning 日志。
    fake_post(status=401, text="invalid key")
    client.post(TEST_URL)
    fake_post(raising=TimeoutError("timeout"))
    client.post(TEST_URL)
    with caplog.at_level(logging.WARNING, logger="push_channels"):
        fake_post(raising=OSError("boom"))
        client.post(TEST_URL)
    assert SC_KEY not in caplog.text


# ==================== 注销清理 ====================

def test_delete_account_removes_push_row(client, fake_post):
    register(client)
    save(client)
    assert push_row() is not None
    response = client.post(
        "/api/me/delete-account",
        json={"password": "a-test-password-123"},
    )
    assert response.status_code == 200
    assert push_row() is None
