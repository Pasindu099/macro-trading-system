"""Release-date identity for period-less policy decisions in PostgreSQL."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import Date, cast, select

from app.db.models import Indicator, IndicatorRelease
from app.db.session import dispose_engine, get_sessionmaker
from app.ingestion.canonicalizer import CanonicalEvent, Canonicalizer
from app.ingestion.ingest_service import IngestService


def _decision(day: int, actual: str) -> CanonicalEvent:
    return CanonicalEvent(
        canonical_name="fed_interest_rate_decision", display_name="Fed decision",
        primary_category="central_bank", secondary_categories=(), importance=3,
        is_higher_better_for_currency=True, country="US",
        released_at=datetime(2040, 1, day, 14, 0, tzinfo=timezone.utc),
        period_raw=None, period_start_date=None, actual=Decimal(actual),
        previous=None, estimate=None, change=None, change_percentage=None,
        raw_payload={"test": True, "actual": actual},
    )


@pytest.mark.asyncio
async def test_null_period_different_dates_are_prints_same_day_is_revision():
    service = IngestService(Canonicalizer([]))
    async with get_sessionmaker()() as session:
        indicator = (await session.execute(select(Indicator).where(
            Indicator.country_code == "US",
            Indicator.canonical_name == "fed_interest_rate_decision",
        ))).scalar_one()
        assert await service._upsert_release(session, indicator, _decision(1, "4.00")) == "inserted"
        await session.flush()
        assert await service._upsert_release(session, indicator, _decision(2, "4.25")) == "inserted"
        await session.flush()
        assert await service._upsert_release(session, indicator, _decision(1, "3.75")) == "updated"
        await session.flush()
        rows = (await session.execute(select(IndicatorRelease).where(
            IndicatorRelease.indicator_id == indicator.id,
            cast(IndicatorRelease.released_at, Date).between(
                datetime(2040, 1, 1).date(), datetime(2040, 1, 2).date(),
            ),
        ).order_by(IndicatorRelease.released_at, IndicatorRelease.id))).scalars().all()
        assert [(row.released_at.date().day, str(row.actual), row.is_latest) for row in rows] == [
            (1, "4.000000", False), (1, "3.750000", True), (2, "4.250000", True),
        ]
        await session.rollback()
    await dispose_engine()
