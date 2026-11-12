"""개선 3: 로그인 시도 제한 (429 TOO_MANY_ATTEMPTS)."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from app.config import now_kst
from app.db import access_logs, drivers, get_engine
from app.routers import auth
from test_flow import ADMIN, assert_error, login


def _fail(client, driver_id="D001", times=1):
    for _ in range(times):
        assert_error(login(client, driver_id, password="wrong"), 401, "INVALID_CREDENTIALS")


def test_locked_after_five_failures_even_with_correct_password(client, monkeypatch):
    _fail(client, times=5)

    r = login(client, "D001")  # 맞는 비밀번호
    assert_error(r, 429, "TOO_MANY_ATTEMPTS")
    assert r.json()["message"] == "로그인 시도가 너무 많습니다. 10분 뒤에 다시 시도하세요."
    assert_error(login(client, "D001", password="wrong"), 429, "TOO_MANY_ATTEMPTS")

    # 잠긴 동안의 시도는 기기를 묶지 않고, 로그에는 TOO_MANY_ATTEMPTS로 남는다
    with get_engine().connect() as conn:
        assert conn.execute(select(drivers.c.device_uuid).where(drivers.c.driver_id == "D001")).scalar() is None
        results = list(conn.execute(select(access_logs.c.result).order_by(access_logs.c.id)).scalars())
    assert results == ["INVALID_CREDENTIALS"] * 5 + ["TOO_MANY_ATTEMPTS"] * 2

    # 다른 기사는 영향 없음
    assert login(client, "D002", "dev2").status_code == 200

    # 9분 뒤에도 잠김, 11분 뒤에는 다시 로그인 가능
    monkeypatch.setattr(auth, "now_kst", lambda: now_kst() + timedelta(minutes=9))
    assert_error(login(client, "D001"), 429, "TOO_MANY_ATTEMPTS")
    monkeypatch.setattr(auth, "now_kst", lambda: now_kst() + timedelta(minutes=11))
    assert login(client, "D001").status_code == 200


def test_success_resets_failure_count(client):
    _fail(client, times=4)
    assert login(client, "D001").status_code == 200
    _fail(client, times=4)  # 성공 이후 4번이므로 아직 잠기지 않는다
    assert login(client, "D001").status_code == 200
    _fail(client, times=5)
    assert_error(login(client, "D001"), 429, "TOO_MANY_ATTEMPTS")


def test_unknown_id_is_limited_too(client):
    _fail(client, "X999", times=5)
    assert_error(login(client, "X999", password="wrong"), 429, "TOO_MANY_ATTEMPTS")


def test_reset_demo_unlocks(client):
    _fail(client, times=5)
    assert_error(login(client, "D001"), 429, "TOO_MANY_ATTEMPTS")
    assert client.post("/admin/reset-demo", headers=ADMIN).status_code == 200
    assert login(client, "D001").status_code == 200
