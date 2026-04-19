from __future__ import annotations

import pytest

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
