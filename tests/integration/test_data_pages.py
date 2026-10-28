"""Data page routes, live fragments, error states, cache and exports."""
from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.routes import data
from app.db.session import dispose_engine, get_sessionmaker
from app.services import desks


@pytest.fixture
async def client():
    desks._panel_cache.clear()
    app = FastAPI()
    app.include_router(data.router)
    app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    desks._panel_cache.clear()
    await dispose_engine()


@pytest.mark.asyncio
async def test_shells_and_live_views(client):
    async with get_sessionmaker()() as session:
        canonical = (await session.execute(text("SELECT canonical_name FROM indicators WHERE country_code='US' ORDER BY id LIMIT 1"))).scalar_one()
    for path in ("/data", "/data/country/US", f"/data/indicator/US/{canonical}", "/data/sources", "/data/datasets/fx_spot"):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert 'data-state="loading"' in response.text and 'data-content-url=' in response.text
        assert 'href="/data" class="active"' in response.text
        assert 'id="global-data-freshness"' in response.text
    for suffix, expected in (
        ("view=landing", "Indicator library"),
        ("view=country&country=US", "Last 24 prints"),
        (f"view=indicator&country=US&canonical={canonical}", "Every stored print"),
        ("view=sources", "Last error (redacted)"),
        ("view=dataset&name=fx_spot", "personal use"),
    ):
        response = await client.get("/data/content?" + suffix)
        assert response.status_code == 200 and expected in response.text, suffix
        assert 'data-state="unavailable"' not in response.text
        assert response.headers["cache-control"] == "private, max-age=60"


@pytest.mark.asyncio
async def test_empty_filters_validation_and_not_found(client):
    empty = await client.get("/data/content", params={"view": "country", "country": "US", "q": uuid4().hex})
    assert 'data-state="empty"' in empty.text
    empty = await client.get("/data/content?view=dataset&name=fx_spot&start=1900-01-01&end=1900-01-02")
    assert 'data-state="empty"' in empty.text
    assert (await client.get("/data/country/INVALID")).status_code == 404
    assert (await client.get("/data/indicator/US/no-such-indicator")).status_code == 404
    for path in ("/data?importance=5", "/data?theme=unknown", "/data?start=invalid", "/data?offset=-1",
                 "/data?start=2026-02-01&end=2026-01-01", "/data/datasets/fx_spot?currency=INVALID"):
        assert (await client.get(path)).status_code == 422, path


@pytest.mark.asyncio
async def test_unavailable_and_cache(client, monkeypatch):
    builder = AsyncMock(side_effect=RuntimeError("private failure"))
    monkeypatch.setattr(data, "_content", builder)
    response = await client.get("/data/content?view=country&country=US")
    assert 'data-state="unavailable"' in response.text and "private failure" not in response.text
    assert response.headers["cache-control"] == "no-store"
    builder.side_effect = None
    builder.return_value = {"catalogue": {"calendar_only": False, "themes": {}, "count": 0}}
    await client.get("/data/content?view=country&country=US")
    response = await client.get("/data/content?view=country&country=US")
    assert builder.await_count == 2 and 'data-state="empty"' in response.text


@pytest.mark.asyncio
async def test_export_routes_forward_filters_and_policy(client, monkeypatch):
    async def lines():
        yield "actual\r\n1\r\n"

    exporter = AsyncMock(return_value=StreamingResponse(lines(), media_type="text/csv"))
    monkeypatch.setattr(data.library, "export_history", exporter)
    response = await client.get("/data/indicator/US/cpi/csv?start=2020-01-01")
    assert response.status_code == 200 and response.headers["content-type"].startswith("text/csv")
    assert exporter.call_args.kwargs["date_from"] == date(2020, 1, 1)
    config = data.datasets.load_config()
    config["show_unlicensed_sources"] = False
    monkeypatch.setattr(data.datasets, "load_config", lambda: config)
    for path in ("/data/datasets/fx_spot", "/data/datasets/fx_spot/csv", "/data/content?view=dataset&name=fx_spot"):
        assert (await client.get(path)).status_code == 404


@pytest.mark.asyncio
async def test_pagination_preserves_filters(client, monkeypatch):
    # A full table page enables the next link without a 500-print fixture.
    row = {"date": date(2026, 1, 1), "series": "USD/JPY", "currencies": ["USD", "JPY"],
           "value": 150, "source": "test", "details": {}}
    meta = {"title": "FX", "licence_flag": "personal use", "description": "FX history", "frequency": "daily",
            "sources": [], "coverage": {"first_date": None, "last_date": None, "rows": 500}}
    monkeypatch.setattr(data, "_content", AsyncMock(return_value={"dataset": meta, "rows": [row]*500,
                        "series_options": ["USD/JPY"], "chart": {"type": "line", "rows": []}}))
    response = await client.get("/data/content?view=dataset&name=fx_spot&currency=JPY&start=2020-01-01")
    assert "/data/datasets/fx_spot?currency=JPY&amp;start=2020-01-01&amp;offset=500" in response.text
