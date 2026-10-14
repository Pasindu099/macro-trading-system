from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.db.models import IndicatorRelease
from app.ingestion.canonicalizer import CanonicalEvent, Canonicalizer
from app.ingestion.ingest_service import IngestService


def event(actual):
    return CanonicalEvent(
        canonical_name=None, display_name="Inflation Rate", primary_category="Inflation",
        secondary_categories=(), importance=1, is_higher_better_for_currency=True,
        country="US", released_at=datetime(2026, 10, 14, 12, 30, tzinfo=timezone.utc),
        period_raw="Sep", period_start_date=None, actual=actual,
        previous=Decimal("3.4"), estimate=Decimal("3.6"), change=None,
        change_percentage=None,
        raw_payload={"country": "US", "type": "Inflation Rate", "comparison": "yoy"},
    )


@pytest.mark.asyncio
async def test_unmapped_scheduled_release_versions_when_actual_arrives():
    old = IndicatorRelease(id=10, indicator_id=None, actual=None,
                           estimate=Decimal("3.6"), previous=Decimal("3.4"), is_latest=True)

    class Result:
        def scalar_one_or_none(self): return old

    class Session:
        def __init__(self): self.queries = []; self.added = []
        async def execute(self, statement):
            self.queries.append(statement)
            return Result()
        def add(self, row): self.added.append(row)

    session = Session()
    await IngestService(Canonicalizer([]))._store_unmapped(session, event(Decimal("3.7")))
    assert len(session.queries) == 2
    assert "UPDATE indicator_releases" in str(session.queries[1])
    assert len(session.added) == 1
    assert session.added[0].actual == Decimal("3.7")
    assert session.added[0].is_latest is True
