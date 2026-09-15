"""Notification dispatchers — QQ Official (via bot) primary; OneBot optional."""

from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import get_config
from app.notifiers.qq_bot import send_qq_official_alert
from app.notifiers.qq_onebot import send_onebot_alert

logger = logging.getLogger(__name__)


async def notify_all(
    message: str,
    markdown: str | None = None,
    *,
    owner_openid: Optional[str] = None,
    alert_payload: Optional[dict[str, Any]] = None,
) -> None:
    """Send alert. Prefer per-owner QQ Official; OneBot is optional fallback."""
    cfg = get_config()
    errors: list[str] = []

    if owner_openid and cfg.qqofficial.enabled:
        try:
            await send_qq_official_alert(
                owner_openid=owner_openid,
                plain=message,
                payload=alert_payload or {},
            )
        except Exception as e:
            logger.exception("QQ Official notify failed")
            errors.append(f"qqofficial: {e}")

    if cfg.onebot.enabled:
        try:
            await send_onebot_alert(message)
        except Exception as e:
            logger.exception("OneBot notify failed")
            errors.append(f"onebot: {e}")

    # 不做微信 / 企业微信产品路径
    if errors:
        logger.warning("Notify partial failures: %s", errors)


async def notify_owner(
    owner_openid: str,
    *,
    plain: str,
    payload: dict[str, Any],
) -> None:
    await notify_all(plain, owner_openid=owner_openid, alert_payload=payload)
