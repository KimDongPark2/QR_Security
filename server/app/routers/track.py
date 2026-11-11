"""GET /track/{order_no} — 주문자 웹 (SERVER_SPEC §7.9). 개인정보 없음."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from ..config import iso_kst
from ..db import get_engine, orders
from ..errors import ApiError

router = APIRouter(tags=["track"])

STATUS_LABELS = {
    "READY": "발송 준비",
    "AT_HUB": "허브 도착",
    "OUT_FOR_DELIVERY": "배송 중",
    "DELIVERED": "배송 완료",
}


@router.get("/track/{order_no}")
def track(order_no: str):
    with get_engine().connect() as conn:
        row = conn.execute(
            select(orders.c.order_no, orders.c.status, orders.c.delivered_at)
            .where(orders.c.order_no == order_no)
        ).mappings().first()
    if row is None:
        raise ApiError(404, "ORDER_NOT_FOUND")

    delivered = row["status"] == "DELIVERED"
    return {
        "order_no": row["order_no"],
        "status": row["status"],
        "status_label": STATUS_LABELS.get(row["status"], row["status"]),
        "delivered_at": iso_kst(row["delivered_at"]),
        "pii_status": "BLOCKED" if delivered else "VIEWABLE_BY_ASSIGNED_DRIVER",
        "pii_message": "개인정보 조회가 차단되었습니다." if delivered else "담당 기사만 볼 수 있습니다.",
    }
