"""GET /scan/{qr_token} (SERVER_SPEC §7.3)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select

from ..config import now_kst, today_kst
from ..crypto import decrypt
from ..db import assignments, get_engine, log_access, orders, recipient_pii
from ..errors import ApiError
from ..masking import mask_name, watermark_text
from ..security import current_driver

router = APIRouter(tags=["scan"])


def _scan(conn, qr_token: str, driver: dict, ctx: dict) -> dict:
    order = conn.execute(select(orders).where(orders.c.qr_token == qr_token)).mappings().first()
    if order is None:
        raise ApiError(404, "ORDER_NOT_FOUND")
    ctx["order_id"] = order["id"]

    if order["status"] == "DELIVERED":
        raise ApiError(410, "PII_REMOVED")

    if driver["role"] == "HUB":
        status = order["status"]
        if status == "READY":
            conn.execute(
                orders.update().where(orders.c.id == order["id"], orders.c.status == "READY")
                .values(status="AT_HUB")
            )
            status = "AT_HUB"
        # 허브 응답에는 개인정보 관련 키를 아예 넣지 않는다.
        return {"view": "HUB", "order_no": order["order_no"], "region": order["region"], "status": status}

    assigned = conn.execute(
        select(assignments.c.id).where(
            assignments.c.order_id == order["id"],
            assignments.c.driver_id == driver["driver_id"],
            assignments.c.work_date == today_kst(),
        )
    ).first()
    if driver["role"] != "DRIVER" or assigned is None:
        raise ApiError(403, "NOT_ASSIGNED")

    pii = conn.execute(
        select(recipient_pii).where(recipient_pii.c.order_id == order["id"])
    ).mappings().first()
    if pii is None:
        raise ApiError(410, "PII_REMOVED")

    status = order["status"]
    if status in ("READY", "AT_HUB"):
        conn.execute(
            orders.update()
            .where(orders.c.id == order["id"], orders.c.status.in_(("READY", "AT_HUB")))
            .values(status="OUT_FOR_DELIVERY")
        )
        status = "OUT_FOR_DELIVERY"

    # phone_enc(실제 전화번호)는 복호화하지도, 내보내지도 않는다.
    return {
        "view": "DRIVER",
        "order_no": order["order_no"],
        "region": order["region"],
        "status": status,
        "recipient_name_masked": mask_name(decrypt(pii["name_enc"])),
        "safe_number": pii["safe_number"],
        "address": decrypt(pii["address_enc"]),
        "watermark": watermark_text(driver["driver_id"], driver["name"], now_kst()),
    }


@router.get("/scan/{qr_token}")
def scan(qr_token: str, driver: dict = Depends(current_driver)):
    ctx: dict = {"order_id": None}
    try:
        with get_engine().begin() as conn:
            result = _scan(conn, qr_token, driver, ctx)
    except ApiError as e:
        log_access(driver["driver_id"], ctx["order_id"], "SCAN", e.code)
        raise
    log_access(driver["driver_id"], ctx["order_id"], "SCAN", "OK")
    return result
