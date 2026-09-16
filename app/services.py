"""Price check + alert logic + local first-party history stats."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters import get_adapter
from app.config import get_config
from app.models import PriceHistory, Product
from app.notifiers import notify_all

logger = logging.getLogger(__name__)

PLATFORM_LABEL = {"jd": "京东", "taobao": "淘宝/天猫", "pdd": "拼多多"}
_SPARK_CHARS = "▁▂▃▄▅▆▇█"


@dataclass
class LocalHistoryStats:
    days: int
    count: int
    lowest: Optional[float]
    highest: Optional[float]
    avg: Optional[float]
    prices: list[float]  # chronological
    is_history_low: bool = False


def compute_landing(
    list_price: Optional[float],
    coupon: float = 0.0,
    full_reduction: float = 0.0,
    tax_amount: float = 0.0,
) -> Optional[float]:
    """到手价 = 标价 + 税费 - 券 - 满减（floor at 0；返利不计）."""
    if list_price is None:
        return None
    v = (
        list_price
        + (tax_amount or 0)
        - (coupon or 0)
        - (full_reduction or 0)
    )
    return round(max(v, 0), 2)


def sparkline(values: list[float], width: int = 24) -> str:
    """Compact Unicode sparkline for QQ text."""
    if not values:
        return ""
    pts = values[-width:] if len(values) > width else values
    lo, hi = min(pts), max(pts)
    if hi <= lo:
        return _SPARK_CHARS[0] * len(pts)
    n = len(_SPARK_CHARS) - 1
    out = []
    for v in pts:
        idx = int(round((v - lo) / (hi - lo) * n))
        idx = max(0, min(n, idx))
        out.append(_SPARK_CHARS[idx])
    return "".join(out)


def is_near_history_low(
    current: float,
    lowest: Optional[float],
    tolerance_percent: float,
) -> bool:
    if lowest is None or lowest <= 0 or current is None:
        return False
    threshold = lowest * (1.0 + max(tolerance_percent, 0.0) / 100.0)
    return current <= threshold + 1e-9


def should_alert(
    product: Product,
    old_landing: Optional[float],
    new_landing: float,
    *,
    hist_stats: Optional[LocalHistoryStats] = None,
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
        noise = float(getattr(get_config().fetch, "priceNoisePercent", 0.5) or 0)
        # Ignore tiny upward/sideways noise; only evaluate drops beyond noise band
        noise_floor = old_landing * (noise / 100.0) if noise > 0 else 0.0
        meaningful_drop = delta > max(noise_floor, 1e-9)
        if meaningful_drop:
            if drop_yuan and drop_yuan > 0 and delta >= drop_yuan:
                reasons.append(f"较上次下降 ¥{delta:.2f}")
            if drop_pct and drop_pct > 0:
                pct = (delta / old_landing) * 100
                if pct >= drop_pct:
                    reasons.append(f"较上次下降 {pct:.1f}%")

    if cfg.onHistoryLow and hist_stats and hist_stats.lowest is not None and hist_stats.count > 0:
        tol = cfg.historyLowTolerancePercent
        at_low = is_near_history_low(new_landing, hist_stats.lowest, tol)
        was_at_low = (
            old_landing is not None
            and is_near_history_low(old_landing, hist_stats.lowest, tol)
        )
        days = hist_stats.days
        if at_low and not was_at_low:
            reasons.append(
                f"接近/达到近{days}天自采历史最低 ¥{hist_stats.lowest:.2f}"
            )
        elif at_low and old_landing is not None and new_landing < old_landing - 0.005:
            reasons.append(f"刷新近{days}天自采历史最低")

    return (bool(reasons), "；".join(reasons))


def format_alert(
    product: Product,
    old_landing: Optional[float],
    new_landing: float,
    reason: str,
    *,
    hist_stats: Optional[LocalHistoryStats] = None,
) -> str:
    plat = PLATFORM_LABEL.get(product.platform, product.platform)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    old_s = f"¥{old_landing:.2f}" if old_landing is not None else "—"
    lines = [
        f"【到手价告警】{product.name}",
        f"平台：{plat}",
        f"到手价：{old_s} → ¥{new_landing:.2f}",
        f"原因：{reason}",
    ]
    tax = float(product.tax_amount or 0)
    if tax > 0:
        lines.insert(3, f"税费：¥{tax:.2f}")
    if hist_stats and hist_stats.count > 0:
        lo = f"¥{hist_stats.lowest:.2f}" if hist_stats.lowest is not None else "—"
        hi = f"¥{hist_stats.highest:.2f}" if hist_stats.highest is not None else "—"
        avg = f"¥{hist_stats.avg:.2f}" if hist_stats.avg is not None else "—"
        low_tag = "是" if hist_stats.is_history_low else "否"
        lines.append(
            f"近{hist_stats.days}天自采：最低 {lo} / 均价 {avg} / 最高 {hi}（样本 {hist_stats.count}）"
        )
        lines.append(f"是否历史新低：{low_tag}")
        sp = sparkline(hist_stats.prices + [new_landing])
        if sp:
            lines.append(f"走势：{sp}")
    link = product.canonical_url or product.url or "—"
    lines.append(f"链接：{link}")
    lines.append(f"时间：{now}")
    return "\n".join(lines)


async def compute_local_history_stats(
    session: AsyncSession,
    product_id: int,
    *,
    days: Optional[int] = None,
    current_landing: Optional[float] = None,
) -> LocalHistoryStats:
    """Aggregate self-collected price_history over the lookback window."""
    cfg = get_config().alerts
    days_v = int(days if days is not None else cfg.historyLowDays or 90)
    if days_v <= 0:
        days_v = 90
    since = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days_v)
    q = await session.execute(
        select(PriceHistory)
        .where(
            PriceHistory.product_id == product_id,
            PriceHistory.recorded_at >= since,
        )
        .order_by(PriceHistory.recorded_at.asc())
    )
    rows = list(q.scalars().all())
    prices = [float(h.landing_price) for h in rows if h.landing_price is not None]
    lowest = min(prices) if prices else None
    highest = max(prices) if prices else None
    avg = round(sum(prices) / len(prices), 2) if prices else None
    cur = current_landing
    at_low = False
    if cur is not None and lowest is not None:
        at_low = is_near_history_low(cur, lowest, cfg.historyLowTolerancePercent)
    elif cur is not None and not prices:
        at_low = False
    return LocalHistoryStats(
        days=days_v,
        count=len(prices),
        lowest=lowest,
        highest=highest,
        avg=avg,
        prices=prices,
        is_history_low=at_low,
    )


async def recent_history_points(
    session: AsyncSession,
    product_id: int,
    limit: int = 5,
) -> list[dict[str, Any]]:
    hq = await session.execute(
        select(PriceHistory)
        .where(PriceHistory.product_id == product_id)
        .order_by(desc(PriceHistory.recorded_at))
        .limit(limit)
    )
    rows = list(hq.scalars().all())
    out = []
    for h in reversed(rows):
        ts = h.recorded_at.strftime("%m-%d %H:%M") if h.recorded_at else "?"
        out.append({"at": ts, "landing": h.landing_price})
    return out


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
            tax_amount=product.tax_amount or 0,
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
    tax_amount: Optional[float] = None,
    coupon_amount: Optional[float] = None,
    full_reduction: Optional[float] = None,
    rebate_estimate: Optional[float] = None,
    landing_price: Optional[float] = None,
    source: str = "manual",
    send_alert: bool = True,
) -> dict:
    old_landing = product.landing_price

    # Stats from existing samples (before appending this check)
    hist_before = await compute_local_history_stats(session, product.id)

    if list_price is not None:
        product.list_price = list_price
    if tax_amount is not None:
        product.tax_amount = tax_amount
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
            product.tax_amount or 0,
        )

    product.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    product.last_check_at = product.updated_at
    if source == "manual":
        # Manual fill (Web / QQ「填价」): clear error + needs_manual so list stops showing 需手动
        product.last_error = None
        product.needs_manual = False

    await record_history(session, product, source=source)
    await session.commit()
    await session.refresh(product)

    # Refresh stats including the new point for display
    hist_after = await compute_local_history_stats(
        session, product.id, current_landing=product.landing_price
    )
    # For alert decision use prior-window low, but mark is_history_low on after
    alert_stats = LocalHistoryStats(
        days=hist_before.days,
        count=hist_before.count,
        lowest=hist_before.lowest,
        highest=hist_before.highest,
        avg=hist_before.avg,
        prices=hist_before.prices,
        is_history_low=hist_after.is_history_low,
    )
    # If first samples: use after (new point may define the only low)
    if hist_before.count == 0 and hist_after.count > 0:
        alert_stats = hist_after

    alerted = False
    reason = ""
    if send_alert and product.landing_price is not None:
        alerted, reason = should_alert(
            product,
            old_landing,
            product.landing_price,
            hist_stats=alert_stats,
        )
        if alerted:
            plain = format_alert(
                product,
                old_landing,
                product.landing_price,
                reason,
                hist_stats=hist_after,
            )
            history = await recent_history_points(session, product.id, limit=5)
            hist_lines = "\n".join(
                f"  {h['at']}  ¥{h['landing']:.2f}" for h in history
            )
            if hist_lines:
                plain = f"{plain}\n近期价格：\n{hist_lines}"
            payload = {
                "title": product.name,
                "old_landing": old_landing,
                "new_landing": product.landing_price,
                "url": product.canonical_url or product.url,
                "reason": reason,
                "history": history,
                "image_url": product.image_url,
                "product_id": product.id,
                "history_stats": {
                    "days": hist_after.days,
                    "count": hist_after.count,
                    "lowest": hist_after.lowest,
                    "highest": hist_after.highest,
                    "avg": hist_after.avg,
                    "is_history_low": hist_after.is_history_low,
                    "sparkline": sparkline(hist_after.prices),
                },
            }
            await notify_all(
                plain,
                owner_openid=product.owner_openid,
                alert_payload=payload,
            )

    return {
        "product_id": product.id,
        "old_landing": old_landing,
        "new_landing": product.landing_price,
        "alerted": alerted,
        "reason": reason,
        "history_stats": {
            "days": hist_after.days,
            "count": hist_after.count,
            "lowest": hist_after.lowest,
            "highest": hist_after.highest,
            "avg": hist_after.avg,
            "is_history_low": hist_after.is_history_low,
        },
    }


async def check_watch(session: AsyncSession, product: Product) -> dict:
    """MarketEye-style engine: fetch → update → history → alerts → notify."""
    from app.adapters.product_meta import enrich_title_image

    adapter = get_adapter(product.platform)
    fetch_url = product.canonical_url or product.url or ""
    result = await adapter.fetch(fetch_url, product.sku_id)
    product.last_check_at = datetime.now(timezone.utc).replace(tzinfo=None)

    title = getattr(result, "title", None)
    image_url = getattr(result, "image_url", None)

    # Fallback enrich when adapter still missing title/image
    need_title = not title
    need_image = not image_url
    if need_title or need_image:
        try:
            t2, i2 = await enrich_title_image(
                platform=product.platform,
                url=fetch_url,
                sku_id=product.sku_id,
            )
            title = title or t2
            image_url = image_url or i2
        except Exception as e:
            logger.warning("enrich_title_image fallback failed id=%s: %s", product.id, e)

    # Always refresh placeholder name / missing image when meta available
    if title and (
        not product.name
        or product.name.startswith(("京东商品", "淘宝商品", "拼多多商品"))
    ):
        product.name = title
    if image_url and not product.image_url:
        product.image_url = image_url

    if not result.ok:
        product.needs_manual = True
        product.last_error = result.error
        await session.commit()
        return {
            "product_id": product.id,
            "ok": False,
            "needs_manual": True,
            "error": result.error,
        }

    product.needs_manual = False
    product.last_error = None
    if result.raw_note:
        product.note = result.raw_note

    return await apply_price_update(
        session,
        product,
        list_price=result.list_price,
        tax_amount=result.tax_amount,
        coupon_amount=result.coupon_amount,
        full_reduction=result.full_reduction,
        source="check",
        send_alert=True,
    )


async def check_product(session: AsyncSession, product: Product) -> dict:
    """Alias for check_watch (backward compatible)."""
    return await check_watch(session, product)


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
