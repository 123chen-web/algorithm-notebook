"""Manual payment and one-time redemption contracts; all storage is isolated."""
import hashlib
import io
import re
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

import main
import payments
from db import connect


NOW = "2099-01-01T10:00:00+00:00"
BAD_CODE = "兑换码不正确、已使用或已过期"
ALPHABET = set("ABCDEFGHJKMNPQRSTUVWXYZ23456789")
CSRF = {"X-CSRF-Protection": "1"}
USER_NAMES = {1: "operator", 2: "alice", 3: "bob", 4: "trial", 5: "deleted", 6: "banned", 7: "carol"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Do not fall back to a repository directory if pytest's system temp fails.
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "redeem.db"))
    monkeypatch.setenv("PAY_QR_DIR", str(tmp_path / "pay-qr"))
    monkeypatch.setenv("AVATAR_DIR", str(tmp_path / "avatars"))
    monkeypatch.setenv("ADMIN_USERNAME", "")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
    monkeypatch.setattr(main, "utc_now", lambda: NOW)
    monkeypatch.setattr(payments, "utc_now", lambda: NOW)
    monkeypatch.setattr(main, "today_for", lambda user: date(2099, 1, 1))
    main.reset_rate_limits()
    with TestClient(main.app, headers=CSRF) as instance:
        with connect(write=True) as conn:
            for user_id, username in USER_NAMES.items():
                conn.execute(
                    "INSERT INTO users(id, username, password_hash, timezone, created_at, "
                    "is_admin, is_trial, deleted_at, is_banned) VALUES (?, ?, 'unused', "
                    "'Asia/Shanghai', ?, ?, ?, ?, ?)",
                    (user_id, username, NOW, int(user_id == 1), int(user_id == 4),
                     NOW if user_id == 5 else None, int(user_id == 6)),
                )
                conn.execute(
                    "INSERT INTO sessions(token_hash, user_id, expires_at) VALUES (?, ?, ?)",
                    (main.token_hash(f"redeem-session-{username}"), user_id, int(time.time()) + 3600),
                )
            conn.executemany(
                "INSERT INTO plans(id, name, period_days, ai_daily_limit, price_cents, "
                "is_active, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(1, "月套餐", 30, 20, 990, 1, NOW),
                 (2, "季套餐", 90, 50, 2490, 1, NOW),
                 (3, "已停用套餐", 60, 40, 1990, 0, NOW)],
            )
        use_user(instance, 1)
        yield instance
    main.reset_rate_limits()


def use_user(client, user_id):
    client.cookies.clear()
    client.cookies.set("session", f"redeem-session-{USER_NAMES[user_id]}")


def generate(client, **changes):
    response = client.post("/api/admin/redeem-codes", json={"plan_id": 1, "count": 1, **changes})
    assert response.status_code == 201, response.text
    return response.json()["codes"]


def code_hash(code):
    normalized = unicodedata.normalize("NFKC", code)
    normalized = "".join(char for char in normalized if not char.isspace() and char != "-").upper()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stored_code(code):
    with connect() as conn:
        return dict(conn.execute("SELECT * FROM redeem_codes WHERE code_hash = ?", (code_hash(code),)).fetchone())


def subscription(user_id=2):
    with connect() as conn:
        return dict(conn.execute("SELECT plan_id, plan_expires_at FROM users WHERE id = ?", (user_id,)).fetchone())


def image_bytes(fmt="JPEG", size=(400, 300), with_metadata=False):
    image = Image.new("RGB", size, (50, 100, 150))
    buffer = io.BytesIO()
    options = {}
    if with_metadata:
        info = PngImagePlugin.PngInfo()
        info.add_text("private-note", "must disappear when reencoded")
        options["pnginfo"] = info
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


def upload_qr(client, content=None, channel="alipay", filename="qr.jpg", content_type="image/jpeg"):
    return client.put(
        f"/api/admin/manual-payment/qr/{channel}",
        files={"file": (filename, image_bytes() if content is None else content, content_type)},
    )


@pytest.mark.parametrize("payload", [
    {"count": 0}, {"count": 51}, {"count": True}, {"count": "1"},
    {"days": 0}, {"days": 3651}, {"days": True},
    {"expires_in_days": 0}, {"expires_in_days": 366}, {"expires_in_days": True},
    {"note": "n" * 101}, {"plan_id": True},
])
def test_generate_rejects_bounds_and_wrong_types(client, payload):
    response = client.post("/api/admin/redeem-codes", json={"plan_id": 1, "count": 1, **payload})
    assert response.status_code == 422
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 0


