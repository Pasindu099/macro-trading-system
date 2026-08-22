"""Controlled restartable EODHD FOREX backfill.

Example:
    python scripts/backfill_fx_spot.py --start 2023-08-21 --end 2026-08-21 --max-requests 14
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from app.db.session import session_scope
from app.ingestion.eodhd_client import EODHDClient
from app.services.fx_spot import FX_PAIR_SYMBOLS, ingest_eodhd_fx_spot

DEFAULT_CHECKPOINT = Path("data/fx_spot_backfill_checkpoint.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill durable EODHD FX spot observations.")
    parser.add_argument("--pairs", nargs="*", default=list(FX_PAIR_SYMBOLS))
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--max-requests", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    args.checkpoint.parent.mkdir(parents=True, exist_ok=True)
    done = _load_checkpoint(args.checkpoint)
    requests_used = 0
    report = []

    async with EODHDClient() as client:
        for pair in [p.upper() for p in args.pairs]:
            key = f"{pair}:{args.start}:{args.end}"
            if key in done:
                continue
            if args.max_requests is not None and requests_used >= args.max_requests:
                print(json.dumps({"stopped": "max_requests", "requests_used": requests_used, "report": report}, indent=2))
                return
            async with session_scope() as session:
                stats = await ingest_eodhd_fx_spot(
                    session,
                    client,
                    from_date=args.start,
                    to_date=args.end,
                    pairs=[pair],
                    max_requests=1,
                    dry_run=args.dry_run,
                )
            requests_used += stats.requests_used
            report.append({
                "pair": pair,
                "status": stats.status,
                "seen": stats.observations_seen,
                "inserted": stats.observations_inserted,
                "missing": stats.pairs_missing,
                "errors": stats.errors,
            })
            done.add(key)
            _save_checkpoint(args.checkpoint, done)

    print(json.dumps({"requests_used": requests_used, "report": report}, indent=2))


def _load_checkpoint(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return set(json.loads(path.read_text(encoding="utf-8")).get("done", []))


def _save_checkpoint(path: Path, done: set[str]) -> None:
    path.write_text(json.dumps({"done": sorted(done)}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
