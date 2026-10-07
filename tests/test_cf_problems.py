"""cf_problems.py：Codeforces 题库抓取与缓存。

所有测试都用假 HTTP 响应注入，绝不发起真实网络请求：
urlopen 一旦被意外调用就直接失败。
"""
import io
import json
import os
import time
from pathlib import Path

import pytest

import cf_problems


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    # 保险：任何测试都不许碰真实网络。
    def no_network(*args, **kwargs):
        pytest.fail("测试里不允许真实网络请求")

    monkeypatch.setattr(cf_problems.urllib.request, "urlopen", no_network)
    # 跳过 2 秒限流等待。
    monkeypatch.setattr(cf_problems.time, "sleep", lambda seconds: None)
    return tmp_path


def fake_response(payload_bytes, status=200):
    stream = io.BytesIO(payload_bytes)

    class FakeResponse:
        def getcode(self):
            return status

        def read(self, size=-1):
            return stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(request, timeout=None):
        assert request.full_url == cf_problems.API_URL
        assert request.get_header("User-agent") == cf_problems.USER_AGENT
        assert timeout == cf_problems.TIMEOUT_SECONDS
        return FakeResponse()

    return opener


def api_payload(problems):
    return json.dumps(
        {"status": "OK", "result": {"problems": problems, "problemStatistics": []}}
    ).encode("utf-8")


def test_fetch_trims_to_the_five_kept_fields():
    problems = cf_problems.fetch_problems(
        opener=fake_response(
            api_payload([
                {
                    "contestId": 4, "index": "A", "name": "Watermelon",
                    "rating": 800, "tags": ["brute force", "math"],
                    "statement": "整段题面不应被保存",
                    "points": 500,
                },
            ])
        )
    )
    assert problems == [
        {"contestId": 4, "index": "A", "name": "Watermelon",
         "rating": 800, "tags": ["brute force", "math"]},
    ]


def test_fetch_rejects_non_ok_status():
    opener = fake_response(json.dumps({"status": "FAILED", "comment": "x"}).encode())
    with pytest.raises(ValueError, match="异常状态"):
        cf_problems.fetch_problems(opener=opener)


def test_fetch_rejects_http_status_other_than_200():
    with pytest.raises(ValueError, match="HTTP"):
        cf_problems.fetch_problems(opener=fake_response(api_payload([]), status=201))


def test_official_fetch_does_not_follow_redirects():
    request = cf_problems.urllib.request.Request(cf_problems.API_URL)
    with pytest.raises(ValueError, match="重定向"):
        cf_problems._NoRedirect().redirect_request(
            request, None, 302, "redirect", {}, "https://example.org/unofficial"
        )


def test_oversize_cache_is_rejected_before_parsing(tmp_path, monkeypatch):
    monkeypatch.setattr(cf_problems, "MAX_BODY_BYTES", 16)
    (tmp_path / "cf_problems.json").write_bytes(b" " * 17)
    assert cf_problems.load_cache() is None


def test_saving_cache_cannot_store_statement_fields(tmp_path):
    cf_problems.save_cache([{"contestId": 1, "index": "A", "statement": "private text"}])
    stored = json.loads((tmp_path / "cf_problems.json").read_text(encoding="utf-8"))
    assert stored["problems"] == [{"contestId": 1, "index": "A"}]


@pytest.mark.parametrize("payload", [None, [], {"problems": [None]},
    {"problems": [{"contestId": 4, "index": "A", "name": "x", "rating": "800", "tags": []}]},
    {"problems": [{"contestId": 4, "index": "../x", "name": "x", "rating": 800, "tags": []}]},
    {"problems": [], "fetched_at": float("nan")},
])
def test_malformed_cache_structures_are_rejected(tmp_path, payload):
    (tmp_path / "cf_problems.json").write_text(json.dumps(payload), encoding="utf-8")
    assert cf_problems.load_cache() is None
    assert cf_problems.cache_age_days() is None


@pytest.mark.parametrize("payload", [None, [], {"status": "OK", "result": None},
    {"status": "OK", "result": {"problems": [None]}},
])
def test_fetch_rejects_malformed_api_structures(payload):
    with pytest.raises(ValueError):
        cf_problems.fetch_problems(opener=fake_response(json.dumps(payload).encode()))


