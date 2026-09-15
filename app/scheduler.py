"""APScheduler background price checks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_config
from app.db import get_session_factory
from app.services import check_due_products

logger = logging.getLogger(__name__)

_scheduler: AsyncIOScheduler | None = None


async def _job() -> None:
    factory = get_session_factory()
    async with factory() as session:
        results = await check_due_products(session)
        if results:
            logger.info("Scheduled check finished: %d product(s)", len(results))


def start_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    cfg = get_config()
    sched = AsyncIOScheduler()
    # Tick every minute; per-product interval enforced in check_due_products
    sched.add_job(
        _job,
        "interval",
        minutes=1,
        id="price_check",
        max_instances=1,
        coalesce=True,
    )
    delay = max(cfg.scheduler.startupDelaySeconds, 1)
    sched.add_job(
        _job,
        "date",
        run_date=datetime.now() + timedelta(seconds=delay),
        id="startup_check",
    )
    sched.start()
    _scheduler = sched
    logger.info("Scheduler started (tick=1min, startup_delay=%ss)", delay)
    return sched


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler:
        _scheduler.shutdown(wait=False)
        _scheduler = None
