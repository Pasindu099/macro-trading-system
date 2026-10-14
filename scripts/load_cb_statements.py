"""Backfill 13 months of policy decision statements for all 8 banks (idempotent; Step 12 Part C).

RBNZ blocks the container (Cloudflare); save its OCR media releases on a host as
<dir>/RBNZ/<YYYY-MM-DD>.html and pass --local-dir <dir>.
"""

import argparse
import asyncio
from pathlib import Path

from app.processing.cb_statements import BANKS, ingest_statements


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", action="append", choices=BANKS)
    parser.add_argument("--local-dir", type=Path)
    args = parser.parse_args()
    for bank, summary in asyncio.run(ingest_statements(tuple(args.bank or BANKS), local_dir=args.local_dir)).items():
        print(bank, summary)
