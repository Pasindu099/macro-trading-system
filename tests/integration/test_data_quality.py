"""Read-only data-quality SQL and health composition."""
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from app.db.session import dispose_engine, get_sessionmaker
from app.services import data_quality as quality


@pytest.mark.parametrize("stale,run,options,expected", [
    (False, "success", {}, "fresh"), (True, "success", {}, "stale"),
    (False, "failed", {}, "error"), (False, "timeout", {}, "error"),
    (True, "manual_import", {}, "manual import"),
    (True, "failed", {"manual": True}, "manual import"),
    (False, "success", {"enabled": False}, "disabled"),
])
def test_health_statuses(stale, run, options, expected):
    assert quality.health_status(stale, run, **options) == expected


def test_errors_are_redacted():
    result = quality.safe_error({"password": "top-secret", "message": "api_token=secret-token & Authorization: Bearer abcdefghijkl",
                                 "nested": {"api_key": "secret-value"}, "url": "postgresql://user:pass@host/db"})
    assert all(secret not in result for secret in ("top-secret", "secret-token", "abcdefghijkl", "secret-value", "user:pass"))


def test_business_day_gaps_exclude_weekends_and_holidays():
    assert quality.missing_business_days(date(2026, 12, 24), {date(2026, 12, 24)},
                                        date(2026, 12, 1), date(2026, 12, 28), "US") == [date(2026, 12, 28)]


@pytest.mark.asyncio
async def test_source_health_manual_disabled_next_run(monkeypatch):
    now = datetime(2026, 10, 28, tzinfo=UTC)
    monkeypatch.setattr(quality.freshness, "get_data_freshness", AsyncMock(return_value={
        "fx_spot": {"label": "FX", "stale": False, "as_of": "2026-10-28"}}))
    monkeypatch.setattr(quality.job_status, "get_job_status", AsyncMock(return_value=[{
        "run_type": "job:fx_spot", "last_run": {"status": "failed", "started_at": now},
        "last_success": now, "last_error": {"api_key": "hide-me"}}]))
    monkeypatch.setattr(quality.job_status, "get_source_status", AsyncMock(return_value={}))
    monkeypatch.setattr(quality.freshness, "manual_import_rows", AsyncMock(return_value=[{
        "source": "cb_manual:RBNZ:speeches", "label": "RBNZ speeches", "as_of": None}]))
    monkeypatch.setattr(quality.news_collector, "load_config", lambda: {"sources": {
        "kitco": {"label": "Kitco", "enabled": False}}})
    session = AsyncMock()
    result = SimpleNamespace(all=lambda: [], mappings=lambda: SimpleNamespace(first=lambda: None))
    session.execute.return_value = result
    future = now + timedelta(hours=4)
    scheduler = SimpleNamespace(get_jobs=lambda: [SimpleNamespace(id="fx_spot", next_run_time=future)])
    result = await quality.source_health(session, now=now, scheduler=scheduler)
    fx = result["rows"][0]
    assert fx["status"] == "error" and fx["next_scheduled_run"] == future
    assert "hide-me" not in fx["last_error"]
    assert result["counts"]["manual import"] == result["counts"]["disabled"] == 1


@pytest.mark.asyncio
async def test_live_quality_inventory_and_read_only(monkeypatch):
    monkeypatch.setattr(quality, "unavailable_situations", AsyncMock(return_value=[{
        "kind": "situation", "series": "test", "missing_inputs": ["brent"]}]))
    async with get_sessionmaker()() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        result = await quality.quality_log(session)
        assert (result["through"] - result["since"]).days == 29
        assert set(result) >= {"outliers", "gaps", "revisions", "unmapped_events", "headline_parser", "unavailable_inputs"}
        assert result["unavailable_inputs"][-1]["kind"] == "situation"
        assert result["headline_parser"]["parsed"] <= result["headline_parser"]["total"]
        assert all(row["count"] == len(row["dates"]) for row in result["gaps"])
        health = await quality.source_health(session)
        assert sum(health["counts"].values()) == len(health["rows"])
        assert any(row["status"] == "manual import" for row in health["rows"])
        await session.rollback()
    await dispose_engine()


@pytest.mark.asyncio
async def test_quality_log_records(monkeypatch):
    from uuid import uuid4
    token = "quality_" + uuid4().hex[:12]
    monkeypatch.setattr(quality, "unavailable_situations", AsyncMock(return_value=[]))
    async with get_sessionmaker()() as session:
        top = (await session.execute(text("""
            SELECT COALESCE(raw_payload->>'type', raw_payload->>'event', 'Unknown') AS name, count(*) AS count
            FROM indicator_releases WHERE indicator_id IS NULL AND retrieved_at::date >= current_date-29
            GROUP BY 1 ORDER BY count(*) DESC, 1 LIMIT 1
        """))).mappings().first()
        event_name, baseline = (top["name"], top["count"]) if top else (token, 0)
        await session.execute(text("""
            INSERT INTO fx_spot_observations (provider, provider_symbol, pair, base_currency, quote_currency,
                close_value, observation_date, data_frequency, source_type, quality_status, is_outlier, payload_hash)
            VALUES ('test', :token, 'USD/JPY', 'USD', 'JPY', 999, current_date, 'daily', 'official', 'valid', true, :token)
        """), {"token": token})
        await session.execute(text("""
            INSERT INTO indicator_releases (released_at, raw_payload) VALUES (now(), jsonb_build_object('type', CAST(:token AS text)))
        """), {"token": event_name})
        await session.execute(text("""
            INSERT INTO policy_model_daily (bank, data_date, model_version, status, missing_inputs, inputs, computed_at)
            VALUES ('TEST', current_date, :token, 'unavailable', '["neutral_rate"]', '{}', now())
        """), {"token": token})
        result = await quality.quality_log(session)
        assert any(row["series"] == token for row in result["outliers"])
        assert any(row["name"] == event_name and row["count"] == baseline+1 for row in result["unmapped_events"])
        assert any(row["series"] == "TEST" and row["missing_inputs"] == ["neutral_rate"]
                   for row in result["unavailable_inputs"])
        await session.rollback()
    await dispose_engine()


@pytest.mark.asyncio
async def test_unavailable_situation_current_inputs(monkeypatch):
    monkeypatch.setattr(quality, "load_config", lambda: {"intervention_zone_jpy": [158, 160], "situations": {
        "risk_off": {"trigger": {"field": "vix", "op": "gt", "value": 30}}}})
    monkeypatch.setattr(quality.situations, "load_history", AsyncMock(return_value={"fx": {}, "spreads": {}}))
    monkeypatch.setattr(quality.situations, "scopes", lambda spec, pairs: ["GLOBAL"])
    monkeypatch.setattr(quality.situations, "features", lambda *args: {"vix": None})
    rows = await quality.unavailable_situations(date(2026, 10, 28))
    assert rows == [{"kind": "situation", "series": "risk_off", "scope": "GLOBAL",
                     "date": date(2026, 10, 28), "missing_inputs": ["vix"]}]
