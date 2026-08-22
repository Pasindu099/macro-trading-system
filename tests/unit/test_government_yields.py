from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.services import government_yields as gy


class _Result:
    def __init__(self, rowcount: int = 0, scalar=None):
        self.rowcount = rowcount
        self._scalar = scalar

    def scalar_one_or_none(self):
        return self._scalar


class _FakeSession:
    def __init__(self, rowcounts: list[int] | None = None, scalar_values: list[object] | None = None):
        self.rowcounts = rowcounts or []
        self.scalar_values = scalar_values or []
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        if self.rowcounts:
            return _Result(rowcount=self.rowcounts.pop(0))
        if self.scalar_values:
            return _Result(scalar=self.scalar_values.pop(0))
        return _Result()


class _FakeClient:
    async def fetch_exchange_symbols(self, exchange: str):
        assert exchange == "GBOND"
        return [{"Code": "US2Y"}]

    async def fetch_government_yield_history(self, country_prefix, maturity, *, from_date, to_date):
        assert country_prefix == "US"
        assert maturity == "2Y"
        return [{"date": "2026-08-21", "close": "3.742"}]


def test_configured_gbond_symbols_include_actual_2y() -> None:
    symbols = gy.configured_gbond_symbols()

    assert "US2Y.GBOND" in symbols
    assert "DE2Y.GBOND" in symbols
    assert len(symbols) == 8 * 8


def test_build_observation_record_uses_close_as_actual_yield() -> None:
    record = gy.build_observation_record(
        "US",
        "2Y",
        {"date": "2026-08-21", "close": "3.742"},
    )

    assert record is not None
    assert record["provider_symbol"] == "US2Y.GBOND"
    assert record["maturity"] == "2Y"
    assert record["yield_value"] == Decimal("3.742")
    assert record["observation_kind"] == "actual"
    assert record["source_type"] == "licensed_api"
    assert record["quality_status"] == "valid"


def test_build_observation_record_flags_impossible_values() -> None:
    record = gy.build_observation_record(
        "US",
        "10Y",
        {"date": "2026-08-21", "close": "125.0"},
    )

    assert record is not None
    assert record["quality_status"] == "invalid"
    assert record["validation_errors"] == ["yield_above_reasonable_ceiling"]


def test_negative_yields_remain_valid() -> None:
    record = gy.build_observation_record(
        "DE",
        "2Y",
        {"date": "2026-08-21", "close": "-0.421"},
    )

    assert record is not None
    assert record["yield_value"] == Decimal("-0.421")
    assert record["quality_status"] == "valid"
    assert record["validation_errors"] is None


def test_same_date_changed_provider_payload_gets_distinct_hash() -> None:
    first = gy.build_observation_record(
        "US",
        "2Y",
        {"date": "2026-08-21", "close": "3.742"},
    )
    revised = gy.build_observation_record(
        "US",
        "2Y",
        {"date": "2026-08-21", "close": "3.743"},
    )

    assert first is not None
    assert revised is not None
    assert first["provider_symbol"] == revised["provider_symbol"]
    assert first["market_observation_date"] == revised["market_observation_date"]
    assert first["payload_hash"] != revised["payload_hash"]


@pytest.mark.asyncio
async def test_insert_observation_idempotent_returns_rowcount() -> None:
    record = gy.build_observation_record(
        "US",
        "2Y",
        {"date": "2026-08-21", "close": "3.742"},
    )
    assert record is not None
    session = _FakeSession(rowcounts=[1, 0])

    assert await gy.insert_observation_idempotent(session, record) == 1
    assert await gy.insert_observation_idempotent(session, record) == 0


@pytest.mark.asyncio
async def test_ingest_reports_partial_success_for_missing_symbols() -> None:
    session = _FakeSession(rowcounts=[1, 1])
    stats = await gy.ingest_eodhd_government_yields(
        session,
        _FakeClient(),
        from_date=date(2026, 8, 20),
        to_date=date(2026, 8, 21),
        country_prefixes=["US"],
        maturities=["2Y", "10Y"],
        stale_after_days=3,
    )

    assert stats.observations_seen == 1
    assert stats.observations_inserted == 1
    assert stats.symbols_missing == ["US10Y.GBOND"]
    assert stats.status == "partial"


@pytest.mark.asyncio
async def test_stale_check_reports_missing_and_stale_symbols(monkeypatch) -> None:
    monkeypatch.setattr(gy, "configured_gbond_symbols", lambda: ["US2Y.GBOND", "DE2Y.GBOND"])
    session = _FakeSession(scalar_values=[date(2026, 8, 10), None])

    stats = await gy.check_government_yield_staleness(
        session,
        as_of=date(2026, 8, 21),
        stale_after_days=3,
    )

    assert stats.stale_symbols == ["US2Y.GBOND"]
    assert stats.symbols_missing == ["DE2Y.GBOND"]
    assert stats.status == "partial"
