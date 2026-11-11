# 중앙 서버 구현 명세서 — QR 안심 운송장 (seungcurity 팀)

> Claude Code에게 주는 작업 명세서다. 담당: **이승엽 (중앙 서버)**.
> 앱(한승일), DB 스키마·주문자 웹(주승윤)은 이 작업 범위가 아니다.
> 일정: 11/12(목) 17:00 API 공유 → 11/13(금) 23:00 앱 연결 → **11/14(토) 13:00 코드 동결** → 11/15(일) 제출.

---

## 0. 작업 규칙 (꼭 지킬 것)

1. **§7 API(경로·요청·응답·오류 코드)는 팀원과 맞춘 약속이다.** 임의로 바꾸지 않는다. 바꿔야 할 이유가 생기면 구현을 멈추고 `CHANGES.md`에 이유와 제안을 적은 뒤 보고한다.
2. §9의 단계 순서대로 만든다. 단계가 끝날 때마다 `pytest`를 돌려 통과시키고, 멈춰서 한 일을 짧게 요약한다.
3. **개인정보(이름·전화번호·상세 주소) 평문**은 DB, 로그, 오류 메시지, 허브·비담당 기사 응답 어디에도 남기지 않는다. `print`·로그에 복호화한 값을 찍지 않는다.
4. 데이터는 전부 가상이다. 실제 사람 이름·주소·전화번호를 쓰지 않는다.
5. 만들지 않는 것: Android 앱, 주문자 웹 HTML, MySQL 스키마 파일(`../db/schema.sql`, 주승윤 담당), Docker, Redis, 회원가입.
6. 단순하게 만든다. 동기 FastAPI + SQLAlchemy 2 Core, 파일 몇 개. 비동기 DB, ORM 모델 상속 구조, 마이그레이션 도구는 쓰지 않는다.
7. 비밀값(`.env`, 키)은 커밋하지 않는다. `.env.example`만 커밋한다.

---

## 1. 시스템 한눈에

- 운송장에는 QR만 인쇄된다. QR 안에는 **무작위 토큰(`qr_token`)만** 있다. 개인정보·주문번호는 없다.
- 서버가 **누가 스캔했는지** 보고 보여줄 범위를 정한다.
  - 허브 직원(`HUB`) → 권역(예: "인천 연수구 송도동")만
  - 오늘 이 건을 배정받은 기사 → 가린 이름, 안심번호, 상세 주소, 워터마크 문구
  - 그 밖의 기사 → 거부
- **배송 완료 처리 순간**, DB 트리거가 조회용 개인정보(`recipient_pii`)를 보존용 테이블(`pii_archive`)로 옮기고 지운다. 그 뒤 같은 QR을 스캔하면 410.
- 개인정보 열은 **AES-256-GCM 암호문**으로만 저장한다.
- 로그인은 **오늘 배정 물량이 있는 기사만** 할 수 있다. 계정은 **처음 로그인한 기기 1대에 묶인다**. 토큰은 **오늘 23:59:59(KST)에 만료**된다(근무 연동).

---

## 2. 기술 스택과 환경

- Python 3.11 이상
- `fastapi`, `uvicorn[standard]`, `sqlalchemy`(2.x), `pymysql`, `cryptography`, `pyjwt`, `python-multipart`, `qrcode[pil]`, `python-dotenv`, `pytest`, `httpx`
- 버전은 설치 시점의 최신 안정판으로 `requirements.txt`에 고정한다.
- DB
  - 개발·테스트: **SQLite**. 스키마는 `server/db/sqlite_schema.sql`(Claude Code가 작성, §4의 약속 그대로 + 트리거).
  - 시연: **MySQL 8**. 스키마는 주승윤의 `../db/schema.sql`.
  - `DATABASE_URL`만 바꿔서 전환한다. 앱 코드의 SQL은 두 DB에서 모두 도는 것만 쓴다. 시각은 DB 함수가 아니라 Python에서 만든다.
- 시간대: 모든 "오늘", "지금"은 `Asia/Seoul`(`zoneinfo`). DB에는 **KST 기준 시각(시간대 없는 값)**을 저장하고, API 응답은 `+09:00`이 붙은 ISO 8601로 보낸다.

### 환경 변수 (`.env`, 커밋 금지)