@pytest.mark.parametrize("days,expiry_days", [(1, 1), (3650, 365)])
def test_generation_accepts_day_and_expiry_boundaries(client, days, expiry_days):
    code = generate(client, days=days, expires_in_days=expiry_days, note="补偿")[0]
    stored = stored_code(code)
    assert stored["period_days"] == days
    assert stored["expires_at"] == (datetime.fromisoformat(NOW) + timedelta(days=expiry_days)).isoformat(timespec="seconds")
    assert stored["created_by"] == 1
    assert stored["note"] == "补偿"


def test_generation_uses_plan_defaults_and_requires_existing_plan(client):
    code = generate(client, plan_id=2)[0]
    assert stored_code(code)["period_days"] == 90
    assert stored_code(code)["expires_at"] is None
    response = client.post("/api/admin/redeem-codes", json={"plan_id": 999, "count": 1})
    assert response.status_code == 404


def test_one_hundred_generated_codes_are_unique_safe_and_only_returned_once(client):
    codes = generate(client, count=50) + generate(client, count=50)
    assert len(codes) == len(set(codes)) == 100
    for code in codes:
        assert re.fullmatch(r"[A-Z2-9]{4}(?:-[A-Z2-9]{4}){3}", code)
        assert set(code.replace("-", "")) <= ALPHABET
    response = client.get("/api/admin/redeem-codes", params={"status": "all", "limit": 100})
    assert response.status_code == 200
    listed = response.json()["codes"]
    assert len(listed) == 100
    assert all(set(item) == {"id", "hint", "plan_name", "period_days", "note", "status", "created_at", "expires_at", "redeemed_by", "redeemed_at"} for item in listed)
    assert all(len(item["hint"]) == 4 for item in listed)
    with connect() as conn:
        records = [dict(row) for row in conn.execute("SELECT * FROM redeem_codes")]
    by_hash = {row["code_hash"]: row for row in records}
    assert set(by_hash) == {code_hash(code) for code in codes}
    for code in codes:
        assert by_hash[code_hash(code)]["code_hint"] == code[-4:]
        assert code not in str(records)
        assert code.replace("-", "") not in str(records)
        assert code not in response.text


@pytest.mark.parametrize("old_plan,old_expiry,new_plan,days,expected", [
    (None, None, 1, None, "2099-01-31T10:00:00+00:00"),
    (1, "2099-01-10T10:00:00+00:00", 1, None, "2099-02-09T10:00:00+00:00"),
    (1, "2098-12-25T10:00:00+00:00", 1, None, "2099-01-31T10:00:00+00:00"),
    (1, NOW, 1, None, "2099-01-31T10:00:00+00:00"),
    (1, "2099-01-10T10:00:00+00:00", 2, 7, "2099-01-17T10:00:00+00:00"),
])
def test_redeem_keeps_payment_subscription_rules(client, old_plan, old_expiry, new_plan, days, expected):
    changes = {"plan_id": new_plan}
    if days is not None:
        changes["days"] = days
    code = generate(client, **changes)[0]
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = 2", (old_plan, old_expiry))
    use_user(client, 2)
    response = client.post("/api/redeem", json={"code": code})
    assert response.status_code == 200
    assert response.json() == {"plan_name": "月套餐" if new_plan == 1 else "季套餐", "period_days": days or 30, "plan_expires_at": expected}
    assert subscription() == {"plan_id": new_plan, "plan_expires_at": expected}
    stored = stored_code(code)
    assert stored["redeemed_by"] == 2
    assert stored["redeemed_at"] == NOW


def test_me_and_ai_quota_immediately_use_new_limit_without_resetting_usage(client):
    code = generate(client, plan_id=2)[0]
    with connect(write=True) as conn:
        conn.execute("INSERT INTO ai_usage(user_id, day, attempts) VALUES (2, ?, 3)", (NOW[:10],))
    use_user(client, 2)
    assert client.post("/api/redeem", json={"code": code}).status_code == 200
    me = client.get("/api/me").json()
    with connect() as conn:
        quota = main.ai_quota(conn, 2, NOW[:10])
    for result in (me, quota):
        assert result["plan_id"] == 2
        assert result["plan_name"] == "季套餐"
        assert result["plan_active"] is True
        assert result["ai_daily_limit"] == 50
        assert result["ai_daily_used"] == 3
        assert result["ai_daily_remaining"] == 47


