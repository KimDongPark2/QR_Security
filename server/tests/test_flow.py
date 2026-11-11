"""SERVER_SPEC §10 흐름 테스트 (1·2단계 범위)."""
from __future__ import annotations

import re
import time

import jwt
from sqlalchemy import func, select

from app.config import get_settings
from app.crypto import decrypt, encrypt
from app.db import access_logs, drivers, get_engine, orders, pii_archive, recipient_pii
from app.masking import mask_name, watermark_text
from app.security import hash_password, verify_password

PII_KEYS = ("recipient_name_masked", "safe_number", "address")


def login(client, driver_id, device="device-A", password="demo1234"):
    return client.post("/auth/login", json={"driver_id": driver_id, "password": password, "device_uuid": device})


def auth_headers(client, driver_id, device="device-A"):
    r = login(client, driver_id, device)
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}", "X-Device-UUID": device}


def first_order_of(seeded, driver_id):
    return next(o for o in seeded if o["assign_to"] == driver_id)


def pii_strings(demo_data):
    out = []
    for o in demo_data["orders"]:
        out += [o["name"], o["phone"], o["address"]]
    return out


def assert_error(r, status, code):
    assert r.status_code == status, r.text
    body = r.json()
    assert set(body) == {"code", "message"}
    assert body["code"] == code


# ---- 기본 ----

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"ok": True}


# ---- §10-1~4 로그인·기기 ----

def test_login_success_shape(client):
    r = login(client, "D001")
    assert r.status_code == 200
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["driver_id"] == "D001" and body["driver_name"] == "김담당" and body["role"] == "DRIVER"
    assert body["today_assignments"] == 4
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T23:59:59\+09:00", body["expires_at"])
    claims = jwt.decode(body["access_token"], get_settings().jwt_secret, algorithms=["HS256"])
    assert claims["sub"] == "D001" and claims["role"] == "DRIVER" and claims["dev"] == "device-A"


def test_login_no_assignment_today(client):  # §10-1
    assert_error(login(client, "D003"), 403, "NO_ASSIGNMENT_TODAY")
    # 거부된 로그인은 기기를 묶지 않는다
    with get_engine().connect() as conn:
        assert conn.execute(select(drivers.c.device_uuid).where(drivers.c.driver_id == "D003")).scalar() is None


def test_hub_login_without_assignment(client):
    assert login(client, "H001").status_code == 200


def test_login_device_binding(client):  # §10-2
    assert login(client, "D001", "device-A").status_code == 200
    assert login(client, "D001", "device-A").status_code == 200  # 같은 기기는 다시 로그인 가능
    assert_error(login(client, "D001", "device-B"), 403, "DEVICE_MISMATCH")


def test_login_invalid_credentials(client):  # §10-3
    assert_error(login(client, "D001", password="wrong"), 401, "INVALID_CREDENTIALS")
    assert_error(login(client, "X999"), 401, "INVALID_CREDENTIALS")


def test_jwt_device_vs_header_mismatch(client, seeded):  # §10-4
    headers = auth_headers(client, "D001", "device-A")
    headers["X-Device-UUID"] = "device-B"
    token = first_order_of(seeded, "D001")["qr_token"]
    assert_error(client.get(f"/scan/{token}", headers=headers), 403, "DEVICE_MISMATCH")


def test_missing_device_header(client, seeded):
    headers = auth_headers(client, "D001")
    del headers["X-Device-UUID"]
    assert_error(client.get(f"/scan/{seeded[0]['qr_token']}", headers=headers), 403, "DEVICE_MISMATCH")


