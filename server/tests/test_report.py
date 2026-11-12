"""개선 4: 정량 결과 리포트 (python -m app.report)."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import func, select

from app import report
from app.db import access_logs, get_engine, orders
from test_flow import pii_strings

SERVER_DIR = Path(__file__).resolve().parent.parent

EXPECTED = {
    "허브 스캔": (200, "-", 0),
    "담당 기사 스캔": (200, "-", 1),
    "비담당 기사 스캔": (403, "NOT_ASSIGNED", 0),
    "등록 안 된 기기": (403, "DEVICE_MISMATCH", 0),
    "배송 완료 후 재스캔": (410, "PII_REMOVED", 0),
    "오늘 배정 없는 기사 로그인": (403, "NO_ASSIGNMENT_TODAY", 0),
    "비밀번호 6회 틀림": (429, "TOO_MANY_ATTEMPTS", 0),
    "위조 토큰": (401, "TOKEN_INVALID", 0),
}


def test_measure_and_render(seeded, demo_data):
    result = report.measure(scans=5, crypto_rounds=20)

    assert {s["label"]: (s["status"], s["code"], s["pii_count"]) for s in result["scenarios"]} == EXPECTED
    assert result["exposures"]["assigned"] == {"name": False, "phone": False, "address": True}
    for key in ("hub", "unassigned", "delivered"):
        assert not any(result["exposures"][key].values())

    assert sum(t["plaintext_rows"] for t in result["db"]) == 0
    db = {t["table"]: t for t in result["db"]}
    assert db["recipient_pii"]["rows"] == 5 and db["recipient_pii"]["decryptable"] == 5
    assert db["pii_archive"]["rows"] == 1 and db["pii_archive"]["decryptable"] == 1
    assert result["scan"]["n"] == 5 and 0 < result["scan"]["mean_ms"] <= result["scan"]["max_ms"]
    assert result["scan"]["p95_ms"] <= result["scan"]["max_ms"]

    md = report.render(result)
    assert "노트북 로컬 측정, 네트워크 제외" in md
    assert "| 기존 종이 운송장(누구나, 비교 기준) | 노출 | 노출 | 노출 | 3 |" in md
    assert "합계 0행" in md
    for s in pii_strings(demo_data):
        assert s not in md  # 개인정보 값 자체는 리포트에 없다


def test_cli_uses_temp_db_and_writes_file(seeded, demo_data, tmp_path):
    out = tmp_path / "result.md"
    dev_db = SERVER_DIR / "dev.db"
    before = dev_db.stat().st_mtime_ns if dev_db.exists() else None

    proc = subprocess.run(
        [sys.executable, "-m", "app.report", "--out", str(out), "--scans", "3"],
        cwd=SERVER_DIR, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0, proc.stderr

    md = out.read_text(encoding="utf-8")
    assert "## (a) 시나리오별 결과" in md and "## (d) 응답 시간" in md
    for s in pii_strings(demo_data):
        assert s not in md and s not in proc.stdout

    # 환경 변수로 지정된 DB(여기서는 테스트 DB)와 dev.db는 건드리지 않는다
    assert (dev_db.stat().st_mtime_ns if dev_db.exists() else None) == before
    with get_engine().connect() as conn:
        assert conn.execute(select(func.count()).select_from(access_logs)).scalar_one() == 0
        tokens = set(conn.execute(select(orders.c.qr_token)).scalars())
    assert tokens == {o["qr_token"] for o in seeded}