@pytest.mark.parametrize("variant", ["lower", "spaces", "fullwidth", "compact"])
def test_redeem_normalizes_lowercase_whitespace_hyphens_and_fullwidth(client, variant):
    code = generate(client)[0]
    if variant == "lower":
        submitted = code.lower()
    elif variant == "spaces":
        submitted = " \t" + " - \n".join(code.split("-")) + "\u3000"
    elif variant == "fullwidth":
        submitted = "".join(chr(ord(char) + 0xFEE0) for char in code.lower())
    else:
        submitted = code.replace("-", "")
    use_user(client, 2)
    response = client.post("/api/redeem", json={"code": submitted})
    assert response.status_code == 200
    assert stored_code(code)["redeemed_by"] == 2


@pytest.mark.parametrize("state", ["missing", "redeemed", "expired", "expires_now", "revoked"])
def test_invalid_code_states_have_the_same_private_failure(client, state):
    code = generate(client)[0]
    with connect(write=True) as conn:
        if state == "redeemed":
            conn.execute("UPDATE redeem_codes SET redeemed_by = 3, redeemed_at = ?", (NOW,))
        elif state in {"expired", "expires_now"}:
            expires_at = NOW if state == "expires_now" else "2098-12-31T10:00:00+00:00"
            conn.execute("UPDATE redeem_codes SET expires_at = ?", (expires_at,))
        elif state == "revoked":
            conn.execute("UPDATE redeem_codes SET revoked_at = ?", (NOW,))
    use_user(client, 2)
    response = client.post("/api/redeem", json={"code": "ABCD-EFGH-JKMN-PQRS" if state == "missing" else code})
    assert response.status_code == 400
    assert response.json() == {"detail": BAD_CODE}
    assert subscription() == {"plan_id": None, "plan_expires_at": None}


def test_redemption_is_once_only_for_same_and_different_users(client):
    code = generate(client)[0]
    use_user(client, 2)
    assert client.post("/api/redeem", json={"code": code}).status_code == 200
    first = subscription()
    for user_id in (2, 3):
        use_user(client, user_id)
        response = client.post("/api/redeem", json={"code": code})
        assert response.status_code == 400
        assert response.json()["detail"] == BAD_CODE
    assert subscription() == first
    assert subscription(3)["plan_id"] is None


def test_two_threads_competing_for_same_code_have_exactly_one_winner(client):
    code = generate(client)[0]
    barrier = threading.Barrier(2)

    def redeem(user_id):
        # Independent client cookie jars; no nested application lifespan.
        competitor = TestClient(main.app, headers=CSRF)
        try:
            use_user(competitor, user_id)
            barrier.wait(timeout=10)
            response = competitor.post("/api/redeem", json={"code": code})
            return user_id, response.status_code, response.json()
        finally:
            competitor.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(redeem, (2, 3)))
    assert sorted(status for _, status, _ in outcomes) == [200, 400]
    winner = next(user_id for user_id, status, _ in outcomes if status == 200)
    loser = 3 if winner == 2 else 2
    assert stored_code(code)["redeemed_by"] == winner
    assert subscription(winner)["plan_id"] == 1
    assert subscription(loser)["plan_id"] is None
    assert next(body for _, status, body in outcomes if status == 400) == {"detail": BAD_CODE}


def test_inactive_plan_is_still_redeemable(client):
    code = generate(client, plan_id=3)[0]
    use_user(client, 2)
    response = client.post("/api/redeem", json={"code": code})
    assert response.status_code == 200
    assert response.json()["plan_name"] == "已停用套餐"
    assert response.json()["period_days"] == 60
    assert client.get("/api/me").json()["ai_daily_limit"] == 40


def test_activation_failure_rolls_back_code_claim_and_subscription(client, monkeypatch):
    code = generate(client)[0]
    use_user(client, 2)
    original = main.activate_plan

    def fail_after_update(conn, user_id, plan_id, period_days, now=None):
        original(conn, user_id, plan_id, period_days, now=now)
        raise RuntimeError("simulated activation failure")

    monkeypatch.setattr(main, "activate_plan", fail_after_update)
    with pytest.raises(RuntimeError, match="simulated activation failure"):
        client.post("/api/redeem", json={"code": code})
    assert stored_code(code)["redeemed_by"] is None
    assert stored_code(code)["redeemed_at"] is None
    assert subscription() == {"plan_id": None, "plan_expires_at": None}
    monkeypatch.setattr(main, "activate_plan", original)
    assert client.post("/api/redeem", json={"code": code}).status_code == 200


