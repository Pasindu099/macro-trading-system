"""Retroactively classify stored unmapped EODHD events after a mapping is added.

Unmapped events are stored with indicator_id = NULL so they can be replayed here.
Each one the current indicator_mapping.yaml now maps is ingested through the
normal revision logic, then its unmapped copy is deleted (it would otherwise
appear twice in the calendar). Events that still do not map are left alone.

Run: docker compose exec app python -m scripts.reclassify_unmapped --country US [--dry-run]
"""

import argparse
import asyncio

from sqlalchemy import text

from app.db.session import session_scope
from app.ingestion.canonicalizer import Canonicalizer
from app.ingestion.ingest_service import IngestService


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--country", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    canonicalizer = Canonicalizer.from_default_config()
    service = IngestService(canonicalizer)
    async with session_scope() as session:
        rows = (await session.execute(text("""
            SELECT id, raw_payload FROM indicator_releases
            WHERE indicator_id IS NULL AND raw_payload->>'country' = :c
            ORDER BY released_at, id
        """), {"c": args.country.upper()})).all()
        now_mapped = [r for r in rows if (ev := canonicalizer.canonicalize(r.raw_payload)) and ev.canonical_name]
        by_type: dict[str, int] = {}
        for r in now_mapped:
            by_type[r.raw_payload.get("type")] = by_type.get(r.raw_payload.get("type"), 0) + 1
        print({"unmapped_rows": len(rows), "now_mapped": len(now_mapped), "by_type": by_type})
        if args.dry_run or not now_mapped:
            return
        # Replay only periods with no mapped row yet. Replaying an older payload over a newer
        # stored release would run the revision logic backwards (e.g. an advance GDP estimate
        # replacing the third estimate); those unmapped copies are simply dropped.
        stored = {(r.name, r.k) for r in (await session.execute(text("""
            SELECT i.canonical_name AS name, coalesce(r.period_start_date::text, r.period) AS k
            FROM indicator_releases r JOIN indicators i ON i.id = r.indicator_id
            WHERE i.country_code = :c
        """), {"c": args.country.upper()})).all()}
        replay = []
        for r in now_mapped:
            ev = canonicalizer.canonicalize(r.raw_payload)
            key = str(ev.period_start_date) if ev.period_start_date else ev.period_raw
            if (ev.canonical_name, key) not in stored:
                replay.append(r.raw_payload)
        stats = await service.ingest_events(session, replay, store_unmapped=False)
        if stats.errors:
            raise RuntimeError(f"{len(stats.errors)} events failed; nothing deleted")
        deleted = await session.execute(text("DELETE FROM indicator_releases WHERE id = ANY(:ids)"),
                                        {"ids": [r.id for r in now_mapped]})
        print({"replayed": len(replay), "inserted": stats.inserted, "updated": stats.updated, "same": stats.skipped_same,
               "unmapped_copies_deleted": deleted.rowcount})


if __name__ == "__main__":
    asyncio.run(main())
