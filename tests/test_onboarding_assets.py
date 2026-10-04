"""首次清单的静态契约、接线，以及示例数据能被真实后端接受。"""
from datetime import date
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
SCRIPT = (STATIC / "onboarding.js").read_text(encoding="utf-8")
STYLE = (STATIC / "onboarding.css").read_text(encoding="utf-8")
APP = (STATIC / "app.js").read_text(encoding="utf-8")


def script_sources():
    return re.findall(r'<script[^>]+src="([^"]+)"', INDEX)


def test_assets_are_versioned_and_loaded_before_app():
    assert INDEX.count('<link rel="stylesheet" href="/static/onboarding.css?v=1">') == 1
    sources = script_sources()
    assert sources.count("/static/onboarding.js?v=1") == 1
    app = [index for index, src in enumerate(sources) if src.startswith("/static/app.js")]
    assert len(app) == 1 and sources.index("/static/onboarding.js?v=1") < app[0]


def test_changed_assets_had_their_versions_bumped():
    # 基线：app.js=58、mastery.js=2、clusters.js=4；改过的都加一。
    assert "/static/app.js?v=59" in script_sources()
    assert "/static/mastery.js?v=3" in script_sources()
    assert "/static/clusters.js?v=5" in script_sources()


def test_overview_files_were_not_touched_by_this_task():
    assert "/static/overview.js?v=2" in script_sources()
    assert 'href="/static/overview.css?v=2"' in INDEX


@pytest.mark.parametrize("source,name", [(SCRIPT, "onboarding.js"), (STYLE, "onboarding.css")])
def test_no_hex_colours_important_or_inline_styles(source, name):
    code = re.sub(r"/\*[\s\S]*?\*/", "", source)
    assert not re.search(r"#[0-9a-fA-F]{3}(?:[0-9a-fA-F]{3})?\b(?![\w-])", code), name
    assert "!important" not in code
    assert not re.search(r"""\bstyle\s*=|setAttribute\(\s*["']style["']""", code)
    assert "backdrop-filter" not in code
    assert "innerHTML" not in code and "insertAdjacentHTML" not in code
    assert "confirm(" not in code, "二次确认必须是内联的，不能用 confirm()"


def test_stylesheet_only_animates_when_motion_is_allowed():
    code = re.sub(r"/\*[\s\S]*?\*/", "", STYLE)
    outside = re.sub(r"@media \(prefers-reduced-motion: no-preference\)\s*\{[\s\S]*?\n\}", "", code)
    assert "transition" not in outside and "animation" not in outside
    assert "prefers-reduced-motion: no-preference" in code


def test_touch_targets_and_narrow_screens():
    assert re.search(r"\.ob-go\s*\{[^}]*min-height:\s*40px", STYLE)
    assert re.search(r"\.ob-demo-button\s*\{[^}]*min-height:\s*40px", STYLE)
    assert re.search(r"\.ob-dismiss\s*\{[^}]*min-height:\s*40px", STYLE)
    assert re.search(r"overflow-wrap:\s*anywhere", STYLE)
    assert not re.search(r"(?<![-\w])width:\s*\d{3,}px", STYLE), "固定宽度会在 320px 屏上造成横向滚动"


def test_app_js_wiring_is_minimal_and_complete():
    assert 'new CustomEvent("app:home-rendered"' in APP
    assert APP.count("window.Onboarding?.reset(user)") == 1
    assert APP.count("window.Onboarding?.reset()") == 1
    assert "window.Onboarding?.configure({ api })" in APP
    # 登出路径里的 reset 在 signedOut() 内，登录路径里的在 enterApp() 内。
    signed_out = APP[APP.index("function signedOut()"):]
    assert signed_out.index("Onboarding?.reset()") < signed_out.index("\n}\n")
    enter_app = APP[APP.index("async function enterApp()"):]
    assert enter_app.index("Onboarding?.reset(user)") < enter_app.index("\n}\n")
    for kind in ("weakness",):
        assert f'emptyNext("{kind}"' in APP
    assert "emptyNext(view," in APP
    assert 'emptyNext("mastery"' in (STATIC / "mastery.js").read_text(encoding="utf-8")
    assert 'emptyNext("clusters"' in (STATIC / "clusters.js").read_text(encoding="utf-8")


def test_user_controlled_text_only_goes_through_text_content():
    assert "textContent" in SCRIPT
    assert not re.search(r"\.(?:outerHTML|innerText)\s*=", SCRIPT)
    assert "document.write" not in SCRIPT


def test_storage_keys_match_the_spec():
    for key in ("onboarding-weakness-seen", "onboarding-dismissed"):
        assert f'"{key}"' in SCRIPT


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setattr(main, "today_for", lambda user: date(2026, 10, 2))
    main.reset_rate_limits()
    with TestClient(main.app, headers={"X-CSRF-Protection": "1"}) as instance:
        yield instance


def register(client, username):
    response = client.post("/api/auth/register", json={
        "username": username, "password": "a-test-password-123", "accept_terms": True,
        "invite_code": "test-invite", "email": f"{username}@example.com", "timezone": "Asia/Shanghai",
    })
    assert response.status_code == 201


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_the_four_sample_records_are_accepted_by_the_real_api_and_removable(client):
    dumped = subprocess.run(
        ["node", "-e", """
          const { load } = require('./tests/js_harness.cjs');
          const env = load(['onboarding.js']);
          process.stdout.write(JSON.stringify(env.window.Onboarding.demos()));
        """],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60, check=True,
    )
    demos = json.loads(dumped.stdout)
    assert [demo["zone"] for demo in demos] == ["算法", "数据库", "高等数学", "概率统计"]
    register(client, "newbie")
    register_other_title = "我自己的题"
    own = client.post("/api/problems", json={
        "title": register_other_title, "zone": "算法", "language": "Python",
        "code": "print(1)", "thinking": "自己写的", "mistakes": ["自己的错因"],
    })
    assert own.status_code == 201
    problem_ids = []
    for demo in demos:
        payload = {key: demo[key] for key in ("title", "zone", "language", "code", "thinking", "mistakes")}
        response = client.post("/api/problems", json=payload)
        assert response.status_code == 201, (demo["title"], response.text)
        assert len(response.json()["mistake_ids"]) == 1
        problem_ids.append(response.json()["id"])

    items = client.get("/api/mistakes", params={"due_only": "false"}).json()["items"]
    assert sum(item["title"].startswith("【示例】") for item in items) == 4
    assert all(item["last_reviewed_at"] is None for item in items)
    for problem_id in problem_ids:
        assert client.delete(f"/api/problems/{problem_id}").status_code == 200
    left = client.get("/api/mistakes", params={"due_only": "false"}).json()["items"]
    assert [item["title"] for item in left] == [register_other_title]
