"""Shared outbound HTTP helpers: proxy env + AsyncClient factory."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# Prefer JD-specific override, then standard proxy env names (upper then lower).
_PROXY_ENV_KEYS = (
    "JD_HTTP_PROXY",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "ALL_PROXY",
    "https_proxy",
    "http_proxy",
    "all_proxy",
)


def outbound_proxy() -> Optional[str]:
    """
    Resolve outbound HTTP(S) proxy for JD / page fetches.

    Order: JD_HTTP_PROXY → HTTPS_PROXY → HTTP_PROXY → ALL_PROXY
    (also checks lowercase variants).
    """
    for key in _PROXY_ENV_KEYS:
        val = (os.environ.get(key) or "").strip()
        if val:
            return val
    return None


def async_client_kwargs(**extra: Any) -> dict[str, Any]:
    """
    Kwargs for httpx.AsyncClient with optional proxy from env.

    Callers may still pass timeout / follow_redirects / headers etc.
    Explicit ``proxy=`` in extra wins over env.
    """
    kwargs: dict[str, Any] = dict(extra)
    if "proxy" not in kwargs:
        proxy = outbound_proxy()
        if proxy:
            kwargs["proxy"] = proxy
            logger.debug("httpx client using proxy=%s", proxy.split("@")[-1][:60])
    return kwargs


def make_async_client(**extra: Any) -> httpx.AsyncClient:
    """Create AsyncClient with proxy env wired when present."""
    return httpx.AsyncClient(**async_client_kwargs(**extra))
