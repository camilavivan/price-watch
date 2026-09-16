"""Price plausibility checks (manual / soft-hint / scrape).

Rejects junk like 「2段」parsed as ¥2, login-wall placeholders, and
candidates far below external history lows.
"""

from __future__ import annotations

import re
from typing import Optional

# Digits glued to these product / quantity units are specs, not retail prices.
PRODUCT_UNIT_CHARS = (
    "段",
    "罐",
    "盒",
    "袋",
    "瓶",
    "件",
    "岁",
    "月",
    "抽",
    "片",
    "斤",
    "两",
    "升",
    "克",
    "个",
    "只",
    "双",
    "条",
    "包",
    "箱",
    "桶",
    "支",
    "台",
    "部",
    "辆",
)
# Latin / mixed units (case-insensitive match via regex)
PRODUCT_UNIT_LATIN = (
    "kg",
    "g",
    "ml",
    "l",
    "人份",
)

LOGIN_WALL_MARKERS = (
    "登录查看价格",
    "登陆查看价格",
    "登录后可查看",
    "登陆后可查看",
    "登录后查看价格",
    "¥???",
    "￥???",
    "¥？？？",
    "￥？？？",
    "价格登录可见",
)

# Spec digits glued to unit: 2段 / 3罐 / 900g / 1.5kg / 12人份
_UNIT_GLUED_RE = re.compile(
    r"(?<![.\d])(\d+(?:\.\d+)?)\s*(?:"
    + "|".join(re.escape(u) for u in PRODUCT_UNIT_CHARS)
    + r"|kg|g|ml|L|人份)(?!\d)",
    re.IGNORECASE,
)


def min_plausible_price(cfg_value: Optional[float] = None) -> float:
    if cfg_value is not None:
        try:
            v = float(cfg_value)
            if v >= 0:
                return v
        except (TypeError, ValueError):
            pass
    return 10.0


def is_login_wall_text(text: str) -> bool:
    if not text:
        return False
    sample = text[:12000] if len(text) > 12000 else text
    return any(m in sample for m in LOGIN_WALL_MARKERS)


def looks_like_unit_spec(text: str, value: float) -> bool:
    """True if `value` appears in text glued to a product unit (e.g. 2段)."""
    for m in _UNIT_GLUED_RE.finditer(text or ""):
        try:
            if abs(float(m.group(1)) - float(value)) < 1e-9:
                return True
        except (TypeError, ValueError):
            continue
    return False


def is_plausible_retail(
    price: Optional[float],
    *,
    min_plausible: float = 10.0,
    labeled: bool = False,
    force: bool = False,
    has_decimal: bool | None = None,
) -> bool:
    """
    Accept a candidate retail / landing price.

    - force=True → always accept (non-negative already checked elsewhere)
    - labeled (￥/元/到手…) → allow below min_plausible
    - otherwise require price >= min_plausible OR decimal places (e.g. 9.9)
    """
    if price is None:
        return False
    try:
        p = float(price)
    except (TypeError, ValueError):
        return False
    if p < 0 or p != p:  # NaN
        return False
    if force:
        return True
    if labeled:
        return p > 0
    if has_decimal is True:
        return p > 0
    # Integer / whole number without currency label
    if p < float(min_plausible):
        return False
    return True


def is_bogus_vs_history(
    price: float,
    history_lowest: Optional[float],
    *,
    fraction: float = 0.2,
) -> bool:
    """
    True if candidate is absurdly below external/local history lowest.

    Default: price < lowest * 0.2 (also covers lowest*0.3-style thresholds
    when fraction is configured higher).
    """
    if history_lowest is None:
        return False
    try:
        low = float(history_lowest)
        p = float(price)
    except (TypeError, ValueError):
        return False
    if low <= 0 or p < 0:
        return False
    return p < low * float(fraction)


def reject_manual_price_reason(
    price: float,
    *,
    min_plausible: float = 10.0,
    history_lowest: Optional[float] = None,
    history_fraction: float = 0.2,
    force: bool = False,
    source_text: str = "",
) -> Optional[str]:
    """
    Return a Chinese reason string if price should be rejected, else None.
    """
    if force:
        return None
    labeled = False
    if source_text:
        # If explicitly labeled in the same paste, allow small prices
        if re.search(
            rf"(?:￥|¥|元|到手|现价|价格|填价)\s*[：:=\s]*{re.escape(str(int(price)) if float(price) == int(price) else price)}",
            source_text,
        ) or re.search(
            rf"(?:￥|¥)\s*{re.escape(str(int(price)) if float(price) == int(price) else price)}",
            source_text,
        ):
            labeled = True
        if looks_like_unit_spec(source_text, price) and not labeled:
            disp = str(int(price)) if float(price) == int(price) else str(price)
            return f"未采用「{disp}」疑似规格（如2段），请发真实到手价"

    has_dec = abs(price - round(price)) > 1e-9
    if not is_plausible_retail(
        price,
        min_plausible=min_plausible,
        labeled=labeled,
        force=False,
        has_decimal=has_dec,
    ):
        disp = str(int(price)) if float(price) == int(price) else str(price)
        return (
            f"未采用「{disp}」疑似规格（如2段），请发真实到手价"
            if price < min_plausible
            else f"未采用「{disp}」，价格不合理"
        )

    if is_bogus_vs_history(price, history_lowest, fraction=history_fraction):
        disp = str(int(price)) if float(price) == int(price) else str(price)
        return (
            f"未采用「{disp}」远低于历史最低（约¥{float(history_lowest):.0f}），"
            f"疑似误识别，请发真实到手价"
        )
    return None