def test_redeem_trial_login_and_code_length_contract(client):
    code = generate(client)[0]
    use_user(client, 4)
    response = client.post("/api/redeem", json={"code": code})
    assert response.status_code == 403
    assert response.json()["detail"] == "体验账号不能兑换，请先注册正式账号"
    client.cookies.clear()
    assert client.post("/api/redeem", json={"code": code}).status_code == 401
    use_user(client, 2)
    assert client.post("/api/redeem", json={"code": "A" * 65}).status_code == 422
    assert client.post("/api/redeem", json={"code": 123}).status_code == 422


def test_redeem_accepts_exactly_64_characters_and_empty_is_uniform_error(client):
    code = generate(client)[0]
    use_user(client, 2)
    assert client.post("/api/redeem", json={"code": ""}).json() == {"detail": BAD_CODE}
    submitted = code + " " * (64 - len(code))
    assert len(submitted) == 64
    assert client.post("/api/redeem", json={"code": submitted}).status_code == 200


def test_user_rate_limit_allows_ten_attempts_then_returns_429(client):
    use_user(client, 2)
    for _ in range(10):
        assert client.post("/api/redeem", json={"code": "invalid"}).status_code == 400
    response = client.post("/api/redeem", json={"code": "invalid"})
    assert response.status_code == 429
    assert response.json()["detail"] == "尝试次数过多，请稍后再试"


def test_ip_rate_limit_counts_multiple_users_at_the_login_client_ip(client):
    for user_id in (1, 2, 3):
        use_user(client, user_id)
        for _ in range(10):
            assert client.post("/api/redeem", json={"code": "invalid"}).status_code == 400
    use_user(client, 7)
    response = client.post("/api/redeem", json={"code": "invalid"})
    assert response.status_code == 429
    assert response.json()["detail"] == "尝试次数过多，请稍后再试"
    assert "redeem-ip:testclient" in main._rate_buckets


def test_codes_and_inputs_do_not_appear_in_logs(client, caplog):
    caplog.set_level("INFO")
    code = generate(client)[0]
    use_user(client, 2)
    submitted = code.lower()
    assert client.post("/api/redeem", json={"code": submitted}).status_code == 200
    assert code not in caplog.text
    assert submitted not in caplog.text


@pytest.mark.parametrize("user_id", [5, 6])
def test_deleted_and_banned_accounts_follow_current_user_rules(client, user_id):
    code = generate(client)[0]
    use_user(client, user_id)
    assert client.post("/api/redeem", json={"code": code}).status_code == 401
    assert client.get("/api/manual-payment").status_code == 401
    assert stored_code(code)["redeemed_by"] is None


@pytest.mark.parametrize("method,path,payload", [
    ("GET", "/api/admin/redeem-codes", None),
    ("POST", "/api/admin/redeem-codes", {"plan_id": 1, "count": 1}),
    ("POST", "/api/admin/redeem-codes/1/revoke", None),
    ("POST", "/api/admin/manual-grant", {"username": "alice", "plan_id": 1}),
    ("PUT", "/api/admin/manual-payment/settings", {"enabled": True, "contact": "test"}),
    ("DELETE", "/api/admin/manual-payment/qr/alipay", None),
])
def test_admin_routes_reject_non_admin_consistently(client, method, path, payload):
    use_user(client, 2)
    response = client.request(method, path, json=payload)
    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理员权限"


def test_qr_upload_requires_admin(client):
    use_user(client, 2)
    response = upload_qr(client)
    assert response.status_code == 403
    assert response.json()["detail"] == "需要管理员权限"


def test_admin_and_manual_read_routes_require_login(client):
    client.cookies.clear()
    assert client.get("/api/admin/redeem-codes").status_code == 401
    assert client.get("/api/manual-payment").status_code == 401
    assert upload_qr(client).status_code == 401


@pytest.mark.parametrize("method,path,payload", [
    ("POST", "/api/redeem", {"code": "ABCD-EFGH-JKMN-PQRS"}),
    ("POST", "/api/admin/redeem-codes", {"plan_id": 1, "count": 1}),
    ("POST", "/api/admin/redeem-codes/1/revoke", None),
    ("POST", "/api/admin/manual-grant", {"username": "alice", "plan_id": 1}),
    ("PUT", "/api/admin/manual-payment/settings", {"enabled": True, "contact": "test"}),
    ("DELETE", "/api/admin/manual-payment/qr/alipay", None),
])
def test_all_json_writes_require_csrf(client, method, path, payload):
    client.headers.pop("X-CSRF-Protection")
    assert client.request(method, path, json=payload).status_code == 403


