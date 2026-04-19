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
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn
from fastapi import FastAPI

from app.api.routes import admin
from app.db.session import dispose_engine
from app.ingestion.scheduler import Scheduler
from app.logging_config import configure_logging
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

# Mount API routers
app.include_router(admin.router)


# ══════════════════════════════════════════════════════════════════════
#  Root routes
# ══════════════════════════════════════════════════════════════════════

@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe. Returns 200 OK if the process is up."""
    return {"status": "ok"}


@app.get("/")
async def root() -> dict[str, str]:
    """Root page — redirect suggestion. Real UI comes in Step 5."""
    return {
        "name": "Macro Dashboard API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }


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