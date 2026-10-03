"""Rebuild rates derivatives after government-yield ingestion."""

import asyncio

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.services.job_lock import job_lock
from app.services.rates_quality import flag_rates_outliers
from app.services.yield_spreads import build_yield_spreads


async def run_rates_derived() -> dict[str, int]:
    async with job_lock("rates_derived") as acquired:
        async with run_logger("job:rates_derived") as run:
            if not acquired:
                run.mark_skipped()
                return {}

            async def _transaction() -> dict[str, int]:
                async with session_scope(statement_timeout="15min") as session:
                    flags = await flag_rates_outliers(session)
                    spreads = await build_yield_spreads(session)
                return {"yield_flags": flags["government_yield_observations"],
                        "fx_flags": flags["fx_spot_observations"], "spreads": spreads}

            counts = await asyncio.wait_for(_transaction(), timeout=16 * 60)
            run.record_rows(counts["spreads"])
            return counts
