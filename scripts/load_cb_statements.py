"""Backfill 13 months of policy decision statements for all 8 banks (idempotent; Step 12 Part C).

RBA and RBNZ are manual-import sources (blocked): upload statements at /admin/cb-imports.
"""

import argparse
import asyncio
from app.processing.cb_statements import BANKS, ingest_statements


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", action="append", choices=BANKS)
    args = parser.parse_args()
    for bank, summary in asyncio.run(ingest_statements(tuple(args.bank or BANKS))).items():
        print(bank, summary)
