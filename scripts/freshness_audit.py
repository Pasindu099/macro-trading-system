"""Print compact source and job watermarks for freshness recovery."""

import asyncio

from sqlalchemy import text

from app.db.session import session_scope
from app.services.job_status import get_job_status


SOURCES = {
    "fx": "SELECT max(observation_date) FROM fx_spot_observations",
    "yields": "SELECT max(market_observation_date) FROM government_yield_observations",
    "fred": "SELECT max(observation_date) FROM fred_observations",
    "calendar": "SELECT max(released_at) FROM indicator_releases WHERE released_at <= now()",
    "cot": "SELECT max(report_date) FROM cot_positions",
    "news": "SELECT max(published_at) FROM raw_news WHERE source = 'investinglive'",
    "macro": "SELECT max(date) FROM processed.currency_stance",
    "dominance": "SELECT max(snapshot_date) FROM dominance_snapshots",
}


async def main() -> None:
    async with session_scope() as session:
        for name, query in SOURCES.items():
            try:
                value = (await session.execute(text(query))).scalar_one()
            except Exception as exc:
                await session.rollback()
                value = f"ERROR {type(exc).__name__}: {str(exc).splitlines()[0][:100]}"
            print(f"SOURCE {name}: {value}")
        for row in await get_job_status(session):
            print(f"JOB {row['run_type']}: {row['last_run']} error={str(row['last_error'])[:120]}")
        runs = (await session.execute(text("""
            SELECT DISTINCT ON (run_type) run_type, started_at, status, errors
            FROM ingestion_runs WHERE run_type NOT LIKE 'job:%'
            ORDER BY run_type, started_at DESC
        """))).all()
        for name, started, status, errors in runs:
            if name.startswith("scheduled_") or "yield" in name or "fx" in name:
                print(f"RUN {name}: {started} {status} {str(errors)[:120]}")


if __name__ == "__main__":
    asyncio.run(main())
