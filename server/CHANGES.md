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

---

## 4. [추가] 로그인 시도 제한 — 429 `TOO_MANY_ATTEMPTS`

- `POST /auth/login`: 같은 `driver_id`로 마지막 로그인 성공 이후, 최근 10분 안에 `INVALID_CREDENTIALS`가 5번 이상이면 429 `TOO_MANY_ATTEMPTS`. 메시지 "로그인 시도가 너무 많습니다. 10분 뒤에 다시 시도하세요." 잠긴 동안에는 비밀번호가 맞아도 거부한다.
- 새 테이블 없이 `access_logs`로 센다. `/admin/reset-demo`를 하면 풀린다. 잠긴 동안의 시도는 `TOO_MANY_ATTEMPTS`로 기록되고 실패 횟수에는 더해지지 않으므로, 마지막으로 틀린 뒤 10분이 지나면 풀린다.
- 없는 ID도 똑같이 제한된다(ID가 있는지 없는지 응답으로 구분되지 않게).
- **앱 영향(한승일)**: 로그인 화면에서 새 코드 429 `TOO_MANY_ATTEMPTS`가 올 수 있다. 오류 본문 모양은 다른 오류와 같으므로 `message`를 그대로 보여주면 된다.
- 한계: 다른 사람이 일부러 5번 틀려서 특정 기사를 10분 동안 못 들어오게 할 수 있다. 시연 중에 걸리면 `/admin/reset-demo`.
- SERVER_SPEC §7.2와 오류 코드 표에 반영.

## 5. [추가] 보존 기간 만료 파기 — `POST /admin/purge-expired`

- `pii_archive`에서 `keep_until` < 지금(KST)인 행을 지우고 200 `{"purged": N, "at": "..."}`. `python -m app.purge`도 같은 동작.
- `access_logs`에 `driver_id`=`ADMIN`, `action`=`PURGE`, `result`=`OK`. (`action` 값에 `PURGE`가 추가됨. DB 열은 그대로.)
- 관리자 전용이라 앱·웹 영향 없음. SERVER_SPEC §7.10 표에 반영.

## 6. [추가] 의심 시도 집계 — `GET /admin/alerts?minutes=60`

- 최근 N분(1~1440) 동안 `access_logs`의 `NOT_ASSIGNED`·`DEVICE_MISMATCH`·`INVALID_CREDENTIALS`·`TOO_MANY_ATTEMPTS`를 기사별로 센다. `total` 큰 순.
- 한계: 토큰은 맞는데 `X-Device-UUID`가 다른 스캔·완료 요청의 `DEVICE_MISMATCH`는 인증 단계에서 거부되어 `access_logs`에 남지 않는다. 그래서 여기에는 **로그인 때의** `DEVICE_MISMATCH`만 집계된다.
- 관리자 전용이라 앱·웹 영향 없음. SERVER_SPEC §7.10 표에 반영.
