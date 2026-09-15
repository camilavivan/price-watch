"""REST endpoints consumed by bot/ (X-Admin-Token)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.adapters.jd import extract_sku_id
from app.config import get_config
from app.db import get_db
from app.models import PriceHistory, Product
from app.platform_detect import detect_platform, guess_name_from_url
from app.services import check_product, compute_landing

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/bot", tags=["bot"])


def _require_admin_token(x_admin_token: Optional[str] = Header(None, alias="X-Admin-Token")) -> None:
    expected = (get_config().adminToken or "").strip()
    if not expected:
        return  # open within docker network when token unset
    if (x_admin_token or "").strip() != expected:
        raise HTTPException(status_code=401, detail="未授权：需要 X-Admin-Token")


class WatchCreate(BaseModel):
    openid: str = Field(..., min_length=1)
    url: str = Field(..., min_length=4)
    target_price: Optional[float] = None
    name: Optional[str] = None
    platform: Optional[str] = None


def _serialize(p: Product) -> dict[str, Any]:
    return {
        "id": p.id,
        "name": p.name,
        "platform": p.platform,
        "url": p.url,
        "sku_id": p.sku_id,
        "owner_openid": p.owner_openid,
        "list_price": p.list_price,
        "coupon_amount": p.coupon_amount,
        "full_reduction": p.full_reduction,
        "rebate_estimate": p.rebate_estimate,
        "landing_price": p.landing_price,
        "target_price": p.target_price,
        "enabled": p.enabled,
        "needs_manual": p.needs_manual,
        "image_url": p.image_url,
        "last_check_at": p.last_check_at.isoformat() if p.last_check_at else None,
        "last_error": p.last_error,
        "note": p.note,
    }


@router.get("/health")
async def bot_health(_: None = Depends(_require_admin_token)):
    return {"ok": True, "service": "price-watch-api"}


@router.get("/watches")
async def list_watches(
    openid: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin_token),
):
    q = await db.execute(
        select(Product)
        .where(Product.owner_openid == openid)
        .order_by(desc(Product.updated_at))
    )
    items = [_serialize(p) for p in q.scalars().all()]
    return {"watches": items}


@router.post("/watches")
async def create_watch(
    body: WatchCreate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin_token),
):
    openid = body.openid.strip()
    url = body.url.strip()
    platform = (body.platform or detect_platform(url) or "").lower()
    if platform not in ("jd", "taobao", "pdd"):
        raise HTTPException(
            status_code=400,
            detail="无法识别平台，请使用京东 / 淘宝 / 拼多多商品链接",
        )

    # Dedup by owner+url
    existing = await db.execute(
        select(Product).where(Product.owner_openid == openid, Product.url == url)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="你已监控该链接")

    adapter = get_adapter(platform)
    sku = extract_sku_id(url, None) if platform == "jd" else None
    name = (body.name or "").strip() or guess_name_from_url(url, platform)
    product = Product(
        name=name,
        platform=platform,
        url=url,
        sku_id=sku,
        owner_openid=openid,
        target_price=body.target_price,
        enabled=True,
        needs_manual=not adapter.supports_auto,
    )
    db.add(product)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="你已监控该链接")
    await db.refresh(product)

    # Best-effort immediate check
    try:
        await check_product(db, product)
        await db.refresh(product)
    except Exception as e:
        logger.warning("initial check failed for %s: %s", product.id, e)

    return {"watch": _serialize(product)}


@router.get("/watches/{watch_id}")
async def get_watch(
    watch_id: int,
    openid: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin_token),
):
    product = await db.get(Product, watch_id)
    if not product or product.owner_openid != openid:
        raise HTTPException(status_code=404, detail="监控不存在或不属于你")
    return {"watch": _serialize(product)}


@router.delete("/watches/{watch_id}")
async def delete_watch(
    watch_id: int,
    openid: str = Query(...),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin_token),
):
    product = await db.get(Product, watch_id)
    if not product or product.owner_openid != openid:
        raise HTTPException(status_code=404, detail="监控不存在或不属于你")
    hq = await db.execute(
        select(PriceHistory).where(PriceHistory.product_id == watch_id)
    )
    for h in hq.scalars().all():
        await db.delete(h)
    await db.delete(product)
    await db.commit()
    return {"ok": True, "deleted": watch_id}


@router.get("/watches/{watch_id}/history")
async def watch_history(
    watch_id: int,
    openid: str = Query(...),
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin_token),
):
    product = await db.get(Product, watch_id)
    if not product or product.owner_openid != openid:
        raise HTTPException(status_code=404, detail="监控不存在或不属于你")
    hq = await db.execute(
        select(PriceHistory)
        .where(PriceHistory.product_id == watch_id)
        .order_by(desc(PriceHistory.recorded_at))
        .limit(limit)
    )
    rows = list(hq.scalars().all())
    history = [
        {
            "landing_price": h.landing_price,
            "list_price": h.list_price,
            "coupon_amount": h.coupon_amount,
            "full_reduction": h.full_reduction,
            "source": h.source,
            "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None,
        }
        for h in rows
    ]
    return {"watch_id": watch_id, "history": history}


# silence unused import warning for compute_landing (kept for future)
_ = compute_landing
