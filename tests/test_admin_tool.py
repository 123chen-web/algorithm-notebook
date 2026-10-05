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


def test_grant_requires_exact_username(admin_database, capsys):
    user_id = add_user("alice")
    assert admin_tool.main(["grant", " ＡＬＩＣＥ "]) == 1
    assert admin_flag(user_id) == 0
    assert "精确用户名" in capsys.readouterr().err
    assert admin_tool.main(["grant", "alice"]) == 0
    assert admin_flag(user_id) == 1
    assert "已授予『alice』的管理员身份" in capsys.readouterr().out


def test_grant_finds_legacy_fullwidth_username(admin_database):
    user_id = add_user("ａｌｉｃｅ")
    assert admin_tool.main(["grant", "ａｌｉｃｅ"]) == 0
    assert admin_flag(user_id) == 1


@pytest.mark.parametrize("command", ["grant", "revoke"])
def test_ambiguous_username_requires_explicit_id(admin_database, capsys, command):
    first = add_user("alice", is_admin=1)
    second = add_user("ａｌｉｃｅ", is_admin=1)
    assert admin_tool.main([command, "alice"]) == 1
    error = capsys.readouterr().err
    assert str(first) in error and str(second) in error and "--id" in error
    assert admin_flag(first) == admin_flag(second) == 1
    assert admin_tool.main([command, "--id", str(second)]) == 0
    assert admin_flag(first) == 1
    assert admin_flag(second) == int(command == "grant")


def test_check_usernames_is_read_only_and_reports_all_categories(admin_database, capsys, monkeypatch):
    first = add_user("alice")
    second = add_user("ａｌｉｃｅ")
    invisible = add_user("a\u200bb")
    reserved = add_user("已注销用户 #999")
    deleted = add_user("已注销用户 #5", deleted_at="2026-10-04")
    with db.connect() as conn:
        before = [tuple(row) for row in conn.execute("SELECT * FROM users ORDER BY id")]
    monkeypatch.setattr(admin_tool, "init_db", lambda: pytest.fail("check-usernames must not migrate"))
    assert admin_tool.main(["check-usernames"]) == 0
    output = capsys.readouterr().out
    for user_id in (first, second, invisible, reserved):
        assert f"#{user_id}" in output
    assert f"#{deleted} " not in output
    assert "规范化冲突" in output and "不可见字符" in output and "保留名前缀" in output
    assert "\\u200b" in output
    assert "private-password-hash" not in output
    with db.connect() as conn:
        assert [tuple(row) for row in conn.execute("SELECT * FROM users ORDER BY id")] == before


def test_check_usernames_does_not_create_missing_database(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "missing.db"
    monkeypatch.setenv("DATABASE_PATH", str(missing))
    assert admin_tool.main(["check-usernames"]) == 1
    assert not missing.exists()
    assert "错误" in capsys.readouterr().err


def test_id_lookup_rejects_deleted_or_missing_account(admin_database):
    deleted = add_user("deleted", deleted_at="2026-10-04")
    assert admin_tool.main(["grant", "--id", str(deleted)]) == 1
    assert admin_tool.main(["grant", "--id", "999"]) == 1
    assert admin_flag(deleted) == 0


def test_revoke_with_another_admin(admin_database, capsys):
    first_id = add_user("alice", is_admin=1)
    second_id = add_user("bob", is_admin=1)
    assert admin_tool.main(["revoke", "alice"]) == 0
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


def test_cli_read_only_check_dispatch_never_initializes_database(monkeypatch):
    calls = []
    monkeypatch.setattr(admin_tool, "init_db", lambda: pytest.fail("must not initialize database"))
    monkeypatch.setattr(admin_tool, "check_usernames", lambda: calls.append("checked"))
    assert admin_tool.main(["check-usernames"]) == 0
    assert calls == ["checked"]


def test_cli_lookup_ambiguity_with_stub_connection():
    users = [dict(id=1, username="alice", is_admin=0), dict(id=2, username="ａｌｉｃｅ", is_admin=1)]

    class Connection:
        def execute(self, sql):
            return users

    with pytest.raises(ValueError, match="--id") as error:
        admin_tool.find_user(Connection(), "alice")
    assert "#1" in str(error.value) and "#2" in str(error.value)
    assert [user["is_admin"] for user in users] == [0, 1]


def test_cli_lookup_requires_exact_name_with_stub_connection():
    user = dict(id=1, username="alice", is_admin=0)

    class Connection:
        def execute(self, sql):
            return [user]

    with pytest.raises(ValueError, match="精确用户名"):
        admin_tool.find_user(Connection(), " ＡＬＩＣＥ ")
    assert admin_tool.find_user(Connection(), "alice") is user
