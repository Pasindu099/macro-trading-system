"""Freshness boundaries, visible labels, and stale situation protection."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from jinja2 import Environment, FileSystemLoader

from app.services import freshness, situations


NOW = datetime(2026, 10, 13, 12, tzinfo=UTC)


@pytest.mark.parametrize("source,fresh,stale", [
    ("fx_spot", date(2026, 10, 9), date(2026, 10, 8)),
    ("government_yields", date(2026, 10, 9), date(2026, 10, 8)),
    ("fred_daily", date(2026, 10, 7), date(2026, 10, 6)),
    ("calendar", date(2026, 10, 12), date(2026, 10, 11)),
    ("cot", date(2026, 10, 3), date(2026, 10, 2)),
    ("news", NOW - timedelta(hours=6), NOW - timedelta(hours=6, minutes=1)),
])
def test_source_threshold_boundaries(source, fresh, stale):
    assert freshness.status_for(source, fresh, now=NOW)["stale"] is False
    result = freshness.status_for(source, stale, now=NOW)
    assert result["stale"] is True
    assert "limit" in result["reason"]


def test_badge_shows_as_of_and_visible_stale_reason():
    environment = Environment(loader=FileSystemLoader(Path("app/web/templates")), autoescape=True)
    row = freshness.status_for("news", NOW - timedelta(hours=7), now=NOW)
    html = environment.get_template("_freshness.html").render(rows=[row])
    assert "as of 13 Oct 2026 05:00 UTC" in html
    assert ">stale</strong>" in html
    assert "7 hours old; limit 6 hours" in html


def test_us_holiday_and_publication_lag():
    assert date(2026, 10, 12) in freshness.us_federal_holidays(2026)
    assert freshness.business_days_since(date(2026, 10, 9), NOW.date(), "US") == 1
    row = freshness.status_for("fred_daily", date(2026, 10, 9), now=NOW)
    assert row["age"] == 0 and not row["stale"]


def test_weekend_and_market_calendar():
    assert freshness.business_days_since(date(2026, 10, 9), date(2026, 10, 11), "US") == 0
    assert freshness.business_days_since(date(2026, 10, 9), NOW.date(), "FX") == 2
    assert freshness.business_days_since(date(2026, 10, 9), NOW.date(), "CA") == 1
    assert freshness.status_for("government_yields", date(2026, 10, 8),
                                now=NOW, market="US")["stale"] is False


def test_derived_uses_successful_run_not_data_date():
    row = freshness.status_for("macro_state", date(2026, 9, 1), now=NOW,
                               updated_at=NOW - timedelta(hours=2))
    assert row["stale"] is False
    html = Environment(loader=FileSystemLoader(Path("app/web/templates"))).get_template(
        "_freshness.html").render(rows=[row])
    assert "updated 13 Oct 2026 10:00 UTC; data as of 01 Sep 2026" in html
    assert freshness.status_for("macro_state", date(2026, 10, 13), now=NOW)["stale"] is True


@pytest.mark.asyncio
async def test_stale_fx_does_not_replace_situation_episodes(monkeypatch):
    executed = []
    class Session:
        async def execute(self, query, params=None):
            executed.append(str(query))
    @asynccontextmanager
    async def session_scope(**_kwargs):
        yield Session()
    @asynccontextmanager
    async def job_lock(_name):
        yield True
    @asynccontextmanager
    async def run_logger(_name):
        yield SimpleNamespace(record_rows=lambda _count: None, mark_skipped=lambda: None)
    async def backtest_all():
        return [{"situation_id": "intervention_risk_jpy", "scope": "pair",
                 "scope_key": "USD/JPY", "data_available": True,
                 "episodes": [{"started_at": date(2026, 10, 1), "ended_at": None,
                               "evidence": {"current": {"usd_jpy": 159.0}}}]}]
    async def data_freshness(_session):
        return {source: {"stale": source == "fx_spot"} for source in freshness.QUERIES}
    monkeypatch.setattr(situations, "session_scope", session_scope)
    monkeypatch.setattr(situations, "job_lock", job_lock)
    monkeypatch.setattr(situations, "run_logger", run_logger)
    monkeypatch.setattr(situations, "backtest_all", backtest_all)
    monkeypatch.setattr(freshness, "get_data_freshness", data_freshness)
    assert await situations.run_situations_job() == 0
    assert not any("DELETE FROM situation_episodes" in query or
                   "INSERT INTO situation_episodes" in query for query in executed)
