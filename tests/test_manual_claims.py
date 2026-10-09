"""手动收款登记：用户登记 → 站长确认/驳回 → 自动开通；命令行与迁移。"""
import logging
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import admin_tool
import db
import mailer
import main
import manual_claims
from db import connect
from test_redeem_codes import CSRF, NOW, client, generate, subscription, use_user  # noqa: F401

PAYLOAD = {"plan_id": 1, "payer_note": "支付宝尾号 1234", "contact": "wx-alice"}


def submit(client, **changes):
    return client.post("/api/manual-claims", json={**PAYLOAD, **changes})


def claim_row(claim_id):
    with connect() as conn:
        return dict(conn.execute(
            "SELECT * FROM manual_payment_claims WHERE id = ?", (claim_id,)
        ).fetchone())


def as_user(client, user_id):
    use_user(client, user_id)


def make_claim(client, user_id=2, **changes):
    as_user(client, user_id)
    response = submit(client, **changes)
    assert response.status_code == 201, response.text
    as_user(client, 1)
    return response.json()["claim"]["id"]


def confirmation(claim_id):
    # The server now requires an actual reconciled amount and a full receipt.
    row = claim_row(claim_id) if claim_id != 999 else None
    return {"verified_amount_cents": row["amount_cents"] if row else 990,
            "receipt_reference": f"alipay:test-{claim_id}"}


def confirm_request(client, claim_id):
    return client.post(f"/api/admin/manual-claims/{claim_id}/confirm",
                       json=confirmation(claim_id))


def confirm_cli(claim_id):
    body = confirmation(claim_id)
    return ["confirm-claim", str(claim_id), "--received-cents",
            str(body["verified_amount_cents"]), "--receipt", body["receipt_reference"]]


def count_claims():
    with connect() as conn:
        return conn.execute("SELECT COUNT(*) FROM manual_payment_claims").fetchone()[0]


# ---- 用户端 ----

def test_submit_returns_claim_shape(client):
    as_user(client, 2)
    response = submit(client)
    assert response.status_code == 201
    claim = response.json()["claim"]
    assert set(claim) == {"id", "plan_id", "plan_name", "payer_note", "contact", "status",
                          "reject_reason", "created_at", "decided_at", "amount_cents", "period_days",
                          "plan_name_snapshot", "verified_amount_cents", "receipt_reference"}
    assert claim["status"] == "pending" and claim["plan_name"] == "月套餐"
    assert claim["payer_note"] == "支付宝尾号 1234" and claim["decided_at"] is None
    assert claim["created_at"] == NOW


def test_contact_may_be_empty_and_text_is_stripped(client):
    as_user(client, 2)
    claim = submit(client, payer_note="  备注  ", contact="").json()["claim"]
    assert claim["payer_note"] == "备注" and claim["contact"] == ""


@pytest.mark.parametrize("changes", [
    {"payer_note": ""}, {"payer_note": "   "}, {"payer_note": "字" * 61},
    {"contact": "c" * 61}, {"payer_note": "a\nb"}, {"contact": "x\x00y"},
    {"payer_note": "a‮b"}, {"plan_id": "1"}, {"plan_id": 0}, {"plan_id": True},
    {"extra": 1},
], ids=lambda value: repr(value)[:24])
def test_submit_validation_rejects_bad_input(client, changes):
    as_user(client, 2)
    response = submit(client, **changes)
    assert response.status_code == 422
    assert count_claims() == 0


def test_boundary_lengths_accepted(client):
    as_user(client, 2)
    assert submit(client, payer_note="字" * 60, contact="c" * 60).status_code == 201


def test_unknown_and_inactive_plans_rejected_with_detail(client):
    as_user(client, 2)
    for plan_id in (3, 999):
        response = submit(client, plan_id=plan_id)
        assert response.status_code == 422
        assert "detail" in response.json()
    assert count_claims() == 0