def test_qr_upload_requires_csrf(client):
    client.headers.pop("X-CSRF-Protection")
    assert upload_qr(client).status_code == 403


@pytest.mark.parametrize("kind", ["redeem", "generate", "revoke", "grant", "settings", "upload", "delete"])
def test_every_new_write_rechecks_account_inside_transaction(client, monkeypatch, kind):
    code = generate(client)[0]
    row_id = stored_code(code)["id"]
    calls = []

    def account_disappeared(conn, user_id, expected_hash=None):
        calls.append(user_id)
        raise HTTPException(401, "登录已过期，请重新登录")

    monkeypatch.setattr(main, "recheck_account", account_disappeared)
    if kind == "redeem":
        use_user(client, 2)
        response = client.post("/api/redeem", json={"code": code})
    elif kind == "generate":
        response = client.post("/api/admin/redeem-codes", json={"plan_id": 1, "count": 1})
    elif kind == "revoke":
        response = client.post(f"/api/admin/redeem-codes/{row_id}/revoke")
    elif kind == "grant":
        response = client.post("/api/admin/manual-grant", json={"username": "alice", "plan_id": 1})
    elif kind == "settings":
        response = client.put("/api/admin/manual-payment/settings", json={"enabled": True, "contact": "test"})
    elif kind == "upload":
        response = upload_qr(client)
    else:
        response = client.delete("/api/admin/manual-payment/qr/alipay")
    assert response.status_code == 401
    assert calls
    assert stored_code(code)["redeemed_by"] is None
    assert stored_code(code)["revoked_at"] is None


@pytest.mark.parametrize("change,status,message", [
    ("is_banned = 1", 401, "账号已被封禁，无法继续使用"),
    ("is_trial = 1", 403, "体验账号不能兑换，请先注册正式账号"),
    ("deleted_at = '2099-01-01T10:00:00+00:00'", 401, "登录已过期，请重新登录"),
])
def test_redeem_rechecks_changes_after_authentication(client, monkeypatch, change, status, message):
    code = generate(client)[0]
    use_user(client, 2)
    original = main.recheck_account

    def changed_after_snapshot(conn, user_id, expected_hash=None):
        # Simulate a concurrent status change between current_user and the write.
        conn.execute(f"UPDATE users SET {change} WHERE id = ?", (user_id,))
        return original(conn, user_id, expected_hash)

    monkeypatch.setattr(main, "recheck_account", changed_after_snapshot)
    response = client.post("/api/redeem", json={"code": code})
    assert response.status_code == status
    assert response.json()["detail"] == message
    assert stored_code(code)["redeemed_by"] is None
    assert subscription()["plan_id"] is None


@pytest.mark.parametrize("change,status", [("is_admin = 0", 403), ("is_banned = 1", 401)])
def test_admin_write_rechecks_revoked_permissions_and_ban(client, monkeypatch, change, status):
    original = main.recheck_account

    def changed_after_snapshot(conn, user_id, expected_hash=None):
        conn.execute(f"UPDATE users SET {change} WHERE id = ?", (user_id,))
        return original(conn, user_id, expected_hash)

    monkeypatch.setattr(main, "recheck_account", changed_after_snapshot)
    response = client.post("/api/admin/redeem-codes", json={"plan_id": 1, "count": 1})
    assert response.status_code == status
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 0


def test_revoke_only_unused_codes(client):
    unused, used = generate(client, count=2)
    use_user(client, 2)
    assert client.post("/api/redeem", json={"code": used}).status_code == 200
    use_user(client, 1)
    unused_id = stored_code(unused)["id"]
    assert client.post(f"/api/admin/redeem-codes/{unused_id}/revoke").json() == {"ok": True}
    assert stored_code(unused)["revoked_at"] == NOW
    assert client.post(f"/api/admin/redeem-codes/{stored_code(used)['id']}/revoke").status_code == 409
    assert client.post(f"/api/admin/redeem-codes/{unused_id}/revoke").status_code == 409
    assert client.post("/api/admin/redeem-codes/999/revoke").status_code == 409
    assert stored_code(used)["revoked_at"] is None


