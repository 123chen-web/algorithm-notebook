"""静态文件长缓存和 HSTS：只在该有的地方出现。"""
import pytest
from fastapi.testclient import TestClient

import main


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "cache.db"))
    monkeypatch.delenv("TRUST_PROXY", raising=False)
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    with TestClient(main.app) as client:
        yield client


def test_versioned_static_files_are_cached_for_a_year(client):
    response = client.get("/static/notes-map.js?v=1")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_unversioned_static_files_keep_default_caching(client):
    response = client.get("/static/notes-map.js")
    assert response.status_code == 200
    assert "immutable" not in response.headers.get("cache-control", "")


def test_service_worker_and_index_are_never_long_cached(client):
    for path in ("/sw.js", "/sw.js?v=3", "/static/sw.js?v=3", "/", "/static/index.html?v=2"):
        response = client.get(path)
        assert "immutable" not in response.headers.get("cache-control", ""), path
    assert client.get("/").headers["cache-control"] == "no-store"


def test_missing_versioned_file_is_not_cached(client):
    response = client.get("/static/does-not-exist.js?v=1")
    assert response.status_code == 404
    assert "immutable" not in response.headers.get("cache-control", "")


def test_api_responses_stay_no_store(client):
    assert client.get("/api/me").headers["cache-control"] == "no-store"
    assert "immutable" not in client.get("/api/me?v=1").headers["cache-control"]


def test_hsts_only_on_https_requests(client):
    assert "strict-transport-security" not in client.get("/healthz").headers
    https = TestClient(main.app, base_url="https://testserver")
    assert https.get("/healthz").headers["strict-transport-security"] == "max-age=31536000"


def test_hsts_follows_the_proxy_header_when_trusted(client, monkeypatch):
    monkeypatch.setenv("TRUST_PROXY", "1")
    assert client.get("/healthz", headers={"X-Forwarded-Proto": "https"}).headers["strict-transport-security"] == "max-age=31536000"
    assert "strict-transport-security" not in client.get("/healthz", headers={"X-Forwarded-Proto": "http"}).headers
    monkeypatch.delenv("TRUST_PROXY")
    assert "strict-transport-security" not in client.get("/healthz", headers={"X-Forwarded-Proto": "https"}).headers


def test_hsts_has_no_subdomain_or_preload_commitment(client):
    https = TestClient(main.app, base_url="https://testserver")
    value = https.get("/healthz").headers["strict-transport-security"]
    assert "includeSubDomains" not in value and "preload" not in value
