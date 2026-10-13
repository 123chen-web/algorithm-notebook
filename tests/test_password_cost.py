"""线上密码哈希强度：conftest 为提速把迭代次数降了，这里用真实值钉住。"""
import hashlib

import pytest

from core import config, security


@pytest.mark.real_password_hash
def test_production_iterations_stay_at_owasp_level():
    # OWASP 对 PBKDF2-HMAC-SHA256 的建议是 >= 600,000 次。
    assert config.PASSWORD_ITERATIONS >= 600_000
    assert security.PASSWORD_ITERATIONS == config.PASSWORD_ITERATIONS


@pytest.mark.real_password_hash
def test_hash_uses_the_production_iteration_count():
    stored = security.password_hash("correct horse battery")
    salt, digest = stored.split(":")
    expected = hashlib.pbkdf2_hmac(
        "sha256", b"correct horse battery", bytes.fromhex(salt), config.PASSWORD_ITERATIONS
    ).hex()
    assert digest == expected
    assert security.password_matches("correct horse battery", stored)
    assert not security.password_matches("wrong", stored)


def test_suite_runs_with_reduced_iterations_by_default():
    assert security.PASSWORD_ITERATIONS == 1_000
