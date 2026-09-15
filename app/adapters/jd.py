"""JD (京东) best-effort public price API + HTML/JSON fallback; else needs_manual."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import httpx

from app.adapters.base import FetchResult
from app.adapters.generic_html import (
    BROWSER_UA,
    DEFAULT_HEADERS,
    extract_from_html,
    fetch_html,
)
from app.adapters.product_meta import enrich_title_image
from app.adapters.rate_limit import wait_rate_limit
from app.url_normalize import extract_jd_sku

logger = logging.getLogger(__name__)

# JSON / pageConfig style price keys (goods price, not tax)
_PRICE_KEYS = (
    "pPrice",
    "op",
    "price",
    "jdPrice",
    "realPrice",
    "plusPrice",
    "originPrice",
    "salePrice",
    "actualPrice",
    "p",
    "m",
)

# Explicit tax keys
_TAX_KEYS = (
    "taxFee",
    "tax",
    "taxPrice",
    "taxation",
    "importTax",
    "crossBorderTax",
    "taxAmount",
)

# All-in / tax-inclusive totals (prefer as landing when no split tax)
_ALLIN_KEYS = (
    "plusTaxPrice",
    "totalPrice",
    "taxInclusivePrice",
    "includeTaxPrice",
    "finalPrice",
)

_NUM_RE = re.compile(r"([0-9]{1,7}(?:\.[0-9]{1,2})?)")

# Require quoted JSON keys so "plusTaxPrice" does not match "taxPrice" / "price"
_PRICE_FIELD_RE = re.compile(
    r"""["'](?:"""
    + "|".join(_PRICE_KEYS)
    + r""")["']\s*[:=]\s*["']?([0-9]{1,7}(?:\.[0-9]{1,2})?)""",
    re.I,
)

_TAX_FIELD_RE = re.compile(
    r"""["'](?:"""
    + "|".join(_TAX_KEYS)
    + r""")["']\s*[:=]\s*["']?([0-9]{1,7}(?:\.[0-9]{1,2})?)""",
    re.I,
)

_ALLIN_FIELD_RE = re.compile(
    r"""["'](?:"""
    + "|".join(_ALLIN_KEYS)
    + r""")["']\s*[:=]\s*["']?([0-9]{1,7}(?:\.[0-9]{1,2})?)""",
    re.I,
)

# Text markers near amounts
_TAX_TEXT_RE = re.compile(
    r"(?:预估税费|进口税|跨境税|税费|税额)\s*[:：]?\s*[￥¥]?\s*"
    r"([0-9]{1,7}(?:\.[0-9]{1,2})?)",
    re.I,
)
_ALLIN_TEXT_RE = re.compile(
    r"(?:含税价|预估合计|预估到手|到手价|含税合计)\s*[:：]?\s*[￥¥]?\s*"
    r"([0-9]{1,7}(?:\.[0-9]{1,2})?)",
    re.I,
)
_GOODS_TEXT_RE = re.compile(
    r"(?:商品价|商品金额|完税价格|申报价)\s*[:：]?\s*[￥¥]?\s*"
    r"([0-9]{1,7}(?:\.[0-9]{1,2})?)",
    re.I,
)


def extract_sku_id(url: str, sku_id: Optional[str] = None) -> Optional[str]:
    if sku_id:
        return str(sku_id).strip() or None
    found = extract_jd_sku(url)
    if found:
        return found
    # Legacy fallbacks
    m = re.search(r"item\.jd\.com/(\d+)\.html", url or "")
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)\.html", url or "")
    if m:
        return m.group(1)
    qs = parse_qs(urlparse(url or "").query)
    for key in ("sku", "skuId", "skuid"):
        if key in qs and qs[key]:
            return qs[key][0]
    return None


def _to_price(val: Any) -> Optional[float]:
    if val is None:
        return None
    if isinstance(val, (int, float)):
        f = float(val)
        return f if f > 0 else None
    s = str(val).strip().replace(",", "")
    if not s or s in ("-1", "暂无报价", "null", "None"):
        return None
    m = _NUM_RE.search(s)
    if not m:
        return None
    try:
        f = float(m.group(1))
    except ValueError:
        return None
    return f if f > 0 else None


