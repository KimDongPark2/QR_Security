"""개선 5: GET /admin/alerts?minutes=60."""
from __future__ import annotations

from datetime import timedelta

from app.config import now_kst
from app.db import access_logs, get_engine
from test_flow import ADMIN, assert_error, auth_headers, first_order_of, login, pii_strings


def test_alerts_counts_by_driver(client, seeded, demo_data):
    assert_error(client.get("/admin/alerts"), 401, "ADMIN_ONLY")
    assert client.get("/admin/alerts", headers=ADMIN).json()["alerts"] == []

    d2 = auth_headers(client, "D002", "dev2")
    for _ in range(2):  # 남의 건 스캔 2번
        client.get(f"/scan/{first_order_of(seeded, 'D001')['qr_token']}", headers=d2)
    login(client, "D002", "another-device")  # 다른 기기로 로그인 시도
    for _ in range(6):  # 5번 틀리고 6번째는 잠김
        login(client, "D001", password="wrong")
    login(client, "D003")  # NO_ASSIGNMENT_TODAY 는 세지 않는다
    with get_engine().begin() as conn:  # 2시간 전 기록은 60분 창 밖
        conn.execute(access_logs.insert().values(driver_id="D002", order_id=None, action="SCAN",
                                                 result="NOT_ASSIGNED", at=now_kst() - timedelta(hours=2)))

    r = client.get("/admin/alerts?minutes=60", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["minutes"] == 60 and body["since"].endswith("+09:00")
    assert body["alerts"] == [
        {"driver_id": "D001", "NOT_ASSIGNED": 0, "DEVICE_MISMATCH": 0, "INVALID_CREDENTIALS": 5,
         "TOO_MANY_ATTEMPTS": 1, "total": 6},
        {"driver_id": "D002", "NOT_ASSIGNED": 2, "DEVICE_MISMATCH": 1, "INVALID_CREDENTIALS": 0,
         "TOO_MANY_ATTEMPTS": 0, "total": 3},
    ]
    for s in pii_strings(demo_data):
        assert s not in r.text

    wide = client.get("/admin/alerts?minutes=180", headers=ADMIN).json()["alerts"]
    assert next(a for a in wide if a["driver_id"] == "D002")["NOT_ASSIGNED"] == 3
    assert client.get("/admin/alerts?minutes=0", headers=ADMIN).status_code == 422
