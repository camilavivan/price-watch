"""Pinduoduo — Cookie HTTP when useful; else HTML best-effort → 填价."""

from __future__ import annotations

import logging
from typing import Optional

from app.adapters.base import FetchResult
from app.adapters.generic_html import DEFAULT_HEADERS, extract_from_html, fetch_html, fetch_and_extract
from app.adapters.rate_limit import wait_rate_limit
from app.url_normalize import extract_pdd_goods_id, normalize_url

logger = logging.getLogger(__name__)

PDD_BLOCKED = (
    "拼多多自动取价失败（风控较严）；请 Web 粘贴 Cookie 后重试，或用 QQ「填价」"
)


def _cookie_header() -> Optional[str]:
    try:
        from app.browser.cookies_common import load_cookie_header

        return load_cookie_header("pdd")
    except Exception as e:
        logger.debug("pdd cookie load: %s", e)
        return None


def _min_plausible() -> float:
    try:
        from app.config import get_config
        from app.price_sanity import min_plausible_price

        return min_plausible_price(getattr(get_config().fetch, "minPlausiblePrice", 10))
    except Exception:
        return 10.0


def _ok_price(price: Optional[float]) -> Optional[float]:
    if price is None:
        return None
    try:
        from app.price_sanity import is_plausible_retail

        if not is_plausible_retail(price, min_plausible=_min_plausible()):
            return None
    except Exception:
        if float(price) < 10:
            return None
    return float(price)


def _candidate_urls(page: str, sku: Optional[str]) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str) -> None:
        u = (u or "").strip()
        if not u.startswith("http"):
            return
        key = u.split("#", 1)[0]
        if key in seen:
            return
        seen.add(key)
        urls.append(key)

    add(page)
    if sku:
        add(f"https://mobile.yangkeduo.com/goods.html?goods_id={sku}")
        add(f"https://www.yangkeduo.com/goods.html?goods_id={sku}")
    return urls


class PDDAdapter:
    platform = "pdd"
    display_name = "拼多多"
    supports_auto = True

    async def fetch(self, url: str, sku_id: Optional[str] = None) -> FetchResult:
        info = normalize_url(url or "")
        sku = (sku_id or info.sku_id or extract_pdd_goods_id(url or "") or "").strip() or None
        page = info.canonical_url or (url or "").strip()
        if not page:
            return FetchResult(
                ok=False,
                needs_manual=True,
                error="缺少拼多多商品链接",
            )

        await wait_rate_limit()
        cookie = _cookie_header()
        title = None
        image_url = None
        tried: list[str] = []

        if cookie:
            headers = {
                **DEFAULT_HEADERS,
                "Cookie": cookie,
                "Referer": "https://mobile.yangkeduo.com/",
            }
            for u in _candidate_urls(page, sku):
                host = u.split("/")[2] if "://" in u else u[:40]
                tried.append(f"cookie:{host}")
                try:
                    html = await fetch_html(u, timeout=15.0, headers=headers)
                except Exception as e:
                    logger.info("pdd cookie fetch fail %s: %s", host, e)
                    continue
                try:
                    from app.price_sanity import is_login_wall_text

                    if is_login_wall_text(html):
                        continue
                except Exception:
                    pass
                result = extract_from_html(html, base_url=u)
                title = title or result.title
                image_url = image_url or result.image_url
                price = _ok_price(result.price if result.ok else None)
                if price is not None:
                    return FetchResult(
                        ok=True,
                        list_price=price,
                        tax_amount=result.tax_amount or 0.0,
                        title=title,
                        image_url=image_url,
                        needs_manual=False,
                        raw_note=f"拼多多 Cookie+HTML ({host})",
                    )

        tried.append("html")
        result = await fetch_and_extract(page)
        title = title or result.title
        image_url = image_url or result.image_url
        price = _ok_price(result.price if result.ok else None)
        if price is not None:
            return FetchResult(
                ok=True,
                list_price=price,
                tax_amount=result.tax_amount or 0.0,
                title=title,
                image_url=image_url,
                needs_manual=False,
                raw_note="拼多多 HTML 尽力解析（不稳定，失败请手动）",
            )

        tip = PDD_BLOCKED
        if not cookie:
            tip += "；尚未粘贴拼多多 Cookie（打开 /cookies）"
        return FetchResult(
            ok=False,
            needs_manual=True,
            error=tip + f"（已试：{'、'.join(tried)}）",
            title=title,
            image_url=image_url,
            raw_note="需手动更新",
        )
