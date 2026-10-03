from datetime import date
from types import SimpleNamespace

import pytest

from app.services.yield_spreads import align_spread, build_yield_spreads


def test_common_dates_and_two_business_day_fill():
    base = {date(2026, 8, 3): 4.0, date(2026, 8, 7): 4.2}
    quote = {date(2026, 8, 3): 3.0, date(2026, 8, 5): 3.1, date(2026, 8, 6): 3.2}
    rows = align_spread(base, quote)
    assert [row["obs_date"] for row in rows] == [
        date(2026, 8, 3), date(2026, 8, 5), date(2026, 8, 7),
    ]
    assert rows[1]["spread_bp"] == pytest.approx(90.0)
    assert rows[2]["spread_bp"] == pytest.approx(100.0)


def test_no_fill_past_two_business_days_or_before_first_print():
    base = {date(2026, 8, 3): 4.0, date(2026, 8, 10): 4.1}
    quote = {date(2026, 8, 5): 3.0}
    rows = align_spread(base, quote)
    assert [row["obs_date"] for row in rows] == [date(2026, 8, 5)]


@pytest.mark.asyncio
async def test_configured_spread_signs_for_three_pairs_and_fr_de():
    day = date(2026, 8, 3)
    values = {"US": 4.0, "JP": 1.0, "DE": 2.0, "AU": 3.0, "CA": 2.5, "FR": 2.4}

    class Session:
        rows = []

        async def execute(self, query, params=None):
            if "SELECT DISTINCT" in str(query):
                return [SimpleNamespace(country_code=country, maturity="2Y",
                                        market_observation_date=day, yield_value=value)
                        for country, value in values.items()]
            if "INSERT INTO yield_spreads" in str(query):
                self.rows = params
            return []

    session = Session()
    await build_yield_spreads(session)
    by_name = {row["spread_name"]: row["spread_bp"] for row in session.rows}
    assert by_name["USD/JPY"] == pytest.approx(300)
    assert by_name["EUR/USD"] == pytest.approx(-200)
    assert by_name["AUD/CAD"] == pytest.approx(50)
    assert by_name["FR-DE"] == pytest.approx(40)
