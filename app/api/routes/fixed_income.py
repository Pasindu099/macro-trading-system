"""Fixed-income intelligence API backed by durable government-yield observations."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from statistics import pstdev
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_session
from app.ingestion.eodhd_client import GBOND_MATURITIES
from app.services.fixed_income_analytics import (
    DIFFERENTIAL_MATURITIES,
    FX_PAIR_COUNTRIES,
    assess_mispricing,
    assess_rates_fx_confirmation,
    classify_cross_asset_shock,
    classify_curve_change,
    curve_slopes,
    differential_for_pair,
    policy_repricing_proxy,
    rolling_regression,
    rolling_z_score,
)
from app.services.fx_spot import FX_PAIR_SYMBOLS

router = APIRouter(prefix="/api/fixed-income", tags=["fixed-income"])
SessionDep = Depends(get_session)


@router.get("/government-yields/latest")
async def latest_government_yields(session: AsyncSession = SessionDep) -> dict[str, Any]:
    """Latest point-in-time government-yield observations by country and maturity."""
    rows = await _latest_yield_rows(session)
    return {
        "countries": _curves_payload(rows),
        "conventions": _conventions(),
    }


@router.get("/countries")
async def fixed_income_countries(session: AsyncSession = SessionDep) -> dict[str, Any]:
    rows = await _latest_yield_rows(session)
    countries = sorted({row["country_code"] for row in rows})
    return {
        "countries": [
            {
                "country_code": country,
                "currency_code": next(row["currency_code"] for row in rows if row["country_code"] == country),
                "available_maturities": sorted(
                    [row["maturity"] for row in rows if row["country_code"] == country],
                    key=_maturity_sort_key,
                ),
                "missing_maturities": [
                    maturity
                    for maturity in GBOND_MATURITIES
                    if maturity not in {row["maturity"] for row in rows if row["country_code"] == country}
                ],
            }
            for country in countries
        ],
        "conventions": _conventions(),
    }


@router.get("/countries/{currency}/curve")
async def country_curve(currency: str, session: AsyncSession = SessionDep) -> dict[str, Any]:
    rows = await _latest_yield_rows(session)
    country = _country_for_currency_or_code(currency)
    country_rows = [row for row in rows if row["country_code"] == country]
    history = await _country_curve_history(session, country)
    return {
        "country_code": country,
        "curve": _curves_payload(country_rows).get(country, {}),
        "historical_curves": history["curves"],
        "maturity_changes_bps": history["changes_bps"],
        "missing_inputs": [
            maturity
            for maturity in GBOND_MATURITIES
            if maturity not in {row["maturity"] for row in country_rows}
        ],
        "calculation_method": "Latest, 1-observation, 5-observation, and 20-observation durable EODHD GBOND curves.",
        "lookback_window": "latest/1d/5d/20d observations",
        "confidence": 0.85 if country_rows else 0.0,
        "conventions": _conventions(),
    }


@router.get("/countries/{currency}/regime")
async def country_regime(currency: str, session: AsyncSession = SessionDep) -> dict[str, Any]:
    rows = await _latest_yield_rows(session)
    country = _country_for_currency_or_code(currency)
    curve = _curves_by_country(rows).get(country, {})
    history = await _country_curve_history(session, country)
    movement = _curve_movement_from_history(history, "5d")
    return {
        "country_code": country,
        "curve_shape": _curve_shape(curve),
        "curve_movement": movement,
        "slopes": curve_slopes(curve),
        "inversion_status": _inversion_status(curve),
        "missing_inputs": [
            maturity for maturity in ("2Y", "10Y") if maturity not in curve
        ],
        "calculation_method": "Curve shape uses current 2s10s; curve movement uses 5-observation change in 2Y and 10Y yields.",
        "lookback_window": "latest and 5 observations",
        "confidence": 0.8 if "2Y" in curve and "10Y" in curve else 0.25,
        "conventions": _conventions(),
    }


@router.get("/command-centre")
async def fixed_income_command_centre(session: AsyncSession = SessionDep) -> dict[str, Any]:
    """Decision-focused summary from currently stored durable yield observations."""
    rows = await _latest_yield_rows(session)
    curves = _curves_by_country(rows)
    daily_moves = await _yield_change_rows(session, "2Y", 1)
    weekly_moves = await _yield_change_rows(session, "2Y", 5)
    monthly_moves = await _yield_change_rows(session, "2Y", 20)
    relative_repricing = await _pair_repricing_rows(session, "2Y", 5)
    repricing = [
        policy_repricing_proxy(
            country_code=country,
            lookback="5 observations",
            changes_bps=await _country_policy_changes(session, country, 5),
        )
        for country, curve in curves.items()
    ]
    repricing_payload = [
        {
            "country_code": item.country_code,
            "lookback": item.lookback,
            "score": item.score,
            "label": item.label,
            "component_contributions": item.component_contributions,
        }
        for item in repricing
    ]
    return {
        "largest_2y_yields": sorted(
            [
                {
                    "country_code": country,
                    "yield": curve.get("2Y"),
                    "source_quality": _quality_for(rows, country, "2Y"),
                }
                for country, curve in curves.items()
                if curve.get("2Y") is not None
            ],
            key=lambda item: item["yield"],
            reverse=True,
        ),
        "largest_daily_2y_moves": daily_moves,
        "largest_weekly_2y_moves": weekly_moves,
        "largest_20d_2y_moves": monthly_moves,
        "most_hawkish_repricing": sorted(repricing_payload, key=lambda item: item["score"], reverse=True),
        "most_dovish_repricing": sorted(repricing_payload, key=lambda item: item["score"]),
        "strongest_pairwise_repricing": relative_repricing,
        "policy_repricing_proxy": repricing_payload,
        "curve_regime": {
            country: {
                "curve_shape": _curve_shape(curve),
                "curve_movement": _curve_movement_from_history(
                    await _country_curve_history(session, country),
                    "5d",
                ),
                "slopes": curve_slopes(curve),
                "inversion_status": _inversion_status(curve),
            }
            for country, curve in curves.items()
        },
        "data_freshness": _freshness_payload(rows),
        "last_successful_update": _latest_ingestion_success(rows),
        "conventions": _conventions(),
    }


@router.get("/pairs")
async def fixed_income_pairs(session: AsyncSession = SessionDep) -> dict[str, Any]:
    rows = await _latest_yield_rows(session)
    fx_counts = await _fx_coverage(session)
    return {
        "pairs": [
            {
                "pair": pair,
                "base_country": countries[0],
                "quote_country": countries[1],
                "provider_symbol": FX_PAIR_SYMBOLS.get(pair),
                "fx_history_rows": fx_counts.get(pair, {}).get("rows", 0),
                "fx_earliest_date": fx_counts.get(pair, {}).get("earliest"),
                "fx_latest_date": fx_counts.get(pair, {}).get("latest"),
                "latest_2y_differential": (
                    differential_for_pair(pair, "2Y", _curves_by_country(rows)).differential
                    if differential_for_pair(pair, "2Y", _curves_by_country(rows))
                    else None
                ),
            }
            for pair, countries in FX_PAIR_COUNTRIES.items()
        ],
        "conventions": _conventions(),
    }


@router.get("/differentials/latest")
async def latest_differentials(session: AsyncSession = SessionDep) -> dict[str, Any]:
    """Latest FX relative-rates matrix using base-minus-quote convention."""
    rows = await _latest_yield_rows(session)
    curves = _curves_by_country(rows)
    payload: dict[str, list[dict[str, Any]]] = {}
    for pair in FX_PAIR_COUNTRIES:
        points = []
        for maturity in DIFFERENTIAL_MATURITIES:
            point = differential_for_pair(pair, maturity, curves)
            if point is None:
                continue
            points.append({
                "maturity": point.maturity,
                "differential": point.differential,
                "base_country": point.base_country,
                "quote_country": point.quote_country,
                "interpretation": point.interpretation,
                "source_quality": _pair_quality(rows, point.base_country, point.quote_country, point.maturity),
                "timestamp_alignment": _timestamp_alignment(rows, point.base_country, point.quote_country, point.maturity),
            })
        payload[pair] = points
    return {"pairs": payload, "conventions": _conventions()}


@router.get("/pairs/{pair:path}/differentials")
async def pair_differentials(
    pair: str,
    maturity: str = "2Y",
    window: int = 250,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    normalized = _normalize_pair(pair)
    series = await _aligned_rates_fx_series(session, normalized, maturity.upper(), window)
    return {
        "pair": normalized,
        "maturity": maturity.upper(),
        "points": [
            {
                "date": item["date"].isoformat(),
                "differential": item["differential"],
                "fx_close": item["fx_close"],
            }
            for item in series
        ],
        "latest_differential_z_score": rolling_z_score([item["differential"] for item in series]),
        "differential_change_bps_5d": _series_change_bps(series, 5, "differential"),
        "differential_change_bps_20d": _series_change_bps(series, 20, "differential"),
        "missing_inputs": _series_missing_inputs(series, normalized, maturity.upper()),
        "calculation_method": "Differential = base-currency yield minus quote-currency yield.",
        "lookback_window": f"{window} aligned observations",
        "confidence": 0.8 if len(series) >= min(window, 60) else 0.25,
        "conventions": _conventions(),
    }


@router.get("/pairs/{pair:path}/mispricing")
async def pair_mispricing(
    pair: str,
    maturity: str = "2Y",
    window: int = 120,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    normalized = _normalize_pair(pair)
    series = await _aligned_rates_fx_series(session, normalized, maturity.upper(), window)
    differentials = [item["differential"] for item in series]
    fx_values = [item["fx_close"] for item in series]
    regression = rolling_regression(differentials, fx_values)
    latest_fx = fx_values[-1] if fx_values else None
    estimate = (
        regression["alpha"] + regression["beta"] * differentials[-1]
        if regression["alpha"] is not None and regression["beta"] is not None and differentials
        else None
    )
    residual_history = [
        fx - (regression["alpha"] + regression["beta"] * diff)
        for diff, fx in zip(differentials, fx_values, strict=True)
    ] if regression["alpha"] is not None and regression["beta"] is not None else []
    fitted_values = [
        regression["alpha"] + regression["beta"] * diff
        for diff in differentials
    ] if regression["alpha"] is not None and regression["beta"] is not None else []
    residual_z_history = [
        rolling_z_score(residual_history[: idx + 1])
        for idx in range(len(residual_history))
    ]
    assessment = assess_mispricing(
        spot_fx=latest_fx or 0.0,
        rates_implied_estimate=estimate,
        residual_history=residual_history,
        correlation=regression["correlation"],
        r_squared=regression["r_squared"],
        data_fresh=_data_fresh(series),
        timestamps_aligned=True,
    )
    gates = {
        "relationship_reliable": assessment.relationship_strength in {"moderate relationship", "strong relationship"},
        "data_fresh": _data_fresh(series),
        "timestamps_aligned": True,
        "residual_material": bool(assessment.residual_z_score and abs(assessment.residual_z_score) >= 1.5),
        "model_stable": len(series) >= window,
        "sufficient_history": len(series) >= min(window, 60),
    }
    adjusted_score = _adjusted_opportunity_score(
        assessment.residual_z_score,
        regression["r_squared"],
        gates,
    )
    return {
        "pair": normalized,
        "maturity": maturity.upper(),
        "state": _mispricing_state(assessment.label, gates),
        "state_label": "Model residual state",
        "spot_fx": latest_fx,
        "rates_implied_estimate": estimate,
        "residual": assessment.residual,
        "residual_z_score": assessment.residual_z_score,
        "adjusted_opportunity_score": adjusted_score,
        "opportunity_bucket": _opportunity_bucket(adjusted_score, gates),
        "model_series": [
            {
                "date": item["date"].isoformat(),
                "fx_close": item["fx_close"],
                "differential": item["differential"],
                "rates_implied_estimate": fitted_values[idx] if idx < len(fitted_values) else None,
                "residual": residual_history[idx] if idx < len(residual_history) else None,
                "residual_z_score": residual_z_history[idx] if idx < len(residual_z_history) else None,
                "upper_1sd": (fitted_values[idx] + _residual_sigma(residual_history)) if idx < len(fitted_values) and _residual_sigma(residual_history) is not None else None,
                "lower_1sd": (fitted_values[idx] - _residual_sigma(residual_history)) if idx < len(fitted_values) and _residual_sigma(residual_history) is not None else None,
                "upper_2sd": (fitted_values[idx] + 2 * _residual_sigma(residual_history)) if idx < len(fitted_values) and _residual_sigma(residual_history) is not None else None,
                "lower_2sd": (fitted_values[idx] - 2 * _residual_sigma(residual_history)) if idx < len(fitted_values) and _residual_sigma(residual_history) is not None else None,
            }
            for idx, item in enumerate(series)
        ],
        "regression": regression,
        "confidence": assessment.confidence,
        "relationship_strength": assessment.relationship_strength,
        "gates": gates,
        "missing_inputs": _series_missing_inputs(series, normalized, maturity.upper()),
        "calculation_method": "Rolling OLS of FX close on base-minus-quote yield differential.",
        "lookback_window": f"{window} aligned observations",
        "conventions": _conventions(),
    }


@router.get("/pairs/{pair:path}/confirmation")
async def pair_confirmation(
    pair: str,
    maturity: str = "2Y",
    lookback: int = 5,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    normalized = _normalize_pair(pair)
    series = await _aligned_rates_fx_series(session, normalized, maturity.upper(), max(lookback + 1, 60))
    if len(series) <= lookback:
        return {
            "pair": normalized,
            "state": "Insufficient history",
            "missing_inputs": _series_missing_inputs(series, normalized, maturity.upper()),
            "confidence": 0.0,
            "conventions": _conventions(),
        }
    diff_change_bps = (series[-1]["differential"] - series[-1 - lookback]["differential"]) * 100
    fx_return_pct = (series[-1]["fx_close"] / series[-1 - lookback]["fx_close"] - 1) * 100
    regression = rolling_regression(
        [item["differential"] for item in series],
        [item["fx_close"] for item in series],
    )
    reliable = bool(regression["r_squared"] and regression["r_squared"] >= 0.15)
    result = assess_rates_fx_confirmation(
        pair=normalized,
        differential_change_bps=diff_change_bps,
        fx_return_pct=fx_return_pct,
        relationship_reliable=reliable,
    )
    return {
        "pair": normalized,
        "maturity": maturity.upper(),
        "state": result.state,
        "differential_change_bps": result.differential_change_bps,
        "fx_return_pct": result.fx_return_pct,
        "evidence": result.evidence,
        "regression": regression,
        "missing_inputs": _series_missing_inputs(series, normalized, maturity.upper()),
        "calculation_method": "Compare FX return with base-minus-quote yield differential change.",
        "lookback_window": f"{lookback} aligned observations",
        "confidence": 0.65 if reliable and _data_fresh(series) else 0.3,
        "conventions": _conventions(),
    }


@router.get("/pairs/{pair:path}/narrative")
async def pair_narrative(
    pair: str,
    maturity: str = "2Y",
    lookback: int = 5,
    session: AsyncSession = SessionDep,
) -> dict[str, Any]:
    """Deterministic narrative assembled from calculated rates/FX evidence."""
    normalized = _normalize_pair(pair)
    mat = maturity.upper()
    series = await _aligned_rates_fx_series(session, normalized, mat, max(lookback + 1, 120))
    missing = _series_missing_inputs(series, normalized, mat)
    if len(series) <= lookback:
        return {
            "pair": normalized,
            "maturity": mat,
            "state": "Insufficient history",
            "narrative": f"{normalized} has insufficient aligned {mat} rates and FX history for a deterministic narrative.",
            "answers": {
                "what_changed": "Insufficient aligned observations.",
                "maturity_driver": mat,
                "absolute_or_relative": "Relative rates differential.",
                "fx_confirmed": "Unknown.",
                "relationship_reliable": "Unknown.",
                "supports": [],
                "contradicts": [],
                "missing_data": missing,
                "invalidation": "More aligned point-in-time rates and FX observations are required.",
            },
            "missing_inputs": missing,
            "calculation_method": "Deterministic narrative from aligned durable FX and base-minus-quote yield differentials.",
            "lookback_window": f"{lookback} aligned observations",
            "confidence": 0.0,
            "conventions": _conventions(),
        }

    diff_change_bps = _series_change_bps(series, lookback, "differential") or 0.0
    fx_return_pct = (series[-1]["fx_close"] / series[-1 - lookback]["fx_close"] - 1) * 100
    regression = rolling_regression(
        [item["differential"] for item in series],
        [item["fx_close"] for item in series],
    )
    reliable = bool(regression["r_squared"] and regression["r_squared"] >= 0.15)
    confirmation = assess_rates_fx_confirmation(
        pair=normalized,
        differential_change_bps=diff_change_bps,
        fx_return_pct=fx_return_pct,
        relationship_reliable=reliable,
    )
    supports = list(confirmation.evidence)
    contradicts = [] if confirmation.state in {"Confirmed", "Latent divergence"} else confirmation.evidence
    direction = "widened" if diff_change_bps > 0 else "narrowed"
    base = normalized.split("/", 1)[0]
    quote = normalized.split("/", 1)[1]
    narrative = (
        f"{base}-{quote} {mat} differentials {direction} by {abs(diff_change_bps):.1f} bp "
        f"over {lookback} aligned observations under the dashboard's base-minus-quote convention. "
        f"{normalized} returned {fx_return_pct:.2f}% over the same window, so the rates-FX state is "
        f"{confirmation.state.lower()}. The rolling relationship is "
        f"{'reliable enough for confirmation checks' if reliable else 'not reliable enough for high-confidence interpretation'}."
    )
    return {
        "pair": normalized,
        "maturity": mat,
        "state": confirmation.state,
        "narrative": narrative,
        "answers": {
            "what_changed": f"{mat} differential {direction} by {diff_change_bps:.1f} bp.",
            "maturity_driver": mat,
            "absolute_or_relative": "Relative rates differential between base and quote countries.",
            "fx_confirmed": confirmation.state,
            "relationship_reliable": reliable,
            "supports": supports,
            "contradicts": contradicts,
            "missing_data": missing,
            "invalidation": "A reversal in the differential, stale data, or a lower rolling R-squared invalidates the interpretation.",
        },
        "regression": regression,
        "missing_inputs": missing,
        "calculation_method": "Deterministic narrative from aligned durable FX and base-minus-quote yield differentials.",
        "lookback_window": f"{lookback} aligned observations",
        "confidence": 0.65 if reliable and _data_fresh(series) else 0.3,
        "conventions": _conventions(),
    }


@router.get("/shock/current")
async def current_shock_classification(session: AsyncSession = SessionDep) -> dict[str, Any]:
    """Rule-based shock monitor using currently available verified inputs."""
    us_history = await _country_curve_history(session, "US")
    two_year_change = _curve_change_bps(us_history["curves"], "2Y", "1d")
    ten_year_change = _curve_change_bps(us_history["curves"], "10Y", "1d")
    slope_2s10s = _slope_change_bps(us_history["curves"], "1d")
    shock = classify_cross_asset_shock(
        two_year_change_bps=two_year_change,
        ten_year_change_bps=ten_year_change,
        slope_2s10s_change_bps=slope_2s10s,
        dxy_return_pct=None,
        gold_return_pct=None,
        vix_change_pct=None,
    )
    return {
        "classification": shock.classification,
        "supporting_evidence": shock.supporting_evidence,
        "contradictory_evidence": shock.contradictory_evidence,
        "confidence": shock.confidence,
        "input_snapshot": {
            "US_2Y_change_bps_1d": two_year_change,
            "US_10Y_change_bps_1d": ten_year_change,
            "US_2s10s_change_bps_1d": slope_2s10s,
            "available_but_not_wired": [],
        },
        "missing_inputs": shock.missing_inputs,
        "alternative_classification": shock.alternative_classification,
        "language_guardrail": "Classifications are consistent with evidence; causality requires a linked event.",
    }


@router.get("/data-quality")
async def fixed_income_data_quality(session: AsyncSession = SessionDep) -> dict[str, Any]:
    yield_rows = await _yield_coverage(session)
    fx_rows = await _fx_coverage(session)
    rate_probability_sources = await _rate_probability_source_quality(session)
    return {
        "government_yields": yield_rows,
        "fx_spot": fx_rows,
        "rate_probability_sources": rate_probability_sources,
        "source_quality_scale": [
            "Official",
            "Licensed API",
            "Exchange",
            "Provider-observed",
            "Proxy",
            "Third-party scraped",
            "Model-estimated",
            "Stale",
            "Unavailable",
            "Configured but unverified",
        ],
        "normalization": _source_quality_normalization(),
        "conventions": _conventions(),
    }


@router.get("/ingestion-status")
async def fixed_income_ingestion_status(session: AsyncSession = SessionDep) -> dict[str, Any]:
    result = await session.execute(
        text("""
            SELECT job_name, status, last_attempted_at, last_successful_at,
                   observations_seen, observations_inserted, symbols_missing,
                   stale_symbols, errors, updated_at
            FROM government_yield_ingestion_status
            ORDER BY updated_at DESC
        """)
    )
    return {"government_yield_jobs": [dict(row._mapping) for row in result]}


async def _latest_yield_rows(session: AsyncSession) -> list[dict[str, Any]]:
    result = await session.execute(
        text("""
            SELECT DISTINCT ON (country_code, maturity)
                provider,
                provider_symbol,
                country_code,
                currency_code,
                maturity,
                yield_value,
                market_observation_date,
                provider_timestamp,
                ingested_at,
                market_timezone,
                data_frequency,
                source_type,
                quality_status,
                observation_kind
            FROM government_yield_observations
            WHERE quality_status IN ('valid', 'stale')
            ORDER BY country_code, maturity, market_observation_date DESC, ingested_at DESC, id DESC
        """)
    )
    return [dict(row._mapping) for row in result]


async def _aligned_rates_fx_series(
    session: AsyncSession,
    pair: str,
    maturity: str,
    window: int,
) -> list[dict[str, Any]]:
    base_country, quote_country = FX_PAIR_COUNTRIES[pair]
    result = await session.execute(
        text("""
            WITH base_y AS (
                SELECT DISTINCT ON (market_observation_date)
                    market_observation_date AS obs_date,
                    yield_value::float AS base_yield
                FROM government_yield_observations
                WHERE country_code = :base_country
                  AND maturity = :maturity
                  AND quality_status = 'valid'
                ORDER BY market_observation_date, ingested_at DESC, id DESC
            ),
            quote_y AS (
                SELECT DISTINCT ON (market_observation_date)
                    market_observation_date AS obs_date,
                    yield_value::float AS quote_yield
                FROM government_yield_observations
                WHERE country_code = :quote_country
                  AND maturity = :maturity
                  AND quality_status = 'valid'
                ORDER BY market_observation_date, ingested_at DESC, id DESC
            ),
            fx AS (
                SELECT DISTINCT ON (observation_date)
                    observation_date AS obs_date,
                    close_value::float AS fx_close
                FROM fx_spot_observations
                WHERE pair = :pair
                  AND quality_status = 'valid'
                ORDER BY observation_date, ingested_at DESC, id DESC
            )
            SELECT fx.obs_date AS date,
                   base_y.base_yield,
                   quote_y.quote_yield,
                   base_y.base_yield - quote_y.quote_yield AS differential,
                   fx.fx_close
            FROM fx
            JOIN base_y USING (obs_date)
            JOIN quote_y USING (obs_date)
            ORDER BY fx.obs_date DESC
            LIMIT :window
        """),
        {
            "pair": pair,
            "base_country": base_country,
            "quote_country": quote_country,
            "maturity": maturity,
            "window": window,
        },
    )
    rows = [dict(row._mapping) for row in result]
    rows.reverse()
    return rows


def _curves_by_country(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    curves: dict[str, dict[str, float]] = defaultdict(dict)
    for row in rows:
        value = row["yield_value"]
        curves[row["country_code"]][row["maturity"]] = float(value)
    return dict(curves)


def _curves_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_country: dict[str, dict[str, Any]] = defaultdict(lambda: {"curve": {}, "observations": []})
    for row in rows:
        country = row["country_code"]
        by_country[country]["curve"][row["maturity"]] = float(row["yield_value"])
        by_country[country]["observations"].append(_row_payload(row))
    for _country, payload in by_country.items():
        ordered_curve = {
            maturity: payload["curve"][maturity]
            for maturity in GBOND_MATURITIES
            if maturity in payload["curve"]
        }
        payload["curve"] = ordered_curve
        payload["slopes"] = curve_slopes(ordered_curve)
        payload["inversion_status"] = _inversion_status(ordered_curve)
    return dict(by_country)


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _jsonable(row[key])
        for key in (
            "provider",
            "provider_symbol",
            "currency_code",
            "maturity",
            "yield_value",
            "market_observation_date",
            "provider_timestamp",
            "ingested_at",
            "market_timezone",
            "data_frequency",
            "source_type",
            "quality_status",
            "observation_kind",
        )
    }


def _quality_for(rows: list[dict[str, Any]], country: str, maturity: str) -> dict[str, Any] | None:
    for row in rows:
        if row["country_code"] == country and row["maturity"] == maturity:
            return {
                "source_type": row["source_type"],
                "quality_status": row["quality_status"],
                "observation_kind": row["observation_kind"],
                "market_observation_date": _jsonable(row["market_observation_date"]),
            }
    return None


def _pair_quality(rows: list[dict[str, Any]], base: str, quote: str, maturity: str) -> dict[str, Any]:
    return {
        "base": _quality_for(rows, base, maturity),
        "quote": _quality_for(rows, quote, maturity),
    }


def _timestamp_alignment(rows: list[dict[str, Any]], base: str, quote: str, maturity: str) -> dict[str, Any]:
    base_quality = _quality_for(rows, base, maturity)
    quote_quality = _quality_for(rows, quote, maturity)
    base_date = base_quality.get("market_observation_date") if base_quality else None
    quote_date = quote_quality.get("market_observation_date") if quote_quality else None
    return {
        "base_date": base_date,
        "quote_date": quote_date,
        "aligned": base_date is not None and base_date == quote_date,
    }


def _freshness_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        row["provider_symbol"]: {
            "market_observation_date": _jsonable(row["market_observation_date"]),
            "ingested_at": _jsonable(row["ingested_at"]),
            "quality_status": row["quality_status"],
        }
        for row in rows
    }


async def _yield_change_rows(session: AsyncSession, maturity: str, lookback_rows: int) -> list[dict[str, Any]]:
    result = await session.execute(
        text("""
            WITH ranked AS (
                SELECT country_code, maturity, market_observation_date, yield_value::float AS yield_value,
                       row_number() OVER (
                           PARTITION BY country_code, maturity
                           ORDER BY market_observation_date DESC, ingested_at DESC, id DESC
                       ) AS rn
                FROM government_yield_observations
                WHERE maturity = :maturity
                  AND quality_status = 'valid'
            )
            SELECT latest.country_code,
                   latest.market_observation_date AS latest_date,
                   prior.market_observation_date AS prior_date,
                   (latest.yield_value - prior.yield_value) * 100 AS change_bps
            FROM ranked latest
            JOIN ranked prior
              ON prior.country_code = latest.country_code
             AND prior.maturity = latest.maturity
             AND prior.rn = :prior_rank
            WHERE latest.rn = 1
            ORDER BY abs((latest.yield_value - prior.yield_value) * 100) DESC
        """),
        {"maturity": maturity, "prior_rank": lookback_rows + 1},
    )
    return [
        {
            "country_code": row.country_code,
            "latest_date": row.latest_date.isoformat() if row.latest_date else None,
            "prior_date": row.prior_date.isoformat() if row.prior_date else None,
            "change_bps": row.change_bps,
            "maturity": maturity,
            "source_type": "licensed_api",
            "quality_status": "valid",
        }
        for row in result
    ]


async def _country_policy_changes(session: AsyncSession, country: str, lookback_rows: int) -> dict[str, float]:
    changes: dict[str, float] = {}
    for maturity in ("3M", "6M", "1Y", "2Y"):
        history = await _country_curve_history(session, country, lookbacks=(lookback_rows,))
        change = _curve_change_bps(history["curves"], maturity, f"{lookback_rows}d")
        if change is not None:
            changes[maturity] = change
    return changes


async def _pair_repricing_rows(session: AsyncSession, maturity: str, lookback_rows: int) -> list[dict[str, Any]]:
    latest_rows = await _latest_yield_rows(session)
    curves = _curves_by_country(latest_rows)
    country_changes: dict[str, float | None] = {}
    for country in curves:
        history = await _country_curve_history(session, country, lookbacks=(lookback_rows,))
        country_changes[country] = _curve_change_bps(history["curves"], maturity, f"{lookback_rows}d")

    rows: list[dict[str, Any]] = []
    for pair, (base_country, quote_country) in FX_PAIR_COUNTRIES.items():
        base_change = country_changes.get(base_country)
        quote_change = country_changes.get(quote_country)
        if base_change is None or quote_change is None:
            continue
        rows.append({
            "pair": pair,
            "maturity": maturity,
            "lookback_window": f"{lookback_rows} observations",
            "base_country": base_country,
            "quote_country": quote_country,
            "base_change_bps": base_change,
            "quote_change_bps": quote_change,
            "relative_repricing_bps": base_change - quote_change,
            "interpretation": "Positive means base-country yield repriced more hawkishly than quote-country yield.",
        })
    return sorted(rows, key=lambda item: abs(item["relative_repricing_bps"]), reverse=True)


async def _country_curve_history(
    session: AsyncSession,
    country: str,
    lookbacks: tuple[int, ...] = (1, 5, 20),
) -> dict[str, Any]:
    max_rank = max(lookbacks, default=1) + 1
    result = await session.execute(
        text("""
            WITH ranked AS (
                SELECT maturity, maturity_months, market_observation_date,
                       yield_value::float AS yield_value,
                       row_number() OVER (
                           PARTITION BY maturity
                           ORDER BY market_observation_date DESC, ingested_at DESC, id DESC
                       ) AS rn
                FROM government_yield_observations
                WHERE country_code = :country
                  AND quality_status = 'valid'
            )
            SELECT maturity, maturity_months, market_observation_date, yield_value, rn
            FROM ranked
            WHERE rn <= :max_rank
            ORDER BY maturity_months, rn
        """),
        {"country": country, "max_rank": max_rank},
    )
    by_rank: dict[int, dict[str, float]] = defaultdict(dict)
    dates_by_rank: dict[int, dict[str, str]] = defaultdict(dict)
    for row in result:
        by_rank[row.rn][row.maturity] = row.yield_value
        dates_by_rank[row.rn][row.maturity] = row.market_observation_date.isoformat()

    curves: dict[str, dict[str, Any]] = {
        "current": {
            "curve": _ordered_curve(by_rank.get(1, {})),
            "dates": dates_by_rank.get(1, {}),
        }
    }
    changes: dict[str, dict[str, float | None]] = {}
    for lookback in lookbacks:
        key = f"{lookback}d"
        prior = by_rank.get(lookback + 1, {})
        curves[key] = {
            "curve": _ordered_curve(prior),
            "dates": dates_by_rank.get(lookback + 1, {}),
        }
        changes[key] = {
            maturity: ((by_rank[1][maturity] - prior[maturity]) * 100 if maturity in by_rank.get(1, {}) and maturity in prior else None)
            for maturity in GBOND_MATURITIES
        }
    return {"curves": curves, "changes_bps": changes}


def _ordered_curve(curve: dict[str, float]) -> dict[str, float]:
    return {
        maturity: curve[maturity]
        for maturity in GBOND_MATURITIES
        if maturity in curve
    }


def _curve_change_bps(curves: dict[str, Any], maturity: str, lookback_key: str) -> float | None:
    current = curves.get("current", {}).get("curve", {})
    prior = curves.get(lookback_key, {}).get("curve", {})
    if maturity not in current or maturity not in prior:
        return None
    return (current[maturity] - prior[maturity]) * 100


def _slope_change_bps(curves: dict[str, Any], lookback_key: str) -> float | None:
    current = curves.get("current", {}).get("curve", {})
    prior = curves.get(lookback_key, {}).get("curve", {})
    current_slope = curve_slopes(current).get("2Y-10Y")
    prior_slope = curve_slopes(prior).get("2Y-10Y")
    if current_slope is None or prior_slope is None:
        return None
    return (current_slope - prior_slope) * 100


def _curve_shape(curve: dict[str, float]) -> dict[str, Any]:
    slope = curve_slopes(curve).get("2Y-10Y")
    if slope is None:
        label = "insufficient data"
    elif slope < 0:
        label = "inverted"
    elif slope < 0.25:
        label = "flat"
    elif slope < 1.00:
        label = "normal"
    else:
        label = "steep"
    return {
        "label": label,
        "slope_2y_10y_bps": slope * 100 if slope is not None else None,
        "convention": "10Y yield minus 2Y yield",
    }


def _curve_movement_from_history(history: dict[str, Any], lookback_key: str) -> dict[str, Any]:
    curves = history["curves"]
    short_change = _curve_change_bps(curves, "2Y", lookback_key)
    long_change = _curve_change_bps(curves, "10Y", lookback_key)
    if short_change is None or long_change is None:
        return {
            "label": "Insufficient history",
            "lookback_window": lookback_key,
            "short_end_change_bps": short_change,
            "long_end_change_bps": long_change,
            "slope_change_bps": None,
            "confidence": 0.0,
        }
    movement = classify_curve_change(
        short_end_change_bps=short_change,
        long_end_change_bps=long_change,
        lookback=lookback_key,
    )
    return {
        "label": movement.classification,
        "lookback_window": movement.lookback,
        "short_end_change_bps": movement.short_end_change_bps,
        "long_end_change_bps": movement.long_end_change_bps,
        "slope_change_bps": movement.slope_change_bps,
        "confidence": movement.confidence,
        "explanation": movement.explanation,
    }


def _latest_ingestion_success(rows: list[dict[str, Any]]) -> str | None:
    ingested = [row["ingested_at"] for row in rows if row.get("ingested_at")]
    return _jsonable(max(ingested)) if ingested else None


def _inversion_status(curve: dict[str, float]) -> dict[str, Any]:
    slope = None
    if curve.get("2Y") is not None and curve.get("10Y") is not None:
        slope = curve["10Y"] - curve["2Y"]
    return {
        "slope_2y_10y": slope,
        "is_inverted": slope is not None and slope < 0,
        "convention": "10Y yield minus 2Y yield",
    }


def _conventions() -> dict[str, str]:
    return {
        "curve_slope": "Longer maturity yield minus shorter maturity yield.",
        "fx_differential": "Base-currency yield minus quote-currency yield.",
        "rates_implied_estimate": "Regression estimate from rates; not objective fundamental value.",
    }


async def _yield_coverage(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        text("""
            SELECT country_code, maturity, COUNT(*) AS rows,
                   MIN(market_observation_date) AS earliest,
                   MAX(market_observation_date) AS latest,
                   COUNT(*) - COUNT(DISTINCT market_observation_date) AS duplicate_count
            FROM government_yield_observations
            GROUP BY country_code, maturity
            ORDER BY country_code, maturity
        """)
    )
    return {
        f"{row.country_code}:{row.maturity}": {
            "rows": row.rows,
            "earliest": row.earliest.isoformat() if row.earliest else None,
            "latest": row.latest.isoformat() if row.latest else None,
            "duplicate_count": row.duplicate_count,
        }
        for row in result
    }


async def _fx_coverage(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(
        text("""
            SELECT pair, COUNT(*) AS rows,
                   MIN(observation_date) AS earliest,
                   MAX(observation_date) AS latest,
                   COUNT(*) - COUNT(DISTINCT observation_date) AS duplicate_count
            FROM fx_spot_observations
            GROUP BY pair
            ORDER BY pair
        """)
    )
    return {
        row.pair: {
            "rows": row.rows,
            "earliest": row.earliest.isoformat() if row.earliest else None,
            "latest": row.latest.isoformat() if row.latest else None,
            "duplicate_count": row.duplicate_count,
        }
        for row in result
    }


async def _rate_probability_source_quality(session: AsyncSession) -> dict[str, Any]:
    ois = await session.execute(
        text("""
            SELECT bank, source, max(curve_date) AS latest_curve_date, count(*) AS rows
            FROM ois_cache
            GROUP BY bank, source
            ORDER BY bank, source
        """)
    )
    scraped = await session.execute(
        text("""
            SELECT bank, max(updated_at) AS latest_update, count(*) AS rows
            FROM rp_scraped_summary
            GROUP BY bank
            ORDER BY bank
        """)
    )
    return {
        "ois_and_futures": [
            {
                "bank": row.bank,
                "source": row.source,
                **_source_dimensions_for_rate_source(row.source),
                "latest_date": row.latest_curve_date.isoformat() if row.latest_curve_date else None,
                "rows": row.rows,
                "freshness": _freshness_status(row.latest_curve_date),
                "quality_status": "stale" if _freshness_status(row.latest_curve_date)["is_stale"] else "valid",
            }
            for row in ois
        ],
        "third_party_scraped": [
            {
                "bank": row.bank,
                "source": "rateprobability.com",
                "provenance": "third-party display",
                "delivery_source": "rateprobability.com",
                "methodology": "scraped meeting-probability summary",
                "verification": "comparison-only",
                "latest_update": _jsonable(row.latest_update),
                "rows": row.rows,
                "freshness": _freshness_status(row.latest_update.date() if row.latest_update else None),
                "quality_status": "stale" if _freshness_status(row.latest_update.date() if row.latest_update else None)["is_stale"] else "provider-observed",
            }
            for row in scraped
        ],
        "selection_policy": (
            "OIS/futures curves are primary when present; scraped probabilities are comparison-only. "
            "Government-yield policy repricing is a proxy and is not combined into meeting probabilities."
        ),
    }


def _normalize_pair(pair: str) -> str:
    normalized = pair.upper().replace("-", "/")
    if "/" not in normalized and len(normalized) == 6:
        normalized = f"{normalized[:3]}/{normalized[3:]}"
    if normalized not in FX_PAIR_COUNTRIES:
        raise HTTPException(status_code=404, detail=f"Unsupported pair: {pair}")
    return normalized


def _series_change_bps(series: list[dict[str, Any]], lookback: int, field: str) -> float | None:
    if len(series) <= lookback:
        return None
    return (series[-1][field] - series[-1 - lookback][field]) * 100


def _residual_sigma(residual_history: list[float | Decimal]) -> float | None:
    clean = [float(value) for value in residual_history]
    if len(clean) < 2:
        return None
    return float(pstdev(clean))


def _adjusted_opportunity_score(
    residual_z_score: float | None,
    r_squared: float | None,
    gates: dict[str, bool],
) -> float:
    if residual_z_score is None or r_squared is None:
        return 0.0
    freshness = 1.0 if gates["data_fresh"] else 0.0
    stability = 1.0 if gates["model_stable"] else 0.5
    history = 1.0 if gates["sufficient_history"] else 0.0
    return round(abs(residual_z_score) * max(r_squared, 0.0) * freshness * stability * history, 4)


def _opportunity_bucket(score: float, gates: dict[str, bool]) -> str:
    if not gates["sufficient_history"]:
        return "insufficient history"
    if not gates["data_fresh"]:
        return "stale"
    if not gates["residual_material"]:
        return "low" if score > 0 else "none"
    if score >= 1.0 and gates["relationship_reliable"]:
        return "high"
    if score >= 0.45 and gates["relationship_reliable"]:
        return "medium"
    if score > 0:
        return "low"
    return "none"


def _source_quality_normalization() -> dict[str, str]:
    return {
        "official": "Official central-bank or official market data.",
        "licensed_api": "Licensed provider API observation retained with provider symbol and timestamp.",
        "exchange": "Exchange-published futures or cash-rate contract data.",
        "provider_observed": "Provider-observed market snapshot or scraped summary.",
        "proxy": "Directional proxy that must not be merged into meeting probabilities.",
        "third_party_scraped": "Scraped third-party probability display; comparison-only.",
        "model_estimated": "Regression or interpolation result generated by this application.",
        "stale": "Observation exists but freshness threshold failed.",
        "unavailable": "Required source has no usable observations.",
        "configured_unverified": "Configured source has not been conclusively verified as production-grade.",
    }


def _source_dimensions_for_rate_source(source: str | None) -> dict[str, str]:
    normalized = (source or "").lower()
    delivery = "configured source"
    if "yfinance" in normalized:
        delivery = "Yahoo Finance"
    elif "tradingview" in normalized:
        delivery = "TradingView"
    elif "ecb" in normalized:
        delivery = "ECB data service"
    elif "boe" in normalized:
        delivery = "Bank of England"
    elif "tfx" in normalized:
        delivery = "TFX"
    elif "rbnz" in normalized:
        delivery = "RBNZ"

    if "futures" in normalized or "zq" in normalized or "tradingview" in normalized or "tfx" in normalized:
        provenance = "exchange futures"
    elif "ecb" in normalized or "boe" in normalized or "rbnz" in normalized:
        provenance = "official source"
    else:
        provenance = "configured source"

    if "ois" in normalized or "sonia" in normalized or "estr" in normalized:
        methodology = "OIS-derived curve"
        verification = "market-derived"
    elif "proxy" in normalized or "yc" in normalized:
        methodology = "rates proxy, not meeting-level OIS"
        verification = "configured/unverified"
    elif "futures" in normalized or "zq" in normalized:
        methodology = "futures-derived proxy"
        verification = "market-derived proxy"
    else:
        methodology = "configured rates curve"
        verification = "configured/unverified"

    return {
        "provenance": provenance,
        "delivery_source": delivery,
        "methodology": methodology,
        "verification": verification,
    }


def _freshness_status(observation_date: date | None, stale_after_days: int = 7) -> dict[str, Any]:
    if observation_date is None:
        return {"is_stale": True, "age_days": None, "status": "unavailable"}
    age = (date.today() - observation_date).days
    if age > stale_after_days:
        return {"is_stale": True, "age_days": age, "status": "stale"}
    if age > 2:
        return {"is_stale": False, "age_days": age, "status": "watch"}
    return {"is_stale": False, "age_days": age, "status": "fresh"}


def _country_for_currency_or_code(value: str) -> str:
    normalized = value.upper()
    currency_to_country = {
        "USD": "US",
        "EUR": "DE",
        "GBP": "UK",
        "JPY": "JP",
        "AUD": "AU",
        "NZD": "NZ",
        "CAD": "CA",
        "CHF": "CH",
    }
    return currency_to_country.get(normalized, normalized)


def _maturity_sort_key(maturity: str) -> int:
    order = {maturity: idx for idx, maturity in enumerate(GBOND_MATURITIES)}
    return order.get(maturity, 999)


def _series_missing_inputs(series: list[dict[str, Any]], pair: str, maturity: str) -> list[str]:
    missing: list[str] = []
    if pair not in FX_PAIR_SYMBOLS:
        missing.append("FX spot history")
    if not series:
        missing.append(f"Aligned {maturity} yield/FX history")
    return missing


def _data_fresh(series: list[dict[str, Any]]) -> bool:
    if not series:
        return False
    latest = series[-1]["date"]
    if isinstance(latest, str):
        latest = date.fromisoformat(latest)
    return latest >= date.today() - timedelta(days=7)


def _mispricing_state(label: str, gates: dict[str, bool]) -> str:
    if not gates["sufficient_history"]:
        return "Insufficient history"
    if not gates["data_fresh"] or not gates["timestamps_aligned"]:
        return "Stale/misaligned data"
    if label == "weak relationship":
        return "Weak relationship"
    if not gates["model_stable"]:
        return "Unstable relationship"
    if not gates["residual_material"]:
        return "No material divergence"
    if gates["relationship_reliable"] and gates["residual_material"]:
        return "Actionable divergence"
    return "Latent divergence"


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    return value
