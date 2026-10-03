import main
from db import SCHEMA_VERSION, connect
from test_app import client


def test_healthz_without_login_or_headers(client):
    client.headers.clear()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": SCHEMA_VERSION}
    assert response.headers["Cache-Control"] == "no-store"
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM ai_usage").fetchone()[0] == 0


def test_healthz_hides_database_errors(client, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("private database path and failure details")

    monkeypatch.setattr(main, "connect", unavailable)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "error"}
    assert "private" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_healthz_checks_write_access(client, monkeypatch):
    original = main.connect
    calls = []

    def unavailable_for_write(write=False, **kwargs):
        calls.append(write)
        if write:
            raise RuntimeError("private write failure")
        return original(write=write, **kwargs)

    monkeypatch.setattr(main, "connect", unavailable_for_write)
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "error"}
    assert calls == [False, True]


def test_healthz_does_not_create_missing_database(client, tmp_path, monkeypatch):
    path = tmp_path / "missing" / "notebook.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "error"}
    assert not path.parent.exists()
