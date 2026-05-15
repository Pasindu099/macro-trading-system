"""Backfill historical economic events from EODHD.

Loops over all tracked countries across ~6 years (2020-present) in 6-month chunks.
Results are canonicalized and written to the database.

Resumable: progress is checkpointed to .backfill_progress.json. If the
script crashes or you Ctrl+C, re-running picks up where it left off.
Before a checkpointed chunk is skipped, the database is checked for stored
rows in that country/date window so a stale checkpoint cannot hide missing
history.

Usage:
    python scripts/run_backfill.py

    # Force a fresh start (wipes checkpoint file):
    python scripts/run_backfill.py --reset

    # Different date range:
    python scripts/run_backfill.py --from 2022-01-01 --to 2024-12-31

Notes:
    - Total API calls expected: tracked countries × ~14 chunks
    - Well under the 50,000/day limit
    - Takes ~5-10 minutes total depending on network and EODHD response speed
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.db import session_scope
from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.eodhd_client import ALLOWED_COUNTRIES, EODHDClient, EODHDError
from app.ingestion.ingest_service import IngestService, IngestStats

logger = logging.getLogger(__name__)

CHECKPOINT_PATH = Path(".backfill_progress.json")
DEFAULT_START_DATE = date(2020, 1, 1)
CHUNK_MONTHS = 6


@dataclass
class ChunkResult:
    country: str
    from_date: str
    to_date: str
    events_fetched: int
    inserted: int
    updated: int
    skipped_same: int
    unmapped: int
    errors_count: int


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--from", dest="from_date", type=str, default=None,
                   help=f"Start date YYYY-MM-DD (default: {DEFAULT_START_DATE})")
    p.add_argument("--to", dest="to_date", type=str, default=None,
                   help="End date YYYY-MM-DD (default: today)")
    p.add_argument("--reset", action="store_true",
                   help="Ignore any existing checkpoint and start fresh")
    p.add_argument("--countries", nargs="+", default=None,
                   help=f"Subset of countries (default: all {sorted(ALLOWED_COUNTRIES)})")
    p.add_argument(
        "--no-checkpoint-db-validation",
        action="store_true",
        help=(
            "Trust the checkpoint file without confirming matching rows exist "
            "in Postgres. Not recommended for production backfills."
        ),
    )
    return p.parse_args()


def parse_date_arg(s: str | None, default: date) -> date:
    if s is None:
        return default
    return datetime.strptime(s, "%Y-%m-%d").date()


def chunk_date_range(start: date, end: date, months: int) -> list[tuple[date, date]]:
    """Split [start, end] into chunks of approximately `months` each."""
    chunks = []
    current = start
    while current <= end:
        next_start = _add_months(current, months)
        chunk_end = min(next_start - timedelta(days=1), end)
        chunks.append((current, chunk_end))
        current = next_start
    return chunks


def _add_months(d: date, months: int) -> date:
    """Add n months to a date, clamping day to end of month if needed."""
    month = d.month - 1 + months
    year = d.year + month // 12
    month = month % 12 + 1
    day = min(d.day, _days_in_month(year, month))
    return date(year, month, day)


def _days_in_month(year: int, month: int) -> int:
    if month == 12:
        return 31
    return (date(year, month + 1, 1) - timedelta(days=1)).day


def load_checkpoint() -> dict[str, list[str]]:
    """Return mapping of country → list of completed chunk keys ('from_to')."""
    if not CHECKPOINT_PATH.exists():
        return {}
    try:
        with CHECKPOINT_PATH.open() as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Could not read checkpoint file (%s), starting fresh", exc)
        return {}


def save_checkpoint(progress: dict[str, list[str]]) -> None:
    with CHECKPOINT_PATH.open("w") as f:
        json.dump(progress, f, indent=2)


def chunk_key(c_from: date, c_to: date) -> str:
    return f"{c_from.isoformat()}_{c_to.isoformat()}"


async def count_stored_chunk_rows(country: str, c_from: date, c_to: date) -> int:
    """Count stored releases for a country/release-date chunk.

    The checkpoint file is only a resume hint. The database is the source of
    truth, so a completed checkpoint should be trusted only when rows exist
    for the same country and date range. We include mapped rows via the
    indicator country and unmapped rows via raw_payload->>'country'.
    """
    async with session_scope() as session:
        result = await session.execute(
            text(
                """
                SELECT count(*)::int
                FROM indicator_releases r
                LEFT JOIN indicators i ON i.id = r.indicator_id
                WHERE (i.country_code = :country OR r.raw_payload->>'country' = :country)
                  AND r.released_at::date BETWEEN :from_date AND :to_date
                """
            ),
            {
                "country": country,
                "from_date": c_from,
                "to_date": c_to,
            },
        )
        return int(result.scalar_one() or 0)


async def backfill_one_chunk(
    client: EODHDClient,
    service: IngestService,
    country: str,
    c_from: date,
    c_to: date,
) -> ChunkResult:
    events = await client.fetch_economic_events(
        country=country, from_date=c_from, to_date=c_to,
    )

    async with session_scope() as session:
        stats: IngestStats = await service.ingest_events(session, events)

    return ChunkResult(
        country=country,
        from_date=c_from.isoformat(),
        to_date=c_to.isoformat(),
        events_fetched=len(events),
        inserted=stats.inserted,
        updated=stats.updated,
        skipped_same=stats.skipped_same,
        unmapped=stats.unmapped,
        errors_count=len(stats.errors),
    )


async def main_async(args: argparse.Namespace) -> int:
    from_date = parse_date_arg(args.from_date, DEFAULT_START_DATE)
    to_date = parse_date_arg(args.to_date, date.today())

    if args.countries:
        countries = [c.upper() for c in args.countries]
        invalid = set(countries) - ALLOWED_COUNTRIES
        if invalid:
            logger.error("Unknown countries: %s", invalid)
            return 2
    else:
        countries = sorted(ALLOWED_COUNTRIES)

    if args.reset and CHECKPOINT_PATH.exists():
        CHECKPOINT_PATH.unlink()
        logger.info("Reset: removed existing checkpoint")

    progress = load_checkpoint()

    canonicalizer = Canonicalizer.from_default_config()
    service = IngestService(canonicalizer)

    chunks = chunk_date_range(from_date, to_date, CHUNK_MONTHS)
    total_tasks = len(countries) * len(chunks)
    completed_tasks = sum(len(done) for done in progress.values())

    logger.info(
        "Backfill plan: %d countries × %d chunks = %d tasks "
        "(%d already done)",
        len(countries), len(chunks), total_tasks, completed_tasks,
    )

    grand_totals: dict[str, int] = {
        "events_fetched": 0, "inserted": 0, "updated": 0,
        "skipped_same": 0, "unmapped": 0, "errors_count": 0,
    }
    checkpoint_rows_checked = 0
    stale_checkpoint_chunks = 0

    async with EODHDClient() as client:
        for country in countries:
            done_keys = set(progress.get(country, []))

            for c_from, c_to in chunks:
                key = chunk_key(c_from, c_to)
                if key in done_keys:
                    if args.no_checkpoint_db_validation:
                        logger.debug("Skipping %s %s (checkpoint trusted)", country, key)
                        continue

                    stored_rows = await count_stored_chunk_rows(country, c_from, c_to)
                    checkpoint_rows_checked += stored_rows
                    if stored_rows > 0:
                        logger.debug(
                            "Skipping %s %s (checkpoint valid, %d stored rows)",
                            country,
                            key,
                            stored_rows,
                        )
                        continue

                    stale_checkpoint_chunks += 1
                    done_keys.remove(key)
                    progress[country] = [
                        completed_key
                        for completed_key in progress.get(country, [])
                        if completed_key != key
                    ]
                    save_checkpoint(progress)
                    logger.warning(
                        "Checkpoint for %s %s is stale: no stored rows found; refetching",
                        country,
                        key,
                    )

                logger.info("Fetching %s %s..%s", country, c_from, c_to)
                try:
                    result = await backfill_one_chunk(
                        client, service, country, c_from, c_to,
                    )
                except EODHDError as exc:
                    logger.error("EODHD failure for %s %s: %s", country, key, exc)
                    # Save progress before exiting
                    save_checkpoint(progress)
                    return 3

                logger.info(
                    "  %s: fetched=%d inserted=%d updated=%d same=%d unmapped=%d",
                    country, result.events_fetched, result.inserted,
                    result.updated, result.skipped_same, result.unmapped,
                )
                for k in grand_totals:
                    grand_totals[k] += getattr(result, k)

                progress.setdefault(country, []).append(key)
                save_checkpoint(progress)
                completed_tasks += 1
                logger.info(
                    "Progress: %d/%d tasks", completed_tasks, total_tasks,
                )

    logger.info("══════════════════════════════════════════════════")
    logger.info("BACKFILL COMPLETE")
    for k, v in grand_totals.items():
        logger.info("  %s: %d", k, v)
    logger.info("  checkpoint_rows_checked: %d", checkpoint_rows_checked)
    logger.info("  stale_checkpoint_chunks_refetched: %d", stale_checkpoint_chunks)
    logger.info("══════════════════════════════════════════════════")
    return 0


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info("Interrupted — progress saved, run again to resume")
        return 130


if __name__ == "__main__":
    sys.exit(main())
