from __future__ import annotations

from argparse import Namespace
from datetime import date

import pytest

from scripts import run_backfill


class FakeEODHDClient:
    async def __aenter__(self) -> "FakeEODHDClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


@pytest.mark.asyncio
async def test_stale_checkpoint_chunk_is_refetched(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = {"AU": ["2020-01-01_2020-06-30"]}
    saved_progress: list[dict[str, list[str]]] = []
    fetched_chunks: list[tuple[str, date, date]] = []

    async def fake_count_stored_chunk_rows(country: str, c_from: date, c_to: date) -> int:
        return 0

    async def fake_backfill_one_chunk(client, service, country: str, c_from: date, c_to: date):
        fetched_chunks.append((country, c_from, c_to))
        return run_backfill.ChunkResult(
            country=country,
            from_date=c_from.isoformat(),
            to_date=c_to.isoformat(),
            events_fetched=3,
            inserted=2,
            updated=0,
            skipped_same=1,
            unmapped=0,
            errors_count=0,
        )

    monkeypatch.setattr(run_backfill, "load_checkpoint", lambda: progress)
    monkeypatch.setattr(
        run_backfill,
        "save_checkpoint",
        lambda p: saved_progress.append({country: keys[:] for country, keys in p.items()}),
    )
    monkeypatch.setattr(run_backfill, "count_stored_chunk_rows", fake_count_stored_chunk_rows)
    monkeypatch.setattr(run_backfill, "backfill_one_chunk", fake_backfill_one_chunk)
    monkeypatch.setattr(run_backfill, "EODHDClient", FakeEODHDClient)
    monkeypatch.setattr(run_backfill.Canonicalizer, "from_default_config", lambda: object())
    monkeypatch.setattr(run_backfill, "IngestService", lambda canonicalizer: object())

    result = await run_backfill.main_async(
        Namespace(
            from_date="2020-01-01",
            to_date="2020-06-30",
            reset=False,
            countries=["AU"],
            no_checkpoint_db_validation=False,
        )
    )

    assert result == 0
    assert fetched_chunks == [("AU", date(2020, 1, 1), date(2020, 6, 30))]
    assert progress["AU"] == ["2020-01-01_2020-06-30"]
    assert saved_progress[0] == {"AU": []}
    assert saved_progress[-1] == {"AU": ["2020-01-01_2020-06-30"]}


@pytest.mark.asyncio
async def test_valid_checkpoint_chunk_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    progress = {"AU": ["2020-01-01_2020-06-30"]}
    fetched_chunks: list[tuple[str, date, date]] = []

    async def fake_count_stored_chunk_rows(country: str, c_from: date, c_to: date) -> int:
        return 12

    async def fake_backfill_one_chunk(client, service, country: str, c_from: date, c_to: date):
        fetched_chunks.append((country, c_from, c_to))
        raise AssertionError("valid checkpoint chunks should not be fetched")

    monkeypatch.setattr(run_backfill, "load_checkpoint", lambda: progress)
    monkeypatch.setattr(run_backfill, "save_checkpoint", lambda p: None)
    monkeypatch.setattr(run_backfill, "count_stored_chunk_rows", fake_count_stored_chunk_rows)
    monkeypatch.setattr(run_backfill, "backfill_one_chunk", fake_backfill_one_chunk)
    monkeypatch.setattr(run_backfill, "EODHDClient", FakeEODHDClient)
    monkeypatch.setattr(run_backfill.Canonicalizer, "from_default_config", lambda: object())
    monkeypatch.setattr(run_backfill, "IngestService", lambda canonicalizer: object())

    result = await run_backfill.main_async(
        Namespace(
            from_date="2020-01-01",
            to_date="2020-06-30",
            reset=False,
            countries=["AU"],
            no_checkpoint_db_validation=False,
        )
    )

    assert result == 0
    assert fetched_chunks == []