def test_list_filters_newest_order_limit_and_redemption_username(client):
    unused, redeemed, revoked = generate(client, count=3, note="<img src=x onerror=alert(1)>")
    use_user(client, 2)
    assert client.post("/api/redeem", json={"code": redeemed}).status_code == 200
    use_user(client, 1)
    assert client.post(f"/api/admin/redeem-codes/{stored_code(revoked)['id']}/revoke").status_code == 200
    older_id = stored_code(unused)["id"]
    with connect(write=True) as conn:
        conn.execute("UPDATE redeem_codes SET created_at = '2098-12-31T10:00:00+00:00' WHERE id = ?", (older_id,))
    expected = {"unused": unused, "redeemed": redeemed, "revoked": revoked}
    for status, code in expected.items():
        response = client.get("/api/admin/redeem-codes", params={"status": status})
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["codes"]] == [stored_code(code)["id"]]
        assert response.json()["codes"][0]["status"] == status
        assert response.json()["codes"][0]["note"] == "<img src=x onerror=alert(1)>"
        if status == "redeemed":
            assert response.json()["codes"][0]["redeemed_by"] == "alice"
            assert response.json()["codes"][0]["redeemed_at"] == NOW
    listed = client.get("/api/admin/redeem-codes").json()["codes"]
    assert [item["id"] for item in listed] == [stored_code(revoked)["id"], stored_code(redeemed)["id"], older_id]
    assert len(client.get("/api/admin/redeem-codes", params={"limit": 1}).json()["codes"]) == 1
    assert client.get("/api/admin/redeem-codes", params={"status": "unknown"}).status_code == 422


@pytest.mark.parametrize("old_expiry,days,expected", [
    (None, None, "2099-01-31T10:00:00+00:00"),
    ("2098-12-01T10:00:00+00:00", 7, "2099-01-08T10:00:00+00:00"),
    ("2099-01-10T10:00:00+00:00", 7, "2099-01-17T10:00:00+00:00"),
])
def test_manual_grant_normalizes_username_uses_shared_rules_and_audit(client, old_expiry, days, expected):
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET plan_id = 2, plan_expires_at = ? WHERE id = 2", (old_expiry,))
    payload = {"username": "  ＡＬＩＣＥ  ", "plan_id": 1}
    if days is not None:
        payload["days"] = days
    response = client.post("/api/admin/manual-grant", json=payload)
    assert response.status_code == 200
    assert response.json() == {"username": "alice", "plan_name": "月套餐", "plan_expires_at": expected}
    assert subscription() == {"plan_id": 1, "plan_expires_at": expected}
    with connect() as conn:
        record = dict(conn.execute("SELECT * FROM redeem_codes").fetchone())
    assert record["redeemed_by"] == 2
    assert record["created_by"] == 1
    assert record["redeemed_at"] == NOW
    assert record["note"].startswith("管理员直接开通：")
    assert record["period_days"] == (days or 30)
    assert re.fullmatch("[0-9a-f]{64}", record["code_hash"])
    assert len(record["code_hint"]) == 4
    listed = client.get("/api/admin/redeem-codes", params={"status": "redeemed"}).json()["codes"]
    assert listed[0]["redeemed_by"] == "alice"
    use_user(client, 3)
    assert client.post("/api/redeem", json={"code": record["code_hint"]}).json() == {"detail": BAD_CODE}


@pytest.mark.parametrize("submitted", ["ＡＬＩＣＥ", "  ＡＬＩＣＥ  ", "\u3000ＡＬＩＣＥ\u3000"])
def test_manual_grant_preserves_legacy_fullwidth_username_lookup(client, submitted):
    legacy_name = "ａｌｉｃｅ"
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET username = ? WHERE id = 2", (legacy_name,))
    response = client.post("/api/admin/manual-grant", json={"username": submitted, "plan_id": 1})
    assert response.status_code == 200
    assert response.json()["username"] == legacy_name
    assert subscription()["plan_id"] == 1
    listed = client.get("/api/admin/redeem-codes", params={"status": "redeemed"}).json()["codes"]
    assert listed[0]["redeemed_by"] == legacy_name
    assert listed[0]["note"] == "管理员直接开通：" + legacy_name


@pytest.mark.parametrize("username,message", [
    (" \t\u3000", "用户名不能为空"),
    ("a" * 33, "用户名不能超过 32 个字符"),
])
def test_manual_grant_rejects_whitespace_only_and_overlong_normalized_name(client, username, message):
    response = client.post("/api/admin/manual-grant", json={"username": username, "plan_id": 1})
    assert response.status_code == 400
    assert response.json()["detail"] == message
    assert subscription()["plan_id"] is None


