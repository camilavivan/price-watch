"""JD (京东) best-effort public price API + HTML/JSON fallback; else needs_manual."""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from app.adapters.base import FetchResult
from app.adapters.generic_html import (
    BROWSER_UA,
    DEFAULT_HEADERS,
    extract_from_html,
    fetch_html,
)
from app.adapters.http_util import make_async_client
from app.adapters.product_meta import enrich_title_image
from app.adapters.rate_limit import wait_rate_limit
from app.url_normalize import extract_jd_sku, jd_product_canonical

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
    "bfPrice",
    "vipPrice",
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
    "estimatePrice",
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

_RISK_MARKERS = (
    "京东验证",
    "risk_handler",
    "privatedomain/risk",
    "bp_bizid",
    "cfe.m.jd.com/privatedomain",
)

# Honest user-facing error when cloud VPS cannot auto-price JD (risk / SPA / locked APIs)
JD_BLOCKED_ERROR = (
    "京东反爬/风控拦截，服务器无法自动取价；请用「填价」或配置 HTTP 代理后重试"
)

_SPA_SHELL_MARKERS = (
    "pageConfig",
    "window.pageConfig",
    "wareInfo",
    "__NEXT_DATA__",
    "webpackJsonp",
    'id="app"',
    "id='app'",
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


def _host(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").lower().split("@")[-1].split(":")[0]
    except Exception:
        return ""


def _is_jd_hk(url: str) -> bool:
    h = _host(url)
    return h == "jd.hk" or h.endswith(".jd.hk")


def _looks_like_risk_html(html: str) -> bool:
    if not html or len(html) < 40:
        return False
    sample = html[:8000]
    if any(m in sample for m in _RISK_MARKERS):
        return True
    # Title / body often carries 京东验证 even when URL already redirected
    if "京东验证" in html[:2000] or "<title>京东验证" in html:
        return True
    return False


def _has_embedded_price_fields(html: str) -> bool:
    if not html:
        return False
    return bool(
        _PRICE_FIELD_RE.search(html)
        or _TAX_FIELD_RE.search(html)
        or _ALLIN_FIELD_RE.search(html)
        or _GOODS_TEXT_RE.search(html)
        or _ALLIN_TEXT_RE.search(html)
    )


def _looks_like_spa_shell_no_price(html: str) -> bool:
    """
    SPA / empty product shell: pageConfig (or similar) present but no pPrice/taxFee.
    Typical npcitem/item.jd.hk cloud response ~35KB with no embedded prices.
    """
    if not html or len(html) < 200:
        return False
    if _looks_like_risk_html(html):
        return False
    if _has_embedded_price_fields(html):
        return False
    # Parsed note path already ran elsewhere; here only structural hint
    spaish = any(m in html for m in _SPA_SHELL_MARKERS)
    # Small-ish shells are common; also treat larger shells with pageConfig and no prices
    if spaish and (len(html) < 120_000 or "pageConfig" in html):
        return True
    return False


def _is_unusable_api_body(text: str) -> bool:
    if not text or len(text) < 8:
        return True
    low = text.lower()
    if '"echo"' in text or "'echo'" in text:
        if any(
            s in text or s in low
            for s in (
                "does not exist",
                "no access",
                "error2",
                "API does not exist",
                "not exists",
            )
        ):
            return True
    if "no access" in low and len(text) < 500:
        return True
    if "api does not exist" in low:
        return True
    return False


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
    if depth > 10:
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
                # Nested {"price": {"pPrice": ...}} — recurse into dict/list values
                if isinstance(v, (dict, list)):
                    _walk_collect(v, out, depth + 1)
                else:
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
            r"(?:pageConfig|wareInfo|itemInfo|_itemInfo|priceInfo|skuJson|product|"
            r"wareBusiness|priceResult)\s*=\s*(\{.+?\})\s*;",
            re.S | re.I,
        ),
        re.compile(
            r"window\.(?:pageConfig|ware|_itemInfo)\s*=\s*(\{.+?\})\s*;",
            re.S | re.I,
        ),
    ]
    for pat in patterns:
        for m in pat.finditer(html):
            raw = m.group(1)
            if len(raw) > 500_000:
                continue
            try:
                blobs.append(json.loads(raw))
            except Exception:
                continue
    # Also try raw regex-friendly substrings that look like JSON with pPrice/taxFee
    for m in re.finditer(
        r"\{[^{}]{0,3000}?(?:pPrice|taxFee|jdPrice|plusTaxPrice)[^{}]{0,3000}?\}",
        html,
    ):
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
    if not html:
        return {"list_price": None, "tax_amount": None, "note": None}

    collected: dict[str, list[float]] = {}
    for blob in _extract_json_blobs(html):
        _walk_collect(blob, collected)

    # Whole-document JSON (API response body)
    stripped = html.strip()
    if stripped[:1] in ("{", "["):
        try:
            _walk_collect(json.loads(stripped), collected)
        except Exception:
            pass

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
        price = None

    note: Optional[str] = None
    if price is not None and tax is not None:
        note = "JD HTML/JSON：商品价+税费分拆"
        return {"list_price": price, "tax_amount": tax, "note": note}
    if price is not None:
        note = "JD HTML/JSON：商品价" + ("（另有税费）" if tax else "")
        return {"list_price": price, "tax_amount": tax or 0.0, "note": note}
    if allin is not None:
        note = "JD HTML/JSON：含税价/预估合计作标价（tax=0，已含税）"
        return {"list_price": allin, "tax_amount": 0.0, "note": note}
    if tax is not None:
        return {"list_price": None, "tax_amount": tax, "note": "仅解析到税费"}
    return {"list_price": None, "tax_amount": None, "note": None}


