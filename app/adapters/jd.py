"""JD (京东) best-effort public price fetch; falls back to manual."""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters.base import FetchResult

logger = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def extract_sku_id(url: str, sku_id: Optional[str] = None) -> Optional[str]:
    if sku_id:
        return sku_id.strip()
    # https://item.jd.com/100012043978.html
    m = re.search(r"item\.jd\.com/(\d+)\.html", url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)\.html", url)
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url).query)
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

        # Best-effort: public price API pattern (often blocked / rate-limited)
        api_url = f"https://p.3.cn/prices/mgets?skuIds=J_{sku}&type=1"
        headers = {
            "User-Agent": UA,
            "Referer": f"https://item.jd.com/{sku}.html",
            "Accept": "application/json, text/javascript, */*;q=0.01",
        }
        try:
            async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
                resp = await client.get(api_url, headers=headers)
                if resp.status_code != 200:
                    return FetchResult(
                        ok=False,
                        needs_manual=True,
                        error=f"京东价格接口 HTTP {resp.status_code}，请手动更新价格",
                    )
                text = resp.text.strip()
                # Response may be JSON array: [{"id":"J_xxx","p":"99.00",...}]
                data = resp.json()
                if isinstance(data, list) and data:
                    item = data[0]
                    price_str = item.get("p") or item.get("op") or item.get("m")
                    if price_str is not None:
                        price = float(str(price_str))
                        if price > 0:
                            return FetchResult(
                                ok=True,
                                list_price=price,
                                needs_manual=False,
                                raw_note="来自京东公开价格接口（到手价需自行填券/满减）",
                            )
                return FetchResult(
                    ok=False,
                    needs_manual=True,
                    error=f"京东价格接口返回无法解析: {text[:120]}",
                )
        except Exception as e:
            logger.warning("JD fetch failed sku=%s: %s", sku, e)
            return FetchResult(
                ok=False,
                needs_manual=True,
                error=f"京东抓取失败（{e}），请手动更新价格",
            )