| 이름 | 예시 | 설명 |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./dev.db` / `mysql+pymysql://user:pw@localhost:3306/seungcurity?charset=utf8mb4` | DB 연결 |
| `JWT_SECRET` | 무작위 32바이트 이상 | JWT 서명 |
| `PII_KEY` | base64로 인코딩한 32바이트 | AES-256 키 |
| `ADMIN_TOKEN` | 무작위 문자열 | 관리자 API |
| `UPLOAD_DIR` | `./uploads` | 배송 완료 사진 |
| `DEMO_DATA` | `../db/demo_data.json` (없으면 `./demo/demo_data.sample.json`) | 시드 데이터 |

README에 키 만드는 명령을 적는다.
`python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"`

---

## 3. 폴더 구조

```
QR_Security/
├─ server/                 ← 이 작업 범위
│  ├─ CLAUDE.md
│  ├─ SERVER_SPEC.md       (이 파일)
│  ├─ CHANGES.md           (API를 바꿔야 할 때만)
│  ├─ README.md            (실행 방법 §11)
│  ├─ requirements.txt
│  ├─ .env.example
│  ├─ app/
│  │  ├─ main.py           FastAPI 앱, 라우터 등록, 오류 처리, CORS, /web 정적 서빙
│  │  ├─ config.py         .env 읽기
│  │  ├─ db.py             엔진, 테이블 정의(Core), 트랜잭션 도우미
│  │  ├─ crypto.py         encrypt / decrypt (§5) — 주승윤 버전이 오면 교체 가능
│  │  ├─ security.py       비밀번호 해시, JWT, 인증 의존성(기사·관리자)
│  │  ├─ masking.py        이름 가리기, 워터마크 문구
│  │  ├─ seed.py           python -m app.seed
│  │  └─ routers/          auth.py, scan.py, deliveries.py, orders.py, track.py, admin.py
│  ├─ db/sqlite_schema.sql
│  ├─ demo/demo_data.sample.json
│  ├─ tests/test_flow.py
│  └─ uploads/             (커밋 제외)
├─ app/                    ← 한승일 (Android)
├─ db/                     ← 주승윤 (schema.sql, demo_data.json)
└─ web/                    ← 주승윤 (주문자 확인 웹)
```

---

## 4. DB 약속 (주승윤과 공유)

서버는 아래 **테이블·열 이름을 그대로** 쓴다. MySQL 스키마는 주승윤이 이 표대로 만든다.

| 테이블 | 열 | 비고 |
|---|---|---|
| `drivers` | `driver_id` VARCHAR(10) PK · `name` VARCHAR(20) · `password_hash` VARCHAR(200) · `role` VARCHAR(10) · `device_uuid` VARCHAR(64) NULL | `role` = `HUB` 또는 `DRIVER`. 예: `H001`, `D001` |
| `orders` | `id` INT PK 자동증가 · `order_no` VARCHAR(20) UNIQUE · `qr_token` VARCHAR(32) UNIQUE · `region` VARCHAR(50) · `status` VARCHAR(20) · `created_at` DATETIME · `delivered_at` DATETIME NULL · `photo_path` VARCHAR(200) NULL | 개인정보 없음. `status` = `READY` → `AT_HUB` → `OUT_FOR_DELIVERY` → `DELIVERED` |
| `recipient_pii` | `order_id` INT PK, FK→`orders.id` · `name_enc` TEXT · `phone_enc` TEXT · `address_enc` TEXT · `safe_number` VARCHAR(20) | 조회용. `*_enc`는 §5 형식의 암호문 |
| `pii_archive` | `id` INT PK 자동증가 · `order_id` INT · `name_enc` TEXT · `phone_enc` TEXT · `address_enc` TEXT · `archived_at` DATETIME · `keep_until` DATETIME | 보존용. **API로 복호화해서 보여주지 않는다** |
| `assignments` | `id` INT PK 자동증가 · `driver_id` FK→`drivers` · `order_id` FK→`orders` · `work_date` DATE · UNIQUE(`order_id`, `work_date`) | 오늘 배정 = 로그인·조회 조건 |
| `access_logs` | `id` INT PK 자동증가 · `driver_id` VARCHAR(10) · `order_id` INT NULL · `action` VARCHAR(20) · `result` VARCHAR(30) · `at` DATETIME | `action` = `LOGIN` / `SCAN` / `COMPLETE`. `result` = `OK` 또는 오류 코드. 없는 ID로 로그인 시도한 것도 남기므로 `driver_id`에 FK 없음 |

