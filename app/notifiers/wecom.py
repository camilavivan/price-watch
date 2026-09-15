"""企业微信群机器人 Webhook notifier."""

from __future__ import annotations

import logging

import httpx

from app.config import get_config

logger = logging.getLogger(__name__)


async def send_wecom_alert(content: str) -> None:
    cfg = get_config().wecom
    url = (cfg.webhookUrl or "").strip()
    if not cfg.enabled or not url:
        return
    # Prefer markdown; fall back handled by wecom if too long
    payload = {
        "msgtype": "markdown",
        "markdown": {"content": content[:4096]},
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            # try text
            payload = {"msgtype": "text", "text": {"content": content[:2048]}}
            resp = await client.post(url, json=payload)
        if resp.status_code >= 400:
            raise RuntimeError(f"WeCom webhook HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
        if isinstance(data, dict) and data.get("errcode", 0) not in (0, None):
            raise RuntimeError(f"WeCom webhook error: {data}")
        logger.info("WeCom webhook notify ok")
