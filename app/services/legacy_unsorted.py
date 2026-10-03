"""Remaining legacy data calculations pending domain-specific replacement.

Public entry points below expose raw data for the new design.
"""
from __future__ import annotations
import json
import calendar
from datetime import date, datetime, timedelta, timezone
from typing import Any
from sqlalchemy import desc, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import (
    Country, Indicator, IndicatorRelease, IngestionRun, KnowledgeDocumentPage,
    KnowledgeDocumentSection, KnowledgeObject, KnowledgeSourceDocument,
    KnowledgeSourceFile, KnowledgeTable,
)
from app.services.country_data import get_country_detail_payload, list_country_summaries, list_biggest_surprises
from app.services.rates import _build_yield_differentials
from app.settings import get_settings
YIELD_BASE_CURRENCY = "USD"
CURRENCY_METER_ORDER = ("USD", "EUR", "JPY", "GBP", "AUD", "CAD", "CHF", "NZD")


CURRENCY_METER_LOOKBACKS = (
    ("Current", 0),
    ("1M Ago", 1),
    ("3M Ago", 3),
    ("6M Ago", 6),
)


COUNTRY_FLAGS = {
    "US": "\U0001F1FA\U0001F1F8",
    "EU": "\U0001F1EA\U0001F1FA",
    "DE": "\U0001F1E9\U0001F1EA",
    "FR": "\U0001F1EB\U0001F1F7",
    "UK": "\U0001F1EC\U0001F1E7",
    "JP": "\U0001F1EF\U0001F1F5",
    "AU": "\U0001F1E6\U0001F1FA",
    "NZ": "\U0001F1F3\U0001F1FF",
    "CA": "\U0001F1E8\U0001F1E6",
    "CH": "\U0001F1E8\U0001F1ED",
}


WORLD_MAP_GEO_NAMES: dict[str, tuple[str, ...]] = {
    "US": ("United States of America",),
    "CA": ("Canada",),
    "EU": (
        "Austria", "Belgium", "Croatia", "Cyprus", "Estonia", "Finland",
        "Greece", "Ireland", "Italy", "Latvia", "Lithuania", "Luxembourg",
        "Netherlands", "Portugal", "Slovakia", "Slovenia", "Spain",
    ),
    "DE": ("Germany",),
    "FR": ("France",),
    "UK": ("United Kingdom",),
    "CH": ("Switzerland",),
    "JP": ("Japan",),
    "AU": ("Australia",),
    "NZ": ("New Zealand",),
}


MAP_METRIC_PREFERENCES = {
    # DE and FR are deliberately absent: euro-area members do not set their own
    # policy rate, so "no own rate" is the correct answer for them, not the ECB's.
    "rate": (
        "policy_rate",
        "fed_interest_rate_decision",
        "ecb_deposit_rate",
        "bank_rate",
        "cash_rate",
        "official_cash_rate",
        "overnight_rate",
        "boj_interest_rate_decision",
    ),
    "inflation": ("cpi_headline_yoy", "cpi_headline_qoq", "cpi_headline_mom"),
    "labour": ("unemployment_rate", "u6_unemployment_rate"),
    "gdp": ("gdp_qoq", "gdp_yoy", "gdp_mom", "niesr_monthly_gdp_tracker"),
}


def _flag_for_country(country_code: str) -> str:
    return COUNTRY_FLAGS.get(country_code, "\U0001F3F3\ufe0f")


def _format_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return "N/A"
    formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{formatted} {unit}".strip() if unit else formatted


def _meter_percent(value: float | None) -> int:
    if value is None:
        return 50
    clamped = max(-2.5, min(2.5, float(value)))
    return int(round(((clamped + 2.5) / 5) * 100))


def _meter_color_class(color: str | None) -> str:
    if color == "green":
        return "is-positive"
    if color == "red":
        return "is-negative"
    return "is-neutral"


def _format_score(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.2f}"


def _score_color_class(value: float | None) -> str:
    if value is None:
        return "is-neutral"
    if value >= 0.25:
        return "is-positive"
    if value <= -0.25:
        return "is-negative"
    return "is-neutral"


def _format_bp(value: float | None, decimals: int = 0) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.{decimals}f} bp"


def _format_map_metric(value: float | None, unit: str | None) -> str:
    if value is None:
        return "N/A"
    formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    if unit and unit.strip() == "%":
        return f"{formatted}%"
    return f"{formatted} {unit}".strip() if unit else formatted


def _subtract_months(value: date, months: int) -> date:
    month_index = value.month - 1 - months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _category_meter_score(value: float | None, direction: float | None) -> float | None:
    if value is None and direction is None:
        return None
    if direction is None:
        return value
    if value is None:
        return direction
    return (float(value) * 0.30) + (float(direction) * 0.70)


def _format_int(value: int | None) -> str:
    return f"{int(value or 0):,}"


def _percent(value: int | None, total: int | None) -> int:
    if not value or not total:
        return 0
    return int(round((value / total) * 100))


