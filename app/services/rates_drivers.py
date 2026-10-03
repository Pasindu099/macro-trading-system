"""Correlate daily yield-spread changes with FX spot changes."""

from datetime import date
from math import sqrt
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker


def _pearson(left: list[float], right: list[float]) -> float | None:
    if len(left) != len(right) or len(left) < 2:
        return None
    n = len(left)
    sum_left, sum_right = sum(left), sum(right)
    covariance = sum(a * b for a, b in zip(left, right, strict=True)) - sum_left * sum_right / n
    variance_left = sum(a * a for a in left) - sum_left * sum_left / n
    variance_right = sum(b * b for b in right) - sum_right * sum_right / n
    if variance_left <= 0 or variance_right <= 0:
        return None
    return covariance / sqrt(variance_left * variance_right)


def correlation_changes(
    spread: dict[date, float], spot: dict[date, float], observations: int,
) -> float | None:
    common = sorted(set(spread) & set(spot))
    if len(common) < observations + 1:
        return None
    dates = common[-(observations + 1):]
    spread_changes = [spread[day] - spread[previous] for previous, day in zip(dates, dates[1:])]
    spot_changes = [spot[day] - spot[previous] for previous, day in zip(dates, dates[1:])]
    return _pearson(spread_changes, spot_changes)


def calculate_drivers(pair: str, tenor: str, spread: dict[date, float], spot: dict[date, float]) -> dict[str, Any]:
    base = {"pair": pair, "tenor": tenor}
    if not spread:
        return {**base, "status": "unavailable", "reason": "Yield spread history is absent"}
    if not spot:
        return {**base, "status": "unavailable", "reason": "Spot history is absent"}
    common = sorted(set(spread) & set(spot))
    if len(common) < 61:
        return {**base, "status": "unavailable", "reason": "Fewer than 60 aligned daily changes", "aligned_dates": len(common)}
    c60 = correlation_changes(spread, spot, 60)
    c15 = correlation_changes(spread, spot, 15)
    if c60 is None or c15 is None:
        return {**base, "status": "unavailable", "reason": "Daily changes have zero variance"}
    status = "weak_link" if abs(c60) < 0.25 else "diverging" if c60 - c15 > 0.45 else "aligned"
    latest = common[-1]
    prior = common[-21]
    return {
        **base, "status": status, "as_of": latest,
        "correlation_60": c60, "correlation_15": c15,
        "spread_bp": spread[latest], "spread_change_20_bp": spread[latest] - spread[prior],
        "spot": spot[latest], "spot_change_20_pct": (spot[latest] / spot[prior] - 1) * 100 if spot[prior] else None,
    }


async def get_pair_drivers(pair: str, tenor: str) -> dict[str, Any]:
    async with get_sessionmaker()() as session:
        spread_result = await session.execute(text("""
            SELECT obs_date, spread_bp::float AS value FROM yield_spreads
            WHERE spread_name = :pair AND tenor = :tenor ORDER BY obs_date
        """), {"pair": pair, "tenor": tenor})
        spot_result = await session.execute(text("""
            SELECT DISTINCT ON (observation_date)
                observation_date, close_value::float AS value
            FROM fx_spot_observations
            WHERE pair = :pair AND quality_status = 'valid' AND NOT is_outlier
            ORDER BY observation_date, ingested_at DESC, id DESC
        """), {"pair": pair})
        spread = {row.obs_date: row.value for row in spread_result}
        spot = {row.observation_date: row.value for row in spot_result}
    return calculate_drivers(pair, tenor, spread, spot)
