from __future__ import annotations

import pytest

from app.services.fixed_income_analytics import (
    assess_mispricing,
    assess_rates_fx_confirmation,
    classify_cross_asset_shock,
    classify_curve_change,
    curve_slope,
    differential_for_pair,
    historical_percentile,
    policy_repricing_proxy,
    relationship_strength,
    rolling_regression,
    rolling_z_score,
)


def test_curve_slope_uses_long_minus_short_convention() -> None:
    curve = {"2Y": 4.0, "10Y": 4.5}

    assert curve_slope(curve, "2Y", "10Y") == pytest.approx(0.5)


def test_curve_classification_bear_flattening() -> None:
    result = classify_curve_change(
        short_end_change_bps=12.0,
        long_end_change_bps=5.0,
        lookback="5d",
        min_move_bps=3.0,
    )

    assert result.classification == "Bear flattening"
    assert result.slope_change_bps == pytest.approx(-7.0)
    assert result.confidence > 0.35


def test_curve_classification_ignores_tiny_moves() -> None:
    result = classify_curve_change(
        short_end_change_bps=1.0,
        long_end_change_bps=1.5,
        lookback="1d",
        min_move_bps=3.0,
    )

    assert result.classification == "Mixed/unclear"
    assert "below" in result.explanation


def test_fx_differential_uses_base_minus_quote() -> None:
    point = differential_for_pair(
        "EUR/USD",
        "2Y",
        {"DE": {"2Y": 2.25}, "US": {"2Y": 4.00}},
    )

    assert point is not None
    assert point.base_country == "DE"
    assert point.quote_country == "US"
    assert point.differential == pytest.approx(-1.75)
    assert "base currency" in point.interpretation


def test_policy_repricing_proxy_is_labelled_as_proxy() -> None:
    score = policy_repricing_proxy(
        country_code="US",
        lookback="5d",
        changes_bps={"3M": 10.0, "6M": 8.0, "1Y": 5.0, "2Y": 2.0},
    )

    assert score.label == "Government-yield policy repricing proxy"
    assert score.score == pytest.approx(6.65)
    assert score.component_contributions["3M"] == pytest.approx(3.0)


def test_rolling_z_score_and_percentile() -> None:
    values = [1.0, 2.0, 3.0, 4.0]

    assert rolling_z_score(values, latest=4.0) == pytest.approx(1.3416, abs=0.001)
    assert historical_percentile(values, latest=3.0) == pytest.approx(75.0)


def test_rolling_regression_and_relationship_strength() -> None:
    result = rolling_regression([1, 2, 3, 4], [2, 4, 6, 8])

    assert result["beta"] == pytest.approx(2.0)
    assert result["r_squared"] == pytest.approx(1.0)
    assert relationship_strength(result["correlation"], result["r_squared"]) == "strong relationship"


def test_relationship_strength_gates_weak_models() -> None:
    assert relationship_strength(0.2, 0.04) == "weak relationship"
    assert relationship_strength(None, None) == "insufficient history"


def test_rates_fx_confirmation_detects_contradiction() -> None:
    result = assess_rates_fx_confirmation(
        pair="EUR/USD",
        differential_change_bps=12.0,
        fx_return_pct=-0.35,
    )

    assert result.state == "Contradiction"
    assert result.evidence


def test_mispricing_assessment_gates_weak_relationship() -> None:
    result = assess_mispricing(
        spot_fx=1.10,
        rates_implied_estimate=1.15,
        residual_history=[-0.01, 0.0, 0.01],
        correlation=0.2,
        r_squared=0.04,
        data_fresh=True,
        timestamps_aligned=True,
    )

    assert result.label == "weak relationship"
    assert "weak relationship" in result.reasons


def test_mispricing_assessment_allows_meaningful_divergence() -> None:
    result = assess_mispricing(
        spot_fx=1.20,
        rates_implied_estimate=1.10,
        residual_history=[-0.01, 0.0, 0.01, 0.02],
        correlation=0.8,
        r_squared=0.64,
        data_fresh=True,
        timestamps_aligned=True,
    )

    assert result.label == "meaningful rates-FX divergence"
    assert result.relationship_strength == "strong relationship"


def test_shock_classifier_keeps_evidence_and_missing_inputs() -> None:
    result = classify_cross_asset_shock(
        two_year_change_bps=10.0,
        ten_year_change_bps=4.0,
        dxy_return_pct=0.4,
        gold_return_pct=0.2,
    )

    assert result.classification == "Hawkish policy shock"
    assert result.supporting_evidence
    assert result.contradictory_evidence == ["Gold rose despite higher front-end yields."]
    assert "VIX" in result.missing_inputs