def _walk_collect(obj: Any, out: dict[str, list[float]], depth: int = 0) -> None:
    """Recursively collect known price/tax keys from nested dict/list JSON."""
    if depth > 8:
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k)
            kl = key.lower()
            if key in _TAX_KEYS or kl in {x.lower() for x in _TAX_KEYS}:
                p = _to_price(v)
                if p is not None:
                    out.setdefault("tax", []).append(p)
            elif key in _ALLIN_KEYS or kl in {x.lower() for x in _ALLIN_KEYS}:
                p = _to_price(v)
                if p is not None:
                    out.setdefault("allin", []).append(p)
            elif key in _PRICE_KEYS or kl in {x.lower() for x in _PRICE_KEYS}:
                p = _to_price(v)
                if p is not None and p >= 0.5:
                    out.setdefault("price", []).append(p)
            else:
                _walk_collect(v, out, depth + 1)
    elif isinstance(obj, list):
        for item in obj[:80]:
            _walk_collect(item, out, depth + 1)


def _extract_json_blobs(html: str) -> list[Any]:
    """Best-effort pull of pageConfig / ware / price JSON objects from script tags."""
    blobs: list[Any] = []
    patterns = [
        re.compile(
            r"(?:pageConfig|wareInfo|itemInfo|priceInfo|skuJson|product)\s*=\s*(\{.+?\})\s*;",
            re.S | re.I,
        ),
        re.compile(r"window\.(?:pageConfig|ware)\s*=\s*(\{.+?\})\s*;", re.S | re.I),
    ]
    for pat in patterns:
        for m in pat.finditer(html):
            raw = m.group(1)
            # Truncate huge blobs for safety
            if len(raw) > 500_000:
                continue
            try:
                blobs.append(json.loads(raw))
            except Exception:
                # Try fixing trailing commas lightly — skip on failure
                continue
    # Also try raw regex-friendly substrings that look like JSON with pPrice
    for m in re.finditer(r"\{[^{}]{0,2000}?(?:pPrice|taxFee|jdPrice)[^{}]{0,2000}?\}", html):
        try:
            blobs.append(json.loads(m.group(0)))
        except Exception:
            continue
    return blobs


def parse_jd_price_tax(html: str) -> dict[str, Optional[float]]:
    """
    Parse JD item HTML/JSON for list price + tax.

    Preference:
      1) Explicit 商品价 + 税费 split when both present
      2) JSON price keys + tax keys
      3) All-in 「含税价 / 预估合计 / plusTaxPrice」 as list_price with tax=0
         (documented: already tax-inclusive → avoid double-counting)
    """
    collected: dict[str, list[float]] = {}
    for blob in _extract_json_blobs(html):
        _walk_collect(blob, collected)

    for m in _PRICE_FIELD_RE.finditer(html):
        p = _to_price(m.group(1))
        if p is not None and p >= 0.5:
            collected.setdefault("price", []).append(p)
    for m in _TAX_FIELD_RE.finditer(html):
        p = _to_price(m.group(1))
        if p is not None:
            collected.setdefault("tax", []).append(p)
    for m in _ALLIN_FIELD_RE.finditer(html):
        p = _to_price(m.group(1))
        if p is not None and p >= 0.5:
            collected.setdefault("allin", []).append(p)

    for m in _GOODS_TEXT_RE.finditer(html):
        p = _to_price(m.group(1))
        if p is not None and p >= 0.5:
            collected.setdefault("goods_text", []).append(p)
    for m in _TAX_TEXT_RE.finditer(html):
        p = _to_price(m.group(1))
        if p is not None:
            collected.setdefault("tax", []).append(p)
    for m in _ALLIN_TEXT_RE.finditer(html):
        p = _to_price(m.group(1))
        if p is not None and p >= 0.5:
            collected.setdefault("allin", []).append(p)

    def _pick(vals: list[float] | None) -> Optional[float]:
        if not vals:
            return None
        from collections import Counter

        plausible = [v for v in vals if 0 < v <= 999999]
        if not plausible:
            return None
        best, _ = Counter(round(v, 2) for v in plausible).most_common(1)[0]
        return float(best)

    price = _pick(collected.get("goods_text")) or _pick(collected.get("price"))
    tax = _pick(collected.get("tax"))
    allin = _pick(collected.get("allin"))

    # Tiny values that look like tax should not become list_price
    if price is not None and tax is not None and price < tax and price < 20:
        # likely swapped / mis-picked; prefer all-in
        price = None

    note: Optional[str] = None
    if price is not None and tax is not None:
        note = "JD HTML/JSON：商品价+税费分拆"
        return {"list_price": price, "tax_amount": tax, "note": note}
    if price is not None:
        note = "JD HTML/JSON：商品价" + ("（另有税费）" if tax else "")
        return {"list_price": price, "tax_amount": tax or 0.0, "note": note}
    if allin is not None:
        # All-in already includes tax → store as list_price, tax=0 to avoid double count
        note = "JD HTML/JSON：含税价/预估合计作标价（tax=0，已含税）"
        return {"list_price": allin, "tax_amount": 0.0, "note": note}
    if tax is not None:
        # Tax alone is useless without goods price
        return {"list_price": None, "tax_amount": tax, "note": "仅解析到税费"}
    return {"list_price": None, "tax_amount": None, "note": None}


