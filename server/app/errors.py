"""API 오류: 본문은 항상 {"code": ..., "message": ...} (SERVER_SPEC §7)."""
from __future__ import annotations

MESSAGES = {
    "INVALID_CREDENTIALS": "아이디 또는 비밀번호가 올바르지 않습니다.",
    "TOKEN_INVALID": "로그인 정보가 올바르지 않습니다. 다시 로그인하세요.",
    "TOKEN_EXPIRED": "로그인이 만료되었습니다. 다시 로그인하세요.",
    "ADMIN_ONLY": "관리자만 사용할 수 있습니다.",
    "NO_ASSIGNMENT_TODAY": "오늘 배정된 배송 건이 없어 로그인할 수 없습니다.",
    "DEVICE_MISMATCH": "등록된 기기가 아닙니다.",
    "NOT_ASSIGNED": "이 배송 건의 담당 기사가 아닙니다.",
    "ROLE_FORBIDDEN": "이 작업을 할 수 있는 역할이 아닙니다.",
    "ORDER_NOT_FOUND": "배송 건을 찾을 수 없습니다.",
    "ALREADY_DELIVERED": "이미 배송 완료된 건입니다.",
    "PII_REMOVED": "배송이 완료되어 개인정보 조회가 차단되었습니다.",
    "FILE_TOO_LARGE": "사진은 10MB 이하만 올릴 수 있습니다.",
    "UNSUPPORTED_MEDIA": "사진은 JPEG 또는 PNG만 올릴 수 있습니다.",
    "PII_NOT_REMOVED": "개인정보 삭제가 확인되지 않아 배송 완료를 취소했습니다.",
    "TOO_MANY_ATTEMPTS": "로그인 시도가 너무 많습니다. 10분 뒤에 다시 시도하세요.",
    "DRIVER_NOT_FOUND": "기사를 찾을 수 없습니다.",
}


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str | None = None):
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        # 메시지는 고정 문구만 쓴다. 개인정보를 넣지 않는다.
        self.message = message or MESSAGES.get(code, code)
