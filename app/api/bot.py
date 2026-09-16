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
from app.history_external import fetch_external_history
from app.price_sanity import (
    min_plausible_price,
    reject_manual_price_reason,
)
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
    # Explicit current landing from「监控 url 当前价 [目标价]」
    current_price: Optional[float] = None
    # Single trailing number: if auto landing missing → current; else → target
    trailing_price: Optional[float] = None
    name: Optional[str] = None
    platform: Optional[str] = None
    # Bypass minPlausible / history sanity (explicit override)
    force_price: bool = False


class WatchTargetUpdate(BaseModel):
    openid: str = Field(..., min_length=1)
    target_price: float = Field(..., ge=0)


class WatchPriceUpdate(BaseModel):
    """QQ「填价」/ Web bot API manual price.

    - landing_price only → list_price=landing, tax=0, landing=landing
    - list_price (+ optional tax_amount) → landing = list + tax - coupon - full_reduction
    """

    openid: str = Field(..., min_length=1)
    landing_price: Optional[float] = None
    list_price: Optional[float] = None
    tax_amount: Optional[float] = None
    force_price: bool = False


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


def _serialize_create(
    p: Product,
    *,
    used_manual_current: bool = False,
    rejected_manual_price: float | None = None,
    reject_message: str | None = None,
) -> dict[str, Any]:
    data = _serialize(p)
    data["used_manual_current"] = bool(used_manual_current)
    data["rejected_manual_price"] = rejected_manual_price
    data["reject_message"] = reject_message
    return data


async def _history_lowest_for_url(url: str) -> float | None:
    """Best-effort manmanbuy lowest for sanity checks."""
    if not url:
        return None
    try:
        series = await fetch_external_history(url)
        if series and series.lowest is not None and float(series.lowest) > 0:
            return float(series.lowest)
    except Exception as e:
        logger.debug("history lowest soft-fail: %s", e)
    return None


def _fetch_sanity_cfg() -> tuple[float, float]:
    cfg = get_config().fetch
    return (
        min_plausible_price(getattr(cfg, "minPlausiblePrice", 10)),
        float(getattr(cfg, "historyBogusFraction", 0.2) or 0.2),
    )


def _stats_dict(stats, *, source: str = "local", source_label: str | None = None) -> dict[str, Any]:
    label = source_label or ("来源：慢慢买" if source == "manmanbuy" else "来源：本地自采")
    return {
        "days": stats.days,
        "count": stats.count,
        "lowest": stats.lowest,
        "highest": stats.highest,
        "avg": stats.avg,
        "is_history_low": stats.is_history_low,
        "sparkline": sparkline(stats.prices),
        "source": source,
        "source_label": label,
    }


def _external_to_stats_dict(series, *, current_landing=None, days: int | None = None) -> dict[str, Any]:
    """Build history_stats dict from manmanbuy series (prefer for display)."""
    from app.config import get_config
    from app.services import is_near_history_low

    cfg = get_config().alerts
    days_v = int(days if days is not None else cfg.historyLowDays or 90)
    prices = series.prices()
    # Optionally trim to lookback window by ts
    if series.points and days_v > 0:
        import time
        cutoff_ms = int((time.time() - days_v * 86400) * 1000)
        trimmed = [p.price for p in series.points if p.ts >= cutoff_ms]
        if trimmed:
            prices = trimmed
    lowest = min(prices) if prices else series.lowest
    highest = max(prices) if prices else series.highest
    avg = round(sum(prices) / len(prices), 2) if prices else series.avg
    at_low = False
    if current_landing is not None and lowest is not None:
        at_low = is_near_history_low(
            float(current_landing), float(lowest), cfg.historyLowTolerancePercent
        )
    return {
        "days": days_v,
        "count": len(prices),
        "lowest": lowest,
        "highest": highest,
        "avg": avg,
        "is_history_low": at_low,
        "sparkline": sparkline(prices),
        "source": "manmanbuy",
        "source_label": "来源：慢慢买",
    }


