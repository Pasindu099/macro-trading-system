from datetime import date, datetime, timezone

import pytest

from app.services.scraped_rate_probability import get_scraped_rate_probability_data


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    async def execute(self, statement, params=None):
        self.calls += 1
        return _Result(self.results.pop(0))


@pytest.mark.asyncio
async def test_scraped_probability_api_payload_selection():
    updated_at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    session = _Session([{
        "bank": "FED", "current_rate": 3.75, "next_meeting_date": date(2026, 10, 28),
        "next_cut_prob": .6, "next_hold_prob": .4, "next_hike_prob": None,
        "updated_at": updated_at,
    }], [{
        "bank": "FED", "meeting_date": date(2026, 10, 28), "implied_rate": 3.5,
        "cut_prob": .6, "hold_prob": .4, "hike_prob": 0,
        "delta_bps": -25, "cumulative_moves": -1,
    }])
    data = await get_scraped_rate_probability_data(session)
    assert data["banks"]["FED"]["dominant"] == "CUT"
    assert data["banks"]["FED"]["meetings"][0]["date"] == "2026-10-28"
    assert data["banks"]["ECB"]["current_rate"] is None
    assert session.calls == 2
