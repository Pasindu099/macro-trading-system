"""Legacy parser, separate report storage, idempotency and live coverage."""
from datetime import date

import pytest
from sqlalchemy import text

from app.db.session import dispose_engine, get_sessionmaker
from app.services import cot_legacy as legacy
from app.services.positioning_values import load_group_positions


def test_parser_matches_official_field_names_and_rejects_missing_values():
    row = {"cftc_contract_market_code":"099741","report_date_as_yyyy_mm_dd":"2026-10-20T00:00:00.000",
        "open_interest_all":"1,000","noncomm_positions_long_all":"300","noncomm_positions_short_all":"100",
        "noncomm_postions_spread_all":"25"}
    parsed = legacy.parse_rows([row],{"099741":"EUR"})
    assert parsed[0]["long"] == 300 and parsed[0]["open_interest"] == 1000
    assert parsed[0]["report_date"] == date(2026,10,20)
    del row["noncomm_positions_long_all"]
    with pytest.raises(ValueError): legacy.parse_rows([row],{"099741":"EUR"})


@pytest.mark.asyncio
async def test_separate_legacy_storage_and_idempotent_upserts():
    async with get_sessionmaker()() as session:
        before = (await session.execute(text("SELECT count(*) FROM cot_positions"))).scalar_one()
        row = {"report_date": date(1900,1,2),"contract_code":"099741","currency":"EUR","long":300,"short":100,"spreading":25,"open_interest":1000}
        try:
            assert await legacy.upsert(session,[row]) == 1
            assert await legacy.upsert(session,[row]) == 0
            assert await legacy.upsert(session,[{**row,"long":301}]) == 1
            stored = (await session.execute(text("SELECT report_type FROM cot_legacy_positions WHERE report_date='1900-01-02'"))).scalar_one()
            assert stored == "legacy_futures_only"
            assert (await session.execute(text("SELECT count(*) FROM cot_positions"))).scalar_one() == before
        finally: await session.rollback()
    await dispose_engine()


@pytest.mark.asyncio
async def test_legacy_loader_keeps_noncommercials_separate():
    groups = await load_group_positions("legacy_noncommercial",["EUR"])
    assert "legacy_noncommercial" in groups["EUR"] and "leveraged_funds" not in groups["EUR"]
    assert groups["EUR"]["legacy_noncommercial"][0].report_date == date(2010,1,5)
    await dispose_engine()


@pytest.mark.asyncio
async def test_retry_skips_already_published_report(monkeypatch):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock
    session, run = AsyncMock(), MagicMock()
    result = MagicMock(); result.scalar_one.return_value = date(2026,10,20)
    session.execute.return_value = result
    @asynccontextmanager
    async def lock(name): yield True
    @asynccontextmanager
    async def logger(name): yield run
    @asynccontextmanager
    async def scope(): yield session
    monkeypatch.setattr(legacy,"job_lock",lock)
    monkeypatch.setattr(legacy,"run_logger",logger)
    monkeypatch.setattr(legacy,"session_scope",scope)
    fetch = AsyncMock(); monkeypatch.setattr(legacy,"ingest",fetch)
    assert (await legacy.run_cot_legacy_weekly(retry=True,today=date(2026,10,26)))["status"] == "skipped"
    fetch.assert_not_awaited(); run.mark_skipped.assert_called_once()