### 배송 완료 트리거 (DB가 한다)

`orders.status`가 `DELIVERED`로 바뀌면(이전 값이 `DELIVERED`가 아닐 때), 같은 `order_id`의 `recipient_pii` 행을 `pii_archive`로 복사하고(`keep_until` = 지금 + 5년) `recipient_pii`에서 지운다. `safe_number`는 보존하지 않는다.

- 개인정보를 `orders`와 다른 테이블에 둔 이유: MySQL 트리거는 자기를 실행시킨 테이블을 수정할 수 없다(오류 1442).
- 5년: 전자상거래법 시행령 제6조(대금결제·재화 공급 기록 5년)를 참고한 시연용 값. 개인정보보호법 제21조는 다른 법령에 따라 보존할 때 분리 보관하게 한다.

`server/db/sqlite_schema.sql`에는 아래 트리거를 그대로 넣는다(SQLite에서 동작 확인함, 시각은 KST로 맞추려고 `+9 hours`).

```sql
CREATE TRIGGER trg_orders_delivered
AFTER UPDATE OF status ON orders
WHEN NEW.status = 'DELIVERED' AND OLD.status <> 'DELIVERED'
BEGIN
  INSERT INTO pii_archive(order_id, name_enc, phone_enc, address_enc, archived_at, keep_until)
  SELECT order_id, name_enc, phone_enc, address_enc,
         datetime('now', '+9 hours'), datetime('now', '+9 hours', '+5 years')
  FROM recipient_pii WHERE order_id = NEW.id;
  DELETE FROM recipient_pii WHERE order_id = NEW.id;
END;
```

---

## 5. 암호화·해시·마스킹 규칙

- **`crypto.py`**
  - `encrypt(plain: str) -> str`, `decrypt(token: str) -> str`
  - AES-256-GCM(`cryptography`의 `AESGCM`), 키 = `PII_KEY`(base64 디코드 후 32바이트)
  - 매번 12바이트 무작위 nonce. 저장 형식 = `base64(nonce 12바이트 + 암호문 + 태그)`
  - 다른 모듈은 이 두 함수에만 의존한다. 주승윤이 같은 이름·형식으로 만든 파일을 주면 교체한다.
- **비밀번호**: `hashlib.pbkdf2_hmac('sha256', pw, salt, 200_000)`. 저장 형식은 `pbkdf2_sha256$200000$<salt_b64>$<hash_b64>`. 비교는 `hmac.compare_digest`.
- **이름 가리기 `mask_name`**: 2글자 → 첫 글자 + `O` (김O). 3글자 이상 → 첫 글자 + `O`×(글자 수−2) + 끝 글자 (한O일, 남OO준).
- **워터마크 문구**: `"{driver_id} {기사 이름} · {YYYY-MM-DD HH:MM:SS}"` (KST). 앱이 화면 위에 겹쳐 표시한다.
- **JWT**: HS256. 클레임 = `sub`(driver_id), `role`, `dev`(device_uuid), `iat`, `exp`(오늘 23:59:59 KST).

---

## 6. 인증 규칙

- **기사 API**: 헤더 `Authorization: Bearer <JWT>` + `X-Device-UUID: <uuid>`
  1. JWT가 없거나 위조 → 401 `TOKEN_INVALID`, 만료 → 401 `TOKEN_EXPIRED`
  2. `X-Device-UUID` = JWT의 `dev` = `drivers.device_uuid`가 아니면 → 403 `DEVICE_MISMATCH`
- **관리자 API**: 헤더 `X-Admin-Token: <ADMIN_TOKEN>`. 틀리면 401 `ADMIN_ONLY`.

---

## 7. API 명세 (팀 공유용)

- 모든 요청·응답은 JSON(UTF-8). 예외: 사진 업로드(multipart), QR(PNG), 라벨(HTML).
- **오류 본문은 항상** `{"code": "NOT_ASSIGNED", "message": "이 배송 건의 담당 기사가 아닙니다."}`. 단, 요청 형식 오류는 422로 FastAPI 기본 구조(`{"detail": [{"type", "loc", "msg"}]}`)를 유지하되 각 항목의 `input`·`ctx`는 지운다(개인정보 보호, CHANGES.md 1).
- 시각: `"2026-11-14T14:03:22+09:00"`

### 한눈에

