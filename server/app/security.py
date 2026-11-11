"""비밀번호 해시, JWT, 인증 의존성 (SERVER_SPEC §5, §6)."""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
from datetime import datetime, time

import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from .config import KST, get_settings
from .db import drivers, get_engine
from .errors import ApiError

PBKDF2_ITERATIONS = 200_000
JWT_ALG = "HS256"


# ---- 비밀번호 ----

def hash_password(password: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "pbkdf2_sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(dk).decode("ascii"),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iterations, salt_b64, hash_b64 = stored.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        rounds = int(iterations)
    except (ValueError, binascii.Error):
        return False
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds)
    return hmac.compare_digest(dk, expected)


# 없는 ID로 로그인할 때도 같은 시간이 걸리도록 비교에 쓰는 가짜 해시
DUMMY_PASSWORD_HASH = hash_password("not-a-real-password")


# ---- JWT ----

def token_expiry(now: datetime) -> datetime:
    """오늘(KST) 23:59:59. 근무일과 연동."""
    now = now.astimezone(KST)
    return datetime.combine(now.date(), time(23, 59, 59), tzinfo=KST)


def create_access_token(driver_id: str, role: str, device_uuid: str) -> tuple[str, datetime]:
    now = datetime.now(KST)
    exp = token_expiry(now)
    payload = {
        "sub": driver_id,
        "role": role,
        "dev": device_uuid,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    return jwt.encode(payload, get_settings().jwt_secret, algorithm=JWT_ALG), exp


# ---- 인증 의존성 ----

_bearer = HTTPBearer(auto_error=False, description="POST /auth/login 의 access_token")


def current_driver(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    x_device_uuid: str | None = Header(default=None, description="로그인 때 쓴 기기 UUID"),
) -> dict:
    """기사 API 인증. 성공하면 drivers 행(driver_id, name, role, device_uuid)을 돌려준다."""
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "TOKEN_INVALID")
    try:
        claims = jwt.decode(
            credentials.credentials,
            get_settings().jwt_secret,
            algorithms=[JWT_ALG],
            options={"require": ["sub", "dev", "iat", "exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise ApiError(401, "TOKEN_EXPIRED") from None
    except jwt.InvalidTokenError:
        raise ApiError(401, "TOKEN_INVALID") from None

    with get_engine().connect() as conn:
        row = conn.execute(
            select(drivers.c.driver_id, drivers.c.name, drivers.c.role, drivers.c.device_uuid)
            .where(drivers.c.driver_id == claims["sub"])
        ).mappings().first()
    if row is None:
        raise ApiError(401, "TOKEN_INVALID")

    dev = claims["dev"]
    if not x_device_uuid or x_device_uuid != dev or row["device_uuid"] != dev:
        raise ApiError(403, "DEVICE_MISMATCH")
    return dict(row)


def require_admin(x_admin_token: str | None = Header(default=None)) -> None:
    expected = get_settings().admin_token
    if not x_admin_token or not hmac.compare_digest(x_admin_token.encode(), expected.encode()):
        raise ApiError(401, "ADMIN_ONLY")
