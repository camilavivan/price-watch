"""Normalize product URLs: detect platform, extract sku_id, strip tracking params."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

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


def _ensure_scheme(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    if not re.match(r"^https?://", u, re.I):
        u = "https://" + u.lstrip("/")
    return u


def detect_platform(url: str) -> Optional[str]:
    """Detect platform from host: jd / taobao / pdd."""
    u = _ensure_scheme(url)
    if not u:
        return None
    try:
        host = urlparse(u).netloc.lower()
    except Exception:
        return None
    # strip port
    host = host.split("@")[-1].split(":")[0]
    if host.endswith(".jd.com") or host == "jd.com" or host.endswith(".jd.hk") or host == "jd.hk":
        return "jd"
    if (
        host.endswith(".taobao.com")
        or host == "taobao.com"
        or host.endswith(".tmall.com")
        or host == "tmall.com"
        or host.endswith(".tmall.hk")
        or host == "tmall.hk"
        or host.endswith(".liangxinyao.com")  # some tmall brand stores
    ):
        return "taobao"
    if (
        host.endswith(".pinduoduo.com")
        or host == "pinduoduo.com"
        or host.endswith(".yangkeduo.com")
        or host == "yangkeduo.com"
    ):
        return "pdd"
    return None


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


def normalize_url(url: str, *, require_known_platform: bool = False) -> NormalizedURL:
    """
    Expand/clean URL → extract sku_id → store canonical_url.

    Returns {platform, sku_id, canonical_url} (+ original_url).
    Raises UnknownPlatformError if require_known_platform and host unknown.
    """
    original = (url or "").strip()
    ensured = _ensure_scheme(original)
    platform = detect_platform(ensured)
    if require_known_platform and platform is None:
        raise UnknownPlatformError(
            "无法识别平台，请使用京东 / 淘宝(天猫) / 拼多多商品链接"
        )

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
