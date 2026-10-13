"""Operational data for the admin jobs status API."""

from __future__ import annotations

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IngestionRun, JobWatermark


async def get_job_status(session: AsyncSession) -> list[dict[str, object]]:
    """Last run, last success and watermark of each analytics job."""
    rows = (await session.execute(
        select(IngestionRun)
        .where(IngestionRun.run_type.like("job:%"))
        .order_by(IngestionRun.run_type, desc(IngestionRun.started_at))
    )).scalars().all()
    watermarks = (await session.execute(select(JobWatermark))).scalars().all()
    by_name: dict[str, dict[str, object]] = {
        f"job:{w.job_name}": {
            "run_type": f"job:{w.job_name}",
            "last_run": None,
            "last_success": None,
            "rows_written": None,
            "last_error": None,
            "watermark": w.last_success_at,
        }
        for w in watermarks
    }
    for row in rows:
        job = by_name.setdefault(row.run_type, {
            "run_type": row.run_type,
            "last_run": None,
            "last_success": None,
            "rows_written": None,
            "last_error": None,
            "watermark": None,
        })
        if job["last_run"] is None:
            job["last_run"] = {
                "started_at": row.started_at,
                "finished_at": row.finished_at,
                "status": row.status,
            }
            job["rows_written"] = row.events_inserted
            job["last_error"] = row.errors
        if row.status == "success" and job["last_success"] is None:
            job["last_success"] = row.finished_at
    return list(by_name.values())


async def get_source_status(session: AsyncSession) -> dict[str, dict[str, object]]:
    """Operational runs and newest observations for the four source collectors."""
    definitions = {
        "fx_spot": ("SELECT started_at, finished_at, status FROM ingestion_runs WHERE run_type = 'job:fx_spot' ORDER BY started_at DESC LIMIT 1",
                    "SELECT max(observation_date) FROM fx_spot_observations"),
        "government_yields": ("SELECT last_attempted_at, last_successful_at, status FROM government_yield_ingestion_status WHERE job_name = 'government_yields_incremental'",
                              "SELECT max(market_observation_date) FROM government_yield_observations"),
        "eodhd_calendar_sessions": ("SELECT started_at, finished_at, status FROM ingestion_runs WHERE run_type IN ('scheduled_asia', 'scheduled_london', 'scheduled_ny') ORDER BY started_at DESC LIMIT 1",
                                    "SELECT max(released_at::date) FROM indicator_releases WHERE released_at <= now()"),
        "investinglive_collection": ("SELECT max(last_checked_at), max(last_checked_at), CASE WHEN bool_and(is_healthy) THEN 'success' ELSE 'failed' END FROM source_health WHERE source_name LIKE 'investinglive:%'",
                                     "SELECT max(published_at::date) FROM raw_news WHERE source = 'investinglive'"),
    }
    result = {}
    for name, (run_query, date_query) in definitions.items():
        run = (await session.execute(text(run_query))).first()
        newest = (await session.execute(text(date_query))).scalar_one()
        result[name] = {"last_run": {"started_at": run[0], "finished_at": run[1],
                                      "status": run[2]} if run and run[0] else None,
                        "status": run[2] if run and run[0] else "never_run",
                        "newest_row_date": newest}
    return result
