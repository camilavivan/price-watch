"""REST endpoints consumed by bot/ (X-Admin-Token)."""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.config import get_config
from app.db import get_db
from app.models import PriceHistory, Product
from app.services import (
    apply_price_update,
    check_watch,
    compute_local_history_stats,
    sparkline,
)
from app.url_normalize import (
    UnknownPlatformError,
    guess_name_from_url,
    normalize_url,
    resolve_url,
)

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


class WatchPriceUpdate(BaseModel):
    """QQ「填价」/ Web bot API manual price.

    - landing_price only → list_price=landing, tax=0, landing=landing
    - list_price (+ optional tax_amount) → landing = list + tax - coupon - full_reduction
    """

    openid: str = Field(..., min_length=1)
    landing_price: Optional[float] = None
    list_price: Optional[float] = None
    tax_amount: Optional[float] = None


def _serialize(p: Product, *, history_stats: dict | None = None) -> dict[str, Any]:
    data = {
        "id": p.id,
        "name": p.name,
        "platform": p.platform,
        "url": p.canonical_url or p.url,
        "canonical_url": p.canonical_url,
        "sku_id": p.sku_id,
        "owner_openid": p.owner_openid,
        "list_price": p.list_price,
        "tax_amount": p.tax_amount or 0,
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
    if history_stats is not None:
        data["history_stats"] = history_stats
    return data


def _stats_dict(stats) -> dict[str, Any]:
    return {
        "days": stats.days,
        "count": stats.count,
        "lowest": stats.lowest,
        "highest": stats.highest,
        "avg": stats.avg,
        "is_history_low": stats.is_history_low,
        "sparkline": sparkline(stats.prices),
    }


async def _find_existing(
    db: AsyncSession,
    openid: str,
    *,
    platform: str,
    sku_id: Optional[str],
    url: str,
    canonical_url: str,
) -> Optional[Product]:
    if sku_id:
        q = await db.execute(
            select(Product).where(
                Product.owner_openid == openid,
                Product.platform == platform,
                Product.sku_id == sku_id,
            )
        )
        found = q.scalar_one_or_none()
        if found:
            return found
    # Fall back to url / canonical_url match
    q = await db.execute(
        select(Product).where(
            Product.owner_openid == openid,
            or_(
                Product.url == url,
                Product.url == canonical_url,
                Product.canonical_url == canonical_url,
                Product.canonical_url == url,
            ),
        )
    )
    return q.scalar_one_or_none()


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
    raw_url = body.url.strip()
    # Expand short links (3.jd.hk / m.tb.cn / …) before normalize / sku extract
    resolved = await resolve_url(raw_url)
    try:
        info = normalize_url(resolved, require_known_platform=True)
    except UnknownPlatformError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    platform = (body.platform or info.platform or "").lower()
    if platform not in ("jd", "taobao", "pdd"):
        raise HTTPException(
            status_code=400,
            detail=(
                "无法识别平台，请使用京东 / 淘宝(天猫) / 拼多多商品链接"
                "（支持短链：m.tb.cn、tb.cn、u.jd.com、3.cn、3.jd.hk、"
                "p.pinduoduo.com 等，短链会自动跳转展开）"
            ),
        )

    canonical = info.canonical_url
    sku = info.sku_id
    store_url = canonical or resolved or raw_url

    existing = await _find_existing(
        db,
        openid,
        platform=platform,
        sku_id=sku,
        url=resolved or raw_url,
        canonical_url=canonical,
    )
    if existing:
        raise HTTPException(status_code=409, detail="你已监控该商品")

    adapter = get_adapter(platform)
    name = (body.name or "").strip() or guess_name_from_url(
        canonical or resolved or raw_url, platform
    )
    product = Product(
        name=name,
        platform=platform,
        url=store_url,
        canonical_url=canonical,
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
        raise HTTPException(status_code=409, detail="你已监控该商品")
    await db.refresh(product)

    # Best-effort immediate check
    try:
        await check_watch(db, product)
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
    stats = await compute_local_history_stats(
        db, product.id, current_landing=product.landing_price
    )
    return {"watch": _serialize(product, history_stats=_stats_dict(stats))}


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
            "tax_amount": h.tax_amount or 0,
            "coupon_amount": h.coupon_amount,
            "full_reduction": h.full_reduction,
            "source": h.source,
            "recorded_at": h.recorded_at.isoformat() if h.recorded_at else None,
        }
        for h in rows
    ]
    stats = await compute_local_history_stats(
        db, watch_id, current_landing=product.landing_price
    )
    return {
        "watch_id": watch_id,
        "history": history,
        "history_stats": _stats_dict(stats),
    }


@router.post("/watches/{watch_id}/price")
@router.patch("/watches/{watch_id}/price")
async def update_watch_price(
    watch_id: int,
    body: WatchPriceUpdate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin_token),
):
    """Manual price from QQ「填价」/「改价」/「手动价」."""
    openid = body.openid.strip()
    product = await db.get(Product, watch_id)
    if not product or product.owner_openid != openid:
        raise HTTPException(status_code=404, detail="监控不存在或不属于你")

    landing = body.landing_price
    list_price = body.list_price
    tax = body.tax_amount

    if list_price is not None:
        if list_price < 0:
            raise HTTPException(status_code=400, detail="标价不能为负")
        tax_val = float(tax if tax is not None else 0)
        if tax_val < 0:
            raise HTTPException(status_code=400, detail="税费不能为负")
        await apply_price_update(
            db,
            product,
            list_price=float(list_price),
            tax_amount=tax_val,
            source="manual",
            send_alert=True,
        )
    elif landing is not None:
        if landing < 0:
            raise HTTPException(status_code=400, detail="到手价不能为负")
        # 填价 <id> <到手价> → list_price=到手价, tax=0, landing=到手价
        lp = float(landing)
        await apply_price_update(
            db,
            product,
            list_price=lp,
            tax_amount=0.0,
            landing_price=lp,
            source="manual",
            send_alert=True,
        )
    else:
        raise HTTPException(
            status_code=400,
            detail="请提供 landing_price，或 list_price（可选 tax_amount）",
        )

    await db.refresh(product)
    return {"watch": _serialize(product)}
