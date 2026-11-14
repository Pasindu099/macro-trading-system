"""Catch up the independent sources missed while the dashboard was stopped."""
import asyncio
import logging

from app.services.cot_positions import run_cot_weekly
from app.services.myfxbook import run_snapshot
from app.services.news_collector import collect_source


async def main():
    logging.basicConfig(level=logging.ERROR)
    for name, job in (("cot", lambda: run_cot_weekly(retry=True)),
                      ("retail", run_snapshot),
                      ("news", lambda: collect_source("investinglive"))):
        try:
            print(name, await job(), flush=True)
        except Exception as exc:
            print(name, type(exc).__name__, str(exc).splitlines()[0][:160], flush=True)


if __name__ == "__main__":
    asyncio.run(main())
