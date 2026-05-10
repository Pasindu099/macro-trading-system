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
os.environ["AUTH_ENABLED"] = "false"

from app.main import app  # noqa: E402 — import after env var set


@pytest.fixture(scope="module")
def client() -> TestClient:
    with TestClient(app) as c:
        yield c


def test_root_endpoint(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "This Week&#39;s Biggest Surprises" in r.text


def test_health_endpoint(client: TestClient) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_country_page_renders_html(client: TestClient) -> None:
    countries_response = client.get("/api/countries")
    countries = countries_response.json()["data"]["countries"]
    country_code = countries[0]["code"]

    r = client.get(f"/country/{country_code}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert country_code in r.text


def test_calendar_page_renders_html(client: TestClient) -> None:
    r = client.get("/calendar")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Economic Calendar" in r.text


def test_analytics_page_renders_html(client: TestClient) -> None:
    r = client.get("/analytics")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Data Analytics" in r.text
    assert "Country Depth" in r.text
    assert "Download PDF report" in r.text


def test_analytics_report_downloads_csv(client: TestClient) -> None:
    r = client.get("/analytics/report.csv")
    assert r.status_code == 200
    assert "text/csv" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert "Macro Dashboard Data Summary" in r.text
    assert "Category Coverage" in r.text


def test_analytics_report_downloads_pdf(client: TestClient) -> None:
    r = client.get("/analytics/report.pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert r.content.startswith(b"%PDF")


def test_country_page_404_for_unknown_country(client: TestClient) -> None:
    r = client.get("/country/ZZ")
    assert r.status_code == 404


def test_country_tab_fragment_renders_html(client: TestClient) -> None:
    countries_response = client.get("/api/countries")
    countries = countries_response.json()["data"]["countries"]
    country_code = countries[0]["code"]

    r = client.get(f"/country/{country_code}/tab/inflation")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]


def test_public_surprises_endpoint(client: TestClient) -> None:
    r = client.get("/api/surprises?days=7&limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["data"]["days"] == 7
    assert isinstance(body["data"]["items"], list)


def test_public_calendar_endpoint(client: TestClient) -> None:
    r = client.get("/api/calendar?days_back=1&days_forward=7&limit=50")
    assert r.status_code == 200
    body = r.json()["data"]

    assert body["days_back"] == 1
    assert body["days_forward"] == 7
    assert "total_events" in body
    assert isinstance(body["events"], list)


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
    # At least one tracked country should be reported once seed ran.
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


def test_public_countries_endpoint(client: TestClient) -> None:
    r = client.get("/api/countries")
    assert r.status_code == 200
    body = r.json()

    assert "data" in body and "meta" in body
    countries = body["data"]["countries"]
    assert isinstance(countries, list)
    assert len(countries) >= 1

    first = countries[0]
    assert "code" in first
    assert "name" in first
    assert "currency_code" in first
    assert "indicator_count" in first


def test_public_country_detail_endpoint(client: TestClient) -> None:
    countries_response = client.get("/api/countries")
    countries = countries_response.json()["data"]["countries"]
    country_code = countries[0]["code"]

    r = client.get(f"/api/countries/{country_code}")
    assert r.status_code == 200
    body = r.json()["data"]

    assert body["country"]["code"] == country_code
    assert "release_count" in body
    assert isinstance(body["indicators"], list)


def test_public_country_detail_404_for_unknown_country(client: TestClient) -> None:
    r = client.get("/api/countries/ZZ")
    assert r.status_code == 404


def test_public_indicator_detail_endpoint(client: TestClient) -> None:
    countries_response = client.get("/api/countries")
    countries = countries_response.json()["data"]["countries"]
    country_code = countries[0]["code"]

    country_detail = client.get(f"/api/countries/{country_code}").json()["data"]
    indicators = country_detail["indicators"]
    if not indicators:
        pytest.skip("No indicators loaded yet for public indicator detail test")

    indicator_id = indicators[0]["id"]
    r = client.get(f"/api/indicators/{indicator_id}")
    assert r.status_code == 200

    body = r.json()["data"]
    assert body["indicator"]["id"] == indicator_id
    assert "total_releases" in body
    assert isinstance(body["history"], list)


def test_indicator_detail_page_renders_html(client: TestClient) -> None:
    countries_response = client.get("/api/countries")
    countries = countries_response.json()["data"]["countries"]
    country_code = countries[0]["code"]

    country_detail = client.get(f"/api/countries/{country_code}").json()["data"]
    indicators = country_detail["indicators"]
    if not indicators:
        pytest.skip("No indicators loaded yet for indicator detail page test")

    canonical_name = indicators[0]["canonical_name"]
    r = client.get(f"/country/{country_code}/indicator/{canonical_name}")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert "Recent Prints" in r.text


def test_indicator_explorer_endpoint(client: TestClient) -> None:
    countries_response = client.get("/api/countries")
    countries = countries_response.json()["data"]["countries"]
    country_code = countries[0]["code"]

    country_detail = client.get(f"/api/countries/{country_code}").json()["data"]
    indicators = country_detail["indicators"]
    if not indicators:
        pytest.skip("No indicators loaded yet for indicator explorer test")

    canonical_name = indicators[0]["canonical_name"]
    r = client.get(
        f"/api/country/{country_code}/indicator/{canonical_name}"
        "?range_key=1y&revision_mode=latest"
    )
    assert r.status_code == 200
    body = r.json()["data"]

    assert body["indicator"]["canonical_name"] == canonical_name
    assert body["country"]["code"] == country_code
    assert body["range_key"] == "1y"
    assert body["revision_mode"] == "latest"
    assert isinstance(body["series"], list)
    assert isinstance(body["recent_prints"], list)


def test_public_indicator_detail_404_for_unknown_indicator(client: TestClient) -> None:
    r = client.get("/api/indicators/999999999")
    assert r.status_code == 404
