"""Ingestion service.

Responsibilities:
    1. Take raw EODHD events → canonicalize → write to DB
    2. Handle revisions: new value for existing (indicator, period) marks the
       old row is_latest=False and inserts a new row (spec §4.4)
    3. Create indicators on-demand from canonicalizer mappings
    4. Return detailed stats for caller to log / display

Usage:
    from app.ingestion.canonicalizer import Canonicalizer
    from app.ingestion.ingest_service import IngestService
    from app.db import session_scope

    canonicalizer = Canonicalizer.from_default_config()
    service = IngestService(canonicalizer)

    async with session_scope() as session:
        stats = await service.ingest_events(session, raw_events)
        print(f"Inserted: {stats.inserted}, Updated: {stats.updated}")
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session
from app.db.models import Indicator, IndicatorRelease
from app.ingestion.canonicalizer import Canonicalizer, CanonicalEvent

logger = logging.getLogger(__name__)


@dataclass
class IngestStats:
    """Summary of what happened during an ingest run."""

    inserted: int = 0      # Brand new (indicator_id, period) rows
    updated: int = 0       # Revisions: new version of existing (indicator_id, period)
    skipped_same: int = 0  # Already have this exact value, no-op
    skipped_null_country: int = 0   # Country outside allowlist
    unmapped: int = 0      # Type not in mapping YAML
    unmapped_stored: int = 0  # Unmapped but stored with indicator_id=NULL
    errors: list[str] = field(default_factory=list)

    @property
    def total_processed(self) -> int:
        return (
            self.inserted + self.updated + self.skipped_same
            + self.skipped_null_country + self.unmapped
        )

    def __str__(self) -> str:
        return (
            f"ingested={self.inserted} updated={self.updated} "
            f"same={self.skipped_same} unmapped={self.unmapped} "
            f"off-country={self.skipped_null_country} errors={len(self.errors)}"
        )


class IngestService:
    """Writes canonicalized events to the database.

    One IngestService per app instance is enough. It's stateless aside from
    the injected canonicalizer and an in-memory indicator cache for
    performance (rebuilt at process start).
    """

    def __init__(self, canonicalizer: Canonicalizer) -> None:
        self._canonicalizer = canonicalizer
        # Cache of (canonical_name, country) → Indicator ORM object.
        # Populated lazily during ingest. Reset when process restarts.
        self._indicator_cache: dict[tuple[str, str], Indicator] = {}

    async def ingest_events(
        self,
        session: AsyncSession,
        raw_events: list[dict[str, Any]],
        *,
        store_unmapped: bool = True,
    ) -> IngestStats:
        """Ingest a batch of raw EODHD events.

        Args:
            session: Active async DB session. Caller controls commit/rollback.
            raw_events: Raw event dicts as returned by EODHDClient.
            store_unmapped: If True, unmapped events are stored with
                indicator_id=NULL so they can be retroactively classified
                when a mapping is added later. If False, they're dropped.

        Returns:
            IngestStats with counts of what happened.
        """
        stats = IngestStats()

        for raw in raw_events:
            try:
                await self._ingest_one(session, raw, stats, store_unmapped)
            except Exception as exc:
                msg = f"Failed to ingest event {raw.get('type')!r}: {exc}"
                logger.exception(msg)
                stats.errors.append(msg)

        # Flush accumulated changes so caller sees them before commit.
        await session.flush()

        logger.info("Ingest batch complete: %s", stats)
        return stats

    async def _ingest_one(
        self,
        session: AsyncSession,
        raw: dict[str, Any],
        stats: IngestStats,
        store_unmapped: bool,
    ) -> None:
        """Process one raw event."""
        canonical = self._canonicalizer.canonicalize(raw)

        if canonical is None:
            # Country outside allowlist — silently skipped
            stats.skipped_null_country += 1
            return

        if canonical.canonical_name is None:
            stats.unmapped += 1
            if store_unmapped:
                await self._store_unmapped(session, canonical)
                stats.unmapped_stored += 1
            return

        # Canonical is mapped — find or create the indicator row
        indicator = await self._get_or_create_indicator(session, canonical)

        # Apply revision logic
        outcome = await self._upsert_release(session, indicator, canonical)
        if outcome == "inserted":
            stats.inserted += 1
        elif outcome == "updated":
            stats.updated += 1
        else:  # "same"
            stats.skipped_same += 1

    async def _get_or_create_indicator(
        self,
        session: AsyncSession,
        canonical: CanonicalEvent,
    ) -> Indicator:
        """Look up or create the indicator row for this canonical event."""
        assert canonical.canonical_name is not None  # checked by caller

        cache_key = (canonical.canonical_name, canonical.country)
        cached = self._indicator_cache.get(cache_key)
        if cached is not None:
            # Confirm it's still in the session (can detach between batches)
            if cached in session:
                return cached

        # Query DB
        result = await session.execute(
            select(Indicator).where(
                Indicator.canonical_name == canonical.canonical_name,
                Indicator.country_code == canonical.country,
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            self._indicator_cache[cache_key] = existing
            return existing

        # Find the full mapping to get all the fields we need
        mapping = self._canonicalizer._lookup.get(
            (canonical.country, _guess_eodhd_type(canonical), None)
        )
        # We don't strictly need the mapping here — we have everything we
        # need from the canonical event itself.

        new_indicator = Indicator(
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
        session.add(new_indicator)
        await session.flush()  # Get the generated id
        logger.info(
            "Created indicator %s for country %s (id=%d)",
            canonical.canonical_name, canonical.country, new_indicator.id,
        )
        self._indicator_cache[cache_key] = new_indicator
        return new_indicator

    async def _upsert_release(
        self,
        session: AsyncSession,
        indicator: Indicator,
        canonical: CanonicalEvent,
    ) -> str:
        """Insert-or-revise the release for this (indicator, period).

        Returns one of: "inserted", "updated", "same"
        """
        # Find the current latest row for this (indicator, period)
        query = select(IndicatorRelease).where(
            IndicatorRelease.indicator_id == indicator.id,
            IndicatorRelease.period_start_date == canonical.period_start_date,
            IndicatorRelease.is_latest.is_(True),
        )
        # If period_start_date is None (unparseable), match by raw period string
        if canonical.period_start_date is None:
            query = select(IndicatorRelease).where(
                IndicatorRelease.indicator_id == indicator.id,
                IndicatorRelease.period == canonical.period_raw,
                IndicatorRelease.is_latest.is_(True),
            )

        query = query.order_by(IndicatorRelease.id.desc()).limit(1)
        result = await session.execute(query)
        latest = result.scalar_one_or_none()

        if latest is None:
            # No existing row → insert fresh
            new_row = self._make_release_row(indicator.id, canonical)
            session.add(new_row)
            return "inserted"

        # Compare actual value. If unchanged, skip (no-op).
        if latest.actual == canonical.actual:
            # Also check estimate — EODHD sometimes updates consensus post-release
            if (
                latest.estimate == canonical.estimate
                and latest.previous == canonical.previous
            ):
                return "same"

        # Value changed → mark old as not-latest and insert new
        await session.execute(
            update(IndicatorRelease)
            .where(IndicatorRelease.id == latest.id)
            .values(is_latest=False)
        )
        new_row = self._make_release_row(indicator.id, canonical)
        session.add(new_row)
        return "updated"

    async def _store_unmapped(
        self,
        session: AsyncSession,
        canonical: CanonicalEvent,
    ) -> None:
        """Store an unmapped event with indicator_id=NULL.

        We use (country, period, raw_payload.type) as a dedup key to avoid
        exploding the DB with duplicate unmapped events.
        """
        raw_type = canonical.raw_payload.get("type")
        raw_comparison = canonical.raw_payload.get("comparison")

        # Check if we already have this exact unmapped entry
        existing = await session.execute(
            select(IndicatorRelease)
            .where(
                IndicatorRelease.indicator_id.is_(None),
                IndicatorRelease.period == canonical.period_raw,
                IndicatorRelease.released_at == canonical.released_at,
                IndicatorRelease.raw_payload["type"].astext == raw_type,
                IndicatorRelease.raw_payload["comparison"].astext == raw_comparison,
            )
            .order_by(IndicatorRelease.id.desc())
            .limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            return  # already stored

        new_row = IndicatorRelease(
            indicator_id=None,
            period=canonical.period_raw,
            period_start_date=canonical.period_start_date,
            released_at=canonical.released_at,
            actual=canonical.actual,
            previous=canonical.previous,
            estimate=canonical.estimate,
            change=canonical.change,
            change_percentage=canonical.change_percentage,
            retrieved_at=datetime.now(timezone.utc),
            is_latest=True,
            raw_payload=canonical.raw_payload,
        )
        session.add(new_row)

    def _make_release_row(
        self,
        indicator_id: int,
        canonical: CanonicalEvent,
    ) -> IndicatorRelease:
        return IndicatorRelease(
            indicator_id=indicator_id,
            period=canonical.period_raw,
            period_start_date=canonical.period_start_date,
            released_at=canonical.released_at,
            actual=canonical.actual,
            previous=canonical.previous,
            estimate=canonical.estimate,
            change=canonical.change,
            change_percentage=canonical.change_percentage,
            retrieved_at=datetime.now(timezone.utc),
            is_latest=True,
            raw_payload=canonical.raw_payload,
        )


# ── Helpers ─────────────────────────────────────────────────────────────

def _infer_frequency(canonical: CanonicalEvent) -> str:
    """Fall back to 'irregular' if we can't derive frequency from period_raw."""
    period = canonical.period_raw
    if not period:
        return "irregular"
    period_lower = period.lower()
    if period_lower.startswith("q"):
        return "quarterly"
    if any(abbr.lower() in period_lower for abbr in [
        "jan", "feb", "mar", "apr", "may", "jun",
        "jul", "aug", "sep", "oct", "nov", "dec",
    ]):
        return "monthly"
    if "/" in period:  # "Apr/15" style — weekly reports use this often
        return "weekly"
    return "irregular"


def _infer_unit(canonical: CanonicalEvent) -> str | None:
    """We don't have unit in CanonicalEvent, so return None. Future Phase
    improvement: enrich CanonicalEvent to carry unit through."""
    return None


def _guess_eodhd_type(canonical: CanonicalEvent) -> str:
    """Best-effort recovery of the eodhd_type from a canonical event."""
    return canonical.raw_payload.get("type", canonical.canonical_name or "unknown")
