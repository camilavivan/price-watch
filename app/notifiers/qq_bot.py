"""Push alerts to the Node QQ Official bot over the internal Docker network."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import get_config

logger = logging.getLogger(__name__)


async def send_qq_official_alert(
    *,
    owner_openid: str,
    plain: str,
    payload: dict[str, Any],
) -> None:
    cfg = get_config()
    url = (cfg.qqofficial.notifyUrl or "").strip()
    if not url:
        logger.warning("qqofficial.notifyUrl empty; skip QQ alert")
        return

    body = {
        "openid": owner_openid,
        "text": plain,
        **payload,
    }
    headers = {"Content-Type": "application/json"}
    token = (cfg.adminToken or "").strip()
    if token:
        headers["X-Admin-Token"] = token

    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=body, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"bot notify HTTP {resp.status_code}: {resp.text[:300]}"
            )
        logger.info("QQ Official alert queued for %s", owner_openid)