def _is_dns_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    name = type(exc).__name__.lower()
    return any(
        s in msg or s in name
        for s in (
            "name or service not known",
            "no address associated",
            "nodename nor servname",
            "getaddrinfo",
            "name resolution",
            "errno -2",
            "errno -5",
            "gaierror",
        )
    )


async def _try_price_host(sku: str, host: str) -> Optional[float]:
    """Try a p.3.cn-style public price host. Soft-fail on DNS/network."""
    api_url = f"https://{host}/prices/mgets?skuIds=J_{sku}&type=1"
    headers = {
        "User-Agent": BROWSER_UA,
        "Referer": f"https://item.jd.com/{sku}.html",
        "Accept": "application/json, text/javascript, */*;q=0.01",
    }
    try:
        async with make_async_client(timeout=12.0, follow_redirects=True) as client:
            resp = await client.get(api_url, headers=headers)
            if resp.status_code != 200:
                logger.info("JD %s status=%s sku=%s", host, resp.status_code, sku)
                return None
            data = resp.json()
            if not (isinstance(data, list) and data):
                return None
            item = data[0]
            for key in ("p", "op", "m"):
                price = _to_price(item.get(key))
                if price is not None:
                    logger.info("JD price found source=%s sku=%s price=%s", host, sku, price)
                    return price
    except Exception as e:
        if _is_dns_error(e):
            logger.info("JD %s DNS soft-fail sku=%s: %s", host, sku, e)
        else:
            logger.info("JD %s soft-fail sku=%s: %s", host, sku, e)
    return None


async def _try_p3cn(sku: str) -> Optional[float]:
    """Public price APIs — never fatal; try p.3.cn then pe.3.cn if it resolves."""
    for host in ("p.3.cn", "pe.3.cn"):
        price = await _try_price_host(sku, host)
        if price is not None:
            return price
    return None


def _candidate_page_urls(sku: str, preferred_url: str = "") -> list[str]:
    """
    Ordered page URLs to try for HTML/JSON price parse.

    Prefer the caller-provided / resolved URL first (especially *.jd.hk),
    then other HK hosts, then mobile/mainland.
    """
    urls: list[str] = []
    seen: set[str] = set()

    def _add(u: str) -> None:
        u = (u or "").strip()
        if not u or not u.startswith("http"):
            return
        # Drop tracking query for fetch identity, keep path
        key = u.split("?", 1)[0]
        if key in seen:
            return
        seen.add(key)
        urls.append(u.split("#", 1)[0])

    pref = (preferred_url or "").strip()
    if pref.startswith("http"):
        _add(pref)
        # Normalize to clean jd.hk / jd.com product URL when sku known
        if sku:
            _add(jd_product_canonical(sku, pref))

    if sku:
        # Always try HK hosts too — global SKUs may be stored as item.jd.com by mistake
        for u in (
            f"https://mitem.jd.hk/product/{sku}.html",
            f"https://npcitem.jd.hk/{sku}.html",
            f"https://item.jd.hk/{sku}.html",
            f"https://item.m.jd.com/product/{sku}.html",
            f"https://item.jd.com/{sku}.html",
        ):
            _add(u)

    return urls


