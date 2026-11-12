"""정량 결과 리포트: python -m app.report [--out report/result.md] [--scans 200]

dev.db를 건드리지 않도록 임시 SQLite에 샘플 데이터(가상)를 넣고 TestClient로 측정한다.
리포트에는 개인정보 값 자체를 쓰지 않고 개수만 쓴다.
"""
from __future__ import annotations

import argparse
import base64
import io
import math
import os
import platform
import secrets
import shutil
import statistics
import tempfile
import time
from datetime import datetime
from pathlib import Path

from .config import BASE_DIR, KST

SAMPLE_DATA = BASE_DIR / "demo" / "demo_data.sample.json"
DEFAULT_OUT = BASE_DIR / "report" / "result.md"
PII_FIELDS = (("name", "이름 원문"), ("phone", "실제 전화"), ("address", "상세 주소"))
LOCAL_NOTE = "노트북 로컬 측정, 네트워크 제외"


def _exposure(text: str, demo: dict) -> dict:
    """응답 본문에 시드의 이름 원문·실제 전화·상세 주소가 나왔는지(값이 아니라 여부만)."""
    return {key: any(o[key] in text for o in demo["orders"]) for key, _ in PII_FIELDS}


def _png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), "white").save(buf, format="PNG")
    return buf.getvalue()


def _count_plaintext_rows(demo: dict) -> list[dict]:
    """각 테이블에서 문자열 열에 평문 개인정보가 들어 있는 행 수."""
    from sqlalchemy import select

    from .crypto import decrypt
    from .db import access_logs, get_engine, orders, pii_archive, recipient_pii

    plains = [o[key] for o in demo["orders"] for key, _ in PII_FIELDS]
    names = {o["name"] for o in demo["orders"]}
    out = []
    with get_engine().connect() as conn:
        for table in (recipient_pii, pii_archive, orders, access_logs):
            rows = conn.execute(select(table)).mappings().all()
            bad = sum(
                1 for r in rows
                if any(isinstance(v, str) and any(p in v for p in plains) for v in r.values())
            )
            entry = {"table": table.name, "rows": len(rows), "plaintext_rows": bad, "decryptable": None}
            if table.name in ("recipient_pii", "pii_archive"):
                # 암호문이 진짜 암호문인지: 키로 풀면 원래 값이 나오는 행 수(값은 기록하지 않는다)
                entry["decryptable"] = sum(1 for r in rows if decrypt(r["name_enc"]) in names)
            out.append(entry)
    return out


