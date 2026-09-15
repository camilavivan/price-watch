"""JD (京东) best-effort public price API + HTML fallback; else needs_manual."""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters.base import FetchResult
from app.adapters.generic_html import BROWSER_UA, fetch_and_extract
from app.adapters.product_meta import enrich_title_image
from app.adapters.rate_limit import wait_rate_limit
from app.url_normalize import extract_jd_sku

logger = logging.getLogger(__name__)


def extract_sku_id(url: str, sku_id: Optional[str] = None) -> Optional[str]:
    if sku_id:
        return str(sku_id).strip() or None
    found = extract_jd_sku(url)
    if found:
        return found
    # Legacy fallbacks
    m = re.search(r"item\.jd\.com/(\d+)\.html", url or "")
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)\.html", url or "")
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url or "").query)
    for key in ("sku", "skuId", "skuid"):
        if key in qs and qs[key]:
            return qs[key][0]
    return None


class JDAdapter:
    platform = "jd"
    display_name = "京东"
    supports_auto = True

    async def fetch(self, url: str, sku_id: Optional[str] = None) -> FetchResult:
        sku = extract_sku_id(url, sku_id)
        if not sku:
            return FetchResult(
                ok=False,
                needs_manual=True,
                error="无法解析京东 SKU，请填写 sku_id 或手动更新价格",
            )

        await wait_rate_limit()

        page_url = (
            url.strip()
            if (url or "").strip().startswith("http")
            else f"https://item.jd.com/{sku}.html"
        )

        # 1) Best-effort public price API (often blocked / rate-limited)
        api_url = f"https://p.3.cn/prices/mgets?skuIds=J_{sku}&type=1"
        headers = {
            "User-Agent": BROWSER_UA,
            "Referer": f"https://item.jd.com/{sku}.html",
            "Accept": "application/json, text/javascript, */*;q=0.01",
        }
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(api_url, headers=headers)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        item = data[0]
                        price_str = item.get("p") or item.get("op") or item.get("m")
                        if price_str is not None:
                            price = float(str(price_str))
                            if price > 0:
                                title, image_url = await enrich_title_image(
                                    platform="jd", url=page_url, sku_id=sku
                                )
                                return FetchResult(
                                    ok=True,
                                    list_price=price,
                                    title=title,
                                    image_url=image_url,
                                    needs_manual=False,
                                    raw_note="来自京东公开价格接口（到手价需自行填券/满减）",
                                )
        except Exception as e:
            logger.warning("JD API fetch failed sku=%s: %s", sku, e)

        # 2) Generic HTML fallback on item page
        html_result = await fetch_and_extract(page_url)
        if html_result.ok and html_result.price:
            title = html_result.title
            image_url = html_result.image_url
            if not title or not image_url:
                t2, i2 = await enrich_title_image(
                    platform="jd", url=page_url, sku_id=sku
                )
                title = title or t2
                image_url = image_url or i2
            return FetchResult(
                ok=True,
                list_price=html_result.price,
                title=title,
                image_url=image_url,
                needs_manual=False,
                raw_note="京东 HTML 兜底解析",
            )

        # Failure path: still attach title/image if found
        title = html_result.title
        image_url = html_result.image_url
        if not title or not image_url:
            t2, i2 = await enrich_title_image(platform="jd", url=page_url, sku_id=sku)
            title = title or t2
            image_url = image_url or i2

        err = html_result.error or "京东价格接口与页面解析均失败"
        return FetchResult(
            ok=False,
            needs_manual=True,
            error=f"{err}，请手动更新价格",
            title=title,
            image_url=image_url,
        )
