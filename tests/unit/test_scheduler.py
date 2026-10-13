from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest

from app.ingestion import scheduler as scheduler_module
from app.ingestion.scheduler import (
    POST_RELEASE_LOOKBACK_DAYS,
    _add_minutes,
    _build_cron_trigger,
    _resolve_post_release_lookback_days,
)


def test_add_minutes_wraps_across_midnight() -> None:
    assert _add_minutes(23, 50, 20) == (0, 10)


def test_build_cron_trigger_applies_delay_and_schedule_fields() -> None:
    trigger = _build_cron_trigger({
        "name": "US Non-Farm Payrolls",
        "country": "US",
        "day_of_week": 4,
        "day_of_month_range": "1-7",
        "scheduled_at_utc": "12:30",
        "trigger_delay_minutes": 15,
    })
    fields = {field.name: str(field) for field in trigger.fields}

    assert fields["day_of_week"] == "4"
    assert fields["day"] == "1-7"
    assert fields["hour"] == "12"
    assert fields["minute"] == "45"


def test_resolve_post_release_lookback_days_uses_default() -> None:
    assert _resolve_post_release_lookback_days({}) == POST_RELEASE_LOOKBACK_DAYS


def test_resolve_post_release_lookback_days_allows_override() -> None:
    assert _resolve_post_release_lookback_days({"lookback_days": "10"}) == 10


@pytest.mark.parametrize("value", [0, -1, "abc", None])
def test_resolve_post_release_lookback_days_rejects_invalid_values(
    value: object,
) -> None:
    with pytest.raises(ValueError):
        _resolve_post_release_lookback_days({"lookback_days": value})


@pytest.mark.asyncio
async def test_missed_daily_run_catches_up_sources_before_derived(monkeypatch):
    calls = []
    class Result:
        def scalar_one(self):
            return datetime.now(UTC) - timedelta(days=3)
    class Session:
        async def execute(self, _query):
            return Result()
    @asynccontextmanager
    async def session_scope():
        yield Session()
    @asynccontextmanager
    async def job_lock(_name):
        yield True
    async def record(name, *args, **kwargs):
        calls.append(name)
    monkeypatch.setattr(scheduler_module, "session_scope", session_scope)
    monkeypatch.setattr(scheduler_module, "job_lock", job_lock)
    scheduler = scheduler_module.Scheduler()
    monkeypatch.setattr(scheduler, "_run_government_yield_incremental", lambda **kw: record("yields_fx", **kw))
    monkeypatch.setattr(scheduler, "_run_scheduled_session", lambda *a, **kw: record("calendar", *a, **kw))
    for name in ("ingest_fred_series", "run_cot_weekly", "run_rates_derived",
                 "run_incremental_event_innovation", "run_macro_state_chain",
                 "run_situations_job", "run_dominance_job"):
        monkeypatch.setattr(scheduler_module, name, lambda *a, name=name, **kw: record(name, *a, **kw))
    await scheduler._catch_up_after_restart()
    assert calls == ["yields_fx", "ingest_fred_series", "calendar", "run_cot_weekly",
                     "run_rates_derived", "run_incremental_event_innovation",
                     "run_macro_state_chain", "run_situations_job", "run_dominance_job"]