async def _try_p3cn(sku: str) -> Optional[float]:
    api_url = f"https://p.3.cn/prices/mgets?skuIds=J_{sku}&type=1"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": f"https://item.jd.com/{sku}.html",
        "Accept": "application/json, text/javascript, */*;q=0.01",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                return None
            data = resp.json()
            if not (isinstance(data, list) and data):
                return None
            item = data[0]
            for key in ("p", "op", "m"):
                price = _to_price(item.get(key))
                if price is not None:
                    return price
    except Exception as e:
        logger.warning("JD p.3.cn failed sku=%s: %s", sku, e)
    return None


async def _try_secondary_endpoints(sku: str) -> Optional[dict[str, Optional[float]]]:
    """Best-effort public ware-style endpoints; fail soft."""
    urls = [
        f"https://item-soa.jd.com/getWareBusiness?skuId={sku}",
        f"https://api.m.jd.com/api?functionId=pc_itempage_wareBusiness&appid=item-v3"
        f"&body=%7B%22skuId%22%3A%22{sku}%22%7D",
    ]
    headers = {
        **DEFAULT_HEADERS,
        "Referer": f"https://item.jd.com/{sku}.html",
        "Accept": "application/json, text/plain, */*",
    }
    for url in urls:
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    continue
                text = resp.text
                if not text or len(text) < 20:
                    continue
                try:
                    data = resp.json()
                except Exception:
                    data = None
                collected: dict[str, list[float]] = {}
                if data is not None:
                    _walk_collect(data, collected)
                # Also regex on raw body
                parsed = parse_jd_price_tax(text)
                price = None
                tax = None
                if collected.get("price"):
                    from collections import Counter

                    price = float(
                        Counter(round(v, 2) for v in collected["price"]).most_common(1)[0][0]
                    )
                if collected.get("tax"):
                    from collections import Counter

                    tax = float(
                        Counter(round(v, 2) for v in collected["tax"]).most_common(1)[0][0]
                    )
                if parsed.get("list_price") and not price:
                    price = parsed["list_price"]
                if parsed.get("tax_amount") and tax is None:
                    tax = parsed["tax_amount"]
                if price:
                    return {
                        "list_price": price,
                        "tax_amount": tax or 0.0,
                        "note": "JD 公开 ware/business 接口",
                    }
        except Exception as e:
            logger.debug("JD secondary endpoint soft-fail %s: %s", url[:60], e)
    return None


async def _fetch_page_parse(url: str) -> tuple[dict[str, Optional[float]], Optional[str], Optional[str]]:
    """Fetch HTML and parse JD-specific + generic fallback. Returns (parsed, title, image)."""
    try:
        html = await fetch_html(url, timeout=15.0)
    except Exception as e:
        logger.warning("JD page fetch failed url=%s: %s", url[:80], e)
        return {"list_price": None, "tax_amount": None, "note": None}, None, None

    parsed = parse_jd_price_tax(html)
    generic = extract_from_html(html, base_url=url)
    title = generic.title
    image_url = generic.image_url

    if parsed.get("list_price") is None and generic.ok and generic.price:
        # Don't treat a tiny tax-like amount as main price when tax markers exist
        tax = parsed.get("tax_amount") or generic.tax_amount
        if tax and generic.price <= tax and generic.price < 50:
            pass
        else:
            parsed = {
                "list_price": generic.price,
                "tax_amount": tax or 0.0,
                "note": "京东 HTML 通用解析",
            }
    elif parsed.get("list_price") is not None and parsed.get("tax_amount") in (None, 0):
        if generic.tax_amount:
            parsed["tax_amount"] = generic.tax_amount
            if parsed.get("note"):
                parsed["note"] = str(parsed["note"]) + " + generic 税费"

    return parsed, title, image_url