def test_save_failure_keeps_old_cache_and_removes_staging_file(tmp_path, monkeypatch):
    old = {"fetched_at": time.time(), "problems": []}
    target = tmp_path / "cf_problems.json"
    target.write_text(json.dumps(old), encoding="utf-8")

    def fail_replace(source, destination):
        raise OSError("replace failed")

    monkeypatch.setattr(cf_problems.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        cf_problems.save_cache([])
    assert json.loads(target.read_text(encoding="utf-8")) == old
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cf_problems.json", "cf_problems.lock"]


def test_concurrent_cache_writers_use_independent_staging_files(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    replace = cf_problems.os.replace
    sources = []

    def synchronized_replace(source, destination):
        sources.append(source)
        replace(source, destination)

    monkeypatch.setattr(cf_problems.os, "replace", synchronized_replace)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(cf_problems.save_cache, [{"contestId": contest, "index": "A"}])
                for contest in (1, 2)]
        for job in jobs:
            job.result(timeout=10)
    assert len(set(sources)) == 2
    assert cf_problems.load_cache()["problems"][0]["contestId"] in (1, 2)
    assert sorted(path.name for path in tmp_path.iterdir()) == ["cf_problems.json", "cf_problems.lock"]


def test_fetch_rejects_oversize_body(monkeypatch):
    calls = []

    class BigResponse:
        def getcode(self):
            return 200

        def read(self, size=-1):
            calls.append(1)
            return b"x" * (64 * 1024)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(request, timeout=None):
        return BigResponse()

    with pytest.raises(ValueError, match="超过.*字节上限"):
        cf_problems.fetch_problems(opener=opener)
    # 读到超限就停，不会把 15MB 全读完。
    assert len(calls) < 300


def test_save_and_load_cache_roundtrip(tmp_path):
    target = cf_problems.save_cache([{"contestId": 1, "index": "A"}])
    assert target == tmp_path / "cf_problems.json"
    assert not tmp_path.joinpath("cf_problems.json.tmp").exists(), "临时文件不应残留"
    payload = cf_problems.load_cache()
    assert payload["problems"] == [{"contestId": 1, "index": "A"}]
    assert payload["fetched_at"] <= time.time()


def test_load_cache_returns_none_when_missing_or_corrupt(tmp_path):
    assert cf_problems.load_cache() is None
    (tmp_path / "cf_problems.json").write_text("not json{{", encoding="utf-8")
    assert cf_problems.load_cache() is None
    (tmp_path / "cf_problems.json").write_text('{"fetched_at": 1}', encoding="utf-8")
    assert cf_problems.load_cache() is None


def test_refresh_keeps_old_cache_on_failure(tmp_path, monkeypatch):
    old = {"fetched_at": time.time() - 100, "problems": [{"contestId": 9, "index": "Z"}]}
    (tmp_path / "cf_problems.json").write_text(
        json.dumps(old), encoding="utf-8"
    )

    def boom(*args, **kwargs):
        raise ConnectionError("断网了")

    monkeypatch.setattr(cf_problems, "fetch_problems", boom)
    assert cf_problems.refresh() == 1
    assert json.loads((tmp_path / "cf_problems.json").read_text(encoding="utf-8")) == old


def test_refresh_writes_new_cache_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(
        cf_problems,
        "fetch_problems",
        lambda opener=None: [{"contestId": 4, "index": "A", "name": "n",
                              "rating": 800, "tags": []}],
    )
    assert cf_problems.refresh() == 0
    payload = cf_problems.load_cache()
    assert len(payload["problems"]) == 1


def test_refresh_reports_cache_write_failure(monkeypatch):
    monkeypatch.setattr(cf_problems, "fetch_problems", lambda: [])
    def fail_save(problems):
        raise OSError("write failed")
    monkeypatch.setattr(cf_problems, "save_cache", fail_save)
    assert cf_problems.refresh() == 1


def test_cache_age_days(tmp_path):
    assert cf_problems.cache_age_days() is None
    (tmp_path / "cf_problems.json").write_text(
        json.dumps({"fetched_at": time.time() - 15 * 86400, "problems": []}),
        encoding="utf-8",
    )
    assert cf_problems.cache_age_days() == pytest.approx(15, abs=0.01)


def test_cli_usage_errors():
    import subprocess, sys

    root = Path(cf_problems.__file__).resolve().parent
    result = subprocess.run(
        [sys.executable, "cf_problems.py"],
        cwd=root, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 2
    assert "refresh" in result.stderr
