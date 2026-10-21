"""手机分享直录（Web Share Target）：清单声明、脚本接线、服务端仍返回 SPA，以及 Node 行为测试封装。"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from static_helpers import versioned_src


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"


def test_manifest_declares_a_get_share_target_inside_scope():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    target = manifest["share_target"]
    assert target["method"] == "GET"
    assert target["params"] == {"title": "title", "text": "text", "url": "url"}
    assert target["action"].startswith(manifest["scope"])
    # 原有字段不能丢
    for key in ("name", "short_name", "start_url", "scope", "display", "icons"):
        assert key in manifest


def test_share_target_script_is_versioned_and_runs_after_capture_before_app():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    scripts = re.findall(r'<script defer src="(/static/[^"]+)"', html)
    share = versioned_src("share-target.js")
    assert share in scripts
    assert scripts.index(versioned_src("capture.js")) < scripts.index(share) < scripts.index(versioned_src("app.js"))


@pytest.mark.parametrize("query", [
    "?title=Two%20Sum&url=https%3A%2F%2Fleetcode.cn%2Fproblems%2Ftwo-sum%2F",
    "?text=https%3A%2F%2Fleetcode.cn%2Fproblems%2Ftwo-sum%2F",
    "?url=javascript%3Aalert(1)",
])
def test_share_query_still_serves_the_spa(query, monkeypatch):
    monkeypatch.setattr(main, "init_db", lambda: None)
    with TestClient(main.app) as client:
        response = client.get("/" + query)
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "<title>" in response.text


@pytest.mark.skipif(shutil.which("node") is None, reason="需要 Node.js（PATH 里没有 node）")
def test_share_target_behaviour_in_a_fake_browser():
    result = subprocess.run(
        ["node", "--test", "tests/share_target_behaviour.cjs"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1000:]
    assert re.search(r"(?m)^(?:ℹ|#) fail 0$", result.stdout), result.stdout[-1500:]
    passed = re.search(r"(?m)^(?:ℹ|#) pass (\d+)$", result.stdout)
    assert passed and int(passed.group(1)) >= 10, result.stdout[-1500:]
