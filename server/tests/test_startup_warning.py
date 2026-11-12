"""개선 1: 서버 시작 시 오늘 배정이 0건이면 경고."""
from __future__ import annotations

import logging

from fastapi.testclient import TestClient

from app.config import today_kst
from app.db import assignments, get_engine
from app.main import app

EXPECTED = "오늘({}) 배정이 0건입니다. python -m app.seed 또는 POST /admin/reset-demo를 실행하세요."


def _startup_warnings(caplog) -> list[str]:
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="uvicorn.error"):
        with TestClient(app) as c:
            assert c.get("/health").status_code == 200
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_no_warning_when_assignments_exist(seeded, caplog):
    assert not any("배정이 0건" in m for m in _startup_warnings(caplog))


def test_warning_when_no_assignments_today(seeded, caplog):
    with get_engine().begin() as conn:
        conn.execute(assignments.delete())
    assert EXPECTED.format(today_kst().isoformat()) in _startup_warnings(caplog)
