"""OneBot v11 HTTP API notifier (NapCat / go-cqhttp / Lagrange)."""

from __future__ import annotations

import logging

import httpx

from app.config import get_config

logger = logging.getLogger(__name__)


async def _call(action: str, params: dict) -> None:
    cfg = get_config().onebot
    base = cfg.apiBase.rstrip("/")
    url = f"{base}/{action}"
    headers = {"Content-Type": "application/json"}
    token = (cfg.accessToken or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=params, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"OneBot {action} HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            data = resp.json()
        except Exception:
            return
        # status / retcode conventions vary slightly across implementations
        if isinstance(data, dict):
            ret = data.get("retcode", data.get("status"))
            if ret not in (0, "0", "ok", None, "async"):
                raise RuntimeError(f"OneBot {action} error: {data}")


async def send_onebot_alert(message: str) -> None:
    cfg = get_config().onebot
    if not cfg.enabled:
        return
    groups = cfg.notifyGroups or []
    users = cfg.notifyUsers or []
    if not groups and not users:
        logger.warning("OneBot enabled but no notifyGroups/notifyUsers configured")
        return
    for gid in groups:
        await _call("send_group_msg", {"group_id": int(gid), "message": message})
        logger.info("OneBot group notify -> %s", gid)
    for uid in users:
        await _call("send_private_msg", {"user_id": int(uid), "message": message})
        logger.info("OneBot private notify -> %s", uid)
