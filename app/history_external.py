"""External price-history providers (慢慢买 best-effort).

Patterns adapted from public open-source references (ideas only, not copied
verbatim): PPsteven/manmanbuy_js_crack, zhangbincheng1997/mall-monitor,
hamflx gist (京东历史价格插件).

Unofficial third-party endpoint — may return 402 / 403 / captcha; soft-fail only.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import quote

from app.config import get_config

logger = logging.getLogger(__name__)

# Publicly documented client secret used by HistoryLowest.aspx JS
_MMB_SECRET = "c5c3f201a8e8fc634d37a766a0299218"
_TICKET_PAGE = "https://tool.manmanbuy.com/HistoryLowest.aspx"
_API_URL = "https://tool.manmanbuy.com/api.ashx"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

_TICKET_RE = re.compile(
    r'id=["\']ticket["\'][^>]*value=["\']([^"\']+)["\']'
    r'|value=["\']([^"\']+)["\'][^>]*id=["\']ticket["\']',
    re.I,
)


@dataclass
class HistoryPoint:
    ts: int  # unix ms
    price: float


@dataclass
class ExternalHistorySeries:
    source: str  # "manmanbuy"
    points: list[HistoryPoint] = field(default_factory=list)
    lowest: Optional[float] = None
    highest: Optional[float] = None
    avg: Optional[float] = None
    count: int = 0

    def prices(self) -> list[float]:
        return [p.price for p in self.points]


# ---- pure helpers (unit-testable) ----


def extract_ticket_from_html(html: str) -> Optional[str]:
    """Parse ticket hidden input from HistoryLowest.aspx HTML."""
    if not html:
        return None
    m = _TICKET_RE.search(html)
    if not m:
        # Fallback: simpler pattern used in public scripts
        m2 = re.search(r'id="ticket"\s+value="([^"]+)"', html, re.I)
        if not m2:
            m2 = re.search(r'id="ticket".+?value="([^"]+)"', html, re.I)
        return m2.group(1) if m2 else None
    return m.group(1) or m.group(2)


def ticket_to_basic_auth(ticket: str) -> str:
    """Rotate last 4 chars to front → Authorization: BasicAuth …"""
    t = (ticket or "").strip()
    if len(t) > 4:
        t = t[-4:] + t[:-4]
    return f"BasicAuth {t}"


def build_history_token(
    *,
    method: str,
    key: str,
    t: str | int,
    secret: str = _MMB_SECRET,
) -> str:
    """
    MD5 token scheme from public scripts:

    secret + encodeURIComponent(k)+encodeURIComponent(v) for sorted keys
    (non-empty values) + secret → upper → md5 → upper.
    """
    params = {
        "method": str(method),
        "key": str(key),
        "t": str(t),
    }
    buf = secret
    for k in sorted(params.keys()):
        v = params[k]
        if v is None or v == "":
            continue
        buf += quote(k, safe="") + quote(v, safe="")
    buf += secret
    digest = hashlib.md5(buf.upper().encode("utf-8")).hexdigest().upper()
    return digest


def parse_date_price(raw: Any) -> list[HistoryPoint]:
    """
    Parse manmanbuy ``datePrice`` into [{ts, price}, …].

    Accepts:
      - list of [ts, price] / [ts, price, extra]
      - JSON string of that list
      - comma-joined body that needs wrapping with ``[]`` (hamflx style)
    """
    if raw is None or raw == "":
        return []
    data = raw
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return []
        try:
            data = json.loads(s)
        except json.JSONDecodeError:
            try:
                data = json.loads(f"[{s}]")
            except json.JSONDecodeError:
                logger.debug("datePrice JSON parse failed (len=%s)", len(s))
                return []
    if not isinstance(data, list):
        return []

    out: list[HistoryPoint] = []
    for item in data:
        ts: Optional[int] = None
        price: Optional[float] = None
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                ts = int(float(item[0]))
                price = float(item[1])
            except (TypeError, ValueError):
                continue
        elif isinstance(item, dict):
            try:
                ts_raw = item.get("ts") or item.get("date") or item.get("time")
                pr_raw = item.get("price") or item.get("p")
                if ts_raw is None or pr_raw is None:
                    continue
                ts = int(float(ts_raw))
                price = float(pr_raw)
            except (TypeError, ValueError):
                continue
        else:
            continue
        if ts is None or price is None:
            continue
        if price < 0:
            continue
        # Normalise seconds → ms if needed
        if ts < 10_000_000_000:
            ts *= 1000
        out.append(HistoryPoint(ts=ts, price=round(price, 2)))

    out.sort(key=lambda p: p.ts)
    return out


def series_from_points(
    points: list[HistoryPoint], *, source: str = "manmanbuy"
) -> Optional[ExternalHistorySeries]:
    if not points:
        return None
    prices = [p.price for p in points]
    return ExternalHistorySeries(
        source=source,
        points=points,
        lowest=min(prices),
        highest=max(prices),
        avg=round(sum(prices) / len(prices), 2),
        count=len(prices),
    )


# ---- rate limit + cache ----

_lock = threading.Lock()
_last_call_mono: float = 0.0
_cache: dict[str, tuple[float, ExternalHistorySeries]] = {}
_ticket_cache: tuple[float, str] | None = None


def _cache_key(url: str) -> str:
    return (url or "").strip().lower()


def clear_caches() -> None:
    """Test helper."""
    global _last_call_mono, _ticket_cache
    with _lock:
        _cache.clear()
        _ticket_cache = None
        _last_call_mono = 0.0


def _history_cfg():
    cfg = get_config()
    return getattr(cfg, "history", None)


def external_history_enabled() -> bool:
    h = _history_cfg()
    if h is None:
        return True
    if not getattr(h, "enabled", True):
        return False
    ext = (getattr(h, "external", "manmanbuy") or "none").strip().lower()
    return ext == "manmanbuy"


def soft_price_hint_enabled(platform: str) -> bool:
    h = _history_cfg()
    if h is None:
        return platform in ("jd", "taobao")
    if not getattr(h, "softPriceHint", True):
        return False
    plats = getattr(h, "softPriceHintPlatforms", None) or ["jd", "taobao"]
    return platform in plats


async def _rate_limit_wait() -> None:
    global _last_call_mono
    h = _history_cfg()
    gap = float(getattr(h, "rateLimitSeconds", 2.0) or 2.0) if h else 2.0
    with _lock:
        now = time.monotonic()
        wait = max(0.0, gap - (now - _last_call_mono))
        if wait == 0.0:
            _last_call_mono = now
    if wait > 0:
        import asyncio

        await asyncio.sleep(wait)
        with _lock:
            _last_call_mono = time.monotonic()


async def _fetch_ticket(client) -> Optional[str]:
    """GET HistoryLowest.aspx and extract ticket (cached briefly)."""
    global _ticket_cache
    now = time.time()
    with _lock:
        if _ticket_cache and now - _ticket_cache[0] < 300:
            return _ticket_cache[1]

    try:
        resp = await client.get(
            _TICKET_PAGE,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
        )
        if resp.status_code != 200:
            logger.warning("manmanbuy ticket page HTTP %s", resp.status_code)
            return None
        ticket = extract_ticket_from_html(resp.text or "")
        if not ticket:
            logger.warning("manmanbuy ticket not found in HTML (captcha?)")
            return None
        with _lock:
            _ticket_cache = (now, ticket)
        return ticket
    except Exception as e:
        logger.warning("manmanbuy ticket fetch failed: %s", e)
        return None


async def fetch_manmanbuy_history(
    product_url: str,
    *,
    force: bool = False,
) -> Optional[ExternalHistorySeries]:
    """
    Fetch history trend for canonical product URL.

    Soft-fail: network / 402 / 403 / captcha / parse errors → None + log.
    Cached per URL (default 1h). Rate-limited between outbound calls.
    """
    url = (product_url or "").strip()
    if not url or not external_history_enabled():
        return None

    h = _history_cfg()
    cache_ttl = float(getattr(h, "cacheSeconds", 3600) or 3600) if h else 3600.0
    ck = _cache_key(url)
    now = time.time()
    if not force:
        with _lock:
            hit = _cache.get(ck)
            if hit and now - hit[0] < cache_ttl:
                return hit[1]

    try:
        from app.adapters.http_util import make_async_client

        await _rate_limit_wait()
        async with make_async_client(
            timeout=20.0,
            follow_redirects=True,
            headers={"User-Agent": _BROWSER_UA},
        ) as client:
            ticket = await _fetch_ticket(client)
            if not ticket:
                return None

            t_ms = str(int(time.time() * 1000))
            token = build_history_token(method="getHistoryTrend", key=url, t=t_ms)
            auth = ticket_to_basic_auth(ticket)
            form = {
                "method": "getHistoryTrend",
                "key": url,
                "t": t_ms,
                "token": token,
            }
            resp = await client.post(
                _API_URL,
                data=form,
                headers={
                    "User-Agent": _BROWSER_UA,
                    "Authorization": auth,
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": "https://tool.manmanbuy.com",
                    "Referer": f"{_TICKET_PAGE}?url={quote(url, safe='')}",
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "*/*",
                    "Accept-Language": "zh-CN,zh;q=0.9",
                },
            )
            text = (resp.text or "").strip()
            # Bare body "402"/"403" is common from datacenter IPs (blocked)
            if (
                resp.status_code in (402, 403)
                or text in ("402", "403")
                or text.strip() in ("402", "403")
            ):
                code = resp.status_code if resp.status_code in (402, 403) else text.strip()[:8]
                logger.warning(
                    "manmanbuy api blocked HTTP/body %s (datacenter/captcha) for %s",
                    code,
                    url[:80],
                )
                return None
            if resp.status_code != 200:
                logger.warning(
                    "manmanbuy api HTTP %s for %s", resp.status_code, url[:80]
                )
                return None
            if "验证" in text or "Validate" in text or "captcha" in text.lower():
                logger.warning("manmanbuy captcha/validate page for %s", url[:80])
                return None

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("manmanbuy non-JSON response (len=%s)", len(text))
                return None

            # Response shapes: {data: {datePrice: ...}} or {datePrice: ...}
            date_price = None
            if isinstance(payload, dict):
                data = payload.get("data")
                if isinstance(data, dict):
                    date_price = data.get("datePrice")
                if date_price is None:
                    date_price = payload.get("datePrice")
            points = parse_date_price(date_price)
            series = series_from_points(points, source="manmanbuy")
            if series is None:
                logger.info("manmanbuy empty datePrice for %s", url[:80])
                return None
            with _lock:
                _cache[ck] = (time.time(), series)
            return series
    except Exception as e:
        logger.warning("manmanbuy history failed for %s: %s", url[:80], e)
        return None


async def fetch_external_history(
    product_url: str,
    *,
    force: bool = False,
) -> Optional[ExternalHistorySeries]:
    """Dispatch to configured external provider."""
    if not external_history_enabled():
        return None
    return await fetch_manmanbuy_history(product_url, force=force)


async def latest_external_price(product_url: str) -> Optional[float]:
    """Latest point from external series (soft current-price hint)."""
    series = await fetch_external_history(product_url)
    if not series or not series.points:
        return None
    return series.points[-1].price
