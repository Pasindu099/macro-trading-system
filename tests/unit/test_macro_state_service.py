from unittest.mock import AsyncMock

import pytest

from app.services.macro_state import get_macro_state_board


class Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


@pytest.mark.asyncio
async def test_empty_primary_uses_legacy_rows():
    session = AsyncMock()
    session.execute.side_effect = [Result([]), Result([{"currency": "USD", "overall_score": 1.2}])]
    board = await get_macro_state_board(session)
    assert board == {"source": "currency_stance", "rows": [{"currency": "USD", "overall_score": 1.2}]}


@pytest.mark.asyncio
async def test_primary_exception_uses_legacy_rows_after_rollback():
    session = AsyncMock()
    session.execute.side_effect = [RuntimeError("missing"), Result([{"currency": "EUR"}])]
    board = await get_macro_state_board(session)
    session.rollback.assert_awaited_once()
    assert board["source"] == "currency_stance"
    assert board["rows"] == [{"currency": "EUR"}]


@pytest.mark.asyncio
async def test_primary_rows_take_precedence():
    session = AsyncMock()
    session.execute.return_value = Result([{"currency": "JPY", "overall_score": 0.4}])
    board = await get_macro_state_board(session)
    assert board["source"] == "cb_preferred_score"
    assert session.execute.await_count == 1
