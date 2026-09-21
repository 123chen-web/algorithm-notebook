from datetime import date, datetime, timezone

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import ai
import mailer
import main
from db import connect
from scheduler import schedule, today_in_timezone


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("INVITE_CODE", "test-invite")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")
    monkeypatch.setenv("COOKIE_SECURE", "0")
    monkeypatch.setenv("AI_DAILY_LIMIT", "2")
    monkeypatch.setattr(
        main, "today_for", lambda user: date(2026, 9, 19)
    )
    main.reset_rate_limits()

    # 所有 AI 测试都会替换 ai.generate，不会发起真实 API 请求。
    with TestClient(
        main.app,
        headers={"X-CSRF-Protection": "1"},
    ) as instance:
        yield instance


def register(client, username="alice", email=None):
    response = client.post(
        "/api/auth/register",
        json={
            "username": username,
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": email or f"{username}@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert response.status_code == 201
    return response.json()


def new_problem(client):
    response = client.post(
        "/api/problems",
        json={
            "title": "二分查找",
            "language": "Python",
            "code": "def search(a, target):\n    return -1\n",
            "thinking": "维护闭区间，但对结束条件理解不清楚。",
            "mistakes": [
                "循环结束条件漏掉 left == right。",
                "没有处理空数组。",
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["mistake_ids"]


def test_sm2_success_failure_and_floor():
    day = date(2026, 9, 19)

    first = schedule(0, 0, 2.5, 4, day)
    assert first["interval_days"] == 1
    assert first["due_date"] == "2026-09-20"

    second = schedule(1, 1, first["ease_factor"], 4, day)
    assert second["interval_days"] == 6

    third = schedule(2, 6, second["ease_factor"], 4, day)
    assert third["interval_days"] == 15

    failed = schedule(3, 15, 2.5, 0, day)
    assert failed["repetitions"] == 0
    assert failed["interval_days"] == 1
    assert failed["ease_factor"] == 1.7

    floor = schedule(0, 1, 1.3, 0, day)
    assert floor["ease_factor"] == 1.3

    # 浮点间隔向上取整。
    assert schedule(2, 6, 2.6, 5, day)["interval_days"] == 16

    with pytest.raises(ValueError):
        schedule(0, 0, 2.5, 6, day)


def test_timezone_day_boundary():
    now = datetime(2026, 9, 19, 16, 30, tzinfo=timezone.utc)
    assert today_in_timezone("Asia/Taipei", now) == date(2026, 9, 20)
    assert today_in_timezone("America/Los_Angeles", now) == date(2026, 9, 19)


def test_review_is_per_mistake_and_prevents_duplicate(client):
    register(client)
    first, second = new_problem(client)

    # 模拟逾期，验证下一次日期从实际复习日计算。
    with connect(write=True) as conn:
        conn.execute(
            "UPDATE mistakes SET due_date = ? WHERE id = ?",
            ("2026-09-01", first),
        )

    due = client.get("/api/mistakes").json()
    assert len(due["items"]) == 2

    response = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 0},
    )
    assert response.status_code == 200
    assert response.json()["due_date"] == "2026-09-20"

    # 相同版本的重复提交不能再次延长间隔。
    duplicate = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 0},
    )
    assert duplicate.status_code == 409

    # 即使带最新版本，未到期也不能再次评分。
    early = client.post(
        f"/api/mistakes/{first}/review",
        json={"quality": 4, "version": 1},
    )
    assert early.status_code == 409

    remaining = client.get("/api/mistakes").json()["items"]
    assert [item["id"] for item in remaining] == [second]
    assert remaining[0]["version"] == 0

    detail = client.get(f"/api/mistakes/{first}").json()
    assert len(detail["reviews"]) == 1

    all_items = client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"]
    assert len(all_items) == 2


def test_ai_persists_results_and_limits_attempts(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]

    def failed_generation(item):
        raise HTTPException(502, "模拟 AI 失败")

    monkeypatch.setattr(ai, "generate", failed_generation)
    failed = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert failed.status_code == 502
    assert client.get(
        f"/api/mistakes/{mistake_id}"
    ).json()["variants"] == []

    monkeypatch.setattr(
        ai,
        "generate",
        lambda item: {
            "description": "新题目：在有序数组中查找第一个不小于目标值的位置。",
            "model": "mock-model",
        },
    )
    generated = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert generated.status_code == 201
    variant_id = generated.json()["id"]

    saved = client.put(
        f"/api/variants/{variant_id}/result",
        json={
            "result": "solved",
            "answer_code": "def lower_bound(a, x):\n    pass\n",
            "notes": "这次正确处理了闭区间的结束条件。",
        },
    )
    assert saved.status_code == 200

    # 失败的一次也计入今日两次额度。
    limited = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert limited.status_code == 429

    detail = client.get(f"/api/mistakes/{mistake_id}").json()
    assert detail["variants"][0]["result"] == "solved"
    assert detail["variants"][0]["notes"]
    assert detail["version"] == 0
    assert detail["due_date"] == "2026-09-19"


def test_user_isolation(client, monkeypatch):
    register(client, "alice")
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(
        ai,
        "generate",
        lambda item: {"description": "模拟题目", "model": "mock-model"},
    )
    variant_id = client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).json()["id"]

    client.post("/api/auth/logout")
    register(client, "bob")

    assert client.get("/api/mistakes").json()["items"] == []
    assert client.get(f"/api/mistakes/{mistake_id}").status_code == 404

    assert client.post(
        f"/api/mistakes/{mistake_id}/review",
        json={"quality": 5, "version": 0},
    ).status_code == 404

    assert client.post(
        f"/api/mistakes/{mistake_id}/variants"
    ).status_code == 404

    assert client.put(
        f"/api/variants/{variant_id}/result",
        json={"result": "failed", "answer_code": "", "notes": ""},
    ).status_code == 404


