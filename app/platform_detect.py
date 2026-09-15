"""Guess ecommerce platform from product URL."""

from __future__ import annotations

import re
from urllib.parse import urlparse


def detect_platform(url: str) -> str | None:
    u = (url or "").strip().lower()
    if not u:
        return None
    host = urlparse(u if "://" in u else f"https://{u}").netloc
    if "jd.com" in host or "jd.hk" in host:
        return "jd"
    if "taobao.com" in host or "tmall.com" in host or "tmall.hk" in host:
        return "taobao"
    if "pinduoduo.com" in host or "yangkeduo.com" in host:
        return "pdd"
    return None


def guess_name_from_url(url: str, platform: str) -> str:
    plat_label = {"jd": "京东", "taobao": "淘宝", "pdd": "拼多多"}.get(platform, platform)
    # Try to keep a short readable stub
    m = re.search(r"/(\d+)\.html", url or "")
    if m:
        return f"{plat_label}商品 {m.group(1)}"
    return f"{plat_label}商品"
