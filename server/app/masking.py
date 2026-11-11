"""이름 가리기와 워터마크 문구 (SERVER_SPEC §5)."""
from __future__ import annotations

from datetime import datetime


def mask_name(name: str) -> str:
    """김철 → 김O, 한승일 → 한O일, 남궁민준 → 남OO준."""
    name = name.strip()
    if len(name) <= 1:
        return "O"
    if len(name) == 2:
        return name[0] + "O"
    return name[0] + "O" * (len(name) - 2) + name[-1]


def watermark_text(driver_id: str, driver_name: str, at: datetime) -> str:
    """"D001 김담당 · 2026-11-14 14:03:22" (at은 KST)."""
    return f"{driver_id} {driver_name} · {at:%Y-%m-%d %H:%M:%S}"
