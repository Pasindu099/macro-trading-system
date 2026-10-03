from datetime import date
from types import SimpleNamespace

import pytest

from app.services import rates


class _Result:
    def __iter__(self):
        return iter([
            SimpleNamespace(provider_symbol="US10Y.GBOND", obs_date=date(2026, 8, 3), close=4.5),
        ])


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def execute(self, query, parameters):
        assert "government_yield_observations" in str(query)
        assert "quality_status = 'valid'" in str(query)
        assert parameters["symbols"] == ["US10Y.GBOND", "DE10Y.GBOND"]
        return _Result()


@pytest.mark.asyncio
async def test_stored_histories_keep_symbol_order_and_missing_series(monkeypatch):
    monkeypatch.setattr(rates, "get_sessionmaker", lambda: _Session)
    histories = await rates._stored_histories(
        ["US10Y.GBOND", "DE10Y.GBOND"], date(2026, 8, 1), date(2026, 8, 5),
    )
    assert histories == [[{"date": "2026-08-03", "close": 4.5}], []]