def test_trial_anonymous_and_missing_csrf_rejected(client):
    as_user(client, 4)
    assert submit(client).status_code == 403
    client.cookies.clear()
    assert submit(client).status_code == 401
    as_user(client, 2)
    bare = TestClient(main.app)
    bare.cookies.set("session", "redeem-session-alice")
    assert bare.post("/api/manual-claims", json=PAYLOAD).status_code == 403
    assert count_claims() == 0


def test_banned_user_rejected(client):
    as_user(client, 2)
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_banned = 1 WHERE id = 2")
    assert submit(client).status_code == 401
    assert client.get("/api/manual-claims").status_code == 401
    assert count_claims() == 0


def test_pending_cap_is_three_and_frees_after_decision(client):
    ids = [make_claim(client) for _ in range(3)]
    as_user(client, 2)
    response = submit(client)
    assert response.status_code == 429 and "detail" in response.json()
    assert count_claims() == 3
    as_user(client, 3)
    assert submit(client).status_code == 201  # 上限按用户计
    as_user(client, 1)
    assert client.post(f"/api/admin/manual-claims/{ids[0]}/reject",
                       json={"reason": "没收到"}).status_code == 200
    as_user(client, 2)
    assert submit(client).status_code == 201


def test_daily_limit_is_five_and_rejected_submissions_do_not_count(client):
    for _ in range(5):
        as_user(client, 2)
        assert submit(client).status_code == 201
        with connect(write=True) as conn:  # 腾出待处理名额，只测每日上限
            conn.execute("UPDATE manual_payment_claims SET status = 'rejected'")
    as_user(client, 2)
    response = submit(client)
    assert response.status_code == 429 and "detail" in response.json()
    assert count_claims() == 5


def test_cap_rejections_do_not_burn_daily_quota(client):
    for _ in range(3):
        make_claim(client)
    as_user(client, 2)
    for _ in range(4):
        assert submit(client).status_code == 429
    with connect(write=True) as conn:
        conn.execute("UPDATE manual_payment_claims SET status = 'rejected'")
    assert submit(client).status_code == 201  # 第 4 次真正提交
    assert submit(client).status_code == 201  # 第 5 次


def test_list_returns_only_own_claims_and_purchasable_plans(client):
    mine = make_claim(client, 2)
    other = make_claim(client, 3, payer_note="bob 的备注")
    as_user(client, 2)
    response = client.get("/api/manual-claims")
    body = response.json()
    assert [claim["id"] for claim in body["claims"]] == [mine]
    assert "bob" not in response.text
    assert body["plans"] == [{"id": 1, "name": "月套餐", "price_text": "¥9.90"},
                             {"id": 2, "name": "季套餐", "price_text": "¥24.90"}]
    as_user(client, 3)
    assert [claim["id"] for claim in client.get("/api/manual-claims").json()["claims"]] == [other]


# ---- 管理员权限 ----

ADMIN_CALLS = [
    ("get", "/api/admin/manual-claims", None),
    ("post", "/api/admin/manual-claims/1/confirm", None),
    ("post", "/api/admin/manual-claims/1/reject", {"reason": "x"}),
]


def call(client, method, path, body):
    return client.post(path, json=body or {}) if method == "post" else client.get(path)


@pytest.mark.parametrize("method,path,body", ADMIN_CALLS, ids=["list", "confirm", "reject"])
def test_admin_endpoints_reject_anonymous_and_non_admin(client, method, path, body):
    make_claim(client)
    client.cookies.clear()
    assert call(client, method, path, body).status_code == 401
    as_user(client, 2)
    assert call(client, method, path, body).status_code == 403
    assert claim_row(1)["status"] == "pending"
    assert subscription(2)["plan_id"] is None


