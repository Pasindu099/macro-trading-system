"""Integration smoke tests for admin endpoints.

These tests use FastAPI's TestClient, which hits the app in-process but
through the real HTTP stack. They connect to the REAL development Postgres
(not a mock), so they require:
    - Docker Postgres running (docker compose up -d)
    - Alembic migrations applied
    - At least the countries table seeded

The tests are light on assertions — they verify the endpoints return 200
and have the expected top-level shape. Deep data validation is covered by
unit tests.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

# Disable the scheduler for tests so APScheduler doesn't spin up jobs.
os.environ["ENABLE_SCHEDULER"] = "false"

from app.main import app  # noqa: E402 — import after env var set


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_root_endpoint(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    data = r.json()
    assert data["name"] == "Macro Dashboard API"


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_admin_health(client: TestClient) -> None:
    r = client.get("/api/admin/health")
    assert r.status_code == 200
    body = r.json()

    assert "data" in body and "meta" in body
    data = body["data"]

    assert "database_ok" in data
    assert data["database_ok"] is True
    assert "scheduler_enabled" in data
    assert "countries" in data
    # At least the 8 countries should be reported once seed ran.
    assert len(data["countries"]) >= 1

    # Each country has basic fields
    for c in data["countries"]:
        assert "country_code" in c
        assert "indicator_count" in c
        assert "release_count" in c


def test_admin_ingestion_runs_pagination(client: TestClient) -> None:
    r = client.get("/api/admin/ingestion-runs?limit=5&offset=0")
    assert r.status_code == 200
    body = r.json()
    data = body["data"]

    assert data["limit"] == 5
    assert data["offset"] == 0
    assert isinstance(data["runs"], list)
    assert len(data["runs"]) <= 5


def test_admin_ingestion_runs_filter_by_type(client: TestClient) -> None:
    r = client.get(
        "/api/admin/ingestion-runs?run_type=nonexistent_type"
    )
    assert r.status_code == 200
    assert r.json()["data"]["runs"] == []


def test_admin_ingestion_runs_rejects_bad_limit(client: TestClient) -> None:
    r = client.get("/api/admin/ingestion-runs?limit=9999")
    assert r.status_code == 422  # FastAPI validation error


def test_admin_unmapped_events(client: TestClient) -> None:
    r = client.get("/api/admin/unmapped-events?limit=10")
    assert r.status_code == 200
    body = r.json()
    data = body["data"]

    assert "total_distinct_types" in data
    assert "total_unmapped_releases" in data
    assert isinstance(data["groups"], list)
    assert len(data["groups"]) <= 10


def test_admin_unmapped_events_filter_by_country(client: TestClient) -> None:
    r = client.get("/api/admin/unmapped-events?country=US&limit=5")
    assert r.status_code == 200
    groups = r.json()["data"]["groups"]
    # Every group (if any) should be US
    for g in groups:
        assert g["country"] == "US"