"""无需临时目录的关键安全契约；业务行为另外由 test_redeem_codes 覆盖。"""
import ast
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "main.py"


def function(name):
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    return next(node for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name)


def test_claim_sql_contains_all_atomic_guards():
    sql = next(node.value for node in ast.walk(function("redeem_code"))
               if isinstance(node, ast.Constant) and isinstance(node.value, str)
               and "UPDATE redeem_codes SET redeemed_by" in node.value)
    sql = " ".join(sql.split())
    assert "WHERE code_hash = ? AND redeemed_by IS NULL AND revoked_at IS NULL" in sql
    assert "AND (expires_at IS NULL OR expires_at > ?)" in sql


def test_claim_failure_uses_single_exact_error():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    error = next(node.value.value for node in tree.body if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "REDEEM_ERROR"
                         for target in node.targets))
    assert error == "兑换码不正确、已使用或已过期"
    guard = next(node for node in ast.walk(function("redeem_code"))
                 if isinstance(node, ast.If) and ast.unparse(node.test) == "claimed.rowcount != 1")
    assert ast.unparse(guard.body[0]) == "raise HTTPException(400, REDEEM_ERROR)"


@pytest.mark.parametrize("name", [
    "generate_redeem_codes", "list_redeem_codes", "revoke_redeem_code",
    "manual_grant", "upload_manual_payment_qr", "delete_manual_payment_qr",
    "update_manual_payment_settings",
])
def test_admin_routes_authorize_before_access(name):
    node = function(name)
    assert ast.unparse(node.body[0]) == "require_admin(user)"
    if name != "list_redeem_codes":
        calls = [item for item in ast.walk(node) if isinstance(item, ast.Call)
                 and isinstance(item.func, ast.Name) and item.func.id == "recheck_manual_account"]
        assert any(any(keyword.arg == "admin" and ast.unparse(keyword.value) == "True"
                       for keyword in call.keywords) for call in calls)


def test_failed_admin_check_is_forbidden():
    guard = function("require_admin")
    assert ast.unparse(guard.body[0].test) == "not user['is_admin']"
    assert ast.unparse(guard.body[0].body[0]) == "raise HTTPException(403, '需要管理员权限')"
