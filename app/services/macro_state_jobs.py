"""Scheduled Macro State chain, preserving each builder's transaction boundary."""

from __future__ import annotations

import asyncio
import logging
from time import monotonic
from typing import Awaitable, Callable

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.processing.cb_preferred_score import build_cb_preferred_score
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
    """Rebuild in dependency order; any failure stops dependent work."""
    async with job_lock("macro_state") as acquired:
        if not acquired:
            async with run_logger("job:macro_state:chain") as run:
                run.mark_skipped()
            return

        for name, builder, output_table in STEPS:
            started = monotonic()
            try:
                async with run_logger(f"job:macro_state:{name}") as run:
                    await asyncio.wait_for(
                        builder(export=False, statement_timeout="15min"),
                        timeout=16 * 60,
                    )
                    async with session_scope(statement_timeout="15min") as session:
                        count = (await session.execute(
                            text(f"SELECT count(*) FROM {output_table}")
                        )).scalar_one()
                    run.record_rows(count)
            finally:
                duration = monotonic() - started
                logger.info("Macro State %s finished after %.2fs", name, duration)
                if duration > 10:
                    logger.warning("Macro State %s held its build lock for %.2fs", name, duration)
