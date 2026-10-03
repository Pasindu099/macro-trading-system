"""ICE US Dollar Index computed from its six FX components."""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fx_spot import insert_fx_observation_idempotent
from app.services.government_yields import hash_payload

DXY_CONSTANT = 50.14348112
DXY_WEIGHTS = {
    "EUR/USD": -0.576, "USD/JPY": 0.136, "GBP/USD": -0.119,
    "USD/CAD": 0.091, "USD/SEK": 0.042, "USD/CHF": 0.036,
}
DXY_PAIR = "USD/DXY"
DXY_SOURCE = "computed_dxy"


def compute_dxy(legs: dict[str, dict[date, float]]) -> dict[date, float]:
    """DXY on dates where all six components have a close."""
    dates = set.intersection(*(set(legs.get(pair, {})) for pair in DXY_WEIGHTS))
    output = {}
    for day in dates:
        value = DXY_CONSTANT
        for pair, weight in DXY_WEIGHTS.items():
            value *= legs[pair][day] ** weight
        output[day] = round(value, 6)
    return output


async def build_computed_dxy(session: AsyncSession) -> int:
    """Insert computed DXY rows; a changed component yields a new newest row for that date."""
    result = await session.execute(text("""
        SELECT DISTINCT ON (pair, observation_date) pair, observation_date, close_value::float AS close
        FROM fx_spot_observations
        WHERE pair = ANY(:pairs) AND source_type NOT IN ('synthetic', 'computed_dxy')
          AND quality_status = 'valid' AND NOT is_outlier
        ORDER BY pair, observation_date, ingested_at DESC, id DESC
    """), {"pairs": list(DXY_WEIGHTS)})
    legs: dict[str, dict[date, float]] = {}
    for row in result:
        legs.setdefault(row.pair, {})[row.observation_date] = row.close
    inserted = 0
    for day, value in sorted(compute_dxy(legs).items()):
        raw = {"date": day.isoformat(), "close": value, "components": {p: legs[p][day] for p in DXY_WEIGHTS}}
        inserted += await insert_fx_observation_idempotent(session, {
            "provider": "computed", "provider_symbol": "DXY.COMPUTED", "pair": DXY_PAIR,
            "base_currency": "USD", "quote_currency": "DXY", "close_value": Decimal(str(value)),
            "observation_date": day, "provider_timestamp": None, "data_frequency": "daily",
            "source_type": DXY_SOURCE, "quality_status": "valid",
            "payload_hash": hash_payload(raw), "raw_payload": raw, "validation_errors": None,
        })
    return inserted
