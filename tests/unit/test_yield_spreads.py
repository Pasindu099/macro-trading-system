from datetime import date

import pytest

from app.services.yield_spreads import align_spread


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
