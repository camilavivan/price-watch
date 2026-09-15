"""Platform price adapters."""

from __future__ import annotations

from app.adapters.base import FetchResult, PriceAdapter
from app.adapters.jd import JDAdapter
from app.adapters.pdd import PDDAdapter
from app.adapters.taobao import TaobaoAdapter

ADAPTERS: dict[str, PriceAdapter] = {
    "jd": JDAdapter(),
    "taobao": TaobaoAdapter(),
    "pdd": PDDAdapter(),
}


def get_adapter(platform: str) -> PriceAdapter:
    key = (platform or "").lower().strip()
    if key not in ADAPTERS:
        raise ValueError(f"未知平台: {platform}")
    return ADAPTERS[key]


__all__ = ["ADAPTERS", "FetchResult", "PriceAdapter", "get_adapter"]
