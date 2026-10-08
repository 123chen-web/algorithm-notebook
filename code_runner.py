"""Optional Judge0-compatible adapter. This application never executes source code."""
import base64
import binascii
import json
import os
import re
import threading
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

LANGUAGES = {"Python": 71, "C++": 54}
BODY_LIMIT = 65536
OUTPUT_LIMIT = 8000
_slots = threading.BoundedSemaphore(2)


class RunnerUnavailable(Exception):
    pass


class RunnerBusy(Exception):
    pass


def configuration():
    url = os.getenv("CODE_RUNNER_URL", "").strip().rstrip("/")
    token = os.getenv("CODE_RUNNER_TOKEN", "").strip()
    parsed = urlsplit(url)
    if (not token or len(token) > 4096 or any(c in token for c in "\r\n")
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or not parsed.hostname or parsed.scheme not in ("http", "https")
            or parsed.hostname.rstrip(".") == "judge0.com"
            or parsed.hostname.rstrip(".").endswith(".judge0.com")
            or (parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost", "::1"))):
        raise RunnerUnavailable("独立运行服务尚未配置或配置无效。")
    return url, token


def configured():
    try:
        configuration()
        return True
    except (RunnerUnavailable, ValueError):
        return False


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _request(url, token, data=None):
    request = Request(url, data=None if data is None else json.dumps(data).encode("utf-8"),
                      headers={"Content-Type": "application/json", "X-Auth-Token": token})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=5) as response:
            if response.status not in (200, 201):
                raise RunnerUnavailable()
            raw = response.read(BODY_LIMIT + 1)
            if len(raw) > BODY_LIMIT:
                raise RunnerUnavailable()
            result = json.loads(raw.decode("utf-8"))
            if not isinstance(result, dict):
                raise RunnerUnavailable()
            return result
    except Exception:
        # Provider errors can contain source, authentication headers, or private
        # server paths; never put their bodies into an HTTP response or a log.
        raise RunnerUnavailable("独立运行服务暂不可用，请稍后重试。") from None


def _output(value):
    if value is None:
        return "", False
    if not isinstance(value, str):
        raise RunnerUnavailable("运行服务返回了无效结果。")
    try:
        text = base64.b64decode(value, validate=True).decode("utf-8", errors="replace")
    except (ValueError, binascii.Error):
        raise RunnerUnavailable("运行服务返回了无效结果。") from None
    return text[:OUTPUT_LIMIT], len(text) > OUTPUT_LIMIT


def run(language, code, stdin):
    try:
        url, auth = configuration()
    except ValueError:
        raise RunnerUnavailable("独立运行服务配置无效。") from None
    try:
        source = base64.b64encode(code.encode("utf-8")).decode("ascii")
        input_text = base64.b64encode(stdin.encode("utf-8")).decode("ascii")
    except UnicodeError:
        raise RunnerUnavailable("代码和输入必须是有效的 UTF-8 文本。") from None
    if not _slots.acquire(blocking=False):
        raise RunnerBusy("运行任务较多，请稍后重试。")
    try:
        submission = _request(url + "/submissions?base64_encoded=true&wait=false", auth, {
            "source_code": source, "stdin": input_text,
            "language_id": LANGUAGES[language], "cpu_time_limit": 2, "wall_time_limit": 5,
            "memory_limit": 128000, "max_file_size": 64,
            "max_processes_and_or_threads": 16, "enable_network": False, "number_of_runs": 1,
        })
        task = submission.get("token")
        if not isinstance(task, str) or not re.fullmatch(r"[0-9a-fA-F-]{36}", task):
            raise RunnerUnavailable("运行服务返回了无效任务。")
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            result = _request(url + f"/submissions/{task}?base64_encoded=true&fields=status,stdout,stderr,compile_output", auth)
            status = result.get("status")
            status_id = status.get("id") if isinstance(status, dict) else None
            if type(status_id) is not int or status_id not in range(1, 15):
                raise RunnerUnavailable("运行服务返回了无效状态。")
            if status_id in (1, 2):
                time.sleep(0.2)
                continue
            if status_id == 13:
                raise RunnerUnavailable("独立运行服务暂不可用，请稍后重试。")
            output = {}
            truncated = False
            for field in ("stdout", "stderr", "compile_output"):
                output[field], clipped = _output(result.get(field))
                truncated |= clipped
            label = {3: "completed", 5: "time_limit", 6: "compile_error"}.get(status_id, "runtime_error")
            return {"status": label, **output, "truncated": truncated}
        raise RunnerUnavailable("运行任务等待超时，请稍后重试。")
    finally:
        _slots.release()
