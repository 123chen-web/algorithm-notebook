#!/usr/bin/env python3
"""Default: loopback-only fake HTTP protocol, no execution. --live: own sandbox only."""
import argparse
import base64
from contextlib import contextmanager
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
import threading
from urllib.parse import parse_qs, urlsplit
import uuid

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import code_runner

BODY_LIMIT = 65536
FIXED_LIMITS = {"cpu_time_limit": 2, "wall_time_limit": 5, "memory_limit": 128000,
                "max_file_size": 64, "max_processes_and_or_threads": 16,
                "enable_network": False, "number_of_runs": 1}
CASES = (
    {"name": "python_sum", "language": "Python", "code": "a, b = map(int, input().split())\nprint(a + b)",
     "stdin": "1 2\n", "id": 3, "status": "completed", "stdout": "3\n"},
    {"name": "cpp_sum", "language": "C++", "code": "#include <iostream>\nint main(){int a,b;std::cin>>a>>b;std::cout<<a+b<<'\\n';}",
     "stdin": "1 2\n", "id": 3, "status": "completed", "stdout": "3\n"},
    {"name": "compile_error", "language": "C++", "code": "int main( {", "stdin": "",
     "id": 6, "status": "compile_error", "compile_output": "MOCK: compilation error\n"},
    {"name": "time_limit", "language": "Python", "code": "while True:\n    pass", "stdin": "",
     "id": 5, "status": "time_limit"},
)


class FakeProtocol:
    """Accept only fixed fixtures and return constants; never evaluate or launch code."""
    def __init__(self, token):
        self.token = token
        self.tasks = {}

    def authorized(self, headers):
        value = headers.get("X-Auth-Token", "")
        return isinstance(value, str) and value.isascii() and hmac.compare_digest(value, self.token)

    def submit(self, headers, raw):
        if not self.authorized(headers):
            return 401, {"error": "mock authentication required"}
        if len(raw) > BODY_LIMIT:
            return 413, {"error": "mock request too large"}
        try:
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict) or set(data) != set(FIXED_LIMITS) | {"source_code", "stdin", "language_id"}:
                raise ValueError()
            if any(type(data[key]) is not type(value) or data[key] != value for key, value in FIXED_LIMITS.items()):
                raise ValueError()
            source = base64.b64decode(data["source_code"], validate=True).decode("utf-8")
            stdin = base64.b64decode(data["stdin"], validate=True).decode("utf-8")
            case = next((item for item in CASES if item["code"] == source and item["stdin"] == stdin
                         and code_runner.LANGUAGES[item["language"]] == data["language_id"]), None)
            if case is None or len(self.tasks) >= 20:
                raise ValueError()
        except (ValueError, TypeError, KeyError, UnicodeError):
            return 400, {"error": "mock request rejected"}
        task = str(uuid.uuid4())
        result = {"status": {"id": case["id"]}}
        for field in ("stdout", "stderr", "compile_output"):
            result[field] = base64.b64encode(case.get(field, "").encode("utf-8")).decode("ascii")
        self.tasks[task] = {"polls": 0, "result": result}
        return 201, {"token": task}

    def poll(self, headers, task):
        if not self.authorized(headers):
            return 401, {"error": "mock authentication required"}
        item = self.tasks.get(task)
        if item is None:
            return 404, {"error": "mock task missing"}
        item["polls"] += 1
        return 200, {"status": {"id": 1}} if item["polls"] == 1 else item["result"]


def handler_type(protocol):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, *_args):
            pass

        def reply(self, status, data):
            raw = json.dumps(data).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self):
            if self.path != "/submissions?base64_encoded=true&wait=false":
                self.reply(404, {"error": "mock route missing"})
                return
            try:
                length = int(self.headers.get("Content-Length", ""))
                if not 0 <= length <= BODY_LIMIT:
                    self.reply(413, {"error": "mock request too large"})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError()
            except (ValueError, OSError):
                self.reply(400, {"error": "mock request rejected"})
                return
            self.reply(*protocol.submit(self.headers, raw))

        def do_GET(self):
            parts = urlsplit(self.path)
            query = parse_qs(parts.query)
            if (not parts.path.startswith("/submissions/")
                    or query != {"base64_encoded": ["true"], "fields": ["status,stdout,stderr,compile_output"]}):
                self.reply(404, {"error": "mock route missing"})
                return
            self.reply(*protocol.poll(self.headers, parts.path[len("/submissions/"):]))
    return Handler


@contextmanager
def fake_service():
    token = secrets.token_urlsafe(32)
    server = HTTPServer(("127.0.0.1", 0), handler_type(FakeProtocol(token)))
    saved = {key: os.environ.get(key) for key in ("CODE_RUNNER_URL", "CODE_RUNNER_TOKEN")}
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    try:
        os.environ["CODE_RUNNER_URL"] = f"http://127.0.0.1:{server.server_port}"
        os.environ["CODE_RUNNER_TOKEN"] = token
        worker.start()
        yield
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def check_cases(cases):
    results = []
    for case in cases:
        try:
            result = code_runner.run(case["language"], case["code"], case["stdin"])
            passed = result["status"] == case["status"]
            if "stdout" in case:
                passed = passed and result["stdout"] == case["stdout"]
            if "compile_output" in case:
                passed = passed and result["compile_output"] == case["compile_output"]
        except Exception:
            # Never print provider errors, URLs, source code, or authentication tokens.
            passed = False
        results.append({"case": case["name"], "passed": passed})
    return results


def live_configuration_allowed():
    try:
        url, _token = code_runner.configuration()
        host = urlsplit(url).hostname
        return host != "judge0.com" and not host.endswith(".judge0.com")
    except (ValueError, code_runner.RunnerUnavailable):
        return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly execute two small examples on the configured owner-managed sandbox; never loads .env")
    parser.add_argument("--confirm-isolated-service", action="store_true", help="Confirm the service is your independent sandbox with no website data or keys")
    args = parser.parse_args(argv)
    if args.live:
        results = (check_cases(CASES[:2]) if args.confirm_isolated_service and live_configuration_allowed() else
                   [{"case": "owner_managed_configuration", "passed": False}])
        scope = "真实服务的两个最小运行样例；不证明沙箱隔离、负载、清理或漏洞状态。"
    else:
        with fake_service():
            results = check_cases(CASES)
        scope = "仅本机假 HTTP 提交、轮询与固定输出；没有执行 Python/C++ 源码。"
    passed = all(item["passed"] for item in results)
    print(json.dumps({"mode": "live" if args.live else "mock", "executed_source": None if args.live else False,
                      "execution_requested": args.live,
                      "passed": passed, "results": results, "scope": scope}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
