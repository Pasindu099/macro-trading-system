"""APScheduler setup for background ingestion.

Three kinds of jobs run:

1. SCHEDULED RUNS (3x/day)
   - 00:00 UTC — catches Asian session (JP, AU, NZ)
   - 09:00 UTC — catches European session (EU, UK, CH)
   - 14:00 UTC — catches Americas session (US, CA)
   Each run fetches all tracked countries with a 45-day lookback.

2. POST-RELEASE TRIGGERS
   - Fire ~15 minutes after major releases (NFP, CPI, etc.)
   - Fetch only the affected country with a configurable lookback
     (default 7 days)
   - Driven by config/release_schedule.yaml

3. HOURLY BACKLOG CATCH-UP (optional — disabled by default)
   - Not used in Phase 1.

Usage:
    from app.ingestion.scheduler import Scheduler

    scheduler = Scheduler()
    await scheduler.start()
    # ... application runs ...
    await scheduler.shutdown()

When the FastAPI app starts (via lifespan), we call start(). On shutdown
we call shutdown().
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.db.session import session_scope
from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.eodhd_client import ALLOWED_COUNTRIES, EODHDClient, EODHDError
from app.ingestion.ingest_service import IngestService
from app.ingestion.run_logger import run_logger
from app.services.meeting_calendar import SUPPORTED_BANKS
from app.services.rate_fetchers import fetch_all, should_fetch_on_startup
from app.services.rate_probability import save_snapshot

logger = logging.getLogger(__name__)

RELEASE_SCHEDULE_PATH = Path("config/release_schedule.yaml")

# Scheduled run lookback — wide enough to catch any monthly indicator
# even on an off-timing release day (UK labor releases mid-month).
SCHEDULED_LOOKBACK_DAYS = 45

# Post-release trigger lookback — narrow, we just want the fresh print.
POST_RELEASE_LOOKBACK_DAYS = 7

# Forward fetch window so the calendar can show upcoming releases.
CALENDAR_FORWARD_DAYS = 14

# UTC times for the 3 daily runs.
SCHEDULED_SESSIONS: dict[str, str] = {
    "scheduled_asia":   "00:00",
    "scheduled_london": "09:00",
    "scheduled_ny":     "14:00",
}


class Scheduler:
    """Wraps APScheduler with our ingestion jobs.

    Call start() from FastAPI lifespan startup, shutdown() from lifespan
    shutdown. Thread-safe (APScheduler manages its own locks).
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")
        self._canonicalizer: Canonicalizer | None = None
        self._ingest_service: IngestService | None = None

    async def start(self) -> None:
        """Load config, register jobs, and start the scheduler."""
        logger.info("Initializing scheduler…")

        # Build canonicalizer + service once, reuse across jobs
        self._canonicalizer = Canonicalizer.from_default_config()
        self._ingest_service = IngestService(self._canonicalizer)

        # 1. Register the 3 daily runs
        for session_name, hhmm in SCHEDULED_SESSIONS.items():
            hour, minute = (int(p) for p in hhmm.split(":"))
            self._scheduler.add_job(
                self._run_scheduled_session,
                trigger=CronTrigger(hour=hour, minute=minute, timezone="UTC"),
                args=[session_name],
                id=session_name,
                name=f"Daily run: {session_name}",
                replace_existing=True,
                # Misfire grace: if the app was down when the job should have
                # fired, run it on startup instead of skipping.
                misfire_grace_time=60 * 60,
            )
            logger.info("  Registered %s at %s UTC", session_name, hhmm)

        # 2. Register post-release triggers from YAML
        triggers_added = self._register_post_release_triggers()
        logger.info("  Registered %d post-release triggers", triggers_added)

        self._scheduler.add_job(
            self._run_rate_probability_fetch,
            trigger=CronTrigger(hour=6, minute=0, timezone="UTC"),
            id="rate_probability_ois_fetch",
            name="Daily rate probability OIS/futures fetch",
            replace_existing=True,
            misfire_grace_time=60 * 60,
        )
        logger.info("  Registered rate probability OIS/futures fetch at 06:00 UTC")

        self._scheduler.start()
        await self._run_rate_probability_fetch_if_empty()
        logger.info("Scheduler started.")

    async def shutdown(self) -> None:
        """Stop the scheduler gracefully, letting in-flight jobs finish."""
        logger.info("Shutting down scheduler…")
        self._scheduler.shutdown(wait=True)
        logger.info("Scheduler stopped.")

    # ── Job implementations ─────────────────────────────────────────

    async def _run_scheduled_session(self, session_name: str) -> None:
        """A daily scheduled run — fetches all tracked countries with wide lookback."""
        countries = sorted(ALLOWED_COUNTRIES)
        today = date.today()
        to_date = today + timedelta(days=CALENDAR_FORWARD_DAYS)
        from_date = today - timedelta(days=SCHEDULED_LOOKBACK_DAYS)

        async with run_logger(session_name, countries=countries) as run:
            assert self._ingest_service is not None
            async with EODHDClient() as client:
                for country in countries:
                    try:
                        events = await client.fetch_economic_events(
                            country=country,
                            from_date=from_date,
                            to_date=to_date,
                        )
                        run.record_api_call(1)
                    except EODHDError as exc:
                        logger.error(
                            "Fetch failed for %s: %s (continuing)", country, exc,
                        )
                        run.errors.append(f"{country}: {exc}")
                        continue

                    async with session_scope() as session:
                        stats = await self._ingest_service.ingest_events(
                            session, events,
                        )
                    run.record_stats(stats)
                    logger.info(
                        "  %s: fetched=%d inserted=%d updated=%d unmapped=%d",
                        country, len(events), stats.inserted, stats.updated,
                        stats.unmapped,
                    )

    async def _run_post_release(self, trigger_config: dict[str, Any]) -> None:
        """A single post-release trigger — one country, short lookback."""
        country = trigger_config["country"]
        trigger_name = trigger_config["name"]
        lookback_days = _resolve_post_release_lookback_days(trigger_config)
        today = date.today()
        to_date = today + timedelta(days=CALENDAR_FORWARD_DAYS)
        from_date = today - timedelta(days=lookback_days)

        async with run_logger("post_release", countries=[country]) as run:
            assert self._ingest_service is not None
            async with EODHDClient() as client:
                try:
                    events = await client.fetch_economic_events(
                        country=country, from_date=from_date, to_date=to_date,
                    )
                    run.record_api_call(1)
                except EODHDError as exc:
                    logger.error("Post-release fetch failed (%s): %s",
                                 trigger_name, exc)
                    run.errors.append(f"{trigger_name}: {exc}")
                    return

                async with session_scope() as session:
                    stats = await self._ingest_service.ingest_events(
                        session, events,
                    )
                run.record_stats(stats)
                logger.info(
                    "Post-release %s: fetched=%d inserted=%d updated=%d",
                    trigger_name, len(events), stats.inserted, stats.updated,
                )

    async def _run_rate_probability_fetch_if_empty(self) -> None:
        async with session_scope() as session:
            if not await should_fetch_on_startup(session):
                return
        await self._run_rate_probability_fetch()

    async def _run_rate_probability_fetch(self) -> None:
        async with session_scope() as session:
            statuses = await fetch_all(session)
            for bank in SUPPORTED_BANKS:
                await save_snapshot(bank, session)
        logger.info("Rate probability fetch statuses: %s", statuses)

    # ── Config loading ──────────────────────────────────────────────

    def _register_post_release_triggers(self) -> int:
        """Parse release_schedule.yaml and add APScheduler jobs."""
        if not RELEASE_SCHEDULE_PATH.exists():
            logger.warning(
                "No %s found — post-release triggers disabled.",
                RELEASE_SCHEDULE_PATH,
            )
            return 0

        with RELEASE_SCHEDULE_PATH.open(encoding="utf-8") as f:
            data = yaml.safe_load(f)

        entries = data.get("release_schedule") or []
        count = 0
        for entry in entries:
            try:
                trigger = _build_cron_trigger(entry)
                _resolve_post_release_lookback_days(entry)
            except ValueError as exc:
                logger.warning(
                    "Skipping invalid release_schedule entry %r: %s",
                    entry.get("name"), exc,
                )
                continue

            job_id = f"post_release:{entry['name']}"
            self._scheduler.add_job(
                self._run_post_release,
                trigger=trigger,
                args=[entry],
                id=job_id,
                name=f"Post-release: {entry['name']}",
                replace_existing=True,
                misfire_grace_time=60 * 60,
            )
            count += 1
        return count


