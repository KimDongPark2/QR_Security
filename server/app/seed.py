"""시드: python -m app.seed  (SERVER_SPEC §8)

- SQLite이고 테이블이 없으면 db/sqlite_schema.sql 을 먼저 적용한다(MySQL은 만들지 않는다).
- 모든 행을 지우고(FK 순서) DEMO_DATA 로 다시 채운다.
- 비밀번호는 해시, 개인정보는 암호화. assign_to 가 있으면 오늘(KST)로 배정.
"""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.engine import Engine

from .config import get_settings, today_kst
from .db import DELETE_ORDER, assignments, drivers, ensure_sqlite_schema, get_engine, insert_order
from .security import hash_password


def load_demo_data(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def reset_and_seed(engine: Engine, data: dict) -> list[dict]:
    """모든 행을 지우고 data로 채운다. 만든 주문의 (order_no, qr_token, assign_to) 목록을 돌려준다."""
    ensure_sqlite_schema(engine)
    today = today_kst()
    created: list[dict] = []

    with engine.begin() as conn:
        for table in DELETE_ORDER:
            conn.execute(table.delete())

        for d in data["drivers"]:
            conn.execute(drivers.insert().values(
                driver_id=d["driver_id"],
                name=d["name"],
                password_hash=hash_password(d["password"]),
                role=d["role"],
                device_uuid=None,
            ))

        for o in data["orders"]:
            order = insert_order(conn, name=o["name"], phone=o["phone"], address=o["address"], region=o["region"])
            if o.get("assign_to"):
                conn.execute(assignments.insert().values(
                    driver_id=o["assign_to"], order_id=order["id"], work_date=today,
                ))
            created.append({"order_no": order["order_no"], "qr_token": order["qr_token"],
                            "assign_to": o.get("assign_to")})

    return created


def main() -> None:
    settings = get_settings()
    data = load_demo_data(settings.demo_data)
    created = reset_and_seed(get_engine(), data)

    # 개인정보는 출력하지 않는다(주문번호·토큰·배정만).
    print(f"시드 완료: 기사 {len(data['drivers'])}명, 주문 {len(created)}건 (배정일 {today_kst()})")
    print(f"데이터 파일: {settings.demo_data.name}")
    for c in created:
        print(f"  {c['order_no']}  qr_token={c['qr_token']}  배정={c['assign_to'] or '-'}")


if __name__ == "__main__":
    main()
