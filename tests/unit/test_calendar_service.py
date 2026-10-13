from datetime import date, datetime, timezone

import pytest

from app.services.calendar_service import _shape, impact_for, list_events
from app.processing.cb_preferred_score import MANDATE_WEIGHTS


def row(**changes):
    base = dict(id=1, indicator_id=2, released_at=datetime(2024, 1, 2, 13, tzinfo=timezone.utc),
                period="Dec", actual=3, estimate=2, previous=1, first_previous=0,
                raw_payload={}, retrieved_at=None, display_name="CPI", canonical_name="cpi",
                importance=3, unit="%", country_code="US", currency_code="USD",
                country_name="United States", surprise_normalized=1.5,
                bundle_id=None, bundle_key=None)
    return base | changes


def test_overrides_and_bundle_fallback():
    assert impact_for("Caixin Manufacturing PMI", 3) == 1
    assert impact_for("Minor auction", 3) == 3
    events = _shape([row(), row(id=2, display_name="Core CPI", surprise_normalized=-2)],
                    None, 1, False)
    assert len(events) == 1
    assert len(events[0]["components"]) == 2
    assert events[0]["surprise_sigma"] == -2
    assert events[0]["components"][0]["revised_from"] == 0


def test_china_calendar_only_and_unmapped():
    assert "CN" not in MANDATE_WEIGHTS
    events = _shape([row(country_code=None, currency_code="CNY", raw_payload={"country": "CN", "type": "PMI"},
                         indicator_id=None, display_name=None, surprise_normalized=None)], None, 3, False)
    assert events[0]["country_code"] == "CN"
    assert events[0]["components"][0]["mapped"] is False


@pytest.mark.asyncio
async def test_day_paging_past_and_future():
    class Result:
        def mappings(self): return self
        def all(self): return []
    class Session:
        def __init__(self): self.calls = []
        async def execute(self, _sql, params):
            self.calls.append(params)
            return Result()
    session = Session()
    await list_events(session, date(2020, 1, 1), date(2020, 1, 3))
    assert len(session.calls) == 3
    assert all((p["end_at"] - p["start_at"]).days == 1 for p in session.calls)
    session.calls.clear()
    await list_events(session, date(2030, 1, 1), date(2030, 12, 31), day=date(2030, 5, 1))
    assert len(session.calls) == 1
