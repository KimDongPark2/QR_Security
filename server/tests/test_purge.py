"""개선 2: 보존 기간 만료 파기 (POST /admin/purge-expired, python -m app.purge)."""
from __future__ import annotations

import re
from datetime import timedelta

from sqlalchemy import select

from app import purge
from app.config import now_kst
from app.db import access_logs, get_engine, pii_archive
from test_flow import ADMIN, assert_error, auth_headers, complete, first_order_of


def _add_archive(order_id: int, keep_until):
    with get_engine().begin() as conn:
        conn.execute(pii_archive.insert().values(
            order_id=order_id, name_enc="x", phone_enc="x", address_enc="x",
            archived_at=keep_until - timedelta(days=365 * 5), keep_until=keep_until,
        ))


def _archive_order_ids() -> list[int]:
    with get_engine().connect() as conn:
        return sorted(conn.execute(select(pii_archive.c.order_id)).scalars())


def test_purge_expired_api(client, seeded):
    # 방금 완료한 건(만료까지 5년 남음) + 만료된 행 2개 + 아직 안 지난 행 1개
    order = first_order_of(seeded, "D001")
    assert complete(client, auth_headers(client, "D001"), order["qr_token"]).status_code == 200
    now = now_kst()
    _add_archive(9001, now - timedelta(seconds=5))
    _add_archive(9002, now - timedelta(days=30))
    _add_archive(9003, now + timedelta(days=1))
    assert len(_archive_order_ids()) == 4

    assert_error(client.post("/admin/purge-expired"), 401, "ADMIN_ONLY")
    assert len(_archive_order_ids()) == 4

    r = client.post("/admin/purge-expired", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["purged"] == 2
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00", body["at"])
    remaining = _archive_order_ids()
    assert 9001 not in remaining and 9002 not in remaining
    assert 9003 in remaining and len(remaining) == 2  # 만료 전 행과 방금 완료한 건은 남는다

    with get_engine().connect() as conn:
        log = conn.execute(select(access_logs).order_by(access_logs.c.id.desc())).mappings().first()
    assert (log["driver_id"], log["action"], log["result"], log["order_id"]) == ("ADMIN", "PURGE", "OK", None)

    # 다시 돌리면 지울 것이 없다
    assert client.post("/admin/purge-expired", headers=ADMIN).json()["purged"] == 0


def test_purge_cli(seeded, capsys):
    now = now_kst()
    _add_archive(9001, now - timedelta(hours=1))
    _add_archive(9002, now + timedelta(hours=1))
    purge.main()
    assert "1건" in capsys.readouterr().out
    assert _archive_order_ids() == [9002]
    with get_engine().connect() as conn:
        actions = list(conn.execute(select(access_logs.c.action)).scalars())
    assert actions == ["PURGE"]
