"""ECB deposit-rate regime and market-conditioned projection gap."""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

from app.db.session import get_sessionmaker
from app.services.ecb_tracking import get_tracking, round_values, rounds
from app.services.fed_regime import REGIMES, classify_regime

BANK = "ECB"
GAP_METHOD = ("Market-conditioned bank: final annual HICP point projection minus the ECB 2% target. "
              "Below -0.1pp implies dovish easing risk; above +0.1pp implies hawkish risk. No rate-bp estimate.")


def qualitative_gap(projections: dict[str, float]) -> dict[str, Any]:
    if not projections:
        return {"status": "unavailable", "reason": "No HICP projection horizon"}
    horizon = max(projections, key=int)
    delta = round(projections[horizon] - 2.0, 3)
    direction = "dovish" if delta < -0.1 else "hawkish" if delta > 0.1 else "neutral"
    message = {"dovish": "dovish: projections imply more easing than priced",
               "hawkish": "hawkish: projections imply less easing than priced",
               "neutral": "neutral: end-horizon HICP is near target"}[direction]
    return {"status": "available", "horizon": horizon, "projection_pct": projections[horizon],
            "target_pct": 2.0, "difference_pp": delta, "direction": direction, "message": message,
            "method": GAP_METHOD}


async def policy_decisions() -> list[tuple[date, float]]:
    async with get_sessionmaker()() as session:
        rows = await session.execute(text("""SELECT DISTINCT ON (r.released_at::date)
            r.released_at::date AS day, r.actual::float AS rate
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = 'EU' AND i.canonical_name = 'ecb_deposit_rate'
              AND r.is_latest AND r.actual IS NOT NULL AND r.released_at <= now()
            ORDER BY r.released_at::date, r.retrieved_at DESC, r.id DESC"""))
        return [(row.day, row.rate) for row in rows]


async def get_regime(today: date | None = None) -> dict[str, Any]:
    result = classify_regime(await policy_decisions(), today or date.today())
    return {"bank": BANK, "as_of": today or date.today(), **result, "ladder": REGIMES,
            "method": "Fed v1 rate-level and last-move rules applied to ECB deposit facility rate"}


async def get_gap() -> dict[str, Any]:
    dates = await rounds()
    if not dates:
        return {"bank": BANK, "status": "unavailable", "reason": "No ECB projections stored", "method": GAP_METHOD}
    values = await round_values(dates[-1])
    result = qualitative_gap({horizon: value for (variable, horizon), value in values.items()
                              if variable == "hicp_inflation" and horizon.isdigit()})
    return {"bank": BANK, "round": dates[-1], **result}