| # | 메서드·경로 | 쓰는 쪽 | 인증 |
|---|---|---|---|
| 1 | `GET /health` | 모두 | 없음 |
| 2 | `POST /auth/login` | 앱 | 없음 |
| 3 | `GET /scan/{qr_token}` | 앱 | 기사 |
| 4 | `POST /deliveries/{qr_token}/complete` | 앱 | 기사 |
| 5 | `GET /me/assignments` | 앱 (선택) | 기사 |
| 6 | `POST /orders` | 관리자 | 관리자 |
| 7 | `GET /qr/{order_no}` | 브라우저 | 없음 (개인정보 없음) |
| 8 | `GET /labels/{order_no}` | 브라우저 | 없음 (개인정보 없음) |
| 9 | `GET /track/{order_no}` | 주문자 웹 | 없음 (개인정보 없음) |
| 10 | `/admin/...` (§7.10) | 관리자 | 관리자 |

### 7.1 `GET /health`
→ 200 `{"ok": true}`

### 7.2 `POST /auth/login`
요청 `{"driver_id": "D001", "password": "demo1234", "device_uuid": "a1b2c3..."}`

1. ID·비밀번호가 틀리면 → 401 `INVALID_CREDENTIALS`
2. `DRIVER`인데 오늘(KST) 배정이 0건이면 → 403 `NO_ASSIGNMENT_TODAY`. `HUB`는 이 검사를 하지 않는다.
3. `drivers.device_uuid`가 비어 있으면 이번 `device_uuid`로 등록한다(처음 로그인한 폰에 묶임). 이미 다른 값이면 → 403 `DEVICE_MISMATCH`
4. 성공 → 200
```json
{"access_token": "...", "token_type": "bearer", "driver_id": "D001", "driver_name": "김담당",
 "role": "DRIVER", "expires_at": "2026-11-14T23:59:59+09:00", "today_assignments": 4}
```
모든 시도를 `access_logs`에 남긴다(`LOGIN`, 결과 코드).

### 7.3 `GET /scan/{qr_token}` (기사 인증)

1. 토큰이 없으면 → 404 `ORDER_NOT_FOUND`
2. `status`가 `DELIVERED`면 → 410 `PII_REMOVED`, message = `"배송이 완료되어 개인정보 조회가 차단되었습니다."`
3. `HUB` → 200 `{"view": "HUB", "order_no": "...", "region": "인천 연수구 송도동", "status": "AT_HUB"}`. `READY`였으면 `AT_HUB`로 바꾼다.
4. `DRIVER`이고 오늘 이 건을 배정받았으면 → 200
```json
{"view": "DRIVER", "order_no": "SC-20261114-0001", "region": "인천 연수구 송도동",
 "status": "OUT_FOR_DELIVERY", "recipient_name_masked": "홍O동",
 "safe_number": "050-4821-3307", "address": "가상로 12, 101동 1001호",
 "watermark": "D001 김담당 · 2026-11-14 14:03:22"}
```
`READY`·`AT_HUB`였으면 `OUT_FOR_DELIVERY`로 바꾼다. 실제 전화번호(`phone_enc`)는 어떤 응답에도 내보내지 않는다.
5. `DRIVER`인데 배정받지 않았으면 → 403 `NOT_ASSIGNED`
6. 모든 스캔을 `access_logs`에 남긴다(`SCAN`).

**허브 응답과 오류 응답에는 `recipient_name_masked`, `safe_number`, `address` 키 자체가 없어야 한다.**

### 7.4 `POST /deliveries/{qr_token}/complete` (기사 인증, multipart, 필드 이름 `photo`)

1. 토큰이 없으면 → 404 `ORDER_NOT_FOUND`. 이미 `DELIVERED`면 → 409 `ALREADY_DELIVERED`
2. `HUB`면 → 403 `ROLE_FORBIDDEN`. 배정받지 않은 기사면 → 403 `NOT_ASSIGNED`
3. 사진은 `image/jpeg` 또는 `image/png`, 10MB 이하. 아니면 415 `UNSUPPORTED_MEDIA` / 413 `FILE_TOO_LARGE`. `UPLOAD_DIR/{order_no}.jpg|png`로 저장한다.
   - content-type뿐 아니라 파일 시작 바이트(JPEG/PNG 시그니처)도 확인한다. 맞지 않으면 415 `UNSUPPORTED_MEDIA`.
   - 사진은 DB 커밋이 성공한 뒤에만 최종 이름으로 저장한다(롤백되면 남기지 않는다).
   - `orders.photo_path`에는 파일 이름만 저장한다(예: `SC-20261114-0001.png`).
