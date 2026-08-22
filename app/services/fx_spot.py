"""Durable EODHD FX spot ingestion for rates-FX analytics."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FxSpotObservation
from app.ingestion.eodhd_client import EODHDClient, EODHDError
from app.services.government_yields import hash_payload

FX_PAIR_SYMBOLS: dict[str, str] = {
    "EUR/USD": "EURUSD.FOREX",
    "GBP/USD": "GBPUSD.FOREX",
    "USD/JPY": "USDJPY.FOREX",
    "AUD/USD": "AUDUSD.FOREX",
    "NZD/USD": "NZDUSD.FOREX",
    "USD/CAD": "USDCAD.FOREX",
    "USD/CHF": "USDCHF.FOREX",
    "EUR/GBP": "EURGBP.FOREX",
    "EUR/JPY": "EURJPY.FOREX",
    "GBP/JPY": "GBPJPY.FOREX",
    "AUD/JPY": "AUDJPY.FOREX",
    "AUD/NZD": "AUDNZD.FOREX",
    "EUR/CHF": "EURCHF.FOREX",
    "CAD/JPY": "CADJPY.FOREX",
}


@dataclass(slots=True)
class FxIngestStats:
    started_at: datetime
    finished_at: datetime | None = None
    requests_used: int = 0
    observations_seen: int = 0
    observations_inserted: int = 0
    pairs_missing: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors and self.observations_seen == 0:
            return "failed"
        if self.errors or self.pairs_missing:
            return "partial"
        return "success"


async def ingest_eodhd_fx_spot(
    session: AsyncSession,
    client: EODHDClient,
    *,
    from_date: date,
    to_date: date,
    pairs: list[str] | None = None,
    max_requests: int | None = None,
    dry_run: bool = False,
) -> FxIngestStats:
    """Fetch and persist EODHD FX spot history with partial-success semantics."""
    stats = FxIngestStats(started_at=datetime.now(UTC))
    target_pairs = [pair.upper() for pair in (pairs or list(FX_PAIR_SYMBOLS))]
    for pair in target_pairs:
        if max_requests is not None and stats.requests_used >= max_requests:
            break
        symbol = FX_PAIR_SYMBOLS.get(pair)
        if symbol is None:
            stats.errors.append(f"{pair}: unsupported pair")
            continue
        try:
            rows = await client.fetch_eod_history(
                symbol,
                from_date=from_date,
                to_date=to_date,
                period="d",
            )
            stats.requests_used += 1
        except EODHDError as exc:
            stats.errors.append(f"{pair}: {exc}")
            continue
        if not rows:
            stats.pairs_missing.append(pair)
            continue
        for row in rows:
            stats.observations_seen += 1
            record = build_fx_observation_record(pair, symbol, row)
            if record is None:
                stats.errors.append(f"{pair}: invalid row skipped")
                continue
            if not dry_run:
                stats.observations_inserted += await insert_fx_observation_idempotent(
                    session,
                    record,
                )
    stats.finished_at = datetime.now(UTC)
    return stats


def build_fx_observation_record(
    pair: str,
    provider_symbol: str,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    observation_date = _parse_date(row.get("date"))
    close_value = _parse_decimal(row.get("close"))
    if observation_date is None or close_value is None:
        return None
    base, quote = pair.split("/")
    validation_errors = validate_fx_close(close_value)
    return {
        "provider": "eodhd",
        "provider_symbol": provider_symbol,
        "pair": pair,
        "base_currency": base,
        "quote_currency": quote,
        "close_value": close_value,
        "observation_date": observation_date,
        "provider_timestamp": _parse_datetime(row.get("timestamp") or row.get("datetime")),
        "data_frequency": "daily",
        "source_type": "licensed_api",
        "quality_status": "invalid" if validation_errors else "valid",
        "payload_hash": hash_payload(row),
        "raw_payload": row,
        "validation_errors": validation_errors or None,
    }


async def insert_fx_observation_idempotent(
    session: AsyncSession,
    record: dict[str, Any],
) -> int:
    stmt = (
        insert(FxSpotObservation)
        .values(**record)
        .on_conflict_do_nothing(constraint="uq_fx_spot_provider_symbol_date_hash")
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)


def validate_fx_close(value: Decimal) -> list[str]:
    if value <= 0:
        return ["fx_close_must_be_positive"]
    if value > Decimal("10000"):
        return ["fx_close_above_reasonable_ceiling"]
    return []


def _parse_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
