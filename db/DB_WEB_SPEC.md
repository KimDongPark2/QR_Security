# DB · 주문자 웹 작업 명세서 — QR 안심 운송장 (담당: 주승윤)

> 이승엽이 정리한 작업 지시서. Claude Code에 그대로 줘도 된다.
> 서버(`server/`)는 끝났고 테스트 36개 + 정량 리포트까지 통과했다. 이 문서는 서버에 **맞춰서** 만들 것만 적었다.

## 0. 규칙
1. 고치는 곳은 `db/`와 `web/`뿐. `server/`는 건드리지 않는다. 서버에 바꿀 게 보이면 단톡에 먼저 말한다.
2. **실제 사람 이름·주소·전화번호 금지.** 저장소가 공개라서 그대로 인터넷에 올라간다.
3. 비밀번호, `.env`, 키는 커밋하지 않는다.
4. 테이블·열 이름은 아래 표(= `server/SERVER_SPEC.md` §4)에서 한 글자도 바꾸지 않는다. 서버 코드가 이 이름으로 조회한다.

## 1. 할 일

| # | 만들 것 |
|---|---|
| 1 | `db/schema.sql` — MySQL 8 테이블 6개 + 배송 완료 트리거 |
| 2 | `db/demo_data.json` — 시연용 가상 데이터 |
| 3 | `web/index.html` — 주문자 배송 확인 페이지 |
| 4 | 탐구: 한계와 후속 과제 / PPT / 발표 구간 녹화 |

---

## 2. `db/schema.sql` (MySQL 8)

### 테이블 약속

| 테이블 | 열 | 비고 |
|---|---|---|
| `drivers` | `driver_id` VARCHAR(10) PK · `name` VARCHAR(20) NOT NULL · `password_hash` VARCHAR(200) NOT NULL · `role` VARCHAR(10) NOT NULL · `device_uuid` VARCHAR(64) NULL | `role`은 CHECK로 `HUB`, `DRIVER`만 |
| `orders` | `id` INT AUTO_INCREMENT PK · `order_no` VARCHAR(20) NOT NULL UNIQUE · `qr_token` VARCHAR(32) NOT NULL UNIQUE · `region` VARCHAR(50) NOT NULL · `status` VARCHAR(20) NOT NULL DEFAULT 'READY' · `created_at` DATETIME NOT NULL · `delivered_at` DATETIME NULL · `photo_path` VARCHAR(200) NULL | `status`는 CHECK로 `READY`, `AT_HUB`, `OUT_FOR_DELIVERY`, `DELIVERED`만. **개인정보 열 없음** |
| `recipient_pii` | `order_id` INT PK, FK→`orders(id)` · `name_enc` TEXT NOT NULL · `phone_enc` TEXT NOT NULL · `address_enc` TEXT NOT NULL · `safe_number` VARCHAR(20) NOT NULL | 조회용. `*_enc`에는 서버가 AES-256-GCM 암호문(base64)을 넣는다 |
| `pii_archive` | `id` INT AUTO_INCREMENT PK · `order_id` INT NOT NULL · `name_enc` TEXT NOT NULL · `phone_enc` TEXT NOT NULL · `address_enc` TEXT NOT NULL · `archived_at` DATETIME NOT NULL · `keep_until` DATETIME NOT NULL | 보존용. FK 없음 |
| `assignments` | `id` INT AUTO_INCREMENT PK · `driver_id` VARCHAR(10) NOT NULL FK→`drivers` · `order_id` INT NOT NULL FK→`orders` · `work_date` DATE NOT NULL · UNIQUE(`order_id`, `work_date`) · INDEX(`driver_id`, `work_date`) | 오늘 배정 = 로그인·조회 조건 |
| `access_logs` | `id` INT AUTO_INCREMENT PK · `driver_id` VARCHAR(10) NOT NULL · `order_id` INT NULL · `action` VARCHAR(20) NOT NULL · `result` VARCHAR(30) NOT NULL · `at` DATETIME NOT NULL | `driver_id`에 **FK 걸지 않기** (없는 ID 로그인 시도, `ADMIN`도 기록함) |

- 모든 테이블 `ENGINE=InnoDB DEFAULT CHARSET=utf8mb4`
- 파일 맨 위에 `DROP TRIGGER IF EXISTS`, `DROP TABLE IF EXISTS`(자식 → 부모 순서)를 넣어서 **여러 번 실행해도** 되게 한다.
- `CREATE DATABASE`는 넣지 않는다(DB 이름은 각자 정함, 예: `seungcurity`).