def test_token_invalid_and_expired(client, seeded):
    token = seeded[0]["qr_token"]
    assert_error(client.get(f"/scan/{token}"), 401, "TOKEN_INVALID")
    assert_error(client.get(f"/scan/{token}", headers={"Authorization": "Bearer nope", "X-Device-UUID": "a"}),
                 401, "TOKEN_INVALID")
    forged = jwt.encode({"sub": "D001", "role": "DRIVER", "dev": "device-A", "iat": 1, "exp": 2**31},
                        "wrong-secret-wrong-secret-wrong-secret", algorithm="HS256")
    assert_error(client.get(f"/scan/{token}", headers={"Authorization": f"Bearer {forged}", "X-Device-UUID": "device-A"}),
                 401, "TOKEN_INVALID")
    now = int(time.time())
    expired = jwt.encode({"sub": "D001", "role": "DRIVER", "dev": "device-A", "iat": now - 100, "exp": now - 10},
                         get_settings().jwt_secret, algorithm="HS256")
    assert_error(client.get(f"/scan/{token}", headers={"Authorization": f"Bearer {expired}", "X-Device-UUID": "device-A"}),
                 401, "TOKEN_EXPIRED")


# ---- §10-5~8 스캔 ----

def test_hub_scan(client, seeded):  # §10-5
    headers = auth_headers(client, "H001", "hub-device")
    order = seeded[0]
    r = client.get(f"/scan/{order['qr_token']}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body == {"view": "HUB", "order_no": order["order_no"], "region": "인천 연수구 송도동", "status": "AT_HUB"}
    for k in PII_KEYS:
        assert k not in body


def test_unassigned_driver_scan(client, seeded, demo_data):  # §10-6
    headers = auth_headers(client, "D002", "device-D002")
    r = client.get(f"/scan/{first_order_of(seeded, 'D001')['qr_token']}", headers=headers)
    assert_error(r, 403, "NOT_ASSIGNED")
    for s in pii_strings(demo_data):
        assert s not in r.text
    for k in PII_KEYS:
        assert k not in r.json()


def test_assigned_driver_scan(client, seeded, demo_data):  # §10-7
    headers = auth_headers(client, "D001")
    order = first_order_of(seeded, "D001")
    r = client.get(f"/scan/{order['qr_token']}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    seed_order = demo_data["orders"][0]
    assert body["view"] == "DRIVER"
    assert body["order_no"] == order["order_no"]
    assert body["status"] == "OUT_FOR_DELIVERY"
    assert body["recipient_name_masked"] == "홍O동"
    assert re.fullmatch(r"050-\d{4}-\d{4}", body["safe_number"])
    assert body["address"] == seed_order["address"]
    assert body["watermark"].startswith("D001 김담당 · ")
    # 실제 전화번호와 이름 원문은 응답 어디에도 없다
    assert seed_order["phone"] not in r.text
    assert seed_order["name"] not in r.text


def test_scan_unknown_token(client):
    headers = auth_headers(client, "D001")
    assert_error(client.get("/scan/no-such-token", headers=headers), 404, "ORDER_NOT_FOUND")


def test_pii_encrypted_in_db(client, demo_data):  # §10-8
    with get_engine().connect() as conn:
        rows = conn.execute(select(recipient_pii)).mappings().all()
    names = {decrypt(r["name_enc"]) for r in rows}
    assert names == {o["name"] for o in demo_data["orders"]}
    plain = pii_strings(demo_data)
    for r in rows:
        for col in ("name_enc", "phone_enc", "address_enc"):
            assert r[col] not in plain
            assert all(p not in r[col] for p in plain)


# ---- 트리거 (배송 완료 API는 2단계, 여기서는 DB 트리거만 확인) ----

def test_delivered_trigger_and_410(client, seeded):
    headers = auth_headers(client, "D001")
    order = first_order_of(seeded, "D001")
    with get_engine().begin() as conn:
        oid = conn.execute(select(orders.c.id).where(orders.c.order_no == order["order_no"])).scalar_one()
        conn.execute(orders.update().where(orders.c.id == oid).values(status="DELIVERED"))
    with get_engine().connect() as conn:
        assert conn.execute(select(func.count()).select_from(recipient_pii)
                            .where(recipient_pii.c.order_id == oid)).scalar_one() == 0
        arch = conn.execute(select(pii_archive).where(pii_archive.c.order_id == oid)).mappings().all()
    assert len(arch) == 1 and arch[0]["keep_until"] > arch[0]["archived_at"]

    r = client.get(f"/scan/{order['qr_token']}", headers=headers)
    assert_error(r, 410, "PII_REMOVED")
    assert r.json()["message"] == "배송이 완료되어 개인정보 조회가 차단되었습니다."

    t = client.get(f"/track/{order['order_no']}").json()
    assert t["pii_status"] == "BLOCKED" and t["status_label"] == "배송 완료"


# ---- §10-11 track ----

def test_track_before_delivery(client, seeded, demo_data):
    r = client.get(f"/track/{seeded[0]['order_no']}")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "READY" and body["status_label"] == "발송 준비"
    assert body["delivered_at"] is None
    assert body["pii_status"] == "VIEWABLE_BY_ASSIGNED_DRIVER"
    assert body["pii_message"] == "담당 기사만 볼 수 있습니다."
    for s in pii_strings(demo_data):
        assert s not in r.text
    assert_error(client.get("/track/SC-00000000-0000"), 404, "ORDER_NOT_FOUND")


# ---- §10-13 access_logs ----

def test_access_logs_recorded(client, seeded):
    login(client, "X999")
    login(client, "D003")
    headers = auth_headers(client, "D001")
    client.get(f"/scan/{first_order_of(seeded, 'D001')['qr_token']}", headers=headers)
    client.get(f"/scan/{first_order_of(seeded, 'D002')['qr_token']}", headers=headers)
    with get_engine().connect() as conn:
        logs = conn.execute(select(access_logs.c.driver_id, access_logs.c.action, access_logs.c.result,
                                   access_logs.c.order_id).order_by(access_logs.c.id)).all()
    assert [(l.driver_id, l.action, l.result) for l in logs] == [
        ("X999", "LOGIN", "INVALID_CREDENTIALS"),
        ("D003", "LOGIN", "NO_ASSIGNMENT_TODAY"),
        ("D001", "LOGIN", "OK"),
        ("D001", "SCAN", "OK"),
        ("D001", "SCAN", "NOT_ASSIGNED"),
    ]
    assert logs[3].order_id is not None and logs[4].order_id is not None


# ---- 단위: 암호화·해시·마스킹 ----

def test_crypto_roundtrip_random_nonce():
    a, b = encrypt("홍길동"), encrypt("홍길동")
    assert a != b
    assert decrypt(a) == decrypt(b) == "홍길동"


def test_password_hash_format():
    h = hash_password("demo1234")
    assert h.startswith("pbkdf2_sha256$200000$")
    assert verify_password("demo1234", h)
    assert not verify_password("demo12345", h)
    assert not verify_password("demo1234", "garbage")


def test_mask_name():
    assert mask_name("김철") == "김O"
    assert mask_name("한승일") == "한O일"
    assert mask_name("남궁민준") == "남OO준"


def test_watermark_text():
    from datetime import datetime
    assert watermark_text("D001", "김담당", datetime(2026, 11, 14, 14, 3, 22)) == "D001 김담당 · 2026-11-14 14:03:22"


# ======================= 2단계 =======================

import io

from PIL import Image
from sqlalchemy import text

from app.db import SCHEMA_PATH, assignments
from app.config import get_settings as _gs

ADMIN = {"X-Admin-Token": "test-admin-token"}


def png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def complete(client, headers, qr_token, data=None, content_type="image/png", name="p.png"):
    return client.post(f"/deliveries/{qr_token}/complete", headers=headers,
                       files={"photo": (name, data if data is not None else png_bytes(), content_type)})


def order_row(order_no):
    with get_engine().connect() as conn:
        return conn.execute(select(orders).where(orders.c.order_no == order_no)).mappings().first()


def pii_counts(order_id):
    with get_engine().connect() as conn:
        live = conn.execute(select(func.count()).select_from(recipient_pii)
                            .where(recipient_pii.c.order_id == order_id)).scalar_one()
        arch = conn.execute(select(pii_archive).where(pii_archive.c.order_id == order_id)).mappings().all()
    return live, arch


# ---- §10-9, 10 완료 ----

def test_complete_flow(client, seeded):  # §10-9, 10
    headers = auth_headers(client, "D001")
    order = first_order_of(seeded, "D001")
    assert client.get(f"/scan/{order['qr_token']}", headers=headers).status_code == 200

    r = complete(client, headers, order["qr_token"])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["order_no"] == order["order_no"] and body["status"] == "DELIVERED"
    assert body["pii"] == "REMOVED_FROM_LOOKUP"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+09:00", body["delivered_at"])

    row = order_row(order["order_no"])
    live, arch = pii_counts(row["id"])
    assert live == 0 and len(arch) == 1 and arch[0]["keep_until"] > arch[0]["archived_at"]
    assert row["photo_path"] == f"{order['order_no']}.png"
    assert (_gs().upload_dir / row["photo_path"]).read_bytes() == png_bytes()

    assert_error(client.get(f"/scan/{order['qr_token']}", headers=headers), 410, "PII_REMOVED")
    assert_error(complete(client, headers, order["qr_token"]), 409, "ALREADY_DELIVERED")

    t = client.get(f"/track/{order['order_no']}").json()
    assert t["pii_status"] == "BLOCKED" and t["delivered_at"] == body["delivered_at"]


def test_complete_errors(client, seeded):
    d1 = auth_headers(client, "D001")
    order = first_order_of(seeded, "D001")
    assert_error(complete(client, d1, "no-such-token"), 404, "ORDER_NOT_FOUND")
    assert_error(complete(client, auth_headers(client, "H001", "hub"), order["qr_token"]), 403, "ROLE_FORBIDDEN")
    assert_error(complete(client, auth_headers(client, "D002", "dev2"), order["qr_token"]), 403, "NOT_ASSIGNED")
    assert_error(complete(client, d1, order["qr_token"], b"hello", "text/plain", "a.txt"), 415, "UNSUPPORTED_MEDIA")
    assert_error(complete(client, d1, order["qr_token"], b"not really a png", "image/png"), 415, "UNSUPPORTED_MEDIA")
    big = b"\x89PNG\r\n\x1a\n" + b"\0" * (10 * 1024 * 1024)
    assert_error(complete(client, d1, order["qr_token"], big), 413, "FILE_TOO_LARGE")
    # 실패한 시도로는 상태가 바뀌지 않고 사진도 남지 않는다
    row = order_row(order["order_no"])
    assert row["status"] == "READY" and row["photo_path"] is None
    up = _gs().upload_dir
    assert not up.exists() or not any(up.iterdir())


def _restore_trigger():
    script = SCHEMA_PATH.read_text(encoding="utf-8")
    trigger_sql = script[script.index("CREATE TRIGGER"):]
    raw = get_engine().raw_connection()
    try:
        raw.driver_connection.executescript(trigger_sql)
        raw.commit()
    finally:
        raw.close()


def test_complete_without_trigger_rolls_back(client, seeded):  # §10-15
    headers = auth_headers(client, "D001")
    order = first_order_of(seeded, "D001")
    with get_engine().begin() as conn:
        conn.execute(text("DROP TRIGGER trg_orders_delivered"))
    try:
        assert_error(complete(client, headers, order["qr_token"]), 500, "PII_NOT_REMOVED")
        row = order_row(order["order_no"])
        assert row["status"] == "READY" and row["delivered_at"] is None and row["photo_path"] is None
        live, arch = pii_counts(row["id"])
        assert live == 1 and arch == []
        assert not (_gs().upload_dir / f"{order['order_no']}.png").exists()
    finally:
        _restore_trigger()
    assert complete(client, headers, order["qr_token"]).status_code == 200


# ---- /me/assignments ----

def test_me_assignments(client, seeded):
    r = client.get("/me/assignments", headers=auth_headers(client, "D001"))
    assert r.status_code == 200
    items = r.json()
    assert [i["order_no"] for i in items] == sorted(o["order_no"] for o in seeded if o["assign_to"] == "D001")
    assert all(set(i) == {"order_no", "region", "status"} for i in items)


# ---- /orders, /qr, /labels ----

def test_create_order(client, demo_data):
    payload = {"name": "강감찬", "phone": "010-0000-0099", "address": "가상로 999", "region": "인천 연수구 송도동"}
    assert_error(client.post("/orders", json=payload), 401, "ADMIN_ONLY")
    r = client.post("/orders", json=payload, headers=ADMIN)
    assert r.status_code == 201, r.text
    body = r.json()
    assert re.fullmatch(r"SC-\d{8}-0007", body["order_no"])
    assert body["label_url"] == f"/labels/{body['order_no']}" and body["qr_url"] == f"/qr/{body['order_no']}"
    for s in payload.values():
        if s != payload["region"]:
            assert s not in r.text

    row = order_row(body["order_no"])
    assert row["status"] == "READY" and row["qr_token"] == body["qr_token"]
    with get_engine().connect() as conn:
        pii = conn.execute(select(recipient_pii).where(recipient_pii.c.order_id == row["id"])).mappings().one()
    assert decrypt(pii["name_enc"]) == "강감찬" and decrypt(pii["phone_enc"]) == "010-0000-0099"
    assert re.fullmatch(r"050-\d{4}-\d{4}", pii["safe_number"])


def test_qr_png(client, seeded):
    r = client.get(f"/qr/{seeded[0]['order_no']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG")
    assert_error(client.get("/qr/SC-00000000-0000"), 404, "ORDER_NOT_FOUND")


def test_label_has_no_pii(client, seeded, demo_data):  # §10-12 (라벨)
    order = seeded[0]
    r = client.get(f"/labels/{order['order_no']}")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "seungcurity 안심 운송장" in r.text
    assert f'src="/qr/{order["order_no"]}"' in r.text
    assert order["order_no"] in r.text
    assert "이 운송장에는 개인정보가 인쇄되지 않습니다." in r.text
    for s in pii_strings(demo_data) + [o["region"] for o in demo_data["orders"]]:
        assert s not in r.text
    assert order["qr_token"] not in r.text
    assert_error(client.get("/labels/SC-00000000-0000"), 404, "ORDER_NOT_FOUND")


# ---- 관리자 ----

def test_admin_requires_token(client):  # §10-14 (앞부분)
    for method, path in [("post", "/admin/reset-demo"), ("post", "/admin/drivers/D001/reset-device"),
                         ("get", "/admin/logs"), ("get", "/admin/db-peek/x")]:
        assert_error(getattr(client, method)(path), 401, "ADMIN_ONLY")
        assert_error(getattr(client, method)(path, headers={"X-Admin-Token": "wrong"}), 401, "ADMIN_ONLY")


def test_admin_reset_demo(client, seeded):  # §10-14
    headers = auth_headers(client, "D001")
    order = first_order_of(seeded, "D001")
    assert complete(client, headers, order["qr_token"]).status_code == 200

    r = client.post("/admin/reset-demo", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and len(body["orders"]) == 6 and body["photos_removed"] == 1
    with get_engine().connect() as conn:
        assert conn.execute(select(func.count()).select_from(recipient_pii)).scalar_one() == 6
        assert conn.execute(select(func.count()).select_from(pii_archive)).scalar_one() == 0
        assert conn.execute(select(func.count()).select_from(access_logs)).scalar_one() == 0
        assert set(conn.execute(select(orders.c.status)).scalars()) == {"READY"}
        assert set(conn.execute(select(drivers.c.device_uuid)).scalars()) == {None}
    assert not [p for p in _gs().upload_dir.iterdir() if p.is_file()]
    # 기기 묶음도 풀려서 다른 기기로 로그인 가능
    assert login(client, "D001", "device-B").status_code == 200


def test_admin_assign_and_reset_device(client, seeded):
    target = first_order_of(seeded, "D002")["order_no"]
    assert_error(login(client, "D003"), 403, "NO_ASSIGNMENT_TODAY")
    r = client.post("/admin/assign", json={"driver_id": "D003", "order_no": target}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["previous_driver_id"] == "D002"
    assert login(client, "D003", "d3").status_code == 200
    with get_engine().connect() as conn:
        assert conn.execute(select(func.count()).select_from(assignments)
                            .where(assignments.c.driver_id == "D002")).scalar_one() == 1

    assert_error(client.post("/admin/assign", json={"driver_id": "X999", "order_no": target}, headers=ADMIN),
                 404, "DRIVER_NOT_FOUND")
    assert_error(client.post("/admin/assign", json={"driver_id": "H001", "order_no": target}, headers=ADMIN),
                 403, "ROLE_FORBIDDEN")
    assert_error(client.post("/admin/assign", json={"driver_id": "D001", "order_no": "nope"}, headers=ADMIN),
                 404, "ORDER_NOT_FOUND")

    assert_error(login(client, "D003", "other"), 403, "DEVICE_MISMATCH")
    assert client.post("/admin/drivers/D003/reset-device", headers=ADMIN).json() == {"driver_id": "D003", "device_uuid": None}
    assert login(client, "D003", "other").status_code == 200
    assert_error(client.post("/admin/drivers/X999/reset-device", headers=ADMIN), 404, "DRIVER_NOT_FOUND")


def test_admin_logs_no_pii(client, seeded, demo_data):  # §10-12 (로그), §10-13
    headers = auth_headers(client, "D001")
    order = first_order_of(seeded, "D001")
    client.get(f"/scan/{order['qr_token']}", headers=headers)
    complete(client, headers, order["qr_token"])
    r = client.get("/admin/logs?limit=50", headers=ADMIN)
    assert r.status_code == 200
    logs = r.json()
    assert [(l["action"], l["result"]) for l in logs] == [("COMPLETE", "OK"), ("SCAN", "OK"), ("LOGIN", "OK")]
    assert logs[0]["order_no"] == order["order_no"] and logs[0]["at"].endswith("+09:00")
    for s in pii_strings(demo_data):
        assert s not in r.text
    assert len(client.get("/admin/logs?limit=1", headers=ADMIN).json()) == 1


def test_admin_db_peek_ciphertext_only(client, seeded, demo_data):
    order = first_order_of(seeded, "D001")
    r = client.get(f"/admin/db-peek/{order['order_no']}", headers=ADMIN)
    assert r.status_code == 200
    body = r.json()
    assert decrypt(body["recipient_pii"]["name_enc"]) == demo_data["orders"][0]["name"]
    assert body["pii_archive"] == []
    for s in pii_strings(demo_data):
        assert s not in r.text

    complete(client, auth_headers(client, "D001"), order["qr_token"])
    body = client.get(f"/admin/db-peek/{order['order_no']}", headers=ADMIN).json()
    assert body["recipient_pii"] is None and len(body["pii_archive"]) == 1
    assert_error(client.get("/admin/db-peek/nope", headers=ADMIN), 404, "ORDER_NOT_FOUND")


# ---- 422: input·ctx 제거 (CHANGES.md 1) ----

def _assert_clean_422(r, secrets_):
    assert r.status_code == 422, r.text
    body = r.json()
    assert set(body) == {"detail"} and body["detail"]
    for item in body["detail"]:
        assert set(item) == {"type", "loc", "msg"}
    for s in secrets_:
        assert s not in r.text


def test_422_orders_missing_field_no_pii(client):
    payload = {"name": "강감찬", "phone": "010-0000-0099", "address": "가상로 999"}  # region 누락
    r = client.post("/orders", json=payload, headers=ADMIN)
    _assert_clean_422(r, payload.values())
    assert ["body", "region"] in [i["loc"] for i in r.json()["detail"]]


def test_422_login_missing_device_no_password(client):
    r = client.post("/auth/login", json={"driver_id": "D001", "password": "SuperSecret-Pw-123"})
    _assert_clean_422(r, ["SuperSecret-Pw-123"])
    assert ["body", "device_uuid"] in [i["loc"] for i in r.json()["detail"]]


def test_422_broken_json_no_echo(client):
    raw = '{"name": "강감찬", "phone": "010-0000-0099", "address": "가상로 999",'.encode("utf-8")
    r = client.post("/orders", content=raw, headers={**ADMIN, "Content-Type": "application/json"})
    _assert_clean_422(r, ["강감찬", "010-0000-0099", "가상로 999"])
    r = client.post("/auth/login", content=b'{"driver_id": "D001", "password": "SuperSecret-Pw-123"',
                    headers={"Content-Type": "application/json"})
    _assert_clean_422(r, ["SuperSecret-Pw-123"])
