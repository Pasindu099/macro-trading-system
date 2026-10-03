"""Incremental Event Innovation rebuild after committed calendar ingestion."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Indicator, JobWatermark
from app.db.session import session_scope
from app.ingestion.run_logger import run_logger
from app.processing.event_innovation import (
    BundleResult, ScoredRelease, ScoringConfig, build_bundles, load_changed_indicator_dates,
    load_config, load_release_records, persist, score_releases,
)
from app.services.job_lock import job_lock

logger = logging.getLogger(__name__)
JOB_NAME = "event_innovation_incremental"


@dataclass
class IncrementalPlan:
    scores: list[ScoredRelease]
    bundles: list[BundleResult]
    affected_bundle_keys: set[tuple[str, date]]
    affected_indicators: list[int]


def plan_incremental(
    scored: list[ScoredRelease], bundles: list[BundleResult],
    first_dates: dict[int, date], config: ScoringConfig,
) -> IncrementalPlan:
    """Choose the suffix to write while retaining complete scoring history."""
    member_to_bundle = {
        (spec.country, member.canonical_name): spec.bundle_key
        for spec in config.bundles for member in spec.members
    }
    affected_bundle_keys: set[tuple[str, date]] = set()
    for row in scored:
        first_date = first_dates.get(row.indicator_id)
        if first_date is None or row.release_date < first_date:
            continue
        bundle_key = member_to_bundle.get((row.country_code, row.canonical_name))
        if bundle_key:
            affected_bundle_keys.add((bundle_key, row.release_date))

    selected_scores = [
        row for row in scored
        if (row.indicator_id in first_dates
            and row.release_date >= first_dates[row.indicator_id])
        or (row.bundle_key, row.release_date) in affected_bundle_keys
    ]
    selected_bundles = [
        bundle for bundle in bundles
        if (bundle.bundle_key, bundle.release_date) in affected_bundle_keys
    ]
    return IncrementalPlan(
        scores=selected_scores,
        bundles=selected_bundles,
        affected_bundle_keys=affected_bundle_keys,
        affected_indicators=sorted(first_dates),
    )


async def _partner_ids(
    session: AsyncSession, changed_ids: list[int], config: ScoringConfig,
) -> list[int]:
    changed = (await session.execute(
        select(Indicator.id, Indicator.country_code, Indicator.canonical_name)
        .where(Indicator.id.in_(changed_ids))
    )).all()
    member_names: set[tuple[str, str]] = set()
    for indicator in changed:
        for spec in config.bundles_for_country(indicator.country_code):
            if spec.weight_for(indicator.canonical_name) is not None:
                member_names.update(
                    (spec.country, member.canonical_name) for member in spec.members
                )
    if not member_names:
        return changed_ids
    countries = sorted({country for country, _ in member_names})
    partners = (await session.execute(
        select(Indicator.id, Indicator.country_code, Indicator.canonical_name)
        .where(Indicator.country_code.in_(countries))
    )).all()
    return sorted(set(changed_ids) | {
        row.id for row in partners
        if (row.country_code, row.canonical_name) in member_names
    })


_DELETE_SUPERSEDED = text("""
WITH winners AS (
    SELECT DISTINCT ON (
        r.indicator_id,
        COALESCE(r.period_start_date::text, r.period, r.released_at::date::text)
    ) r.id
    FROM indicator_releases r
    WHERE r.indicator_id = ANY(:indicator_ids)
      AND r.actual IS NOT NULL
    ORDER BY r.indicator_id,
        COALESCE(r.period_start_date::text, r.period, r.released_at::date::text),
        r.retrieved_at DESC, r.id DESC
)
DELETE FROM event_innovation_scores s
WHERE s.indicator_id = ANY(:indicator_ids)
  AND NOT EXISTS (SELECT 1 FROM winners w WHERE w.id = s.release_id)
""")


async def delete_superseded_scores(
    session: AsyncSession, indicator_ids: list[int],
) -> int:
    if not indicator_ids:
        return 0
    result = await session.execute(_DELETE_SUPERSEDED, {"indicator_ids": indicator_ids})
    return result.rowcount or 0


async def delete_stale_bundles(
    session: AsyncSession, affected: set[tuple[str, date]],
    current: list[BundleResult],
) -> int:
    """Explicit delete; score.bundle_id is set null by its FK."""
    current_keys = {(bundle.bundle_key, bundle.release_date) for bundle in current}
    stale = affected - current_keys
    deleted = 0
    for bundle_key, release_date in stale:
        result = await session.execute(text("""
            DELETE FROM release_bundles
            WHERE bundle_key = :bundle_key AND release_date = :release_date
        """), {"bundle_key": bundle_key, "release_date": release_date})
        deleted += result.rowcount or 0
    return deleted


async def score_new_releases(session: AsyncSession, since: datetime) -> int:
    """Re-score changed indicators and their bundle partners in one transaction."""
    first_dates = await load_changed_indicator_dates(session, since)
    if not first_dates:
        return 0
    config = load_config()
    indicator_ids = await _partner_ids(session, sorted(first_dates), config)
    records = await load_release_records(session, indicator_ids=indicator_ids)
    scored = score_releases(records, config)
    bundles = build_bundles(scored, config)
    plan = plan_incremental(scored, bundles, first_dates, config)
    # A revision replaces release_id, so clear old score rows before upserting.
    deleted_scores = await delete_superseded_scores(session, plan.affected_indicators)
    deleted_bundles = await delete_stale_bundles(
        session, plan.affected_bundle_keys, plan.bundles,
    )
    written = await persist(session, plan.scores, plan.bundles)
    logger.info(
        "Event Innovation incremental: %d indicators, %d scores, %d bundles, "
        "%d superseded scores, %d stale bundles deleted",
        len(first_dates), written["scores"], written["bundles"],
        deleted_scores, deleted_bundles,
    )
    return written["scores"] + written["bundles"]


async def run_incremental_event_innovation() -> None:
    """Lock, log, guard and advance watermark only on successful commit."""
    async with job_lock(JOB_NAME) as acquired:
        async with run_logger(f"job:{JOB_NAME}") as run:
            if not acquired:
                run.mark_skipped()
                return
            started_at = datetime.now(timezone.utc)

            async def _transaction() -> int:
                async with session_scope(statement_timeout="15min") as session:
                    watermark = await session.get(JobWatermark, JOB_NAME)
                    since = (
                        watermark.last_success_at - timedelta(minutes=10)
                        if watermark else datetime.min.replace(tzinfo=timezone.utc)
                    )
                    rows = await score_new_releases(session, since)
                    if watermark:
                        watermark.last_success_at = started_at
                        watermark.updated_at = started_at
                    else:
                        session.add(JobWatermark(
                            job_name=JOB_NAME,
                            last_success_at=started_at,
                            updated_at=started_at,
                        ))
                    return rows

            run.record_rows(await asyncio.wait_for(_transaction(), timeout=16 * 60))
