"""Synthetic FX crosses from two observed USD legs when a direct symbol is absent."""

from datetime import date
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.fx_spot import insert_fx_observation_idempotent
from app.services.government_yields import hash_payload


def _usd_leg(currency: str) -> str:
    return f"{currency}/USD" if currency in {"EUR", "GBP", "AUD", "NZD"} else f"USD/{currency}"


def synthesize_cross(pair: str, legs: dict[str, dict[date, float]]) -> dict[date, float]:
    base, quote = pair.split("/")
    base_leg, quote_leg = _usd_leg(base), _usd_leg(quote)
    dates = set(legs.get(base_leg, {})) & set(legs.get(quote_leg, {}))
    result = {}
    for day in dates:
        left, right = legs[base_leg][day], legs[quote_leg][day]
        if left <= 0 or right <= 0:
            continue
        usd_per_base = left if base_leg.endswith("/USD") else 1 / left
        quote_per_usd = right if quote_leg.startswith("USD/") else 1 / right
        result[day] = round(usd_per_base * quote_per_usd, 8)
    return result


async def ingest_synthetic_cross(
    session: AsyncSession, pair: str, from_date: date, to_date: date,
) -> tuple[int, int]:
    base, quote = pair.split("/")
    legs_needed = [_usd_leg(base), _usd_leg(quote)]
    result = await session.execute(text("""
        SELECT DISTINCT ON (pair, observation_date)
            pair, observation_date, close_value::float AS close_value
        FROM fx_spot_observations
        WHERE pair = ANY(:pairs) AND observation_date BETWEEN :from_date AND :to_date
          AND source_type <> 'synthetic' AND quality_status = 'valid' AND NOT is_outlier
        ORDER BY pair, observation_date, ingested_at DESC, id DESC
    """), {"pairs": legs_needed, "from_date": from_date, "to_date": to_date})
    legs: dict[str, dict[date, float]] = {}
    for row in result:
        legs.setdefault(row.pair, {})[row.observation_date] = row.close_value
    crosses = synthesize_cross(pair, legs)
    inserted = 0
    for day, close in sorted(crosses.items()):
        raw = {"date": day.isoformat(), "close": close, "usd_legs": legs_needed}
        inserted += await insert_fx_observation_idempotent(session, {
            "provider": "synthetic", "provider_symbol": pair.replace("/", "") + ".SYNTH",
            "pair": pair, "base_currency": base, "quote_currency": quote,
            "close_value": Decimal(str(close)), "observation_date": day,
            "provider_timestamp": None, "data_frequency": "daily",
            "source_type": "synthetic", "quality_status": "valid",
            "payload_hash": hash_payload(raw), "raw_payload": raw, "validation_errors": None,
        })
    return len(crosses), inserted
