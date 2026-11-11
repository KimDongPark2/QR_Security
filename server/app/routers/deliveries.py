"""POST /deliveries/{qr_token}/complete (§7.4), GET /me/assignments (§7.5)."""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy import func, select

from ..config import get_settings, iso_kst, now_kst, today_kst
from ..db import assignments, get_engine, log_access, orders, recipient_pii
from ..errors import ApiError
from ..security import current_driver

router = APIRouter(tags=["deliveries"])

MAX_PHOTO_BYTES = 10 * 1024 * 1024
# content-type → (확장자, 파일 시작 바이트)
PHOTO_TYPES = {
    "image/jpeg": ("jpg", b"\xff\xd8\xff"),
    "image/png": ("png", b"\x89PNG\r\n\x1a\n"),
}


def _read_photo(photo: UploadFile) -> tuple[bytes, str]:
    content_type = (photo.content_type or "").split(";")[0].strip().lower()
    if content_type not in PHOTO_TYPES:
        raise ApiError(415, "UNSUPPORTED_MEDIA")
    ext, magic = PHOTO_TYPES[content_type]
    data = photo.file.read(MAX_PHOTO_BYTES + 1)
    if len(data) > MAX_PHOTO_BYTES:
        raise ApiError(413, "FILE_TOO_LARGE")
    if not data.startswith(magic):
        raise ApiError(415, "UNSUPPORTED_MEDIA")
    return data, ext


def _complete(qr_token: str, photo: UploadFile, driver: dict, ctx: dict) -> dict:
    engine = get_engine()
    with engine.connect() as conn:
        order = conn.execute(select(orders).where(orders.c.qr_token == qr_token)).mappings().first()
        if order is None:
            raise ApiError(404, "ORDER_NOT_FOUND")
        ctx["order_id"] = order["id"]
        if order["status"] == "DELIVERED":
            raise ApiError(409, "ALREADY_DELIVERED")
        if driver["role"] == "HUB":
            raise ApiError(403, "ROLE_FORBIDDEN")
        assigned = conn.execute(
            select(assignments.c.id).where(
                assignments.c.order_id == order["id"],
                assignments.c.driver_id == driver["driver_id"],
                assignments.c.work_date == today_kst(),
            )
        ).first()
        if driver["role"] != "DRIVER" or assigned is None:
            raise ApiError(403, "NOT_ASSIGNED")

    data, ext = _read_photo(photo)

    # 임시 파일에 먼저 쓰고, DB 커밋이 성공한 뒤에만 최종 이름으로 바꾼다.
    upload_dir: Path = get_settings().upload_dir
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{order['order_no']}.{ext}"
    tmp = upload_dir / f".{filename}.{secrets.token_hex(4)}.tmp"
    tmp.write_bytes(data)
    try:
        now = now_kst()
        with engine.begin() as conn:
            updated = conn.execute(
                orders.update()
                .where(orders.c.id == order["id"], orders.c.status != "DELIVERED")
                .values(status="DELIVERED", delivered_at=now, photo_path=filename)
            ).rowcount
            if not updated:  # 동시에 다른 요청이 먼저 완료한 경우
                raise ApiError(409, "ALREADY_DELIVERED")
            # 트리거가 개인정보를 옮겼는지 같은 트랜잭션 안에서 확인. 남아 있으면 예외 → 롤백.
            remaining = conn.execute(
                select(func.count()).select_from(recipient_pii).where(recipient_pii.c.order_id == order["id"])
            ).scalar_one()
            if remaining:
                raise ApiError(500, "PII_NOT_REMOVED")
        os.replace(tmp, upload_dir / filename)
    finally:
        tmp.unlink(missing_ok=True)

    return {
        "order_no": order["order_no"],
        "status": "DELIVERED",
        "delivered_at": iso_kst(now),
        "pii": "REMOVED_FROM_LOOKUP",
    }


@router.post("/deliveries/{qr_token}/complete")
def complete(qr_token: str, photo: UploadFile = File(...), driver: dict = Depends(current_driver)):
    ctx: dict = {"order_id": None}
    try:
        result = _complete(qr_token, photo, driver, ctx)
    except ApiError as e:
        log_access(driver["driver_id"], ctx["order_id"], "COMPLETE", e.code)
        raise
    log_access(driver["driver_id"], ctx["order_id"], "COMPLETE", "OK")
    return result


@router.get("/me/assignments")
def my_assignments(driver: dict = Depends(current_driver)):
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(orders.c.order_no, orders.c.region, orders.c.status)
            .join(assignments, assignments.c.order_id == orders.c.id)
            .where(assignments.c.driver_id == driver["driver_id"], assignments.c.work_date == today_kst())
            .order_by(orders.c.order_no)
        ).mappings().all()
    return [dict(r) for r in rows]
