import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from app.db import AsyncSessionLocal
from app.scrapers import news
from app.pipeline.scorer import score_all_firms

log = logging.getLogger(__name__)


async def _run_news():
    log.info("Scheduler: News run starting")
    async with AsyncSessionLocal() as db:
        await news.run(db)
        await score_all_firms(db)


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        _run_news,
        trigger=IntervalTrigger(minutes=30),
        id="news",
        replace_existing=True,
        misfire_grace_time=120,
    )
    return scheduler