def test_auth_and_write_request_protection(client):
    assert client.get("/api/me").status_code == 401

    register(client)
    response = client.post(
        "/api/problems",
        headers={"X-CSRF-Protection": ""},
        json={},
    )
    assert response.status_code == 403

    client.post("/api/auth/logout")
    assert client.get("/api/me").status_code == 401


def test_edit_and_delete_mistake(client):
    register(client)
    first, second = new_problem(client)

    edited = client.put(
        f"/api/mistakes/{first}",
        json={"description": "更准确的易错点描述。", "version": 0},
    )
    assert edited.status_code == 200
    assert edited.json()["description"] == "更准确的易错点描述。"
    assert edited.json()["version"] == 1

    # 版本过期的修改会被拒绝，不会覆盖别处已经生效的更新。
    stale = client.put(
        f"/api/mistakes/{first}",
        json={"description": "过期的修改。", "version": 0},
    )
    assert stale.status_code == 409

    deleted = client.delete(f"/api/mistakes/{second}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}
    assert client.delete(f"/api/mistakes/{second}").status_code == 404

    remaining = client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"]
    assert [item["id"] for item in remaining] == [first]


def test_edit_and_delete_problem_cascades(client, monkeypatch):
    register(client)
    mistake_id = new_problem(client)[0]
    monkeypatch.setattr(
        ai,
        "generate",
        lambda item: {"description": "模拟题目", "model": "mock-model"},
    )
    client.post(f"/api/mistakes/{mistake_id}/variants")
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]

    edited = client.put(
        f"/api/problems/{problem_id}",
        json={
            "title": "二分查找（修订）",
            "language": "Python",
            "code": "def search(a, target):\n    return -1\n",
            "thinking": "补充了对空数组的处理。",
        },
    )
    assert edited.status_code == 200
    assert edited.json()["title"] == "二分查找（修订）"

    refreshed = client.get(f"/api/mistakes/{mistake_id}").json()
    assert refreshed["title"] == "二分查找（修订）"
    assert refreshed["thinking"] == "补充了对空数组的处理。"

    deleted = client.delete(f"/api/problems/{problem_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"ok": True}

    # 级联删除：这道题下的易错点、复习记录和变体题都跟着消失。
    assert client.get(f"/api/mistakes/{mistake_id}").status_code == 404
    assert client.get(
        "/api/mistakes", params={"due_only": "false"}
    ).json()["items"] == []
    assert client.delete(f"/api/problems/{problem_id}").status_code == 404


def test_edit_and_delete_respect_ownership(client):
    register(client, "alice")
    mistake_id = new_problem(client)[0]
    problem_id = client.get(f"/api/mistakes/{mistake_id}").json()["problem_id"]

    client.post("/api/auth/logout")
    register(client, "bob")

    assert client.put(
        f"/api/mistakes/{mistake_id}",
        json={"description": "越权修改", "version": 0},
    ).status_code == 404
    assert client.delete(f"/api/mistakes/{mistake_id}").status_code == 404
    assert client.put(
        f"/api/problems/{problem_id}",
        json={
            "title": "越权修改",
            "language": "Python",
            "code": "pass",
            "thinking": "x",
        },
    ).status_code == 404
    assert client.delete(f"/api/problems/{problem_id}").status_code == 404


def test_register_is_rate_limited(client):
    for index in range(main.REGISTER_LIMIT):
        client.post(
            "/api/auth/register",
            json={
                "username": f"flooduser{index}",
                "password": "a-test-password-123",
                "invite_code": "wrong-code",
                "email": f"flooduser{index}@example.com",
                "timezone": "Asia/Shanghai",
            },
        )

    # 即使邀请码这次是对的，超过次数限制也会被挡在验证之前。
    blocked = client.post(
        "/api/auth/register",
        json={
            "username": "onemore",
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": "onemore@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert blocked.status_code == 429


def test_login_is_rate_limited(client):
    register(client)
    client.post("/api/auth/logout")

    for _ in range(main.LOGIN_LIMIT):
        client.post(
            "/api/auth/login",
            json={"username": "alice", "password": "wrong-password"},
        )

    blocked = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-test-password-123"},
    )
    assert blocked.status_code == 429


def test_forgot_and_reset_password_flow(client, monkeypatch):
    register(client, "alice")
    client.post("/api/auth/logout")

    sent = []
    monkeypatch.setattr(
        mailer, "send_email",
        lambda to, subject, body: sent.append((to, subject, body)),
    )

    response = client.post(
        "/api/auth/forgot-password", json={"email": "alice@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(sent) == 1
    to, subject, body = sent[0]
    assert to == "alice@example.com"
    assert "重置" in subject
    token = body.split("reset_token=")[1].split()[0]

    # 密码强度校验和普通注册/登录一样生效。
    weak = client.post(
        "/api/auth/reset-password", json={"token": token, "password": "short"}
    )
    assert weak.status_code == 422

    reset = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "a-new-password-456"},
    )
    assert reset.status_code == 200

    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-test-password-123"},
    ).status_code == 401
    assert client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "a-new-password-456"},
    ).status_code == 200

    # 用过的重置链接不能再用第二次。
    reuse = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "another-password-789"},
    )
    assert reuse.status_code == 400


