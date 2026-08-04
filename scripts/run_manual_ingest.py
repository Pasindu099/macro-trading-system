"""Manually trigger an ingestion run.

Useful when:
  - You just started the app and don't want to wait until the next
    scheduled run to get fresh data.
  - You added a new mapping to indicator_mapping.yaml and want to
    re-process recent events through the updated canonicalizer.
  - Testing that the ingest pipeline works end-to-end.

Usage:
    # Fetch all tracked countries, last 45 days (same as a scheduled run):
    python scripts/run_manual_ingest.py

    # Fetch just one country:
    python scripts/run_manual_ingest.py --country US

    # Custom lookback:
    python scripts/run_manual_ingest.py --days 7

    # Specific date range:
    python scripts/run_manual_ingest.py --from 2026-04-01 --to 2026-04-18

    # Deep historical backfill, chunked to avoid vendor result caps:
    python scripts/run_manual_ingest.py --from 2020-01-01 --chunk-days 90
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime, timedelta

from app.db.session import session_scope
from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.eodhd_client import ALLOWED_COUNTRIES, EODHDClient, EODHDError
from app.ingestion.ingest_service import IngestService
from app.ingestion.run_logger import run_logger
from app.logging_config import configure_logging

logger = logging.getLogger(__name__)
CALENDAR_FORWARD_DAYS = 14


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--country",
        help=f"Single country code (default: all {sorted(ALLOWED_COUNTRIES)})",
    )
    p.add_argument(
        "--days",
        type=int,
        default=45,
        help="Lookback window in days (default 45)",
    )
    p.add_argument(
        "--from",
        dest="from_date",
        help="Start date YYYY-MM-DD (overrides --days)",
    )
    p.add_argument(
        "--to",
        dest="to_date",
        help="End date YYYY-MM-DD (default: today)",
    )
    p.add_argument(
        "--chunk-days",
        type=int,
        default=45,
        help=(
            "Split the request into this many days per API call. "
            "Keeps long historical backfills below EODHD's per-call row cap "
            "(default 45)."
        ),
    )
    return p.parse_args()


def parse_date(s: str | None, default: date) -> date:
    if s is None:
        return default
    return datetime.strptime(s, "%Y-%m-%d").date()


def iter_date_chunks(
    from_date: date,
    to_date: date,
    chunk_days: int,
) -> list[tuple[date, date]]:
    """Return inclusive date windows no longer than chunk_days."""
    if chunk_days < 1:
        raise ValueError("--chunk-days must be at least 1")

    chunks: list[tuple[date, date]] = []
    start = from_date
    while start <= to_date:
        end = min(start + timedelta(days=chunk_days - 1), to_date)
        chunks.append((start, end))
        start = end + timedelta(days=1)
    return chunks


async def main_async(args: argparse.Namespace) -> int:
    today = date.today()
    to_date = parse_date(args.to_date, today + timedelta(days=CALENDAR_FORWARD_DAYS))
    if args.from_date:
        from_date = parse_date(args.from_date, to_date)
    else:
        from_date = today - timedelta(days=args.days)

    if args.country:
        country = args.country.upper()
        if country not in ALLOWED_COUNTRIES:
            logger.error(
                "Country %r not in allowlist %s",
                country, sorted(ALLOWED_COUNTRIES),
            )
            return 2
        countries = [country]
    else:
        countries = sorted(ALLOWED_COUNTRIES)

    chunks = iter_date_chunks(from_date, to_date, args.chunk_days)

    logger.info(
        "Manual ingest: countries=%s from=%s to=%s chunks=%d chunk_days=%d",
        countries, from_date, to_date, len(chunks), args.chunk_days,
    )

    canonicalizer = Canonicalizer.from_default_config()
    service = IngestService(canonicalizer)

    total_inserted = 0
    total_updated = 0
    total_unmapped = 0

    async with run_logger("manual_backfill", countries=countries) as run:
        async with EODHDClient() as client:
            for c in countries:
                for chunk_start, chunk_end in chunks:
                    try:
                        events = await client.fetch_economic_events(
                            country=c, from_date=chunk_start, to_date=chunk_end,
                        )
                        run.record_api_call(1)
                    except EODHDError as exc:
                        logger.error(
                            "Fetch failed for %s %s..%s: %s",
                            c, chunk_start, chunk_end, exc,
                        )
                        run.errors.append(f"{c} {chunk_start}..{chunk_end}: {exc}")
                        continue

                    async with session_scope() as session:
                        stats = await service.ingest_events(session, events)
                    run.record_stats(stats)

                    total_inserted += stats.inserted
                    total_updated += stats.updated
                    total_unmapped += stats.unmapped

                    logger.info(
                        "  %s %s..%s: fetched=%d inserted=%d updated=%d same=%d unmapped=%d",
                        c, chunk_start, chunk_end, len(events), stats.inserted,
                        stats.updated, stats.skipped_same, stats.unmapped,
                    )

    logger.info("═══════════════════════════════════════════════════")
    logger.info("MANUAL INGEST COMPLETE")
    logger.info("  inserted: %d", total_inserted)
    logger.info("  updated:  %d", total_updated)
    logger.info("  unmapped: %d (stored with NULL indicator_id)", total_unmapped)
    logger.info("═══════════════════════════════════════════════════")
    return 0


def main() -> int:
    configure_logging()
    args = parse_args()
    try:
        return asyncio.run(main_async(args))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130


if __name__ == "__main__":
    sys.exit(main())
