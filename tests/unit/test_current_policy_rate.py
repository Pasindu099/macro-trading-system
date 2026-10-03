"""Step 8 Part 0: the Fed's current rate comes from the latest decision; config is a dated fallback."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services import rate_probability


class _Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class _Session:
    def __init__(self, row):
        self.row = row

    async def execute(self, statement, params=None):
        return _Result(self.row)


@pytest.mark.asyncio
async def test_fed_rate_from_latest_decision():
    row = SimpleNamespace(rate=4.0, released_at=datetime(2026, 9, 16, 18, tzinfo=UTC))
    resolved = await rate_probability.resolve_current_rate("FED", _Session(row))
    assert resolved == {"rate": 4.0, "source": "decision", "as_of": date(2026, 9, 16)}


@pytest.mark.asyncio
async def test_config_is_fallback_with_as_of_date():
    resolved = await rate_probability.resolve_current_rate("FED", _Session(None))
    assert resolved["source"] == "config"
    assert resolved["rate"] == rate_probability._bank_config("FED")["current_rate"]
    assert resolved["as_of"] == date(2026, 5, 10)
