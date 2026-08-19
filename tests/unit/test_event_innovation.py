"""Unit tests for the event innovation scoring layer.

Everything here exercises the pure core — no database, no config file on disk
except the one real-config sanity check at the bottom.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.processing.event_innovation import (
    DECAY_BUCKETS,
    EventInnovationConfigError,
    ReleaseRecord,
    ScoringConfig,
    bundle_score,
    build_bundles,
    decay_factor,
    decayed_innovation,
    ewma_scale,
    load_config,
    normalize_surprise,
    parse_config,
    percentile,
    rolls_up_to_policy_baseline,
    score_releases,
    surprise_scale,
    winsorize,
)

CONFIG_DATA = {
    "defaults": {
        "ewma_halflife_observations": 24,
        "winsor_lower_percentile": 5.0,
        "winsor_upper_percentile": 95.0,
        "min_observations": 4,
        "max_abs_normalized": 6.0,
        "min_bundle_members": 2,
    },
    "ewma_halflife_by_frequency": {"quarterly": 8},
    "scored_importance_max": 2,
    "decay_half_lives_days": {
        "high_freq_high_revision": 10,
        "high_freq_low_revision": 21,
        "low_freq_structural": 45,
        "meeting_adjacent": None,
    },
    "decay_buckets": {
        "high_freq_high_revision": ["nfp"],
        "high_freq_low_revision": ["unemployment_rate", "avg_hourly_earnings_mom"],
        "low_freq_structural": ["gdp_qoq"],
        "meeting_adjacent": ["fed_interest_rate_decision"],
    },
    "bundles": [
        {
            "bundle_key": "US_NFP_DAY",
            "country": "US",
            "members": [
                {"indicator": "nfp", "weight": 1.0},
                {"indicator": "unemployment_rate", "weight": 0.8},
                {"indicator": "avg_hourly_earnings_mom", "weight": 0.6},
            ],
        }
    ],
}


@pytest.fixture
def config() -> ScoringConfig:
    return parse_config(CONFIG_DATA)


# ── winsorization ────────────────────────────────────────────────────────────


def test_percentile_interpolates_between_neighbours():
    assert percentile([0.0, 10.0], 50.0) == pytest.approx(5.0)
    assert percentile([1.0, 2.0, 3.0, 4.0], 0.0) == 1.0
    assert percentile([1.0, 2.0, 3.0, 4.0], 100.0) == 4.0


def test_winsorize_clamps_the_tails_and_keeps_the_count():
    values = [-100.0] + [1.0] * 20 + [100.0]
    clamped = winsorize(values, 10.0, 90.0)
    assert len(clamped) == len(values)
    assert min(clamped) == 1.0
    assert max(clamped) == 1.0


def test_winsorize_leaves_an_in_band_sample_untouched():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert winsorize(values, 0.0, 100.0) == values


def test_winsorization_stops_one_extreme_print_inflating_the_scale():
    """The COVID case: one 20-sigma print must not permanently blow up vol."""
    normal = [1.0, -1.0] * 12
    contaminated = normal + [200.0]

    raw = ewma_scale(contaminated, 24)
    cleaned = ewma_scale(winsorize(contaminated, 5.0, 95.0), 24)

    assert raw > 10.0                # unusable — everything after looks in line
    assert cleaned == pytest.approx(1.0, abs=0.05)


def test_percentile_winsorization_is_weak_on_small_samples():
    """Why the shipped config uses 5/95 rather than 1/99.

    A monthly indicator has ~70 prints in our 2020-2026 window. At n=25 the 99th
    percentile lands between the top two observations and interpolates to nearly
    the outlier itself, so the clamp is a no-op and the scale stays wrecked.
    Recorded as a test so nobody "tightens" the config back to 1/99 and quietly
    disables the defence.
    """
    contaminated = [1.0, -1.0] * 12 + [200.0]

    barely_clipped = ewma_scale(winsorize(contaminated, 1.0, 99.0), 24)
    properly_clipped = ewma_scale(winsorize(contaminated, 5.0, 95.0), 24)

    assert barely_clipped > 10.0
    assert properly_clipped == pytest.approx(1.0, abs=0.05)


# ── EWMA scale ───────────────────────────────────────────────────────────────


def test_ewma_scale_of_constant_magnitude_is_that_magnitude():
    assert ewma_scale([2.0, -2.0, 2.0, -2.0], 24) == pytest.approx(2.0)


def test_ewma_weights_the_newest_observation_most():
    """A recent regime shift must move the scale; the sample is newest-last."""
    quiet_then_loud = ewma_scale([0.1] * 20 + [5.0] * 4, 4)
    loud_then_quiet = ewma_scale([5.0] * 4 + [0.1] * 20, 4)
    assert quiet_then_loud > loud_then_quiet


def test_ewma_halflife_weights_an_observation_h_back_at_one_half():
    # Two observations, the older exactly one half-life back. The weighted mean
    # square is (1*a^2 + 0.5*b^2) / 1.5.
    scale = ewma_scale([4.0, 2.0], 1.0)
    expected = math.sqrt((1.0 * 2.0**2 + 0.5 * 4.0**2) / 1.5)
    assert scale == pytest.approx(expected)


def test_ewma_scale_of_all_zero_surprises_is_unscoreable():
    assert ewma_scale([0.0, 0.0, 0.0], 24) is None


def test_ewma_scale_of_empty_sample_is_none():
    assert ewma_scale([], 24) is None


def test_ewma_scale_rejects_non_positive_halflife():
    with pytest.raises(ValueError):
        ewma_scale([1.0], 0)


def test_surprise_scale_needs_min_observations(config):
    assert surprise_scale([1.0, -1.0, 1.0], config) is None
    assert surprise_scale([1.0, -1.0, 1.0, -1.0], config) == pytest.approx(1.0)


def test_surprise_scale_uses_the_frequency_override(config):
    # Quarterly half-life is 8 observations vs the 24-print default, so the same
    # sample yields a different (more recency-weighted) scale.
    sample = [0.1] * 20 + [5.0] * 4
    assert surprise_scale(sample, config, frequency="quarterly") > surprise_scale(
        sample, config, frequency="monthly"
    )


# ── normalization and direction ──────────────────────────────────────────────


def test_normalize_divides_by_scale():
    z = normalize_surprise(2.5, 2.0, 0.25, higher_is_better_for_currency=True)
    assert z == pytest.approx(2.0)


def test_lower_than_expected_unemployment_is_a_positive_innovation():
    """Direction comes from is_higher_better_for_currency, which already exists."""
    z = normalize_surprise(3.8, 4.0, 0.1, higher_is_better_for_currency=False)
    assert z == pytest.approx(2.0)


def test_normalize_clips_at_max_abs():
    z = normalize_surprise(100.0, 0.0, 1.0, higher_is_better_for_currency=True, max_abs=6.0)
    assert z == 6.0


def test_normalize_without_consensus_or_scale_is_none():
    assert normalize_surprise(1.0, None, 1.0, higher_is_better_for_currency=True) is None
    assert normalize_surprise(1.0, 0.0, None, higher_is_better_for_currency=True) is None
    assert normalize_surprise(1.0, 0.0, 0.0, higher_is_better_for_currency=True) is None


# ── decay ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("half_life", [10.0, 21.0, 45.0])
def test_decay_curve_at_zero_one_and_two_half_lives(half_life):
    assert decay_factor(0, half_life) == pytest.approx(1.0)
    assert decay_factor(half_life, half_life) == pytest.approx(0.5)
    assert decay_factor(2 * half_life, half_life) == pytest.approx(0.25)


def test_decay_is_monotonically_decreasing():
    factors = [decay_factor(t, 21.0) for t in range(0, 60, 5)]
    assert factors == sorted(factors, reverse=True)


def test_meeting_adjacent_does_not_decay():
    assert decay_factor(0, None) == 1.0
    assert decay_factor(365, None) == 1.0


def test_future_dated_release_is_not_amplified():
    assert decay_factor(-5, 10.0) == 1.0


def test_decay_factor_rejects_non_positive_half_life():
    with pytest.raises(ValueError):
        decay_factor(1.0, 0.0)


def test_decayed_innovation_scales_the_score_and_keeps_its_sign():
    assert decayed_innovation(-2.0, 10.0, 10.0) == pytest.approx(-1.0)
    assert decayed_innovation(None, 10.0, 10.0) is None


def test_rolls_up_flag_is_set_only_for_meeting_adjacent():
    assert rolls_up_to_policy_baseline("meeting_adjacent") is True
    for bucket in DECAY_BUCKETS:
        if bucket != "meeting_adjacent":
            assert rolls_up_to_policy_baseline(bucket) is False
    assert rolls_up_to_policy_baseline(None) is False


# ── bundle aggregation ───────────────────────────────────────────────────────


def test_bundle_score_is_a_weighted_average():
    assert bundle_score([(2.0, 1.0), (0.0, 1.0)]) == pytest.approx(1.0)
    assert bundle_score([(2.0, 3.0), (0.0, 1.0)]) == pytest.approx(1.5)


def test_bundle_score_renormalizes_over_present_members():
    """A missing member must not dilute the bundle toward zero."""
    full = bundle_score([(2.0, 1.0), (2.0, 0.8), (2.0, 0.6)])
    partial = bundle_score([(2.0, 1.0), (2.0, 0.6)])
    assert full == pytest.approx(2.0)
    assert partial == pytest.approx(2.0)


def test_bundle_score_of_nothing_is_none():
    assert bundle_score([]) is None
    assert bundle_score([(2.0, 0.0)]) is None


def _nfp_day_records() -> list[ReleaseRecord]:
    """Enough history for each member to have a scale, then one NFP day."""
    records: list[ReleaseRecord] = []
    members = [
        (1, "nfp", 175.0, True),
        (2, "unemployment_rate", 4.0, False),
        (3, "avg_hourly_earnings_mom", 0.3, True),
    ]
    # 12 prior months alternating +1/-1 sigma-ish around consensus.
    for indicator_id, name, consensus, higher_better in members:
        for month in range(1, 13):
            step = 10.0 if name == "nfp" else 0.1
            records.append(
                ReleaseRecord(
                    release_id=indicator_id * 100 + month,
                    indicator_id=indicator_id,
                    canonical_name=name,
                    country_code="US",
                    release_date=date(2025, month, 5),
                    actual=consensus + (step if month % 2 else -step),
                    consensus=consensus,
                    importance=1,
                    higher_is_better=higher_better,
                    frequency="monthly",
                )
            )
    # NFP day: payrolls beat, unemployment ticks up, earnings in line.
    records.append(
        ReleaseRecord(
            release_id=901, indicator_id=1, canonical_name="nfp", country_code="US",
            release_date=date(2026, 1, 9), actual=195.0, consensus=175.0,
            importance=1, higher_is_better=True, frequency="monthly",
        )
    )
    records.append(
        ReleaseRecord(
            release_id=902, indicator_id=2, canonical_name="unemployment_rate",
            country_code="US", release_date=date(2026, 1, 9), actual=4.1,
            consensus=4.0, importance=1, higher_is_better=False, frequency="monthly",
        )
    )
    records.append(
        ReleaseRecord(
            release_id=903, indicator_id=3, canonical_name="avg_hourly_earnings_mom",
            country_code="US", release_date=date(2026, 1, 9), actual=0.3,
            consensus=0.3, importance=1, higher_is_better=True, frequency="monthly",
        )
    )
    return records


def test_nfp_day_collapses_to_one_bundle_not_three_shocks(config):
    scored = score_releases(_nfp_day_records(), config)
    bundles = build_bundles(scored, config)

    nfp_day = [b for b in bundles if b.release_date == date(2026, 1, 9)]
    assert len(nfp_day) == 1
    bundle = nfp_day[0]
    assert bundle.bundle_key == "US_NFP_DAY"
    assert bundle.country == "US"
    assert bundle.member_count == 3
    assert bundle.indicator_ids == [1, 2, 3]
    assert bundle.score is not None

    # Every member release is tagged with the bundle, so no member is left
    # floating as an independent shock.
    day_rows = [s for s in scored if s.release_date == date(2026, 1, 9)]
    assert all(row.bundle_key == "US_NFP_DAY" for row in day_rows)


def test_bundle_takes_its_decay_bucket_from_the_heaviest_member(config):
    scored = score_releases(_nfp_day_records(), config)
    bundle = [
        b for b in build_bundles(scored, config) if b.release_date == date(2026, 1, 9)
    ][0]
    # NFP carries weight 1.0 and is high_freq_high_revision, so the bundle
    # decays like payrolls (10d) rather than like the unemployment rate (21d).
    assert bundle.decay_bucket == "high_freq_high_revision"
    assert bundle.half_life_days == pytest.approx(10.0)


def test_a_lone_member_does_not_form_a_bundle(config):
    records = [r for r in _nfp_day_records() if r.indicator_id != 2]
    records = [r for r in records if not (r.release_date == date(2026, 1, 9) and r.indicator_id == 3)]
    scored = score_releases(records, config)
    bundles = build_bundles(scored, config)
    assert [b for b in bundles if b.release_date == date(2026, 1, 9)] == []


def test_bundle_matches_the_hand_computed_weighted_average(config):
    scored = score_releases(_nfp_day_records(), config)
    by_release = {row.release_id: row for row in scored}
    weights = {901: 1.0, 902: 0.8, 903: 0.6}
    expected = bundle_score(
        (by_release[rid].surprise_normalized, weight) for rid, weight in weights.items()
    )
    bundle = [
        b for b in build_bundles(scored, config) if b.release_date == date(2026, 1, 9)
    ][0]
    assert bundle.score == pytest.approx(expected)


# ── point-in-time scoring and filtering ──────────────────────────────────────


def test_scale_is_point_in_time_and_first_prints_are_unscored(config):
    scored = score_releases(_nfp_day_records(), config)
    nfp_rows = sorted(
        (s for s in scored if s.indicator_id == 1), key=lambda s: s.release_date
    )
    # min_observations is 4 in the test config, so the first four prints have
    # no trustworthy scale and are stored unscored.
    assert [row.scored for row in nfp_rows[:4]] == [False] * 4
    assert all(row.scored for row in nfp_rows[4:])
    assert nfp_rows[0].surprise_scale is None
    # The raw surprise is always kept, scored or not.
    assert nfp_rows[0].surprise_raw == pytest.approx(10.0)


def test_low_impact_releases_are_stored_but_not_scored(config):
    records = [
        ReleaseRecord(
            release_id=i,
            indicator_id=7,
            canonical_name="nfp",
            country_code="US",
            release_date=date(2025, i, 1),
            actual=100.0 + i,
            consensus=100.0,
            importance=3,  # EODHD impact = low
            higher_is_better=True,
            frequency="monthly",
        )
        for i in range(1, 13)
    ]
    scored = score_releases(records, config)
    assert all(row.scored is False for row in scored)
    # Still fully populated — only the flag differs, so raising the threshold
    # later is a re-flag, not a re-ingest.
    assert scored[-1].surprise_normalized is not None
    assert scored[-1].decay_bucket == "high_freq_high_revision"


def test_indicator_with_no_bucket_assigned_is_not_scored(config):
    records = [
        ReleaseRecord(
            release_id=i,
            indicator_id=8,
            canonical_name="redbook_yoy",  # deliberately unbucketed
            country_code="US",
            release_date=date(2025, i, 1),
            actual=100.0 + i,
            consensus=100.0,
            importance=1,
            higher_is_better=True,
            frequency="weekly",
        )
        for i in range(1, 13)
    ]
    scored = score_releases(records, config)
    assert all(row.decay_bucket is None for row in scored)
    assert all(row.scored is False for row in scored)


# ── config validation ────────────────────────────────────────────────────────


def test_country_scoped_bucket_beats_the_global_one():
    data = dict(CONFIG_DATA)
    data["decay_buckets"] = {
        "high_freq_low_revision": ["gdp_mom"],
        "low_freq_structural": ["UK:gdp_mom"],
    }
    config = parse_config(data)
    assert config.decay_bucket_for("US", "gdp_mom") == "high_freq_low_revision"
    assert config.decay_bucket_for("UK", "gdp_mom") == "low_freq_structural"


def test_meeting_adjacent_with_a_half_life_is_rejected():
    data = dict(CONFIG_DATA)
    data["decay_half_lives_days"] = dict(CONFIG_DATA["decay_half_lives_days"])
    data["decay_half_lives_days"]["meeting_adjacent"] = 30
    with pytest.raises(EventInnovationConfigError, match="meeting_adjacent"):
        parse_config(data)


def test_unknown_decay_bucket_is_rejected():
    data = dict(CONFIG_DATA)
    data["decay_buckets"] = {"made_up_bucket": ["nfp"]}
    with pytest.raises(EventInnovationConfigError, match="Unknown decay bucket"):
        parse_config(data)


def test_an_indicator_cannot_sit_in_two_bundles_for_one_country():
    data = dict(CONFIG_DATA)
    data["bundles"] = CONFIG_DATA["bundles"] + [
        {
            "bundle_key": "US_OTHER_DAY",
            "country": "US",
            "members": [{"indicator": "nfp", "weight": 1.0}],
        }
    ]
    with pytest.raises(EventInnovationConfigError, match="claimed by"):
        parse_config(data)


def test_duplicate_bundle_key_is_rejected():
    data = dict(CONFIG_DATA)
    data["bundles"] = CONFIG_DATA["bundles"] * 2
    with pytest.raises(EventInnovationConfigError, match="Duplicate bundle_key"):
        parse_config(data)


# ── the shipped config ───────────────────────────────────────────────────────


def test_shipped_bundle_config_parses_and_covers_the_key_events():
    config = load_config()

    keys = {bundle.bundle_key for bundle in config.bundles}
    assert "US_NFP_DAY" in keys
    assert "US_CPI_DAY" in keys

    nfp_day = next(b for b in config.bundles if b.bundle_key == "US_NFP_DAY")
    member_names = {member.canonical_name for member in nfp_day.members}
    assert {"nfp", "unemployment_rate", "participation_rate"} <= member_names
    assert nfp_day.weight_for("nfp") == max(m.weight for m in nfp_day.members)

    assert config.decay_bucket_for("US", "nfp") == "high_freq_high_revision"
    assert config.decay_bucket_for("US", "core_cpi_yoy") == "high_freq_low_revision"
    assert config.decay_bucket_for("AU", "gdp_qoq") == "low_freq_structural"
    assert config.decay_bucket_for("US", "fed_interest_rate_decision") == "meeting_adjacent"
    assert config.half_life_days("meeting_adjacent") is None
    # The brief's starting half-lives.
    assert config.half_life_days("high_freq_high_revision") == 10
    assert config.half_life_days("high_freq_low_revision") == 21
    assert config.half_life_days("low_freq_structural") == 45


def test_shipped_config_buckets_at_least_fifteen_indicators():
    config = load_config()
    assert len(config.bucket_by_indicator) >= 15


def _mapped_indicators() -> set[tuple[str, str]]:
    mapping_path = (
        Path(__file__).resolve().parents[2] / "config" / "indicator_mapping.yaml"
    )
    with mapping_path.open(encoding="utf-8") as handle:
        entries = yaml.safe_load(handle)["mappings"]
    return {(entry["country"], entry["canonical_name"]) for entry in entries}


def test_every_config_indicator_exists_in_the_indicator_mapping():
    """Guards against typos and dead config as the buckets get expanded.

    A misspelled canonical name is invisible at runtime — the indicator simply
    never matches and silently drops out of scoring — so it has to be caught here.
    """
    config = load_config()
    known_names = {name for _, name in _mapped_indicators()}

    unknown = sorted(
        f"{country or '*'}:{name}"
        for (country, name) in config.bucket_by_indicator
        if name not in known_names
    )
    assert unknown == [], f"decay_buckets reference unmapped indicators: {unknown}"


def test_every_bundle_member_is_mapped_for_its_country_and_bucketed():
    """A bundle member that is unmapped or unbucketed can never contribute."""
    config = load_config()
    mapped = _mapped_indicators()

    problems: list[str] = []
    for bundle in config.bundles:
        for member in bundle.members:
            if (bundle.country, member.canonical_name) not in mapped:
                problems.append(
                    f"{bundle.bundle_key}: {member.canonical_name} not mapped for "
                    f"{bundle.country}"
                )
            elif config.decay_bucket_for(bundle.country, member.canonical_name) is None:
                problems.append(
                    f"{bundle.bundle_key}: {member.canonical_name} has no decay bucket"
                )
    assert problems == [], "\n".join(problems)
