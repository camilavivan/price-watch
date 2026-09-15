"""Taobao / Tmall stub — requires manual price update."""

from __future__ import annotations

from typing import Optional

from app.adapters.base import FetchResult


class TaobaoAdapter:
    platform = "taobao"
    display_name = "淘宝/天猫"
    supports_auto = False

    async def fetch(self, url: str, sku_id: Optional[str] = None) -> FetchResult:
        return FetchResult(
            ok=False,
            needs_manual=True,
            error="淘宝/天猫需手动更新价格（无稳定公开接口，自动抓取易违反平台条款）",
            raw_note="需手动更新",
        )
