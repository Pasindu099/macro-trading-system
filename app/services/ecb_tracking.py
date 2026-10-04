"""ECB projection tracking using structured HICP index levels."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services import cb_tracking
from app.services.ecb_series import HICP_SA, load_series

BANK = "ECB"


async def rounds() -> list[date]:
    async with get_sessionmaker()() as session:
        result = await session.execute(text("SELECT DISTINCT release_date FROM cb_projection_values "
                                            "WHERE bank = 'ECB' ORDER BY release_date"))
        return result.scalars().all()


async def round_values(release_date: date) -> dict[tuple[str, str], float]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""SELECT variable, horizon, value::float AS value
            FROM cb_projection_values WHERE bank = 'ECB' AND release_date = :day AND stat = 'median'"""),
                                     {"day": release_date})
        return {(row.variable, row.horizon): row.value for row in rows}


def track_hicp(points: list[tuple[date, float]], projection: float, year: int) -> dict[str, Any]:
    prior = [value for day, value in points if day.year == year - 1 and day.month in (10, 11, 12)]
    current = {day.month: value for day, value in points if day.year == year}
    base = sum(prior) / 3 if len(prior) == 3 else None
    required = cb_tracking.required_monthly_pace(current, base, projection) if base else None
    actual = cb_tracking.annualised_3m([value for _, value in points])
    return {"projection": projection, "series": HICP_SA, "latest_month": points[-1][0] if points else None,
            "required_pace": round(required, 2) if required is not None else None,
            "actual_3m_ann": round(actual, 2) if actual is not None else None,
            "status": cb_tracking.status_vs_required(actual, required, cb_tracking.INFLATION_BAND) or "unavailable"}


async def get_tracking(year: int | None = None) -> dict[str, Any]:
    dates = await rounds()
    if not dates:
        return {"bank": BANK, "status": "unavailable", "reason": "No ECB projections stored"}
    latest = dates[-1]
    year = year or latest.year
    values = await round_values(latest)
    projection = values.get(("hicp_inflation", str(year)))
    if projection is None:
        return {"bank": BANK, "round": latest, "status": "unavailable", "reason": f"No {year} HICP projection"}
    points = await load_series(HICP_SA, date(year - 1, 1, 1))
    entry = track_hicp(points, projection, year) if points else {"status": "unavailable", "reason": "No HICP index levels stored"}
    return {"bank": BANK, "round": latest, "year": year, "variables": {"hicp_inflation": entry},
            "method": "ECB MPD annual HICP point projection; seasonally adjusted HICP Q4 average required pace vs 3m annualised"}


async def get_revisions() -> dict[str, Any]:
    dates = await rounds()
    if len(dates) < 2:
        return {"bank": BANK, "status": "unavailable", "reason": "Fewer than two ECB projection rounds stored"}
    latest, previous = dates[-1], dates[-2]
    now, before = await round_values(latest), await round_values(previous)
    rows = [{"variable": variable, "horizon": horizon, "median": value,
             "previous_median": before[(variable, horizon)],
             "median_change": round(value - before[(variable, horizon)], 3)}
            for (variable, horizon), value in sorted(now.items()) if (variable, horizon) in before]
    return {"bank": BANK, "round": latest, "previous_round": previous, "revisions": rows,
            "method": "point projection change in percentage points"}
