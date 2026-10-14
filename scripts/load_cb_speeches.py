"""Backfill 12 months of speeches and investingLive comment attribution (idempotent; Step 12 Part E)."""

import argparse
import asyncio

from app.processing.cb_speeches import BANKS, attribute_comments, ingest_speeches


async def main(banks: tuple[str, ...]) -> None:
    for bank, summary in (await ingest_speeches(banks)).items():
        print(bank, summary, flush=True)
    print("comments", await attribute_comments(), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--bank", action="append", choices=BANKS)
    asyncio.run(main(tuple(parser.parse_args().bank or BANKS)))
