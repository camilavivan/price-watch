"""Price check + alert logic."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.config import get_config
from app.models import PriceHistory, Product
from app.notifiers import notify_all

logger = logging.getLogger(__name__)

PLATFORM_LABEL = {"jd": "京东", "taobao": "淘宝/天猫", "pdd": "拼多多"}


def compute_landing(
    list_price: Optional[float],
    coupon: float = 0.0,
    full_reduction: float = 0.0,
) -> Optional[float]:
    if list_price is None:
        return None
    return round(max(list_price - (coupon or 0) - (full_reduction or 0), 0), 2)


def should_alert(
    product: Product,
    old_landing: Optional[float],
    new_landing: float,
) -> tuple[bool, str]:
    cfg = get_config().alerts
    reasons: list[str] = []

    if cfg.onBelowTarget and product.target_price is not None:
        if new_landing <= product.target_price:
            reasons.append(f"低于目标价 ¥{product.target_price:.2f}")

    drop_pct = (
        product.alert_drop_percent
        if product.alert_drop_percent is not None
        else cfg.dropPercent
    )
    drop_yuan = (
        product.alert_drop_yuan
        if product.alert_drop_yuan is not None
        else cfg.dropYuan
    )

    if old_landing is not None and old_landing > 0:
        delta = old_landing - new_landing
        if drop_yuan and drop_yuan > 0 and delta >= drop_yuan:
            reasons.append(f"较上次下降 ¥{delta:.2f}")
        if drop_pct and drop_pct > 0:
            pct = (delta / old_landing) * 100
            if pct >= drop_pct:
                reasons.append(f"较上次下降 {pct:.1f}%")

    return (bool(reasons), "；".join(reasons))


def format_alert(
    product: Product,
    old_landing: Optional[float],
    new_landing: float,
    reason: str,
) -> tuple[str, str]:
    plat = PLATFORM_LABEL.get(product.platform, product.platform)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old_s = f"¥{old_landing:.2f}" if old_landing is not None else "—"
    plain = (
        f"【到手价告警】{product.name}\n"
        f"平台：{plat}\n"
        f"原到手价：{old_s}\n"
        f"新手到价：¥{new_landing:.2f}\n"
        f"原因：{reason}\n"
        f"链接：{product.url or '—'}\n"
        f"时间：{now}"
    )
    md = (
        f"**到手价告警**\n"
        f"> 商品：{product.name}\n"
        f"> 平台：{plat}\n"
        f"> 原到手价：{old_s}\n"
        f"> 新手到价：<font color=\"warning\">¥{new_landing:.2f}</font>\n"
        f"> 原因：{reason}\n"
        f"> 链接：[打开]({product.url})\n"
        f"> 时间：{now}"
    )
    return plain, md


async def record_history(
    session: AsyncSession,
    product: Product,
    source: str = "check",
) -> None:
    if product.landing_price is None:
        return
    session.add(
        PriceHistory(
            product_id=product.id,
            list_price=product.list_price,
            coupon_amount=product.coupon_amount or 0,
            full_reduction=product.full_reduction or 0,
            rebate_estimate=product.rebate_estimate or 0,
            landing_price=product.landing_price,
            source=source,
            recorded_at=datetime.now(timezone.utc).replace(tzinfo=None),
        )
    )


async def apply_price_update(
    session: AsyncSession,
    product: Product,
    *,
    list_price: Optional[float] = None,
    coupon_amount: Optional[float] = None,
    full_reduction: Optional[float] = None,
    rebate_estimate: Optional[float] = None,
    landing_price: Optional[float] = None,
    source: str = "manual",
    send_alert: bool = True,
) -> dict:
    old_landing = product.landing_price

    if list_price is not None:
        product.list_price = list_price
    if coupon_amount is not None:
        product.coupon_amount = coupon_amount
    if full_reduction is not None:
        product.full_reduction = full_reduction
    if rebate_estimate is not None:
        product.rebate_estimate = rebate_estimate

    if landing_price is not None:
        product.landing_price = landing_price
    else:
        product.landing_price = compute_landing(
            product.list_price,
            product.coupon_amount or 0,
            product.full_reduction or 0,
        )

    product.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    product.last_check_at = product.updated_at
    if source == "manual":
        product.last_error = None
        # keep needs_manual for stub platforms
        adapter = get_adapter(product.platform)
        if not adapter.supports_auto:
            product.needs_manual = True

    await record_history(session, product, source=source)
    await session.commit()
    await session.refresh(product)

    alerted = False
    reason = ""
    if send_alert and product.landing_price is not None:
        alerted, reason = should_alert(product, old_landing, product.landing_price)
        if alerted:
            plain, md = format_alert(product, old_landing, product.landing_price, reason)
            await notify_all(plain, md)

    return {
        "product_id": product.id,
        "old_landing": old_landing,
        "new_landing": product.landing_price,
        "alerted": alerted,
        "reason": reason,
    }


async def check_product(session: AsyncSession, product: Product) -> dict:
    adapter = get_adapter(product.platform)
    result = await adapter.fetch(product.url or "", product.sku_id)
    product.last_check_at = datetime.now(timezone.utc).replace(tzinfo=None)

    if not result.ok:
        product.needs_manual = True
        product.last_error = result.error
        # Still schedule against last known — no price change, no alert
        await session.commit()
        return {
            "product_id": product.id,
            "ok": False,
            "needs_manual": True,
            "error": result.error,
        }

    product.needs_manual = False
    product.last_error = None
    if result.title and not product.name:
        product.name = result.title

    return await apply_price_update(
        session,
        product,
        list_price=result.list_price,
        coupon_amount=result.coupon_amount,
        full_reduction=result.full_reduction,
        source="check",
        send_alert=True,
    )


async def check_due_products(session: AsyncSession) -> list[dict]:
    cfg = get_config()
    default_interval = cfg.scheduler.defaultIntervalMinutes
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    q = await session.execute(select(Product).where(Product.enabled.is_(True)))
    products = list(q.scalars().all())
    results = []
    for p in products:
        interval = p.check_interval_minutes or default_interval
        if p.last_check_at is not None:
            elapsed = (now - p.last_check_at).total_seconds() / 60
            if elapsed < interval:
                continue
        try:
            r = await check_product(session, p)
            results.append(r)
        except Exception as e:
            logger.exception("check product %s failed", p.id)
            p.last_error = str(e)
            p.needs_manual = True
            await session.commit()
            results.append({"product_id": p.id, "ok": False, "error": str(e)})
    return results
