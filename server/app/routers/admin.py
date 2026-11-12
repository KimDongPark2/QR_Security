"""관리자 API (§7.10). 모든 경로에 X-Admin-Token 필요.

개인정보는 복호화하지 않는다. db-peek 은 암호문을 그대로 보여준다.
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from ..config import get_settings, iso_kst, now_kst, today_kst
from ..db import access_logs, assignments, drivers, get_engine, orders, pii_archive, recipient_pii
from ..errors import ApiError
from ..purge import purge_expired
from ..security import require_admin
from ..seed import load_demo_data, reset_and_seed

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.post("/reset-demo")
def reset_demo():
    settings = get_settings()
    data = load_demo_data(settings.demo_data)
    created = reset_and_seed(get_engine(), data)

    removed = 0
    if settings.upload_dir.is_dir():
        for p in settings.upload_dir.iterdir():
            if p.is_file():
                p.unlink()
                removed += 1

    return {"ok": True, "drivers": len(data["drivers"]), "orders": created, "photos_removed": removed}


@router.post("/purge-expired")
def purge_expired_archive():
    """pii_archive 에서 보존 기간(keep_until)이 지난 행을 지운다."""
    return purge_expired()


class AssignRequest(BaseModel):
    driver_id: str = Field(examples=["D001"])
    order_no: str = Field(examples=["SC-20261114-0001"])


@router.post("/assign")
def assign(body: AssignRequest):
    today = today_kst()
    with get_engine().begin() as conn:
        driver = conn.execute(select(drivers.c.role).where(drivers.c.driver_id == body.driver_id)).first()
        if driver is None:
            raise ApiError(404, "DRIVER_NOT_FOUND")
        if driver.role != "DRIVER":
            raise ApiError(403, "ROLE_FORBIDDEN")
        order = conn.execute(
            select(orders.c.id, orders.c.status).where(orders.c.order_no == body.order_no)
        ).first()
        if order is None:
            raise ApiError(404, "ORDER_NOT_FOUND")
        if order.status == "DELIVERED":
            raise ApiError(409, "ALREADY_DELIVERED")

        # UNIQUE(order_id, work_date): 오늘 다른 기사에게 배정돼 있으면 이 기사로 옮긴다.
        existing = conn.execute(
            select(assignments.c.id, assignments.c.driver_id)
            .where(assignments.c.order_id == order.id, assignments.c.work_date == today)
        ).first()
        previous = existing.driver_id if existing else None
        if existing is None:
            conn.execute(assignments.insert().values(driver_id=body.driver_id, order_id=order.id, work_date=today))
        elif existing.driver_id != body.driver_id:
            conn.execute(assignments.update().where(assignments.c.id == existing.id)
                         .values(driver_id=body.driver_id))

    return {"driver_id": body.driver_id, "order_no": body.order_no, "work_date": today.isoformat(),
            "previous_driver_id": previous}


@router.post("/drivers/{driver_id}/reset-device")
def reset_device(driver_id: str):
    with get_engine().begin() as conn:
        n = conn.execute(drivers.update().where(drivers.c.driver_id == driver_id)
                         .values(device_uuid=None)).rowcount
    if not n:
        raise ApiError(404, "DRIVER_NOT_FOUND")
    return {"driver_id": driver_id, "device_uuid": None}


@router.get("/logs")
def logs(limit: int = Query(50, ge=1, le=500)):
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(access_logs, orders.c.order_no)
            .select_from(access_logs.outerjoin(orders, orders.c.id == access_logs.c.order_id))
            .order_by(access_logs.c.id.desc())
            .limit(limit)
        ).mappings().all()
    return [
        {"id": r["id"], "driver_id": r["driver_id"], "order_id": r["order_id"], "order_no": r["order_no"],
         "action": r["action"], "result": r["result"], "at": iso_kst(r["at"])}
        for r in rows
    ]


ALERT_RESULTS = ("NOT_ASSIGNED", "DEVICE_MISMATCH", "INVALID_CREDENTIALS", "TOO_MANY_ATTEMPTS")


@router.get("/alerts")
def alerts(minutes: int = Query(60, ge=1, le=1440)):
    """최근 N분 동안의 의심스러운 시도를 기사별로 센다(access_logs 기준, 개인정보 없음)."""
    since = now_kst() - timedelta(minutes=minutes)
    with get_engine().connect() as conn:
        rows = conn.execute(
            select(access_logs.c.driver_id, access_logs.c.result, func.count().label("n"))
            .where(access_logs.c.at >= since, access_logs.c.result.in_(ALERT_RESULTS))
            .group_by(access_logs.c.driver_id, access_logs.c.result)
        ).all()

    by_driver: dict[str, dict] = {}
    for r in rows:
        entry = by_driver.setdefault(r.driver_id, {"driver_id": r.driver_id, **{k: 0 for k in ALERT_RESULTS}})
        entry[r.result] = r.n
    items = [{**e, "total": sum(e[k] for k in ALERT_RESULTS)} for e in by_driver.values()]
    items.sort(key=lambda e: (-e["total"], e["driver_id"]))
    return {"minutes": minutes, "since": iso_kst(since), "alerts": items}


@router.get("/db-peek/{order_no}")
def db_peek(order_no: str):
    with get_engine().connect() as conn:
        order = conn.execute(
            select(orders.c.id, orders.c.order_no, orders.c.status).where(orders.c.order_no == order_no)
        ).mappings().first()
        if order is None:
            raise ApiError(404, "ORDER_NOT_FOUND")
        pii = conn.execute(select(recipient_pii).where(recipient_pii.c.order_id == order["id"])).mappings().first()
        archive = conn.execute(
            select(pii_archive).where(pii_archive.c.order_id == order["id"]).order_by(pii_archive.c.id)
        ).mappings().all()

    # 암호문 그대로. decrypt 하지 않는다.
    return {
        "order_no": order["order_no"],
        "order_id": order["id"],
        "status": order["status"],
        "recipient_pii": dict(pii) if pii else None,
        "pii_archive": [
            {**dict(a), "archived_at": iso_kst(a["archived_at"]), "keep_until": iso_kst(a["keep_until"])}
            for a in archive
        ],
    }
