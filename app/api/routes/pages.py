"""Server-rendered page routes for the Step 5 frontend."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import and_, desc, or_, select
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

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory=str(Path("app/web/templates")))

CATEGORY_TABS = ("Inflation", "Growth", "Labor")
TAB_SLUGS = {category.lower(): category for category in CATEGORY_TABS}
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


async def _build_country_rows(
    session: AsyncSession,
    country_code: str,
    category: str,
) -> list[dict[str, Any]]:
    rows_q = await session.execute(
        select(Indicator, IndicatorRelease)
        .outerjoin(
            IndicatorRelease,
            and_(
                IndicatorRelease.indicator_id == Indicator.id,
                IndicatorRelease.is_latest.is_(True),
            ),
        )
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
    for indicator, latest_release in rows_q.all():
        history_q = await session.execute(
            select(IndicatorRelease)
            .where(
                IndicatorRelease.indicator_id == indicator.id,
                IndicatorRelease.is_latest.is_(True),
            )
            .order_by(
                IndicatorRelease.period_start_date.desc().nullslast(),
                desc(IndicatorRelease.released_at),
                desc(IndicatorRelease.id),
            )
            .limit(12)
        )
        history_rows = list(reversed(history_q.scalars().all()))
        sparkline_values = [
            float(row.actual) if row.actual is not None else None
            for row in history_rows
        ]

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
    return templates.TemplateResponse(
        request,
        "country.html",
        {
            "request": request,
            "page_title": f"{country.name} | Macro Dashboard",
            "country": country,
            "country_flag": _flag_for_country(country.code),
            "active_category": active_category,
            "category_tabs": CATEGORY_TABS,
            "rows": rows,
            "show_footnote": any(row["is_multi_category"] for row in rows),
        },
    )


@router.get("/", response_class=HTMLResponse)
async def landing_page(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the landing page with surprise strip and country cards."""
    countries = await list_country_summaries(session)
    surprises = await list_biggest_surprises(session, days=7, limit=5)

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
            "category_tabs": CATEGORY_TABS,
            "calendar_windows": CALENDAR_WINDOWS,
        },
    )
