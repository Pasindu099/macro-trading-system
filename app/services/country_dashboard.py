"""Country indicator histories and representative macro series."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.db.models import Indicator, IndicatorRelease

HISTORY_LIMIT = 500
PROFILE_RULES = (
    ("Monetary Policy", ("fed funds", "policy rate", "target rate", "interest rate", "cash rate", "bank rate", "deposit rate", "overnight rate", "main refinancing"), True),
    ("Inflation", ("cpi yoy", "cpi (yoy)", "headline cpi", "cpi headline yoy", "consumer price index", "hicp (yoy)", "hicp yoy"), False),
    ("Labor", ("unemployment", "jobless"), False),
    ("Growth", ("gdp qoq", "gdp (qoq)", "gross domestic product", "gdp mom", "gdp (mom)", "gdp yoy", "gdp (yoy)"), False),
)


async def get_country_rows(session: AsyncSession, country_code: str, category: str) -> list[dict[str, Any]]:
    """Return revision-aware actuals, preserving the old selection order."""
    now = datetime.now(timezone.utc)
    result = await session.execute(
        select(Indicator).where(
            Indicator.country_code == country_code.upper(),
            or_(Indicator.primary_category == category, Indicator.secondary_categories.any(category)),
        ).order_by(Indicator.importance.asc(), Indicator.display_name.asc())
    )
    indicators = result.scalars().all()
    if not indicators:
        return []
    order = (
        IndicatorRelease.period_start_date.desc().nullslast(),
        desc(IndicatorRelease.released_at),
        desc(IndicatorRelease.retrieved_at),
        desc(IndicatorRelease.id),
    )
    ranked = select(
        IndicatorRelease,
        func.row_number().over(partition_by=IndicatorRelease.indicator_id, order_by=order).label("row_number"),
    ).where(IndicatorRelease.indicator_id.in_([indicator.id for indicator in indicators])).subquery()
    release = aliased(IndicatorRelease, ranked)
    history_result = await session.execute(
        select(release).where(ranked.c.row_number <= HISTORY_LIMIT)
        .order_by(release.indicator_id, ranked.c.row_number)
    )
    histories: dict[int, list[IndicatorRelease]] = {}
    for item in history_result.scalars().all():
        histories.setdefault(item.indicator_id, []).append(item)
    rows = []
    for indicator in indicators:
        by_period = {}
        for item in histories.get(indicator.id, []):
            key = (getattr(item, "period", None), getattr(item, "period_start_date", None),
                   getattr(item, "released_at", None))
            by_period.setdefault(key, item)
        history = list(reversed(list(by_period.values())[:12]))
        actuals = [release for release in history if release.actual is not None
                   and getattr(release, "released_at", now) <= now]
        if not actuals:
            continue
        latest = actuals[-1]
        values = [float(release.actual) for release in actuals]
        previous = (float(latest.previous) if getattr(latest, "previous", None) is not None
                    else float(actuals[-2].actual) if len(actuals) >= 2 else None)
        if len(values) < 2 and previous is not None and getattr(latest, "previous", None) is not None:
            values = [previous, values[-1]]
        rows.append({
            "indicator_id": indicator.id,
            "canonical_name": indicator.canonical_name,
            "latest_value": float(latest.actual),
            "previous_value": previous,
            "released_at": latest.released_at,
            "sparkline_values": values,
            "is_multi_category": bool(indicator.secondary_categories),
        })
    return rows


def get_country_profile(rows_by_category: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Select policy, inflation, labor and growth indicators by old priority rules."""
    selected = {}
    for category, terms, allow_fallback in PROFILE_RULES:
        rows = rows_by_category.get(category, [])
        match = next((row for term in terms for row in rows
                      if term in str(row.get("canonical_name") or "").replace("_", " ").lower()), None)
        if match is None and allow_fallback and rows:
            match = rows[0]
        selected[category] = match
    tracked = [row for rows in rows_by_category.values() for row in rows[:3]
               if row.get("latest_value") is not None]
    changed = sum(1 for row in tracked if len(row.get("sparkline_values", [])) >= 2
                  and row["sparkline_values"][-1] != row["sparkline_values"][-2])
    return {"selected": selected, "changed_indicator_count": changed}