### 배송 완료 트리거 (핵심)

`orders.status`가 `DELIVERED`로 바뀌는 순간(이전 값이 `DELIVERED`가 아닐 때), 같은 `order_id`의 `recipient_pii` 행을 `pii_archive`로 복사하고 `recipient_pii`에서 지운다. `safe_number`는 복사하지 않는다.

```sql
DELIMITER //
CREATE TRIGGER trg_orders_delivered
AFTER UPDATE ON orders
FOR EACH ROW
BEGIN
  IF NEW.status = 'DELIVERED' AND OLD.status <> 'DELIVERED' THEN
    INSERT INTO pii_archive (order_id, name_enc, phone_enc, address_enc, archived_at, keep_until)
    SELECT order_id, name_enc, phone_enc, address_enc,
           UTC_TIMESTAMP() + INTERVAL 9 HOUR,
           UTC_TIMESTAMP() + INTERVAL 9 HOUR + INTERVAL 5 YEAR
    FROM recipient_pii WHERE order_id = NEW.id;
    DELETE FROM recipient_pii WHERE order_id = NEW.id;
  END IF;
END//
DELIMITER ;
```

꼭 알아둘 것
- **시각은 `NOW()` 말고 `UTC_TIMESTAMP() + INTERVAL 9 HOUR`.** 서버는 모든 시각을 KST로 저장한다. `NOW()`는 MySQL 설정에 따라 UTC가 들어가서 9시간이 어긋난다(MariaDB로 실제 확인함).
- **왜 개인정보가 `orders`와 다른 테이블에 있나**: MySQL 트리거는 자기를 실행시킨 테이블(`orders`)을 고칠 수 없다(오류 1442). 그래서 지울 대상을 `recipient_pii`로 따로 뺐다. 발표 때 설명할 포인트.
- **5년**: 전자상거래법 시행령 제6조(대금결제·재화 공급 기록 5년)를 참고한 시연용 값. 기간이 지나면 서버의 `POST /admin/purge-expired`가 `pii_archive`를 지운다.
- `DELIMITER`는 MySQL 명령줄·Workbench용 문법이다. **`schema.sql`은 root 계정으로 실행**한다. 다른 계정으로 트리거를 만들면 바이너리 로그 설정 때문에 권한 오류(1419)가 날 수 있다.

---

## 3. `db/demo_data.json`

형식(서버의 `python -m app.seed`가 읽어서 비밀번호는 해시, 개인정보는 암호화해서 넣는다. **여기엔 평문으로 쓴다**):

```json
{
  "drivers": [
    {"driver_id": "H001", "name": "이허브", "password": "demo1234", "role": "HUB"}
  ],
  "orders": [
    {"name": "홍길동", "phone": "010-0000-0001", "address": "가상로 12, 101동 1001호",
     "region": "인천 연수구 송도동", "assign_to": "D001"}
  ]
}
```

조건
- 기사 ID **`H001`(HUB), `D001`, `D002`, `D003`(DRIVER)는 꼭 있어야 한다.** 시연 대본이 이 ID를 쓴다. `D003`에는 배정을 주지 않는다(로그인 거부 장면). 이름은 바꿔도 된다.
- 주문 6~8건. `D001`에 3~4건, `D002`에 2건, **`assign_to: null`인 주문 1건 이상**(관리자가 배정하는 장면용).
- 이름: 홍길동처럼 누가 봐도 가상. 전화: `010-0000-00NN`. 주소: "가상로 ○○, ○동 ○호". 권역(`region`)은 실제 동 이름 써도 된다(동 단위라 개인을 특정할 수 없음).
- 참고 샘플: `server/demo/demo_data.sample.json`

---

## 4. `web/index.html` — 주문자 배송 확인 페이지

서버가 `web/` 폴더를 `/web/` 주소로 띄워 준다(`http://localhost:8000/web/`, 터널이면 `https://…trycloudflare.com/web/`). 폴더를 새로 만들었으면 서버를 한 번 껐다 켠다.

