"""Pure extract helpers for Playwright-captured HTML/JSON (unit-testable)."""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from app.price_sanity import is_login_wall_text, min_plausible_price


def _min_p() -> float:
    try:
        from app.config import get_config

        return min_plausible_price(getattr(get_config().fetch, "minPlausiblePrice", 10))
    except Exception:
        return 10.0


def _accept_price(val: Optional[float]) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    if f <= 0:
        return None
    if f < _min_p():
        return None
    return f


def extract_price_from_ware_json(payload: Any) -> dict[str, Optional[float | str]]:
    """
    Extract list_price / tax_amount / title from wareBusiness-like JSON.

    Accepts dict or JSON string. Prefer priceInfo.pPrice + taxFee.
    """
    data = payload
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return {}
    if not isinstance(data, dict):
        return {}

    out: dict[str, Optional[float | str]] = {}
    price_info = data.get("priceInfo") if isinstance(data.get("priceInfo"), dict) else {}
    price = data.get("price") if isinstance(data.get("price"), dict) else {}
    ware = data.get("wareInfo") if isinstance(data.get("wareInfo"), dict) else {}

    def _f(*keys_sources):
        for src, key in keys_sources:
            if not src:
                continue
            v = src.get(key)
            if v is None or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None

    list_price = _f(
        (price_info, "pPrice"),
        (price_info, "jdPrice"),
        (price, "p"),
        (data, "pPrice"),
        (data, "jdPrice"),
    )
    tax = _f((price_info, "taxFee"), (data, "taxFee"))
    allin = _f(
        (price_info, "plusTaxPrice"),
        (data, "plusTaxPrice"),
    )

    list_price = _accept_price(list_price)
    allin = _accept_price(allin)

    if list_price is not None and list_price > 0:
        out["list_price"] = list_price
        out["tax_amount"] = float(tax or 0)
    elif allin is not None and allin > 0:
        # All-in only: store as list, tax 0 to avoid double tax
        out["list_price"] = allin
        out["tax_amount"] = 0.0
        out["note"] = "plusTaxPrice/all-in"

    name = ware.get("name") or data.get("name")
    if isinstance(name, str) and name.strip():
        out["title"] = name.strip()[:200]
    return out


def extract_price_from_dom_text(text: str) -> dict[str, Optional[float]]:
    """Best-effort from visible page text (商品价 / 税费 / 到手).

    Login walls (登录查看价格 / ¥???) → no price.
    """
    if not text:
        return {}
    if is_login_wall_text(text):
        return {}
    out: dict[str, Optional[float]] = {}
    m = re.search(
        r"(?:商品价|京东价|到手价|预估到手|含税价)[：:\s]*[￥¥]?\s*(\d+(?:\.\d+)?)",
        text,
    )
    tax_m = re.search(r"(?:预估税费|税费|进口税)[：:\s]*[￥¥]?\s*(\d+(?:\.\d+)?)", text)
    if m:
        lp = _accept_price(float(m.group(1)))
        if lp is not None:
            out["list_price"] = lp
    if tax_m:
        out["tax_amount"] = float(tax_m.group(1))
    return out


def merge_playwright_hits(
    *parts: dict,
) -> dict[str, Optional[float | str]]:
    """Prefer first non-empty list_price; fill tax/title from any.

    Drops login_wall / below-minPlausible junk.
    """
    merged: dict[str, Optional[float | str]] = {}
    for p in parts:
        if not p:
            continue
        lp = p.get("list_price")
        if lp is not None:
            lp = _accept_price(float(lp) if lp is not None else None)
        if merged.get("list_price") is None and lp is not None:
            merged["list_price"] = lp
            if p.get("tax_amount") is not None:
                merged["tax_amount"] = p.get("tax_amount")
        if merged.get("tax_amount") is None and p.get("tax_amount") is not None:
            merged["tax_amount"] = p["tax_amount"]
        if not merged.get("title") and p.get("title"):
            merged["title"] = p["title"]
        if not merged.get("note") and p.get("note"):
            merged["note"] = p["note"]
    if merged.get("list_price") is not None and merged.get("tax_amount") is None:
        merged["tax_amount"] = 0.0
    return merged