async def _preferred_history_stats(product, db, local_stats) -> dict[str, Any]:
    """Prefer external (慢慢买) series for QQ 历史/详情 display; fall back to local."""
    url = product.canonical_url or product.url or ""
    try:
        series = await fetch_external_history(url) if url else None
    except Exception as e:
        logger.warning("external history soft-fail id=%s: %s", product.id, e)
        series = None
    if series and series.count > 0:
        return _external_to_stats_dict(
            series, current_landing=product.landing_price, days=local_stats.days
        )
    return _stats_dict(local_stats, source="local")


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

    used_manual_current = False
    rejected_manual_price = None
    reject_message = None
    current = body.current_price
    trailing = body.trailing_price
    target = body.target_price
    force = bool(body.force_price)
    min_p, hist_frac = _fetch_sanity_cfg()

    # Resolve single trailing number after auto fetch
    if trailing is not None and current is None and target is None:
        if product.landing_price is None:
            current = trailing
        else:
            target = trailing

    if target is not None:
        product.target_price = float(target)

    # Use user current when auto empty (tax 0) — with plausibility / history sanity
    if current is not None and product.landing_price is None:
        if current < 0:
            raise HTTPException(status_code=400, detail="当前到手价不能为负")
        lp = float(current)
        hist_low = await _history_lowest_for_url(
            product.canonical_url or product.url or store_url
        )
        reason = reject_manual_price_reason(
            lp,
            min_plausible=min_p,
            history_lowest=hist_low,
            history_fraction=hist_frac,
            force=force,
        )
        if reason:
            rejected_manual_price = lp
            reject_message = reason
            product.needs_manual = True
            if not product.last_error:
                product.last_error = reason
            await db.commit()
            await db.refresh(product)
            logger.info(
                "rejected manual current id=%s price=%s reason=%s",
                product.id,
                lp,
                reason,
            )
        else:
            await apply_price_update(
                db,
                product,
                list_price=lp,
                tax_amount=0.0,
                landing_price=lp,
                source="manual",
                send_alert=False,
            )
            used_manual_current = True
            await db.refresh(product)
    elif target is not None:
        # Persist target-only change if we did not apply_price_update
        await db.commit()
        await db.refresh(product)

    return {
        "watch": _serialize_create(
            product,
            used_manual_current=used_manual_current,
            rejected_manual_price=rejected_manual_price,
            reject_message=reject_message,
        )
    }


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
    local = await compute_local_history_stats(
        db, product.id, current_landing=product.landing_price
    )
    stats = await _preferred_history_stats(product, db, local)
    return {"watch": _serialize(product, history_stats=stats)}


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
    local = await compute_local_history_stats(
        db, watch_id, current_landing=product.landing_price
    )
    stats = await _preferred_history_stats(product, db, local)
    # When external series available, also expose a compact external recent list
    external_history = []
    if stats.get("source") == "manmanbuy":
        try:
            series = await fetch_external_history(product.canonical_url or product.url or "")
            if series and series.points:
                # last N points for display (newest first to match local order)
                tail = series.points[-limit:]
                external_history = [
                    {
                        "landing_price": p.price,
                        "list_price": p.price,
                        "tax_amount": 0,
                        "coupon_amount": 0,
                        "full_reduction": 0,
                        "source": "manmanbuy",
                        "recorded_at": __import__("datetime").datetime.utcfromtimestamp(
                            p.ts / 1000.0
                        ).isoformat(timespec="seconds"),
                    }
                    for p in reversed(tail)
                ]
        except Exception as e:
            logger.warning("external history points soft-fail: %s", e)
    return {
        "watch_id": watch_id,
        "history": history,
        "history_stats": stats,
        "external_history": external_history,
        "history_source": stats.get("source", "local"),
    }


@router.post("/watches/{watch_id}/target")
@router.patch("/watches/{watch_id}/target")
async def update_watch_target(
    watch_id: int,
    body: WatchTargetUpdate,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(_require_admin_token),
):
    """QQ「目标」— set target_price only."""
    openid = body.openid.strip()
    product = await db.get(Product, watch_id)
    if not product or product.owner_openid != openid:
        raise HTTPException(status_code=404, detail="监控不存在或不属于你")
    product.target_price = float(body.target_price)
    await db.commit()
    await db.refresh(product)
    return {"watch": _serialize(product)}


@router.get("/browser/jd-status")
async def browser_jd_status(_: None = Depends(_require_admin_token)):
    """QQ「登录状态」— Playwright / JD cookie hint (no secrets)."""
    try:
        from app.browser.jd_session import status_dict

        return status_dict()
    except Exception as e:
        logger.warning("browser status failed: %s", e)
        return {
            "playwright_enabled": False,
            "has_storage_state": False,
            "message": f"browser module unavailable: {e}",
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
    force = bool(body.force_price)
    min_p, hist_frac = _fetch_sanity_cfg()
    hist_low = await _history_lowest_for_url(
        product.canonical_url or product.url or ""
    )

    if list_price is not None:
        if list_price < 0:
            raise HTTPException(status_code=400, detail="标价不能为负")
        tax_val = float(tax if tax is not None else 0)
        if tax_val < 0:
            raise HTTPException(status_code=400, detail="税费不能为负")
        candidate = float(list_price) + tax_val
        reason = reject_manual_price_reason(
            candidate,
            min_plausible=min_p,
            history_lowest=hist_low,
            history_fraction=hist_frac,
            force=force,
        )
        if reason:
            raise HTTPException(
                status_code=400,
                detail=f"{reason}。或发：填价 {watch_id} 真实到手价",
            )
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
        reason = reject_manual_price_reason(
            lp,
            min_plausible=min_p,
            history_lowest=hist_low,
            history_fraction=hist_frac,
            force=force,
        )
        if reason:
            raise HTTPException(
                status_code=400,
                detail=f"{reason}。或发：填价 {watch_id} 真实到手价",
            )
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
