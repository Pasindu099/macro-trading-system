"""Backfill BoE/BoC/RBNZ/SNB projection rounds since 2020 (idempotent; Step 12 Part B).

RBNZ blocks the container's HTTP client (Cloudflare 403); download the MPS data
workbooks on a host with curl into a directory and pass --rbnz-dir.
"""

import argparse
import asyncio
from pathlib import Path

from app.services.cb_projection_loaders import BANKS, load_projections


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", action="append", choices=BANKS)
    parser.add_argument("--rbnz-dir", type=Path)
    args = parser.parse_args()
    result = asyncio.run(load_projections(tuple(args.bank or BANKS), rbnz_dir=args.rbnz_dir))
    for bank, summary in result.items():
        print(bank, {k: v for k, v in summary.items() if k != "skipped"}, "skipped:", summary.get("skipped", {}))