쓰는 API는 하나뿐:
`GET /track/{order_no}` → 
```json
{"order_no": "SC-20261114-0001", "status": "DELIVERED", "status_label": "배송 완료",
 "delivered_at": "2026-11-14T14:05:10+09:00",
 "pii_status": "BLOCKED", "pii_message": "개인정보 조회가 차단되었습니다."}
```
- 없는 주문번호 → 404 `{"code": "ORDER_NOT_FOUND", "message": "배송 건을 찾을 수 없습니다."}`
- `status`: `READY` 발송 준비 → `AT_HUB` 허브 도착 → `OUT_FOR_DELIVERY` 배송 중 → `DELIVERED` 배송 완료
- `pii_status`: 배송 전 `VIEWABLE_BY_ASSIGNED_DRIVER`, 완료 후 `BLOCKED`

요구 사항
1. 입력칸은 **주문번호 하나**. 이름·전화번호 같은 개인정보는 입력받지 않는다.
2. 주소창에 `?no=SC-...`가 있으면 자동으로 채워서 바로 조회한다(영상 촬영용).
3. 4단계 진행 표시(발송 준비 → 허브 도착 → 배송 중 → 배송 완료)에서 현재 단계를 강조한다.
4. `pii_status`가 `BLOCKED`면 "🔒 개인정보 조회가 차단되었습니다" 같은 눈에 띄는 상자로 보여 준다. **영상의 마지막 장면**이다.
5. **5초마다 자동 새로고침.** 기사가 앱에서 배송 완료를 누르면 이 화면이 저절로 바뀌는 장면을 찍는다.
6. fetch 주소는 **상대 경로** `/track/${encodeURIComponent(no)}`. 서버 주소를 코드에 적지 않는다(localhost·터널 둘 다 그대로 동작).
7. 404와 네트워크 오류는 `message`를 그대로 보여 준다.
8. 파일 하나(HTML 안에 CSS·JS)로, 외부 라이브러리·CDN 없이. 폰 화면에서도 보기 좋게.

---

## 5. 완료 확인 방법

`server/README.md`대로 서버를 각자 PC에서 켤 수 있다. MySQL로 확인하려면:

1. MySQL에서 빈 DB 만들기: `CREATE DATABASE seungcurity CHARACTER SET utf8mb4;`
2. root로 `schema.sql` 실행 (Workbench에서 열어 실행, 또는 `mysql -u root -p seungcurity < db/schema.sql`)
3. `server/.env`의 `DATABASE_URL`을 `mysql+pymysql://root:비밀번호@localhost:3306/seungcurity?charset=utf8mb4`로 바꾸기
4. `python -m app.seed` → 주문번호·토큰 출력 확인
5. 서버를 켜고 `/docs`에서: D001 로그인 → 자기 건 스캔(200) → 배송 완료(사진 아무 PNG) → 다시 스캔(410)
6. `/admin/db-peek/{주문번호}`(헤더 `X-Admin-Token`)에서 **`recipient_pii`가 null, `pii_archive`가 1행, `archived_at`이 지금 한국 시각**이면 통과
7. `http://localhost:8000/web/?no={주문번호}` → "배송 완료" + 차단 상자

MySQL 설치가 어려우면 2번까지 한 `schema.sql`과 `demo_data.json`만 올리고 단톡에 말하기 → 이승엽이 자기 PC에서 확인한다.

---

## 6. 탐구 · 발표 · PPT (주승윤 담당)

- **탐구: 한계와 후속 과제** (발표 "한계" 구간)
  - 서버가 1대라 멈추면 허브·기사 모두 조회 불가
  - 지하 주차장처럼 통신이 안 되는 곳에서는 스캔 불가
  - 반품·분실·오배송 처리 때 배송 완료 후 정보가 다시 필요할 수 있음
  - DB의 `DELETE`는 백업·로그까지 복구 불가능하게 지우는 것은 아님
  - 보존 테이블도 조회용과 같은 키로 암호화됨 → 후속 과제: 보존용 키 분리
  - 로그인 5회 실패 잠금은 남이 일부러 틀려서 진짜 기사를 잠글 수도 있음
- **PPT** (16:9, 첫 장에 발표 제목 + 3명 학번·이름): 서버 정량 결과는 `server/report/result.md` 표를 그대로 쓴다.
- **발표 구간**: 데이터 보호 구조(테이블 2개 + 트리거 + 암호문만 저장) + 한계 + 시연 중 "재스캔하면 정보 없음 → 주문자 웹 차단 화면"
- **녹화할 화면**: `/admin/db-peek` (암호문만 보이는 화면), 주문자 웹이 '배송 중 → 배송 완료 + 차단'으로 바뀌는 장면
