"""Scheduled Macro State chain, preserving each builder's transaction boundary."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Awaitable, Callable

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.processing.cb_preferred_score import CB_WATCHLIST, build_cb_preferred_score
from app.processing.currency_stance import build_currency_stance_layer
from app.processing.macro_dataset import build_processed_dataset
from app.processing.macro_features import build_feature_layer
from app.processing.macro_indices import build_macro_indices
from app.services.job_lock import job_lock

logger = logging.getLogger(__name__)

Step = tuple[str, Callable[..., Awaitable[object]], str]
STEPS: tuple[Step, ...] = (
    ("processed_dataset", build_processed_dataset, "processed.macro_observations"),
    ("feature_layer", build_feature_layer, "processed.indicator_features"),
    ("cb_preferred_score", build_cb_preferred_score, "processed.cb_preferred_score"),
    ("macro_indices", build_macro_indices, "processed.theme_indices"),
    ("currency_stance", build_currency_stance_layer, "processed.currency_stance"),
)


async def run_macro_state_chain() -> None:
    """Rebuild in one transaction; a failed invariant leaves prior tables intact."""
    async with job_lock("macro_state") as acquired:
        if not acquired:
            async with run_logger("job:macro_state:chain") as run:
                run.mark_skipped()
            return

        async with run_logger("job:macro_state:chain"):
            async with session_scope(statement_timeout="15min") as session:
                for name, builder, output_table in STEPS:
                    started = monotonic()
                    try:
                        async with run_logger(f"job:macro_state:{name}") as run:
                            await asyncio.wait_for(
                                builder(export=False, statement_timeout="15min",
                                        shared_session=session),
                                timeout=16 * 60,
                            )
                            count = (await session.execute(
                                text(f"SELECT count(*) FROM {output_table}")
                            )).scalar_one()
                            run.record_rows(count)
                    finally:
                        duration = monotonic() - started
                        logger.info("Macro State %s finished after %.2fs", name, duration)
                        if duration > 10:
                            logger.warning("Macro State %s held its build lock for %.2fs", name, duration)
                await validate_macro_state(session)


async def validate_macro_state(session) -> None:
    """Reject invalid scores or dates before the chain transaction commits."""
    for table, date_column, values in (
        ("processed.cb_preferred_score", "date",
         ("inflation_score", "labor_score", "growth_score", "cb_strength_score")),
        ("processed.theme_indices", "index_date",
         ("inflation_score", "labor_score", "growth_score")),
        ("processed.currency_stance", "date",
         ("inflation_score", "labor_score", "growth_score", "overall_stance_score")),
    ):
        checks = " OR ".join(f"abs({column}) > 5" for column in values)
        bad = (await session.execute(text(
            f"SELECT count(*) FROM {table} WHERE {date_column} > CURRENT_DATE OR {checks}"
        ))).scalar_one()
        if bad:
            raise RuntimeError(f"Macro State invariant failed: {table} has {bad} future or out-of-range rows")

    for table, release_column in (
        ("processed.macro_observations", "release_timestamp_utc"),
        ("processed.indicator_features", "release_timestamp_utc"),
    ):
        actual_check = "actual_value IS NULL OR " if table.endswith("macro_observations") else ""
        bad = (await session.execute(text(
            f"SELECT count(*) FROM {table} WHERE {actual_check}{release_column} > CURRENT_TIMESTAMP"
        ))).scalar_one()
        if bad:
            raise RuntimeError(f"Macro State invariant failed: {table} has {bad} future or null-actual rows")

    latest = (await session.execute(text("""
        SELECT DISTINCT ON (country_code) country_code, total_contributors
        FROM processed.cb_preferred_score
        ORDER BY country_code, date DESC
    """))).all()
    coverage = {country: contributors for country, contributors in latest}
    for country, indicators in CB_WATCHLIST.items():
        required = (len(indicators) + 1) // 2
        observed = coverage.get(country, 0)
        if observed < required:
            raise RuntimeError(
                f"Macro State invariant failed: {country} has {observed}/{len(indicators)} "
                f"scorable indicators (need {required})"
            )
