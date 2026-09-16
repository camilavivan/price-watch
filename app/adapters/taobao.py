"""Taobao / Tmall — Cookie HTTP fetch when pasted; else HTML best-effort → 填价."""

from __future__ import annotations

import logging
from typing import Optional

from app.adapters.base import FetchResult
from app.adapters.generic_html import DEFAULT_HEADERS, extract_from_html, fetch_html, fetch_and_extract
from app.adapters.rate_limit import wait_rate_limit
from app.url_normalize import extract_taobao_id, normalize_url

logger = logging.getLogger(__name__)

TB_BLOCKED = (
    "淘宝/天猫自动取价失败（需登录或风控）；请 Web 粘贴本机 Cookie，或用 QQ「填价」"
)


def _cookie_header() -> Optional[str]:
    try:
        from app.browser.cookies_common import load_cookie_header, looks_logged_in, load_cookie_dict

        header = load_cookie_header("taobao")
        if not header:
            return None
        # Prefer logged-in; still try if any cookies present
        _ = looks_logged_in(load_cookie_dict("taobao"), "taobao")
        return header
    except Exception as e:
        logger.debug("taobao cookie load: %s", e)
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
        add(f"https://item.taobao.com/item.htm?id={sku}")
        add(f"https://detail.tmall.com/item.htm?id={sku}")
        add(f"https://h5.m.taobao.com/awp/core/detail.htm?id={sku}")
        add(f"https://detail.m.tmall.com/item.htm?id={sku}")
    return urls


class TaobaoAdapter:
    platform = "taobao"
    display_name = "淘宝/天猫"
    supports_auto = True

    async def fetch(self, url: str, sku_id: Optional[str] = None) -> FetchResult:
        info = normalize_url(url or "")
        sku = (sku_id or info.sku_id or extract_taobao_id(url or "") or "").strip() or None
        page = info.canonical_url or (url or "").strip()
        if not page:
            return FetchResult(
                ok=False,
                needs_manual=True,
                error="缺少淘宝/天猫商品链接",
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
                "Referer": "https://www.taobao.com/",
            }
            for u in _candidate_urls(page, sku):
                host = u.split("/")[2] if "://" in u else u[:40]
                tried.append(f"cookie:{host}")
                try:
                    html = await fetch_html(u, timeout=15.0, headers=headers)
                except Exception as e:
                    logger.info("taobao cookie fetch fail %s: %s", host, e)
                    continue
                try:
                    from app.price_sanity import is_login_wall_text

                    if is_login_wall_text(html):
                        logger.info("taobao login wall at %s", host)
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
                        raw_note=f"淘宝/天猫 Cookie+HTML ({host})",
                    )

        # Anonymous best-effort (often login wall on cloud IP)
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
                raw_note="淘宝/天猫 HTML 尽力解析（不稳定，失败请手动）",
            )

        tip = TB_BLOCKED
        if not cookie:
            tip += "；尚未粘贴淘宝 Cookie（打开 /cookies）"
        return FetchResult(
            ok=False,
            needs_manual=True,
            error=tip + f"（已试：{'、'.join(tried)}）",
            title=title,
            image_url=image_url,
            raw_note="需手动更新",
        )
