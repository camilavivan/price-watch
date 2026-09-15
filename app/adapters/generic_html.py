"""Generic HTML price/title extractor (MarketEye-inspired heuristics, original code)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

import httpx

from app.url_normalize import normalize_zh_text

logger = logging.getLogger(__name__)

BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

DEFAULT_HEADERS = {
    "User-Agent": BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}

# CNY / USD style prices appearing in HTML or JSON blobs
_PRICE_PATTERNS = [
    re.compile(r"[￥¥]\s*([0-9]{1,7}(?:\.[0-9]{1,2})?)", re.I),
    re.compile(
        r"(?:price|pPrice|actualPrice|salePrice|realPrice|currentPrice|oprice)"
        r"""["']?\s*[:=]\s*["']?([0-9]{1,7}(?:\.[0-9]{1,2})?)""",
        re.I,
    ),
    re.compile(r"\$\s*([0-9]{1,7}(?:\.[0-9]{1,2})?)"),
    re.compile(r"([0-9]{1,7}\.[0-9]{2})\s*元"),
]

_TITLE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', re.I),
    re.compile(r"<title[^>]*>([^<]+)</title>", re.I),
]

_IMAGE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
]

_OOS_MARKERS = ("无货", "售罄", "缺货", "下架", "已下架", "补货中", "out of stock", "sold out")
_IN_STOCK_MARKERS = ("有货", "现货", "立即购买", "加入购物车", "in stock")


@dataclass
class HtmlExtractResult:
    ok: bool
    price: Optional[float] = None
    title: Optional[str] = None
    image_url: Optional[str] = None
    in_stock: Optional[bool] = None
    error: Optional[str] = None
    raw_note: Optional[str] = None


def _pick_price(candidates: list[float]) -> Optional[float]:
    """Pick a plausible retail price (ignore tiny / huge outliers)."""
    plausible = [p for p in candidates if 0.5 <= p <= 999999]
    if not plausible:
        return None
    # Prefer mid-low among common list/sale duplicates: take the most frequent,
    # else the median-ish lower half first value.
    from collections import Counter

    counts = Counter(round(p, 2) for p in plausible)
    best, _ = counts.most_common(1)[0]
    return float(best)


def extract_from_html(html: str) -> HtmlExtractResult:
    if not html or len(html) < 40:
        return HtmlExtractResult(ok=False, error="页面内容过短，无法解析")

    text = normalize_zh_text(html)
    lower = text.lower()

    prices: list[float] = []
    for pat in _PRICE_PATTERNS:
        for m in pat.finditer(html):
            try:
                prices.append(float(m.group(1)))
            except ValueError:
                continue

    price = _pick_price(prices)

    title: Optional[str] = None
    for pat in _TITLE_PATTERNS:
        m = pat.search(html)
        if m:
            title = normalize_zh_text(m.group(1))
            # Drop site suffix: "name - 京东" / "name_淘宝"
            title = re.split(r"\s*[-_|｜]\s*", title)[0].strip()
            if title:
                break

    image_url: Optional[str] = None
    for pat in _IMAGE_PATTERNS:
        m = pat.search(html)
        if m:
            image_url = m.group(1).strip()
            break

    in_stock: Optional[bool] = None
    if any(m in text for m in _OOS_MARKERS) or any(m in lower for m in ("out of stock", "sold out")):
        in_stock = False
    elif any(m in text for m in _IN_STOCK_MARKERS) or "in stock" in lower:
        in_stock = True

    if price is None:
        return HtmlExtractResult(
            ok=False,
            title=title,
            image_url=image_url,
            in_stock=in_stock,
            error="未能从 HTML 中解析到价格",
        )

    return HtmlExtractResult(
        ok=True,
        price=price,
        title=title,
        image_url=image_url,
        in_stock=in_stock,
        raw_note="generic HTML extract",
    )


async def fetch_html(url: str, *, timeout: float = 15.0, headers: Optional[dict] = None) -> str:
    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers=hdrs)
        resp.raise_for_status()
        return resp.text


async def fetch_and_extract(url: str, *, timeout: float = 15.0) -> HtmlExtractResult:
    try:
        html = await fetch_html(url, timeout=timeout)
    except Exception as e:
        logger.warning("HTML fetch failed url=%s: %s", url[:80], e)
        return HtmlExtractResult(ok=False, error=f"页面请求失败（{e}）")
    return extract_from_html(html)