class JDAdapter:
    platform = "jd"
    display_name = "京东"
    supports_auto = True

    async def fetch(self, url: str, sku_id: Optional[str] = None) -> FetchResult:
        sku = extract_sku_id(url, sku_id)
        if not sku:
            return FetchResult(
                ok=False,
                needs_manual=True,
                error="无法解析京东 SKU，请填写 sku_id 或手动更新价格",
            )

        await wait_rate_limit()

        page_url = (
            url.strip()
            if (url or "").strip().startswith("http")
            else f"https://item.jd.com/{sku}.html"
        )

        list_price: Optional[float] = None
        tax_amount: Optional[float] = None
        note: Optional[str] = None
        title: Optional[str] = None
        image_url: Optional[str] = None

        # 1) p.3.cn — ignore invalid (≤0, -1, missing)
        api_price = await _try_p3cn(sku)
        if api_price is not None:
            list_price = api_price
            note = "来自京东公开价格接口（到手价需自行填券/满减；海淘税费另计）"

        # 2) Desktop / given page HTML+JSON
        parsed, t1, i1 = await _fetch_page_parse(page_url)
        title = title or t1
        image_url = image_url or i1
        if parsed.get("tax_amount"):
            tax_amount = float(parsed["tax_amount"] or 0)
        if list_price is None and parsed.get("list_price"):
            list_price = float(parsed["list_price"])
            note = parsed.get("note") or "JD HTML/JSON"
        elif list_price is not None and tax_amount:
            # Keep API price, attach tax from HTML
            note = (note or "p.3.cn") + " + HTML 税费"

        # 3) Mobile / HK pages when still no price
        if list_price is None:
            alt_urls = [
                f"https://item.m.jd.com/product/{sku}.html",
                f"https://npcitem.jd.hk/{sku}.html",
                f"https://item.jd.hk/{sku}.html",
            ]
            for alt in alt_urls:
                parsed2, t2, i2 = await _fetch_page_parse(alt)
                title = title or t2
                image_url = image_url or i2
                if parsed2.get("tax_amount") and not tax_amount:
                    tax_amount = float(parsed2["tax_amount"] or 0)
                if parsed2.get("list_price"):
                    list_price = float(parsed2["list_price"])
                    tax_amount = (
                        float(parsed2["tax_amount"] or 0)
                        if parsed2.get("tax_amount") is not None
                        else (tax_amount or 0.0)
                    )
                    note = (parsed2.get("note") or "JD 移动/海淘页") + f" ({alt.split('/')[2]})"
                    break

        # 4) Optional secondary public endpoints
        if list_price is None:
            sec = await _try_secondary_endpoints(sku)
            if sec and sec.get("list_price"):
                list_price = float(sec["list_price"])
                tax_amount = float(sec.get("tax_amount") or 0)
                note = sec.get("note")

        # Always enrich title/image
        if not title or not image_url:
            t2, i2 = await enrich_title_image(platform="jd", url=page_url, sku_id=sku)
            title = title or t2
            image_url = image_url or i2

        if list_price is not None and list_price > 0:
            return FetchResult(
                ok=True,
                list_price=list_price,
                tax_amount=float(tax_amount or 0),
                title=title,
                image_url=image_url,
                needs_manual=False,
                raw_note=note or "京东价格解析",
            )

        return FetchResult(
            ok=False,
            needs_manual=True,
            error="京东价格接口与页面解析均失败，请手动更新价格",
            title=title,
            image_url=image_url,
            tax_amount=float(tax_amount) if tax_amount else None,
        )
