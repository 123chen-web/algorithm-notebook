"""push_channels 的 pytest 测试。一律注入假 post，绝不联网。"""

import pytest

from push_channels import BODY_MAX, TIMEOUT, TITLE_MAX, send

SC_KEY = "SCT" + "a1B2" * 10          # 合法 serverchan key（43 位）
PP_KEY = "abcdef0123456789" * 2       # 合法 pushplus key（32 位小写 hex）


def make_post(status=200, text='{"code": 0}'):
    """返回 (假 post, calls)；calls 记录每次调用参数。"""
    calls = []

    def post(url, data, timeout):
        calls.append({"url": url, "data": data, "timeout": timeout})
        return status, text

    return post, calls


def make_raising_post(exc):
    def post(url, data, timeout):
        raise exc

    return post


# ---------- 正常发送 ----------

def test_serverchan_ok():
    post, calls = make_post(text='{"code": 0, "message": "success"}')
    result = send("serverchan", SC_KEY, "标题", "正文", post=post)
    assert result == {"ok": True, "kind": "ok"}
    assert len(calls) == 1


def test_pushplus_ok():
    post, _ = make_post(text='{"code": 200, "msg": "请求成功"}')
    result = send("pushplus", PP_KEY, "标题", "正文", post=post)
    assert result == {"ok": True, "kind": "ok"}


def test_serverchan_url_fixed():
    post, calls = make_post()
    send("serverchan", SC_KEY, "t", "b", post=post)
    assert calls[0]["url"] == f"https://sctapi.ftqq.com/{SC_KEY}.send"


def test_pushplus_url_fixed():
    post, calls = make_post(text='{"code": 200}')
    send("pushplus", PP_KEY, "t", "b", post=post)
    assert calls[0]["url"] == "https://www.pushplus.plus/send"


def test_serverchan_payload_fields():
    post, calls = make_post()
    send("serverchan", SC_KEY, "hello", "world", post=post)
    assert calls[0]["data"] == {"title": "hello", "desp": "world"}


def test_pushplus_payload_fields():
    post, calls = make_post(text='{"code": 200}')
    send("pushplus", PP_KEY, "hello", "world", post=post)
    assert calls[0]["data"] == {"token": PP_KEY, "title": "hello", "content": "world"}


def test_timeout_is_8_seconds():
    post, calls = make_post()
    send("serverchan", SC_KEY, "t", "b", post=post)
    assert calls[0]["timeout"] == TIMEOUT == 8


# ---------- 非法 channel / key ----------

def test_invalid_channel_raises():
    post, _ = make_post()
    with pytest.raises(ValueError):
        send("telegram", SC_KEY, "t", "b", post=post)


def test_invalid_channel_message_has_no_key():
    post, _ = make_post()
    with pytest.raises(ValueError) as excinfo:
        send("telegram", SC_KEY, "t", "b", post=post)
    assert SC_KEY not in str(excinfo.value)


@pytest.mark.parametrize("bad_key", [
    "",
    "a" * 7,                # 太短
    "a" * 65,               # 太长
    "SCT" + "a" * 10 + "!", # 非法字符
    "SCT 12345678",         # 含空格
    "中文长度足够凑八字的密钥",
])
def test_serverchan_invalid_keys(bad_key):
    post, _ = make_post()
    with pytest.raises(ValueError):
        send("serverchan", bad_key, "t", "b", post=post)


@pytest.mark.parametrize("bad_key", [
    "",
    "abcdef012345678",          # 15 位，太短
    "ABCDEF0123456789",         # 大写不允许
    "g" * 16,                   # 非 hex
    "abcdef0123456789-xyz",     # 非法字符
])
def test_pushplus_invalid_keys(bad_key):
    post, _ = make_post()
    with pytest.raises(ValueError):
        send("pushplus", bad_key, "t", "b", post=post)


def test_invalid_key_message_has_no_key():
    bad_key = "SCT" + "a" * 10 + "!"
    post, _ = make_post()
    with pytest.raises(ValueError) as excinfo:
        send("serverchan", bad_key, "t", "b", post=post)
    assert bad_key not in str(excinfo.value)


def test_invalid_key_post_never_called():
    post, calls = make_post()
    with pytest.raises(ValueError):
        send("pushplus", "bad", "t", "b", post=post)
    assert calls == []


# ---------- 截断与控制字符 ----------

def test_title_truncated():
    post, calls = make_post()
    send("serverchan", SC_KEY, "x" * (TITLE_MAX + 10), "b", post=post)
    title = calls[0]["data"]["title"]
    assert title == "x" * TITLE_MAX + "…"


