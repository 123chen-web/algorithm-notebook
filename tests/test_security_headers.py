import pytest
from fastapi.testclient import TestClient

import main


@pytest.mark.parametrize("path", ["/", "/api/zones"])
def test_csp_allows_local_photo_blobs_without_relaxing_other_sources(path, monkeypatch):
    # These routes are stateless; checking response headers needs no database.
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "bootstrap_admin", lambda: None)
    with TestClient(main.app) as client:
        response = client.get(path)

    assert response.status_code == 200
    policy = response.headers["Content-Security-Policy"]
    directives = {
        parts[0]: parts[1:]
        for directive in policy.split(";")
        if (parts := directive.split())
    }
    assert directives == {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "connect-src": ["'self'"],
        "img-src": ["'self'", "blob:"],
        "base-uri": ["'none'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
    }
    assert "img-src 'self' blob:" in policy
    for forbidden in ("unsafe-inline", "unsafe-eval", "data:", "*"):
        assert forbidden not in policy
