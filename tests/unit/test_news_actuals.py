from decimal import Decimal
from datetime import datetime, UTC
from types import SimpleNamespace

import pytest

from app.services.news_actuals import choose_candidate, parse_headline
from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.ingest_service import IngestService
from app.db.models import IndicatorRelease


def test_parse_explicit_cpi_print():
    result = parse_headline("US September CPI +0.4% vs +0.3% expected")
    assert result is not None
    assert (result.country, result.canonical_prefix, result.actual, result.consensus) == (
        "US", "cpi_headline", Decimal("0.4"), Decimal("0.3"))


def test_parse_lower_is_better_unemployment_print():
    result = parse_headline("UK unemployment rate 4.1% vs 4.3% expected")
    assert result is not None
    assert (result.country, result.canonical_prefix, result.actual) == (
        "UK", "unemployment_rate", Decimal("4.1"))


def test_previews_and_ambiguous_headlines_are_not_parsed():
    assert parse_headline("US September CPI preview: 0.4% expected") is None
    assert parse_headline("CPI 0.4% vs 0.3% expected") is None


def test_forecast_resolves_co_releases_but_not_different_indicators_with_same_forecast():
    parsed = parse_headline("US September CPI 3.7% vs 3.6% expected")
    rows = [{"id": 1, "canonical_name": "cpi_headline_mom", "period": "Sep", "released_at": 1,
             "estimate": Decimal("0.6")},
            {"id": 2, "canonical_name": "cpi_headline_yoy", "period": "Sep", "released_at": 1,
             "estimate": Decimal("3.6")}]
    assert choose_candidate(rows, parsed) == 2
    rows.append({**rows[1], "id": 3})
    assert choose_candidate(rows, parsed) == 3
    rows.append({**rows[1], "id": 4, "canonical_name": "cpi_other_yoy"})
    assert choose_candidate(rows, parsed) is None


@pytest.mark.asyncio
async def test_eodhd_confirmation_versions_newswire_even_when_actual_agrees():
    latest = IndicatorRelease(id=9, indicator_id=2, actual=Decimal("3.7"),
                              estimate=Decimal("3.6"), previous=Decimal("3.4"),
                              raw_payload={"source_status": "newswire"}, is_latest=True)
    canonical = SimpleNamespace(period_start_date=None, period_raw="Sep",
                                released_at=datetime(2026, 10, 14, 12, 30, tzinfo=UTC),
                                actual=Decimal("3.7"), estimate=Decimal("3.6"),
                                previous=Decimal("3.4"), change=None, change_percentage=None,
                                raw_payload={"source": "eodhd"})

    class Session:
        def __init__(self): self.queries = []; self.added = []
        async def execute(self, statement, params=None):
            self.queries.append(str(statement))
            return SimpleNamespace(scalar_one_or_none=lambda: latest)
        async def flush(self): pass
        def add(self, row): self.added.append(row)

    session = Session()
    service = IngestService(Canonicalizer([]))
    assert await service._upsert_release(session, SimpleNamespace(id=2), canonical) == "updated"
    assert len(session.added) == 1
    assert "UPDATE newswire_actuals" in session.queries[-1]
    session = Session()
    canonical.actual = None
    assert await service._upsert_release(session, SimpleNamespace(id=2), canonical) == "same"
    assert not session.added
