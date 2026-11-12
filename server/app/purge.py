"""보존 기간이 지난 개인정보 파기: python -m app.purge  (POST /admin/purge-expired 와 같은 동작)

pii_archive 에서 keep_until < 지금(KST) 인 행을 지운다. 암호문을 복호화하지 않는다.
"""
from __future__ import annotations

from datetime import datetime

from .config import iso_kst, now_kst
from .db import get_engine, log_access, pii_archive


def purge_expired(now: datetime | None = None) -> dict:
    now = now or now_kst()
    with get_engine().begin() as conn:
        purged = conn.execute(pii_archive.delete().where(pii_archive.c.keep_until < now)).rowcount
    log_access("ADMIN", None, "PURGE", "OK")
    return {"purged": purged, "at": iso_kst(now)}


def main() -> None:
    result = purge_expired()
    print(f"보존 기간 만료 파기: {result['purged']}건 ({result['at']})")


if __name__ == "__main__":
    main()