def measure(scans: int = 200, crypto_rounds: int = 2000) -> dict:
    """현재 설정된 DB를 샘플 데이터로 초기화하고 측정한다(main()이 임시 DB로 바꿔 둔다)."""
    import jwt
    from fastapi.testclient import TestClient

    from .crypto import decrypt, encrypt
    from .db import get_engine
    from .main import app
    from .seed import load_demo_data, reset_and_seed

    demo = load_demo_data(SAMPLE_DATA)
    created = reset_and_seed(get_engine(), demo)
    target = next(o for o in created if o["assign_to"] == "D001")["qr_token"]
    perf_target = [o for o in created if o["assign_to"] == "D001"][1]["qr_token"]
    password = demo["drivers"][0]["password"]

    scenarios: list[dict] = []
    exposures: dict[str, dict] = {}

    def record(key: str, label: str, resp, note: str = "") -> None:
        exp = _exposure(resp.text, demo)
        exposures[key] = exp
        code = "-"
        if resp.status_code >= 400:
            try:
                code = resp.json().get("code", "-")
            except ValueError:
                pass
        scenarios.append({"key": key, "label": label, "status": resp.status_code, "code": code,
                          "pii_count": sum(exp.values()), "note": note})

    with TestClient(app) as c:
        def login(driver_id: str, device: str, pw: str = password):
            return c.post("/auth/login", json={"driver_id": driver_id, "password": pw, "device_uuid": device})

        def headers(driver_id: str, device: str) -> dict:
            token = login(driver_id, device).json()["access_token"]
            return {"Authorization": f"Bearer {token}", "X-Device-UUID": device}

        hub, d1, d2 = headers("H001", "hub-device"), headers("D001", "device-d1"), headers("D002", "device-d2")

        record("hub", "허브 스캔", c.get(f"/scan/{target}", headers=hub), "권역만 표시")
        record("assigned", "담당 기사 스캔", c.get(f"/scan/{target}", headers=d1),
               "이름은 가림, 전화는 안심번호, 주소만 표시")
        record("unassigned", "비담당 기사 스캔", c.get(f"/scan/{target}", headers=d2))
        record("device", "등록 안 된 기기", c.get(f"/scan/{target}", headers={**d1, "X-Device-UUID": "other-device"}),
               "담당 기사의 토큰을 다른 기기에서 사용")

        # (d) 스캔 응답 시간: 담당 기사가 아직 배송 전인 건을 반복 스캔
        timings = []
        for _ in range(scans):
            t0 = time.perf_counter()
            r = c.get(f"/scan/{perf_target}", headers=d1)
            timings.append((time.perf_counter() - t0) * 1000)
            assert r.status_code == 200

        done = c.post(f"/deliveries/{target}/complete", headers=d1, files={"photo": ("p.png", _png(), "image/png")})
        assert done.status_code == 200
        record("delivered", "배송 완료 후 재스캔", c.get(f"/scan/{target}", headers=d1), "담당 기사가 다시 스캔")
        record("no_assignment", "오늘 배정 없는 기사 로그인", login("D003", "device-d3"))
        for _ in range(5):
            login("D002", "device-d2", "wrong-password")
        record("lockout", "비밀번호 6회 틀림", login("D002", "device-d2", "wrong-password"),
               "1~5회는 401 INVALID_CREDENTIALS, 6회째 결과")
        forged = jwt.encode({"sub": "D001", "role": "DRIVER", "dev": "device-d1", "iat": 1, "exp": 2**31},
                            secrets.token_urlsafe(48), algorithm="HS256")
        record("forged", "위조 토큰", c.get(f"/scan/{perf_target}",
                                         headers={"Authorization": f"Bearer {forged}", "X-Device-UUID": "device-d1"}),
               "다른 키로 서명한 JWT")

    sample = demo["orders"][0]["address"]
    t0 = time.perf_counter()
    tokens = [encrypt(sample) for _ in range(crypto_rounds)]
    enc_ms = (time.perf_counter() - t0) * 1000 / crypto_rounds
    t0 = time.perf_counter()
    for token in tokens:
        decrypt(token)
    dec_ms = (time.perf_counter() - t0) * 1000 / crypto_rounds

    ordered = sorted(timings)
    return {
        "measured_at": datetime.now(KST).replace(microsecond=0).isoformat(),
        "python": platform.python_version(),
        "os": f"{platform.system()} {platform.release()}",
        "counts": {"drivers": len(demo["drivers"]), "orders": len(demo["orders"])},
        "scenarios": scenarios,
        "exposures": exposures,
        "db": _count_plaintext_rows(demo),
        "scan": {"n": scans, "mean_ms": statistics.fmean(timings),
                 "p95_ms": ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)],
                 "min_ms": ordered[0], "max_ms": ordered[-1]},
        "crypto": {"n": crypto_rounds, "encrypt_ms": enc_ms, "decrypt_ms": dec_ms},
    }


