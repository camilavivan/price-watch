"""Normalize product URLs: detect platform, extract sku_id, strip tracking params.

Also expands commerce short links (m.tb.cn, 3.jd.hk, …) by following HTTP
redirects and parsing HTML interstitial pages (var url / location / meta refresh).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import unescape
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx

logger = logging.getLogger(__name__)

# Tracking / affiliate / share junk to drop from query strings
_STRIP_QUERY_PREFIXES = (
    "utm_",
    "spm",
    "scm",
    "scm_id",
    "from",
    "source",
    "refer",
    "share",
    "share_",
    "track",
    "track_",
    "clk",
    "click",
    "scene",
    "extension_id",
    "jd_pop",
    "jd_",
    "cu",
    "cu_id",
    "gx",
    "gxd",
    "abt",
    "wx_",
    "mp_",
    "pvid",
    "scm-url",
)
_STRIP_QUERY_EXACT = frozenset(
    {
        "spm",
        "scm",
        "from",
        "source",
        "refer",
        "referer",
        "referrer",
        "shareid",
        "shareUniqueId",
        "share_id",
        "extension_id",
        "jd_pop",
        "cu",
        "cu_id",
        "btas",
        "pvid",
        "scene",
        "clicktime",
        "enterutm_medium",
        "enterutm_term",
        "enterutm_campaign",
        "enterutm_content",
        "enterutm_source",
        "jxsid",
        "appuid",
        "umpChannel",
        "u_channel",
        "_x_log_bu",
        "_x_log_sm",
    }
)

# Query keys that identify the product — keep these
_KEEP_QUERY = frozenset(
    {
        "id",
        "item_id",
        "itemId",
        "itemid",
        "goods_id",
        "goodsId",
        "skuId",
        "skuid",
        "sku_id",
    }
)

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
_MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)

_MAX_EXPAND_HOPS = 8

# Trailing punctuation glued to URLs in QQ / WeChat share pastes
_TRAILING_URL_JUNK = re.compile(r"[）)」』】\"'“”‘’。，、！？!?,.;:\]\}>]+$")

# Stop at whitespace, CJK, or common wrappers — share pastes glue junk to URLs
# Also stop at emoji / symbol blocks often used in 淘口令 (💲🔐 etc.)
_URL_IN_TEXT = re.compile(
    r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef"
    r"\U0001f300-\U0001faff\u2600-\u27bf"
    r"<>\"\'）)」』】\[\]{}|\\^`]+",
    re.I,
)

# Title wrappers in share pastes: 「…」 or 【…】
_TITLE_DOUBLE = re.compile(r"「([^」]{2,80})」")
_TITLE_CORNER = re.compile(r"【([^】]{2,80})】")
_TITLE_STRIP_PREFIXES = (
    "询客服领券",
    "点击领取",
    "领券",
    "粉丝福利购",
    "福利购",
    "京东",
    "淘宝",
    "天猫",
    "拼多多",
)

UNKNOWN_PLATFORM_MSG = (
    "无法识别平台，请使用京东 / 淘宝(天猫) / 拼多多商品链接"
    "（支持短链：m.tb.cn、tb.cn、u.jd.com、3.cn、3.jd.hk、p.pinduoduo.com 等，"
    "短链会自动跳转展开）"
)


@dataclass(frozen=True)
class NormalizedURL:
    platform: Optional[str]
    sku_id: Optional[str]
    canonical_url: str
    original_url: str

    def as_dict(self) -> dict:
        return {
            "platform": self.platform,
            "sku_id": self.sku_id,
            "canonical_url": self.canonical_url,
        }


class UnknownPlatformError(ValueError):
    """URL host is not jd / taobao|tmall / pdd."""


def strip_url_trailing_junk(url: str) -> str:
    """Remove trailing punctuation often copied with share links."""
    u = (url or "").strip()
    prev = None
    while prev != u:
        prev = u
        u = _TRAILING_URL_JUNK.sub("", u)
    return u


def extract_first_url(text: str) -> Optional[str]:
    """Extract first http(s) URL from free text; strip trailing junk."""
    if not text:
        return None
    m = _URL_IN_TEXT.search(text)
    if not m:
        return None
    url = strip_url_trailing_junk(m.group(0))
    if not re.match(r"^https?://.+", url, re.I):
        return None
    return url


def extract_urls(text: str) -> list[str]:
    """Extract all http(s) URLs from free text (deduped, order preserved)."""
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for m in _URL_IN_TEXT.finditer(text):
        url = strip_url_trailing_junk(m.group(0))
        if not re.match(r"^https?://.+", url, re.I):
            continue
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
    return out


def _commerce_url_priority(url: str) -> int:
    """Lower = better. Prefer known short hosts, then item pages, then other commerce."""
    host = _host_of(url)
    if not host:
        return 100
    if host in (
        "m.tb.cn",
        "tb.cn",
        "e.tb.cn",
        "s.tb.cn",
        "u.jd.com",
        "3.jd.com",
        "3.jd.hk",
        "3.cn",
        "p.pinduoduo.com",
    ) or host.endswith(".tb.cn"):
        return 0
    if host.endswith(".jd.hk") and not host.startswith("item."):
        return 1
    if is_short_link(url):
        return 2
    if host in ("item.jd.com", "item.m.jd.com", "item.taobao.com") or "tmall.com" in host:
        return 3
    if detect_platform(url):
        return 5
    return 50


def extract_best_url(text: str) -> Optional[str]:
    """
    Prefer commerce short hosts (m.tb.cn / 3.jd.hk / …) when multiple URLs present.
    Falls back to first URL.
    """
    urls = extract_urls(text)
    if not urls:
        return None
    urls_sorted = sorted(urls, key=_commerce_url_priority)
    return urls_sorted[0]


def extract_title_hint(text: str) -> Optional[str]:
    """
    Extract a product title hint from 「…」 or 【…】 near share pastes.

    Strips coupon / platform prefixes like 「询客服领券」. Skips bare platform
    tags such as 【京东】 / 【淘宝】.
    """
    if not text:
        return None
    candidates: list[str] = []
    for m in _TITLE_DOUBLE.finditer(text):
        candidates.append(m.group(1).strip())
    for m in _TITLE_CORNER.finditer(text):
        candidates.append(m.group(1).strip())

    skip_exact = {"京东", "淘宝", "天猫", "拼多多", "JD", "Taobao", "Tmall"}
    for raw in candidates:
        t = raw.strip()
        if not t or t in skip_exact:
            continue
        # Nested 【询客服领券】a2… inside 「」
        inner = _TITLE_CORNER.fullmatch(t)
        if inner:
            t = inner.group(1).strip()
        # Strip leading 【prefix】
        t = re.sub(r"^【[^】]{1,20}】\s*", "", t).strip()
        for pref in _TITLE_STRIP_PREFIXES:
            if t.startswith(pref):
                t = t[len(pref) :].lstrip("：:·-— \t")
        t = t.strip()
        if t in skip_exact or len(t) < 2:
            continue
        # Prefer longer / more product-like (has CJK or alnum beyond platform tag)
        if re.search(r"[\u4e00-\u9fffA-Za-z0-9]", t):
            return t[:80]
    return None


def _ensure_scheme(url: str) -> str:
    u = strip_url_trailing_junk(url or "")
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u.lstrip("/")
    return u


def _host_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host.split("@")[-1].split(":")[0]


def detect_platform(url: str) -> Optional[str]:
    """Detect platform from host: jd / taobao / pdd (incl. short-link hosts)."""
    u = _ensure_scheme(url)
    if not u:
        return None
    host = _host_of(u)
    if not host:
        return None

    # --- JD (incl. short links) ---
    # u.jd.com, 3.jd.com, 3.jd.hk, item.jd.com, numeric *.jd.hk, bare 3.cn
    if (
        host == "3.cn"
        or host == "jd.com"
        or host == "jd.hk"
        or host.endswith(".jd.com")
        or host.endswith(".jd.hk")
    ):
        return "jd"

    # --- Taobao / Tmall short + long ---
    if host in (
        "m.tb.cn",
        "tb.cn",
        "e.tb.cn",
        "s.tb.cn",
        "a.m.taobao.com",
    ) or host.endswith(".tb.cn"):
        return "taobao"
    if (
        host.endswith(".taobao.com")
        or host == "taobao.com"
        or host.endswith(".tmall.com")
        or host == "tmall.com"
        or host.endswith(".tmall.hk")
        or host == "tmall.hk"
        or host.endswith(".liangxinyao.com")  # some tmall brand stores
        or host == "s.click.taobao.com"
        or host.endswith(".click.taobao.com")
        or host.endswith(".click.tmall.com")
    ):
        return "taobao"

    # --- PDD ---
    if (
        host.endswith(".pinduoduo.com")
        or host == "pinduoduo.com"
        or host.endswith(".yangkeduo.com")
        or host == "yangkeduo.com"
        or host == "p.pinduoduo.com"
    ):
        return "pdd"

    return None


def is_short_link(url: str) -> bool:
    """Whether URL looks like a share/short link that should be HTTP-expanded."""
    u = _ensure_scheme(url)
    host = _host_of(u)
    if not host:
        return False
    short_exact = {
        "3.cn",
        "m.tb.cn",
        "tb.cn",
        "e.tb.cn",
        "s.tb.cn",
        "u.jd.com",
        "3.jd.com",
        "3.jd.hk",
        "a.m.taobao.com",
        "p.pinduoduo.com",
        "s.click.taobao.com",
    }
    if host in short_exact or host.endswith(".tb.cn"):
        return True
    # numeric subdomain short paths on jd.hk e.g. 3.jd.hk already covered;
    # also u*.jd.com shortener-style hosts without item path
    if host.endswith(".jd.hk") and not host.startswith("item."):
        path = urlparse(u).path or ""
        # Product pages on mitem/npcitem are not short links
        if re.search(r"/(?:product/)?\d+\.html", path, re.I):
            return False
        if not re.search(r"/\d+\.html", path, re.I):
            return True
    if host in ("u.jd.com",) or (host.endswith(".jd.com") and host.startswith("u")):
        return True
    if "click.taobao.com" in host or "click.tmall.com" in host:
        return True
    return False


def _looks_like_item_url(url: str) -> bool:
    """True if URL already has an extractable product id."""
    plat = detect_platform(url)
    if plat == "jd" and extract_jd_sku(url):
        return True
    if plat == "taobao" and extract_taobao_id(url):
        return True
    if plat == "pdd" and extract_pdd_goods_id(url):
        return True
    return False


def _should_strip_query_key(key: str) -> bool:
    k = (key or "").strip()
    if not k:
        return True
    if k in _KEEP_QUERY:
        return False
    kl = k.lower()
    if kl in {x.lower() for x in _STRIP_QUERY_EXACT}:
        return True
    for p in _STRIP_QUERY_PREFIXES:
        if kl.startswith(p.lower()):
            return True
    return False


def strip_tracking_params(url: str) -> str:
    """Drop tracking/affiliate query params; keep product id params."""
    u = _ensure_scheme(url)
    if not u:
        return ""
    parsed = urlparse(u)
    kept = []
    for k, v in parse_qsl(parsed.query, keep_blank_values=False):
        if _should_strip_query_key(k):
            continue
        kept.append((k, v))
    # Prefer a stable order
    kept.sort(key=lambda kv: kv[0].lower())
    new_query = urlencode(kept, doseq=True)
    # Drop fragments (often share junk)
    return urlunparse(
        (parsed.scheme.lower() or "https", parsed.netloc.lower(), parsed.path, "", new_query, "")
    )


def extract_jd_sku(url: str) -> Optional[str]:
    u = _ensure_scheme(url)
    if not u:
        return None
    patterns = (
        r"(?:item\.m\.jd\.com|item\.jd\.com|mitem\.jd\.hk|npcitem\.jd\.hk|item\.jd\.hk)/(\d+)\.html",
        r"(?:www\.)?jd\.com/(\d+)\.html",
        r"/product/(\d+)\.html",
        r"/(\d{6,})\.html",
    )
    for pat in patterns:
        m = re.search(pat, u, re.I)
        if m:
            return m.group(1)
    qs = dict(parse_qsl(urlparse(u).query, keep_blank_values=False))
    for key in ("skuId", "skuid", "sku_id", "sku"):
        if key in qs and re.fullmatch(r"\d{5,}", qs[key] or ""):
            return qs[key]
    return None


def extract_jd_sku_from_html(html: str) -> Optional[str]:
    """Pull skuId from JD page HTML / embedded JS when URL path lacks it."""
    if not html:
        return None
    patterns = (
        r"""["']?skuId["']?\s*[:=]\s*["']?(\d{5,})""",
        r"""item\.jd\.com/(\d{5,})\.html""",
        r"""item\.m\.jd\.com/product/(\d{5,})\.html""",
        r"""["']skuid["']\s*[:=]\s*["']?(\d{5,})""",
    )
    for pat in patterns:
        m = re.search(pat, html, re.I)
        if m:
            return m.group(1)
    return None


def extract_taobao_id(url: str) -> Optional[str]:
    u = _ensure_scheme(url)
    if not u:
        return None
    parsed = urlparse(u)
    qs = dict(parse_qsl(parsed.query, keep_blank_values=False))
    for key in ("id", "item_id", "itemId", "itemid"):
        if key in qs and re.fullmatch(r"\d{5,}", qs[key] or ""):
            return qs[key]
    # path forms: /i123456.htm /item/123456.htm
    m = re.search(r"/i(\d{5,})\.htm", parsed.path, re.I)
    if m:
        return m.group(1)
    m = re.search(r"/item(?:\.htm)?/(\d{5,})", parsed.path, re.I)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=(\d{5,})", u, re.I)
    if m:
        return m.group(1)
    return None


def extract_pdd_goods_id(url: str) -> Optional[str]:
    u = _ensure_scheme(url)
    if not u:
        return None
    parsed = urlparse(u)
    qs = dict(parse_qsl(parsed.query, keep_blank_values=False))
    for key in ("goods_id", "goodsId", "goods_id"):
        if key in qs and re.fullmatch(r"\d{5,}", qs[key] or ""):
            return qs[key]
    # /goods/123456.html or /goods.html?goods_id=
    m = re.search(r"/goods(?:/|\.html).*?[?&]goods_id=(\d{5,})", u, re.I)
    if m:
        return m.group(1)
    m = re.search(r"/goods/(\d{5,})", parsed.path, re.I)
    if m:
        return m.group(1)
    m = re.search(r"[?&]goods_id=(\d{5,})", u, re.I)
    if m:
        return m.group(1)
    return None


def _is_jd_hk_host(host: str) -> bool:
    h = (host or "").lower()
    return h == "jd.hk" or h.endswith(".jd.hk")


def jd_product_canonical(sku_id: str, source_url: str = "") -> str:
    """
    Build JD product canonical URL.

    Preserve *.jd.hk product hosts (mitem / npcitem / item.jd.hk) when the
    resolved/source URL already lives on jd.hk — do NOT rewrite those to
    item.jd.com (mainland page often lacks HK/global price JSON).
    """
    sku = str(sku_id).strip()
    host = _host_of(source_url or "")
    path = ""
    try:
        path = urlparse(source_url or "").path or ""
    except Exception:
        path = ""
    if _is_jd_hk_host(host):
        if host.startswith("mitem.") or "/product/" in path:
            return f"https://mitem.jd.hk/product/{sku}.html"
        if host.startswith("npcitem."):
            return f"https://npcitem.jd.hk/{sku}.html"
        if host.startswith("item.") or host == "jd.hk":
            return f"https://item.jd.hk/{sku}.html"
        # Other *.jd.hk product hosts → mitem product form (share/mobile style)
        return f"https://mitem.jd.hk/product/{sku}.html"
    return f"https://item.jd.com/{sku}.html"


def _canonical_for(platform: str, sku_id: Optional[str], cleaned: str) -> str:
    if platform == "jd" and sku_id:
        return jd_product_canonical(sku_id, cleaned or "")
    if platform == "taobao" and sku_id:
        # Prefer item.taobao.com; tmall also accepts id=
        host = urlparse(cleaned).netloc.lower()
        if "tmall" in host:
            return f"https://detail.tmall.com/item.htm?id={sku_id}"
        return f"https://item.taobao.com/item.htm?id={sku_id}"
    if platform == "pdd" and sku_id:
        return f"https://mobile.yangkeduo.com/goods.html?goods_id={sku_id}"
    return cleaned


def _clean_extracted_url(raw: str, base: str = "") -> Optional[str]:
    if not raw:
        return None
    u = unescape(raw.strip().strip("\"'"))
    u = u.replace("\\/", "/").replace("\\u002F", "/").replace("\\x2f", "/")
    if u.startswith("//"):
        u = "https:" + u
    if base and not re.match(r"^https?://", u, re.I):
        u = urljoin(base, u)
    u = strip_url_trailing_junk(_ensure_scheme(u))
    if not re.match(r"^https?://.+", u, re.I):
        return None
    return u


def extract_url_from_html(html: str, *, base_url: str = "") -> Optional[str]:
    """
    Extract next hop URL from short-link interstitial HTML.

    Priority:
      1. var url = '...' / var url = "..."
      2. window.location / location.href assignments
      3. meta refresh URL=
      4. og:url
      5. first http(s) link to known item hosts
    """
    if not html:
        return None
    body = html

    # 1. var url = '...'  (Taobao m.tb.cn classic)
    for pat in (
        r"""var\s+url\s*=\s*['"]([^'"]+)['"]""",
        r"""var\s+url\s*=\s*`([^`]+)`""",
    ):
        m = re.search(pat, body, re.I)
        if m:
            u = _clean_extracted_url(m.group(1), base_url)
            if u:
                return u

    # 2. location assignments
    for pat in (
        r"""(?:window\.)?location(?:\.href)?\s*=\s*['"]([^'"]+)['"]""",
        r"""(?:window\.)?location\.replace\(\s*['"]([^'"]+)['"]""",
        r"""(?:window\.)?location\.assign\(\s*['"]([^'"]+)['"]""",
    ):
        m = re.search(pat, body, re.I)
        if m:
            u = _clean_extracted_url(m.group(1), base_url)
            if u:
                return u

    # 3. meta refresh
    m = re.search(
        r"""<meta[^>]+http-equiv\s*=\s*['"]?refresh['"]?[^>]+content\s*=\s*['"][^'"]*url\s*=\s*([^'">\s]+)""",
        body,
        re.I,
    )
    if not m:
        m = re.search(
            r"""<meta[^>]+content\s*=\s*['"][^'"]*url\s*=\s*([^'">\s]+)[^'"]*['"][^>]+http-equiv\s*=\s*['"]?refresh['"]?""",
            body,
            re.I,
        )
    if m:
        u = _clean_extracted_url(m.group(1), base_url)
        if u:
            return u

    # 4. og:url
    m = re.search(
        r"""<meta[^>]+property\s*=\s*['"]og:url['"][^>]+content\s*=\s*['"]([^'"]+)['"]""",
        body,
        re.I,
    )
    if not m:
        m = re.search(
            r"""<meta[^>]+content\s*=\s*['"]([^'"]+)['"][^>]+property\s*=\s*['"]og:url['"]""",
            body,
            re.I,
        )
    if m:
        u = _clean_extracted_url(m.group(1), base_url)
        if u:
            return u

    # 5. first link to known item hosts
    item_link = re.search(
        r"""https?://(?:item\.taobao\.com|detail\.tmall\.com|h5\.m\.taobao\.com|item\.jd\.com|item\.m\.jd\.com|mitem\.jd\.hk|npcitem\.jd\.hk|item\.jd\.hk|detail\.tmall\.hk)[^\s"'<>\\]+""",
        body,
        re.I,
    )
    if item_link:
        u = _clean_extracted_url(item_link.group(0), base_url)
        if u:
            return u
    # sku= / id= query form in href
    m = re.search(
        r"""https?://[^\s"'<>\\]+(?:[?&](?:skuId|sku|id|item_id|itemId)=[0-9]{5,})[^\s"'<>\\]*""",
        body,
        re.I,
    )
    if m:
        u = _clean_extracted_url(m.group(0), base_url)
        if u and detect_platform(u):
            return u

    return None


def _needs_further_expand(url: str, html: str = "") -> bool:
    """Continue expanding if still short/intermediate or HTML has no sku yet."""
    if is_short_link(url):
        return True
    if _looks_like_item_url(url):
        return False
    host = _host_of(url)
    # Intermediate affiliate / login / jump pages
    if host and (
        "click.taobao.com" in host
        or "click.tmall.com" in host
        or host.endswith(".tb.cn")
        or "login." in host
        or "passport." in host
    ):
        return True
    if html and extract_url_from_html(html, base_url=url):
        # HTML interstitial without sku on URL
        if not _looks_like_item_url(url):
            return True
    return False


def _headers_for(ua: str) -> dict[str, str]:
    return {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }


async def _fetch_once(
    client: httpx.AsyncClient, url: str
) -> tuple[str, str, Optional[str]]:
    """
    GET url without auto-follow; return (final_or_location_url, body, location_header).

    Manually inspects Location so we can log each hop and also parse HTML bodies
    that lack a redirect header.
    """
    resp = await client.get(url, follow_redirects=False)
    loc = resp.headers.get("location") or resp.headers.get("Location")
    body = ""
    ctype = (resp.headers.get("content-type") or "").lower()
    if "html" in ctype or "text" in ctype or not ctype:
        try:
            body = resp.text or ""
        except Exception:
            body = ""
    if loc:
        next_url = _clean_extracted_url(loc, url) or loc
        return next_url, body, loc
    # Some CDNs return 200 with the real URL already
    return str(resp.url), body, None


async def resolve_url(url: str) -> str:
    """
    Multi-hop expand short / share links to a product URL.

    For each hop (max 8):
      1. GET with browser UA (desktop; mobile fallback for tb.cn if stuck)
      2. Prefer Location redirect, else parse HTML embedded URL
         (var url / location / meta refresh / og:url / item hosts)
      3. Stop when URL looks like an item page with sku/id, or hops exhausted

    For JD: if final URL/HTML has skuId, canonicalize to item.jd.com/{sku}.html (or mitem/npcitem/item.jd.hk/{sku} when resolved on *.jd.hk).
    For Taobao: prefer item.taobao.com / detail.tmall.com with id=.
    Never calls paid APIs. On failure returns the cleaned original URL.
    """
    original = (url or "").strip()
    ensured = _ensure_scheme(original)
    if not ensured:
        return original

    current = ensured
    last_html = ""
    used_mobile = False

    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=False,
            headers=_headers_for(_BROWSER_UA),
        ) as client:
            for hop in range(_MAX_EXPAND_HOPS):
                if _looks_like_item_url(current) and not is_short_link(current):
                    break

                logger.info("resolve_url hop %s: GET %s", hop + 1, current)
                try:
                    next_url, body, loc = await _fetch_once(client, current)
                except Exception as e:
                    logger.warning("resolve_url hop %s fetch failed: %s", hop + 1, e)
                    break

                if body:
                    last_html = body

                embedded = extract_url_from_html(body, base_url=str(current)) if body else None

                # Choose next: Location (if still intermediate / short) or HTML embed
                candidate = None
                if loc and next_url and next_url != current:
                    candidate = next_url
                    logger.info("resolve_url hop %s: Location -> %s", hop + 1, candidate)
                if embedded and (not candidate or is_short_link(candidate) or not _looks_like_item_url(candidate)):
                    # Prefer embedded item URL over another short hop when available
                    if _looks_like_item_url(embedded) or not candidate:
                        candidate = embedded
                        logger.info("resolve_url hop %s: HTML embed -> %s", hop + 1, candidate)
                    elif is_short_link(candidate) and not is_short_link(embedded):
                        candidate = embedded
                        logger.info("resolve_url hop %s: HTML embed (prefer) -> %s", hop + 1, candidate)

                if not candidate:
                    # Auto-followed style: response URL may already be final
                    if next_url and next_url != current and (
                        is_short_link(current) or not _looks_like_item_url(current)
                    ):
                        candidate = next_url
                    else:
                        # Stuck on tb.cn interstitial — try mobile UA once
                        host = _host_of(current)
                        if (
                            not used_mobile
                            and host
                            and (host.endswith(".tb.cn") or host == "tb.cn")
                        ):
                            used_mobile = True
                            client.headers.update(_headers_for(_MOBILE_UA))
                            logger.info("resolve_url: retry with mobile UA for %s", current)
                            continue
                        break

                candidate = strip_url_trailing_junk(_ensure_scheme(candidate))
                if not candidate or candidate == current:
                    break
                current = candidate

                if _looks_like_item_url(current) and not is_short_link(current):
                    logger.info("resolve_url done (item url): %s", current)
                    break
    except Exception as e:
        logger.warning("resolve_url failed for %s: %s", ensured, e)
        return ensured

    # Post-process: JD sku from URL or HTML → preserve jd.hk when resolved there
    plat = detect_platform(current)
    if plat == "jd":
        sku = extract_jd_sku(current) or extract_jd_sku_from_html(last_html)
        if sku:
            canonical = jd_product_canonical(sku, current)
            logger.info("resolve_url JD canonical: %s", canonical)
            return canonical
    if plat == "taobao":
        tid = extract_taobao_id(current)
        if tid:
            host = _host_of(current)
            if "tmall" in host:
                return f"https://detail.tmall.com/item.htm?id={tid}"
            # h5.m.taobao.com / item.taobao.com / others → desktop item
            return f"https://item.taobao.com/item.htm?id={tid}"

    final = strip_url_trailing_junk(current)
    logger.debug("resolve_url %s -> %s", ensured, final)
    return final or ensured


def normalize_url(url: str, *, require_known_platform: bool = False) -> NormalizedURL:
    """
    Clean URL → extract sku_id → store canonical_url.

    Accepts already-resolved (redirect-expanded) URLs. Call resolve_url() first
    for short links in create-watch flows.

    Returns {platform, sku_id, canonical_url} (+ original_url).
    Raises UnknownPlatformError if require_known_platform and host unknown.
    """
    original = (url or "").strip()
    ensured = _ensure_scheme(original)
    platform = detect_platform(ensured)
    if require_known_platform and platform is None:
        raise UnknownPlatformError(UNKNOWN_PLATFORM_MSG)

    cleaned = strip_tracking_params(ensured) if ensured else ""
    sku_id: Optional[str] = None
    if platform == "jd":
        sku_id = extract_jd_sku(ensured) or extract_jd_sku(cleaned)
    elif platform == "taobao":
        sku_id = extract_taobao_id(ensured) or extract_taobao_id(cleaned)
    elif platform == "pdd":
        sku_id = extract_pdd_goods_id(ensured) or extract_pdd_goods_id(cleaned)

    if platform:
        canonical = _canonical_for(platform, sku_id, cleaned or ensured)
    else:
        canonical = cleaned or ensured or original

    return NormalizedURL(
        platform=platform,
        sku_id=sku_id,
        canonical_url=canonical,
        original_url=original,
    )


def guess_name_from_url(url: str, platform: str) -> str:
    plat_label = {"jd": "京东", "taobao": "淘宝", "pdd": "拼多多"}.get(platform, platform)
    info = normalize_url(url)
    if info.sku_id:
        return f"{plat_label}商品 {info.sku_id}"
    return f"{plat_label}商品"


def normalize_zh_text(text: str) -> str:
    """Light Chinese text cleanup (whitespace / fullwidth digits)."""
    if not text:
        return ""
    t = text.replace("\u3000", " ").strip()
    t = re.sub(r"\s+", " ", t)
    # fullwidth digits → halfwidth
    trans = str.maketrans("０１２３４５６７８９．，", "0123456789.,")
    return t.translate(trans)
