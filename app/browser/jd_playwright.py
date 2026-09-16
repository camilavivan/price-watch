"""Playwright fetch for JD when HTTP scrape hits risk/SPA.

Soft-fail: missing playwright / no cookies / still blocked → None.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from urllib.parse import urlparse

from app.browser.extract import (
    extract_price_from_dom_text,
    extract_price_from_ware_json,
    merge_playwright_hits,
)
from app.browser.jd_session import (
    ensure_browser_dirs,
    jd_logged_in_hint,
    load_storage_state,
    playwright_enabled,
    storage_state_path,
)
from app.config import get_config

logger = logging.getLogger(__name__)


def _timeout_ms() -> int:
    cfg = getattr(get_config().fetch, "playwright", None)
    return int(getattr(cfg, "timeoutMs", 45000) or 45000)


def _headless() -> bool:
    cfg = getattr(get_config().fetch, "playwright", None)
    return bool(getattr(cfg, "headless", True)) if cfg else True


def _prefer_mobile() -> bool:
    cfg = getattr(get_config().fetch, "playwright", None)
    return bool(getattr(cfg, "preferMobile", True)) if cfg else True


def candidate_urls(sku: str, preferred: str = "") -> list[str]:
    """Navigate order for haitao / jd.hk."""
    sku = str(sku).strip()
    urls: list[str] = []
    if preferred and preferred.startswith("http"):
        urls.append(preferred.strip())
    if _prefer_mobile():
        urls.extend(
            [
                f"https://mitem.jd.hk/product/{sku}.html",
                f"https://npcitem.jd.hk/{sku}.html",
                f"https://item.jd.hk/{sku}.html",
                f"https://item.m.jd.com/product/{sku}.html",
                f"https://item.jd.com/{sku}.html",
            ]
        )
    else:
        urls.extend(
            [
                f"https://item.jd.hk/{sku}.html",
                f"https://npcitem.jd.hk/{sku}.html",
                f"https://mitem.jd.hk/product/{sku}.html",
                f"https://item.jd.com/{sku}.html",
            ]
        )
    # dedupe preserve order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def _looks_risk(html: str) -> bool:
    if not html:
        return False
    markers = ("京东验证", "risk_handler", "privatedomain/risk", "bp_bizid")
    sample = html[:12000]
    return any(m in sample for m in markers)


async def fetch_jd_with_playwright(
    sku: str,
    preferred_url: str = "",
) -> Optional[dict[str, Any]]:
    """
    Return dict with list_price, tax_amount, title?, note? or None on soft-fail.
    """
    if not playwright_enabled():
        return None
    ensure_browser_dirs()
    state = load_storage_state()
    if not state or not jd_logged_in_hint(state):
        logger.info(
            "playwright skip sku=%s: no JD login storage_state (use browser_login)",
            sku,
        )
        return None

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("playwright package not installed")
        return None

    hits: list[dict] = []
    xhr_hits: list[dict] = []

    async def on_response(response):
        try:
            url = response.url or ""
            if not any(
                k in url
                for k in (
                    "wareBusiness",
                    "pc_itempage_wareBusiness",
                    "price",
                    "getPrice",
                    "color.jd",
                )
            ):
                return
            ctype = (response.headers or {}).get("content-type", "")
            if "json" not in ctype and "javascript" not in ctype and "text" not in ctype:
                # still try
                pass
            body = await response.text()
            if not body or len(body) < 10:
                return
            if body.strip() in ("402", "403"):
                logger.info("playwright XHR blocked body=%s url=%s", body.strip(), url[:80])
                return
            parsed = extract_price_from_ware_json(body)
            if parsed.get("list_price"):
                xhr_hits.append(parsed)
        except Exception as e:
            logger.debug("playwright on_response: %s", e)

    urls = candidate_urls(sku, preferred_url)
    timeout = _timeout_ms()

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=_headless())
            context = await browser.new_context(
                storage_state=str(storage_state_path()),
                user_agent=(
                    "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/122.0.0.0 Mobile Safari/537.36"
                    if _prefer_mobile()
                    else None
                ),
                locale="zh-CN",
            )
            page = await context.new_page()
            page.on("response", on_response)

            for url in urls[:4]:
                try:
                    logger.info("playwright goto %s", url[:100])
                    await page.goto(url, wait_until="domcontentloaded", timeout=timeout)
                    # Wait briefly for XHR / hydration
                    try:
                        await page.wait_for_timeout(2500)
                    except Exception:
                        pass
                    html = await page.content()
                    if _looks_risk(html):
                        logger.info("playwright still risk page at %s", urlparse(url).netloc)
                        continue
                    try:
                        from app.price_sanity import is_login_wall_text

                        if is_login_wall_text(html):
                            logger.info(
                                "playwright login wall at %s", urlparse(url).netloc
                            )
                            continue
                    except Exception:
                        pass
                    # Reuse JD HTML parser
                    try:
                        from app.adapters.jd import parse_jd_price_tax

                        parsed_html = parse_jd_price_tax(html)
                        if parsed_html.get("list_price"):
                            hits.append(
                                {
                                    "list_price": parsed_html["list_price"],
                                    "tax_amount": float(parsed_html.get("tax_amount") or 0),
                                    "note": f"playwright HTML ({urlparse(url).netloc})",
                                }
                            )
                    except Exception as e:
                        logger.debug("parse_jd_price_tax: %s", e)

                    try:
                        body_text = await page.inner_text("body")
                        dom = extract_price_from_dom_text(body_text or "")
                        if dom.get("list_price"):
                            hits.append({**dom, "note": "playwright DOM text"})
                    except Exception:
                        pass

                    merged = merge_playwright_hits(*(xhr_hits + hits))
                    if merged.get("list_price"):
                        title = None
                        try:
                            title = await page.title()
                        except Exception:
                            pass
                        if title and "验证" not in title:
                            merged.setdefault("title", title[:120])
                        merged["note"] = merged.get("note") or f"playwright ({urlparse(url).netloc})"
                        await context.close()
                        await browser.close()
                        return merged
                except Exception as e:
                    logger.warning("playwright navigate failed %s: %s", url[:60], e)
                    continue

            await context.close()
            await browser.close()
    except Exception as e:
        logger.warning("playwright fetch failed sku=%s: %s", sku, e)
        return None

    merged = merge_playwright_hits(*(xhr_hits + hits))
    if merged.get("list_price"):
        return merged
    logger.info("playwright no price for sku=%s after %d urls", sku, len(urls[:4]))
    return None
