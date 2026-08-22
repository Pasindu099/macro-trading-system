from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routes import fixed_income


class _EmptyResult:
    def __iter__(self):
        return iter(())


class _EmptySession:
    async def execute(self, statement, params=None):
        return _EmptyResult()


@pytest.mark.asyncio
async def test_countries_endpoint_returns_honest_empty_response() -> None:
    payload = await fixed_income.fixed_income_countries(_EmptySession())

    assert payload["countries"] == []
    assert payload["conventions"]["fx_differential"] == "Base-currency yield minus quote-currency yield."


def test_unsupported_pair_raises_not_found() -> None:
    with pytest.raises(HTTPException) as exc:
        fixed_income._normalize_pair("XXXYYY")

    assert exc.value.status_code == 404


def test_small_residual_is_not_labelled_latent_divergence() -> None:
    state = fixed_income._mispricing_state(
        "apparent divergence only",
        {
            "relationship_reliable": True,
            "data_fresh": True,
            "timestamps_aligned": True,
            "residual_material": False,
            "model_stable": True,
            "sufficient_history": True,
        },
    )

    assert state == "No material divergence"


def test_adjusted_opportunity_score_penalizes_weak_models() -> None:
    weak = fixed_income._adjusted_opportunity_score(
        residual_z_score=2.3,
        r_squared=0.08,
        gates={
            "data_fresh": True,
            "model_stable": True,
            "sufficient_history": True,
        },
    )
    reliable = fixed_income._adjusted_opportunity_score(
        residual_z_score=1.2,
        r_squared=0.81,
        gates={
            "data_fresh": True,
            "model_stable": True,
            "sufficient_history": True,
        },
    )

    assert weak < reliable


def test_opportunity_bucket_requires_material_residual_for_medium_or_high() -> None:
    bucket = fixed_income._opportunity_bucket(
        0.9,
        {
            "sufficient_history": True,
            "data_fresh": True,
            "residual_material": False,
            "relationship_reliable": True,
        },
    )

    assert bucket == "low"


def test_curve_shape_separates_shape_from_movement() -> None:
    assert fixed_income._curve_shape({"2Y": 4.5, "10Y": 4.0})["label"] == "inverted"
    assert fixed_income._curve_shape({"2Y": 4.5, "10Y": 4.6})["label"] == "flat"
    assert fixed_income._curve_shape({"2Y": 4.0, "10Y": 4.7})["label"] == "normal"


def test_source_dimensions_keep_provenance_and_methodology_separate() -> None:
    ecb = fixed_income._source_dimensions_for_rate_source("ecb_yc_proxy")
    fed = fixed_income._source_dimensions_for_rate_source("yfinance_ZQ")

    assert ecb["provenance"] == "official source"
    assert ecb["methodology"] == "rates proxy, not meeting-level OIS"
    assert fed["delivery_source"] == "Yahoo Finance"
    assert fed["methodology"] == "futures-derived proxy"