async def _try_secondary_endpoints(sku: str, *, prefer_hk: bool = False) -> Optional[dict[str, Optional[float]]]:
    """Best-effort public ware-style endpoints; fail soft."""
    urls: list[str] = []
    if prefer_hk:
        urls.append(
            f"https://color.jd.hk/api?functionId=pc_itempage_wareBusiness&appid=item-v3"
            f"&client=pc&clientVersion=1.0.0&body=%7B%22skuId%22%3A%22{sku}%22%7D"
        )
    urls.extend(
        [
            f"https://api.m.jd.com/api?functionId=pc_itempage_wareBusiness&appid=item-v3"
            f"&body=%7B%22skuId%22%3A%22{sku}%22%7D",
            f"https://item-soa.jd.com/getWareBusiness?skuId={sku}",
        ]
    )
    if not prefer_hk:
        urls.append(
            f"https://color.jd.hk/api?functionId=pc_itempage_wareBusiness&appid=item-v3"
            f"&client=pc&clientVersion=1.0.0&body=%7B%22skuId%22%3A%22{sku}%22%7D"
        )

    referer = (
        f"https://npcitem.jd.hk/{sku}.html"
        if prefer_hk
        else f"https://item.jd.com/{sku}.html"
    )
    headers = {
        **DEFAULT_HEADERS,
        "Referer": referer,
        "Accept": "application/json, text/plain, */*",
    }
    for url in urls:
        host = _host(url) or url[:40]
        try:
            async with make_async_client(timeout=10.0, follow_redirects=True) as client:
                resp = await client.get(url, headers=headers)
                if resp.status_code != 200:
                    logger.info("JD secondary %s status=%s sku=%s", host, resp.status_code, sku)
                    continue
                text = resp.text
                if not text or len(text) < 20:
                    continue
                # Skip echo / no access / API does not exist payloads
                if _is_unusable_api_body(text):
                    logger.info("JD secondary %s unusable API body sku=%s", host, sku)
                    continue
                try:
                    data = resp.json()
                except Exception:
                    data = None
                collected: dict[str, list[float]] = {}
                if data is not None:
                    _walk_collect(data, collected)
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
                if collected.get("allin") and not price:
                    from collections import Counter

                    price = float(
                        Counter(round(v, 2) for v in collected["allin"]).most_common(1)[0][0]
                    )
                    tax = 0.0
                if parsed.get("list_price") and not price:
                    price = parsed["list_price"]
                if parsed.get("tax_amount") is not None and tax is None:
                    tax = parsed["tax_amount"]
                if price:
                    logger.info(
                        "JD price found source=%s sku=%s price=%s tax=%s",
                        host,
                        sku,
                        price,
                        tax,
                    )
                    return {
                        "list_price": price,
                        "tax_amount": tax if tax is not None else 0.0,
                        "note": f"JD 公开 ware/business 接口 ({host})",
                    }
                logger.info("JD secondary %s no price in body sku=%s", host, sku)
        except Exception as e:
            if _is_dns_error(e):
                logger.info("JD secondary DNS soft-fail %s: %s", host, e)
            else:
                logger.info("JD secondary soft-fail %s: %s", host, e)
    return None


