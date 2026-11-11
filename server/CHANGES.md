# API 변경 제안·기록

SERVER_SPEC §7은 팀(앱 한승일, DB·웹 주승윤)과 맞춘 약속이다. 바꿔야 할 점을 여기 적고 확인을 받는다.

---

## 1. [결정·적용] 422 오류 본문에 요청 값(개인정보)이 그대로 담긴다 → A안

**문제**
§7은 "요청 형식 오류는 FastAPI 기본 422를 그대로 둔다"고 한다. 그런데 FastAPI 기본 422 본문의 `detail[].input`에는 **클라이언트가 보낸 값이 그대로** 들어간다.

- `POST /orders`에서 필드 하나만 빠져도 `input`에 요청 본문 전체가 실린다. 그러면 이름·전화번호·주소 평문이 오류 응답에 나온다.
  → §0-3 "개인정보 평문을 오류 메시지에 남기지 않는다"는 규칙과 부딪힌다.
- `POST /auth/login`의 형식 오류(예: `device_uuid` 누락)에서도 비밀번호 평문이 응답에 실린다.

**제안 (A안, 권장)**
상태 코드 422와 `{"detail": [...]}` 구조는 그대로 둔다. 각 항목의 `input`(과 `ctx`)만 지운다. `loc`·`msg`·`type`은 남으므로 앱·웹이 어떤 필드가 틀렸는지 아는 데는 지장이 없다.

```json
{"detail": [{"type": "missing", "loc": ["body", "phone"], "msg": "Field required"}]}
```

- B안: 422도 `{"code": "VALIDATION_ERROR", "message": "..."}`로 바꾼다. 앱·웹 쪽 처리도 바꿔야 해서 권장하지 않는다.
- C안: 그대로 둔다. `/orders`는 관리자만 쓰므로 위험이 작다고 보는 경우.

**결정**: A안. `main.py`에 `RequestValidationError` 처리기를 추가해서 모든 엔드포인트의 422 응답에서 `input`·`ctx`를 지운다. 상태 코드 422와 `{"detail": [{type, loc, msg}]}` 구조는 그대로다(앱·웹이 받는 모양 변화 없음). SERVER_SPEC §7 문장 수정. 테스트: `/orders` 필드 누락, `/auth/login` `device_uuid` 누락, 깨진 JSON.

---

## 2. [결정·적용] 관리자 API의 정해지지 않은 부분 → 구현대로 승인

SERVER_SPEC §7.10 표에 응답 형식을, §7 오류 코드 표에 `404 DRIVER_NOT_FOUND`를 반영했다. `/admin/assign`의 배정 옮기기 동작은 그대로 둔다.

§7.10에는 응답 본문과 일부 오류가 정해져 있지 않다. 아래처럼 구현했다. **관리자 전용이라 앱·웹에는 영향이 없다.**

| 경로 | 구현한 동작 |
|---|---|
| `POST /admin/reset-demo` | 200 `{"ok": true, "drivers": 4, "orders": [{"order_no", "qr_token", "assign_to"}], "photos_removed": N}` |
| `POST /admin/assign` | 200 `{"driver_id", "order_no", "work_date", "previous_driver_id"}`. 오늘 다른 기사에게 배정돼 있으면 이 기사로 **옮긴다**(UNIQUE(order_id, work_date) 때문). 없는 기사 → 404 `DRIVER_NOT_FOUND`(새 코드), HUB → 403 `ROLE_FORBIDDEN`, 없는 주문 → 404 `ORDER_NOT_FOUND`, 완료된 주문 → 409 `ALREADY_DELIVERED` |
| `POST /admin/drivers/{id}/reset-device` | 200 `{"driver_id", "device_uuid": null}`. 없는 기사 → 404 `DRIVER_NOT_FOUND`(새 코드) |
| `GET /admin/logs?limit=50` | 200 `[{"id", "driver_id", "order_id", "order_no", "action", "result", "at"}]`, 최신순, `limit` 1~500 |
| `GET /admin/db-peek/{order_no}` | 200 `{"order_no", "order_id", "status", "recipient_pii": {열 그대로} \| null, "pii_archive": [{열 그대로}]}`. 암호문 그대로, 복호화하지 않음 |

**새 오류 코드**: `404 DRIVER_NOT_FOUND`(관리자 API에서 없는 기사 ID). §7 오류 코드 표에 추가함.

---

## 3. [결정·적용] 정해지지 않은 세부 동작 → 그대로 유지

사진 관련 세 줄은 SERVER_SPEC §7.4 3번 항목에 반영했다.


- `GET /track/{order_no}`: 배송 전에는 `delivered_at`이 `null`이다.
- `POST /deliveries/.../complete`: content-type뿐 아니라 파일 시작 바이트(JPEG/PNG 시그니처)도 확인한다. 맞지 않으면 415 `UNSUPPORTED_MEDIA`. `orders.photo_path`에는 파일 이름만 저장한다(예: `SC-20261114-0001.png`).
- 사진은 DB 커밋이 성공한 뒤에만 `UPLOAD_DIR`에 최종 저장한다(500 `PII_NOT_REMOVED`로 롤백되면 남기지 않는다).
