"""Step 7 Part F: calendar re-fetch stays under the EODHD 1,000-event cap."""

from datetime import date

import pytest

from scripts import backfill_calendar_gaps as gaps


def test_month_windows_cover_range_without_overlap():
    windows = gaps.month_windows(date(2025, 1, 15), date(2025, 3, 10))
    assert windows == [(date(2025, 1, 15), date(2025, 1, 31)), (date(2025, 2, 1), date(2025, 2, 28)),
                       (date(2025, 3, 1), date(2025, 3, 10))]


class _CappedClient:
    """Returns at most 1,000 events: the latest ones, like EODHD."""

    def __init__(self, per_day: int):
        self.per_day = per_day

    async def fetch_economic_events(self, country, a, b):
        days = (b - a).days + 1
        events = [{"day": a.toordinal() + i // self.per_day, "n": i} for i in range(days * self.per_day)]
        return events[-gaps.EODHD_CAP:]


@pytest.mark.asyncio
async def test_window_hitting_cap_is_split_until_complete():
    calls = [0]
    events = await gaps.fetch_window(_CappedClient(per_day=50), "US", date(2025, 1, 1), date(2025, 1, 31), calls)
    assert len(events) == 31 * 50          # nothing truncated
    assert calls[0] > 1                    # the 1,550-event month was split


@pytest.mark.asyncio
async def test_window_under_cap_is_one_call():
    calls = [0]
    events = await gaps.fetch_window(_CappedClient(per_day=10), "US", date(2025, 1, 1), date(2025, 1, 31), calls)
    assert len(events) == 310 and calls[0] == 1
