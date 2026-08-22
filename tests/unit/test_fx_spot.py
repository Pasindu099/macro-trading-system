from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import fx_spot


class _Result:
    def __init__(self, rowcount: int):
        self.rowcount = rowcount


class _FakeSession:
    def __init__(self, rowcounts: list[int]):
        self.rowcounts = rowcounts

    async def execute(self, statement):
        return _Result(self.rowcounts.pop(0))


class _FakeClient:
    async def fetch_eod_history(self, symbol, *, from_date, to_date, period):
        assert symbol == "EURUSD.FOREX"
        assert period == "d"
        return [{"date": "2026-08-21", "close": "1.10425"}]


def test_fx_pairs_cover_required_crosses() -> None:
    for pair in ("EUR/USD", "AUD/NZD", "CAD/JPY", "EUR/CHF"):
        assert pair in fx_spot.FX_PAIR_SYMBOLS


def test_build_fx_observation_record() -> None:
    record = fx_spot.build_fx_observation_record(
        "EUR/USD",
        "EURUSD.FOREX",
        {"date": "2026-08-21", "close": "1.10425"},
    )

    assert record is not None
    assert record["pair"] == "EUR/USD"
    assert record["base_currency"] == "EUR"
    assert record["quote_currency"] == "USD"
    assert record["close_value"] == Decimal("1.10425")
    assert record["source_type"] == "licensed_api"
    assert record["quality_status"] == "valid"


def test_fx_negative_or_zero_prices_are_invalid() -> None:
    assert fx_spot.validate_fx_close(Decimal("0")) == ["fx_close_must_be_positive"]
    assert fx_spot.validate_fx_close(Decimal("-1")) == ["fx_close_must_be_positive"]


@pytest.mark.asyncio
async def test_fx_ingestion_is_idempotent_by_rowcount() -> None:
    record = fx_spot.build_fx_observation_record(
        "EUR/USD",
        "EURUSD.FOREX",
        {"date": "2026-08-21", "close": "1.10425"},
    )
    assert record is not None
    session = _FakeSession([1, 0])

    assert await fx_spot.insert_fx_observation_idempotent(session, record) == 1
    assert await fx_spot.insert_fx_observation_idempotent(session, record) == 0


@pytest.mark.asyncio
async def test_fx_ingest_partial_success() -> None:
    stats = await fx_spot.ingest_eodhd_fx_spot(
        _FakeSession([1]),
        _FakeClient(),
        from_date=date(2026, 8, 20),
        to_date=date(2026, 8, 21),
        pairs=["EUR/USD", "XXX/YYY"],
    )

    assert stats.observations_seen == 1
    assert stats.observations_inserted == 1
    assert stats.errors == ["XXX/YYY: unsupported pair"]
    assert stats.status == "partial"
