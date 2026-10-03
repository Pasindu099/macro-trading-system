"""Jobs status uses the real compose database and HTTP stack."""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

os.environ["ENABLE_SCHEDULER"] = "false"
os.environ["AUTH_ENABLED"] = "false"

from app.main import app  # noqa: E402


def test_jobs_status_shape() -> None:
    with TestClient(app) as client:
        response = client.get("/api/admin/jobs/status")
    assert response.status_code == 200
    assert isinstance(response.json()["jobs"], list)
