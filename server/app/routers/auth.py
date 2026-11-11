"""POST /auth/login (SERVER_SPEC §7.2)."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import today_kst
from ..db import assignments, drivers, get_engine, log_access
from ..errors import ApiError
from ..security import DUMMY_PASSWORD_HASH, create_access_token, verify_password

router = APIRouter(tags=["auth"])


class LoginRequest(BaseModel):
    driver_id: str = Field(examples=["D001"])
    password: str = Field(examples=["demo1234"])
    device_uuid: str = Field(min_length=1, max_length=64, examples=["demo-device-a"])


def _attempt(body: LoginRequest) -> tuple[str | None, dict | None]:
    """(오류 코드, 성공 응답) 중 하나를 돌려준다."""
    with get_engine().begin() as conn:
        row = conn.execute(select(drivers).where(drivers.c.driver_id == body.driver_id)).mappings().first()
        if row is None:
            verify_password(body.password, DUMMY_PASSWORD_HASH)
            return "INVALID_CREDENTIALS", None
        if not verify_password(body.password, row["password_hash"]):
            return "INVALID_CREDENTIALS", None

        today_count = conn.execute(
            select(func.count()).select_from(assignments).where(
                assignments.c.driver_id == row["driver_id"],
                assignments.c.work_date == today_kst(),
            )
        ).scalar_one()
        if row["role"] == "DRIVER" and today_count == 0:
            return "NO_ASSIGNMENT_TODAY", None

        if not row["device_uuid"]:
            # 처음 로그인한 기기에 묶는다. 동시에 두 기기가 시도하면 먼저 쓴 쪽만 성공.
            bound = conn.execute(
                drivers.update()
                .where(drivers.c.driver_id == row["driver_id"], drivers.c.device_uuid.is_(None))
                .values(device_uuid=body.device_uuid)
            ).rowcount
            if not bound:
                current = conn.execute(
                    select(drivers.c.device_uuid).where(drivers.c.driver_id == row["driver_id"])
                ).scalar()
                if current != body.device_uuid:
                    return "DEVICE_MISMATCH", None
        elif row["device_uuid"] != body.device_uuid:
            return "DEVICE_MISMATCH", None

        token, exp = create_access_token(row["driver_id"], row["role"], body.device_uuid)
        return None, {
            "access_token": token,
            "token_type": "bearer",
            "driver_id": row["driver_id"],
            "driver_name": row["name"],
            "role": row["role"],
            "expires_at": exp.isoformat(),
            "today_assignments": today_count,
        }


STATUS_BY_CODE = {"INVALID_CREDENTIALS": 401, "NO_ASSIGNMENT_TODAY": 403, "DEVICE_MISMATCH": 403}


@router.post("/auth/login")
def login(body: LoginRequest):
    code, payload = _attempt(body)
    log_access(body.driver_id, None, "LOGIN", code or "OK")
    if code:
        raise ApiError(STATUS_BY_CODE[code], code)
    return payload
