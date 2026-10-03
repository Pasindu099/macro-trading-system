"""Operational data for the admin jobs status API."""

from __future__ import annotations

from sqlalchemy import desc, select
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
