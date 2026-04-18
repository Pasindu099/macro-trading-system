"""Manual test script: fetch sample events from EODHD and print them.

This is the Step 1 demo checkpoint. If this runs and prints events, the
EODHD client and settings are wired up correctly.

Usage:
    python scripts/fetch_sample.py US 2026-04-01 2026-04-18
    python scripts/fetch_sample.py UK 2026-03-15 2026-04-17

The script prints the first 10 events to stdout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime

from app.ingestion.eodhd_client import ALLOWED_COUNTRIES, EODHDClient, EODHDError


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a sample of economic events from EODHD.",
    )
    parser.add_argument(
        "country",
        help=f"EODHD country code. Allowed: {sorted(ALLOWED_COUNTRIES)}",
    )
    parser.add_argument(
        "from_date",
        help="Start date, format YYYY-MM-DD",
    )
    parser.add_argument(
        "to_date",
        help="End date, format YYYY-MM-DD",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Max events to print (default: 10)",
    )
    return parser.parse_args()


def parse_date(s: str) -> date:
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as exc:
        raise SystemExit(f"Invalid date {s!r}, expected YYYY-MM-DD") from exc


async def main() -> int:
    args = parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    from_date = parse_date(args.from_date)
    to_date = parse_date(args.to_date)

    try:
        async with EODHDClient() as client:
            events = await client.fetch_economic_events(
                country=args.country,
                from_date=from_date,
                to_date=to_date,
            )
    except EODHDError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"\n=== Fetched {len(events)} events total. Showing first {args.limit}: ===\n")
    for event in events[: args.limit]:
        print(json.dumps(event, indent=2, default=str))
        print("-" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))