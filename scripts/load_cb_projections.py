"""Backfill BoE/BoC/RBNZ/SNB projection rounds since 2020 (idempotent; Step 12 Part B).

BoJ, RBA and RBNZ are manual-import sources (PDF-only or blocked): use /admin/cb-imports.
"""

import argparse
import asyncio
from app.services.cb_projection_loaders import BANKS, load_projections


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", action="append", choices=BANKS)
    args = parser.parse_args()
    result = asyncio.run(load_projections(tuple(args.bank or BANKS)))
    for bank, summary in result.items():
        print(bank, {k: v for k, v in summary.items() if k != "skipped"}, "skipped:", summary.get("skipped", {}))
