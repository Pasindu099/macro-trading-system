"""Step 5 Part 0: provider revisions in the last five business days are re-fetched."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from app.ingestion import scheduler as sched
from app.services.fx_spot import build_fx_observation_record
from app.services.government_yields import revision_refetch_start


def test_refetch_start_spans_five_business_days_across_weekend() -> None:
    # Monday 2026-10-05 → previous Mon..Fri is 2026-09-28.
    assert revision_refetch_start(date(2026, 10, 5)) == date(2026, 9, 28)
    assert revision_refetch_start(date(2026, 10, 3)) == date(2026, 9, 28)


def test_revised_fx_close_keeps_date_and_changes_hash() -> None:
    # GBPUSD 2026-08-04: payload stored at ingestion vs the current provider bar.
    stored = build_fx_observation_record("GBP/USD", "GBPUSD.FOREX", {"date": "2026-08-04", "close": 1.3427})
    revised = build_fx_observation_record("GBP/USD", "GBPUSD.FOREX", {"date": "2026-08-04", "close": 1.345055})
    assert stored["observation_date"] == revised["observation_date"] == date(2026, 8, 4)
    assert stored["payload_hash"] != revised["payload_hash"]


@pytest.mark.asyncio
async def test_daily_rates_job_refetches_fx_and_yields_then_rebuilds(monkeypatch) -> None:
    calls: dict[str, object] = {}

    @asynccontextmanager
    async def _ctx(*_args, **_kwargs):
        yield object()

    async def _yields(session, client, *, from_date, to_date, **_):
        calls["yield_from"] = from_date
        return SimpleNamespace(status="success", observations_seen=0, observations_inserted=0,
                               symbols_missing=[], stale_symbols=[], errors=[])

    async def _fx(session, client, *, from_date, to_date, **_):
        calls["fx_from"] = from_date
        return SimpleNamespace(status="success", observations_seen=3, observations_inserted=2, errors=[])

    async def _derived():
        calls["derived"] = True

    class _Date(date):
        @classmethod
        def today(cls):
            return date(2026, 10, 5)

    monkeypatch.setattr(sched, "date", _Date)
    monkeypatch.setattr(sched, "EODHDClient", _ctx)
    monkeypatch.setattr(sched, "session_scope", _ctx)
    monkeypatch.setattr(sched, "ingest_eodhd_government_yields", _yields)
    monkeypatch.setattr(sched, "ingest_eodhd_fx_spot", _fx)
    monkeypatch.setattr(sched, "run_rates_derived", _derived)

    await sched.Scheduler._run_government_yield_incremental(object.__new__(sched.Scheduler))

    assert calls["yield_from"] <= date(2026, 9, 28)
    assert calls["fx_from"] == calls["yield_from"]
    assert calls["derived"] is True