def test_forgot_password_does_not_leak_account_existence(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body: sent.append(to)
    )

    # 其他请求持有写锁时，未知邮箱仍应能直接返回，不争抢写锁。
    with connect(write=True):
        response = client.post(
            "/api/auth/forgot-password", json={"email": "nobody@example.com"}
        )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == []


@pytest.mark.parametrize("smtp_fails", [False, True])
def test_reset_email_runs_after_token_commit(client, monkeypatch, smtp_fails):
    register(client, "alice")
    observed = []

    def send_email(to, subject, body):
        token = body.split("reset_token=")[1].split()[0]
        # SMTP 执行期间，另一个连接应能取得写锁并看到已提交的 token。
        with connect(write=True) as conn:
            row = conn.execute(
                "SELECT token_hash FROM password_resets WHERE token_hash = ?",
                (main.token_hash(token),),
            ).fetchone()
            observed.append(row["token_hash"] if row else None)
        if smtp_fails:
            raise RuntimeError("模拟 SMTP 发送失败")

    monkeypatch.setattr(mailer, "send_email", send_email)
    response = client.post(
        "/api/auth/forgot-password", json={"email": "alice@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert len(observed) == 1
    assert observed[0] is not None
    with connect() as conn:
        rows = conn.execute("SELECT token_hash FROM password_resets").fetchall()
        assert [row["token_hash"] for row in rows] == observed


def test_forgot_password_rechecks_email_before_issuing_token(client, monkeypatch):
    register(client, "alice")
    original_token = main.secrets.token_urlsafe
    sent = []

    def change_email(size):
        # 模拟查询邮箱与写入 token 之间，账号已更换邮箱。
        with connect(write=True) as conn:
            conn.execute(
                "UPDATE users SET email = ? WHERE username = 'alice'",
                ("new@example.com",),
            )
        return original_token(size)

    monkeypatch.setattr(main.secrets, "token_urlsafe", change_email)
    monkeypatch.setattr(mailer, "send_email", lambda *args: sent.append(args))
    response = client.post(
        "/api/auth/forgot-password", json={"email": "alice@example.com"}
    )
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert sent == []
    with connect() as conn:
        assert conn.execute("SELECT count(*) FROM password_resets").fetchone()[0] == 0


@pytest.mark.parametrize("expired", [False, True])
def test_reset_password_rejects_bad_token(client, expired):
    token = "not-a-real-token"
    if expired:
        register(client, "alice")
        with connect(write=True) as conn:
            user_id = conn.execute(
                "SELECT id FROM users WHERE username = 'alice'"
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO password_resets(token_hash, user_id, expires_at) "
                "VALUES (?, ?, ?)",
                (main.token_hash(token), user_id, int(main.time.time()) - 1),
            )
    with connect(write=True):
        response = client.post(
            "/api/auth/reset-password",
            json={"token": token, "password": "a-new-password-456"},
        )
    assert response.status_code == 400


@pytest.mark.parametrize("change", ["consumed", "expired"])
def test_reset_rechecks_token_after_hashing(client, monkeypatch, change):
    register(client, "alice")
    token = "test-reset-token"
    with connect(write=True) as conn:
        user = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = 'alice'"
        ).fetchone()
        conn.execute(
            "INSERT INTO password_resets(token_hash, user_id, expires_at) "
            "VALUES (?, ?, ?)",
            (main.token_hash(token), user["id"], int(main.time.time()) + 300),
        )
    original_hash = main.password_hash

    def hash_with_concurrent_change(password):
        # 哈希期间不持有写锁；另一个请求可使刚校验过的 token 失效。
        with connect(write=True) as conn:
            if change == "consumed":
                conn.execute("DELETE FROM password_resets WHERE user_id = ?",
                             (user["id"],))
            else:
                conn.execute(
                    "UPDATE password_resets SET expires_at = ? WHERE user_id = ?",
                    (int(main.time.time()) - 1, user["id"]),
                )
        return original_hash(password)

    monkeypatch.setattr(main, "password_hash", hash_with_concurrent_change)
    response = client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "a-new-password-456"},
    )
    assert response.status_code == 400
    with connect() as conn:
        actual = conn.execute(
            "SELECT password_hash FROM users WHERE id = ?", (user["id"],)
        ).fetchone()[0]
        assert actual == user["password_hash"]
    assert client.get("/api/me").status_code == 200


