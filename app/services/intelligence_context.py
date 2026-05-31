"""Build structured currency context for the intelligence pipeline.

This module gathers the latest DB-backed macro state for the eight tracked FX
currencies and shapes it into a JSON-serialisable payload for Claude-powered
macro briefs. It deliberately avoids live external fetches: unavailable signals
are represented with explicit ``data_available`` flags so downstream prompts can
calibrate confidence instead of reasoning from absent data.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.db.session import session_scope

logger = logging.getLogger(__name__)

TRACKED_CURRENCIES = ("USD", "EUR", "GBP", "JPY", "AUD", "NZD", "CAD", "CHF")
TENORS = ("1m", "3m", "6m", "1y", "3y", "5y", "10y")
CURRENCY_TO_BANK = {
    "USD": "FED",
    "EUR": "ECB",
    "GBP": "BOE",
    "JPY": "BOJ",
    "AUD": "RBA",
    "NZD": "RBNZ",
    "CAD": "BOC",
    "CHF": "SNB",
}
CENTRAL_BANK_ALIASES = {
    "FED": "FED",
    "FEDERAL RESERVE": "FED",
    "FOMC": "FED",
    "ECB": "ECB",
    "EUROPEAN CENTRAL BANK": "ECB",
    "BOE": "BOE",
    "BANK OF ENGLAND": "BOE",
    "BOJ": "BOJ",
    "BANK OF JAPAN": "BOJ",
    "RBA": "RBA",
    "RESERVE BANK OF AUSTRALIA": "RBA",
    "RBNZ": "RBNZ",
    "RESERVE BANK OF NEW ZEALAND": "RBNZ",
    "BOC": "BOC",
    "BANK OF CANADA": "BOC",
    "SNB": "SNB",
    "SWISS NATIONAL BANK": "SNB",
}


async def build_currency_context() -> dict[str, Any]:
    """Return the full eight-currency context payload for macro brief generation."""
    async with session_scope() as session:
        countries = await _fetch_countries(session)
        cb_scores = await _fetch_cb_preferred_scores(session)
        stances = await _fetch_currency_stance(session)
        surprises = await _fetch_recent_surprises(session)
        rate_probabilities = await _fetch_rate_probabilities(session)

    retail_sentiment = _load_cached_retail_sentiment()
    cb_rss = _load_cached_cb_rss_analysis()

    profiles = {
        currency: _build_profile(
            currency=currency,
            countries=countries,
            cb_scores=cb_scores,
            stances=stances,
            surprises=surprises,
            rate_probabilities=rate_probabilities,
            retail_sentiment=retail_sentiment,
            cb_rss=cb_rss,
        )
        for currency in TRACKED_CURRENCIES
    }
    ctx = {
        "generated_at": datetime.now(UTC).isoformat(),
        "currencies": profiles,
    }
    ctx["validation"] = validate_currency_context(ctx)
    return ctx


async def build_pair_context(base: str, quote: str) -> dict[str, Any]:
    """Return two currency profiles plus pre-computed pair differentials."""
    base = base.upper()
    quote = quote.upper()
    if base not in TRACKED_CURRENCIES or quote not in TRACKED_CURRENCIES:
        raise ValueError(f"Unsupported currency pair {base}/{quote}")

    context = await build_currency_context()
    base_profile = context["currencies"][base]
    quote_profile = context["currencies"][quote]
    return {
        "base": base_profile,
        "quote": quote_profile,
        "differential": _build_differential(base_profile, quote_profile),
        "validation": context["validation"],
    }


def validate_currency_context(ctx: dict[str, Any]) -> dict[str, Any]:
    """Validate context coverage and return a lightweight data quality summary."""
    currencies = ctx.get("currencies")
    present = set(currencies) if isinstance(currencies, dict) else set()
    missing = [currency for currency in TRACKED_CURRENCIES if currency not in present]
    unavailable_count = _count_unavailable(ctx)
    availability_count = _count_availability_flags(ctx)
    populated_count = max(0, availability_count - unavailable_count)
    data_quality_score = (
        round((populated_count / availability_count) * 100, 1)
        if availability_count
        else 0.0
    )
    if missing:
        data_quality_score = 0.0
    if data_quality_score < 60:
        logger.warning("Currency context data quality is low: %.1f", data_quality_score)
    return {
        "all_currencies_present": not missing,
        "missing_currencies": missing,
        "data_unavailable_fields": unavailable_count,
        "data_available_fields": populated_count,
        "data_quality_score": data_quality_score,
    }


async def _fetch_countries(session: Any) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT code, currency_code, central_bank
            FROM countries
            WHERE currency_code = ANY(:currencies)
            """
        ),
        {"currencies": list(TRACKED_CURRENCIES)},
    )
    rows = [dict(row) for row in result.mappings().all()]
    return {row["currency_code"]: row for row in rows}