async def _build_analytics_snapshot(session: AsyncSession) -> dict[str, Any]:
    now = _now()
    totals_q = await session.execute(
        select(
            func.count(IndicatorRelease.id).label("total_releases"),
            func.count(IndicatorRelease.indicator_id).label("mapped_releases"),
            func.count(IndicatorRelease.actual).label("actual_releases"),
            func.count(IndicatorRelease.estimate).label("estimated_releases"),
            func.min(IndicatorRelease.released_at).label("first_release_at"),
            func.max(IndicatorRelease.released_at)
            .filter(IndicatorRelease.released_at <= now)
            .label("latest_observed_release_at"),
        )
    )
    totals = totals_q.one()

    country_total = await session.scalar(select(func.count(Country.code)))
    indicator_total = await session.scalar(select(func.count(Indicator.id)))
    upcoming_release_total = await session.scalar(
        select(func.count(IndicatorRelease.id)).where(IndicatorRelease.released_at > now)
    )
    ingestion_run_total = await session.scalar(select(func.count(IngestionRun.id)))

    total_releases = int(totals.total_releases or 0)
    mapped_releases = int(totals.mapped_releases or 0)
    unmapped_releases = max(total_releases - mapped_releases, 0)
    actual_releases = int(totals.actual_releases or 0)
    estimated_releases = int(totals.estimated_releases or 0)

    category_q = await session.execute(
        select(
            Indicator.primary_category.label("category"),
            func.count(func.distinct(Indicator.id)).label("indicator_count"),
            func.count(IndicatorRelease.id).label("release_count"),
            func.max(IndicatorRelease.released_at)
            .filter(IndicatorRelease.released_at <= now)
            .label("latest_release_at"),
        )
        .outerjoin(IndicatorRelease, IndicatorRelease.indicator_id == Indicator.id)
        .group_by(Indicator.primary_category)
        .order_by(func.count(IndicatorRelease.id).desc(), Indicator.primary_category.asc())
    )
    category_rows = [
        {
            "category": row.category or "Uncategorized",
            "indicator_count": int(row.indicator_count or 0),
            "release_count": int(row.release_count or 0),
            "latest_release_at": row.latest_release_at,
            "share": _percent(int(row.release_count or 0), total_releases),
        }
        for row in category_q.all()
    ]

    country_q = await session.execute(
        select(
            Country.code,
            Country.name,
            Country.currency_code,
            func.count(func.distinct(Indicator.id)).label("indicator_count"),
            func.count(IndicatorRelease.id).label("release_count"),
            func.max(IndicatorRelease.released_at)
            .filter(IndicatorRelease.released_at <= now)
            .label("latest_release_at"),
        )
        .outerjoin(Indicator, Indicator.country_code == Country.code)
        .outerjoin(IndicatorRelease, IndicatorRelease.indicator_id == Indicator.id)
        .group_by(Country.code, Country.name, Country.currency_code)
        .order_by(func.count(IndicatorRelease.id).desc(), Country.code.asc())
    )
    country_rows = [
        {
            "code": row.code,
            "name": row.name,
            "currency_code": row.currency_code,
            "flag": _flag_for_country(row.code),
            "indicator_count": int(row.indicator_count or 0),
            "release_count": int(row.release_count or 0),
            "latest_release_at": row.latest_release_at,
            "share": _percent(int(row.release_count or 0), total_releases),
        }
        for row in country_q.all()
    ]

    frequency_q = await session.execute(
        select(Indicator.frequency, func.count(Indicator.id).label("indicator_count"))
        .group_by(Indicator.frequency)
        .order_by(func.count(Indicator.id).desc(), Indicator.frequency.asc())
    )
    frequency_rows = [
        {
            "label": (row.frequency or "unknown").title(),
            "indicator_count": int(row.indicator_count or 0),
            "share": _percent(int(row.indicator_count or 0), int(indicator_total or 0)),
        }
        for row in frequency_q.all()
    ]

    importance_labels = {1: "High", 2: "Medium", 3: "Low"}
    importance_q = await session.execute(
        select(Indicator.importance, func.count(Indicator.id).label("indicator_count"))
        .group_by(Indicator.importance)
        .order_by(Indicator.importance.asc())
    )
    importance_rows = [
        {
            "label": importance_labels.get(row.importance, f"Level {row.importance}"),
            "indicator_count": int(row.indicator_count or 0),
            "share": _percent(int(row.indicator_count or 0), int(indicator_total or 0)),
        }
        for row in importance_q.all()
    ]

    recent_runs_q = await session.execute(
        select(IngestionRun)
        .order_by(desc(IngestionRun.started_at), desc(IngestionRun.id))
        .limit(5)
    )
    recent_runs = [
        {
            "id": run.id,
            "run_type": run.run_type,
            "status": run.status,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "events_inserted": int(run.events_inserted or 0),
            "events_updated": int(run.events_updated or 0),
            "api_calls_used": int(run.api_calls_used or 0),
        }
        for run in recent_runs_q.scalars().all()
    ]

    maintenance_indicators_q = await session.execute(
        select(
            Indicator.country_code,
            Country.currency_code,
            Indicator.canonical_name,
            Indicator.display_name,
        )
        .join(Country, Country.code == Indicator.country_code)
        .order_by(
            Country.currency_code.asc(),
            Indicator.display_name.asc(),
            Indicator.canonical_name.asc(),
        )
    )
    maintenance_indicators = [
        {
            "country_code": row.country_code,
            "currency_code": row.currency_code,
            "canonical_name": row.canonical_name,
            "display_name": row.display_name,
            "label": (
                f"{row.currency_code} · {row.display_name} "
                f"({row.canonical_name})"
            ),
        }
        for row in maintenance_indicators_q.all()
    ]

    headline_stats = [
        {"label": "Countries", "value": _format_int(country_total), "detail": "tracked markets"},
        {"label": "Indicators", "value": _format_int(indicator_total), "detail": "canonical series"},
        {"label": "Releases", "value": _format_int(total_releases), "detail": "stored prints"},
        {"label": "Upcoming", "value": _format_int(upcoming_release_total), "detail": "scheduled rows"},
    ]

    data_quality = [
        {
            "label": "Mapped Releases",
            "value": _format_int(mapped_releases),
            "share": _percent(mapped_releases, total_releases),
        },
        {
            "label": "Unmapped Releases",
            "value": _format_int(unmapped_releases),
            "share": _percent(unmapped_releases, total_releases),
        },
        {
            "label": "Actual Values",
            "value": _format_int(actual_releases),
            "share": _percent(actual_releases, total_releases),
        },
        {
            "label": "Estimate Values",
            "value": _format_int(estimated_releases),
            "share": _percent(estimated_releases, total_releases),
        },
    ]

    return {
        "headline_stats": headline_stats,
        "data_quality": data_quality,
        "category_rows": category_rows,
        "country_rows": country_rows,
        "frequency_rows": frequency_rows,
        "importance_rows": importance_rows,
        "recent_runs": recent_runs,
        "maintenance_indicators": maintenance_indicators,
        "total_releases": total_releases,
        "ingestion_run_total": int(ingestion_run_total or 0),
        "first_release_at": totals.first_release_at,
        "latest_observed_release_at": totals.latest_observed_release_at,
    }