@pytest.mark.parametrize("username", ["missing", "deleted"])
def test_manual_grant_excludes_missing_and_deleted_accounts(client, username):
    response = client.post("/api/admin/manual-grant", json={"username": username, "plan_id": 1})
    assert response.status_code == 404
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 0


@pytest.mark.parametrize("days", [0, 3651, True, "7"])
def test_manual_grant_validates_days(client, days):
    assert client.post("/api/admin/manual-grant", json={"username": "alice", "plan_id": 1, "days": days}).status_code == 422


def test_manual_grant_unknown_plan_is_rejected(client):
    assert client.post("/api/admin/manual-grant", json={"username": "alice", "plan_id": 999}).status_code == 404
    assert subscription()["plan_id"] is None


def test_manual_grant_can_use_inactive_plan(client):
    response = client.post("/api/admin/manual-grant", json={"username": "alice", "plan_id": 3})
    assert response.status_code == 200
    assert response.json()["plan_name"] == "已停用套餐"
    assert subscription()["plan_id"] == 3


@pytest.mark.parametrize("username,status", [("trial", 403), ("banned", 401)])
def test_manual_grant_rechecks_trial_and_banned_target(client, username, status):
    response = client.post("/api/admin/manual-grant", json={"username": username, "plan_id": 1})
    assert response.status_code == status
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 0


def test_settings_roundtrip_contact_as_json_and_missing_qr_flags(client):
    assert client.get("/api/manual-payment").json() == {"enabled": False, "contact": "", "qr": {"alipay": False, "wechat": False}}
    contact = "<img src=x onerror=alert('x')> 站长微信：oy"
    response = client.put("/api/admin/manual-payment/settings", json={"enabled": True, "contact": contact})
    assert response.status_code == 200
    assert response.json() == {"enabled": True, "contact": contact}
    assert client.get("/api/manual-payment").json() == {"enabled": True, "contact": contact, "qr": {"alipay": False, "wechat": False}}
    with connect() as conn:
        settings = dict(conn.execute("SELECT key, value FROM app_settings"))
    assert settings == {"manual_payment_enabled": "1", "manual_payment_contact": contact}
    assert client.put("/api/admin/manual-payment/settings", json={"enabled": False, "contact": ""}).json() == {"enabled": False, "contact": ""}
    with connect() as conn:
        assert conn.execute("SELECT value FROM app_settings WHERE key = 'manual_payment_enabled'").fetchone()[0] == "0"


@pytest.mark.parametrize("payload", [{"enabled": "1", "contact": ""}, {"enabled": True, "contact": "c" * 201}])
def test_settings_reject_invalid_input(client, payload):
    assert client.put("/api/admin/manual-payment/settings", json=payload).status_code == 422


def test_contact_maximum_length_is_accepted(client):
    contact = "联" * 200
    response = client.put("/api/admin/manual-payment/settings", json={"enabled": True, "contact": contact})
    assert response.status_code == 200
    assert client.get("/api/manual-payment").json()["contact"] == contact


@pytest.mark.parametrize("channel,fmt", [("alipay", "JPEG"), ("wechat", "PNG")])
def test_qr_upload_reencodes_png_with_fixed_path_and_private_cache(client, tmp_path, channel, fmt):
    response = upload_qr(client, image_bytes(fmt=fmt, with_metadata=fmt == "PNG"), channel=channel, filename="../../outside.png", content_type="image/png" if fmt == "PNG" else "image/jpeg")
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    qr_dir = tmp_path / "pay-qr"
    assert [path.name for path in qr_dir.iterdir()] == [f"{channel}.png"]
    assert not (tmp_path / "outside.png").exists()
    fetched = client.get(f"/api/manual-payment/qr/{channel}")
    assert fetched.status_code == 200
    assert fetched.headers["content-type"] == "image/png"
    assert fetched.headers["cache-control"] == "private, max-age=300"
    image = Image.open(io.BytesIO(fetched.content))
    image.load()
    assert image.format == "PNG"
    assert image.size == (400, 300)
    assert "private-note" not in image.info
    assert client.get("/api/manual-payment").json()["qr"][channel] is True
    assert client.delete(f"/api/admin/manual-payment/qr/{channel}").json() == {"ok": True}
    assert not (qr_dir / f"{channel}.png").exists()
    assert client.get(f"/api/manual-payment/qr/{channel}").status_code == 404
    assert client.get("/api/manual-payment").json()["qr"][channel] is False


