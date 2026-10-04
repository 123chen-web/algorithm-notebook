"""安全的“微信提醒”推送客户端（Server酱 / PushPlus）。

- 仅使用标准库；
- 发送地址固定，绝不接受调用者传入的 URL；
- 任何返回值、异常信息、日志中都不包含 key 原文。
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TIMEOUT = 8
MAX_RESPONSE = 64 * 1024
TITLE_MAX = 64
BODY_MAX = 2000

# 固定发送地址，调用者无法覆盖
_URLS = {
    "serverchan": "https://sctapi.ftqq.com/{key}.send",
    "pushplus": "https://www.pushplus.plus/send",
}

_KEY_PATTERNS = {
    "serverchan": re.compile(r"[A-Za-z0-9]{8,64}\Z"),
    "pushplus": re.compile(r"[a-f0-9]{16,64}\Z"),
}

# 除换行符 (\n) 以外的控制字符（含 DEL 与 C1 控制区）
_CONTROL_RE = re.compile(r"[\x00-\x09\x0b-\x1f\x7f-\x9f​-‏‪-‮⁠⁦-⁩﻿]")

_AUTH_STATUS = {401, 403}
_RATE_LIMIT_STATUS = {429}

# 服务商返回 message 中的启发式关键字（统一转小写后匹配）
_RATE_HINTS = ("频繁", "rate", "limit", "too many")
_AUTH_HINTS = ("key", "token", "授权", "认证")


def _sanitize(text: str, limit: int) -> str:
    """去掉控制字符（换行除外），超长截断并在末尾加省略号。"""
    if not isinstance(text, str):
        raise TypeError("title/body must be str")
    cleaned = _CONTROL_RE.sub("", text)
    if len(cleaned) > limit:
        cleaned = cleaned[:limit] + "…"
    return cleaned


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _default_post(url: str, data: dict, timeout: float):
    """默认网络实现：urllib.request 发 POST，返回 (status_code, text)。"""
    payload = urllib.parse.urlencode(data).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    # 不跟随任何跳转（服务商地址是固定的，跳转到别处一律视为失败），响应体最多读 64 KB。
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_RESPONSE).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # 非 2xx 响应也读回 body，交给上层分类
        return exc.code, exc.read(MAX_RESPONSE).decode("utf-8", "replace")


def _build_payload(channel: str, key: str, title: str, body: str) -> dict:
    if channel == "serverchan":
        return {"title": title, "desp": body}
    return {"token": key, "title": title, "content": body}


def _classify(status: int, text: str, channel: str) -> str:
    """把 HTTP 状态与服务商响应体映射为 kind。"""
    if status in _AUTH_STATUS:
        return "auth"
    if status in _RATE_LIMIT_STATUS:
        return "rate_limited"
    if status != 200:
        return "provider"
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return "provider"
    if not isinstance(payload, dict):
        return "provider"
    ok_code = 0 if channel == "serverchan" else 200
    if payload.get("code") == ok_code:
        return "ok"
    message = str(payload.get("message") or payload.get("msg") or "").lower()
    if any(hint in message for hint in _RATE_HINTS):
        return "rate_limited"
    if any(hint in message for hint in _AUTH_HINTS):
        return "auth"
    return "provider"


def send(channel: str, key: str, title: str, body: str, post=None) -> dict:
    """发送一条推送，返回 {"ok": bool, "kind": str}。

    kind ∈ {"ok", "network", "auth", "rate_limited", "provider"}。
    非法 channel 或 key 抛 ValueError（信息中不含 key 内容）。
    """
    if channel not in _URLS:
        raise ValueError(f"unsupported channel: {channel!r}")
    if not isinstance(key, str) or _KEY_PATTERNS[channel].fullmatch(key) is None:
        raise ValueError(f"invalid key format for channel {channel!r}")

    safe_title = _sanitize(title, TITLE_MAX)
    safe_body = _sanitize(body, BODY_MAX)
    url = _URLS[channel].format(key=key)
    data = _build_payload(channel, key, safe_title, safe_body)
    poster = post if post is not None else _default_post

    try:
        status, text = poster(url, data, TIMEOUT)
    except Exception:
        # 吞掉一切网络/超时异常，且不把异常原文（可能含 URL）透传出去
        logger.warning("push to %s failed with an exception", channel)
        return {"ok": False, "kind": "network"}

    kind = _classify(status, text, channel)
    if kind != "ok":
        logger.warning("push to %s failed: http_status=%s kind=%s", channel, status, kind)
    return {"ok": kind == "ok", "kind": kind}