async def _build_fundamental_currency_meter(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    currency_list = ", ".join(f"'{currency}'" for currency in CURRENCY_METER_ORDER)
    use_legacy_stance = False
    try:
        result = await session.execute(
            text(
                f"""
                WITH latest AS (
                    SELECT date
                    FROM processed.cb_preferred_score
                    WHERE currency IN ({currency_list})
                    GROUP BY date
                    HAVING count(DISTINCT currency) = {len(CURRENCY_METER_ORDER)}
                    ORDER BY date DESC
                    LIMIT 1
                )
                SELECT
                    date,
                    country_code,
                    currency,
                    inflation_score,
                    labor_score,
                    growth_score,
                    cb_strength_score,
                    strength_label
                FROM processed.cb_preferred_score
                WHERE currency IN ({currency_list})
                    AND date <= (SELECT date FROM latest)
                ORDER BY currency, date
                """
            )
        )
    except Exception:
        await session.rollback()
        use_legacy_stance = True
        try:
            result = await session.execute(
                text(
                    f"""
                    WITH latest AS (
                        SELECT date
                        FROM processed.currency_stance
                        WHERE window_months = 3
                            AND currency IN ({currency_list})
                        GROUP BY date
                        HAVING count(DISTINCT currency) = {len(CURRENCY_METER_ORDER)}
                        ORDER BY date DESC
                        LIMIT 1
                    )
                    SELECT
                        date,
                        country_code,
                        currency,
                        inflation_score,
                        labor_score,
                        growth_score,
                        inflation_direction,
                        labor_direction,
                        growth_direction,
                        overall_stance_score,
                        overall_stance_label
                    FROM processed.currency_stance
                    WHERE window_months = 3
                        AND currency IN ({currency_list})
                        AND date <= (SELECT date FROM latest)
                    ORDER BY currency, date
                    """
                )
            )
        except Exception:
            return []

    raw_rows = [dict(row) for row in result.mappings().all()]
    if not raw_rows and not use_legacy_stance:
        use_legacy_stance = True
        try:
            result = await session.execute(
                text(
                    f"""
                    WITH latest AS (
                        SELECT date
                        FROM processed.currency_stance
                        WHERE window_months = 3
                            AND currency IN ({currency_list})
                        GROUP BY date
                        HAVING count(DISTINCT currency) = {len(CURRENCY_METER_ORDER)}
                        ORDER BY date DESC
                        LIMIT 1
                    )
                    SELECT
                        date,
                        country_code,
                        currency,
                        inflation_score,
                        labor_score,
                        growth_score,
                        inflation_direction,
                        labor_direction,
                        growth_direction,
                        overall_stance_score,
                        overall_stance_label
                    FROM processed.currency_stance
                    WHERE window_months = 3
                        AND currency IN ({currency_list})
                        AND date <= (SELECT date FROM latest)
                    ORDER BY currency, date
                    """
                )
            )
            raw_rows = [dict(row) for row in result.mappings().all()]
        except Exception:
            await session.rollback()
            return []

    rows_by_currency: dict[str, list[dict[str, Any]]] = {}
    for row in raw_rows:
        rows_by_currency.setdefault(str(row["currency"]), []).append(dict(row))

    metrics = (
        ("Inflation", "inflation_score", "inflation_direction" if use_legacy_stance else None),
        ("Growth", "growth_score", "growth_direction" if use_legacy_stance else None),
        ("Labor", "labor_score", "labor_direction" if use_legacy_stance else None),
        ("Overall", "overall_stance_score" if use_legacy_stance else "cb_strength_score", None),
    )

    meter_rows: list[dict[str, Any]] = []
    for currency in CURRENCY_METER_ORDER:
        history = rows_by_currency.get(currency, [])
        if not history:
            continue

        latest_row = history[-1]
        latest_date = latest_row["date"]
        if not isinstance(latest_date, date):
            continue

        period_rows: list[dict[str, Any]] = []
        for label, months in CURRENCY_METER_LOOKBACKS:
            target_date = _subtract_months(latest_date, months)
            selected = next(
                (
                    row
                    for row in reversed(history)
                    if isinstance(row["date"], date) and row["date"] <= target_date
                ),
                None,
            )
            period_rows.append({
                "label": label,
                "date": selected["date"] if selected else None,
                "row": selected,
            })

        metric_rows = []
        for label, key, direction_key in metrics:
            score_values = []
            for period in period_rows:
                score_value = _category_meter_score(
                    period["row"][key] if period["row"] is not None else None,
                    (
                        period["row"][direction_key]
                        if period["row"] is not None and direction_key is not None
                        else None
                    ),
                )
                score_values.append({
                    "period": period["label"],
                    "date": period["date"],
                    "score": _format_score(score_value),
                    "raw_score": score_value,
                    "bar_percent": _meter_percent(score_value),
                    "color_class": _score_color_class(score_value),
                })
            metric_rows.append({
                "label": label,
                "slug": label.lower(),
                "score_values": score_values,
            })

        overall_score = latest_row["overall_stance_score" if use_legacy_stance else "cb_strength_score"]
        meter_rows.append({
            "currency": currency,
            "country_code": latest_row["country_code"],
            "latest_date": latest_date,
            "label": str(latest_row["overall_stance_label" if use_legacy_stance else "strength_label"]).replace("_", " ").title(),
            "score": _format_score(overall_score),
            "raw_score": overall_score,
            "score_percent": _meter_percent(overall_score),
            "color_class": _score_color_class(overall_score),
            "periods": period_rows,
            "metrics": metric_rows,
        })

    return meter_rows


async def _build_world_map_snapshots(
    session: AsyncSession,
    countries: list[Any],
    meter_rows: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Build latest macro snapshots for the landing-page world choropleth.

    ``meter_rows`` is the output of :func:`_build_fundamental_currency_meter`; when
    supplied, each snapshot picks up the composite strength score used to shade the
    country on the map.
    """
    meter_by_currency = {
        str(row["currency"]): row for row in (meter_rows or [])
    }
    snapshots: list[dict[str, Any]] = []
    for country in countries:
        geo_names = WORLD_MAP_GEO_NAMES.get(country.code)
        if geo_names is None:
            continue

        detail = await get_country_detail_payload(session, country.code)
        indicators = detail.indicators if detail else []
        by_name = {indicator.canonical_name: indicator for indicator in indicators}

        metric_rows: list[dict[str, str]] = []
        for key, label in (
            ("rate", "Interest rate"),
            ("inflation", "Inflation"),
            ("labour", "Labour"),
            ("gdp", "GDP"),
        ):
            indicator = next(
                (
                    by_name[canonical_name]
                    for canonical_name in MAP_METRIC_PREFERENCES[key]
                    if canonical_name in by_name
                ),
                None,
            )
            release = indicator.latest_release if indicator else None
            raw_value = release.actual if release else None
            metric_rows.append({
                "key": key,
                "label": label,
                "raw": float(raw_value) if raw_value is not None else None,
                "value": _format_map_metric(
                    raw_value,
                    indicator.unit if indicator else None,
                ),
                "unit": indicator.unit if indicator else None,
                "detail": (
                    indicator.display_name
                    if indicator and release else "No latest print"
                ),
            })

        latest_release_at = (
            country.latest_release_at.strftime("%b %d, %Y")
            if country.latest_release_at else "pending"
        )
        tooltip_lines = [
            f"{country.name} ({country.currency_code})",
            *[f"{row['label']}: {row['value']}" for row in metric_rows],
            f"Updated: {latest_release_at}",
        ]
        meter = meter_by_currency.get(str(country.currency_code)) or {}
        snapshots.append({
            "code": country.code,
            "name": country.name,
            "currency_code": country.currency_code,
            "flag": _flag_for_country(country.code),
            "href": f"/country/{country.code.lower()}",
            "geo_names": list(geo_names),
            "score": meter.get("score"),
            "raw_score": meter.get("raw_score"),
            "score_percent": meter.get("score_percent"),
            "stance_label": meter.get("label"),
            "latest_release_at": latest_release_at,
            "metrics": metric_rows,
            "tooltip": "\n".join(tooltip_lines),
        })
    return snapshots


CB_BANK_NAMES = {
    "USD": "Fed", "EUR": "ECB", "GBP": "BoE", "JPY": "BoJ",
    "AUD": "RBA", "NZD": "RBNZ", "CAD": "BoC", "CHF": "SNB",
}


CB_LOCATIONS = {
    "USD": ("Fed", "Washington", -77.04, 38.89),
    "EUR": ("ECB", "Frankfurt", 8.68, 50.11),
    "GBP": ("BoE", "London", -0.09, 51.51),
    "JPY": ("BoJ", "Tokyo", 139.77, 35.69),
    "AUD": ("RBA", "Sydney", 151.21, -33.87),
    "NZD": ("RBNZ", "Wellington", 174.78, -41.29),
    "CAD": ("BoC", "Ottawa", -75.70, 45.42),
    "CHF": ("SNB", "Zurich", 8.54, 47.37),
}


def _json_number(value: Any, default: float | None = None) -> float | None:
    """Coerce a DB Decimal (or anything numeric) to a JSON-serialisable float."""
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _build_landing_kpis(
    meter_rows: list[dict[str, Any]],
    yield_differentials: dict[str, Any],
    surprises: list[Any],
    news_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Assemble the five headline tiles above the landing choropleth.

    Pure assembly over data already fetched by :func:`landing_page` — no queries.
    """
    scored = [row for row in meter_rows if row.get("raw_score") is not None]
    scored.sort(key=lambda row: float(row["raw_score"]), reverse=True)
    strongest = scored[0] if scored else None
    weakest = scored[-1] if len(scored) > 1 else None

    yield_rows = [
        row for row in (yield_differentials or {}).get("rows", [])
        if row.get("currency") != YIELD_BASE_CURRENCY
        and row.get("spread_vs_base_bp") is not None
    ]
    widest = max(
        yield_rows,
        key=lambda row: abs(float(row["spread_vs_base_bp"])),
        default=None,
    )

    def _surprise_value(item: Any) -> float:
        value = getattr(item, "surprise", None)
        return abs(float(value)) if value is not None else 0.0

    biggest = max(surprises, key=_surprise_value, default=None)
    biggest_value = getattr(biggest, "surprise", None) if biggest else None

    return [
        {
            "label": "Strongest G10",
            "value": strongest["currency"] if strongest else "N/A",
            "detail": strongest["label"] if strongest else "meter not built",
            "tone": "bull" if strongest else "neutral",
        },
        {
            "label": "Weakest G10",
            "value": weakest["currency"] if weakest else "N/A",
            "detail": weakest["label"] if weakest else "meter not built",
            "tone": "bear" if weakest else "neutral",
        },
        {
            "label": f"Widest 10Y vs {YIELD_BASE_CURRENCY}",
            "value": widest["spread_display"] if widest else "N/A",
            "detail": widest["currency"] if widest else "yields unavailable",
            # _spread_color_class returns is-positive / is-negative / is-neutral.
            "tone": {
                "is-positive": "bull",
                "is-negative": "bear",
            }.get(widest.get("spread_class"), "neutral") if widest else "neutral",
        },
        {
            "label": "Biggest Surprise",
            "value": (
                f"{float(biggest_value):+.2f}"
                if biggest_value is not None else "N/A"
            ),
            "detail": (
                getattr(biggest, "display_name", None) or "no recent prints"
            ) if biggest else "no recent prints",
            "tone": (
                "bull" if biggest_value is not None and float(biggest_value) >= 0
                else "bear" if biggest_value is not None else "neutral"
            ),
        },
        {
            "label": "Headlines · 24h",
            "value": str(len(news_items)),
            "detail": "InvestingLive tape" if news_items else "tape idle",
            "tone": "neutral",
        },
    ]


def _latest_metric_score(row: dict[str, Any], label: str) -> float | None:
    for metric in row.get("metrics") or []:
        if str(metric.get("label")) != label:
            continue
        values = metric.get("score_values") or []
        if not values:
            return None
        return _json_number(values[0].get("raw_score"))
    return None


def _metric_change(row: dict[str, Any], label: str) -> float | None:
    for metric in row.get("metrics") or []:
        if str(metric.get("label")) != label:
            continue
        values = metric.get("score_values") or []
        if len(values) < 2:
            return None
        current = _json_number(values[0].get("raw_score"))
        prior = _json_number(values[1].get("raw_score"))
        if current is None or prior is None:
            return None
        return current - prior
    return None


def _bias_label(score: float | None) -> str:
    if score is None:
        return "Insufficient data"
    if score >= 0.35:
        return "Bullish"
    if score <= -0.35:
        return "Bearish"
    return "Neutral"


def _bias_class(label: str) -> str:
    normalized = label.lower()
    if "bull" in normalized:
        return "bull"
    if "bear" in normalized:
        return "bear"
    return "neutral"


def _change_label(delta: float | None) -> str:
    if delta is None:
        return "No history"
    if delta >= 0.25:
        return "Improving"
    if delta <= -0.25:
        return "Deteriorating"
    return "Stable"


def _driver_tone(score: float | None) -> str:
    if score is None:
        return "No data"
    if score >= 0.25:
        return "Bullish"
    if score <= -0.25:
        return "Bearish"
    return "Neutral"


def _confidence_label(score: float | None, driver_scores: list[float | None]) -> str:
    coverage = sum(value is not None for value in driver_scores)
    if score is None or coverage < 2:
        return "Low"
    if abs(score) >= 0.75 and coverage >= 3:
        return "High"
    if abs(score) >= 0.35 or coverage >= 3:
        return "Medium"
    return "Low"


def _build_actionable_dashboard_insights(
    currency_meter: list[dict[str, Any]],
    yield_differentials: dict[str, Any],
    surprises: list[Any],
) -> dict[str, Any]:
    """Build transparent dashboard sections from real macro inputs.

    This intentionally avoids opaque composite claims. Each row exposes the
    current stance, the change versus one month ago, and the strongest visible
    driver among the data-backed inflation/growth/labor/rates layers.
    """
    yield_by_currency = {
        str(row.get("currency")): row
        for row in (yield_differentials or {}).get("rows", [])
    }

    bias_rows: list[dict[str, Any]] = []
    driver_matrix: list[dict[str, Any]] = []
    change_rows: list[dict[str, Any]] = []
    alert_rows: list[dict[str, Any]] = []

    for row in currency_meter:
        currency = str(row.get("currency") or "")
        country_code = str(row.get("country_code") or "")
        score = _json_number(row.get("raw_score"))
        one_month_delta = _metric_change(row, "Overall")
        inflation = _latest_metric_score(row, "Inflation")
        growth = _latest_metric_score(row, "Growth")
        labor = _latest_metric_score(row, "Labor")

        yield_row = yield_by_currency.get(currency, {})
        if currency == YIELD_BASE_CURRENCY:
            rates_score = _json_number(yield_row.get("change_5d_bp"))
            rates_tone = (
                "Bullish" if rates_score is not None and rates_score >= 5
                else "Bearish" if rates_score is not None and rates_score <= -5
                else "Neutral" if rates_score is not None
                else "No data"
            )
        else:
            rates_score = _json_number(yield_row.get("spread_vs_base_bp"))
            rates_tone = (
                "Bullish" if rates_score is not None and rates_score >= 25
                else "Bearish" if rates_score is not None and rates_score <= -25
                else "Neutral" if rates_score is not None
                else "No data"
            )

        drivers = [
            ("Rates", rates_tone, rates_score),
            ("Inflation", _driver_tone(inflation), inflation),
            ("Growth", _driver_tone(growth), growth),
            ("Labor", _driver_tone(labor), labor),
        ]
        scored_drivers = [item for item in drivers if item[2] is not None]
        main_driver = max(
            scored_drivers,
            key=lambda item: abs(float(item[2])),
            default=("No dominant driver", "No data", None),
        )
        bias = _bias_label(score)
        change = _change_label(one_month_delta)
        confidence = _confidence_label(score, [inflation, growth, labor])

        bias_rows.append({
            "currency": currency,
            "country_code": country_code,
            "href": f"/country/{country_code.lower()}" if country_code else "/countries",
            "bias": bias,
            "bias_class": _bias_class(bias),
            "change": change,
            "change_class": _bias_class("Bullish" if one_month_delta and one_month_delta > 0 else "Bearish" if one_month_delta and one_month_delta < 0 else "Neutral"),
            "score": _format_score(score),
            "delta": _format_score(one_month_delta),
            "main_driver": main_driver[0],
            "main_driver_tone": main_driver[1],
            "confidence": confidence,
            "updated": (
                row["latest_date"].strftime("%b %d")
                if row.get("latest_date") else "pending"
            ),
        })

        driver_matrix.append({
            "currency": currency,
            "href": f"/country/{country_code.lower()}" if country_code else "/countries",
            "drivers": [
                {
                    "label": label,
                    "tone": tone,
                    "class": _bias_class(tone),
                    "value": (
                        _format_bp(value) if label == "Rates" and value is not None
                        else _format_score(value)
                    ),
                }
                for label, tone, value in drivers
            ],
        })

        if one_month_delta is not None:
            change_rows.append({
                "currency": currency,
                "href": f"/country/{country_code.lower()}" if country_code else "/countries",
                "change": change,
                "delta": _format_score(one_month_delta),
                "driver": main_driver[0],
                "tone": bias,
                "class": _bias_class("Bullish" if one_month_delta > 0 else "Bearish" if one_month_delta < 0 else "Neutral"),
            })

        if bias == "Neutral" and main_driver[0] != "No dominant driver" and main_driver[1] != "Neutral":
            alert_rows.append({
                "currency": currency,
                "message": f"Neutral headline, but {main_driver[0].lower()} is {main_driver[1].lower()}.",
                "class": _bias_class(main_driver[1]),
                "href": f"/country/{country_code.lower()}" if country_code else "/countries",
            })
        elif confidence == "Low":
            alert_rows.append({
                "currency": currency,
                "message": "Low confidence: check country page before using the signal.",
                "class": "neutral",
                "href": f"/country/{country_code.lower()}" if country_code else "/countries",
            })

    change_rows.sort(
        key=lambda item: abs(float(item["delta"])) if item["delta"] not in {"N/A", ""} else 0,
        reverse=True,
    )

    surprise_rows = []
    for item in surprises[:6]:
        value = _json_number(getattr(item, "surprise", None))
        surprise_rows.append({
            "currency": getattr(item, "currency_code", ""),
            "country": getattr(item, "country_code", ""),
            "name": getattr(item, "display_name", "Macro release"),
            "value": f"{value:+.2f}" if value is not None else "N/A",
            "class": "bull" if value is not None and value >= 0 else "bear" if value is not None else "neutral",
            "date": (
                item.released_at.strftime("%b %d")
                if getattr(item, "released_at", None) else "recent"
            ),
        })

    return {
        "bias_rows": bias_rows,
        "driver_matrix": driver_matrix,
        "change_rows": change_rows[:6],
        "surprise_rows": surprise_rows,
        "alert_rows": alert_rows[:5],
    }


def _now():
    from datetime import datetime

    return datetime.now(timezone.utc)




async def _build_knowledge_bank_context(
    session: AsyncSession,
    query: str = "",
    status: str = "",
) -> dict[str, Any]:
    total_docs = (await session.execute(select(func.count(KnowledgeSourceDocument.id)))).scalar_one() or 0
    total_files = (await session.execute(select(func.count(KnowledgeSourceFile.id)))).scalar_one() or 0
    duplicate_files = (
        await session.execute(
            select(func.count(KnowledgeSourceFile.id)).where(
                KnowledgeSourceFile.is_duplicate.is_(True)
            )
        )
    ).scalar_one() or 0
    processed = (
        await session.execute(
            select(func.count(KnowledgeSourceDocument.id)).where(
                KnowledgeSourceDocument.extraction_status == "processed"
            )
        )
    ).scalar_one() or 0
    needs_review = (
        await session.execute(
            select(func.count(KnowledgeSourceDocument.id)).where(
                KnowledgeSourceDocument.extraction_status == "needs_review"
            )
        )
    ).scalar_one() or 0
    failed = (
        await session.execute(
            select(func.count(KnowledgeSourceDocument.id)).where(
                KnowledgeSourceDocument.extraction_status == "failed"
            )
        )
    ).scalar_one() or 0

    analyst_rows = (
        await session.execute(
            select(
                KnowledgeSourceDocument.author,
                KnowledgeSourceDocument.publisher,
                func.count(KnowledgeSourceDocument.id),
            )
            .group_by(KnowledgeSourceDocument.author, KnowledgeSourceDocument.publisher)
            .order_by(func.count(KnowledgeSourceDocument.id).desc())
        )
    ).all()
    year_rows = (
        await session.execute(
            select(
                func.extract("year", KnowledgeSourceDocument.publication_date).label("year"),
                func.count(KnowledgeSourceDocument.id),
            )
            .where(KnowledgeSourceDocument.publication_date.is_not(None))
            .group_by("year")
            .order_by("year")
        )
    ).all()
    object_rows = (
        await session.execute(
            select(KnowledgeObject.knowledge_type, func.count(KnowledgeObject.id))
            .group_by(KnowledgeObject.knowledge_type)
            .order_by(func.count(KnowledgeObject.id).desc())
        )
    ).all()

    document_query = select(KnowledgeSourceDocument).order_by(
        KnowledgeSourceDocument.publication_date.desc().nullslast(),
        KnowledgeSourceDocument.original_filename.asc(),
    )
    if query:
        pattern = f"%{query}%"
        document_query = document_query.where(
            or_(
                KnowledgeSourceDocument.original_filename.ilike(pattern),
                KnowledgeSourceDocument.title.ilike(pattern),
                KnowledgeSourceDocument.author.ilike(pattern),
                KnowledgeSourceDocument.publisher.ilike(pattern),
            )
        )
    if status:
        document_query = document_query.where(KnowledgeSourceDocument.extraction_status == status)
    documents = list((await session.execute(document_query)).scalars().all())

    doc_ids = [doc.id for doc in documents]
    page_counts: dict[int, int] = {}
    section_counts: dict[int, int] = {}
    object_counts: dict[int, int] = {}
    trade_counts: dict[int, int] = {}
    framework_counts: dict[int, int] = {}
    file_counts: dict[int, int] = {}
    if doc_ids:
        page_counts = {
            row.document_id: row.count
            for row in (
                await session.execute(
                    select(KnowledgeDocumentPage.document_id, func.count(KnowledgeDocumentPage.id).label("count"))
                    .where(KnowledgeDocumentPage.document_id.in_(doc_ids))
                    .group_by(KnowledgeDocumentPage.document_id)
                )
            ).all()
        }
        section_counts = {
            row.document_id: row.count
            for row in (
                await session.execute(
                    select(KnowledgeDocumentSection.document_id, func.count(KnowledgeDocumentSection.id).label("count"))
                    .where(KnowledgeDocumentSection.document_id.in_(doc_ids))
                    .group_by(KnowledgeDocumentSection.document_id)
                )
            ).all()
        }
        object_counts = {
            row.document_id: row.count
            for row in (
                await session.execute(
                    select(KnowledgeObject.document_id, func.count(KnowledgeObject.id).label("count"))
                    .where(KnowledgeObject.document_id.in_(doc_ids))
                    .group_by(KnowledgeObject.document_id)
                )
            ).all()
        }
        trade_counts = {
            row.document_id: row.count
            for row in (
                await session.execute(
                    select(KnowledgeObject.document_id, func.count(KnowledgeObject.id).label("count"))
                    .where(
                        KnowledgeObject.document_id.in_(doc_ids),
                        KnowledgeObject.knowledge_type == "explicit_trade_idea",
                    )
                    .group_by(KnowledgeObject.document_id)
                )
            ).all()
        }
        framework_counts = {
            row.document_id: row.count
            for row in (
                await session.execute(
                    select(KnowledgeObject.document_id, func.count(KnowledgeObject.id).label("count"))
                    .where(
                        KnowledgeObject.document_id.in_(doc_ids),
                        KnowledgeObject.knowledge_type.in_(
                            ["timeless_principle", "conditional_heuristic"]
                        ),
                    )
                    .group_by(KnowledgeObject.document_id)
                )
            ).all()
        }
        file_counts = {
            row.document_id: row.count
            for row in (
                await session.execute(
                    select(KnowledgeSourceFile.document_id, func.count(KnowledgeSourceFile.id).label("count"))
                    .where(KnowledgeSourceFile.document_id.in_(doc_ids))
                    .group_by(KnowledgeSourceFile.document_id)
                )
            ).all()
        }

    inventory = [
        {
            "document": doc,
            "stored_pages": page_counts.get(doc.id, 0),
            "sections": section_counts.get(doc.id, 0),
            "objects": object_counts.get(doc.id, 0),
            "frameworks": framework_counts.get(doc.id, 0),
            "trades": trade_counts.get(doc.id, 0),
            "source_files": file_counts.get(doc.id, 0),
        }
        for doc in documents
    ]
    return {
        "overview": {
            "total_documents": total_docs,
            "total_files": total_files,
            "duplicate_files": duplicate_files,
            "processed": processed,
            "needs_review": needs_review,
            "failed": failed,
            "analysts": analyst_rows,
            "years": year_rows,
            "object_types": object_rows,
        },
        "inventory": inventory,
        "query": query,
        "status": status,
    }


_PAGE_KEYS = {
    "flag", "href", "detail_href", "pdf_url", "source_url", "display_label",
    "display_name", "date_display", "score_display", "trend_symbol",
    "name", "label", "detail", "title", "class", "tone_class",
}


def _raw_data(value: Any) -> Any:
    """Discard presentation fields from legacy selection outputs."""
    if isinstance(value, list):
        return [_raw_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_raw_data(item) for item in value)
    if isinstance(value, dict):
        return {
            key: _raw_data(item)
            for key, item in value.items()
            if key not in _PAGE_KEYS
            and not key.endswith(("_class", "_href", "_display", "_label"))
            and not (key == "value" and isinstance(item, str))
        }
    return value


async def get_analytics_data(session: AsyncSession) -> dict[str, Any]:
    return _raw_data(await _build_analytics_snapshot(session))


async def get_currency_meter_data(session: AsyncSession) -> list[dict[str, Any]]:
    return _raw_data(await _build_fundamental_currency_meter(session))


async def get_world_map_data(session: AsyncSession, countries: list[Any]) -> list[dict[str, Any]]:
    return _raw_data(await _build_world_map_snapshots(session, countries))


def get_overview_kpis(meter_rows: list[dict[str, Any]], yields: dict[str, Any],
                      surprises: list[Any], news: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _raw_data(_build_landing_kpis(meter_rows, yields, surprises, news))


def get_actionable_insights(meter_rows: list[dict[str, Any]], yields: dict[str, Any],
                            surprises: list[Any]) -> dict[str, Any]:
    return _raw_data(_build_actionable_dashboard_insights(meter_rows, yields, surprises))


async def get_knowledge_data(session: AsyncSession, query: str = "", status: str = "") -> dict[str, Any]:
    return _raw_data(await _build_knowledge_bank_context(session, query, status))


