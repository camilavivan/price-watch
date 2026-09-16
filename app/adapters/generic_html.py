"""Generic HTML price/title extractor (MarketEye-inspired heuristics, original code)."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin

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
        r"""["'](?:price|pPrice|actualPrice|salePrice|realPrice|currentPrice|oprice|"""
        r"""jdPrice|plusPrice|originPrice|op)["']"""
        r"""\s*[:=]\s*["']?([0-9]{1,7}(?:\.[0-9]{1,2})?)""",
        re.I,
    ),
    re.compile(r"\$\s*([0-9]{1,7}(?:\.[0-9]{1,2})?)"),
    re.compile(r"([0-9]{1,7}\.[0-9]{2})\s*元"),
]

# Tax amounts near Chinese markers or JSON keys (not used as main retail price)
_TAX_PATTERNS = [
    re.compile(
        r"(?:预估税费|进口税|跨境税|税费|税额)\s*[:：]?\s*[￥¥]?\s*"
        r"([0-9]{1,7}(?:\.[0-9]{1,2})?)",
        re.I,
    ),
    re.compile(
        r"""["'](?:taxFee|taxPrice|taxation|importTax|taxAmount|tax)["']"""
        r"""\s*[:=]\s*["']?([0-9]{1,7}(?:\.[0-9]{1,2})?)""",
        re.I,
    ),
]

_ALLIN_PATTERNS = [
    re.compile(
        r"(?:含税价|预估合计|预估到手|含税合计)\s*[:：]?\s*[￥¥]?\s*"
        r"([0-9]{1,7}(?:\.[0-9]{1,2})?)",
        re.I,
    ),
    re.compile(
        r"""["'](?:plusTaxPrice|totalPrice|taxInclusivePrice|includeTaxPrice)["']"""
        r"""\s*[:=]\s*["']?([0-9]{1,7}(?:\.[0-9]{1,2})?)""",
        re.I,
    ),
]

_TITLE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', re.I),
    re.compile(r'<meta[^>]+itemprop=["\']name["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+itemprop=["\']name["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']title["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r"<title[^>]*>([^<]+)</title>", re.I),
]

_IMAGE_PATTERNS = [
    re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I),
    re.compile(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']', re.I),
    re.compile(r'<meta[^>]+itemprop=["\']image["\'][^>]+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+itemprop=["\']image["\']', re.I),
    # JSON fields commonly used by CN e-commerce pages
    re.compile(
        r"""["'](?:imagePath|imageUrl|imgurl|mainImage|image_url|imgUrl)["']\s*:\s*["']([^"']+)["']""",
        re.I,
    ),
    # Common CDN image URLs embedded in HTML
    re.compile(
        r"""((?:https?:)?//(?:img\d*\.)?360buyimg\.com/[^\s"'<>\\]+)""",
        re.I,
    ),
    re.compile(
        r"""((?:https?:)?//(?:img\.)?alicdn\.com/[^\s"'<>\\]+)""",
        re.I,
    ),
    re.compile(
        r"""((?:https?:)?//(?:[^/\s"'<>]+\.)?(?:pddpic\.com|yangkeduo\.com)/[^\s"'<>\\]+)""",
        re.I,
    ),
]

_OOS_MARKERS = ("无货", "售罄", "缺货", "下架", "已下架", "补货中", "out of stock", "sold out")
_IN_STOCK_MARKERS = ("有货", "现货", "立即购买", "加入购物车", "in stock")


@dataclass
class HtmlExtractResult:
    ok: bool
    price: Optional[float] = None
    tax_amount: Optional[float] = None
    title: Optional[str] = None
    image_url: Optional[str] = None
    in_stock: Optional[bool] = None
    error: Optional[str] = None
    raw_note: Optional[str] = None


