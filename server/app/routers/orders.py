"""POST /orders (§7.6), GET /qr/{order_no} (§7.7), GET /labels/{order_no} (§7.8)."""
from __future__ import annotations

import html
import io
from urllib.parse import quote

import qrcode
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..db import get_engine, insert_order, orders
from ..errors import ApiError
from ..security import require_admin

router = APIRouter(tags=["orders"])


class OrderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50, examples=["홍길동"])
    phone: str = Field(min_length=1, max_length=30, examples=["010-0000-0001"])
    address: str = Field(min_length=1, max_length=200, examples=["가상로 12, 101동 1001호"])
    region: str = Field(min_length=1, max_length=50, examples=["인천 연수구 송도동"])


@router.post("/orders", status_code=201, dependencies=[Depends(require_admin)])
def create_order(body: OrderCreate):
    # 같은 순간 두 요청이 같은 순번을 잡으면 UNIQUE에 걸리므로 몇 번 다시 시도한다.
    for attempt in range(5):
        try:
            with get_engine().begin() as conn:
                order = insert_order(conn, name=body.name, phone=body.phone,
                                     address=body.address, region=body.region)
            break
        except IntegrityError:
            if attempt == 4:
                raise
    no = quote(order["order_no"])
    return {
        "order_no": order["order_no"],
        "qr_token": order["qr_token"],
        "label_url": f"/labels/{no}",
        "qr_url": f"/qr/{no}",
    }


def _find_order(order_no: str) -> dict:
    with get_engine().connect() as conn:
        row = conn.execute(
            select(orders.c.order_no, orders.c.qr_token).where(orders.c.order_no == order_no)
        ).mappings().first()
    if row is None:
        raise ApiError(404, "ORDER_NOT_FOUND")
    return dict(row)


@router.get("/qr/{order_no}", response_class=Response,
            responses={200: {"content": {"image/png": {}}}})
def qr_png(order_no: str):
    order = _find_order(order_no)
    img = qrcode.make(order["qr_token"])  # QR 내용 = qr_token 문자열 그대로
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")


LABEL_TEMPLATE = """<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>운송장 {order_no}</title>
<style>
  body {{ font-family: "Malgun Gothic", sans-serif; margin: 0; padding: 24px; background: #f4f4f4; }}
  .label {{ width: 100mm; margin: 0 auto; padding: 8mm; background: #fff; border: 2px solid #111; box-sizing: border-box; text-align: center; }}
  h1 {{ font-size: 18px; margin: 0 0 6mm; }}
  img {{ width: 60mm; height: 60mm; image-rendering: pixelated; }}
  .no {{ font-family: Consolas, monospace; font-size: 16px; font-weight: bold; margin: 4mm 0; }}
  .notice {{ font-size: 12px; color: #333; border-top: 1px dashed #999; padding-top: 3mm; }}
  @media print {{ body {{ background: #fff; padding: 0; }} }}
</style>
</head>
<body>
<div class="label">
  <h1>seungcurity 안심 운송장</h1>
  <img src="/qr/{order_no_url}" alt="QR 코드">
  <div class="no">{order_no}</div>
  <div class="notice">이 운송장에는 개인정보가 인쇄되지 않습니다.</div>
</div>
</body>
</html>
"""


@router.get("/labels/{order_no}", response_class=HTMLResponse)
def label(order_no: str):
    order = _find_order(order_no)
    # 이름·전화·주소·권역은 넣지 않는다.
    return LABEL_TEMPLATE.format(
        order_no=html.escape(order["order_no"]),
        order_no_url=html.escape(quote(order["order_no"])),
    )
