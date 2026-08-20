"""Unit tests for the event-driven policy delta panel feed.

Exercises row assembly and the live decay calculation without a database — the
SQL is covered by running the endpoint, but the shaping is where the logic is.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.event_innovation_feed import (
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    _assemble_rows,
    _bar_width,
    _bundle_label,
    _relative_age,
    _summarize,
    resolve_feed_filters,
)

TODAY = date(2026, 8, 20)


def _record(**overrides):
    base = {
        "score_id": 1,
        "release_id": 100,
        "bundle_id": None,
        "indicator_id": 10,
        "release_date": TODAY,
        "actual": 4.1,
        "consensus": 4.0,
        "surprise_raw": 0.1,
        "surprise_normalized": 1.0,
        "decay_bucket": "high_freq_low_revision",
        "half_life_days": 21.0,
        "scored": True,
        "display_name": "Unemployment Rate",
        "canonical_name": "unemployment_rate",
        "unit": "%",
        "category": "Labor",
        "country_code": "US",
        "currency_code": "USD",
        "country_name": "United States",
        "period": "Jul",
        "bundle_key": None,
        "bundle_score": None,
        "bundle_bucket": None,
        "bundle_half_life": None,
        "bundle_member_count": None,
    }
    base.update(overrides)
    return base


# ── filters ──────────────────────────────────────────────────────────────────


def test_window_defaults_and_clamps():
    assert resolve_feed_filters(None).days == DEFAULT_WINDOW_DAYS
    assert resolve_feed_filters(0).days == 1
    assert resolve_feed_filters(-5).days == 1
    assert resolve_feed_filters(9999).days == MAX_WINDOW_DAYS


def test_country_code_is_normalized():
    assert resolve_feed_filters(14, country_code="us").country_code == "US"
    assert resolve_feed_filters(14).country_code is None


def test_unscored_is_excluded_by_default():
    assert resolve_feed_filters(14).include_unscored is False
    assert resolve_feed_filters(14, include_unscored=True).include_unscored is True


# ── live decay ───────────────────────────────────────────────────────────────


def test_a_fresh_release_retains_its_full_signal():
    rows = _assemble_rows([_record(release_date=TODAY)], TODAY)
    row = rows[0]
    assert row["days_elapsed"] == 0
    assert row["remaining"] == pytest.approx(1.0)
    assert row["current"] == pytest.approx(row["initial"])
    assert row["fill_pct"] == 100
    assert row["age_display"] == "today"


def test_signal_halves_after_one_half_life():
    """The whole point of the panel: decay is applied at read time."""
    released = date(2026, 7, 30)  # 21 days before TODAY
    rows = _assemble_rows(
        [_record(release_date=released, half_life_days=21.0, surprise_normalized=2.0)],
        TODAY,
    )
    row = rows[0]
    assert row["days_elapsed"] == 21
    assert row["remaining"] == pytest.approx(0.5)
    assert row["current"] == pytest.approx(1.0)
    assert row["initial"] == pytest.approx(2.0)
    assert row["fill_pct"] == 50


def test_the_same_row_decays_further_when_asked_later():
    """Identical input, two different 'now' values — the answer must change."""
    record = _record(release_date=date(2026, 8, 10), surprise_normalized=2.0)
    early = _assemble_rows([record], date(2026, 8, 12))[0]
    late = _assemble_rows([record], date(2026, 8, 30))[0]
    assert abs(late["current"]) < abs(early["current"])
    assert late["initial"] == early["initial"]


def test_meeting_adjacent_row_does_not_decay():
    rows = _assemble_rows(
        [
            _record(
                release_date=date(2026, 1, 1),
                half_life_days=None,
                decay_bucket="meeting_adjacent",
                surprise_normalized=1.5,
            )
        ],
        TODAY,
    )
    row = rows[0]
    assert row["does_not_decay"] is True
    assert row["remaining"] == pytest.approx(1.0)
    assert row["current"] == pytest.approx(1.5)
    assert row["is_spent"] is False
    assert row["half_life_display"] == "no decay"


def test_long_dead_signal_is_flagged_spent():
    rows = _assemble_rows(
        [_record(release_date=date(2026, 6, 1), half_life_days=10.0)], TODAY
    )
    assert rows[0]["is_spent"] is True


def test_future_dated_release_is_not_amplified():
    rows = _assemble_rows([_record(release_date=date(2026, 9, 1))], TODAY)
    assert rows[0]["days_elapsed"] == 0
    assert rows[0]["remaining"] == pytest.approx(1.0)


# ── direction ────────────────────────────────────────────────────────────────


def test_positive_is_hawkish_and_negative_is_dovish():
    hawk = _assemble_rows([_record(surprise_normalized=1.2)], TODAY)[0]
    dove = _assemble_rows([_record(surprise_normalized=-1.2)], TODAY)[0]
    assert hawk["direction"] == "hawkish"
    assert hawk["direction_class"] == "is-hawkish"
    assert dove["direction"] == "dovish"
    assert dove["direction_class"] == "is-dovish"


def test_exactly_in_line_is_neutral_and_still_draws_a_bar():
    row = _assemble_rows([_record(surprise_normalized=0.0)], TODAY)[0]
    assert row["direction"] == "neutral"
    # Floored so an in-line print reads as "no surprise", not a broken row.
    assert row["track_width"] >= 2


def test_bar_width_scales_and_saturates():
    assert _bar_width(0.0) == 2
    assert _bar_width(1.5) == 50
    assert _bar_width(3.0) == 100
    assert _bar_width(6.0) == 100
    assert _bar_width(-3.0) == 100


# ── bundle collapsing ────────────────────────────────────────────────────────


def _nfp_day_records():
    common = {
        "bundle_id": 7,
        "bundle_key": "US_NFP_DAY",
        "bundle_score": 1.4,
        "bundle_bucket": "high_freq_high_revision",
        "bundle_half_life": 10.0,
        "bundle_member_count": 3,
        "release_date": TODAY,
    }
    return [
        _record(release_id=1, indicator_id=1, display_name="Nonfarm Payrolls",
                surprise_normalized=1.8, **common),
        _record(release_id=2, indicator_id=2, display_name="Unemployment Rate",
                surprise_normalized=-0.4, **common),
        _record(release_id=3, indicator_id=3, display_name="Average Hourly Earnings",
                surprise_normalized=0.9, **common),
    ]


def test_nfp_day_renders_one_row_not_three():
    rows = _assemble_rows(_nfp_day_records(), TODAY)
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == "bundle"
    assert row["name"] == "NFP Day"
    assert row["detail"] == "3 indicators"
    # The bundle's own score leads, not any single member's.
    assert row["initial"] == pytest.approx(1.4)
    assert len(row["members"]) == 3


def test_bundle_members_are_ordered_by_absolute_impact():
    row = _assemble_rows(_nfp_day_records(), TODAY)[0]
    names = [member["display_name"] for member in row["members"]]
    assert names == [
        "Nonfarm Payrolls",         # |1.8|
        "Average Hourly Earnings",  # |0.9|
        "Unemployment Rate",        # |0.4|
    ]


def test_bundle_uses_the_bundle_half_life_not_a_members():
    row = _assemble_rows(_nfp_day_records(), TODAY)[0]
    assert row["half_life_days"] == pytest.approx(10.0)
    assert row["decay_bucket"] == "high_freq_high_revision"


def test_unbundled_releases_stay_separate_rows():
    records = _nfp_day_records() + [
        _record(release_id=99, indicator_id=9, display_name="Initial Jobless Claims")
    ]
    rows = _assemble_rows(records, TODAY)
    assert len(rows) == 2
    assert {row["kind"] for row in rows} == {"bundle", "release"}


def test_row_without_a_usable_score_is_dropped():
    assert _assemble_rows([_record(surprise_normalized=None)], TODAY) == []
    assert _assemble_rows([_record(bundle_id=5, bundle_score=None)], TODAY) == []


def test_single_row_detail_carries_the_period():
    """Two same-day prints of one indicator differ only by period."""
    row = _assemble_rows([_record(category="Growth", period="Q2")], TODAY)[0]
    assert "Q2" in row["detail"]
    assert "Growth" in row["detail"]


# ── labels and summary ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "key,expected",
    [
        ("US_NFP_DAY", "NFP Day"),
        ("US_CPI_DAY", "CPI Day"),
        ("UK_LABOUR_DAY", "Labour Day"),
        ("EU_HICP_DAY", "HICP Day"),
        ("US_PCE_DAY", "PCE Day"),
        (None, "Bundle"),
    ],
)
def test_bundle_label_drops_the_country_and_keeps_acronyms(key, expected):
    assert _bundle_label(key) == expected


@pytest.mark.parametrize(
    "days,expected",
    [(0, "today"), (1, "yesterday"), (2, "2 days ago"), (14, "14 days ago")],
)
def test_relative_age(days, expected):
    assert _relative_age(days) == expected


def test_summary_counts_direction_and_nets_the_surviving_signal():
    rows = _assemble_rows(
        [
            _record(release_id=1, indicator_id=1, surprise_normalized=1.0,
                    release_date=TODAY),
            _record(release_id=2, indicator_id=2, surprise_normalized=-0.5,
                    release_date=TODAY),
        ],
        TODAY,
    )
    summary = _summarize(rows)
    assert summary["total"] == 2
    assert summary["hawkish"] == 1
    assert summary["dovish"] == 1
    # Both fresh, so the net is the undecayed sum.
    assert summary["net"] == pytest.approx(0.5)
    assert summary["net_label"] == "Impulse broadly balanced"


def test_summary_net_label_turns_hawkish_on_a_strong_net():
    rows = _assemble_rows(
        [_record(surprise_normalized=2.0, release_date=TODAY)], TODAY
    )
    assert _summarize(rows)["net_label"] == "Net hawkish impulse"
    assert _summarize(rows)["net_class"] == "is-hawkish"


def test_empty_feed_summarizes_without_crashing():
    summary = _summarize([])
    assert summary["total"] == 0
    assert summary["net_display"] == "+0.00σ"
    assert summary["net_label"] == "Impulse broadly balanced"
