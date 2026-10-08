"""金额/时长冻结及人工核账；全部账户、流水和数据库均为隔离测试数据。"""
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

import admin_tool
import main
import manual_claims
from db import connect
from test_manual_claims import as_user, claim_row, confirmation, make_claim, submit
from test_redeem_codes import CSRF, NOW, client, subscription, use_user  # noqa: F401


def confirm(client, claim_id, **changes):
    return client.post(f"/api/admin/manual-claims/{claim_id}/confirm",
                       json={**confirmation(claim_id), **changes})


def test_new_claim_freezes_amount_duration_and_name(client):
    claim_id = make_claim(client)
    assert {name: claim_row(claim_id)[name] for name in
            ("amount_cents", "period_days", "plan_name_snapshot")} == {
        "amount_cents": 990, "period_days": 30, "plan_name_snapshot": "月套餐"}
    with connect(write=True) as conn:
        conn.execute("UPDATE plans SET price_cents = 1990, period_days = 90, name = '新套餐' WHERE id = 1")
    response = confirm(client, claim_id)
    assert response.status_code == 200
    assert response.json()["claim"]["plan_name"] == "月套餐"
    assert subscription(2)["plan_expires_at"] == "2099-01-31T10:00:00+00:00"
    with connect() as conn:
        audit = dict(conn.execute("SELECT * FROM redeem_codes").fetchone())
    assert audit["period_days"] == 30
    assert audit["code_hash"] == manual_claims.receipt_hash(f"alipay:test-{claim_id}")
    assert f"alipay:test-{claim_id}" not in str(audit)


def test_amount_mismatch_and_new_claim_override_do_not_grant(client):
    claim_id = make_claim(client)
    assert confirm(client, claim_id, verified_amount_cents=989).status_code == 409
    assert confirm(client, claim_id, legacy_reviewed=True, legacy_period_days=1).status_code == 422
    assert claim_row(claim_id)["status"] == "pending"
    assert claim_row(claim_id)["verified_amount_cents"] is None
    assert subscription(2)["plan_id"] is None


