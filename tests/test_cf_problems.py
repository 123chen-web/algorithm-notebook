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


def fake_response(payload_bytes):
    stream = io.BytesIO(payload_bytes)

    class FakeResponse:
        def read(self, size=-1):
            return stream.read(size)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def opener(request, timeout=None):
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


def test_fetch_rejects_oversize_body(monkeypatch):
    calls = []

    class BigResponse:
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
