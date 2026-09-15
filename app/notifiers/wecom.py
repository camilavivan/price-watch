"""企业微信 — 已不做产品路径，保留空壳避免旧 import 报错。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def send_wecom_alert(content: str) -> None:
    logger.debug("WeCom disabled (不做微信/企微产品路径); ignored")