4. **한 트랜잭션 안에서** `UPDATE orders SET status='DELIVERED', delivered_at=지금, photo_path=...`를 실행하면 트리거가 개인정보를 옮긴다.
5. 같은 트랜잭션 안에서 `recipient_pii`에 이 건이 남아 있는지 확인한다. 남아 있으면 롤백하고 500 `PII_NOT_REMOVED`를 돌려준다(트리거 누락 감지).
6. 성공 → 200 `{"order_no": "...", "status": "DELIVERED", "delivered_at": "...", "pii": "REMOVED_FROM_LOOKUP"}`
7. `access_logs`에 남긴다(`COMPLETE`).

### 7.5 `GET /me/assignments` (기사 인증, 선택)
→ 200 `[{"order_no": "...", "region": "...", "status": "..."}]`. 오늘 배정분만, 개인정보 없음.

### 7.6 `POST /orders` (관리자)
요청 `{"name": "홍길동", "phone": "010-0000-0001", "address": "가상로 12, 101동 1001호", "region": "인천 연수구 송도동"}`

- `order_no` = `SC-YYYYMMDD-NNNN` (KST 날짜, 그날 순번 4자리)
- `qr_token` = `secrets.token_urlsafe(16)`
- `safe_number` = `050-` + 무작위 4자리 + `-` + 무작위 4자리
- 이름·전화·주소를 암호화해서 `orders` + `recipient_pii`에 넣는다.

→ 201 `{"order_no": "...", "qr_token": "...", "label_url": "/labels/SC-...", "qr_url": "/qr/SC-..."}`

### 7.7 `GET /qr/{order_no}`
→ `image/png`. **QR 내용 = `qr_token` 문자열 그대로.** 없는 주문이면 404 `ORDER_NOT_FOUND`.

### 7.8 `GET /labels/{order_no}`
→ `text/html`. 인쇄용 운송장 1장. 보이는 것: "seungcurity 안심 운송장", QR 이미지(`/qr/{order_no}`), 주문번호, "이 운송장에는 개인정보가 인쇄되지 않습니다." 문구.
**이름·전화·주소·권역은 넣지 않는다.** 외부 템플릿 엔진 없이 문자열 + `html.escape`로 만든다.

### 7.9 `GET /track/{order_no}` (주문자 웹)
→ 200
```json
{"order_no": "...", "status": "DELIVERED", "status_label": "배송 완료",
 "delivered_at": "2026-11-14T14:05:10+09:00",
 "pii_status": "BLOCKED", "pii_message": "개인정보 조회가 차단되었습니다."}
```
- `status_label`: `READY` 발송 준비 · `AT_HUB` 허브 도착 · `OUT_FOR_DELIVERY` 배송 중 · `DELIVERED` 배송 완료
- `pii_status`: 배송 전 `VIEWABLE_BY_ASSIGNED_DRIVER`(message "담당 기사만 볼 수 있습니다."), 완료 후 `BLOCKED`

### 7.10 관리자 API (`X-Admin-Token`)

| 경로 | 하는 일 | 응답 |
|---|---|---|
| `POST /admin/reset-demo` | 모든 행을 지우고 `DEMO_DATA`로 다시 채운다(배정은 오늘 날짜). `UPLOAD_DIR`도 비운다. 시연을 다시 찍을 때 쓴다 | 200 `{"ok": true, "drivers": 4, "orders": [{"order_no", "qr_token", "assign_to"}], "photos_removed": N}` |
| `POST /admin/assign` | 요청 `{"driver_id": "D001", "order_no": "..."}` → 오늘 배정 추가. 오늘 다른 기사에게 배정돼 있으면 이 기사로 옮긴다 | 200 `{"driver_id", "order_no", "work_date", "previous_driver_id"}`. 없는 기사 404 `DRIVER_NOT_FOUND`, HUB 403 `ROLE_FORBIDDEN`, 없는 주문 404 `ORDER_NOT_FOUND`, 완료된 주문 409 `ALREADY_DELIVERED` |
| `POST /admin/drivers/{driver_id}/reset-device` | `device_uuid`를 비운다(다른 폰으로 다시 묶기) | 200 `{"driver_id", "device_uuid": null}`. 없는 기사 404 `DRIVER_NOT_FOUND` |
| `GET /admin/logs?limit=50` | 최근 `access_logs`(개인정보 없음), 최신순, `limit` 1~500 | 200 `[{"id", "driver_id", "order_id", "order_no", "action", "result", "at"}]` |
| `GET /admin/db-peek/{order_no}` | 그 건의 `recipient_pii`·`pii_archive` 행을 **암호문 그대로** 보여준다(복호화 금지). 영상에서 "DB에는 암호문만 있다"를 보여주는 용도 | 200 `{"order_no", "order_id", "status", "recipient_pii": {열 그대로} \| null, "pii_archive": [{열 그대로}]}`. 없는 주문 404 `ORDER_NOT_FOUND` |

