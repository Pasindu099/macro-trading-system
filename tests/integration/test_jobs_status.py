"""Jobs status returns a real logged run from the compose database."""

from __future__ import annotations

import os

import httpx
import pytest
from sqlalchemy import delete

os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["AUTH_ENABLED"] = "false"

from app.settings import get_settings  # noqa: E402
get_settings.cache_clear()

from app.db.models import IngestionRun  # noqa: E402
from app.db.session import dispose_engine, session_scope  # noqa: E402
from app.ingestion.run_logger import run_logger  # noqa: E402
from app.main import app  # noqa: E402


@pytest.mark.asyncio
async def test_jobs_status_shows_logged_run() -> None:
    run_id = None
    try:
        async with run_logger("job:test_status_integration") as run:
            run_id = run.run_id
            run.record_rows(7)

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            response = await client.get("/api/admin/jobs/status")

        assert response.status_code == 200
        jobs = response.json()["jobs"]
        entry = next(job for job in jobs if job["run_type"] == "job:test_status_integration")
        assert entry["last_run"]["status"] == "success"
        assert entry["rows_written"] == 7
        assert entry["last_success"] is not None
    finally:
        if run_id is not None:
            async with session_scope() as session:
                await session.execute(delete(IngestionRun).where(IngestionRun.id == run_id))
        await dispose_engine()
