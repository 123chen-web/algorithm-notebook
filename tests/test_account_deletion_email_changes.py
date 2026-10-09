"""注销账号必须清掉"待确认的新邮箱"：它是尚未验证的第三方邮箱地址，不能留在库里。"""
from db import connect
from test_account_security import PASSWORD, client, register  # noqa: F401


def test_deleting_an_account_removes_its_pending_email_change(client):
    user = register(client, "pendingmail")
    with connect(write=True) as conn:
        conn.execute(
            "INSERT INTO email_changes(user_id, new_email, token_hash, expires_at, created_at) "
            "VALUES (?, 'someone-else@example.com', 'hash-1', 9999999999, 1)",
            (user["id"],),
        )
    response = client.post("/api/me/delete-account", json={"password": PASSWORD})
    assert response.status_code == 200
    with connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM email_changes WHERE user_id = ?", (user["id"],)).fetchone()[0] == 0
