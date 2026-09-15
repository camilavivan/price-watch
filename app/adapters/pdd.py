"""Pinduoduo — try generic HTML extract; else needs_manual."""

from __future__ import annotations

import logging
from typing import Optional

from app.adapters.base import FetchResult
from app.adapters.generic_html import fetch_and_extract
from app.adapters.rate_limit import wait_rate_limit
from app.url_normalize import extract_pdd_goods_id, normalize_url

logger = logging.getLogger(__name__)


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
        result = await fetch_and_extract(page)
        if result.ok and result.price:
            return FetchResult(
                ok=True,
                list_price=result.price,
                title=result.title,
                image_url=result.image_url,
                needs_manual=False,
                raw_note="拼多多 HTML 尽力解析（不稳定，失败请手动）",
            )

        return FetchResult(
            ok=False,
            needs_manual=True,
            error=result.error or "拼多多需手动更新价格（无稳定公开接口）",
            title=result.title,
            image_url=result.image_url,
            raw_note="需手动更新",
        )
