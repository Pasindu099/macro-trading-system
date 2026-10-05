from unittest.mock import AsyncMock

import pytest

from app.services.macro_state_jobs import validate_macro_state


class Result:
    def __init__(self, value):
        self.value = value

    def scalar_one(self):
        return self.value

    def all(self):
        return self.value


@pytest.mark.asyncio
async def test_macro_guard_rejects_out_of_range_theme_before_commit():
    session = AsyncMock()
    session.execute.return_value = Result(1)
    with pytest.raises(RuntimeError, match="cb_preferred_score has 1 future or out-of-range"):
        await validate_macro_state(session)


@pytest.mark.asyncio
async def test_macro_guard_requires_half_of_each_currency_watchlist():
    session = AsyncMock()
    session.execute.side_effect = [Result(0) for _ in range(5)] + [Result([("US", 4)])]
    with pytest.raises(RuntimeError, match="US has 4/9 scorable indicators"):
        await validate_macro_state(session)


@pytest.mark.asyncio
async def test_macro_guard_accepts_bounded_complete_outputs():
    session = AsyncMock()
    coverage = [("US", 9), ("EU", 9), ("UK", 9), ("JP", 9),
                ("AU", 7), ("NZ", 7), ("CA", 10), ("CH", 6)]
    session.execute.side_effect = [Result(0) for _ in range(5)] + [Result(coverage)]
    await validate_macro_state(session)
