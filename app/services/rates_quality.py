"""Flag transient extreme spikes while preserving provider observations."""

import asyncio
from collections import defaultdict
from statistics import stdev

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.services.job_lock import job_lock


def reversing_spike_ids(points: list[tuple[int, float]]) -> list[int]:
    """Return IDs whose two surrounding changes reverse beyond six prior sigmas."""
    flagged = []
    for index in range(251, len(points) - 1):
        prior = [points[j][1] - points[j - 1][1] for j in range(index - 250, index)]
        sigma = stdev(prior)
        if sigma == 0:
            continue
        change = points[index][1] - points[index - 1][1]
        reverse = points[index + 1][1] - points[index][1]
        if abs(change) > 6 * sigma and abs(reverse) > 6 * sigma and change * reverse < 0:
            flagged.append(points[index][0])
    return flagged


async def flag_rates_outliers(session: AsyncSession) -> dict[str, int]:
    counts = {}
    for table, date_column, value_column, source_filter in (
        ("government_yield_observations", "market_observation_date", "yield_value", ""),
        ("fx_spot_observations", "observation_date", "close_value", "AND source_type <> 'synthetic'"),
    ):
        result = await session.execute(text(f"""
            SELECT DISTINCT ON (provider_symbol, {date_column})
                id, provider_symbol, {value_column}::float AS value
            FROM {table}
            WHERE quality_status = 'valid' {source_filter}
            ORDER BY provider_symbol, {date_column}, ingested_at DESC, id DESC
        """))
        by_symbol = defaultdict(list)
        for row in result:
            by_symbol[row.provider_symbol].append((row.id, row.value))
        flagged = [row_id for points in by_symbol.values() for row_id in reversing_spike_ids(points)]
        newly_flagged = 0
        if flagged:
            update = await session.execute(
                text(f"UPDATE {table} SET is_outlier = true WHERE id = ANY(:ids) AND NOT is_outlier"),
                {"ids": flagged},
            )
            newly_flagged = update.rowcount
        counts[table] = newly_flagged
    return counts


async def run_rates_outlier_job() -> dict[str, int]:
    async with job_lock("rates_derived") as acquired:
        async with run_logger("job:rates_outliers") as run:
            if not acquired:
                run.mark_skipped()
                return {}

            async def _transaction() -> dict[str, int]:
                async with session_scope(statement_timeout="15min") as session:
                    return await flag_rates_outliers(session)

            counts = await asyncio.wait_for(_transaction(), timeout=16 * 60)
            run.record_rows(sum(counts.values()))
            return counts
