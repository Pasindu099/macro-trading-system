"""Deterministic fixed-income analytics for government-yield intelligence.

Conventions used throughout this module:

* Yield-curve slope = longer maturity yield minus shorter maturity yield.
* FX rate differential = base-currency yield minus quote-currency yield.
  A positive move in the differential is interpreted as rates support for the
  base currency versus the quote currency, all else equal.
* Regression fair value is labelled a rates-implied estimate, never objective
  fundamental value.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from math import sqrt
from statistics import mean, pstdev

CURVE_SLOPES: tuple[tuple[str, str], ...] = (
    ("3M", "2Y"),
    ("6M", "2Y"),
    ("1Y", "2Y"),
    ("2Y", "5Y"),
    ("2Y", "10Y"),
    ("5Y", "10Y"),
)

DIFFERENTIAL_MATURITIES: tuple[str, ...] = (
    "3M",
    "6M",
    "1Y",
    "2Y",
    "3Y",
    "5Y",
    "10Y",
)

FX_PAIR_COUNTRIES: dict[str, tuple[str, str]] = {
    "EUR/USD": ("DE", "US"),
    "GBP/USD": ("UK", "US"),
    "USD/JPY": ("US", "JP"),
    "AUD/USD": ("AU", "US"),
    "NZD/USD": ("NZ", "US"),
    "USD/CAD": ("US", "CA"),
    "USD/CHF": ("US", "CH"),
    "EUR/GBP": ("DE", "UK"),
    "EUR/JPY": ("DE", "JP"),
    "GBP/JPY": ("UK", "JP"),
    "AUD/JPY": ("AU", "JP"),
    "AUD/NZD": ("AU", "NZ"),
    "EUR/CHF": ("DE", "CH"),
    "CAD/JPY": ("CA", "JP"),
}

DEFAULT_POLICY_REPRICING_WEIGHTS: dict[str, float] = {
    "3M": 0.30,
    "6M": 0.25,
    "1Y": 0.25,
    "2Y": 0.20,
}


@dataclass(frozen=True, slots=True)
class CurveClassification:
    classification: str
    short_end_change_bps: float
    long_end_change_bps: float
    slope_change_bps: float
    lookback: str
    confidence: float
    explanation: str


@dataclass(frozen=True, slots=True)
class DifferentialPoint:
    pair: str
    maturity: str
    base_country: str
    quote_country: str
    differential: float
    interpretation: str


@dataclass(frozen=True, slots=True)
class PolicyRepricingScore:
    country_code: str
    lookback: str
    score: float
    label: str
    component_contributions: dict[str, float]


@dataclass(frozen=True, slots=True)
class RatesFxConfirmation:
    pair: str
    state: str
    differential_change_bps: float
    fx_return_pct: float
    evidence: list[str]


@dataclass(frozen=True, slots=True)
class MispricingAssessment:
    label: str
    rates_implied_estimate: float | None
    residual: float | None
    residual_z_score: float | None
    relationship_strength: str
    confidence: float
    reasons: list[str]


@dataclass(frozen=True, slots=True)
class ShockClassification:
    classification: str
    supporting_evidence: list[str]
    contradictory_evidence: list[str]
    confidence: float
    missing_inputs: list[str]
    alternative_classification: str


def curve_slope(curve: dict[str, float | Decimal], short_maturity: str, long_maturity: str) -> float | None:
    """Return slope in percentage points: long maturity yield minus short maturity yield."""
    short_yield = _as_float(curve.get(short_maturity))
    long_yield = _as_float(curve.get(long_maturity))
    if short_yield is None or long_yield is None:
        return None
    return long_yield - short_yield


def curve_slopes(curve: dict[str, float | Decimal]) -> dict[str, float | None]:
    """Return configured curve slopes using the module sign convention."""
    return {
        f"{short}-{long}": curve_slope(curve, short, long)
        for short, long in CURVE_SLOPES
    }


def classify_curve_change(
    *,
    short_end_change_bps: float,
    long_end_change_bps: float,
    lookback: str,
    min_move_bps: float = 3.0,
) -> CurveClassification:
    """Classify a curve move using slope = long-end change minus short-end change."""
    slope_change_bps = long_end_change_bps - short_end_change_bps
    short_meaningful = abs(short_end_change_bps) >= min_move_bps
    long_meaningful = abs(long_end_change_bps) >= min_move_bps
    slope_meaningful = abs(slope_change_bps) >= min_move_bps

    if not short_meaningful and not long_meaningful:
        label = "Mixed/unclear"
        confidence = 0.2
        explanation = "Curve movement is below the configured minimum threshold."
    elif short_end_change_bps > 0 and long_end_change_bps > 0:
        if slope_meaningful and slope_change_bps < 0:
            label = "Bear flattening"
        elif slope_meaningful and slope_change_bps > 0:
            label = "Bear steepening"
        else:
            label = "Parallel rise"
        confidence = _classification_confidence(short_end_change_bps, long_end_change_bps, min_move_bps)
        explanation = "Yields rose; slope change determines flattening versus steepening."
    elif short_end_change_bps < 0 and long_end_change_bps < 0:
        if slope_meaningful and slope_change_bps < 0:
            label = "Bull flattening"
        elif slope_meaningful and slope_change_bps > 0:
            label = "Bull steepening"
        else:
            label = "Parallel fall"
        confidence = _classification_confidence(short_end_change_bps, long_end_change_bps, min_move_bps)
        explanation = "Yields fell; slope change determines flattening versus steepening."
    else:
        label = "Mixed/unclear"
        confidence = 0.45 if (short_meaningful or long_meaningful) else 0.2
        explanation = "Short and long ends moved in opposite directions."

    return CurveClassification(
        classification=label,
        short_end_change_bps=short_end_change_bps,
        long_end_change_bps=long_end_change_bps,
        slope_change_bps=slope_change_bps,
        lookback=lookback,
        confidence=round(min(confidence, 1.0), 3),
        explanation=explanation,
    )


def differential_for_pair(
    pair: str,
    maturity: str,
    yields_by_country: dict[str, dict[str, float | Decimal]],
) -> DifferentialPoint | None:
    """Return base-country yield minus quote-country yield for an FX pair."""
    normalized_pair = pair.upper()
    if normalized_pair not in FX_PAIR_COUNTRIES:
        raise ValueError(f"Unsupported FX pair: {pair!r}")
    mat = maturity.upper()
    base_country, quote_country = FX_PAIR_COUNTRIES[normalized_pair]
    base_yield = _as_float(yields_by_country.get(base_country, {}).get(mat))
    quote_yield = _as_float(yields_by_country.get(quote_country, {}).get(mat))
    if base_yield is None or quote_yield is None:
        return None
    differential = base_yield - quote_yield
    return DifferentialPoint(
        pair=normalized_pair,
        maturity=mat,
        base_country=base_country,
        quote_country=quote_country,
        differential=differential,
        interpretation=(
            "Positive differential supports the base currency versus the quote "
            "currency, all else equal."
        ),
    )


def rolling_z_score(values: list[float | Decimal], latest: float | Decimal | None = None) -> float | None:
    """Return population z-score for the latest value against historical values."""
    sample = [_as_float(v) for v in values]
    clean = [v for v in sample if v is not None]
    target = _as_float(latest) if latest is not None else (clean[-1] if clean else None)
    if target is None or len(clean) < 2:
        return None
    sigma = pstdev(clean)
    if sigma == 0:
        return 0.0
    return (target - mean(clean)) / sigma


def historical_percentile(values: list[float | Decimal], latest: float | Decimal | None = None) -> float | None:
    """Return percentile rank from 0 to 100 for latest versus historical values."""
    clean = [v for v in (_as_float(value) for value in values) if v is not None]
    target = _as_float(latest) if latest is not None else (clean[-1] if clean else None)
    if target is None or not clean:
        return None
    below_or_equal = sum(1 for value in clean if value <= target)
    return 100.0 * below_or_equal / len(clean)


def policy_repricing_proxy(
    *,
    country_code: str,
    changes_bps: dict[str, float | Decimal],
    lookback: str,
    weights: dict[str, float] | None = None,
) -> PolicyRepricingScore:
    """Calculate a government-yield policy repricing proxy.

    This is directional only. It is not an OIS-implied rate, exact cuts priced,
    market-implied probability, or meeting probability.
    """
    active_weights = weights or DEFAULT_POLICY_REPRICING_WEIGHTS
    contributions: dict[str, float] = {}
    score = 0.0
    for maturity, weight in active_weights.items():
        change = _as_float(changes_bps.get(maturity))
        if change is None:
            continue
        contribution = change * weight
        contributions[maturity] = contribution
        score += contribution

    return PolicyRepricingScore(
        country_code=country_code,
        lookback=lookback,
        score=score,
        label="Government-yield policy repricing proxy",
        component_contributions=contributions,
    )


def relationship_strength(correlation: float | None, r_squared: float | None) -> str:
    """Coarse reliability bucket for rates-FX relationship gating."""
    if correlation is None or r_squared is None:
        return "insufficient history"
    if r_squared < 0.15 or abs(correlation) < 0.35:
        return "weak relationship"
    if r_squared < 0.35 or abs(correlation) < 0.55:
        return "moderate relationship"
    return "strong relationship"


def assess_rates_fx_confirmation(
    *,
    pair: str,
    differential_change_bps: float,
    fx_return_pct: float,
    min_differential_bps: float = 5.0,
    min_fx_return_pct: float = 0.15,
    relationship_reliable: bool = True,
) -> RatesFxConfirmation:
    """Compare base-minus-quote rates signal with FX response."""
    evidence: list[str] = []
    if not relationship_reliable:
        return RatesFxConfirmation(
            pair=pair.upper(),
            state="Relationship unreliable",
            differential_change_bps=differential_change_bps,
            fx_return_pct=fx_return_pct,
            evidence=["Historical rates-FX relationship is not reliable enough."],
        )
    if abs(differential_change_bps) < min_differential_bps:
        return RatesFxConfirmation(
            pair=pair.upper(),
            state="No meaningful rates signal",
            differential_change_bps=differential_change_bps,
            fx_return_pct=fx_return_pct,
            evidence=["Differential move is below threshold."],
        )

    rates_support_base = differential_change_bps > 0
    fx_supports_base = fx_return_pct > min_fx_return_pct
    fx_weakens_base = fx_return_pct < -min_fx_return_pct
    evidence.append(
        f"Base-minus-quote differential changed by {differential_change_bps:.1f} bps."
    )
    evidence.append(f"FX return was {fx_return_pct:.2f}%.")

    if rates_support_base and fx_supports_base or not rates_support_base and fx_weakens_base:
        state = "Confirmed"
    elif abs(fx_return_pct) < min_fx_return_pct:
        state = "Latent divergence"
    elif (rates_support_base and fx_weakens_base) or ((not rates_support_base) and fx_supports_base):
        state = "Contradiction"
    else:
        state = "Partially confirmed"

    return RatesFxConfirmation(
        pair=pair.upper(),
        state=state,
        differential_change_bps=differential_change_bps,
        fx_return_pct=fx_return_pct,
        evidence=evidence,
    )


def assess_mispricing(
    *,
    spot_fx: float,
    rates_implied_estimate: float | None,
    residual_history: list[float | Decimal],
    correlation: float | None,
    r_squared: float | None,
    data_fresh: bool,
    timestamps_aligned: bool,
    residual_z_threshold: float = 1.5,
) -> MispricingAssessment:
    """Gate rates-FX residuals before describing them as meaningful divergence."""
    strength = relationship_strength(correlation, r_squared)
    reasons: list[str] = []
    if not data_fresh:
        reasons.append("stale data")
    if not timestamps_aligned:
        reasons.append("timestamps misaligned")
    if strength in {"weak relationship", "insufficient history"}:
        reasons.append(strength)
    if rates_implied_estimate is None:
        reasons.append("insufficient history")
        residual = None
    else:
        residual = spot_fx - rates_implied_estimate
    z_score = rolling_z_score(residual_history, latest=residual) if residual is not None else None
    if z_score is None:
        reasons.append("insufficient residual history")
    elif abs(z_score) < residual_z_threshold:
        reasons.append("apparent divergence only")

    if reasons:
        label = reasons[0]
        confidence = 0.2 if strength == "weak relationship" else 0.35
    else:
        label = "meaningful rates-FX divergence"
        confidence = min(0.9, 0.45 + (abs(z_score or 0) / 5))

    return MispricingAssessment(
        label=label,
        rates_implied_estimate=rates_implied_estimate,
        residual=residual,
        residual_z_score=z_score,
        relationship_strength=strength,
        confidence=round(confidence, 3),
        reasons=reasons,
    )


def classify_cross_asset_shock(
    *,
    two_year_change_bps: float | None = None,
    ten_year_change_bps: float | None = None,
    slope_2s10s_change_bps: float | None = None,
    dxy_return_pct: float | None = None,
    gold_return_pct: float | None = None,
    vix_change_pct: float | None = None,
    economic_surprise: float | None = None,
    cb_news_tone: str | None = None,
) -> ShockClassification:
    """Rule-based shock classifier using only currently verified project inputs."""
    support: list[str] = []
    contradiction: list[str] = []
    missing = [
        name for name, value in {
            "2Y yield": two_year_change_bps,
            "10Y yield": ten_year_change_bps,
            "2s10s slope": slope_2s10s_change_bps,
            "DXY": dxy_return_pct,
            "Gold": gold_return_pct,
            "VIX": vix_change_pct,
            "Economic surprise": economic_surprise,
            "Central-bank news": cb_news_tone,
        }.items()
        if value is None
    ]

    classification = "Mixed/unclear"
    alternative = "Positive growth shock"
    confidence = 0.25

    if two_year_change_bps is not None and two_year_change_bps >= 7:
        classification = "Hawkish policy shock"
        support.append("2Y yield rose materially, consistent with front-end policy repricing.")
        confidence = 0.45
        if dxy_return_pct is not None and dxy_return_pct > 0:
            support.append("DXY strengthened alongside the front-end rates move.")
            confidence += 0.15
        if gold_return_pct is not None and gold_return_pct > 0:
            contradiction.append("Gold rose despite higher front-end yields.")
    elif two_year_change_bps is not None and two_year_change_bps <= -7:
        classification = "Dovish policy shock"
        support.append("2Y yield fell materially, consistent with dovish repricing.")
        confidence = 0.45
        if gold_return_pct is not None and gold_return_pct > 0:
            support.append("Gold rose alongside lower front-end yields.")
            confidence += 0.10
        if dxy_return_pct is not None and dxy_return_pct > 0:
            contradiction.append("DXY strengthened despite lower front-end yields.")
    elif vix_change_pct is not None and vix_change_pct >= 8:
        classification = "Negative growth/risk-off shock"
        support.append("VIX rose materially, consistent with risk-off conditions.")
        confidence = 0.40
        if dxy_return_pct is not None and dxy_return_pct > 0:
            support.append("DXY strengthened, consistent with defensive demand.")
            confidence += 0.10
    elif ten_year_change_bps is not None and ten_year_change_bps >= 7 and (slope_2s10s_change_bps or 0) > 3:
        classification = "Long-end/term-premium pressure"
        support.append("10Y yield rose and 2s10s steepened.")
        confidence = 0.45
    elif economic_surprise is not None and economic_surprise > 0:
        classification = "Positive growth shock"
        support.append("Economic surprise was positive.")
        confidence = 0.35

    if cb_news_tone in {"hawkish", "dovish"}:
        support.append(f"Central-bank news tone appears {cb_news_tone}.")
        confidence += 0.10

    return ShockClassification(
        classification=classification,
        supporting_evidence=support,
        contradictory_evidence=contradiction,
        confidence=round(min(confidence, 0.9), 3),
        missing_inputs=missing,
        alternative_classification=alternative if alternative != classification else "Mixed/unclear",
    )


def rolling_regression(x_values: list[float | Decimal], y_values: list[float | Decimal]) -> dict[str, float | None]:
    """Simple OLS y = alpha + beta*x with correlation and R-squared."""
    x = [v for v in (_as_float(value) for value in x_values) if v is not None]
    y = [v for v in (_as_float(value) for value in y_values) if v is not None]
    if len(x) != len(y) or len(x) < 3:
        return {"alpha": None, "beta": None, "correlation": None, "r_squared": None}

    x_mean = mean(x)
    y_mean = mean(y)
    x_var = sum((value - x_mean) ** 2 for value in x)
    y_var = sum((value - y_mean) ** 2 for value in y)
    if x_var == 0 or y_var == 0:
        return {"alpha": None, "beta": None, "correlation": None, "r_squared": None}

    covariance = sum((x_i - x_mean) * (y_i - y_mean) for x_i, y_i in zip(x, y, strict=True))
    beta = covariance / x_var
    alpha = y_mean - beta * x_mean
    correlation = covariance / sqrt(x_var * y_var)
    return {
        "alpha": alpha,
        "beta": beta,
        "correlation": correlation,
        "r_squared": correlation**2,
    }


def _classification_confidence(short_change: float, long_change: float, threshold: float) -> float:
    magnitude = (abs(short_change) + abs(long_change)) / 2
    return max(0.35, min(1.0, magnitude / (threshold * 4)))


def _as_float(value: float | Decimal | int | None) -> float | None:
    if value is None:
        return None
    return float(value)
