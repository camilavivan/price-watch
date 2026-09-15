"""Adapter base types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol


@dataclass
class FetchResult:
    ok: bool
    list_price: Optional[float] = None
    coupon_amount: Optional[float] = None
    full_reduction: Optional[float] = None
    title: Optional[str] = None
    image_url: Optional[str] = None
    error: Optional[str] = None
    needs_manual: bool = False
    raw_note: Optional[str] = None


class PriceAdapter(Protocol):
    platform: str
    display_name: str
    supports_auto: bool

    async def fetch(self, url: str, sku_id: Optional[str] = None) -> FetchResult:
        ...
