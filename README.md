# QR 안심 운송장 (seungcurity)

운송장에 개인정보 대신 토큰 QR만 인쇄하고, 권한이 있는 사람에게만 필요한 만큼 보여주는 시스템.
2026 '나만의 이슈' 창체 동아리 주제탐구 발표 한마당 제출용.

## 폴더와 담당

| 폴더 | 담당 | 내용 |
|---|---|---|
| `server/` | 이승엽 | 중앙 서버 (Python FastAPI). API 약속은 `server/SERVER_SPEC.md` §7 |
| `app/` | 한승일 | 배송기사 앱 (Android Kotlin) |
| `db/` | 주승윤 | `schema.sql`(MySQL 테이블 + 배송 완료 트리거), `demo_data.json`(가상 데이터) |
| `web/` | 주승윤 | 주문자 확인 웹 (서버가 `/web`으로 띄움) |

각자 자기 폴더만 고친다. 다른 사람 폴더에 바꿀 게 있으면 단톡에 먼저 말하기.

## 서로 맞춘 약속
- API: `server/SERVER_SPEC.md` §7
- 테이블·열 이름: `server/SERVER_SPEC.md` §4
- 개인정보 암호화 형식: `server/SERVER_SPEC.md` §5 (AES-256-GCM, base64(nonce + 암호문))
- 데이터는 전부 가상 (실제 이름·주소·번호 금지)