@pytest.mark.parametrize("method,path,body", ADMIN_CALLS, ids=["list", "confirm", "reject"])
def test_demoted_admin_old_session_is_rejected(client, method, path, body):
    make_claim(client)
    as_user(client, 1)
    assert call(client, "get", "/api/admin/manual-claims", None).status_code == 200
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET is_admin = 0 WHERE id = 1")
    assert call(client, method, path, body).status_code == 403
    assert claim_row(1)["status"] == "pending"
    assert subscription(2)["plan_id"] is None


def test_admin_write_requires_csrf_header(client):
    claim_id = make_claim(client)
    bare = TestClient(main.app)
    bare.cookies.set("session", "redeem-session-operator")
    assert bare.post(f"/api/admin/manual-claims/{claim_id}/confirm").status_code == 403
    assert claim_row(claim_id)["status"] == "pending"


def test_admin_list_filters_pages_and_includes_username(client):
    ids = [make_claim(client, 2 + index % 2) for index in range(6)]
    as_user(client, 1)
    body = client.get("/api/admin/manual-claims").json()
    assert set(body) == {"claims", "page", "pages"} and body["pages"] == 1 and body["page"] == 1
    assert [claim["id"] for claim in body["claims"]] == ids
    assert {claim["username"] for claim in body["claims"]} == {"alice", "bob"}
    assert body["claims"][0]["contact"] == "wx-alice"
    client.post(f"/api/admin/manual-claims/{ids[0]}/reject", json={"reason": "r"})
    assert [c["id"] for c in client.get("/api/admin/manual-claims").json()["claims"]] == ids[1:]
    rejected = client.get("/api/admin/manual-claims", params={"status": "rejected"}).json()
    assert [c["id"] for c in rejected["claims"]] == [ids[0]]
    assert client.get("/api/admin/manual-claims", params={"status": "bogus"}).status_code == 422
    assert client.get("/api/admin/manual-claims", params={"page": 0}).status_code == 422
    assert client.get("/api/admin/manual-claims", params={"page": 9}).json()["claims"] == []


def test_admin_list_paginates_twenty_per_page(client):
    with connect(write=True) as conn:
        conn.executemany(
            "INSERT INTO manual_payment_claims(user_id, plan_id, payer_note, created_at) "
            "VALUES (2, 1, 'n', ?)", [(NOW,)] * 45)
    body = client.get("/api/admin/manual-claims", params={"page": 3}).json()
    assert body["pages"] == 3 and len(body["claims"]) == 5


# ---- 确认 ----

def test_confirm_activates_plan_and_records_audit(client, caplog):
    claim_id = make_claim(client)
    caplog.set_level(logging.INFO, logger="algorithm_notebook")
    response = confirm_request(client, claim_id)
    assert response.status_code == 200
    claim = response.json()["claim"]
    assert claim["status"] == "confirmed" and claim["decided_at"] == NOW
    assert subscription(2) == {"plan_id": 1, "plan_expires_at": "2099-01-31T10:00:00+00:00"}
    row = claim_row(claim_id)
    assert row["decided_by"] == 1 and row["decided_at"] == NOW
    with connect() as conn:
        audit = conn.execute(
            "SELECT * FROM redeem_codes WHERE note LIKE ?", (f"手动收款确认 #{claim_id}%",)
        ).fetchone()
    assert audit["redeemed_by"] == 2 and audit["created_by"] == 1
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert f"#{claim_id}" in logged and "wx-alice" not in logged and "1234" not in logged
    as_user(client, 2)
    claims = client.get("/api/manual-claims").json()["claims"]
    assert claims[0]["status"] == "confirmed"


