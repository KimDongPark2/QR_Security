"""엔진, 테이블 정의(SQLAlchemy Core), 공용 도우미.

테이블·열 이름은 SERVER_SPEC §4(주승윤과의 약속) 그대로다.
SQLite 스키마는 db/sqlite_schema.sql, MySQL 스키마는 ../db/schema.sql 이 만든다.
여기의 Table 정의는 쿼리용이며 create_all은 쓰지 않는다.
"""
from __future__ import annotations

import secrets
from datetime import date
from functools import lru_cache

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    func,
    inspect,
    select,
)
from sqlalchemy.engine import Connection, Engine

from .config import BASE_DIR, get_settings, now_kst, today_kst

SCHEMA_PATH = BASE_DIR / "db" / "sqlite_schema.sql"

metadata = MetaData()

drivers = Table(
    "drivers", metadata,
    Column("driver_id", String(10), primary_key=True),
    Column("name", String(20), nullable=False),
    Column("password_hash", String(200), nullable=False),
    Column("role", String(10), nullable=False),
    Column("device_uuid", String(64), nullable=True),
)

orders = Table(
    "orders", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("order_no", String(20), nullable=False, unique=True),
    Column("qr_token", String(32), nullable=False, unique=True),
    Column("region", String(50), nullable=False),
    Column("status", String(20), nullable=False),
    Column("created_at", DateTime, nullable=False),
    Column("delivered_at", DateTime, nullable=True),
    Column("photo_path", String(200), nullable=True),
)

recipient_pii = Table(
    "recipient_pii", metadata,
    Column("order_id", Integer, ForeignKey("orders.id"), primary_key=True),
    Column("name_enc", Text, nullable=False),
    Column("phone_enc", Text, nullable=False),
    Column("address_enc", Text, nullable=False),
    Column("safe_number", String(20), nullable=False),
)

pii_archive = Table(
    "pii_archive", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("order_id", Integer, nullable=False),
    Column("name_enc", Text, nullable=False),
    Column("phone_enc", Text, nullable=False),
    Column("address_enc", Text, nullable=False),
    Column("archived_at", DateTime, nullable=False),
    Column("keep_until", DateTime, nullable=False),
)

assignments = Table(
    "assignments", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("driver_id", String(10), ForeignKey("drivers.driver_id"), nullable=False),
    Column("order_id", Integer, ForeignKey("orders.id"), nullable=False),
    Column("work_date", Date, nullable=False),
    UniqueConstraint("order_id", "work_date"),
)

access_logs = Table(
    "access_logs", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("driver_id", String(10), nullable=False),  # FK 없음: 없는 ID의 로그인 시도도 남긴다
    Column("order_id", Integer, nullable=True),
    Column("action", String(20), nullable=False),
    Column("result", String(30), nullable=False),
    Column("at", DateTime, nullable=False),
)

# FK 순서대로 지울 때 쓰는 순서(자식 → 부모)
DELETE_ORDER = (access_logs, assignments, pii_archive, recipient_pii, orders, drivers)


@lru_cache
def get_engine() -> Engine:
    url = get_settings().database_url
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_recycle=3600)
    return engine


def is_sqlite(engine: Engine) -> bool:
    return engine.dialect.name == "sqlite"


def ensure_sqlite_schema(engine: Engine) -> bool:
    """SQLite이고 테이블이 아직 없으면 db/sqlite_schema.sql을 적용한다. MySQL은 건드리지 않는다."""
    if not is_sqlite(engine) or inspect(engine).has_table("orders"):
        return False
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    raw = engine.raw_connection()
    try:
        raw.driver_connection.executescript(script)
        raw.commit()
    finally:
        raw.close()
    return True


def log_access(driver_id: str, order_id: int | None, action: str, result: str) -> None:
    """access_logs 기록. 요청 처리 트랜잭션이 롤백돼도 남도록 별도 트랜잭션으로 쓴다."""
    with get_engine().begin() as conn:
        conn.execute(access_logs.insert().values(
            driver_id=(driver_id or "")[:10],
            order_id=order_id,
            action=action,
            result=result[:30],
            at=now_kst(),
        ))


# ---- 주문 생성 도우미 (seed, POST /orders 공용) ----

def next_order_no(conn: Connection, day: date) -> str:
    """SC-YYYYMMDD-NNNN (KST 날짜, 그날 순번 4자리)."""
    prefix = f"SC-{day:%Y%m%d}-"
    last = conn.execute(
        select(func.max(orders.c.order_no)).where(orders.c.order_no.like(prefix + "%"))
    ).scalar()
    seq = int(last[len(prefix):]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def new_qr_token() -> str:
    return secrets.token_urlsafe(16)


def new_safe_number() -> str:
    return f"050-{secrets.randbelow(10000):04d}-{secrets.randbelow(10000):04d}"


def insert_order(conn: Connection, *, name: str, phone: str, address: str, region: str) -> dict:
    """orders + recipient_pii(암호문) 한 건을 넣는다. {"id", "order_no", "qr_token"}을 돌려준다."""
    from .crypto import encrypt  # crypto → config 만 의존, 순환 없음

    order_no = next_order_no(conn, today_kst())
    qr_token = new_qr_token()
    order_id = conn.execute(orders.insert().values(
        order_no=order_no,
        qr_token=qr_token,
        region=region,
        status="READY",
        created_at=now_kst(),
    )).inserted_primary_key[0]
    conn.execute(recipient_pii.insert().values(
        order_id=order_id,
        name_enc=encrypt(name),
        phone_enc=encrypt(phone),
        address_enc=encrypt(address),
        safe_number=new_safe_number(),
    ))
    return {"id": order_id, "order_no": order_no, "qr_token": qr_token}