def test_reset_invalidates_existing_sessions(client, monkeypatch):
    register(client, "alice")  # 注册后处于登录状态。

    sent = []
    monkeypatch.setattr(
        mailer, "send_email", lambda to, subject, body: sent.append(body)
    )
    client.post("/api/auth/forgot-password", json={"email": "alice@example.com"})
    token = sent[0].split("reset_token=")[1].split()[0]

    assert client.get("/api/me").status_code == 200

    client.post(
        "/api/auth/reset-password",
        json={"token": token, "password": "a-new-password-456"},
    )

    # 之前的会话已经失效，得用新密码重新登录。
    assert client.get("/api/me").status_code == 401


def test_register_validates_and_dedupes_email(client):
    bad = client.post(
        "/api/auth/register",
        json={
            "username": "carol",
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": "not-an-email",
            "timezone": "Asia/Shanghai",
        },
    )
    assert bad.status_code == 422

    register(client, "carol", email="shared@example.com")
    client.post("/api/auth/logout")

    duplicate = client.post(
        "/api/auth/register",
        json={
            "username": "dave",
            "password": "a-test-password-123",
            "invite_code": "test-invite",
            "email": "shared@example.com",
            "timezone": "Asia/Shanghai",
        },
    )
    assert duplicate.status_code == 409
    assert "邮箱" in duplicate.json()["detail"]


