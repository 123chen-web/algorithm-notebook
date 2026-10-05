from types import SimpleNamespace

import pytest

import main


def fake_request(scheme="http", forwarded=None):
    headers = {"X-Forwarded-Proto": forwarded} if forwarded else {}
    return SimpleNamespace(url=SimpleNamespace(scheme=scheme), headers=headers)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.delenv("TRUST_PROXY", raising=False)


def test_unset_follows_request_scheme():
    assert main.cookie_secure(fake_request("https")) is True
    assert main.cookie_secure(fake_request("http")) is False
    # 没设 TRUST_PROXY 时不信任转发头。
    assert main.cookie_secure(fake_request("http", "https")) is False


def test_trust_proxy_reads_forwarded_proto(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert main.cookie_secure(fake_request("http", "https")) is True
    assert main.cookie_secure(fake_request("http", "http")) is False
    assert main.cookie_secure(fake_request("https")) is True


@pytest.mark.parametrize("value,expected", [("0", False), ("1", True), ("", True)])
def test_explicit_value_wins(monkeypatch, value, expected):
    monkeypatch.setenv("COOKIE_SECURE", value)
    # 空值视为未设置，按请求判断（此处为 https）；0/1 按显式值。
    assert main.cookie_secure(fake_request("https")) is expected
    if value == "0":
        assert main.cookie_secure(fake_request("https")) is False
    if value == "1":
        assert main.cookie_secure(fake_request("http")) is True
