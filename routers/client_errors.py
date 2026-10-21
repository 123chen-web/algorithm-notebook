"""前端 JavaScript 错误上报（只写服务器日志，不建表）。

不要求登录（登录前的页面也会出错），所以靠三道闸保护日志不被灌满：
请求体 <= 4 KB、每个 IP 每小时 20 条、全部字段限长限字符。
日志只记清洗后的字段；绝不记请求头、Cookie、IP 或用户输入的内容。
"""
import json
import logging
import sys

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import ValidationError

from core import ratelimit, security
from models.client_errors import ClientErrorReport

router = APIRouter()

MAX_BODY_BYTES = 4096
HOURLY_LIMIT = 20

log = logging.getLogger("oy.client_error")
log.setLevel(logging.INFO)
if not log.handlers and not logging.getLogger().handlers:
    # uvicorn 只配置自己的 logger，根 logger 没有处理器，INFO 会被丢掉；这里单独接到 stderr。
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s %(name)s %(message)s"))
    log.addHandler(_handler)


def _user_id_or_none(request: Request):
    """有有效会话就取用户 id；任何失败都当作匿名，绝不抛出。"""
    try:
        return security.current_user(request)["id"]
    except Exception:
        return None


@router.post("/api/client-errors", status_code=204)
async def report_client_error(request: Request):
    if ratelimit.rate_limited(f"client-error:{ratelimit.client_ip(request)}", HOURLY_LIMIT, 3600):
        return Response(status_code=429)

    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise HTTPException(413, "请求内容太大")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, "请求内容太大")
    try:
        report = ClientErrorReport.model_validate(json.loads(bytes(body)))
    except (ValueError, ValidationError):
        # 不回显校验细节（里面会带上用户发来的原文）。
        raise HTTPException(422, "报告格式不正确")

    entry = report.model_dump(exclude_none=True)
    user_id = _user_id_or_none(request)
    if user_id is not None:
        entry["user"] = user_id
    log.info(json.dumps(entry, ensure_ascii=False, separators=(",", ":")))
    return Response(status_code=204)