@pytest.mark.parametrize("changes", [
    {"verified_amount_cents": True}, {"verified_amount_cents": "990"},
    {"verified_amount_cents": 0}, {"verified_amount_cents": float("nan")},
    {"verified_amount_cents": 2**63}, {"receipt_reference": "nickname"},
    {"receipt_reference": "alipay:姓名"}, {"receipt_reference": "alipay:abc\ud800"},
    {"receipt_reference": "\nalipay:abc"}, {"receipt_reference": "alipay:abc\t"},
    {"receipt_reference": "alipay:abc\x7f"}, {"legacy_reviewed": 1},
    {"legacy_period_days": True}, {"legacy_period_days": 3651}, {"extra": "secret"},
])
def test_confirmation_rejects_unsafe_or_coerced_input_without_writes(client, changes):
    claim_id = make_claim(client)
    # stdlib JSON can transport NaN; the API must reject it without echoing the raw input.
    import json
    response = client.post(f"/api/admin/manual-claims/{claim_id}/confirm",
                           content=json.dumps({**confirmation(claim_id), **changes}),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert claim_row(claim_id)["status"] == "pending"
    assert subscription(2)["plan_id"] is None


def test_receipt_spaces_normalize_but_same_receipt_is_not_reusable(client):
    first, second = make_claim(client), make_claim(client, 3)
    assert confirm(client, first, receipt_reference="  wechat:local-trade-1  ").status_code == 200
    assert claim_row(first)["receipt_reference"] == "wechat:local-trade-1"
    response = confirm(client, second, receipt_reference="wechat:local-trade-1")
    assert response.status_code == 409 and "流水" in response.json()["detail"]
    assert claim_row(second)["status"] == "pending" and subscription(3)["plan_id"] is None
    # Different channel namespaces can legitimately have the same provider trade number.
    assert confirm(client, second, receipt_reference="alipay:local-trade-1").status_code == 200


def test_receipt_audit_prevents_reuse_after_original_account_deletion(client):
    first = make_claim(client)
    assert confirm(client, first, receipt_reference="alipay:after-delete").status_code == 200
    with connect(write=True) as conn:
        main.delete_account_data(conn, 2, NOW)
        assert conn.execute("SELECT 1 FROM manual_payment_claims WHERE id = ?", (first,)).fetchone() is None
        assert conn.execute("SELECT code_hash FROM redeem_codes").fetchone()[0] == manual_claims.receipt_hash("alipay:after-delete")
    second = make_claim(client, 3)
    assert confirm(client, second, receipt_reference="alipay:after-delete").status_code == 409
    assert subscription(3)["plan_id"] is None and claim_row(second)["receipt_reference"] is None


def test_legacy_claim_needs_explicit_review_and_does_not_fabricate_snapshots(client):
    with connect(write=True) as conn:
        claim_id = conn.execute("INSERT INTO manual_payment_claims(user_id, plan_id, payer_note, created_at) "
                                "VALUES (2, 1, 'legacy', ?)", (NOW,)).lastrowid
    payload = {"verified_amount_cents": 1234, "receipt_reference": "wechat:legacy-reconciled"}
    path = f"/api/admin/manual-claims/{claim_id}/confirm"
    assert client.post(path, json=payload).status_code == 422
    assert client.post(path, json={**payload, "legacy_reviewed": True}).status_code == 422
    response = client.post(path, json={**payload, "legacy_reviewed": True, "legacy_period_days": 7})
    assert response.status_code == 200
    row = claim_row(claim_id)
    assert row["amount_cents"] is row["period_days"] is row["plan_name_snapshot"] is None
    assert row["verified_amount_cents"] == 1234
    assert subscription(2)["plan_expires_at"] == "2099-01-08T10:00:00+00:00"
    with connect() as conn:
        assert conn.execute("SELECT period_days FROM redeem_codes").fetchone()[0] == 7


def test_partial_snapshot_cannot_be_treated_as_unrecorded_legacy(client):
    claim_id = make_claim(client)
    with connect(write=True) as conn:
        conn.execute("UPDATE manual_payment_claims SET period_days = NULL WHERE id = ?", (claim_id,))
    assert confirm(client, claim_id, legacy_reviewed=True, legacy_period_days=7).status_code == 409
    assert subscription(2)["plan_id"] is None and claim_row(claim_id)["status"] == "pending"


def test_ordinary_plan_duration_over_3650_is_frozen_without_new_artificial_cap(client):
    with connect(write=True) as conn:
        conn.execute("UPDATE plans SET period_days = 4000 WHERE id = 1")
    claim_id = make_claim(client)
    assert claim_row(claim_id)["period_days"] == 4000
    assert confirm(client, claim_id).status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT period_days FROM redeem_codes").fetchone()[0] == 4000


def test_failure_rolls_back_confirmation_receipt_and_entitlement(client, monkeypatch):
    claim_id = make_claim(client)
    def broken(*args):
        raise RuntimeError("isolated activation failure")
    monkeypatch.setattr(manual_claims, "activate_plan", broken)
    with pytest.raises(RuntimeError, match="isolated activation failure"):
        confirm(client, claim_id)
    row = claim_row(claim_id)
    assert row["status"] == "pending" and row["receipt_reference"] is None
    assert row["verified_amount_cents"] is None and subscription(2)["plan_id"] is None
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 0


def test_two_concurrent_claims_cannot_share_one_receipt(client):
    ids = (make_claim(client), make_claim(client, 3))
    barrier = threading.Barrier(2)
    def worker(claim_id):
        with TestClient(main.app, headers=CSRF) as competitor:
            use_user(competitor, 1)
            barrier.wait(timeout=10)
            return confirm(competitor, claim_id, receipt_reference="alipay:one-shared-trade").status_code
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(worker, ids)) == [200, 409]
    assert sorted(claim_row(value)["status"] for value in ids) == ["confirmed", "pending"]
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0] == 1


def test_cli_requires_reconciliation_and_does_not_prefill_received_amount(client, capsys):
    claim_id = make_claim(client)
    with pytest.raises(SystemExit):
        admin_tool.main(["confirm-claim", str(claim_id)])
    assert claim_row(claim_id)["status"] == "pending"
    assert admin_tool.main(["confirm-claim", str(claim_id), "--received-cents", "989",
                            "--receipt", "alipay:cli-wrong"]) == 1
    assert "不一致" in capsys.readouterr().err
    assert admin_tool.main(["confirm-claim", str(claim_id), "--received-cents", "990",
                            "--receipt", "alipay:cli-correct"]) == 0


def test_integer_money_format_does_not_round_large_cents():
    assert manual_claims.price_text(2**63 - 1) == "¥92233720368547758.07"


def test_bad_plan_name_is_rejected_before_snapshot_check_error(client):
    with connect(write=True) as conn:
        conn.execute("UPDATE plans SET name = '' WHERE id = 1")
    as_user(client, 2)
    assert submit(client).status_code == 422
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM manual_payment_claims").fetchone()[0] == 0
