"""Unit tests for the event-driven policy delta panel feed.

Exercises row assembly and the live decay calculation without a database — the
SQL is covered by running the endpoint, but the shaping is where the logic is.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.event_innovation_feed import (
    CATEGORY_FILTERS,
    DEFAULT_WINDOW_DAYS,
    MAX_WINDOW_DAYS,
    _assemble_rows,
    _bar_width,
    _bundle_label,
    _relative_age,
    _summarize,
    resolve_primaries,
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
    assert row["half_life_display"] == "does not decay"


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
    assert hawk["tone"] == "hawk"
    assert hawk["is_spent"] is False
    assert dove["tone"] == "dove"
    assert dove["is_spent"] is False


def test_exactly_in_line_is_flat_and_still_draws_a_bar():
    row = _assemble_rows([_record(surprise_normalized=0.0)], TODAY)[0]
    assert row["tone"] == "flat"
    # Floored so an in-line print reads as "no surprise", not a broken row.
    assert row["magnitude_pct"] >= 2


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
    assert row["bundle_badge"] == "3 indicators · bundled"
    # The bundle's own score leads, not any single member's.
    assert row["initial"] == pytest.approx(1.4)
    assert len(row["children"]) == 3


def test_bundle_members_are_ordered_by_absolute_impact():
    row = _assemble_rows(_nfp_day_records(), TODAY)[0]
    names = [child["name"] for child in row["children"]]
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
    assert row["period"] == "Q2"
    assert row["category"] == "Growth"


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
    assert summary["net_label"] == "balanced"


def test_summary_net_label_turns_hawkish_on_a_strong_net():
    rows = _assemble_rows(
        [_record(surprise_normalized=2.0, release_date=TODAY)], TODAY
    )
    assert _summarize(rows)["net_label"] == "hawkish"
    assert _summarize(rows)["net_tone"] == "hawk"


def test_empty_feed_summarizes_without_crashing():
    summary = _summarize([])
    assert summary["total"] == 0
    assert summary["net_display"] == "+0.00σ"
    assert summary["net_label"] == "balanced"


# ── member states ────────────────────────────────────────────────────────────

PRIMARIES = {"USD": "US", "EUR": "EU", "GBP": "UK"}


def test_euro_area_aggregate_is_not_labelled_a_member_state():
    row = _assemble_rows(
        [_record(country_code="EU", currency_code="EUR", country_name="Eurozone")],
        TODAY,
        PRIMARIES,
    )[0]
    assert row["is_member_state"] is False
    assert row["country_label"] is None
    assert row["currency_code"] == "EUR"


@pytest.mark.parametrize(
    "code,name", [("DE", "Germany"), ("FR", "France")]
)
def test_member_state_prints_keep_the_currency_but_gain_a_country_label(code, name):
    """German CPI is a real EUR signal, but it is not the euro-area print.

    Without the label it renders identically to the aggregate and reads as a
    duplicated row.
    """
    row = _assemble_rows(
        [_record(country_code=code, currency_code="EUR", country_name=name)],
        TODAY,
        PRIMARIES,
    )[0]
    assert row["is_member_state"] is True
    assert row["country_label"] == name
    assert row["currency_code"] == "EUR"


def test_single_country_currencies_are_never_member_states():
    row = _assemble_rows([_record()], TODAY, PRIMARIES)[0]
    assert row["is_member_state"] is False
    assert row["country_label"] is None


def test_missing_primary_map_does_not_mislabel_everything():
    """No primaries loaded must not turn every row into a member state."""
    row = _assemble_rows([_record(country_code="DE", currency_code="EUR")], TODAY, {})[0]
    assert row["is_member_state"] is False


# ── category filtering ───────────────────────────────────────────────────────


def test_category_filter_is_case_insensitive_and_rejects_unknown():
    assert resolve_feed_filters(14, category="inflation").category == "Inflation"
    assert resolve_feed_filters(14, category="Monetary Policy").category == "Monetary Policy"
    assert resolve_feed_filters(14, category="all").category is None
    assert resolve_feed_filters(14, category=None).category is None
    # A stale bookmark degrades to the unfiltered panel rather than 404ing.
    assert resolve_feed_filters(14, category="nonsense").category is None


def test_every_offered_chip_resolves_to_itself():
    for name in CATEGORY_FILTERS:
        assert resolve_feed_filters(14, category=name).category == name


def test_bundle_category_comes_from_its_heaviest_member():
    records = _nfp_day_records()
    records[0]["category"] = "Labor"        # payrolls, |1.8| — the anchor
    records[1]["category"] = "Inflation"
    records[2]["category"] = "Inflation"
    row = _assemble_rows(records, TODAY)[0]
    assert row["category"] == "Labor"


# ── children ─────────────────────────────────────────────────────────────────


def test_child_rows_carry_the_verifiable_numbers():
    row = _assemble_rows([_record(actual=4.1, consensus=4.0, surprise_raw=0.1)], TODAY)[0]
    child = row["children"][0]
    assert child["actual_display"] == "4.1 %"
    assert child["consensus_display"] == "4 %"
    assert child["surprise_display"] == "+0.1 %"
    assert child["decay_bucket_label"] == "High freq low revision"


def test_a_standalone_row_still_has_one_child_to_expand():
    row = _assemble_rows([_record()], TODAY)[0]
    assert len(row["children"]) == 1
    assert row["children"][0]["name"] == "Unemployment Rate"


def test_child_decay_matches_its_own_release_not_the_parents():
    records = _nfp_day_records()
    records[1]["release_date"] = TODAY  # all same day in this fixture
    row = _assemble_rows(records, TODAY)[0]
    assert all(child["fill_pct"] == 100 for child in row["children"])


def test_shared_currency_resolves_to_the_bloc_not_a_member_state():
    """The production shape: EUR is held by DE, EU and FR.

    A dev database with only the eight majors never hits this branch, so it is
    tested directly rather than through the query.
    """
    primaries = resolve_primaries(
        {"EUR": ["DE", "EU", "FR"], "USD": ["US"], "GBP": ["UK"]}
    )
    assert primaries["EUR"] == "EU"
    assert primaries["USD"] == "US"
    assert primaries["GBP"] == "UK"


def test_unknown_shared_currency_picks_a_stable_primary():
    """No mapping entry must still leave exactly one non-member-state."""
    primaries = resolve_primaries({"XCD": ["AG", "LC", "VC"]})
    assert primaries["XCD"] == "AG"
    # Deterministic regardless of the order the query returned.
    assert resolve_primaries({"XCD": ["VC", "AG", "LC"]})["XCD"] == "AG"


def test_production_country_set_labels_only_de_and_fr():
    """End-to-end over the real 10-country production shape."""
    primaries = resolve_primaries(
        {"USD": ["US"], "EUR": ["DE", "EU", "FR"], "GBP": ["UK"], "JPY": ["JP"],
         "AUD": ["AU"], "NZD": ["NZ"], "CAD": ["CA"], "CHF": ["CH"]}
    )
    labelled = []
    for code, name in (("EU", "Eurozone"), ("DE", "Germany"), ("FR", "France"),
                       ("US", "United States")):
        currency = "EUR" if code in ("EU", "DE", "FR") else "USD"
        row = _assemble_rows(
            [_record(country_code=code, currency_code=currency, country_name=name)],
            TODAY,
            primaries,
        )[0]
        if row["is_member_state"]:
            labelled.append(name)
    assert labelled == ["Germany", "France"]
