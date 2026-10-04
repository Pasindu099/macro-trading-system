"""Clearly labelled German-yield approximation for ECB rate repricing."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.rate_probability import resolve_current_rate


METHOD_LABEL = ("Approximate: from German government yields, not €STR OIS. "
                "Bunds trade below €STR because of collateral scarcity, so this "
                "understates the expected rate level; read changes, not levels.")
TENORS = {"3M": 3, "6M": 6, "1Y": 12, "2Y": 24}


def choose_tenor(available: set[str], horizon_months: int) -> str | None:
    return min(available, key=lambda t: (abs(TENORS[t] - horizon_months), TENORS[t])) if available else None


def value_on_or_before(rows: dict[date, float], day: date) -> float | None:
    eligible = [d for d in rows if d <= day]
    return rows[max(eligible)] if eligible else None


async def get_ecb_yield_approximation(session: AsyncSession) -> dict[str, Any]:
    result = await session.execute(text("""
        SELECT DISTINCT ON (maturity, market_observation_date)
            maturity, market_observation_date AS day, yield_value::float AS rate
        FROM government_yield_observations
        WHERE country_code = 'DE' AND maturity = ANY(:tenors)
          AND market_observation_date >= CURRENT_DATE - INTERVAL '45 days'
          AND quality_status = 'valid' AND NOT is_outlier
        ORDER BY maturity, market_observation_date, ingested_at DESC, id DESC
    """), {"tenors": list(TENORS)})
    series: dict[str, dict[date, float]] = {tenor: {} for tenor in TENORS}
    for row in result:
        series[row.maturity][row.day] = float(row.rate)
    current = await resolve_current_rate("ECB", session)
    today = date.today()
    horizons = []
    for label, months in (("3M", 3), ("6M", 6), ("12M", 12)):
        available = {t for t, points in series.items() if points and max(points) >= today - timedelta(days=7)}
        tenor = choose_tenor(available, months)
        if tenor is None:
            horizons.append({"horizon": label, "status": "unavailable", "reason": "No recent German yield"})
            continue
        points = series[tenor]
        latest_day = max(points)
        latest = points[latest_day]
        move = latest - current["rate"]
        week = value_on_or_before(points, latest_day - timedelta(days=7))
        month = value_on_or_before(points, latest_day - timedelta(days=30))
        horizons.append({"horizon": label, "status": "approximate", "yield_tenor": tenor,
                         "as_of": latest_day, "german_yield_pct": latest,
                         "deposit_rate_pct": current["rate"], "implied_move_bp": round(move * 100, 2),
                         "change_1w_bp": round((latest - week) * 100, 2) if week is not None else None,
                         "change_1m_bp": round((latest - month) * 100, 2) if month is not None else None})
    main = horizons[-1]
    return {"bank": "ECB", "method": "german_yield_approximation", "label": METHOD_LABEL,
            "status": "approximate" if any(h["status"] == "approximate" for h in horizons) else "unavailable",
            "horizons": horizons, "main_change_1w_bp": main.get("change_1w_bp"),
            "main_change_1m_bp": main.get("change_1m_bp"), "meeting_probabilities": "not priced"}