### 7.11 그 밖
- `../web` 폴더가 있으면 `/web`으로 정적 서빙한다(`html=True`). 주문자 웹이 같은 주소에서 열린다.
- CORS: 모든 origin 허용, credentials 없음(시연용).

### 오류 코드 모음

| HTTP | code | 언제 |
|---|---|---|
| 401 | `INVALID_CREDENTIALS` | ID·비밀번호 틀림 |
| 401 | `TOKEN_INVALID` / `TOKEN_EXPIRED` | JWT 문제 |
| 401 | `ADMIN_ONLY` | 관리자 토큰 틀림 |
| 403 | `NO_ASSIGNMENT_TODAY` | 오늘 배정 없는 기사의 로그인 |
| 403 | `DEVICE_MISMATCH` | 등록된 기기가 아님 |
| 403 | `NOT_ASSIGNED` | 담당 기사가 아님 |
| 403 | `ROLE_FORBIDDEN` | 허브가 배송 완료 시도 |
| 404 | `ORDER_NOT_FOUND` | 없는 토큰·주문번호 |
| 404 | `DRIVER_NOT_FOUND` | 관리자 API에서 없는 기사 ID |
| 409 | `ALREADY_DELIVERED` | 이미 완료된 건을 다시 완료 |
| 410 | `PII_REMOVED` | 완료된 건을 스캔 |
| 413 | `FILE_TOO_LARGE` | 사진 10MB 초과 |
| 415 | `UNSUPPORTED_MEDIA` | 사진 형식 아님 |
| 500 | `PII_NOT_REMOVED` | 트리거가 개인정보를 안 옮김 |

---

## 8. 시드 데이터

- 형식(`demo_data.json`, 최종본은 주승윤이 `../db/`에 작성):
```json
{"drivers": [{"driver_id": "D001", "name": "김담당", "password": "demo1234", "role": "DRIVER"}],
 "orders":  [{"name": "홍길동", "phone": "010-0000-0001", "address": "가상로 12, 101동 1001호",
              "region": "인천 연수구 송도동", "assign_to": "D001"}]}
```
- `python -m app.seed`: SQLite 파일이 없으면 `db/sqlite_schema.sql`을 먼저 적용한다. 그다음 모든 행을 지우고(FK 순서 주의), 비밀번호는 해시, 개인정보는 암호화해서 넣는다. `assign_to`가 있으면 **오늘(KST)** 날짜로 배정한다. MySQL에서는 테이블을 만들지 않는다(주승윤의 `schema.sql`을 먼저 적용).
- Claude Code가 만들 샘플(`demo/demo_data.sample.json`), 누가 봐도 가상인 값으로:
  - 기사 4명(비밀번호 모두 `demo1234`): `H001` 이허브(HUB) · `D001` 김담당 · `D002` 박다른 · `D003` 최휴무(배정 없음 → 로그인 거부 시연)
  - 주문 6건: 이름은 홍길동 같은 예시 이름, 주소는 "가상로 ○○", 전화는 `010-0000-000N`. 1~4번은 `D001`, 5~6번은 `D002`에 배정

---

## 9. 작업 순서와 완료 기준

