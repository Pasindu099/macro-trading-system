"""Server-rendered page routes for the Step 5 frontend."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.public import (
    get_country,
    get_country_detail_payload,
    get_indicator_by_country_and_name,
    list_biggest_surprises,
    list_country_summaries,
)
from app.db.models import Indicator, IndicatorRelease
from app.db.session import get_session
from app.ingestion.eodhd_client import EODHDClient, EODHDError

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))

ALL_CATEGORY_TABS = (
    "Inflation",
    "Growth",
    "Labor",
    "Monetary Policy",
    "Trade",
    "Sentiment",
    "Housing",
    "Other",
)
TAB_SLUGS = {category.lower(): category for category in ALL_CATEGORY_TABS}
CALENDAR_WINDOWS = (1, 3, 7, 14)
COUNTRY_FLAGS = {
    "US": "🇺🇸",
    "EU": "🇪🇺",
    "UK": "🇬🇧",
    "JP": "🇯🇵",
    "AU": "🇦🇺",
    "NZ": "🇳🇿",
    "CA": "🇨🇦",
    "CH": "🇨🇭",
}


def _flag_for_country(country_code: str) -> str:
    return COUNTRY_FLAGS.get(country_code, "🏳️")


def _format_value(value: float | None, unit: str | None) -> str:
    if value is None:
        return "N/A"
    formatted = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{formatted} {unit}".strip() if unit else formatted


def _trend_symbol(values: list[float | None]) -> str:
    comparable = [value for value in values if value is not None]
    if len(comparable) < 2:
        return "→"
    latest = comparable[-1]
    previous = comparable[-2]
    if latest > previous:
        return "▲"
    if latest < previous:
        return "▼"
    return "→"


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


async def _build_currency_stance_dashboard(
    session: AsyncSession,
) -> dict[int, list[dict[str, Any]]]:
    try:
        result = await session.execute(
            text(
                """
                WITH latest AS (
                    SELECT window_months, max(date) AS date
                    FROM processed.currency_stance
                    GROUP BY window_months
                )
                SELECT
                    s.date,
                    s.country_code,
                    s.currency,
                    s.window_months,
                    s.inflation_score,
                    s.labor_score,
                    s.growth_score,
                    s.overall_stance_score,
                    s.overall_stance_label,
                    s.trend_label,
                    s.confidence,
                    s.meter_color,
                    s.inflation_meter_color,
                    s.labor_meter_color,
                    s.growth_meter_color,
                    r.rank_strongest
                FROM processed.currency_stance s
                JOIN processed.currency_stance_rankings r
                    ON r.date = s.date
                    AND r.currency = s.currency
                    AND r.window_months = s.window_months
                JOIN latest l
                    ON l.date = s.date
                    AND l.window_months = s.window_months
                ORDER BY s.window_months, r.rank_strongest
                """
            )
        )
    except Exception:
        return {}

    rows_by_window: dict[int, list[dict[str, Any]]] = {}
    for row in result.mappings().all():
        category_meters = [
            {
                "label": "Inflation",
                "short_label": "I",
                "score": _format_score(row["inflation_score"]),
                "percent": _meter_percent(row["inflation_score"]),
                "color_class": _meter_color_class(row["inflation_meter_color"]),
            },
            {
                "label": "Labor",
                "short_label": "L",
                "score": _format_score(row["labor_score"]),
                "percent": _meter_percent(row["labor_score"]),
                "color_class": _meter_color_class(row["labor_meter_color"]),
            },
            {
                "label": "Growth",
                "short_label": "G",
                "score": _format_score(row["growth_score"]),
                "percent": _meter_percent(row["growth_score"]),
                "color_class": _meter_color_class(row["growth_meter_color"]),
            },
        ]
        window = int(row["window_months"])
        rows_by_window.setdefault(window, []).append(
            {
                "date": row["date"],
                "country_code": row["country_code"],
                "currency": row["currency"],
                "rank": row["rank_strongest"],
                "score": _format_score(row["overall_stance_score"]),
                "score_percent": _meter_percent(row["overall_stance_score"]),
                "label": str(row["overall_stance_label"]).replace("_", " ").title(),
                "trend": str(row["trend_label"]).title(),
                "confidence": str(row["confidence"]).title(),
                "color_class": _meter_color_class(row["meter_color"]),
                "category_meters": category_meters,
            }
        )
    return rows_by_window


async def _build_country_rows(
    session: AsyncSession,
    country_code: str,
    category: str,
) -> list[dict[str, Any]]:
    now = _now()
    rows_q = await session.execute(
        select(Indicator)
        .where(
            Indicator.country_code == country_code.upper(),
            or_(
                Indicator.primary_category == category,
                Indicator.secondary_categories.any(category),
            ),
        )
        .order_by(Indicator.importance.asc(), Indicator.display_name.asc())
    )

    rows: list[dict[str, Any]] = []
    for indicator in rows_q.scalars().all():
        history_q = await session.execute(
            select(IndicatorRelease)
            .where(
                IndicatorRelease.indicator_id == indicator.id,
            )
            .order_by(
                IndicatorRelease.period_start_date.desc().nullslast(),
                desc(IndicatorRelease.released_at),
                desc(IndicatorRelease.retrieved_at),
                desc(IndicatorRelease.id),
            )
            .limit(50)
        )
        history_rows_by_period: dict[tuple[Any, ...], IndicatorRelease] = {}
        for row in history_q.scalars().all():
            key = (
                getattr(row, "period", None),
                getattr(row, "period_start_date", None),
                getattr(row, "released_at", None),
            )
            history_rows_by_period.setdefault(key, row)

        history_rows = list(reversed(list(history_rows_by_period.values())[:12]))
        released_history_rows = [
            row for row in history_rows
            if row.actual is not None
            and getattr(row, "released_at", now) <= now
        ]
        sparkline_values = [float(row.actual) for row in released_history_rows]
        latest_release = released_history_rows[-1] if released_history_rows else None

        latest_value = (
            float(latest_release.actual)
            if latest_release is not None and latest_release.actual is not None
            else None
        )
        rows.append({
            "id": indicator.id,
            "canonical_name": indicator.canonical_name,
            "display_name": indicator.display_name,
            "display_label": (
                f"{indicator.display_name}*"
                if indicator.secondary_categories else indicator.display_name
            ),
            "latest_value": _format_value(latest_value, indicator.unit),
            "trend_symbol": _trend_symbol(sparkline_values),
            "sparkline_values": sparkline_values,
            "detail_href": (
                f"/country/{indicator.country_code.lower()}/indicator/"
                f"{indicator.canonical_name}"
            ),
            "is_multi_category": bool(indicator.secondary_categories),
        })
    return rows


async def _render_country_template(
    request: Request,
    session: AsyncSession,
    country_code: str,
    active_category: str,
) -> HTMLResponse:
    country_payload = await get_country_detail_payload(session, country_code)
    if country_payload is None:
        raise HTTPException(status_code=404, detail="Country not found")

    country = country_payload.country
    rows = await _build_country_rows(session, country.code, active_category)
    category_tabs = _category_tabs_for_country(country_payload.indicators)
    return templates.TemplateResponse(
        request,
        "country.html",
        {
            "request": request,
            "page_title": f"{country.name} | Macro Dashboard",
            "country": country,
            "country_flag": _flag_for_country(country.code),
            "active_category": active_category,
            "category_tabs": category_tabs,
            "rows": rows,
            "show_footnote": any(row["is_multi_category"] for row in rows),
        },
    )


def _category_tabs_for_country(indicators: list[Any]) -> tuple[str, ...]:
    present_categories: set[str] = set()
    for indicator in indicators:
        if indicator.primary_category:
            present_categories.add(indicator.primary_category)
        for category in indicator.secondary_categories or []:
            present_categories.add(category)

    ordered = [category for category in ALL_CATEGORY_TABS if category in present_categories]
    return tuple(ordered or ["Inflation", "Growth", "Labor"])


def _now():
    from datetime import datetime

    return datetime.now(timezone.utc)


def _normalize_news_item(item: dict[str, Any]) -> dict[str, Any] | None:
    title = (
        item.get("title")
        or item.get("headline")
        or item.get("name")
    )
    link = item.get("link") or item.get("url")
    if not title or not link:
        return None

    published_at = (
        item.get("date")
        or item.get("publishedAt")
        or item.get("published_at")
    )
    source = (
        item.get("source")
        or item.get("site")
        or item.get("source_name")
        or "EODHD"
    )
    summary = (
        item.get("content")
        or item.get("text")
        or item.get("description")
        or item.get("snippet")
        or ""
    )

    summary_text = str(summary).strip()
    if len(summary_text) > 180:
        summary_text = summary_text[:177].rstrip() + "..."

    tags = item.get("tags") or item.get("symbols") or []
    if isinstance(tags, str):
        tags = [part.strip() for part in tags.split(",") if part.strip()]

    return {
        "title": str(title).strip(),
        "link": str(link).strip(),
        "source": str(source).strip(),
        "published_at": published_at,
        "summary": summary_text,
        "tags": list(tags)[:3] if isinstance(tags, list) else [],
    }


@router.get("/", response_class=HTMLResponse)
async def landing_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the landing page with surprise strip and country cards."""
    countries = await list_country_summaries(session)
    surprises = await list_biggest_surprises(session, days=7, limit=5)
    currency_stance = await _build_currency_stance_dashboard(session)
    news_items: list[dict[str, Any]] = []

    try:
        async with EODHDClient() as client:
            raw_news = await client.fetch_financial_news(topic="economy", limit=6)
        news_items = [
            normalized
            for item in raw_news
            if (normalized := _normalize_news_item(item)) is not None
        ][:6]
    except (EODHDError, ValueError):
        news_items = []

    country_cards = [
        {
            "code": country.code,
            "flag": _flag_for_country(country.code),
            "name": country.name,
            "currency_code": country.currency_code,
            "latest_release_at": country.latest_release_at,
        }
        for country in countries
    ]

    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "request": request,
            "page_title": "Macro Dashboard",
            "country_cards": country_cards,
            "surprises": surprises,
            "news_items": news_items,
            "currency_stance": currency_stance,
        },
    )


