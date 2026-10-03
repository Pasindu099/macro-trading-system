"""Re-fetch the EODHD calendar below the 1,000-event cap and ingest only missing events.

EODHD returns at most 1,000 events per request — the LATEST 1,000 in the window —
so the original 6-month backfill chunks silently dropped their earlier months.
This walks month-by-month per country (halving any window that still returns
1,000), compares against stored rows by (country, type, comparison, released_at),
and in --apply mode ingests only the missing events:

* mapped events are ingested only when no stored release for the same indicator
  and period is newer (newest payload wins; an older estimate never replaces a
  later one — the Step 7 reclassify fix);
* unmapped events are stored unmapped, as normal ingestion does.

Dry run (default): docker compose exec app python -m scripts.backfill_calendar_gaps
Apply:              docker compose exec app python -m scripts.backfill_calendar_gaps --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.eodhd_client import ALLOWED_COUNTRIES, EODHDClient
from app.ingestion.ingest_service import IngestService

EODHD_CAP = 1000
REPORT_PATH = Path("data/calendar_gap_report.json")


def month_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows, cur = [], start.replace(day=1)
    while cur <= end:
        nxt = (cur.replace(day=28) + timedelta(days=4)).replace(day=1)
        windows.append((max(cur, start), min(end, nxt - timedelta(days=1))))
        cur = nxt
    return windows


def event_key(country: str, raw: dict, released_at: datetime) -> tuple:
    return (country, raw.get("type"), raw.get("comparison") or "", released_at)


async def fetch_window(client: EODHDClient, country: str, a: date, b: date, calls: list[int]) -> list[dict]:
    """Fetch [a, b]; split in half while a response hits the cap."""
    events = await client.fetch_economic_events(country, a, b)
    calls[0] += 1
    if len(events) < EODHD_CAP or a == b:
        return events
    mid = a + (b - a) // 2
    return await fetch_window(client, country, a, mid, calls) + await fetch_window(client, country, mid + timedelta(days=1), b, calls)


async def stored_keys(country: str, a: date, b: date) -> set[tuple]:
    async with session_scope() as session:
        rows = await session.execute(text("""
            SELECT raw_payload->>'type' AS ev_type, coalesce(raw_payload->>'comparison', '') AS ev_comparison, released_at
            FROM indicator_releases
            WHERE raw_payload->>'country' = :c AND released_at >= :a AND released_at < :b
        """), {"c": country, "a": a, "b": b + timedelta(days=1)})
        return {(country, r.ev_type, r.ev_comparison, r.released_at) for r in rows}


async def newest_stored(canonical: str, country: str, period_key: str) -> datetime | None:
    async with session_scope() as session:
        return (await session.execute(text("""
            SELECT max(r.released_at) FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.canonical_name = :n AND i.country_code = :c
              AND coalesce(r.period_start_date::text, r.period) = :k
        """), {"n": canonical, "c": country, "k": period_key})).scalar_one_or_none()


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--from", dest="start", type=date.fromisoformat, default=date(2020, 1, 1))
    parser.add_argument("--to", dest="end", type=date.fromisoformat, default=date.today() + timedelta(days=30))
    parser.add_argument("--countries", nargs="*", default=sorted(ALLOWED_COUNTRIES))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    canonicalizer = Canonicalizer.from_default_config()
    service = IngestService(canonicalizer)
    calls = [0]
    chunks, missing_by_series = [], Counter()
    totals = Counter()
    async with EODHDClient() as client:
        for country in args.countries:
            for a, b in month_windows(args.start, args.end):
                events = await fetch_window(client, country, a, b, calls)
                have = await stored_keys(country, a, b)
                missing = []
                for raw in events:
                    ev = canonicalizer.canonicalize(raw)
                    if ev is None or event_key(country, raw, ev.released_at) in have:
                        continue
                    missing.append((raw, ev))
                totals.update(found=len(events), missing=len(missing))
                chunks.append({"country": country, "month": a.strftime("%Y-%m"), "found": len(events),
                               "stored_before": len(events) - len(missing), "missing": len(missing)})
                for _, ev in missing:
                    missing_by_series[f"{country}:{ev.canonical_name or 'unmapped:' + str(ev.raw_payload.get('type'))}"] += 1
                if not args.apply or not missing:
                    continue
                to_ingest = []
                for raw, ev in sorted(missing, key=lambda m: m[1].released_at):
                    if ev.canonical_name:
                        key = str(ev.period_start_date) if ev.period_start_date else ev.period_raw
                        newest = await newest_stored(ev.canonical_name, country, key)
                        if newest is not None and newest >= ev.released_at:
                            totals.update(skipped_older=1)
                            continue
                    to_ingest.append(raw)
                async with session_scope() as session:
                    stats = await service.ingest_events(session, to_ingest, store_unmapped=True)
                totals.update(inserted=stats.inserted, updated=stats.updated, same=stats.skipped_same,
                              unmapped_stored=stats.unmapped_stored, errors=len(stats.errors))
    summary = {"mode": "apply" if args.apply else "dry-run", "calls": calls[0], **totals,
               "chunks_hitting_cap": sum(1 for c in chunks if c["found"] >= EODHD_CAP),
               "missing_mapped_by_series": {k: v for k, v in missing_by_series.most_common() if ":unmapped:" not in k},
               "missing_unmapped_events": sum(v for k, v in missing_by_series.items() if ":unmapped:" in k),
               "chunks": chunks}
    REPORT_PATH.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"chunks", "missing_mapped_by_series"}}))
    print("top missing mapped series:", list(summary["missing_mapped_by_series"].items())[:15])


if __name__ == "__main__":
    asyncio.run(main())
