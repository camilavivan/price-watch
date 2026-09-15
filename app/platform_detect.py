"""Guess ecommerce platform from product URL (thin wrapper over url_normalize)."""

from __future__ import annotations

from app.url_normalize import detect_platform, guess_name_from_url, normalize_url

__all__ = ["detect_platform", "guess_name_from_url", "normalize_url"]
