"""Run owned record drafts through a separately configured sandbox service."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, StringConstraints, field_validator

import code_runner
import main

router = APIRouter()


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    language: Literal["Python", "C++"]
    code: Annotated[str, StringConstraints(strict=True, min_length=1, max_length=40000)]
    stdin: Annotated[str, StringConstraints(strict=True, max_length=10000)] = ""

    @field_validator("code")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("请先填写要运行的代码")
        return value

    @field_validator("code", "stdin")
    @classmethod
    def valid_utf8(cls, value):
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("代码和输入必须是有效的 UTF-8 文本") from None
        return value


@router.get("/api/code-runner")
def capabilities(user=Depends(main.current_user)):
    return {"configured": code_runner.configured(), "languages": list(code_runner.LANGUAGES),
            "allowed": not user["is_trial"], "cpu_seconds": 2, "memory_kb": 128000}


@router.post("/api/mistakes/{mistake_id}/run")
def run_draft(mistake_id: int, data: RunInput, request: Request, user=Depends(main.current_user)):
    if not 0 < mistake_id <= 9223372036854775807:
        raise HTTPException(404, "错题不存在")
    with main.connect() as conn:
        main.sec_recheck_session(conn, user["id"], request)
        latest = main.rvb_account(conn, user["id"])
        main.require_not_trial(latest, "运行代码")
        row = conn.execute("SELECT m.id FROM mistakes m JOIN problems p ON p.id = m.problem_id "
                           "WHERE m.id = ? AND p.user_id = ?", (mistake_id, user["id"])).fetchone()
    if row is None:
        raise HTTPException(404, "错题不存在")
    if main.rate_limited(f"code-run:{user['id']}", 6, 60):
        raise HTTPException(429, "运行过于频繁，请稍后再试")
    try:
        return code_runner.run(data.language, data.code, data.stdin)
    except code_runner.RunnerBusy as error:
        raise HTTPException(429, str(error)) from None
    except code_runner.RunnerUnavailable as error:
        raise HTTPException(503, str(error)) from None