# ══════════════════════════════════════════════════════════════════════
#  Helpers
# ══════════════════════════════════════════════════════════════════════

def _build_cron_trigger(entry: dict[str, Any]) -> CronTrigger:
    """Convert one release_schedule.yaml entry into a CronTrigger.

    The trigger fires `trigger_delay_minutes` AFTER the scheduled release
    time, so we catch freshly published data. Default delay is 15 minutes.
    """
    name = entry.get("name") or "<unnamed>"
    scheduled_str = entry.get("scheduled_at_utc")
    if not scheduled_str:
        raise ValueError("scheduled_at_utc is required")

    try:
        hour, minute = (int(p) for p in scheduled_str.split(":"))
    except ValueError as exc:
        raise ValueError(
            f"scheduled_at_utc must be HH:MM (got {scheduled_str!r})"
        ) from exc

    delay = int(entry.get("trigger_delay_minutes", 15))
    fire_dt = _add_minutes(hour, minute, delay)

    cron_kwargs: dict[str, Any] = {
        "hour": fire_dt[0],
        "minute": fire_dt[1],
        "timezone": "UTC",
    }

    dow = entry.get("day_of_week")
    if dow is not None:
        cron_kwargs["day_of_week"] = dow

    dom = entry.get("day_of_month")
    dom_range = entry.get("day_of_month_range")
    if dom is not None and dom_range is not None:
        raise ValueError(f"{name}: use day_of_month OR day_of_month_range, not both")
    if dom is not None:
        cron_kwargs["day"] = dom
    if dom_range is not None:
        cron_kwargs["day"] = dom_range

    return CronTrigger(**cron_kwargs)


def _resolve_post_release_lookback_days(entry: dict[str, Any]) -> int:
    """Return a validated lookback window for a post-release trigger."""
    raw_value = entry.get("lookback_days", POST_RELEASE_LOOKBACK_DAYS)
    try:
        lookback_days = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"lookback_days must be a positive integer (got {raw_value!r})"
        ) from exc

    if lookback_days <= 0:
        raise ValueError(f"lookback_days must be >= 1 (got {lookback_days})")
    return lookback_days


def _add_minutes(hour: int, minute: int, delta: int) -> tuple[int, int]:
    """Add `delta` minutes to (hour, minute), wrapping around 24h."""
    total = (hour * 60 + minute + delta) % (24 * 60)
    return divmod(total, 60)