async def _fetch_page_parse(
    url: str,
) -> tuple[dict[str, Optional[float]], Optional[str], Optional[str], str]:
    """
    Fetch HTML and parse JD-specific + generic fallback.
    Returns (parsed, title, image, status_note).
    """
    try:
        html = await fetch_html(url, timeout=15.0)
    except Exception as e:
        if _is_dns_error(e):
            logger.info("JD page DNS soft-fail url=%s: %s", url[:80], e)
            return (
                {"list_price": None, "tax_amount": None, "note": None},
                None,
                None,
                f"DNS失败:{_host(url)}",
            )
        logger.info("JD page fetch failed url=%s: %s", url[:80], e)
        return (
            {"list_price": None, "tax_amount": None, "note": None},
            None,
            None,
            f"请求失败:{_host(url)}",
        )

    if _looks_like_risk_html(html):
        logger.info("JD page risk/challenge html url=%s", url[:80])
        return (
            {"list_price": None, "tax_amount": None, "note": None},
            None,
            None,
            f"风控页:{_host(url)}",
        )

    if _looks_like_spa_shell_no_price(html):
        # Still try meta for title/image
        generic_meta = extract_from_html(html, base_url=url)
        logger.info("JD page SPA shell without price fields url=%s", url[:80])
        return (
            {"list_price": None, "tax_amount": None, "note": "页面无内嵌价格（需接口/手动）"},
            generic_meta.title,
            generic_meta.image_url,
            f"SPA无价格:{_host(url)}",
        )

    parsed = parse_jd_price_tax(html)
    generic = extract_from_html(html, base_url=url)
    title = generic.title
    image_url = generic.image_url

    if parsed.get("list_price") is None and generic.ok and generic.price:
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

    if parsed.get("list_price"):
        status = "ok"
    elif "pageConfig" in html and not _has_embedded_price_fields(html):
        status = f"SPA无价格:{_host(url)}"
        parsed["note"] = parsed.get("note") or "页面无内嵌价格（需接口/手动）"
    else:
        status = f"无价格:{_host(url)}"
    if parsed.get("list_price"):
        logger.info(
            "JD price found source=html:%s sku_url=%s price=%s tax=%s note=%s",
            _host(url),
            url[:100],
            parsed.get("list_price"),
            parsed.get("tax_amount"),
            parsed.get("note"),
        )
    return parsed, title, image_url, status


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

        preferred = (
            url.strip()
            if (url or "").strip().startswith("http")
            else jd_product_canonical(sku, url or "")
        )
        page_urls = _candidate_page_urls(sku, preferred)
        prefer_hk = _is_jd_hk(preferred) or any(_is_jd_hk(u) for u in page_urls[:2])

        list_price: Optional[float] = None
        tax_amount: Optional[float] = None
        note: Optional[str] = None
        title: Optional[str] = None
        image_url: Optional[str] = None
        tried: list[str] = []
        saw_risk = False
        saw_spa = False

        # 1) Prefer actual expanded / HK product HTML first (not p.3.cn)
        for page_url in page_urls:
            tried.append(_host(page_url) or page_url[:40])
            parsed, t1, i1, status = await _fetch_page_parse(page_url)
            title = title or t1
            image_url = image_url or i1
            if status.startswith("风控"):
                saw_risk = True
            if status.startswith("SPA无价格"):
                saw_spa = True
            if parsed.get("tax_amount") and not tax_amount:
                tax_amount = float(parsed["tax_amount"] or 0)
            if parsed.get("list_price"):
                list_price = float(parsed["list_price"])
                if parsed.get("tax_amount") is not None:
                    tax_amount = float(parsed["tax_amount"] or 0)
                note = (parsed.get("note") or "JD HTML/JSON") + f" ({_host(page_url)})"
                break

        # 2) p.3.cn / pe.3.cn — soft DNS, never required
        if list_price is None:
            tried.append("p.3.cn/pe.3.cn")
            api_price = await _try_p3cn(sku)
            if api_price is not None:
                list_price = api_price
                note = "来自京东公开价格接口（到手价需自行填券/满减；海淘税费另计）"

        # 3) Optional secondary public endpoints (api.m.jd.com / color.jd.hk / item-soa)
        if list_price is None:
            tried.append("api.m.jd.com/color.jd.hk")
            sec = await _try_secondary_endpoints(sku, prefer_hk=prefer_hk)
            if sec and sec.get("list_price"):
                list_price = float(sec["list_price"])
                tax_amount = float(sec.get("tax_amount") or 0)
                note = sec.get("note")

        # Always enrich title/image
        if not title or not image_url:
            t2, i2 = await enrich_title_image(platform="jd", url=preferred, sku_id=sku)
            title = title or t2
            image_url = image_url or i2

        if list_price is not None and list_price > 0:
            logger.info(
                "JD fetch ok sku=%s price=%s tax=%s source=%s",
                sku,
                list_price,
                tax_amount,
                note,
            )
            return FetchResult(
                ok=True,
                list_price=list_price,
                tax_amount=float(tax_amount or 0),
                title=title,
                image_url=image_url,
                needs_manual=False,
                raw_note=note or "京东价格解析",
            )

        tried_s = "、".join(dict.fromkeys(tried))  # preserve order, unique
        if saw_risk or saw_spa:
            extra = []
            if saw_risk:
                extra.append("风控验证页")
            if saw_spa:
                extra.append("页面无内嵌价格（需接口/手动）")
            err = JD_BLOCKED_ERROR + f"（已试：{tried_s}；{ '、'.join(extra) }）"
        else:
            err = (
                JD_BLOCKED_ERROR
                + f"（已试：{tried_s}）。"
                "若为海淘/JD.HK 商品请确认链接为 jd.hk。"
            )
        logger.info(
            "JD fetch needs_manual sku=%s tried=%s risk=%s spa=%s",
            sku,
            tried_s,
            saw_risk,
            saw_spa,
        )
        return FetchResult(
            ok=False,
            needs_manual=True,
            error=err,
            title=title,
            image_url=image_url,
            tax_amount=float(tax_amount) if tax_amount else None,
        )
