from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.services.country_dashboard import get_country_profile, get_country_rows


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def execute(self, statement):
        self.calls += 1
        return _Result(self.results.pop(0))


@pytest.mark.asyncio
async def test_country_rows_keep_latest_actual_and_raw_values():
    indicator = SimpleNamespace(id=1, canonical_name="cpi_headline_yoy", display_name="Headline CPI",
                                secondary_categories=[], unit="%")
    old = SimpleNamespace(actual=3.4, previous=None, period=None, period_start_date=None,
                          released_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    new = SimpleNamespace(actual=3.7, previous=None, period=None, period_start_date=None,
                          released_at=datetime(2026, 4, 1, tzinfo=timezone.utc))
    upcoming = SimpleNamespace(actual=None, previous=3.7, period=None, period_start_date=None,
                               released_at=datetime(2026, 12, 1, tzinfo=timezone.utc))
    session = _Session([indicator], [upcoming, new, old])
    rows = await get_country_rows(session, "US", "Inflation")
    assert len(rows) == 1
    assert rows[0]["latest_value"] == 3.7
    assert rows[0]["previous_value"] == 3.4
    assert rows[0]["sparkline_values"] == [3.4, 3.7]
    assert "detail_href" not in rows[0]
    assert session.calls == 2


def test_country_profile_preserves_priority_and_change_count():
    rows = {
        "Monetary Policy": [
            {"canonical_name": "some_rate", "latest_value": 2.0, "sparkline_values": [2.0, 2.0]},
            {"canonical_name": "fed_interest_rate_decision", "latest_value": 3.0, "sparkline_values": [2.5, 3.0]},
        ],
        "Inflation": [{"canonical_name": "cpi_headline_yoy", "latest_value": 2.8, "sparkline_values": [3.0, 2.8]}],
    }
    profile = get_country_profile(rows)
    assert profile["selected"]["Monetary Policy"]["canonical_name"] == "fed_interest_rate_decision"
    assert profile["selected"]["Inflation"]["canonical_name"] == "cpi_headline_yoy"
    assert profile["changed_indicator_count"] == 2
