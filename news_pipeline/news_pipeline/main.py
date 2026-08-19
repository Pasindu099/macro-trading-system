import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI

from news_pipeline.collectors import (
    CENTRAL_BANK_INTERVAL_SECONDS,
    FRED_INTERVAL_SECONDS,
    RSS_INTERVAL_SECONDS,
    collect_al_jazeera,
    collect_ap_news,
    collect_ecb,
    collect_federal_reserve,
    collect_fred_release_calendar,
    collect_investinglive,
    dispose_engine,
)
from news_pipeline.enrich import (
    poll_and_enrich,
    shutdown_price_snapshot_scheduler,
    start_price_snapshot_scheduler,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler(timezone="UTC")


def register_jobs() -> None:
    scheduler.add_job(
        collect_ap_news,
        "interval",
        seconds=RSS_INTERVAL_SECONDS,
        id="collect_ap_news",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        collect_al_jazeera,
        "interval",
        seconds=RSS_INTERVAL_SECONDS,
        id="collect_al_jazeera",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        collect_investinglive,
        "interval",
        seconds=RSS_INTERVAL_SECONDS,
        id="collect_investinglive",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        collect_federal_reserve,
        "interval",
        seconds=CENTRAL_BANK_INTERVAL_SECONDS,
        id="collect_federal_reserve",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        collect_ecb,
        "interval",
        seconds=CENTRAL_BANK_INTERVAL_SECONDS,
        id="collect_ecb",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        collect_fred_release_calendar,
        "interval",
        seconds=FRED_INTERVAL_SECONDS,
        id="collect_fred_release_calendar",
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        poll_and_enrich,
        "interval",
        seconds=20,
        id="poll_and_enrich",
        max_instances=1,
        coalesce=True,
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    if not scheduler.running:
        register_jobs()
        scheduler.start()
        start_price_snapshot_scheduler()
        logger.info("News pipeline scheduler started with %s jobs", len(scheduler.get_jobs()))

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        shutdown_price_snapshot_scheduler()
        await dispose_engine()
        logger.info("News pipeline scheduler stopped")


app = FastAPI(title="Macro Dashboard News Pipeline", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/jobs")
async def jobs() -> list[dict[str, str | None]]:
    return [
        {
            "id": job.id,
            "next_run_time": job.next_run_time.isoformat() if job.next_run_time else None,
        }
        for job in scheduler.get_jobs()
    ]