def test_qr_long_side_shrinks_proportionally_to_2000(client):
    assert upload_qr(client, image_bytes(size=(4000, 1000))).status_code == 200
    fetched = client.get("/api/manual-payment/qr/alipay")
    image = Image.open(io.BytesIO(fetched.content))
    assert image.size == (2000, 500)


@pytest.mark.parametrize("content,content_type", [
    (b"not an image", "image/png"), (b"", "image/jpeg"),
    (image_bytes(fmt="BMP"), "image/png"), (image_bytes(fmt="WEBP"), "image/png"),
], ids=["not-image", "empty", "bmp", "webp"])
def test_qr_rejects_nonimages_and_disallowed_real_formats(client, content, content_type):
    response = upload_qr(client, content, filename="looks-valid.png", content_type=content_type)
    assert response.status_code == 400
    assert client.get("/api/manual-payment/qr/alipay").status_code == 404


def test_qr_upload_size_limit_is_two_mebibytes(client):
    content = image_bytes(fmt="PNG") + b"x" * (2 * 1024 * 1024)
    assert upload_qr(client, content).status_code == 413
    assert client.get("/api/manual-payment/qr/alipay").status_code == 404


def test_qr_upload_accepts_exactly_two_mebibytes_and_drops_trailing_bytes(client):
    png = image_bytes(fmt="PNG")
    content = png + b"x" * (2 * 1024 * 1024 - len(png))
    response = upload_qr(client, content, content_type="image/png")
    assert response.status_code == 200
    fetched = client.get("/api/manual-payment/qr/alipay")
    assert len(fetched.content) < 2 * 1024 * 1024
    image = Image.open(io.BytesIO(fetched.content))
    image.load()
    assert image.size == (400, 300)


@pytest.mark.parametrize("size", [(4, 3), (5, 5)], ids=["bomb-warning", "bomb-error"])
def test_qr_rejects_decompression_bomb_warning_and_error(client, monkeypatch, size):
    content = image_bytes(size=size)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 10)
    response = upload_qr(client, content)
    assert response.status_code == 400
    assert client.get("/api/manual-payment/qr/alipay").status_code == 404


@pytest.mark.parametrize("damage", ["crc", "truncated"])
def test_qr_rejects_damaged_png(client, damage):
    content = bytearray(image_bytes(fmt="PNG"))
    if damage == "crc":
        start = content.index(b"IDAT") - 4
        size = int.from_bytes(content[start:start + 4], "big")
        content[start + 8 + size] ^= 1
    else:
        content = content[:-20]
    assert upload_qr(client, bytes(content), content_type="image/png").status_code == 400


def test_manual_payment_enabled_and_uploaded_channels_are_independent_flags(client):
    assert upload_qr(client).status_code == 200
    body = client.get("/api/manual-payment").json()
    assert body["enabled"] is False
    assert body["qr"] == {"alipay": True, "wechat": False}
    assert client.put("/api/admin/manual-payment/settings", json={"enabled": True, "contact": "微信 oy"}).status_code == 200
    assert upload_qr(client, channel="wechat").status_code == 200
    assert client.get("/api/manual-payment").json() == {"enabled": True, "contact": "微信 oy", "qr": {"alipay": True, "wechat": True}}
    assert client.delete("/api/admin/manual-payment/qr/alipay").status_code == 200
    assert client.delete("/api/admin/manual-payment/qr/wechat").status_code == 200
    assert client.get("/api/manual-payment").json()["enabled"] is True


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/manual-payment/qr/unknown"),
    ("PUT", "/api/admin/manual-payment/qr/unknown"),
    ("DELETE", "/api/admin/manual-payment/qr/unknown"),
])
def test_unknown_qr_channel_is_404(client, method, path):
    files = {"file": ("qr.jpg", image_bytes(), "image/jpeg")} if method == "PUT" else None
    assert client.request(method, path, files=files).status_code == 404


def test_missing_qr_and_unauthenticated_manual_payment_are_404_and_401(client):
    assert client.get("/api/manual-payment/qr/alipay").status_code == 404
    client.cookies.clear()
    assert client.get("/api/manual-payment").status_code == 401
    assert client.get("/api/manual-payment/qr/alipay").status_code == 401
