"""reminder routes (F2 每日复习提醒开关 + 一键退订).

Names living in main's namespace are referenced as ``main.<name>``
(attribute access at call time) so monkeypatch.setattr(main, ...)
in tests keeps affecting the moved code.
"""
import main
import secrets

from fastapi import Depends
from fastapi import HTTPException
from fastapi import APIRouter
from fastapi import Query
from typing import Annotated


router = APIRouter()


class ReminderSetting(main.InputModel):
    opt_in: main.StrictBool


@router.put("/api/me/reminder")
def update_reminder(data: ReminderSetting, user=Depends(main.current_user)):
    # 体验账号也有邮箱字段但注册时 email 为 NULL，不会收到提醒；开关照常可调。
    with main.connect(write=True) as conn:
        main.rvb_account(conn, user["id"])
        conn.execute(
            "UPDATE users SET reminder_opt_in = ? WHERE id = ? AND deleted_at IS NULL",
            (1 if data.opt_in else 0, user["id"]),
        )
    return {"opt_in": bool(data.opt_in)}


@router.get("/api/reminder/unsubscribe")
def unsubscribe(
    token: Annotated[str, Query(max_length=128)],
):
    """有效与无效凭证均返回相同结果，避免枚举；只按不敏感的 ID 查找。"""
    try:
        selector = int(token.split(".", 1)[0])
        if not 0 < selector < 2**63: selector = 0
    except (ValueError, AttributeError):
        selector = 0
    with main.connect(write=True) as conn:
        row = conn.execute("SELECT id, reminder_token FROM users WHERE id = ? AND deleted_at IS NULL", (selector,)).fetchone()
        expected = row["reminder_token"] if row and row["reminder_token"] else "0." + "x" * 32
        valid = secrets.compare_digest(token.encode("utf-8"), expected.encode("utf-8"))
        if valid and row:
            conn.execute("UPDATE users SET reminder_opt_in = 0 WHERE id = ?", (row["id"],))
    return {"ok": True}
