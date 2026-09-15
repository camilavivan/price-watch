"""Pinduoduo stub — requires manual price update."""

from __future__ import annotations

from typing import Optional

from app.adapters.base import FetchResult


class PDDAdapter:
    platform = "pdd"
    display_name = "拼多多"
    supports_auto = False

    async def fetch(self, url: str, sku_id: Optional[str] = None) -> FetchResult:
        return FetchResult(
            ok=False,
            needs_manual=True,
            error="拼多多需手动更新价格（无稳定公开接口）",
            raw_note="需手动更新",
        )