@router.get("/country/{code}", response_class=HTMLResponse)
async def country_page(
    code: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the country page with default Inflation tab."""
    return await _render_country_template(request, session, code, "Inflation")


@router.get("/country/{code}/tab/{category}", response_class=HTMLResponse)
async def country_tab_fragment(
    code: str,
    category: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render HTMX fragment for a country/category indicator list."""
    normalized_category = TAB_SLUGS.get(category.lower())
    if normalized_category is None:
        raise HTTPException(status_code=404, detail="Category not found")

    country = await get_country(session, code)
    if country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    rows = await _build_country_rows(session, country.code, normalized_category)
    return templates.TemplateResponse(
        request,
        "_country_tab.html",
        {
            "request": request,
            "country_code": country.code,
            "rows": rows,
            "show_footnote": any(row["is_multi_category"] for row in rows),
        },
    )


@router.get("/country/{code}/indicator/{canonical_name}", response_class=HTMLResponse)
async def indicator_detail_page(
    code: str,
    canonical_name: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the indicator detail workstation page."""
    country = await get_country(session, code)
    if country is None:
        raise HTTPException(status_code=404, detail="Country not found")

    indicator = await get_indicator_by_country_and_name(
        session, country.code, canonical_name
    )
    if indicator is None:
        raise HTTPException(status_code=404, detail="Indicator not found")

    return templates.TemplateResponse(
        request,
        "indicator.html",
        {
            "request": request,
            "page_title": f"{indicator.display_name} | Macro Dashboard",
            "country": country,
            "indicator": indicator,
        },
    )


@router.get("/calendar", response_class=HTMLResponse)
async def calendar_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the macro calendar page."""
    countries = await list_country_summaries(session)
    return templates.TemplateResponse(
        request,
        "calendar.html",
        {
            "request": request,
            "page_title": "Economic Calendar | Macro Dashboard",
            "countries": countries,
            "category_tabs": ALL_CATEGORY_TABS,
            "calendar_windows": CALENDAR_WINDOWS,
        },
    )
