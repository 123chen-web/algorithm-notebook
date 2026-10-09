"""F1 本地链接解析：预填标题与来源，由用户补充题面，不联网。"""
import main

import re
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi import Depends
from fastapi import HTTPException
from pydantic import BaseModel, Field



router = APIRouter()

# 只允许这两个站点的题目链接；用户提供的 URL 只做解析，绝不直接请求
# 不抓取第三方题面，从源头避免 SSRF。
ALLOWED_HOSTS = ("leetcode.com", "codeforces.com")

_LEETCODE_SLUG_RE = re.compile(r"^/problems/([^/?#]+)/?$")
_CF_CONTEST_RE = re.compile(r"^/contest/(\d+)/problem/([A-Za-z]+\d*)/?$")
_CF_PROBLEMSET_RE = re.compile(r"^/problemset/problem/(\d+)/([A-Za-z]+\d*)/?$")



class FetchUrlInput(BaseModel):
    url: str = Field(min_length=1, max_length=2000)


def _allowed_host(hostname: str) -> bool:
    return hostname in ALLOWED_HOSTS or hostname.endswith(
        tuple("." + host for host in ALLOWED_HOSTS)
    )


def _parse_problem_url(url: str):
    """解析并校验题目链接，返回 (site, payload)；site 为 leetcode/codeforces。"""
    try:
        parsed = urlparse(url)
    except ValueError:
        raise HTTPException(422, "链接格式不正确") from None
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(422, "链接格式不正确")
    hostname = (parsed.hostname or "").lower()
    if not _allowed_host(hostname):
        raise HTTPException(422, "只支持 leetcode.com / codeforces.com 的题目链接")
    path = parsed.path or ""
    if hostname == "leetcode.com" or hostname.endswith(".leetcode.com"):
        match = _LEETCODE_SLUG_RE.match(path)
        if not match:
            raise HTTPException(422, "无法从该 LeetCode 链接解析出题目标识")
        return "leetcode", match.group(1)
    match = _CF_CONTEST_RE.match(path) or _CF_PROBLEMSET_RE.match(path)
    if not match:
        raise HTTPException(422, "无法从该 Codeforces 链接解析出题号")
    return "codeforces", (int(match.group(1)), match.group(2))


@router.post("/api/problems/fetch-from-url")
def fetch_from_url(data: FetchUrlInput, user=Depends(main.current_user)):
    """本地解析 LeetCode / Codeforces 链接，只做预填，不建题。"""
    url = (data.url or "").strip()
    if not url:
        raise HTTPException(422, "请提供题目链接")
    site, payload = _parse_problem_url(url)
    # 仅解析标识，不抓取第三方题面；用户手动补题目内容、难度和标签。
    title = payload.replace("-", " ") if site == "leetcode" else f"Codeforces {payload[0]}{payload[1]}"
    return {"title": title[:200], "description": "", "difficulty": "", "tags": [], "source_url": url}
