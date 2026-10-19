"""Report side-by-side parity between the news_pipeline collectors and the app collector.

Usage:
    PYTHONPATH=. python scripts/news_parity.py --since 2026-10-19T12:00
    PYTHONPATH=. python scripts/news_parity.py --hours 48
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta

from app.services.news_parity import load_rows, parity_summary


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", help="ISO start (UTC); default now - --hours")
    parser.add_argument("--hours", type=float, default=48.0)
    args = parser.parse_args()
    until = datetime.now(UTC)
    since = (datetime.fromisoformat(args.since).replace(tzinfo=UTC) if args.since
             else until - timedelta(hours=args.hours))
    summary = parity_summary(await load_rows(since, until))
    print(f"window {since:%Y-%m-%d %H:%M} -> {until:%Y-%m-%d %H:%M} UTC ({(until - since).total_seconds() / 3600:.1f} h)")
    for source, row in summary.items():
        print(source, row)
    print("PARITY", all(row["parity"] for row in summary.values()) and bool(summary))


if __name__ == "__main__":
    asyncio.run(main())