def test_body_truncated():
    post, calls = make_post()
    send("serverchan", SC_KEY, "t", "y" * (BODY_MAX + 100), post=post)
    body = calls[0]["data"]["desp"]
    assert body == "y" * BODY_MAX + "…"


def test_title_exactly_max_not_truncated():
    post, calls = make_post()
    send("serverchan", SC_KEY, "z" * TITLE_MAX, "b", post=post)
    assert calls[0]["data"]["title"] == "z" * TITLE_MAX


def test_control_chars_removed_newline_kept():
    post, calls = make_post()
    send("serverchan", SC_KEY, "t", "a\x00b\rc\td\x07e\nf\x9fg", post=post)
    assert calls[0]["data"]["desp"] == "abcde\nfg"


def test_title_control_chars_removed():
    post, calls = make_post()
    send("serverchan", SC_KEY, "ti\x1ftle\x7f", "b", post=post)
    assert calls[0]["data"]["title"] == "title"


# ---------- 失败分类 ----------

def test_exception_becomes_network():
    result = send("serverchan", SC_KEY, "t", "b",
                  post=make_raising_post(RuntimeError("boom")))
    assert result == {"ok": False, "kind": "network"}


def test_timeout_exception_becomes_network():
    result = send("pushplus", PP_KEY, "t", "b",
                  post=make_raising_post(TimeoutError("timed out")))
    assert result == {"ok": False, "kind": "network"}


@pytest.mark.parametrize("status", [401, 403])
def test_http_auth_status(status):
    post, _ = make_post(status=status, text="forbidden")
    result = send("serverchan", SC_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "auth"}


def test_http_429_rate_limited():
    post, _ = make_post(status=429, text="too many requests")
    result = send("pushplus", PP_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "rate_limited"}


def test_http_500_provider():
    post, _ = make_post(status=500, text="server error")
    result = send("serverchan", SC_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "provider"}


def test_serverchan_error_code_is_auth():
    post, _ = make_post(text='{"code": 40001, "message": "key错误"}')
    result = send("serverchan", SC_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "auth"}


def test_pushplus_token_error_is_auth():
    post, _ = make_post(text='{"code": 600, "message": "token不正确"}')
    result = send("pushplus", PP_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "auth"}


def test_provider_rate_limit_message():
    post, _ = make_post(text='{"code": 42901, "message": "请求过于频繁，请稍后再试"}')
    result = send("serverchan", SC_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "rate_limited"}


def test_non_json_body_is_provider():
    post, _ = make_post(text="<html>gateway</html>")
    result = send("serverchan", SC_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "provider"}


def test_unknown_error_code_is_provider():
    post, _ = make_post(text='{"code": 12345, "message": "something else"}')
    result = send("pushplus", PP_KEY, "t", "b", post=post)
    assert result == {"ok": False, "kind": "provider"}


# ---------- key 不泄露 ----------

@pytest.mark.parametrize("status,text", [
    (200, '{"code": 0}'),
    (500, "boom"),
    (429, '{"code": 42901, "message": "请求频繁"}'),
    (401, "unauthorized"),
])
def test_result_never_contains_key(status, text):
    post, _ = make_post(status=status, text=text)
    result = send("serverchan", SC_KEY, "t", "b", post=post)
    assert SC_KEY not in str(result)


def test_network_result_never_contains_key():
    # 即使异常信息里碰巧带 key，也不能透传到返回值
    result = send("serverchan", SC_KEY, "t", "b",
                  post=make_raising_post(RuntimeError(f"url has {SC_KEY}")))
    assert SC_KEY not in str(result)


def test_bidi_and_zero_width_characters_are_removed():
    post, calls = make_post()
    send("serverchan", SC_KEY, "a‮b​c", "x⁦y﻿z", post=post)
    assert calls[0]["data"] == {"title": "abc", "desp": "xyz"}


def test_default_post_does_not_follow_redirects_and_reads_a_bounded_body(monkeypatch):
    import urllib.request
    from push_channels import MAX_RESPONSE, _NoRedirect, _default_post

    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, "http://evil.example/") is None
    seen = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, size=-1):
            seen["size"] = size
            return b"{}"

    class FakeOpener:
        def open(self, request, timeout=None):
            seen["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: FakeOpener())
    assert _default_post("https://sctapi.ftqq.com/x.send", {"a": "b"}, 8) == (200, "{}")
    assert seen == {"size": MAX_RESPONSE, "timeout": 8}