@pytest.mark.parametrize("old_plan,old_expiry,plan_id,expected", [
    (None, None, 1, "2099-01-31T10:00:00+00:00"),
    (1, "2099-01-10T10:00:00+00:00", 1, "2099-02-09T10:00:00+00:00"),
    (1, "2098-12-25T10:00:00+00:00", 1, "2099-01-31T10:00:00+00:00"),
    (1, NOW, 1, "2099-01-31T10:00:00+00:00"),
    (1, "2099-01-10T10:00:00+00:00", 2, "2099-04-10T10:00:00+00:00"),
], ids=["fresh", "extends", "expired", "exactly-now", "switch-plan"])
def test_confirm_stacking_matches_redeem_code(client, old_plan, old_expiry, plan_id, expected):
    code = generate(client, plan_id=plan_id)[0]
    for user_id in (2, 3):
        with connect(write=True) as conn:
            conn.execute("UPDATE users SET plan_id = ?, plan_expires_at = ? WHERE id = ?",
                         (old_plan, old_expiry, user_id))
    claim_id = make_claim(client, 3, plan_id=plan_id)
    assert confirm_request(client, claim_id).status_code == 200
    as_user(client, 2)
    assert client.post("/api/redeem", json={"code": code}).status_code == 200
    assert subscription(3) == subscription(2) == {"plan_id": plan_id, "plan_expires_at": expected}


def test_confirm_inactive_plan_still_works_after_deactivation(client):
    claim_id = make_claim(client, plan_id=2)
    with connect(write=True) as conn:
        conn.execute("UPDATE plans SET is_active = 0 WHERE id = 2")
    assert confirm_request(client, claim_id).status_code == 200
    assert subscription(2)["plan_id"] == 2


def test_confirm_twice_and_after_reject_return_409_without_second_grant(client):
    first = make_claim(client)
    second = make_claim(client)
    assert confirm_request(client, first).status_code == 200
    after = subscription(2)
    again = confirm_request(client, first)
    assert again.status_code == 409 and "detail" in again.json()
    assert client.post(f"/api/admin/manual-claims/{first}/reject",
                       json={"reason": "x"}).status_code == 409
    assert subscription(2) == after
    assert client.post(f"/api/admin/manual-claims/{second}/reject",
                       json={"reason": "x"}).status_code == 200
    assert confirm_request(client, second).status_code == 409
    assert subscription(2) == after
    with connect() as conn:
        grants = conn.execute(
            "SELECT COUNT(*) FROM redeem_codes WHERE note LIKE '手动收款确认%'").fetchone()[0]
    assert grants == 1
    assert confirm_request(client, 999).status_code == 404


def test_concurrent_confirm_grants_exactly_once(client):
    claim_id = make_claim(client)
    barrier = threading.Barrier(4)

    def confirm(_):
        competitor = TestClient(main.app, headers=CSRF)
        try:
            use_user(competitor, 1)
            barrier.wait(timeout=10)
            return confirm_request(competitor, claim_id).status_code
        finally:
            competitor.close()

    with ThreadPoolExecutor(max_workers=4) as executor:
        statuses = sorted(executor.map(confirm, range(4)))
    assert statuses == [200, 409, 409, 409]
    assert subscription(2) == {"plan_id": 1, "plan_expires_at": "2099-01-31T10:00:00+00:00"}


def test_confirm_refused_for_deleted_banned_and_trial_users(client):
    deleted, banned, trial = (make_claim(client, 2), make_claim(client, 3), make_claim(client, 7))
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET deleted_at = ? WHERE id = 2", (NOW,))
        conn.execute("UPDATE users SET is_banned = 1 WHERE id = 3")
        conn.execute("UPDATE users SET is_trial = 1 WHERE id = 7")
    for claim_id, user_id in ((deleted, 2), (banned, 3), (trial, 7)):
        response = confirm_request(client, claim_id)
        assert response.status_code == 409 and "detail" in response.json()
        assert claim_row(claim_id)["status"] == "pending"
        assert subscription(user_id)["plan_id"] is None


