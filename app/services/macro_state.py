"""Raw Macro State data for the replacement UI."""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


_PREFERRED = text("""
WITH latest_observed AS (
    SELECT currency_code, max(release_date_utc) AS date
    FROM processed.indicator_features
    WHERE release_timestamp_utc <= CURRENT_TIMESTAMP
    GROUP BY currency_code
), latest AS (
    SELECT DISTINCT ON (s.currency) s.currency, s.date
    FROM processed.cb_preferred_score s
    JOIN latest_observed o ON o.currency_code = s.currency AND s.date <= o.date
    WHERE s.date <= CURRENT_DATE
    ORDER BY s.currency, s.date DESC
)
SELECT s.date, s.country_code, s.currency, s.inflation_score, s.labor_score,
       s.growth_score, s.cb_strength_score AS overall_score,
       s.confidence, r.rank_strongest
FROM processed.cb_preferred_score s
JOIN processed.cb_preferred_rankings r
  ON r.date = s.date AND r.currency = s.currency
JOIN latest l ON l.date = s.date AND l.currency = s.currency
ORDER BY r.rank_strongest
""")

_LEGACY = text("""
WITH latest_observed AS (
    SELECT currency_code, max(release_date_utc) AS date
    FROM processed.indicator_features
    WHERE release_timestamp_utc <= CURRENT_TIMESTAMP
    GROUP BY currency_code
), latest AS (
    SELECT s.currency, s.window_months, max(s.date) AS date
    FROM processed.currency_stance s
    JOIN latest_observed o ON o.currency_code = s.currency AND s.date <= o.date
    WHERE s.date <= CURRENT_DATE
    GROUP BY s.currency, s.window_months
)
SELECT s.date, s.country_code, s.currency, s.window_months,
       s.inflation_score, s.labor_score, s.growth_score,
       s.overall_stance_score AS overall_score,
       s.confidence, r.rank_strongest
FROM processed.currency_stance s
JOIN processed.currency_stance_rankings r
  ON r.date = s.date AND r.currency = s.currency
 AND r.window_months = s.window_months
JOIN latest l ON l.date = s.date AND l.window_months = s.window_months AND l.currency = s.currency
ORDER BY s.window_months, r.rank_strongest
""")


def _raw_rows(result: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in result.mappings().all()]


async def get_macro_state_board(session: AsyncSession) -> dict[str, Any]:
    """Prefer CB scores; use legacy stance on query failure or empty output."""
    try:
        primary = _raw_rows(await session.execute(_PREFERRED))
    except Exception:
        await session.rollback()
        primary = []
    if primary:
        return {"source": "cb_preferred_score", "rows": primary}
    try:
        legacy = _raw_rows(await session.execute(_LEGACY))
    except Exception:
        await session.rollback()
        legacy = []
    return {"source": "currency_stance", "rows": legacy}
