"""Normalize product URLs: detect platform, extract sku_id, strip tracking params."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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

# Trailing punctuation glued to URLs in QQ / WeChat share pastes
_TRAILING_URL_JUNK = re.compile(r"[）)」』】\"'“”‘’。，、！？!?,.;:\]\}>]+$")

# Stop at whitespace, CJK, or common wrappers — share pastes glue junk to URLs
_URL_IN_TEXT = re.compile(
    r"https?://[^\s\u4e00-\u9fff\u3000-\u303f\uff00-\uffef<>\"\'）)」』】\[\]{}|\\^`]+",
    re.I,
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
        if not re.search(r"/\d+\.html", path, re.I):
            return True
    if host in ("u.jd.com",) or (host.endswith(".jd.com") and host.startswith("u")):
        return True
    if "click.taobao.com" in host or "click.tmall.com" in host:
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
        r"(?:item\.m\.jd\.com|item\.jd\.com|npcitem\.jd\.hk|item\.jd\.hk)/(\d+)\.html",
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


def _canonical_for(platform: str, sku_id: Optional[str], cleaned: str) -> str:
    if platform == "jd" and sku_id:
        return f"https://item.jd.com/{sku_id}.html"
    if platform == "taobao" and sku_id:
        # Prefer item.taobao.com; tmall also accepts id=
        host = urlparse(cleaned).netloc.lower()
        if "tmall" in host:
            return f"https://detail.tmall.com/item.htm?id={sku_id}"
        return f"https://item.taobao.com/item.htm?id={sku_id}"
    if platform == "pdd" and sku_id:
        return f"https://mobile.yangkeduo.com/goods.html?goods_id={sku_id}"
    return cleaned


async def resolve_url(url: str) -> str:
    """
    Follow HTTP redirects to the final URL (for short links like 3.jd.hk / m.tb.cn).

    Uses browser UA, ~15s timeout. On failure returns the cleaned original URL.
    """
    original = (url or "").strip()
    ensured = _ensure_scheme(original)
    if not ensured:
        return original

    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=True,
            max_redirects=15,
            headers=headers,
        ) as client:
            # HEAD first is flaky on some CDNs; GET is more reliable for short links
            resp = await client.get(ensured)
            final = str(resp.url)
            final = strip_url_trailing_junk(final)
            if final:
                logger.debug("resolve_url %s -> %s", ensured, final)
                return final
    except Exception as e:
        logger.warning("resolve_url failed for %s: %s", ensured, e)
    return ensured


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
