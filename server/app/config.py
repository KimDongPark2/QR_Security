"""환경 변수(.env) 읽기와 KST 시각 도우미."""
from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent  # server/
KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class Settings:
    database_url: str
    jwt_secret: str
    pii_key: bytes
    admin_token: str
    upload_dir: Path
    demo_data: Path


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"환경 변수 {name}가 비어 있습니다. .env를 확인하세요.")
    return value


def _path(value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else (BASE_DIR / p).resolve()


@lru_cache
def get_settings() -> Settings:
    # 이미 설정된 환경 변수(테스트 등)가 .env보다 우선한다.
    load_dotenv(BASE_DIR / ".env", override=False)

    try:
        key = base64.b64decode(_required("PII_KEY"), validate=True)
    except binascii.Error:
        raise RuntimeError("PII_KEY가 올바른 base64가 아닙니다.") from None
    if len(key) != 32:
        raise RuntimeError("PII_KEY는 base64로 인코딩한 32바이트여야 합니다.")

    demo = _path(os.environ.get("DEMO_DATA", "").strip() or "../db/demo_data.json")
    if not demo.exists():
        demo = BASE_DIR / "demo" / "demo_data.sample.json"

    return Settings(
        database_url=os.environ.get("DATABASE_URL", "").strip() or "sqlite:///./dev.db",
        jwt_secret=_required("JWT_SECRET"),
        pii_key=key,
        admin_token=_required("ADMIN_TOKEN"),
        upload_dir=_path(os.environ.get("UPLOAD_DIR", "").strip() or "./uploads"),
        demo_data=demo,
    )


# ---- 시각: DB에는 KST 기준 naive 값, 응답은 +09:00 ISO 8601 ----

def now_kst() -> datetime:
    """DB 저장용 KST 현재 시각(시간대 없음, 초 단위)."""
    return datetime.now(KST).replace(tzinfo=None, microsecond=0)


def today_kst() -> date:
    return datetime.now(KST).date()


def iso_kst(dt: datetime | None) -> str | None:
    """DB의 KST naive 값 → "2026-11-14T14:03:22+09:00"."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)
    return dt.astimezone(KST).replace(microsecond=0).isoformat()
