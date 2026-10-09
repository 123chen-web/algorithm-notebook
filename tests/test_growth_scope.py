"""The reduced growth scope must leave no optional AI or battle surface."""
from pathlib import Path

import db
import main


def test_reduced_growth_schema_and_routes(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "scope.db"))
    db.init_db()
    with db.connect() as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert not {"explanations", "boss_sessions", "boss_rounds", "boss_graduations", "import_screenshot_daily"} & tables
    # 迁移 70/71/72 分别是笔记链接、附件、画板，与成长/boss 范围无关。
    assert [version for version, _, _ in db.MIGRATIONS if version >= 23 and version not in (40, 41, 50, 51, 52)] == [23, 24, 28, 70, 71, 72]
    paths = set(main.app.openapi()["paths"])
    assert "/api/problems/photo" in paths
    assert "/api/problems/fetch-from-url" in paths
    assert not any("/boss/" in path or path.endswith("/explain") or path.endswith("/parse-screenshot") for path in paths)


def test_reduced_growth_has_no_frontend_entries():
    root = Path(__file__).resolve().parents[1]
    page = (root / "static/index.html").read_text(encoding="utf-8")
    app = (root / "static/app.js").read_text(encoding="utf-8")
    for token in ("boss", "explain.js", "import-screenshot"):
        assert token not in page
    for token in ("window.Boss", "window.Explain", "parseScreenshot", "#import-screenshot"):
        assert token not in app
