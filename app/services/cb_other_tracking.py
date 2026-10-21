"""Required CPI pace versus latest 3-month annualised pace for four CBs."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.services import cb_price_series, cb_projection_loaders, cb_tracking

BANKS = ("BOE", "BOJ", "BOC", "SNB")


def _fiscal_required(points: dict[date, float], year: int, projection: float) -> float | None:
    base_months = [date(year - 1 if month >= 4 else year, month, 1) for month in (*range(4, 13), *range(1, 4))]
    forecast_months = [date(year if month >= 4 else year + 1, month, 1) for month in (*range(4, 13), *range(1, 4))]
    if not all(month in points for month in base_months):
        return None
    known = [month for month in forecast_months if month in points]
    if not known or known[-1] == forecast_months[-1] or known != forecast_months[:len(known)]:
        return None
    latest = known[-1]
    target = sum(points[month] for month in base_months) * (1 + projection / 100)

    def difference(growth: float) -> float:
        return sum(points[month] if month <= latest else points[latest] * (1 + growth) ** (i - len(known) + 1)
                   for i, month in enumerate(forecast_months)) - target

    growth = cb_tracking._bisect(difference, -0.2, 0.2)
    return ((1 + growth) ** 12 - 1) * 100


def track(bank: str, points: list[tuple[date, float]], projection: float, year: int) -> dict[str, Any]:
    variable = cb_projection_loaders.INFLATION[bank]
    entry: dict[str, Any] = {"projection": projection, "series": cb_price_series.SOURCES[bank][0],
                              "latest_month": points[-1][0] if points else None}
    if not points:
        return {**entry, "status": "unavailable", "reason": "No matching official CPI index levels stored"}
    values = dict(points)
    latest = points[-1][0]
    if date.today() - latest > timedelta(days=90):
        return {**entry, "status": "unavailable", "reason": f"Official {variable} index is stale ({latest})"}
    trailing = [date(latest.year - (latest.month - offset - 1 < 0), (latest.month - offset - 1) % 12 + 1, 1)
                for offset in (3, 2, 1, 0)]
    if not all(month in values for month in trailing):
        return {**entry, "status": "unavailable", "reason": "Missing month in latest four CPI index levels"}
    actual = cb_tracking.annualised_3m([values[month] for month in trailing])
    if bank == "BOJ":
        required = _fiscal_required(values, year, projection)
        horizon = f"FY{year} average"
    else:
        prior = [values.get(date(year - 1, month, 1)) for month in (10, 11, 12)]
        current = {month: values[date(year, month, 1)] for month in range(1, 13)
                   if date(year, month, 1) in values}
        required = cb_tracking.required_monthly_pace(current, sum(prior) / 3, projection) if all(v is not None for v in prior) else None
        horizon = f"{year} Q4 average"
    if required is None:
        return {**entry, "actual_3m_ann": round(actual, 2), "status": "unavailable",
                "reason": f"Missing baseline or current CPI index month for {horizon}"}
    return {**entry, "required_pace": round(required, 2), "actual_3m_ann": round(actual, 2),
            "horizon": horizon, "status": cb_tracking.status_vs_required(actual, required, cb_tracking.INFLATION_BAND)}


async def get_tracking(bank: str, year: int | None = None) -> dict[str, Any]:
    if bank not in BANKS:
        raise ValueError(f"Unsupported bank: {bank}")
    dates = await cb_projection_loaders.rounds(bank)
    if not dates:
        return {"bank": bank, "status": "unavailable", "reason": "No official projections stored"}
    latest = dates[-1]
    year = year or latest.year
    variable = cb_projection_loaders.INFLATION[bank]
    values = await cb_projection_loaders.round_values(bank, latest)
    horizon = str(year) if bank == "BOJ" else f"{year}Q4"
    projection = next((values.get((variable, horizon, stat)) for stat in ("median", "point")
                       if values.get((variable, horizon, stat)) is not None), None)
    if projection is None:
        entry = {"status": "unavailable", "reason": f"No {horizon} {variable} point or median projection"}
    else:
        points = await cb_price_series.load_levels(bank, date(year - 2, 1, 1))
        entry = track(bank, points, projection, year)
    return {"bank": bank, "round": latest, "year": year, "variables": {variable: entry},
            "method": "Official CPI index, unadjusted; projection required pace versus latest 3m annualised; 0.5pp band"}