| 단계 | 언제까지 | 할 일 | 완료 기준 |
|---|---|---|---|
| 1 | 11/12(목) 17:00 | 골격, config, SQLite 스키마, crypto, security, seed, `/health`, `/auth/login`, `/scan`, `/track`, README | `uvicorn` 실행 → `/docs`에서 D001 로그인 → 스캔 성공. **§7을 팀 단톡에 공유** |
| 2 | 11/12(목) 밤 | `/deliveries/.../complete`, `/orders`, `/qr`, `/labels`, `/me/assignments`, 관리자 API, `/web` 서빙 | 완료 후 재스캔하면 410 |
| 3 | 11/13(금) 17:30 | `tests/test_flow.py` §10 전부 | `pytest` 전부 통과 |
| 4 | 11/13(금) 밤 | 주승윤의 `schema.sql`로 MySQL 전환, §10 흐름을 수동으로 다시 확인 | MySQL에서 완료 → `recipient_pii`가 비고 `pii_archive`에 1행. 문제가 있으면 `schema.sql`을 고치지 말고 `CHANGES.md`에 적는다 |
| 5 | 11/13(금) 23:00 | Cloudflare 터널로 한승일 폰과 연결 | 앱에서 로그인 → 스캔 성공 |
| — | 11/14(토) 13:00 | **코드 동결** | 이후 버그 수정만 |

---

## 10. 테스트 시나리오 (`pytest`, 임시 SQLite 파일 + `TestClient`)

1. `D003`(오늘 배정 없음) 로그인 → 403 `NO_ASSIGNMENT_TODAY`
2. `D001` 첫 로그인으로 기기 A 등록 → 기기 B로 로그인 → 403 `DEVICE_MISMATCH`
3. 비밀번호 틀림 → 401 `INVALID_CREDENTIALS`
4. JWT는 기기 A, 헤더 `X-Device-UUID`는 기기 B → 403 `DEVICE_MISMATCH`
5. `H001` 스캔 → 200, `view`=`HUB`, 응답에 `recipient_name_masked`·`safe_number`·`address` 키 없음, 상태 `AT_HUB`
6. `D002`가 `D001`의 건을 스캔 → 403 `NOT_ASSIGNED`, 본문에 시드의 이름·주소 문자열 없음
7. `D001` 스캔 → 200, 가린 이름 형식(`홍O동`), 안심번호 `050-`로 시작, 주소 = 시드 주소, 워터마크에 `D001`
8. DB의 `recipient_pii.name_enc`는 평문 이름과 다르고, `decrypt`하면 평문과 같다
9. `D001` 완료(작은 PNG) → 200, `recipient_pii`에 0행, `pii_archive`에 1행, `keep_until` > `archived_at`
10. 완료된 건 재스캔 → 410 `PII_REMOVED` / 다시 완료 → 409 `ALREADY_DELIVERED`
11. `/track` → 완료 전 `VIEWABLE_BY_ASSIGNED_DRIVER`, 완료 후 `BLOCKED`
12. `/labels/{order_no}` HTML과 `/admin/logs` 응답에 시드의 이름·전화·주소 문자열이 없다
13. 스캔·로그인·완료마다 `access_logs`가 쌓인다
14. 관리자 토큰 없이 `/admin/reset-demo` → 401 `ADMIN_ONLY`. 토큰 있으면 초기 상태로 돌아간다
15. 트리거를 지운 DB에서 완료 → 500 `PII_NOT_REMOVED`, `orders.status`는 그대로(롤백)

---

## 11. 실행 방법 (README에 넣을 내용, Windows 기준)

```powershell
cd E:\Seungyeopo\QR_Security\server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # 키 값 채우기
python -m app.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 새 터미널: 다른 집에 있는 팀원이 접속할 주소 만들기
cloudflared tunnel --url http://localhost:8000
```

- 터미널에 나온 `https://...trycloudflare.com` 주소를 한승일·주승윤에게 보낸다. 터널을 다시 켜면 주소가 바뀐다(앱 로그인 화면의 "서버 주소" 칸에 새로 입력).
- 시연·촬영 중에는 노트북 절전을 끄고 충전기를 꽂아 둔다.
- 시연을 다시 찍기 전에는 `POST /admin/reset-demo`.

---

## 12. 하지 않는 것과 한계 (발표 때 말할 것)

- 실제 택배사 시스템·안심번호 서비스와 연동하지 않는다(가상 번호).
- `/track`은 주문번호만 알면 배송 상태를 볼 수 있다(개인정보는 나오지 않음).
- DB의 `DELETE`가 백업·로그까지 복구 불가능하게 지우는 것은 아니다. 실제 서비스라면 별도의 파기 절차가 필요하다.
- 키를 `.env` 파일에 둔다. 실제 서비스라면 키 관리 시스템을 쓴다.
- 서버가 1대라서 서버가 멈추면 허브·기사 모두 조회할 수 없다.
