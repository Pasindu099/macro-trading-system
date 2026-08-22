"""Controlled restartable EODHD GBOND backfill.

Example:
    python scripts/backfill_government_yields.py --start 2023-08-21 --end 2026-08-21 --max-requests 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import date
from pathlib import Path

from app.db.session import session_scope
from app.ingestion.eodhd_client import (
    GBOND_COUNTRY_PREFIXES,
    GBOND_MATURITIES,
    EODHDClient,
    build_gbond_symbol,
)
from app.services.government_yields import ingest_eodhd_government_yields, symbol_name

DEFAULT_CHECKPOINT = Path("data/government_yield_backfill_checkpoint.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill durable EODHD government yields.")
    parser.add_argument("--countries", nargs="*", default=list(GBOND_COUNTRY_PREFIXES))
    parser.add_argument("--maturities", nargs="*", default=list(GBOND_MATURITIES))
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
        available_rows = await client.fetch_exchange_symbols("GBOND")
        available = {
            symbol_name(str(row.get("Code") or row.get("code") or row.get("Symbol") or row.get("symbol") or ""))
            for row in available_rows
        }
        for country in args.countries:
            for maturity in args.maturities:
                symbol = build_gbond_symbol(country, maturity)
                key = f"{symbol}:{args.start}:{args.end}"
                if key in done:
                    continue
                if symbol_name(symbol) not in available:
                    report.append({"symbol": symbol, "status": "missing_from_exchange_list"})
                    done.add(key)
                    _save_checkpoint(args.checkpoint, done)
                    continue
                if args.max_requests is not None and requests_used >= args.max_requests:
                    print(json.dumps({"stopped": "max_requests", "requests_used": requests_used, "report": report}, indent=2))
                    return
                if args.dry_run:
                    report.append({"symbol": symbol, "status": "dry_run"})
                    requests_used += 1
                else:
                    async with session_scope() as session:
                        stats = await ingest_eodhd_government_yields(
                            session,
                            client,
                            from_date=args.start,
                            to_date=args.end,
                            country_prefixes=[country],
                            maturities=[maturity],
                            job_name="government_yields_backfill",
                            available_symbols=available,
                        )
                    requests_used += stats.symbols_requested
                    report.append({
                        "symbol": symbol,
                        "status": stats.status,
                        "seen": stats.observations_seen,
                        "inserted": stats.observations_inserted,
                        "missing": stats.symbols_missing,
                        "stale": stats.stale_symbols,
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