def _pick_price(candidates: list[float], *, exclude: Optional[set[float]] = None) -> Optional[float]:
    """Pick a plausible retail price (ignore tiny / huge outliers; skip known tax)."""
    exclude = exclude or set()
    plausible = [
        p for p in candidates
        if 0.5 <= p <= 999999 and round(p, 2) not in exclude
    ]
    if not plausible:
        return None
    # Prefer mid-range among duplicates: most frequent, else median of sorted.
    from collections import Counter

    counts = Counter(round(p, 2) for p in plausible)
    best, freq = counts.most_common(1)[0]
    if freq == 1 and len(plausible) >= 3:
        ordered = sorted(set(round(p, 2) for p in plausible))
        best = ordered[len(ordered) // 2]
    return float(best)


def _pick_tax(candidates: list[float]) -> Optional[float]:
    """Pick a tax amount; typically smaller than retail but can be large for luxury."""
    plausible = [p for p in candidates if 0 < p <= 999999]
    if not plausible:
        return None
    from collections import Counter

    counts = Counter(round(p, 2) for p in plausible)
    best, _ = counts.most_common(1)[0]
    return float(best)


def normalize_image_url(url: Optional[str], *, base_url: Optional[str] = None) -> Optional[str]:
    """Normalize protocol-relative / absolute image URLs; skip bare relatives without base."""
    if not url:
        return None
    u = url.strip().strip("\\\"'")
    if not u:
        return None
    if u.startswith("//"):
        return "https:" + u
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("/") and base_url:
        try:
            return urljoin(base_url, u)
        except Exception:
            return None
    # Relative without base — leave as-is only if it looks like a path we cannot use
    if u.startswith("/"):
        return None
    # Some JD imagePath values are like "jfs/t1/..." without host
    if u.startswith("jfs/") or u.startswith("/jfs/"):
        path = u.lstrip("/")
        return f"https://img14.360buyimg.com/n1/{path}"
    return None


def extract_meta_only(html: str, *, base_url: Optional[str] = None) -> tuple[Optional[str], Optional[str]]:
    """Extract title + image_url only (price ignored)."""
    if not html or len(html) < 40:
        return None, None

    title: Optional[str] = None
    for pat in _TITLE_PATTERNS:
        m = pat.search(html)
        if m:
            title = normalize_zh_text(m.group(1))
            title = re.split(r"\s*[-_|｜]\s*", title)[0].strip()
            if title:
                break

    image_url: Optional[str] = None
    for pat in _IMAGE_PATTERNS:
        m = pat.search(html)
        if m:
            image_url = normalize_image_url(m.group(1), base_url=base_url)
            if image_url:
                break

    return title, image_url


def extract_from_html(html: str, *, base_url: Optional[str] = None) -> HtmlExtractResult:
    if not html or len(html) < 40:
        return HtmlExtractResult(ok=False, error="页面内容过短，无法解析")

    text = normalize_zh_text(html)
    lower = text.lower()

    taxes: list[float] = []
    for pat in _TAX_PATTERNS:
        for m in pat.finditer(html):
            try:
                taxes.append(float(m.group(1)))
            except ValueError:
                continue
    tax_amount = _pick_tax(taxes)
    tax_exclude: set[float] = set()
    if tax_amount is not None:
        tax_exclude.add(round(tax_amount, 2))

    prices: list[float] = []
    for pat in _PRICE_PATTERNS:
        for m in pat.finditer(html):
            try:
                prices.append(float(m.group(1)))
            except ValueError:
                continue

    allins: list[float] = []
    for pat in _ALLIN_PATTERNS:
        for m in pat.finditer(html):
            try:
                allins.append(float(m.group(1)))
            except ValueError:
                continue

    # Prefer mid-range retail; never pick tiny tax as the main price
    price = _pick_price(prices, exclude=tax_exclude)
    if price is None and allins:
        # All-in tax-inclusive total as list price; tax left 0 to avoid double count
        price = _pick_price(allins)
        if price is not None:
            tax_amount = 0.0

    # If price looks like a tax (very small vs other candidates), try again without it
    if price is not None and tax_amount is not None and price <= tax_amount and price < 50:
        price = _pick_price(
            [p for p in prices if round(p, 2) != round(price, 2)],
            exclude=tax_exclude,
        ) or _pick_price(allins)

    title, image_url = extract_meta_only(html, base_url=base_url)

    in_stock: Optional[bool] = None
    if any(m in text for m in _OOS_MARKERS) or any(m in lower for m in ("out of stock", "sold out")):
        in_stock = False
    elif any(m in text for m in _IN_STOCK_MARKERS) or "in stock" in lower:
        in_stock = True

    if price is None:
        return HtmlExtractResult(
            ok=False,
            tax_amount=tax_amount,
            title=title,
            image_url=image_url,
            in_stock=in_stock,
            error="未能从 HTML 中解析到价格",
        )

    note = "generic HTML extract"
    if tax_amount:
        note += f" (税费 ¥{tax_amount:.2f})"

    return HtmlExtractResult(
        ok=True,
        price=price,
        tax_amount=tax_amount,
        title=title,
        image_url=image_url,
        in_stock=in_stock,
        raw_note=note,
    )


async def fetch_html(url: str, *, timeout: float = 15.0, headers: Optional[dict] = None) -> str:
    from app.adapters.http_util import make_async_client

    hdrs = {**DEFAULT_HEADERS, **(headers or {})}
    async with make_async_client(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(url, headers=hdrs)
        resp.raise_for_status()
        return resp.text


async def fetch_and_extract(url: str, *, timeout: float = 15.0) -> HtmlExtractResult:
    try:
        html = await fetch_html(url, timeout=timeout)
    except Exception as e:
        logger.warning("HTML fetch failed url=%s: %s", url[:80], e)
        return HtmlExtractResult(ok=False, error=f"页面请求失败（{e}）")
    return extract_from_html(html, base_url=url)


async def fetch_meta(url: str, *, timeout: float = 12.0) -> HtmlExtractResult:
    """Fetch page focusing on title+image (price optional). Tolerant of anti-bot pages."""
    try:
        html = await fetch_html(url, timeout=timeout)
    except Exception as e:
        logger.warning("meta fetch failed url=%s: %s", url[:80], e)
        return HtmlExtractResult(ok=False, error=f"页面请求失败（{e}）")

    title, image_url = extract_meta_only(html, base_url=url)
    # Still try price if present, but ok=True when we got title or image
    full = extract_from_html(html, base_url=url)
    if title or image_url:
        return HtmlExtractResult(
            ok=True,
            price=full.price,
            tax_amount=full.tax_amount,
            title=title or full.title,
            image_url=image_url or full.image_url,
            in_stock=full.in_stock,
            raw_note="meta-only extract",
        )
    return HtmlExtractResult(
        ok=False,
        price=full.price,
        tax_amount=full.tax_amount,
        title=full.title,
        image_url=full.image_url,
        in_stock=full.in_stock,
        error=full.error or "未能解析标题/主图",
    )
