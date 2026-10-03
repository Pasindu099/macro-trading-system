"""Scraped rate probability JSON endpoints."""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.services.scraped_rate_probability import get_scraped_rate_probability_data
from app.settings import get_settings

router = APIRouter(tags=["rate-probability-scraped"])


@router.get("/api/rate-probability")
async def get_rate_probability(session: AsyncSession = Depends(get_session)) -> JSONResponse:
    try:
        payload = await get_scraped_rate_probability_data(session)
    except Exception:
        return JSONResponse({"error": "Data not available — run scraper first"}, status_code=503)
    return JSONResponse(payload)


@router.post("/api/rate-probability/scrape")
async def trigger_scrape(background_tasks: BackgroundTasks) -> dict[str, str]:
    if not get_settings().rateprobability_scraper_enabled:
        raise HTTPException(status_code=409, detail="rateprobability.com scraper is disabled")
    from scraper.rate_probability_scraper import run_scraper_async

    async def _run() -> None:
        statuses = await run_scraper_async()
        import logging
        logging.getLogger(__name__).info("On-demand scrape: %s", statuses)

    background_tasks.add_task(_run)
    return {"status": "scrape started"}
