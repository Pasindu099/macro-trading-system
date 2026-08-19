"""Unit tests for the release ledger's window resolution and surprise scoring."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.release_ledger import (
    INLINE_Z_THRESHOLD,
    MAX_ABS_Z,
    MIN_SURPRISE_SAMPLES,
    _build_row,
    _category_pulse,
    _summarize,
    _surprise_stdevs,
    _surprise_timeseries,
    resolve_filters,
)

TODAY = date(2026, 8, 11)


def _record(**overrides):
    base = {
        "release_id": 1,
        "indicator_id": 10,
        "period": "Jul",
        "period_start_date": date(2026, 7, 1),
        "released_at": datetime(2026, 8, 7, 12, 30, tzinfo=timezone.utc),
        "actual": 1.0,
        "estimate": 1.0,
        "previous": 1.0,
        "canonical_name": "unemployment_rate",
        "display_name": "Unemployment Rate",
        "primary_category": "Labor",
        "unit": "%",
        "importance": 1,
        "higher_is_better": False,
        "country_code": "AU",
    }
    base.update(overrides)
    return base


# ── window resolution ────────────────────────────────────────────────────────


def test_month_resolves_to_full_calendar_month():
    filters = resolve_filters("au", month="2026-04", today=TODAY)
    assert filters.date_from == date(2026, 4, 1)
    assert filters.date_to == date(2026, 4, 30)
    assert filters.window_label == "April 2026"
    assert filters.month == "2026-04"


def test_december_month_end_does_not_roll_into_next_year():
    filters = resolve_filters("au", month="2025-12", today=TODAY)
    assert filters.date_to == date(2025, 12, 31)


def test_month_wins_over_range_so_a_stale_range_cannot_leak_through():
    filters = resolve_filters("au", month="2026-04", range_key="6m", today=TODAY)
    assert filters.date_from == date(2026, 4, 1)
    assert filters.range_key is None


def test_current_month_is_clamped_to_today_not_month_end():
    filters = resolve_filters("au", month="2026-08", today=TODAY)
    assert filters.date_to == TODAY


def test_unknown_range_falls_back_to_30d():
    filters = resolve_filters("au", range_key="banana", today=TODAY)
    assert filters.date_from == date(2026, 7, 12)


def test_malformed_month_falls_back_to_range():
    filters = resolve_filters("au", month="not-a-month", range_key="ytd", today=TODAY)
    assert filters.date_from == date(2026, 1, 1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("1", 1), ("3", 3), ("all", None), (None, None), ("9", None), ("x", None)],
)
def test_importance_parsing(raw, expected):
    assert resolve_filters("au", importance=raw, today=TODAY).max_importance == expected


def test_category_all_means_no_filter():
    assert resolve_filters("au", category="all", today=TODAY).category is None
    assert resolve_filters("au", category="Inflation", today=TODAY).category == "Inflation"


# ── surprise scoring ─────────────────────────────────────────────────────────


def test_stdev_needs_a_minimum_sample_before_scoring():
    thin = [_record(actual=1.0 + i, estimate=1.0) for i in range(MIN_SURPRISE_SAMPLES - 1)]
    assert _surprise_stdevs(thin) == {}

    enough = [_record(actual=1.0 + i, estimate=1.0) for i in range(MIN_SURPRISE_SAMPLES)]
    assert 10 in _surprise_stdevs(enough)


def test_indicator_that_never_deviates_gets_no_score_instead_of_dividing_by_zero():
    records = [_record(actual=1.0, estimate=1.0) for _ in range(12)]
    assert _surprise_stdevs(records) == {}


def test_missing_consensus_is_excluded_from_the_distribution():
    records = [_record(estimate=None) for _ in range(12)]
    assert _surprise_stdevs(records) == {}


def test_score_is_flipped_for_indicators_where_lower_is_better():
    """A hotter-than-expected unemployment print is currency-negative."""
    row = _build_row(
        _record(actual=5.6, estimate=5.4, higher_is_better=False),
        {10: 0.2},
    )
    assert row["score"] == pytest.approx(-1.0)
    assert row["score_class"] == "is-negative"


def test_score_keeps_its_sign_where_higher_is_better():
    row = _build_row(
        _record(actual=5.6, estimate=5.4, higher_is_better=True, primary_category="Growth"),
        {10: 0.2},
    )
    assert row["score"] == pytest.approx(1.0)
    assert row["score_class"] == "is-positive"


def test_outlier_scores_are_clamped():
    row = _build_row(_record(actual=100.0, estimate=1.0, higher_is_better=True), {10: 0.1})
    assert row["score"] == MAX_ABS_Z
    assert row["score_width"] == 50


def test_row_without_consensus_has_no_score_but_still_shows_direction():
    row = _build_row(
        _record(actual=5.6, estimate=None, previous=5.4, higher_is_better=False),
        {},
    )
    assert row["score"] is None
    assert row["score_display"] == "—"
    assert row["has_estimate"] is False
    # Unemployment rising is currency-negative even with no consensus to score.
    assert row["change_class"] == "is-negative"


def test_change_class_is_neutral_without_a_prior_print():
    row = _build_row(_record(actual=5.6, estimate=None, previous=None), {})
    assert row["change_class"] == "is-neutral"


# ── aggregation ──────────────────────────────────────────────────────────────


def _scored_row(score, category="Labor"):
    return {"score": score, "category": category}


def test_summary_counts_beats_misses_and_inline_around_the_threshold():
    rows = [
        _scored_row(1.5),
        _scored_row(INLINE_Z_THRESHOLD),      # exactly at threshold counts as a beat
        _scored_row(0.1),
        _scored_row(-INLINE_Z_THRESHOLD),     # and as a miss on the way down
        _scored_row(-2.0),
        {"score": None, "category": "Labor"},
    ]
    summary = _summarize(rows)
    assert (summary["total"], summary["scored"], summary["unscored"]) == (6, 5, 1)
    assert (summary["beats"], summary["inline"], summary["misses"]) == (2, 1, 2)


def test_summary_of_an_empty_window_does_not_divide_by_zero():
    summary = _summarize([])
    assert summary["average_score"] is None
    assert summary["average_display"] == "—"
    assert summary["pulse_label"] == "No scored releases"


def _ts_row(score, released):
    return {"score": score, "released_at": released, "category": "Labor"}


def test_short_windows_bucket_weekly_and_long_windows_bucket_monthly():
    weekly_filters = resolve_filters("au", range_key="30d", today=TODAY)
    monthly_filters = resolve_filters("au", range_key="ytd", today=TODAY)
    rows = [
        _ts_row(1.0, datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)),
        _ts_row(-1.0, datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)),
    ]

    weekly = _surprise_timeseries(rows, weekly_filters)
    monthly = _surprise_timeseries(rows, monthly_filters)

    assert weekly[0]["label"] == "20 Jul"       # ISO week start (a Monday)
    assert monthly[0]["label"] == "Jul 2026"
    assert len(monthly) == 2


def test_gaps_in_the_release_schedule_stay_visible_as_empty_buckets():
    """A quiet month must render as a gap, not be closed up into its neighbour."""
    filters = resolve_filters("au", range_key="ytd", today=TODAY)
    rows = [
        _ts_row(1.0, datetime(2026, 3, 3, tzinfo=timezone.utc)),
        _ts_row(-1.0, datetime(2026, 6, 3, tzinfo=timezone.utc)),
    ]
    series = _surprise_timeseries(rows, filters)

    assert [point["label"] for point in series] == [
        "Mar 2026", "Apr 2026", "May 2026", "Jun 2026",
    ]
    assert [point["average"] for point in series] == [1.0, None, None, -1.0]
    assert series[1]["count"] == 0


def test_timeseries_ignores_unscored_rows_and_empty_input():
    filters = resolve_filters("au", range_key="3m", today=TODAY)
    assert _surprise_timeseries([], filters) == []
    unscored = [_ts_row(None, datetime(2026, 7, 20, tzinfo=timezone.utc))]
    assert _surprise_timeseries(unscored, filters) == []


def test_december_monthly_bucket_advances_into_the_next_year():
    # A YTD window early in the year is short enough to bucket weekly, so reach
    # for a date far enough in that the window crosses the monthly threshold.
    filters = resolve_filters("au", range_key="ytd", today=date(2027, 6, 1))
    rows = [
        _ts_row(1.0, datetime(2026, 12, 3, tzinfo=timezone.utc)),
        _ts_row(-1.0, datetime(2027, 1, 3, tzinfo=timezone.utc)),
    ]
    series = _surprise_timeseries(rows, filters)
    assert [point["label"] for point in series] == ["Dec 2026", "Jan 2027"]


def test_category_pulse_orders_by_strength_of_bias_not_sign():
    rows = [
        _scored_row(0.2, "Growth"),
        _scored_row(-2.0, "Inflation"),
        _scored_row(1.0, "Labor"),
    ]
    pulse = _category_pulse(rows)
    assert [item["category"] for item in pulse] == ["Inflation", "Labor", "Growth"]
    assert pulse[0]["average_display"] == "-2.00σ"
