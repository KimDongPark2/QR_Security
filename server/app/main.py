"""FastAPI 앱: 라우터 등록, 오류 처리, CORS."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from .config import BASE_DIR, get_settings, today_kst
from .db import assignments, ensure_sqlite_schema, get_engine
from .errors import ApiError
from .routers import admin, auth, deliveries, orders, scan, track

WEB_DIR = BASE_DIR.parent / "web"  # 주승윤의 주문자 웹(있을 때만 서빙)


logger = logging.getLogger("uvicorn.error")  # uvicorn 콘솔에 같은 형식으로 찍힌다


def warn_if_no_assignments_today() -> bool:
    """오늘(KST) 배정이 0건이면 콘솔에 경고한다(이 상태에서는 기사가 아무도 로그인하지 못한다)."""
    today = today_kst()
    try:
        with get_engine().connect() as conn:
            count = conn.execute(
                select(func.count()).select_from(assignments).where(assignments.c.work_date == today)
            ).scalar_one()
    except SQLAlchemyError:
        logger.warning("오늘 배정 건수를 확인하지 못했습니다. DB 연결과 스키마를 확인하세요.")
        return False
    if count == 0:
        logger.warning(
            "오늘(%s) 배정이 0건입니다. python -m app.seed 또는 POST /admin/reset-demo를 실행하세요.",
            today.isoformat(),
        )
        return True
    return False


@asynccontextmanager
async def lifespan(_app: FastAPI):
    get_settings()  # 환경 변수가 빠졌으면 시작할 때 바로 알린다
    ensure_sqlite_schema(get_engine())  # SQLite가 비어 있으면 스키마만 만든다(데이터는 seed)
    warn_if_no_assignments_today()
    yield


app = FastAPI(title="seungcurity QR 안심 운송장 서버", version="0.1.0", lifespan=lifespan)

# 시연용: 모든 origin 허용, credentials 없음
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content={"code": exc.code, "message": exc.message})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError):
    # FastAPI 기본 422 구조는 유지하되, 보낸 값이 담기는 input·ctx는 지운다(개인정보·비밀번호 보호).
    detail = [{"type": e.get("type"), "loc": list(e.get("loc", ())), "msg": e.get("msg")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(detail)})


@app.get("/health", tags=["health"])
def health():
    return {"ok": True}


app.include_router(auth.router)
app.include_router(scan.router)
app.include_router(deliveries.router)
app.include_router(orders.router)
app.include_router(track.router)
app.include_router(admin.router)

# ../web 폴더가 있으면 /web 으로 정적 서빙(폴더가 새로 생기면 서버를 다시 켠다)
if WEB_DIR.is_dir():
    app.mount("/web", StaticFiles(directory=WEB_DIR, html=True), name="web")
