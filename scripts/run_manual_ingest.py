"""Manually trigger an ingestion run.

Useful when:
  - You just started the app and don't want to wait until the next
    scheduled run to get fresh data.
  - You added a new mapping to indicator_mapping.yaml and want to
    re-process recent events through the updated canonicalizer.
  - Testing that the ingest pipeline works end-to-end.

Usage:
    # Fetch all 8 countries, last 45 days (same as a scheduled run):
    python scripts/run_manual_ingest.py

    # Fetch just one country:
    python scripts/run_manual_ingest.py --country US

    # Custom lookback:
    python scripts/run_manual_ingest.py --days 7

    # Specific date range:
    python scripts/run_manual_ingest.py --from 2026-04-01 --to 2026-04-18
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
    return p.parse_args()


def parse_date(s: str | None, default: date) -> date:
    if s is None:
        return default
    return datetime.strptime(s, "%Y-%m-%d").date()


async def main_async(args: argparse.Namespace) -> int:
    to_date = parse_date(args.to_date, date.today())
    if args.from_date:
        from_date = parse_date(args.from_date, to_date)
    else:
        from_date = to_date - timedelta(days=args.days)

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

    logger.info(
        "Manual ingest: countries=%s from=%s to=%s",
        countries, from_date, to_date,
    )

    canonicalizer = Canonicalizer.from_default_config()
    service = IngestService(canonicalizer)

    total_inserted = 0
    total_updated = 0
    total_unmapped = 0

    async with run_logger("manual_backfill", countries=countries) as run:
        async with EODHDClient() as client:
            for c in countries:
                try:
                    events = await client.fetch_economic_events(
                        country=c, from_date=from_date, to_date=to_date,
                    )
                    run.record_api_call(1)
                except EODHDError as exc:
                    logger.error("Fetch failed for %s: %s", c, exc)
                    run.errors.append(f"{c}: {exc}")
                    continue

                async with session_scope() as session:
                    stats = await service.ingest_events(session, events)
                run.record_stats(stats)

                total_inserted += stats.inserted
                total_updated += stats.updated
                total_unmapped += stats.unmapped

                logger.info(
                    "  %s: fetched=%d inserted=%d updated=%d same=%d unmapped=%d",
                    c, len(events), stats.inserted, stats.updated,
                    stats.skipped_same, stats.unmapped,
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