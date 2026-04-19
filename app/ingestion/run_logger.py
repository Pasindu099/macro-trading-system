"""Writes audit records to the ingestion_runs table.

Every scheduler job or manual ingest should create an IngestionRun row
at start and update it on completion. This gives the admin health
endpoint a clean history.

Usage:
    from app.ingestion.run_logger import RunLogger

    async with RunLogger("scheduled_ny", countries=["US", "CA"]) as run:
        # do ingest work
        stats = await service.ingest_events(...)
        run.record_stats(stats)
    # On clean exit: status=success, finished_at set.
    # On exception: status=failed, error captured.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update

from app.db.models import IngestionRun
from app.db.session import session_scope
from app.ingestion.ingest_service import IngestStats

logger = logging.getLogger(__name__)


class RunTracker:
    """Accumulates stats during an ingest run, flushed at end."""

    def __init__(self, run_id: int) -> None:
        self.run_id = run_id
        self.events_inserted = 0
        self.events_updated = 0
        self.api_calls_used = 0
        self.errors: list[str] = []

    def record_stats(self, stats: IngestStats) -> None:
        """Accumulate stats from one ingest batch."""
        self.events_inserted += stats.inserted
        self.events_updated += stats.updated
        self.errors.extend(stats.errors)

    def record_api_call(self, n: int = 1) -> None:
        self.api_calls_used += n


@asynccontextmanager
async def run_logger(
    run_type: str,
    countries: list[str] | None = None,
):
    """Context manager for tracking an ingestion run.

    Creates an IngestionRun row at entry, updates it on exit.

    Args:
        run_type: scheduled_asia, scheduled_london, scheduled_ny,
                  post_release, manual_backfill
        countries: list of country codes this run fetches (or None for all)
    """
    countries_list = countries or []

    # Open a session just for the start record. Committed immediately so
    # the run is visible to admin queries while still in progress.
    async with session_scope() as session:
        run_row = IngestionRun(
            started_at=datetime.now(timezone.utc),
            run_type=run_type,
            countries_fetched=countries_list,
            status="running",
        )
        session.add(run_row)
        await session.flush()
        run_id = run_row.id

    logger.info(
        "Ingestion run started: id=%d type=%s countries=%s",
        run_id, run_type, countries_list,
    )

    tracker = RunTracker(run_id)
    try:
        yield tracker
        # Success path
        await _finalize_run(
            run_id=run_id,
            tracker=tracker,
            status="success" if not tracker.errors else "partial",
            error_detail=None,
        )
    except Exception as exc:
        # Failure path
        logger.exception("Ingestion run %d failed: %s", run_id, exc)
        await _finalize_run(
            run_id=run_id,
            tracker=tracker,
            status="failed",
            error_detail={"type": type(exc).__name__, "message": str(exc)},
        )
        raise


async def _finalize_run(
    *,
    run_id: int,
    tracker: RunTracker,
    status: str,
    error_detail: dict[str, Any] | None,
) -> None:
    """Update the IngestionRun row with final stats and status."""
    errors_payload: dict[str, Any] | None = None
    if tracker.errors or error_detail:
        errors_payload = {}
        if tracker.errors:
            errors_payload["item_errors"] = tracker.errors[:50]  # cap to avoid bloat
            errors_payload["item_errors_total"] = len(tracker.errors)
        if error_detail:
            errors_payload["fatal"] = error_detail

    async with session_scope() as session:
        await session.execute(
            update(IngestionRun)
            .where(IngestionRun.id == run_id)
            .values(
                finished_at=datetime.now(timezone.utc),
                events_inserted=tracker.events_inserted,
                events_updated=tracker.events_updated,
                api_calls_used=tracker.api_calls_used,
                errors=errors_payload,
                status=status,
            )
        )

    logger.info(
        "Ingestion run finished: id=%d status=%s inserted=%d updated=%d "
        "api_calls=%d errors=%d",
        run_id, status, tracker.events_inserted, tracker.events_updated,
        tracker.api_calls_used, len(tracker.errors),
    )