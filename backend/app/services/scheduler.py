"""APScheduler that drives daily ETL jobs (currently: ADO MD snapshot)."""
from __future__ import annotations

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.services.ado_md_service import trigger_etl

logger = structlog.get_logger()

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="UTC")
    return _scheduler


async def _md_etl_job() -> None:
    try:
        result = await trigger_etl()
        logger.info("scheduled_md_etl_done", **result)
    except Exception as e:  # noqa: BLE001
        logger.exception("scheduled_md_etl_failed", err=str(e))


def start_scheduler() -> None:
    sched = get_scheduler()
    if sched.running:
        return
    # Daily at 06:00 UTC — matches the architecture doc.
    sched.add_job(
        _md_etl_job,
        CronTrigger(hour=6, minute=0),
        id="ado_md_etl_daily",
        replace_existing=True,
        misfire_grace_time=3600,
    )
    sched.start()
    logger.info("scheduler_started", jobs=[j.id for j in sched.get_jobs()])


def shutdown_scheduler() -> None:
    sched = get_scheduler()
    if sched.running:
        sched.shutdown(wait=False)
