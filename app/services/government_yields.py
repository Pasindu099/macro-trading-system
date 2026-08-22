"""Durable EODHD government-yield ingestion and quality checks."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import GovernmentYieldIngestionStatus, GovernmentYieldObservation
from app.ingestion.eodhd_client import (
    GBOND_COUNTRY_PREFIXES,
    GBOND_MATURITIES,
    GBOND_MATURITY_MONTHS,
    EODHDAuthError,
    EODHDClient,
    EODHDError,
    build_gbond_symbol,
)

logger = logging.getLogger(__name__)

PROVIDER = "eodhd"
DATA_FREQUENCY = "daily"
SOURCE_TYPE = "licensed_api"
OBSERVATION_KIND = "actual"
QUALITY_VALID = "valid"
QUALITY_INVALID = "invalid"
QUALITY_STALE = "stale"
QUALITY_UNAVAILABLE = "unavailable"

MIN_REASONABLE_YIELD = Decimal("-10")
MAX_REASONABLE_YIELD = Decimal("100")


@dataclass(slots=True)
class GovernmentYieldIngestStats:
    """Summary of one government-yield ingestion/check run."""

    job_name: str
    started_at: datetime
    finished_at: datetime | None = None
    observations_seen: int = 0
    observations_inserted: int = 0
    symbols_requested: int = 0
    symbols_missing: list[str] = field(default_factory=list)
    stale_symbols: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.errors and self.observations_inserted == 0 and self.observations_seen == 0:
            return "failed"
        if self.errors or self.symbols_missing or self.stale_symbols:
            return "partial"
        return "success"


def configured_gbond_symbols() -> list[str]:
    """Return every configured EODHD GBOND symbol, including actual 2Y tenors."""
    return [
        build_gbond_symbol(prefix, maturity)
        for prefix in GBOND_COUNTRY_PREFIXES
        for maturity in GBOND_MATURITIES
    ]


def symbol_metadata(country_prefix: str, maturity: str) -> dict[str, Any]:
    """Return normalized metadata for one configured GBOND symbol."""
    prefix = country_prefix.upper()
    mat = maturity.upper()
    meta = GBOND_COUNTRY_PREFIXES[prefix]
    return {
        "provider": PROVIDER,
        "provider_symbol": build_gbond_symbol(prefix, mat),
        "provider_country_prefix": prefix,
        "country_code": meta["country_code"],
        "currency_code": meta["currency_code"],
        "maturity": mat,
        "maturity_months": GBOND_MATURITY_MONTHS[mat],
        "market_timezone": meta["market_timezone"],
    }


async def ingest_eodhd_government_yields(
    session: AsyncSession,
    client: EODHDClient,
    *,
    from_date: date,
    to_date: date,
    country_prefixes: list[str] | None = None,
    maturities: list[str] | None = None,
    job_name: str = "government_yields_incremental",
    stale_after_days: int = 3,
    available_symbols: set[str] | None = None,
) -> GovernmentYieldIngestStats:
    """Fetch and persist EODHD GBOND yields with partial-success semantics."""
    if from_date > to_date:
        raise ValueError("from_date must be <= to_date")

    stats = GovernmentYieldIngestStats(
        job_name=job_name,
        started_at=datetime.now(UTC),
    )
    prefixes = [p.upper() for p in (country_prefixes or list(GBOND_COUNTRY_PREFIXES))]
    tenors = [m.upper() for m in (maturities or list(GBOND_MATURITIES))]

    if available_symbols is None:
        available_symbols = await _fetch_available_gbond_symbols(client, stats)
    if available_symbols is None and stats.errors:
        stats.finished_at = datetime.now(UTC)
        await upsert_government_yield_status(session, stats)
        return stats

    for prefix in prefixes:
        for maturity in tenors:
            symbol = build_gbond_symbol(prefix, maturity)
            stats.symbols_requested += 1
            if available_symbols is not None and symbol_name(symbol) not in available_symbols:
                stats.symbols_missing.append(symbol)
                continue

            try:
                rows = await client.fetch_government_yield_history(
                    prefix,
                    maturity,
                    from_date=from_date,
                    to_date=to_date,
                )
            except EODHDAuthError as exc:
                stats.errors.append(f"{symbol}: auth_or_entitlement: {exc}")
                logger.error("EODHD GBOND entitlement/auth failure for %s", symbol)
                stats.finished_at = datetime.now(UTC)
                await upsert_government_yield_status(session, stats)
                return stats
            except EODHDError as exc:
                stats.errors.append(f"{symbol}: {exc}")
                logger.warning("EODHD GBOND fetch failed for %s: %s", symbol, exc)
                continue

            if not rows:
                stats.symbols_missing.append(symbol)
                continue

            latest_observation_date: date | None = None
            for row in rows:
                stats.observations_seen += 1
                record = build_observation_record(prefix, maturity, row)
                if record is None:
                    stats.errors.append(f"{symbol}: invalid row skipped")
                    continue
                latest_observation_date = max(
                    latest_observation_date or record["market_observation_date"],
                    record["market_observation_date"],
                )
                inserted = await insert_observation_idempotent(session, record)
                stats.observations_inserted += inserted

            if _is_stale(latest_observation_date, to_date, stale_after_days):
                stats.stale_symbols.append(symbol)

    stats.finished_at = datetime.now(UTC)
    await upsert_government_yield_status(session, stats)
    return stats


async def check_government_yield_staleness(
    session: AsyncSession,
    *,
    as_of: date | None = None,
    stale_after_days: int = 3,
    job_name: str = "government_yields_stale_check",
) -> GovernmentYieldIngestStats:
    """Check latest stored observations for missing/stale configured symbols."""
    today = as_of or date.today()
    stats = GovernmentYieldIngestStats(job_name=job_name, started_at=datetime.now(UTC))
    for symbol in configured_gbond_symbols():
        stats.symbols_requested += 1
        result = await session.execute(
            select(GovernmentYieldObservation.market_observation_date)
            .where(GovernmentYieldObservation.provider_symbol == symbol)
            .order_by(desc(GovernmentYieldObservation.market_observation_date))
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        if latest is None:
            stats.symbols_missing.append(symbol)
        elif _is_stale(latest, today, stale_after_days):
            stats.stale_symbols.append(symbol)
    stats.finished_at = datetime.now(UTC)
    await upsert_government_yield_status(session, stats)
    return stats


def build_observation_record(
    country_prefix: str,
    maturity: str,
    row: dict[str, Any],
) -> dict[str, Any] | None:
    """Normalize and validate one EODHD GBOND EOD row."""
    market_date = _parse_date(row.get("date"))
    yield_value = _parse_decimal(row.get("close"))
    if market_date is None or yield_value is None:
        return None

    metadata = symbol_metadata(country_prefix, maturity)
    validation_errors = validate_yield_value(yield_value)
    quality_status = QUALITY_INVALID if validation_errors else QUALITY_VALID
    payload_hash = hash_payload(row)
    provider_timestamp = _parse_datetime(row.get("timestamp") or row.get("datetime"))

    return {
        **metadata,
        "yield_value": yield_value,
        "market_observation_date": market_date,
        "provider_timestamp": provider_timestamp,
        "original_timezone": None,
        "data_frequency": DATA_FREQUENCY,
        "source_type": SOURCE_TYPE,
        "quality_status": quality_status,
        "observation_kind": OBSERVATION_KIND,
        "payload_hash": payload_hash,
        "raw_payload": row,
        "validation_errors": validation_errors or None,
    }


async def insert_observation_idempotent(
    session: AsyncSession,
    record: dict[str, Any],
) -> int:
    """Insert one observation, returning 1 if inserted and 0 if already present."""
    stmt = (
        insert(GovernmentYieldObservation)
        .values(**record)
        .on_conflict_do_nothing(
            constraint="uq_gov_yield_provider_symbol_date_hash",
        )
    )
    result = await session.execute(stmt)
    return int(result.rowcount or 0)


async def upsert_government_yield_status(
    session: AsyncSession,
    stats: GovernmentYieldIngestStats,
    *,
    next_scheduled_run: datetime | None = None,
) -> None:
    """Persist latest operational status for a government-yield job."""
    now = datetime.now(UTC)
    values = {
        "job_name": stats.job_name,
        "last_attempted_at": stats.started_at,
        "last_successful_at": stats.finished_at if stats.status in {"success", "partial"} else None,
        "next_scheduled_run": next_scheduled_run,
        "observations_inserted": stats.observations_inserted,
        "observations_seen": stats.observations_seen,
        "symbols_missing": stats.symbols_missing,
        "stale_symbols": stats.stale_symbols,
        "errors": {"items": stats.errors[:100], "total": len(stats.errors)} if stats.errors else {},
        "status": stats.status,
        "updated_at": now,
    }
    stmt = insert(GovernmentYieldIngestionStatus).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[GovernmentYieldIngestionStatus.job_name],
        set_=values,
    )
    await session.execute(stmt)


def validate_yield_value(value: Decimal) -> list[str]:
    """Return validation errors for impossible government-yield observations."""
    errors: list[str] = []
    if value < MIN_REASONABLE_YIELD:
        errors.append("yield_below_reasonable_floor")
    if value > MAX_REASONABLE_YIELD:
        errors.append("yield_above_reasonable_ceiling")
    return errors


def hash_payload(row: dict[str, Any]) -> str:
    """Stable hash for idempotency and audit."""
    payload = json.dumps(row, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def symbol_name(symbol: str) -> str:
    """Return the exchange-list code portion of an EODHD symbol."""
    return symbol.split(".", 1)[0].upper()


async def _fetch_available_gbond_symbols(
    client: EODHDClient,
    stats: GovernmentYieldIngestStats,
) -> set[str] | None:
    try:
        rows = await client.fetch_exchange_symbols("GBOND")
    except EODHDAuthError as exc:
        stats.errors.append(f"GBOND symbol list auth_or_entitlement: {exc}")
        return None
    except EODHDError as exc:
        stats.errors.append(f"GBOND symbol list unavailable: {exc}")
        return None

    symbols: set[str] = set()
    for row in rows:
        raw = row.get("Code") or row.get("code") or row.get("Symbol") or row.get("symbol")
        if raw:
            symbols.add(symbol_name(str(raw)))
    return symbols


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
            normalized = value.replace("Z", "+00:00")
            parsed = datetime.fromisoformat(normalized)
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


def _is_stale(
    latest_observation_date: date | None,
    as_of: date,
    stale_after_days: int,
) -> bool:
    if latest_observation_date is None:
        return True
    return latest_observation_date < as_of - timedelta(days=stale_after_days)
