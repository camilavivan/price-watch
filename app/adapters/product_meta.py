"""Best-effort product title + main image enrichment (platform-aware)."""

from __future__ import annotations

import logging
import re
from typing import Optional

from app.adapters.generic_html import (
    BROWSER_UA,
    DEFAULT_HEADERS,
    extract_meta_only,
    fetch_and_extract,
    fetch_meta,
    normalize_image_url,
)
from app.url_normalize import extract_jd_sku, normalize_url

logger = logging.getLogger(__name__)


async def _jd_from_html(sku: str, preferred_url: str = "") -> tuple[Optional[str], Optional[str]]:
    pages: list[str] = []
    pref = (preferred_url or "").strip()
    if pref.startswith("http"):
        pages.append(pref)
    pages.extend(
        [
            f"https://mitem.jd.hk/product/{sku}.html",
            f"https://npcitem.jd.hk/{sku}.html",
            f"https://item.jd.hk/{sku}.html",
            f"https://item.m.jd.com/product/{sku}.html",
            f"https://item.jd.com/{sku}.html",
        ]
    )
    seen: set[str] = set()
    ordered: list[str] = []
    for page in pages:
        key = page.split("?", 1)[0]
        if key in seen:
            continue
        seen.add(key)
        ordered.append(page)
    for page in ordered:
        try:
            result = await fetch_meta(page)
            if result.title or result.image_url:
                return result.title, result.image_url
        except Exception as e:
            logger.warning("JD HTML meta failed sku=%s url=%s: %s", sku, page[:60], e)
    return None, None


async def _jd_from_json_apis(sku: str) -> tuple[Optional[str], Optional[str]]:
    """Optional best-effort JSON APIs when HTML is blocked."""
    title: Optional[str] = None
    image_url: Optional[str] = None
    headers = {
        **DEFAULT_HEADERS,
        "User-Agent": BROWSER_UA,
        "Referer": f"https://item.jd.com/{sku}.html",
    }
    try:
        from app.adapters.http_util import make_async_client

        async with make_async_client(timeout=10.0, follow_redirects=True) as client:
            # Name from yx.3.cn
            info_url = f"https://yx.3.cn/service/info.action?ids={sku}"
            try:
                resp = await client.get(info_url, headers=headers)
                if resp.status_code == 200:
                    text = resp.text.strip()
                    # Sometimes wrapped as JSONP or plain JSON object keyed by sku
                    m = re.search(
                        rf'["\']?{re.escape(sku)}["\']?\s*:\s*\{{[^}}]*?"name"\s*:\s*"([^"]+)"',
                        text,
                    )
                    if m:
                        title = m.group(1)
                    else:
                        m2 = re.search(r'"name"\s*:\s*"([^"]+)"', text)
                        if m2:
                            title = m2.group(1)
                    # image path in same payload
                    im = re.search(r'"(?:imagePath|imageurl|imgurl)"\s*:\s*"([^"]+)"', text, re.I)
                    if im:
                        image_url = normalize_image_url(im.group(1))
            except Exception as e:
                logger.warning("JD yx.3.cn info failed sku=%s: %s", sku, e)

            # If still no image, try fetching item HTML just for imagePath
            if not image_url:
                try:
                    page = f"https://item.jd.com/{sku}.html"
                    resp = await client.get(page, headers=headers)
                    if resp.status_code == 200:
                        html = resp.text
                        t2, i2 = extract_meta_only(html, base_url=page)
                        title = title or t2
                        image_url = image_url or i2
                        if not image_url:
                            m = re.search(
                                r"""["']imagePath["']\s*:\s*["']([^"']+)["']""",
                                html,
                                re.I,
                            )
                            if m:
                                image_url = normalize_image_url(m.group(1))
                except Exception as e:
                    logger.warning("JD imagePath scrape failed sku=%s: %s", sku, e)
    except Exception as e:
        logger.warning("JD JSON meta client failed sku=%s: %s", sku, e)

    return title, image_url


async def _enrich_jd(url: str, sku_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    sku = (sku_id or extract_jd_sku(url) or "").strip() or None
    if not sku:
        # Try meta on whatever URL we have
        if url and url.startswith("http"):
            try:
                r = await fetch_meta(url)
                return r.title, r.image_url
            except Exception as e:
                logger.warning("JD enrich without sku failed: %s", e)
        return None, None

    title, image = await _jd_from_html(sku, url or "")
    if title and image:
        return title, image

    t2, i2 = await _jd_from_json_apis(sku)
    return title or t2, image or i2


async def _enrich_generic(url: str) -> tuple[Optional[str], Optional[str]]:
    if not url or not url.startswith("http"):
        return None, None
    try:
        result = await fetch_meta(url)
        if result.title or result.image_url:
            return result.title, result.image_url
        # Fall back to full extract (may still have meta when price fails)
        full = await fetch_and_extract(url)
        return full.title, full.image_url
    except Exception as e:
        logger.warning("generic enrich failed url=%s: %s", url[:80], e)
        return None, None


async def enrich_title_image(
    *,
    platform: str,
    url: str,
    sku_id: Optional[str] = None,
) -> tuple[Optional[str], Optional[str]]:
    """
    Best-effort title + main image for a product.
    Never raises; returns (None, None) on errors.
    """
    plat = (platform or "").lower().strip()
    try:
        if plat == "jd":
            return await _enrich_jd(url or "", sku_id)
        if plat in ("taobao", "tmall", "pdd"):
            page = url or ""
            try:
                info = normalize_url(page)
                page = info.canonical_url or page
            except Exception:
                pass
            return await _enrich_generic(page)
        return await _enrich_generic(url or "")
    except Exception as e:
        logger.warning(
            "enrich_title_image failed platform=%s url=%s: %s",
            plat,
            (url or "")[:80],
            e,
        )
        return None, None
