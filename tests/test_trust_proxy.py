import main


class FakeRequest:
    def __init__(self, headers, host="172.18.0.3"):
        self.headers = headers
        self.client = type("C", (), {"host": host})()


def test_client_ip_ignores_forwarded_header_by_default(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    request = FakeRequest({"X-Forwarded-For": "6.6.6.6"})
    assert main.client_ip(request) == "172.18.0.3"


def test_client_ip_uses_rightmost_forwarded_entry_behind_trusted_proxy(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "1")
    # 客户端自己伪造的项在左边，代理追加的真实地址在最右边。
    request = FakeRequest({"X-Forwarded-For": "6.6.6.6, 203.0.113.9"})
    assert main.client_ip(request) == "203.0.113.9"


def test_client_ip_falls_back_to_socket_without_header(monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert main.client_ip(FakeRequest({})) == "172.18.0.3"
