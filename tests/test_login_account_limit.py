"""按账号叠加的登录失败限流：分散 IP 撞同一账号会被拦住，且只统计失败、不泄露账号是否存在。"""
import main
from test_app import client, register  # noqa: F401

PASSWORD = "a-test-password-123"


def attempt(client, username, password):
    return client.post("/api/auth/login", json={"username": username, "password": password})


def test_repeated_failures_for_one_account_are_blocked_even_when_the_ip_limit_is_far_away(client, monkeypatch):
    register(client, "alice")
    client.post("/api/auth/logout")
    monkeypatch.setattr(main, "LOGIN_LIMIT", 1000)
    monkeypatch.setattr(main, "LOGIN_ACCOUNT_LIMIT", 3)
    main.reset_rate_limits()
    assert [attempt(client, "alice", "wrong-password-1").status_code for _ in range(3)] == [401, 401, 401]
    assert attempt(client, "alice", "wrong-password-1").status_code == 429
    # 命中后只返回 429，不改变账号状态；窗口过去之前连正确密码也会被挡，这是有意的取舍。
    assert attempt(client, "alice", PASSWORD).status_code == 429


def test_account_bucket_counts_only_failures(client, monkeypatch):
    register(client, "bob")
    client.post("/api/auth/logout")
    monkeypatch.setattr(main, "LOGIN_LIMIT", 1000)
    monkeypatch.setattr(main, "LOGIN_ACCOUNT_LIMIT", 2)
    main.reset_rate_limits()
    for _ in range(5):
        assert attempt(client, "bob", PASSWORD).status_code == 200
        client.post("/api/auth/logout")


def test_unknown_and_known_accounts_behave_the_same(client, monkeypatch):
    register(client, "carol")
    client.post("/api/auth/logout")
    monkeypatch.setattr(main, "LOGIN_LIMIT", 1000)
    monkeypatch.setattr(main, "LOGIN_ACCOUNT_LIMIT", 2)
    main.reset_rate_limits()
    for name in ("carol", "no-such-user"):
        codes = [attempt(client, name, "wrong-password-1").status_code for _ in range(3)]
        assert codes == [401, 401, 429], name


def test_one_accounts_failures_do_not_block_another(client, monkeypatch):
    register(client, "dave")
    client.post("/api/auth/logout")
    register(client, "erin")
    client.post("/api/auth/logout")
    monkeypatch.setattr(main, "LOGIN_LIMIT", 1000)
    monkeypatch.setattr(main, "LOGIN_ACCOUNT_LIMIT", 2)
    main.reset_rate_limits()
    for _ in range(2):
        attempt(client, "dave", "wrong-password-1")
    assert attempt(client, "dave", PASSWORD).status_code == 429
    assert attempt(client, "erin", PASSWORD).status_code == 200
