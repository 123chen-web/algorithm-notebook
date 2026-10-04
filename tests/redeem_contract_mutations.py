"""四项无临时目录的静态契约变异；只修改内存中的源码，不写工作区。

这不代替数据库行为变异测试，后者必须在可用的系统临时目录运行。
运行：python -B tests/redeem_contract_mutations.py
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace


path = Path(__file__).with_name("test_redeem_contracts.py")
spec = importlib.util.spec_from_file_location("redeem_contracts", path)
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)
original_path = checks.SOURCE
source = original_path.read_text(encoding="utf-8")
mutations = [
    ("atomic-unused", "AND redeemed_by IS NULL AND revoked_at IS NULL",
     "AND revoked_at IS NULL", checks.test_claim_sql_contains_all_atomic_guards, ()),
    ("expiry-boundary", "AND (expires_at IS NULL OR expires_at > ?)",
     "AND (expires_at IS NULL OR expires_at >= ?)",
     checks.test_claim_sql_contains_all_atomic_guards, ()),
    ("admin-authorization",
     'def generate_redeem_codes(data: NewRedeemCodes, user=Depends(current_user)):\n    require_admin(user)',
     'def generate_redeem_codes(data: NewRedeemCodes, user=Depends(current_user)):\n    pass',
     checks.test_admin_routes_authorize_before_access, ("generate_redeem_codes",)),
    ("uniform-error", 'REDEEM_ERROR = "兑换码不正确、已使用或已过期"',
     'REDEEM_ERROR = "兑换码不存在"', checks.test_claim_failure_uses_single_exact_error, ()),
]
try:
    for name, before, after, test, args in mutations:
        assert before in source, f"mutation target missing: {name}"
        mutated = source.replace(before, after, 1)
        checks.SOURCE = SimpleNamespace(read_text=lambda **kwargs: mutated)
        try:
            test(*args)
        except AssertionError:
            print(f"KILLED {name}: contract assertion failed")
        else:
            raise AssertionError(f"SURVIVED {name}")
finally:
    checks.SOURCE = original_path
assert original_path.read_text(encoding="utf-8") == source
print("4/4 static mutations killed; source unchanged")