async def _fetch_cb_preferred_scores(session: Any) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (currency)
                currency,
                country_code,
                cb_strength_score,
                strength_label,
                trend_label,
                confidence,
                inflation_score,
                labor_score,
                growth_score
            FROM processed.cb_preferred_score
            WHERE currency = ANY(:currencies)
            ORDER BY currency, date DESC
            """
        ),
        {"currencies": list(TRACKED_CURRENCIES)},
    )
    return {row["currency"]: dict(row) for row in result.mappings().all()}


async def _fetch_currency_stance(session: Any) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            """
            SELECT DISTINCT ON (currency)
                currency,
                country_code,
                overall_stance_score,
                overall_stance_label,
                trend_label,
                inflation_score,
                labor_score,
                growth_score,
                inflation_direction,
                labor_direction,
                growth_direction,
                contribution_breakdown
            FROM processed.currency_stance
            WHERE currency = ANY(:currencies)
              AND window_months = 3
            ORDER BY currency, date DESC
            """
        ),
        {"currencies": list(TRACKED_CURRENCIES)},
    )
    return {row["currency"]: dict(row) for row in result.mappings().all()}


async def _fetch_recent_surprises(session: Any) -> dict[str, list[dict[str, Any]]]:
    result = await session.execute(
        text(
            """
            WITH ranked AS (
                SELECT
                    i.country_code,
                    i.canonical_name,
                    i.display_name,
                    i.is_higher_better_for_currency,
                    r.actual,
                    r.estimate,
                    r.previous,
                    r.surprise,
                    r.released_at,
                    row_number() OVER (
                        PARTITION BY i.country_code
                        ORDER BY r.released_at DESC
                    ) AS rn
                FROM indicator_releases r
                JOIN indicators i ON i.id = r.indicator_id
                WHERE r.is_latest = true
                  AND i.importance >= 3
                  AND r.released_at >= now() - interval '45 days'
                  AND r.actual IS NOT NULL
            )
            SELECT *
            FROM ranked
            WHERE rn <= 5
            ORDER BY country_code, released_at DESC
            """
        )
    )
    by_country: dict[str, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        item = dict(row)
        country_code = item.pop("country_code")
        item.pop("rn", None)
        by_country.setdefault(country_code, []).append(
            {
                "indicator": item["canonical_name"],
                "display_name": item["display_name"],
                "actual": _to_float(item["actual"]),
                "forecast": _to_float(item["estimate"]),
                "previous": _to_float(item["previous"]),
                "surprise": _to_float(item["surprise"]),
                "surprise_direction": _surprise_direction(
                    item["actual"],
                    item["estimate"],
                    item["is_higher_better_for_currency"],
                ),
                "release_date": _date_iso(item["released_at"]),
            }
        )
    return by_country


async def _fetch_rate_probabilities(session: Any) -> dict[str, dict[str, Any]]:
    result = await session.execute(
        text(
            """
            WITH next_meetings AS (
                SELECT DISTINCT ON (bank)
                    bank,
                    meeting_dt
                FROM cb_meetings
                WHERE meeting_dt > now()
                ORDER BY bank, meeting_dt ASC
            ),
            latest_snapshots AS (
                SELECT DISTINCT ON (bank)
                    bank,
                    snapshot_date
                FROM rate_snapshots
                ORDER BY bank, snapshot_date DESC
            )
            SELECT
                s.bank,
                m.meeting_dt,
                s.cut_prob,
                s.hold_prob,
                s.hike_prob,
                s.delta_bps
            FROM next_meetings m
            JOIN latest_snapshots l ON l.bank = m.bank
            JOIN rate_snapshots s
              ON s.bank = l.bank
             AND s.snapshot_date = l.snapshot_date
             AND s.meeting_dt = m.meeting_dt
            """
        )
    )
    return {row["bank"]: dict(row) for row in result.mappings().all()}


def _build_profile(
    *,
    currency: str,
    countries: dict[str, dict[str, Any]],
    cb_scores: dict[str, dict[str, Any]],
    stances: dict[str, dict[str, Any]],
    surprises: dict[str, list[dict[str, Any]]],
    rate_probabilities: dict[str, dict[str, Any]],
    retail_sentiment: dict[str, dict[str, Any]],
    cb_rss: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    country = countries.get(currency, {})
    cb_score = cb_scores.get(currency)
    stance = stances.get(currency)
    country_code = (
        (cb_score or {}).get("country_code")
        or (stance or {}).get("country_code")
        or country.get("code")
    )
    bank = _bank_code(currency, country)
    rate_probability = rate_probabilities.get(bank or "")

    profile = {
        "currency": currency,
        "country_code": country_code,
        "cb_preferred": _availability(bool(cb_score)),
        "cb_preferred_score": _to_float((cb_score or {}).get("cb_strength_score")),
        "cb_preferred_label": _title((cb_score or {}).get("strength_label")),
        "cb_preferred_trend": _title((cb_score or {}).get("trend_label")),
        "cb_preferred_confidence": _title((cb_score or {}).get("confidence")),
        "stance": _availability(bool(stance)),
        "stance_score": _to_float((stance or {}).get("overall_stance_score")),
        "stance_label": _title((stance or {}).get("overall_stance_label")),
        "stance_trend": _title((stance or {}).get("trend_label")),
        "stance_breakdown": _stance_breakdown(stance),
        "yield_curve": _empty_yield_curve(),
        "yield_spread_vs_usd": _empty_yield_spread(),
        "recent_surprises": _recent_surprises_payload(surprises.get(str(country_code), [])),
        "cot": _empty_cot(),
        "retail_sentiment": _retail_payload(retail_sentiment.get(currency)),
        "cb_rss_stance": (cb_rss.get(currency) or {}).get("stance"),
        "cb_rss_summary": (cb_rss.get(currency) or {}).get("summary"),
        "cb_rss": _cb_rss_payload(cb_rss.get(currency)),
        "rate_probability": _rate_probability_payload(rate_probability),
    }
    if currency == "USD":
        profile["yield_spread_vs_usd"] = None
    return profile


def _build_differential(base: dict[str, Any], quote: dict[str, Any]) -> dict[str, Any]:
    cb_score_diff = _diff(base.get("cb_preferred_score"), quote.get("cb_preferred_score"))
    yield_spread = None
    base_curve = base.get("yield_curve") or {}
    quote_curve = quote.get("yield_curve") or {}
    if base_curve.get("data_available") and quote_curve.get("data_available"):
        yield_spread = _diff(base_curve.get("10y"), quote_curve.get("10y"))

    return {
        "cb_score_diff": cb_score_diff,
        "stance_alignment": _stance_alignment(base.get("stance_score"), quote.get("stance_score")),
        "yield_10y_spread": yield_spread,
        "cot_alignment": _cot_alignment(base.get("cot"), quote.get("cot")),
        "sentiment_signal": _sentiment_signal(base.get("retail_sentiment"), quote.get("retail_sentiment")),
        "fundamental_bias": _fundamental_bias(cb_score_diff),
    }


def _load_cached_retail_sentiment() -> dict[str, dict[str, Any]]:
    try:
        from app.services import retail_sentiment as service
    except ImportError:
        return {}
    cached = getattr(service, "_cache", {}).get("data")
    if not isinstance(cached, dict):
        return {}
    assets = cached.get("assets") or []
    pairs = {asset.get("name"): asset for asset in assets if isinstance(asset, dict)}
    result: dict[str, dict[str, Any]] = {}
    for currency in TRACKED_CURRENCIES:
        pair = "USDJPY" if currency == "JPY" else f"{currency}USD"
        asset = pairs.get(pair)
        if asset:
            result[currency] = asset
    return result


def _load_cached_cb_rss_analysis() -> dict[str, dict[str, Any]]:
    return {}


def _bank_code(currency: str, country: dict[str, Any]) -> str | None:
    central_bank = str(country.get("central_bank") or "").strip().upper()
    return CENTRAL_BANK_ALIASES.get(central_bank) or CURRENCY_TO_BANK.get(currency)


def _availability(data_available: bool) -> dict[str, bool]:
    return {"data_available": bool(data_available)}


def _empty_yield_curve() -> dict[str, Any]:
    return {"data_available": False, **dict.fromkeys(TENORS)}


def _empty_yield_spread() -> dict[str, Any]:
    return {"data_available": False, "2y_spread": None, "10y_spread": None}


def _empty_cot() -> dict[str, Any]:
    return {
        "data_available": False,
        "net_position": None,
        "direction": None,
        "weeks_trend": None,
    }


def _recent_surprises_payload(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"data_available": bool(rows), "items": rows}


def _retail_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "data_available": False,
            "pct_long": None,
            "pct_short": None,
            "signal": None,
        }
    return {
        "data_available": True,
        "pct_long": _to_float(row.get("long")),
        "pct_short": _to_float(row.get("short")),
        "signal": _retail_signal(row),
    }


def _cb_rss_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "data_available": bool(row),
        "stance": (row or {}).get("stance"),
        "summary": (row or {}).get("summary"),
    }


def _rate_probability_payload(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {
            "data_available": False,
            "next_meeting_date": None,
            "hike_pct": None,
            "hold_pct": None,
            "cut_pct": None,
            "delta_bps": None,
        }
    return {
        "data_available": True,
        "next_meeting_date": _date_iso(row.get("meeting_dt")),
        "hike_pct": _prob_pct(row.get("hike_prob")),
        "hold_pct": _prob_pct(row.get("hold_prob")),
        "cut_pct": _prob_pct(row.get("cut_prob")),
        "delta_bps": _to_float(row.get("delta_bps")),
    }


def _stance_breakdown(stance: dict[str, Any] | None) -> dict[str, Any]:
    return {
        "inflation": {
            "score": _to_float((stance or {}).get("inflation_score")),
            "direction": _direction_label((stance or {}).get("inflation_direction")),
        },
        "labor": {
            "score": _to_float((stance or {}).get("labor_score")),
            "direction": _direction_label((stance or {}).get("labor_direction")),
        },
        "growth": {
            "score": _to_float((stance or {}).get("growth_score")),
            "direction": _direction_label((stance or {}).get("growth_direction")),
        },
    }


def _surprise_direction(actual: Any, estimate: Any, higher_better: bool) -> str:
    actual_value = _to_float(actual)
    estimate_value = _to_float(estimate)
    if actual_value is None or estimate_value is None or actual_value == estimate_value:
        return "UNCERTAIN"
    if higher_better:
        return "BEAT" if actual_value > estimate_value else "MISS"
    return "BEAT" if actual_value < estimate_value else "MISS"


def _direction_label(value: Any) -> str | None:
    numeric = _to_float(value)
    if numeric is None:
        return None
    if numeric > 0.1:
        return "improving"
    if numeric < -0.1:
        return "deteriorating"
    return "stable"


def _retail_signal(row: dict[str, Any]) -> str | None:
    long_pct = _to_float(row.get("long"))
    short_pct = _to_float(row.get("short"))
    if long_pct is None or short_pct is None:
        return None
    if long_pct >= 60:
        return "CONTRARIAN_SHORT"
    if short_pct >= 60:
        return "CONTRARIAN_LONG"
    return "NEUTRAL"


def _stance_alignment(base_score: Any, quote_score: Any) -> str | None:
    base = _to_float(base_score)
    quote = _to_float(quote_score)
    if base is None or quote is None:
        return None
    if abs(base - quote) >= 0.5:
        return "DIVERGING"
    if base * quote > 0:
        return "ALIGNED"
    return "MIXED"


def _cot_alignment(base_cot: Any, quote_cot: Any) -> str | None:
    if not isinstance(base_cot, dict) or not isinstance(quote_cot, dict):
        return None
    if not base_cot.get("data_available") or not quote_cot.get("data_available"):
        return None
    base_net = _to_float(base_cot.get("net_position"))
    quote_net = _to_float(quote_cot.get("net_position"))
    if base_net is None or quote_net is None:
        return None
    if base_net < 0 and quote_net < 0:
        return "BOTH_SHORT"
    if base_net > 0 and quote_net > 0:
        return "BOTH_LONG"
    return "DIVERGING"


def _sentiment_signal(base_sentiment: Any, quote_sentiment: Any) -> str | None:
    if not isinstance(base_sentiment, dict) or not base_sentiment.get("data_available"):
        return None
    signal = base_sentiment.get("signal")
    if signal == "CONTRARIAN_LONG":
        return "CONTRARIAN_LONG_BASE"
    if signal == "CONTRARIAN_SHORT":
        return "CONTRARIAN_SHORT_BASE"
    if isinstance(quote_sentiment, dict) and quote_sentiment.get("signal") == "CONTRARIAN_SHORT":
        return "CONTRARIAN_LONG_BASE"
    return "NEUTRAL"


def _fundamental_bias(cb_score_diff: float | None) -> str:
    if cb_score_diff is None:
        return "NEUTRAL"
    if cb_score_diff > 0.3:
        return "LONG_BASE"
    if cb_score_diff < -0.3:
        return "SHORT_BASE"
    return "NEUTRAL"


def _count_unavailable(value: Any) -> int:
    if isinstance(value, dict):
        count = 1 if value.get("data_available") is False else 0
        return count + sum(_count_unavailable(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_unavailable(item) for item in value)
    return 0


def _count_availability_flags(value: Any) -> int:
    if isinstance(value, dict):
        count = 1 if "data_available" in value else 0
        return count + sum(_count_availability_flags(item) for item in value.values())
    if isinstance(value, list):
        return sum(_count_availability_flags(item) for item in value)
    return 0


def _title(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).replace("_", " ").title()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _prob_pct(value: Any) -> float | None:
    numeric = _to_float(value)
    return round(numeric * 100, 1) if numeric is not None else None


def _date_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    return str(value)[:10]


def _diff(left: Any, right: Any) -> float | None:
    left_value = _to_float(left)
    right_value = _to_float(right)
    if left_value is None or right_value is None:
        return None
    return round(left_value - right_value, 4)