def test_update_email_for_existing_account(client):
    register(client, "alice", email="old@example.com")

    updated = client.put("/api/me/email", json={"email": "NEW@Example.com"})
    assert updated.status_code == 200
    # 存进去之前会统一转小写，和注册时的处理保持一致。
    assert updated.json()["email"] == "new@example.com"
    assert client.get("/api/me").json()["email"] == "new@example.com"

    invalid = client.put("/api/me/email", json={"email": "not-an-email"})
    assert invalid.status_code == 422


def test_update_email_rejects_duplicate(client):
    register(client, "alice", email="alice@example.com")
    client.post("/api/auth/logout")
    register(client, "bob", email="bob@example.com")

    conflict = client.put("/api/me/email", json={"email": "alice@example.com"})
    assert conflict.status_code == 409


def test_forgot_password_is_rate_limited(client):
    for _ in range(main.FORGOT_PASSWORD_LIMIT):
        client.post(
            "/api/auth/forgot-password", json={"email": "nobody@example.com"}
        )
    blocked = client.post(
        "/api/auth/forgot-password", json={"email": "nobody@example.com"}
    )
    assert blocked.status_code == 429


def test_reset_password_is_rate_limited(client):
    for _ in range(main.RESET_PASSWORD_LIMIT):
        client.post(
            "/api/auth/reset-password",
            json={"token": "whatever", "password": "a-test-password-123"},
        )
    blocked = client.post(
        "/api/auth/reset-password",
        json={"token": "whatever", "password": "a-test-password-123"},
    )
    assert blocked.status_code == 429


def test_trial_account_signs_in_without_invite_code(client):
    response = client.post(
        "/api/auth/trial", json={"timezone": "Asia/Shanghai"}
    )
    assert response.status_code == 201
    username = response.json()["username"]
    assert username.startswith("trial_")

    me = client.get("/api/me")
    assert me.status_code == 200
    body = me.json()
    assert body["username"] == username
    assert body["is_trial"] is True
    assert body["email"] is None


def test_trial_account_is_rate_limited(client):
    for _ in range(main.TRIAL_LIMIT):
        client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})

    blocked = client.post(
        "/api/auth/trial", json={"timezone": "Asia/Shanghai"}
    )
    assert blocked.status_code == 429


def test_trial_account_uses_its_own_lower_ai_limit(client, monkeypatch):
    # 正式账号的额度（client fixture 里设成 2）比体验账号（这里设成 1）
    # 更宽松，确认体验账号用的是自己的更低上限，不是全局的 AI_DAILY_LIMIT。
    monkeypatch.setenv("TRIAL_AI_DAILY_LIMIT", "1")
    monkeypatch.setattr(
        ai,
        "generate",
        lambda item: {"description": "新题目", "model": "mock-model"},
    )

    client.post("/api/auth/trial", json={"timezone": "Asia/Shanghai"})
    mistake_id = new_problem(client)[0]

    first = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert first.status_code == 201

    second = client.post(f"/api/mistakes/{mistake_id}/variants")
    assert second.status_code == 429
