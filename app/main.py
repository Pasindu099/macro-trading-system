"""FastAPI application entry point.

Run with:
    uvicorn app.main:app --reload

Or programmatically:
    python -m app.main

When the app starts, the scheduler spins up in the background and starts
running 3x daily ingestion jobs + post-release triggers. Shutdown stops
the scheduler gracefully.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import admin, auth, pages, public, rate_probability, rate_probability_scraped, research
from app.auth import AuthMiddleware, ensure_auth_schema
from app.db.session import dispose_engine, session_scope
from app.ingestion.scheduler import Scheduler
from app.logging_config import configure_logging
from app.services.correlation_service import ensure_correlation_schema
from app.services.meeting_calendar import seed_meetings_from_yaml
from app.settings import get_settings

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  Lifespan — starts scheduler on boot, stops on shutdown
# ══════════════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """FastAPI lifespan: startup + shutdown hooks.

    On startup:
      - Initialize scheduler and register jobs.
    On shutdown:
      - Stop scheduler cleanly (wait for in-flight jobs).
      - Dispose database engine (close connection pool).
    """
    settings = get_settings()
    logger.info("Starting Macro Dashboard app…")
    logger.info("  log_level=%s", settings.log_level)
    if settings.auth_enabled:
        await ensure_auth_schema()
    await ensure_correlation_schema()
    async with session_scope() as session:
        await seed_meetings_from_yaml(session)

    # Scheduler — store on app state so routes can access if needed.
    scheduler: Scheduler | None = None
    if settings.enable_scheduler:
        scheduler = Scheduler()
        await scheduler.start()
        app.state.scheduler = scheduler
    else:
        logger.info("Scheduler disabled via ENABLE_SCHEDULER=false")

    logger.info("App startup complete.")

    try:
        yield
    finally:
        logger.info("Shutting down Macro Dashboard app…")
        if scheduler is not None:
            await scheduler.shutdown()
        await dispose_engine()
        logger.info("App shutdown complete.")


# ══════════════════════════════════════════════════════════════════════
#  App
# ══════════════════════════════════════════════════════════════════════

# Configure logging BEFORE constructing the app so any startup logs emit
# in our preferred format.
configure_logging()

app = FastAPI(
    title="Macro Dashboard",
    version="0.1.0",
    description="Macro economic data for FX trading — Phase 1",
    lifespan=lifespan,
)
app.add_middleware(AuthMiddleware)

# Mount routers and static assets
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.include_router(auth.router)
app.include_router(pages.router)
app.include_router(admin.router)
app.include_router(public.router)
app.include_router(research.router)
app.include_router(rate_probability.router)
app.include_router(rate_probability_scraped.router)


# ══════════════════════════════════════════════════════════════════════
#  Root routes
# ══════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 OK if the process is up."""
    return {"status": "ok"}


# ══════════════════════════════════════════════════════════════════════
#  CLI entry point
# ══════════════════════════════════════════════════════════════════════

def main() -> None:
    """Run via `python -m app.main`.

    For dev use only. In production we'd deploy behind gunicorn + uvicorn workers.
    """
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=False,  # reload=True breaks the scheduler across reloads
        log_config=None,  # Use our configured logging, not uvicorn's defaults
    )


if __name__ == "__main__":
    main()