def test_confirm_sends_mail_in_background_and_failure_does_not_break(client, monkeypatch):
    sent = []
    monkeypatch.setenv("SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(mailer, "send_email", lambda *args: sent.append(args))
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET email = 'alice@example.com' WHERE id = 2")
    first = make_claim(client)
    assert confirm_request(client, first).status_code == 200
    assert len(sent) == 1 and sent[0][0] == "alice@example.com"

    def broken(*args):
        raise mailer.MailConnectError("boom")

    monkeypatch.setattr(mailer, "send_email", broken)
    second = make_claim(client)
    assert confirm_request(client, second).status_code == 200
    assert claim_row(second)["status"] == "confirmed"


def test_confirm_skips_mail_when_not_configured(client, monkeypatch):
    sent = []
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setattr(mailer, "send_email", lambda *args: sent.append(args))
    with connect(write=True) as conn:
        conn.execute("UPDATE users SET email = 'alice@example.com' WHERE id = 2")
    claim_id = make_claim(client)
    assert confirm_request(client, claim_id).status_code == 200
    assert sent == []


# ---- 驳回 ----

def test_reject_records_reason_and_does_not_grant(client):
    claim_id = make_claim(client)
    response = client.post(f"/api/admin/manual-claims/{claim_id}/reject", json={"reason": "没收到款"})
    assert response.status_code == 200
    claim = response.json()["claim"]
    assert claim["status"] == "rejected" and claim["reject_reason"] == "没收到款"
    assert claim_row(claim_id)["decided_by"] == 1
    assert subscription(2)["plan_id"] is None
    as_user(client, 2)
    assert client.get("/api/manual-claims").json()["claims"][0]["reject_reason"] == "没收到款"


@pytest.mark.parametrize("body", [{}, {"reason": ""}, {"reason": "字" * 81}, {"reason": "a\nb"}],
                         ids=["missing", "empty", "long", "newline"])
def test_reject_validates_reason(client, body):
    claim_id = make_claim(client)
    assert client.post(f"/api/admin/manual-claims/{claim_id}/reject", json=body).status_code == 422
    assert claim_row(claim_id)["status"] == "pending"


# ---- 注销 ----

def test_account_deletion_removes_claims(client):
    make_claim(client, 2)
    keep = make_claim(client, 3)
    with connect(write=True) as conn:
        main.delete_account_data(conn, 2, NOW)
    assert count_claims() == 1 and claim_row(keep)["user_id"] == 3


# ---- 迁移 ----

def test_migration_upgrades_version_10_database(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    monkeypatch.setenv("DATABASE_PATH", str(path))
    db.init_db()
    with connect(write=True) as conn:
        conn.execute("DROP TABLE manual_payment_claims")
        # a real version-10 database has none of the tables added by later migrations
        conn.execute("DROP TABLE IF EXISTS goals")
        conn.execute("DROP TABLE IF EXISTS review_ops")
        conn.execute("DROP TABLE IF EXISTS mistake_scratch")
        conn.execute("DROP TABLE IF EXISTS user_push")
        conn.execute("DROP TABLE IF EXISTS email_changes")
        conn.execute("DROP TABLE IF EXISTS problem_recommendations")
        conn.execute("DROP TABLE IF EXISTS import_previews")
        conn.execute("DROP TABLE IF EXISTS notes")
        # migration 70 新增 note_links（双向链接），真实 v10 库同样没有它。
        conn.execute("DROP TABLE IF EXISTS note_links")
        conn.execute("ALTER TABLE users DROP COLUMN reminder_opt_in")
        conn.execute("ALTER TABLE users DROP COLUMN reminder_token")
        conn.execute("DROP INDEX idx_users_api_token_hash")
        conn.execute("ALTER TABLE users DROP COLUMN api_token_hash")
        for column in ("source", "source_url", "statement", "difficulty"):
            conn.execute(f"ALTER TABLE problems DROP COLUMN {column}")
        conn.execute("DROP TRIGGER problem_lifetime_insert")
        conn.execute("ALTER TABLE users DROP COLUMN lifetime_problem_count")
        # migration 18 adds this column, so a real version-10 database does not have it
        conn.execute("ALTER TABLE mistakes DROP COLUMN pending_reason")
        # migration 19 adds bio; the frozen version-10 fixture must omit it too
        conn.execute("ALTER TABLE users DROP COLUMN bio")
        conn.execute("PRAGMA user_version = 10")
    db.init_db()
    with connect() as conn:
        assert db.schema_version(conn) == db.SCHEMA_VERSION >= 11
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(manual_payment_claims)")]
        assert columns == ["id", "user_id", "plan_id", "payer_note", "contact", "status",
                           "reject_reason", "created_at", "decided_at", "decided_by",
                           "amount_cents", "period_days", "verified_amount_cents",
                           "plan_name_snapshot", "receipt_reference"]
    with connect(write=True) as conn:
        conn.execute("INSERT INTO users(id, username, password_hash, timezone, created_at) "
                     "VALUES (1, 'u', 'x', 'Asia/Shanghai', 'now')")
        conn.execute("INSERT INTO plans(id, name, period_days, ai_daily_limit, price_cents, "
                     "is_active, created_at) VALUES (1, 'p', 30, 5, 100, 1, 'now')")
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO manual_payment_claims(user_id, plan_id, payer_note, "
                         "status, created_at) VALUES (1, 1, 'n', 'bogus', 'now')")


# ---- 命令行 ----

@pytest.fixture
def cli(client, monkeypatch):
    # client 夹具已设置好数据库路径与固定的时间。
    monkeypatch.setattr(manual_claims, "_now", lambda: NOW)
    return client


def test_cli_pending_confirm_reject(cli, capsys):
    first = make_claim(cli, 2)
    second = make_claim(cli, 3)
    assert admin_tool.main(["pending-claims"]) == 0
    out = capsys.readouterr().out
    assert "alice" in out and "bob" in out and "wx-alice" not in out
    assert admin_tool.main(confirm_cli(first)) == 0
    assert "已确认" in capsys.readouterr().out
    assert subscription(2) == {"plan_id": 1, "plan_expires_at": "2099-01-31T10:00:00+00:00"}
    assert admin_tool.main(confirm_cli(first)) == 1
    assert "已经处理过" in capsys.readouterr().err
    assert admin_tool.main(["reject-claim", str(second), "--reason", "没收到"]) == 0
    assert claim_row(second)["status"] == "rejected" and claim_row(second)["reject_reason"] == "没收到"
    assert admin_tool.main(["reject-claim", str(second), "--reason", "x"]) == 1
    assert admin_tool.main(confirm_cli(999)) == 1
    assert admin_tool.main(["pending-claims"]) == 0
    assert "没有待处理" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        admin_tool.main(["reject-claim", "1"])


def test_cli_make_codes_are_redeemable_and_printed_once(cli, capsys):
    assert admin_tool.main(["make-codes", "--plan", "2", "--count", "3", "--note", "批量"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.count("-") == 3]
    assert len(lines) == 3 and len(set(lines)) == 3
    with connect() as conn:
        rows = conn.execute("SELECT * FROM redeem_codes").fetchall()
        assert len(rows) == 3 and all(row["period_days"] == 90 and row["note"] == "批量" for row in rows)
        assert not any(code.replace("-", "") in str(dict(row)) for code in lines for row in rows)
    as_user(cli, 2)
    response = cli.post("/api/redeem", json={"code": lines[0]})
    assert response.status_code == 200 and response.json()["plan_name"] == "季套餐"


@pytest.mark.parametrize("args", [
    ["--plan", "99", "--count", "1"], ["--plan", "1", "--count", "0"],
    ["--plan", "1", "--count", "51"], ["--plan", "1", "--count", "1", "--days", "0"],
], ids=["no-plan", "zero", "too-many", "bad-days"])
def test_cli_make_codes_rejects_bad_arguments(cli, capsys, args):
    assert admin_tool.main(["make-codes", *args]) == 1
    assert "错误" in capsys.readouterr().err
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 0