def render(result: dict) -> str:
    mark = lambda exposed: "노출" if exposed else "—"  # noqa: E731
    lines = [
        "# QR 안심 운송장 서버 — 정량 결과",
        "",
        f"- 측정 시각: {result['measured_at']}",
        f"- 환경: Python {result['python']}, {result['os']}, 임시 SQLite + FastAPI TestClient ({LOCAL_NOTE})",
        f"- 데이터: 가상 샘플(기사 {result['counts']['drivers']}명, 주문 {result['counts']['orders']}건). "
        "이 문서에는 개인정보 값이 없고 개수만 있다.",
        "- 다시 만들기: `python -m app.report`",
        "",
        "## (a) 시나리오별 결과",
        "",
        "개인정보 항목 수 = 응답 본문에 나온 이름 원문·실제 전화번호·상세 주소의 개수(0~3).",
        "",
        "| 시나리오 | HTTP | 오류 코드 | 응답에 나온 개인정보 항목 수 | 비고 |",
        "|---|---|---|---|---|",
    ]
    for s in result["scenarios"]:
        lines.append(f"| {s['label']} | {s['status']} | {s['code']} | {s['pii_count']} | {s['note']} |")

    lines += [
        "",
        "## (b) 역할별 노출 비교",
        "",
        "| 누가 보는가 | 이름 원문 | 실제 전화 | 상세 주소 | 노출 항목 수 |",
        "|---|---|---|---|---|",
        "| 기존 종이 운송장(누구나, 비교 기준) | 노출 | 노출 | 노출 | 3 |",
    ]
    for key, label in (("hub", "허브 직원"), ("unassigned", "비담당 기사"),
                       ("assigned", "담당 기사"), ("delivered", "배송 완료 후(담당 기사 포함)")):
        exp = result["exposures"][key]
        lines.append(f"| {label} | {mark(exp['name'])} | {mark(exp['phone'])} | {mark(exp['address'])} "
                     f"| {sum(exp.values())} |")
    lines += [
        "",
        "종이 운송장 행은 측정값이 아니라 비교 기준이다(이름·전화·주소가 모두 인쇄됨). 나머지는 실제 응답에서 센 값이다.",
        "",
        "## (c) DB에 평문 개인정보가 저장된 행 수",
        "",
        "시나리오를 모두 실행한 뒤, 각 테이블의 모든 문자열 열에서 시드의 이름·전화·주소 평문을 찾았다.",
        "",
        "| 테이블 | 전체 행 | 평문이 들어 있는 행 | 키로 복호화되는 행 |",
        "|---|---|---|---|",
    ]
    for t in result["db"]:
        dec = "—" if t["decryptable"] is None else t["decryptable"]
        lines.append(f"| `{t['table']}` | {t['rows']} | {t['plaintext_rows']} | {dec} |")
    total = sum(t["plaintext_rows"] for t in result["db"])
    lines += [
        "",
        f"**평문 개인정보가 저장된 행: 합계 {total}행** (0이어야 한다)",
        "",
        f"## (d) 응답 시간 ({LOCAL_NOTE})",
        "",
        "| 항목 | 횟수 | 평균 | 95% | 최소 | 최대 |",
        "|---|---|---|---|---|---|",
    ]
    sc, cr = result["scan"], result["crypto"]
    lines += [
        f"| 담당 기사 스캔 `GET /scan/{{qr_token}}` | {sc['n']} | {sc['mean_ms']:.2f} ms | {sc['p95_ms']:.2f} ms "
        f"| {sc['min_ms']:.2f} ms | {sc['max_ms']:.2f} ms |",
        f"| AES-256-GCM 암호화 1회 | {cr['n']} | {cr['encrypt_ms']:.4f} ms | — | — | — |",
        f"| AES-256-GCM 복호화 1회 | {cr['n']} | {cr['decrypt_ms']:.4f} ms | — | — | — |",
        "",
        "스캔 시간에는 JWT 검증, 기기 확인, DB 조회, 복호화, 접근 로그 기록이 들어 있다. "
        "같은 프로세스 안에서 호출한 값이라 실제 폰에서는 네트워크 시간이 더해진다.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="정량 결과 리포트 생성")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--scans", type=int, default=200)
    args = parser.parse_args(argv)

    # dev.db와 .env의 키를 쓰지 않는다: 임시 폴더의 SQLite와 일회용 키로 바꾼다.
    tmp = Path(tempfile.mkdtemp(prefix="qrsec-report-"))
    os.environ.update({
        "DATABASE_URL": "sqlite:///" + (tmp / "report.db").as_posix(),
        "JWT_SECRET": secrets.token_urlsafe(48),
        "PII_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
        "ADMIN_TOKEN": secrets.token_urlsafe(16),
        "UPLOAD_DIR": str(tmp / "uploads"),
        "DEMO_DATA": str(SAMPLE_DATA),
    })
    from .config import get_settings
    from .db import get_engine

    get_settings.cache_clear()
    get_engine.cache_clear()
    try:
        result = measure(scans=args.scans)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(render(result), encoding="utf-8")
    finally:
        get_engine().dispose()
        shutil.rmtree(tmp, ignore_errors=True)

    total = sum(t["plaintext_rows"] for t in result["db"])
    print(f"리포트 저장: {args.out}")
    print(f"스캔 {result['scan']['n']}회 평균 {result['scan']['mean_ms']:.2f} ms, "
          f"95% {result['scan']['p95_ms']:.2f} ms / 평문 개인정보 행 {total}")


if __name__ == "__main__":
    main()
