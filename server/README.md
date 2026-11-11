# seungcurity QR 안심 운송장 — 중앙 서버

FastAPI + SQLAlchemy 2 Core. 개발·테스트는 SQLite, 시연은 MySQL 8(`DATABASE_URL`만 바꿔서 전환).
API 명세는 [SERVER_SPEC.md](SERVER_SPEC.md) §7.

## 실행 (Windows)

```powershell
cd E:\Seungyeopo\QR_Security\server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env        # 키 값 채우기 (아래)
python -m app.seed
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 새 터미널: 다른 집에 있는 팀원이 접속할 주소 만들기
cloudflared tunnel --url http://localhost:8000
```

- 터미널에 나온 `https://...trycloudflare.com` 주소를 한승일·주승윤에게 보낸다. 터널을 다시 켜면 주소가 바뀐다(앱 로그인 화면의 "서버 주소" 칸에 새로 입력).
- 시연·촬영 중에는 노트북 절전을 끄고 충전기를 꽂아 둔다.
- 시연을 다시 찍기 전에는 `POST /admin/reset-demo` (헤더 `X-Admin-Token`). DB를 시드 상태로 되돌리고 사진을 지운다.
- `../web` 폴더가 있으면 `http://localhost:8000/web/` 에서 주문자 웹이 열린다(폴더가 새로 생기면 서버를 다시 켠다).
- 관리자 API의 응답 형식과 추가 오류 코드는 [CHANGES.md](CHANGES.md) 참고.

## 키 만들기

`.env`의 `JWT_SECRET`, `PII_KEY`, `ADMIN_TOKEN`에 각각 아래 명령 결과를 넣는다(매번 다른 값).

```powershell
python -c "import secrets,base64;print(base64.b64encode(secrets.token_bytes(32)).decode())"
```

`.env`와 키는 커밋하지 않는다. `PII_KEY`를 바꾸면 기존 암호문을 풀 수 없으므로 `python -m app.seed`를 다시 돌린다.

## 시드 데이터

`DEMO_DATA`(기본 `../db/demo_data.json`)가 없으면 `demo/demo_data.sample.json`을 쓴다. 모두 가상 데이터다.

| ID | 이름 | 역할 | 오늘 배정 |
|---|---|---|---|
| H001 | 이허브 | HUB | — |
| D001 | 김담당 | DRIVER | 주문 1~4 |
| D002 | 박다른 | DRIVER | 주문 5~6 |
| D003 | 최휴무 | DRIVER | 없음 → 로그인 거부 |

비밀번호는 모두 `demo1234`. `python -m app.seed`가 주문번호와 `qr_token`을 출력한다(개인정보는 출력하지 않음).

## /docs 에서 확인하기

1. `http://localhost:8000/docs` → `POST /auth/login`
   `{"driver_id": "D001", "password": "demo1234", "device_uuid": "demo-device-a"}` → `access_token` 복사
2. 오른쪽 위 **Authorize** → 토큰 붙여넣기
3. `GET /scan/{qr_token}` → `qr_token`에 시드 출력의 D001 토큰, `x-device-uuid`에 `demo-device-a` → 200 `view: DRIVER`

기기는 처음 로그인한 UUID에 묶인다. 다른 UUID로 로그인하면 403 `DEVICE_MISMATCH`(시드를 다시 돌리면 풀린다).

## 테스트

```powershell
pytest -q
```

임시 SQLite 파일과 테스트용 키로 돌아가므로 `.env`가 없어도 된다.
