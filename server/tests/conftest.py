"""테스트 환경: 임시 SQLite 파일 + 테스트용 키. app을 import하기 전에 환경 변수를 설정한다."""
from __future__ import annotations

import base64
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_DIR))

_TMP = Path(tempfile.mkdtemp(prefix="qrsec-test-"))
os.environ.update({
    "DATABASE_URL": "sqlite:///" + (_TMP / "test.db").as_posix(),
    "JWT_SECRET": secrets.token_urlsafe(48),
    "PII_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
    "ADMIN_TOKEN": "test-admin-token",
    "UPLOAD_DIR": str(_TMP / "uploads"),
    "DEMO_DATA": str(SERVER_DIR / "demo" / "demo_data.sample.json"),
})

from fastapi.testclient import TestClient  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import load_demo_data, reset_and_seed  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _cleanup_tmp():
    yield
    get_engine().dispose()
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def demo_data() -> dict:
    return load_demo_data(get_settings().demo_data)


@pytest.fixture
def seeded(demo_data) -> list[dict]:
    """테스트마다 DB와 UPLOAD_DIR을 초기 상태로 되돌린다. 주문 목록(order_no, qr_token, assign_to)."""
    shutil.rmtree(get_settings().upload_dir, ignore_errors=True)
    return reset_and_seed(get_engine(), demo_data)


@pytest.fixture
def client(seeded):
    with TestClient(app) as c:
        yield c
