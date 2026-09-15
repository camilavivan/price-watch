"""Notification dispatchers."""

from __future__ import annotations

import logging

from app.config import get_config
from app.notifiers.qq_onebot import send_onebot_alert
from app.notifiers.wecom import send_wecom_alert

logger = logging.getLogger(__name__)


async def notify_all(message: str, markdown: str | None = None) -> None:
    cfg = get_config()
    errors: list[str] = []
    if cfg.onebot.enabled:
        try:
            await send_onebot_alert(message)
        except Exception as e:
            logger.exception("OneBot notify failed")
            errors.append(f"qq: {e}")
    if cfg.wecom.enabled and (cfg.wecom.webhookUrl or "").strip():
        try:
            await send_wecom_alert(markdown or message)
        except Exception as e:
            logger.exception("WeCom notify failed")
            errors.append(f"wecom: {e}")
    if errors:
        logger.warning("Notify partial failures: %s", errors)
