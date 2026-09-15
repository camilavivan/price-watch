"""Simple async rate limiter between outbound price fetches."""

from __future__ import annotations

import asyncio
import time
from typing import Optional

_lock = asyncio.Lock()
_last_fetch_at: float = 0.0


async def wait_rate_limit(seconds: Optional[float] = None) -> None:
    """Sleep so consecutive fetches are at least `seconds` apart."""
    global _last_fetch_at
    from app.config import get_config

    gap = seconds
    if gap is None:
        gap = float(getattr(get_config().fetch, "rateLimitSeconds", 1.5) or 0)
    if gap <= 0:
        async with _lock:
            _last_fetch_at = time.monotonic()
        return

    async with _lock:
        now = time.monotonic()
        wait = _last_fetch_at + gap - now
        if wait > 0:
            await asyncio.sleep(wait)
        _last_fetch_at = time.monotonic()
