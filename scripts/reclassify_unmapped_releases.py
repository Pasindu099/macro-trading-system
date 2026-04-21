"""Attach stored unmapped raw releases to newly-added canonical mappings."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Indicator, IndicatorRelease
from app.db.session import dispose_engine, session_scope
from app.ingestion.canonicalizer import CanonicalEvent, Canonicalizer
from app.ingestion.ingest_service import _infer_frequency, _infer_unit


@dataclass
class ReclassifyStats:
    scanned: int = 0
    mapped: int = 0
    still_unmapped: int = 0
    created_indicators: int = 0
    marked_non_latest_duplicates: int = 0
    revisions: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reclassify indicator_releases rows that were stored as unmapped."
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional max rows to scan, useful for dry operational checks.",
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    canonicalizer = Canonicalizer.from_default_config()
    stats = ReclassifyStats()

    async with session_scope() as session:
        rows = await fetch_unmapped_rows(session, args.limit)
        indicator_cache: dict[tuple[str, str], Indicator] = {}
        for row in rows:
            stats.scanned += 1
            canonical = canonicalizer.canonicalize(row.raw_payload or {})
            if canonical is None or canonical.canonical_name is None:
                stats.still_unmapped += 1
                continue

            indicator = await get_or_create_indicator(
                session, canonical, indicator_cache, stats
            )
            await attach_release(session, row, indicator, canonical, stats)
            stats.mapped += 1

        await session.flush()

    print("Reclassified stored unmapped releases.")
    print(f"  scanned: {stats.scanned}")
    print(f"  mapped: {stats.mapped}")
    print(f"  still_unmapped: {stats.still_unmapped}")
    print(f"  created_indicators: {stats.created_indicators}")
    print(f"  marked_non_latest_duplicates: {stats.marked_non_latest_duplicates}")
    print(f"  revisions: {stats.revisions}")
    await dispose_engine()


async def fetch_unmapped_rows(
    session: AsyncSession, limit: int | None
) -> list[IndicatorRelease]:
    query = (
        select(IndicatorRelease)
        .where(IndicatorRelease.indicator_id.is_(None))
        .order_by(IndicatorRelease.released_at, IndicatorRelease.id)
    )
    if limit is not None:
        query = query.limit(limit)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_or_create_indicator(
    session: AsyncSession,
    canonical: CanonicalEvent,
    cache: dict[tuple[str, str], Indicator],
    stats: ReclassifyStats,
) -> Indicator:
    assert canonical.canonical_name is not None
    cache_key = (canonical.canonical_name, canonical.country)
    if cache_key in cache:
        return cache[cache_key]

    result = await session.execute(
        select(Indicator).where(
            Indicator.canonical_name == canonical.canonical_name,
            Indicator.country_code == canonical.country,
        )
    )
    indicator = result.scalar_one_or_none()
    if indicator is None:
        indicator = Indicator(
            canonical_name=canonical.canonical_name,
            display_name=canonical.display_name or canonical.canonical_name,
            country_code=canonical.country,
            primary_category=canonical.primary_category or "Other",
            secondary_categories=list(canonical.secondary_categories),
            comparison=canonical.raw_payload.get("comparison"),
            frequency=_infer_frequency(canonical),
            unit=_infer_unit(canonical),
            is_higher_better_for_currency=canonical.is_higher_better_for_currency,
            importance=canonical.importance,
        )
        session.add(indicator)
        await session.flush()
        stats.created_indicators += 1

    cache[cache_key] = indicator
    return indicator


async def attach_release(
    session: AsyncSession,
    row: IndicatorRelease,
    indicator: Indicator,
    canonical: CanonicalEvent,
    stats: ReclassifyStats,
) -> None:
    latest = await find_existing_latest(session, row, indicator, canonical)
    row.indicator_id = indicator.id
    row.period = canonical.period_raw
    row.period_start_date = canonical.period_start_date
    row.released_at = canonical.released_at
    row.actual = canonical.actual
    row.previous = canonical.previous
    row.estimate = canonical.estimate
    row.change = canonical.change
    row.change_percentage = canonical.change_percentage

    if latest is None:
        row.is_latest = True
        return

    if (
        latest.actual == canonical.actual
        and latest.estimate == canonical.estimate
        and latest.previous == canonical.previous
    ):
        row.is_latest = False
        stats.marked_non_latest_duplicates += 1
        return

    await session.execute(
        update(IndicatorRelease)
        .where(IndicatorRelease.id == latest.id)
        .values(is_latest=False)
    )
    row.is_latest = True
    stats.revisions += 1


async def find_existing_latest(
    session: AsyncSession,
    row: IndicatorRelease,
    indicator: Indicator,
    canonical: CanonicalEvent,
) -> IndicatorRelease | None:
    query = select(IndicatorRelease).where(
        IndicatorRelease.id != row.id,
        IndicatorRelease.indicator_id == indicator.id,
        IndicatorRelease.is_latest.is_(True),
    )
    if canonical.period_start_date is None:
        query = query.where(IndicatorRelease.period == canonical.period_raw)
    else:
        query = query.where(
            IndicatorRelease.period_start_date == canonical.period_start_date
        )
    result = await session.execute(query.order_by(IndicatorRelease.id.desc()).limit(1))
    return result.scalar_one_or_none()


if __name__ == "__main__":
    asyncio.run(main())
