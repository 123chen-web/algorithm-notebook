import pytest

import admin_tool
import db


@pytest.fixture
def admin_database(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "admins.db"))
    db.init_db()


def add_user(username, *, is_admin=0, deleted_at=None):
    with db.connect(write=True) as conn:
        return conn.execute(
            "INSERT INTO users (username, password_hash, timezone, created_at, "
            "email, is_admin, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                username, "private-password-hash", "Asia/Shanghai",
                "2026-10-03T00:00:00+00:00", f"{username}@example.com",
                is_admin, deleted_at,
            ),
        ).lastrowid


def admin_flag(user_id):
    with db.connect() as conn:
        return conn.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,)).fetchone()[0]


def test_list_shows_only_live_admins_without_sensitive_fields(admin_database, capsys):
    user_id = add_user("alice", is_admin=1)
    add_user("ordinary")
    add_user("deleted", is_admin=1, deleted_at="2026-10-03T01:00:00+00:00")
    assert admin_tool.main(["list"]) == 0
    output = capsys.readouterr().out
    assert f"{user_id}\talice" in output
    assert "ordinary" not in output
    assert "deleted" not in output
    assert "private-password-hash" not in output
    assert "@example.com" not in output


def test_list_without_admins(admin_database, capsys):
    add_user("ordinary")
    assert admin_tool.main(["list"]) == 0
    assert "当前没有管理员账号" in capsys.readouterr().out


def test_grant_normalizes_username(admin_database, capsys):
    user_id = add_user("alice")
    assert admin_tool.main(["grant", " ＡＬＩＣＥ "]) == 0
    assert admin_flag(user_id) == 1
    assert "已授予『alice』的管理员身份" in capsys.readouterr().out


def test_grant_finds_legacy_fullwidth_username(admin_database):
    user_id = add_user("ａｌｉｃｅ")
    assert admin_tool.main(["grant", "ＡＬＩＣＥ"]) == 0
    assert admin_flag(user_id) == 1


def test_revoke_with_another_admin(admin_database, capsys):
    first_id = add_user("alice", is_admin=1)
    second_id = add_user("bob", is_admin=1)
    assert admin_tool.main(["revoke", "ＡＬＩＣＥ"]) == 0
    assert admin_flag(first_id) == 0
    assert admin_flag(second_id) == 1
    assert "已撤销『alice』的管理员身份" in capsys.readouterr().out


def test_revoke_last_admin_is_rejected_and_preserved(admin_database, capsys):
    user_id = add_user("alice", is_admin=1)
    add_user("deleted", is_admin=1, deleted_at="2026-10-03T01:00:00+00:00")
    assert admin_tool.main(["revoke", "alice"]) == 1
    assert admin_flag(user_id) == 1
    assert "不能撤销最后一个管理员" in capsys.readouterr().err


def test_revoke_last_admin_with_force(admin_database):
    user_id = add_user("alice", is_admin=1)
    assert admin_tool.main(["revoke", "alice", "--force"]) == 0
    assert admin_flag(user_id) == 0


def test_revoke_non_admin_does_not_affect_last_admin(admin_database):
    user_id = add_user("alice", is_admin=1)
    ordinary_id = add_user("ordinary")
    assert admin_tool.main(["revoke", "ordinary"]) == 0
    assert admin_flag(user_id) == 1
    assert admin_flag(ordinary_id) == 0


@pytest.mark.parametrize("command", ["grant", "revoke"])
def test_unknown_or_deleted_users_are_rejected(admin_database, capsys, command):
    user_id = add_user("deleted", deleted_at="2026-10-03T01:00:00+00:00")
    assert admin_tool.main([command, "missing"]) == 1
    assert admin_tool.main([command, "deleted"]) == 1
    assert "找不到这个用户" in capsys.readouterr().err
    assert admin_flag(user_id) == 0


@pytest.mark.parametrize("value", ["", "  ", "a" * 33, "㍿" * 9])
def test_grant_rejects_invalid_normalized_username(admin_database, capsys, value):
    assert admin_tool.main(["grant", value]) == 1
    assert "用户名" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("value", "expected"),
    [(" ＡＬＩＣＥ ", "alice"), ("管理员", "管理员"), ("  Alice  ", "alice"), ("㍿", "株式会社")],
)
def test_normalize_username(value, expected):
    assert db.normalize_username(value) == expected


@pytest.mark.parametrize("value", ["", " \t\n", "a" * 33, "㍿" * 9])
def test_normalize_username_rejects_empty_and_long_values(value):
    with pytest.raises(ValueError, match="用户名"):
        db.normalize_username(value)
