"""개인정보 암호화 (SERVER_SPEC §5).

AES-256-GCM, 키 = PII_KEY(base64 디코드한 32바이트).
저장 형식 = base64(nonce 12바이트 + 암호문 + 태그).
다른 모듈은 encrypt / decrypt 두 함수에만 의존한다(같은 이름·형식의 파일로 교체 가능).
"""
from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import get_settings

NONCE_SIZE = 12


def _aesgcm() -> AESGCM:
    return AESGCM(get_settings().pii_key)


def encrypt(plain: str) -> str:
    nonce = os.urandom(NONCE_SIZE)
    sealed = _aesgcm().encrypt(nonce, plain.encode("utf-8"), None)
    return base64.b64encode(nonce + sealed).decode("ascii")


def decrypt(token: str) -> str:
    raw = base64.b64decode(token)
    nonce, sealed = raw[:NONCE_SIZE], raw[NONCE_SIZE:]
    return _aesgcm().decrypt(nonce, sealed, None).decode("utf-8")
